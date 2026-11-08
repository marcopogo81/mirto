# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Il programma ffmpeg: dove si trova, che versione è, quale codificatore H.264 usare.

ffmpeg gira come processo separato: se si blocca o si chiude, Mirto continua e lo riavvia.
Ordine di ricerca: ``recording.ffmpeg`` in configurazione, il pacchetto ``imageio-ffmpeg`` (installato con
Mirto, contiene un ffmpeg pronto), poi quello del sistema (Homebrew, PATH).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# Codificatori H.264 in ordine di preferenza (hardware prima), con le loro opzioni
ENCODERS: dict[str, list[str]] = {
    "h264_videotoolbox": ["-b:v", "{kbps}k", "-realtime", "1", "-allow_sw", "1"],
    "h264_mf": ["-b:v", "{kbps}k"],
    "h264_qsv": ["-b:v", "{kbps}k"],
    "h264_nvenc": ["-b:v", "{kbps}k"],
    "h264_amf": ["-b:v", "{kbps}k"],
    "libx264": ["-preset", "veryfast", "-tune", "zerolatency", "-crf", "26"],  # niente ritardo: orari giusti
    "libopenh264": ["-b:v", "{kbps}k"],
    "mpeg4": ["-q:v", "5"],  # ultima risorsa: si apre con VLC, meno bene con QuickTime
}


def candidate_encoders(platform: str = sys.platform) -> list[str]:
    if platform == "darwin":
        hw = ["h264_videotoolbox"]
    elif platform == "win32":
        hw = ["h264_mf", "h264_qsv", "h264_nvenc", "h264_amf"]
    else:
        hw = []
    return hw + ["libx264", "libopenh264", "mpeg4"]


def popen_flags() -> dict:
    """Su Windows ffmpeg non deve aprire una finestra nera del terminale."""
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {}


def utc_env() -> dict:
    """Ambiente per ffmpeg con l'ora in UTC: i nomi degli spezzoni (``-strftime``) non risentono dell'ora legale."""
    env = dict(os.environ)
    env["TZ"] = "UTC0"
    return env


def find_ffmpeg(configured: str = "") -> str:
    """Percorso di ffmpeg, oppure "" se non c'è."""
    if configured and os.path.isfile(configured) and os.access(configured, os.X_OK):
        return configured
    try:
        import imageio_ffmpeg  # noqa: PLC0415

        path = imageio_ffmpeg.get_ffmpeg_exe()
        if path and os.path.isfile(path):
            return path
    except Exception:  # noqa: BLE001  (pacchetto assente o senza binario per questa piattaforma)
        pass
    found = shutil.which("ffmpeg")
    if found:
        return found
    for p in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"):
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return ""


@dataclass
class FFmpegInfo:
    path: str = ""
    version: str = ""
    encoder: str = ""               # codificatore H.264 scelto per le camere locali
    tried: dict = field(default_factory=dict)  # codificatore -> errore
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.path)

    def encoder_args(self, kbps: int) -> list[str]:
        return ["-c:v", self.encoder] + [a.format(kbps=kbps) for a in ENCODERS.get(self.encoder, [])]


def _version(path: str) -> str:
    try:
        out = subprocess.run([path, "-hide_banner", "-version"], capture_output=True, text=True, timeout=10,
                             **popen_flags()).stdout
        first = out.splitlines()[0] if out else ""
        return first.split(" Copyright")[0].replace("ffmpeg version ", "").strip()
    except (OSError, subprocess.SubprocessError, IndexError):
        return ""


def probe_encoder(path: str, encoder: str, timeout: float = 15.0) -> str:
    """Prova a codificare 5 fotogrammi neri con ``encoder``. Restituisce "" se funziona, altrimenti l'errore.

    Serve perché un codificatore può essere nell'elenco ma non funzionare (es. senza scheda video adatta)."""
    w, h = 320, 240
    cmd = [path, "-hide_banner", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}",
           "-r", "10", "-i", "-", "-frames:v", "5", "-pix_fmt", "yuv420p",
           *(["-c:v", encoder] + [a.format(kbps=500) for a in ENCODERS.get(encoder, [])]), "-f", "null", "-"]
    try:
        res = subprocess.run(cmd, input=bytes(w * h * 3 * 5), capture_output=True, timeout=timeout, **popen_flags())
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)
    if res.returncode == 0:
        return ""
    err = res.stderr.decode("utf-8", "replace").strip().splitlines()
    return (err[-1] if err else f"codice {res.returncode}")[:200]


_cache: dict[str, FFmpegInfo] = {}
_cache_lock = threading.Lock()


def detect(configured: str = "", preferred: str = "", refresh: bool = False) -> FFmpegInfo:
    """ffmpeg e codificatore scelto (risultato tenuto in memoria: le prove durano qualche secondo)."""
    key = f"{configured}|{preferred}"
    with _cache_lock:
        if key in _cache and not refresh:
            return _cache[key]
    info = FFmpegInfo(path=find_ffmpeg(configured))
    if not info.path:
        info.error = "ffmpeg non trovato"
    else:
        info.version = _version(info.path)
        order = candidate_encoders()
        if preferred:
            order = [preferred] + [e for e in order if e != preferred]
        for enc in order:
            err = probe_encoder(info.path, enc)
            if not err:
                info.encoder = enc
                break
            info.tried[enc] = err
        if not info.encoder:
            info.error = "nessun codificatore H.264 funzionante"
        log.info("ffmpeg %s (%s), codificatore per le camere locali: %s", info.version, info.path,
                 info.encoder or "nessuno")
    with _cache_lock:
        _cache[key] = info
    return info
