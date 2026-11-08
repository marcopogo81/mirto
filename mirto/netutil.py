# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Indirizzi degli stream: credenziali fuori dai file e fuori dai registri.

Una camera IP si apre con un indirizzo come ``rtsp://utente:password@192.168.1.64:554/stream``. Quelle
credenziali non devono finire né in ``config.yaml`` né in ``logs/mirto.log`` (che l'utente allega alle
segnalazioni): in configurazione si salva l'indirizzo **senza** utente e password, le credenziali stanno nel
portachiavi del sistema alla voce ``rtsp:<id della camera>``, e si rimettono nell'indirizzo solo al momento di
aprire il flusso. Nei registri e nei messaggi l'indirizzo passa sempre da :func:`redact_url`.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit


def _netloc(host: str, port) -> str:
    if ":" in host:  # IPv6
        host = f"[{host}]"
    return f"{host}:{port}" if port else host


def split_credentials(url: str) -> tuple[str, str]:
    """``rtsp://u:p@host/cam`` → ``("rtsp://host/cam", "u:p")``; senza credenziali, ``(url, "")``."""
    try:
        u = urlsplit(url or "")
        port = u.port
    except ValueError:
        return url or "", ""
    if not u.username:
        return url or "", ""
    clean = urlunsplit((u.scheme, _netloc(u.hostname or "", port), u.path, u.query, u.fragment))
    creds = unquote(u.username) + (f":{unquote(u.password)}" if u.password else "")
    return clean, creds


def with_credentials(url: str, creds: str) -> str:
    """Rimette utente e password nell'indirizzo, al momento di aprire il flusso."""
    if not creds:
        return url or ""
    try:
        u = urlsplit(url or "")
        port = u.port
    except ValueError:
        return url or ""
    user, _sep, password = str(creds).partition(":")
    userinfo = quote(user, safe="") + (f":{quote(password, safe='')}" if password else "") + "@"
    return urlunsplit((u.scheme, userinfo + _netloc(u.hostname or "", port), u.path, u.query, u.fragment))


def redact_url(url: str) -> str:
    """Indirizzo da scrivere nei registri: la password diventa ``***`` (l'utente resta, aiuta a capire)."""
    clean, creds = split_credentials(url)
    if not creds:
        return url or ""
    user, _sep, password = creds.partition(":")
    try:
        u = urlsplit(clean)
        port = u.port
    except ValueError:
        return clean
    userinfo = quote(user, safe="") + (":***" if password else "") + "@"
    return urlunsplit((u.scheme, userinfo + _netloc(u.hostname or "", port), u.path, u.query, u.fragment))


# Un indirizzo con le credenziali dentro, in mezzo a un testo qualunque: «schema://utente:password@».
# L'utente resta (aiuta a capire quale camera era), la password va via. Non tocca né gli indirizzi di
# posta (non hanno schema) né la parola «password» in un messaggio d'errore.
_CREDENZIALI = re.compile(r"(?P<schema>[a-z][a-z0-9+.\-]*://)(?P<utente>[^\s:/@]+):(?P<password>[^\s/@]+)@")


def redact_text(testo: str) -> str:
    """Maschera le password dentro gli indirizzi presenti in un testo (una riga di registro)."""
    return _CREDENZIALI.sub(lambda m: f"{m['schema']}{m['utente']}:***@", testo)


def scrub_logs(folder) -> int:
    """Toglie dai registri **già scritti** le password rimaste dentro gli indirizzi.

    Serve perché una password scritta per sbaglio resta nel file anche dopo che il difetto è
    corretto, e `logs/mirto.log` è proprio il file che un utente allega a una segnalazione.
    Trovato così il 19/10/2026: due password di camera in righe del 17 e 18 ottobre, scritte prima
    che `redact_url` esistesse. Va chiamata all'avvio **prima** di riaprire il registro, altrimenti
    si riscrive un file che il programma tiene già aperto.

    Restituisce quante righe ha ripulito; i file senza niente da togliere non vengono riscritti.
    """
    cartella = Path(folder)
    ripulite = 0
    try:
        percorsi = sorted(cartella.glob("mirto.log*"))
    except OSError:
        return 0
    for percorso in percorsi:
        try:
            originale = percorso.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        pulito = redact_text(originale)
        if pulito == originale:
            continue
        cambiate = sum(1 for a, b in zip(originale.splitlines(), pulito.splitlines()) if a != b)
        try:
            percorso.write_text(pulito, encoding="utf-8")
        except OSError:
            continue   # registro di sola lettura: meglio perdere la ripulitura che non partire
        ripulite += cambiate
    return ripulite


def move_camera_credentials(config, secrets) -> int:
    """Migrazione: credenziali rimaste negli indirizzi di ``cameras.network`` → portachiavi.

    Riscrive il file di configurazione se trova qualcosa. Restituisce quante camere ha sistemato.
    """
    from .credentials import rtsp_name  # noqa: PLC0415

    nets = config.get("cameras.network") or []
    moved = 0
    for net in nets:
        clean, creds = split_credentials(str(net.get("url") or ""))
        if not creds:
            continue
        net["url"] = clean
        if secrets is not None:
            secrets.set(rtsp_name(str(net.get("id"))), creds)
        moved += 1
    if moved:
        config.set("cameras.network", nets)
        try:
            config.save()
        except Exception:  # noqa: BLE001
            import logging  # noqa: PLC0415

            logging.getLogger(__name__).exception("Configurazione non salvata dopo lo spostamento delle credenziali")
    return moved
