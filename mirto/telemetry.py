# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Statistiche anonime d'uso e controllo degli aggiornamenti.

Servono a sapere quante installazioni di Mirto sono attive, senza sapere di chi sono.

* **Consenso**: al primo avvio l'interfaccia chiede se inviare le statistiche; la risposta predefinita è *no*.
  ``telemetry.stats`` vale ``None`` (non ancora chiesto), ``True`` o ``False``.
* **Con il sì**: una volta al giorno parte un "battito" con un ID casuale (``telemetry.install_id``, creato
  al consenso, azzerabile) e pochi dati senza nulla di personale: versione, sistema, lingua, numero di camere e
  di nodi **per fasce**, quali funzioni sono accese. Mai foto, video, audio, nomi, indirizzi, dati di Telegram.
* **Con il no**: parte **una sola volta** un segnale "ha detto no" senza ID né dati del computer (solo la
  versione di Mirto), così si contano anche le installazioni che rifiutano senza poterle riconoscere.
* **Ritiro del consenso o nuovo ID**: l'ID vecchio viene cancellato anche dal server (``/v1/forget``).
* **Aggiornamenti** (``telemetry.update_check``, acceso di serie): una volta al giorno si chiede solo qual è
  l'ultima versione; l'unico dato è la versione di Mirto (nell'intestazione User-Agent). Se ce n'è una nuova
  Mirto lo dice una volta.

Il server (``tools/stats-worker``, un Cloudflare Worker gratuito) non legge né salva gli indirizzi IP
(Cloudflare li tratta solo per consegnare la richiesta). Senza indirizzo del server non si chiede nemmeno il consenso.
Il codice di questo file è tutto ciò che Mirto invia: niente altro esce per le statistiche.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from typing import Callable

from . import __version__

log = logging.getLogger(__name__)

# Indirizzo del server delle statistiche (Cloudflare Worker). Vuoto = statistiche e aggiornamenti spenti.
# Il nome dice ancora «myhouse» perché il Worker e il suo database sono già pubblicati con quel nome:
# cambiarlo qui non li rinomina, ne creerebbe di nuovi e vuoti, e le statistiche già raccolte
# resterebbero nell'altro. È un indirizzo, non un nome di prodotto: non lo legge nessuno.
ENDPOINT = "https://myhouse-stats.myhousesurveillance.workers.dev"
DAY = 24 * 3600
CHECK_EVERY = 6 * 3600      # ogni quanto il thread controlla se è passato un giorno
TIMEOUT = 10


def endpoint(config) -> str:
    """Indirizzo del server; ``MIRTO_STATS_URL=off`` (o ``telemetry.endpoint: off``) spegne tutto (prove, CI)."""
    url = os.environ.get("MIRTO_STATS_URL") or config.get("telemetry.endpoint", "") or ENDPOINT
    return "" if str(url).strip().lower() == "off" else str(url).rstrip("/")


def bucket(n: int) -> str:
    """Numero di camere o nodi per fasce: non si invia mai il numero esatto."""
    n = int(n or 0)
    if n <= 1:
        return str(max(n, 0))
    if n <= 4:
        return "2-4"
    if n <= 9:
        return "5-9"
    return "10+"


def os_name() -> str:
    """Solo il sistema e la versione principale: "macOS 15", "Windows 11", "Linux"."""
    if platform.system() == "Darwin":
        major = (platform.mac_ver()[0] or "").split(".")[0]
        return f"macOS {major}".strip()
    if platform.system() == "Windows":
        rel = platform.release()
        try:  # Windows 11 dichiara ancora "10": si riconosce dal numero di build
            if rel == "10" and int(platform.version().split(".")[-1]) >= 22000:
                rel = "11"
        except (ValueError, IndexError):
            pass
        return f"Windows {rel}"
    return platform.system() or "?"


def parse_version(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:3]) or (0,)


def newer(latest: str, current: str = __version__) -> bool:
    return bool(latest) and parse_version(latest) > parse_version(current)


def payload(config, stats: dict) -> dict:
    """Il battito giornaliero, esattamente come viene inviato (mostrato anche nelle Impostazioni).

    ``stats`` arriva da ``MirtoCore.telemetry_stats()``: conteggi e interruttori, niente nomi."""
    from .i18n import get_language  # noqa: PLC0415

    features = sorted(k for k, on in (stats.get("features") or {}).items() if on)
    return {
        "id": config.get("telemetry.install_id", ""),
        "version": __version__,
        "os": os_name(),
        "lang": get_language(),
        "cameras": bucket(stats.get("cameras", 0)),
        "nodes": bucket(stats.get("nodes", 0)),
        "recording": str(stats.get("recording", "")),
        "features": features,
    }


class Telemetry:
    """Battito giornaliero e controllo aggiornamenti, in un thread in background."""

    def __init__(self, config, stats: Callable[[], dict], on_update: Callable[[str], None] | None = None,
                 clock=time.time, opener=None):
        self.config = config
        self.stats = stats
        self.on_update = on_update
        self.clock = clock
        self._open = opener or self._urlopen
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self.latest = ""
        self.last_error = ""
        self._gen = 0  # cresce a ogni cambio di consenso: un giro iniziato prima non sovrascrive niente

    # --- consenso ------------------------------------------------------------------------
    def asked(self) -> bool:
        return self.config.get("telemetry.stats") is not None

    def available(self) -> bool:
        """C'è un server a cui inviare: senza, non si chiede nemmeno il consenso."""
        return bool(endpoint(self.config))

    def _forget(self, install_id: str) -> None:
        """L'ID va cancellato anche dal server (consenso ritirato o ID rinnovato): parte al prossimo giro."""
        if install_id:
            ids = [i for i in (self.config.get("telemetry.forget_ids") or []) if i != install_id]
            self.config.set("telemetry.forget_ids", (ids + [install_id])[-10:])

    def set_consent(self, yes: bool) -> None:
        """Risposta dell'utente. Il sì crea l'ID; il no lo cancella (anche dal server) e, se è la prima
        risposta, manda una volta il segnale anonimo."""
        self._gen += 1
        before = self.config.get("telemetry.stats")
        self.config.set("telemetry.stats", bool(yes))
        if yes:
            if not self.config.get("telemetry.install_id"):
                self.config.set("telemetry.install_id", uuid.uuid4().hex)
            self.config.set("telemetry.last_sent", 0)    # il primo battito parte subito
            self.config.set("telemetry.declined_pending", False)  # chi poi dice sì non è contato anche tra i no
        else:
            self._forget(self.config.get("telemetry.install_id", ""))
            self.config.set("telemetry.install_id", "")
            if before is None:
                self.config.set("telemetry.declined_pending", True)  # il "no" si conta una volta sola
        self.config.save()
        self.poke()

    def reset_id(self) -> None:
        """Nuovo ID casuale; quello vecchio viene cancellato dal server."""
        if self.config.get("telemetry.stats"):
            self._gen += 1
            self._forget(self.config.get("telemetry.install_id", ""))
            self.config.set("telemetry.install_id", uuid.uuid4().hex)
            self.config.set("telemetry.last_sent", 0)
            self.config.save()
            self.poke()

    # --- invio -----------------------------------------------------------------------------
    def _urlopen(self, req: urllib.request.Request):
        from .netssl import ssl_context  # noqa: PLC0415

        return urllib.request.urlopen(req, timeout=TIMEOUT, context=ssl_context())

    def _request(self, path: str, body: dict | None = None) -> dict:
        url = endpoint(self.config)
        if not url:
            return {}
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url + path, data=data, method="POST" if data else "GET",
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": f"Mirto/{__version__}"})
        with self._open(req) as r:
            raw = r.read(4096)
        try:
            out = json.loads(raw or b"{}")
        except ValueError:
            return {}
        return out if isinstance(out, dict) else {}

    def _send_declined(self) -> None:
        try:
            self._request("/v1/declined", {"version": __version__})
            self.config.set("telemetry.declined_pending", False)
            self.config.save()
        except (OSError, urllib.error.URLError) as exc:  # si riprova al prossimo giro
            self.last_error = str(exc)

    def _send_forget(self) -> None:
        for install_id in list(self.config.get("telemetry.forget_ids") or []):
            try:
                self._request("/v1/forget", {"id": install_id})
            except (OSError, urllib.error.URLError) as exc:  # si riprova al prossimo giro
                self.last_error = str(exc)
                return
            self.config.set("telemetry.forget_ids",
                            [i for i in (self.config.get("telemetry.forget_ids") or []) if i != install_id])
            self.config.save()

    def tick(self) -> None:
        """Un giro: cancellazioni e "no" in sospeso, poi (una volta al giorno) battito con il sì o sola
        domanda della versione."""
        if not endpoint(self.config):
            return
        if self.config.get("telemetry.forget_ids"):
            self._send_forget()
        if self.config.get("telemetry.declined_pending"):
            self._send_declined()
        gen = self._gen
        consent = self.config.get("telemetry.stats")
        install_id = self.config.get("telemetry.install_id", "")
        now = self.clock()
        if now - float(self.config.get("telemetry.last_sent", 0) or 0) < DAY:
            return
        try:
            if consent and install_id:
                answer = self._request("/v1/ping", payload(self.config, self.stats()))
            elif self.config.get("telemetry.update_check", True):
                answer = self._request("/v1/latest")
            else:
                return
        except (OSError, urllib.error.URLError, ValueError) as exc:
            self.last_error = str(exc)
            log.info("Statistiche/aggiornamenti non raggiungibili: %s", exc)
            return
        self.last_error = ""
        if gen != self._gen:  # il consenso è cambiato durante la richiesta: si rifà subito il giro
            self.poke()
            return
        self.config.set("telemetry.last_sent", now)
        self.config.save()
        latest = str(answer.get("latest") or "")[:20]
        if newer(latest):
            self.latest = latest
            if self.config.get("telemetry.update_check", True) and \
                    self.config.get("telemetry.notified_version") != latest:
                # si segna come avvisata solo se qualcuno l'ha mostrata (senza finestra resta da mostrare)
                if self.on_update and self.on_update(latest):
                    self.config.set("telemetry.notified_version", latest)
                    self.config.save()

    # --- thread ----------------------------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="statistiche", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def poke(self) -> None:
        """Fa ripartire il controllo subito (dopo una risposta o un cambio nelle Impostazioni)."""
        self._wake.set()

    def _run(self) -> None:
        self._wake.wait(30)  # non subito all'avvio: prima le camere
        while not self._stop.is_set():
            self._wake.clear()
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                log.exception("Statistiche: errore inatteso")
            self._wake.wait(CHECK_EVERY)
