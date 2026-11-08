# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Rapporto da incollare in una segnalazione, incapace di contenere un segreto.

Chi apre una issue non sa quali dati siano delicati. Il modello di issue dice «non incollare token,
indirizzi IP, ID delle chat», ma per capire un problema serve sapere com'è fatta l'installazione: che
sistema, che versione di Python, se ffmpeg c'è, quante camere, quali errori recenti. Questo modulo
raccoglie quello che serve e **toglie** quello che non deve uscire — non per attenzione di chi lo
usa, ma perché ogni riga passa da :func:`pulisci` e un test lo verifica con segreti veri dentro.

Cosa non entra mai nel rapporto, per costruzione: nomi scelti dall'utente (una camera può chiamarsi
come una persona o come una via), indirizzi di rete, destinatari della posta, ID delle chat, token,
chiavi. Cosa entra: numeri, versioni, sì/no, e le ultime righe di errore ripulite.
"""

from __future__ import annotations

import os
import platform
import re
import sys
from pathlib import Path

from . import __version__
from .netutil import redact_text

# Un indirizzo IPv4 dice dov'è la casa di chi segnala; in un rapporto basta sapere che c'era un
# indirizzo. Lo stesso per la posta, per gli ID numerici lunghi (chat di Telegram) e per le stringhe
# esadecimali lunghe, che sono sempre chiavi o token.
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_POSTA = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b")
_ID_LUNGO = re.compile(r"-?\b\d{9,}\b")
_ESADECIMALE = re.compile(r"\b[0-9a-fA-F]{20,}\b")
_TOKEN_BOT = re.compile(r"\b\d{6,}:[\w-]{20,}\b")

ERRORI_DA_MOSTRARE = 10


def pulisci(testo: str) -> str:
    """Toglie da un testo qualunque le cose che non devono finire in una segnalazione."""
    testo = redact_text(testo)              # password dentro gli indirizzi → ***
    testo = _TOKEN_BOT.sub("<token>", testo)
    testo = _POSTA.sub("<indirizzo di posta>", testo)
    testo = _IPV4.sub("<indirizzo>", testo)
    testo = _ESADECIMALE.sub("<chiave>", testo)
    return _ID_LUNGO.sub("<id>", testo)


def _ffmpeg() -> str:
    try:
        from .recording.ffmpeg import find_ffmpeg

        percorso = find_ffmpeg()
    except Exception:  # noqa: BLE001
        percorso = ""
    if not percorso:
        return "non trovato (senza ffmpeg non si registra)"
    dove = "incluso con Mirto" if "imageio" in percorso else "del sistema"
    # ffprobe non si cita piu': Mirto non lo usa (nemmeno nei test, dal 19/10), e dire
    # "ffprobe assente" su ogni Windows faceva sembrare rotto qualcosa che funziona.
    return f"presente ({dove})"


def _riconoscimento() -> str:
    try:
        import onnxruntime  # noqa: PLC0415

        return ", ".join(onnxruntime.get_available_providers()) or "nessun provider"
    except Exception as exc:  # noqa: BLE001
        return f"onnxruntime non disponibile ({type(exc).__name__})"


def _errori_recenti(cartella: Path) -> list[str]:
    """Le ultime righe di errore dei registri, ripulite. Vuoto se non ci sono registri."""
    percorso = cartella / "logs" / "mirto.log"
    try:
        righe = percorso.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    errori = [r for r in righe if " ERROR " in r or " WARNING " in r]
    return [pulisci(r)[:300] for r in errori[-ERRORI_DA_MOSTRARE:]]


def rapporto(config=None, data_dir: Path | None = None) -> str:
    """Il rapporto completo, pronto da incollare."""
    from .config import Config, data_dir as cartella_dati

    cfg = config if config is not None else Config()
    cartella = Path(data_dir) if data_dir is not None else cartella_dati()
    camere_locali = len(cfg.get("cameras.local") or [])
    camere_rete = len(cfg.get("cameras.network") or [])
    nodi = cfg.get("actions.nodes") or []
    azioni = cfg.get("actions.items") or []
    righe = [
        "=== Mirto · diagnostica ===",
        f"Mirto            {__version__}",
        f"Sistema            {platform.system()} {platform.release()} ({platform.machine()})",
        f"Python             {sys.version.split()[0]}",
        f"ffmpeg             {_ffmpeg()}",
        f"Riconoscimento     {_riconoscimento()}",
        "",
        f"Camere             {camere_locali} locali, {camere_rete} di rete",
        f"Nodi               {len(nodi)} configurat{'o' if len(nodi) == 1 else 'i'}",
        f"Azioni             {len(azioni)} ({sum(1 for a in azioni if a.get('enabled', True))} attive)",
        f"Registrazione      {cfg.get('recording.mode', 'events')}, "
        f"{cfg.get('recording.retention_days', 7)} giorni",
        f"Avvisi             Telegram {'sì' if cfg.get('notify.telegram.enabled') else 'no'}, "
        f"email {'sì' if cfg.get('notify.email.enabled') else 'no'}",
        f"Lingua             {cfg.get('general.language', 'auto')}",
    ]
    if getattr(cfg, "load_warning", ""):
        righe.append(f"Configurazione     ripartita dai predefiniti: {pulisci(cfg.load_warning)}")
    errori = _errori_recenti(cartella)
    righe += ["", f"=== Ultimi errori ({len(errori)}) ==="]
    righe += errori or ["nessun errore nei registri"]
    righe += ["", "Nomi delle camere, indirizzi, destinatari e token non sono inclusi di proposito."]
    return "\n".join(pulisci(r) if r.startswith("Configurazione") else r for r in righe) + "\n"


def cmd_diagnostics() -> int:
    """``mirto --diagnostica``: stampa il rapporto."""
    print(rapporto(), end="")
    return 0
