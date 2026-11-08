# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Camere trasmesse dal Raspberry (es. MediaMTX): il nodo le dichiara a Mirto.

Le camere si leggono dagli annunci in rete già configurati (``/etc/avahi/services/*.service`` con
``_rtsp._tcp``): porta e percorso dello stream. Per ognuna il nodo controlla se lo streaming risponde.
Così Mirto sa che quella camera è "del nodo" e può mostrarla insieme alla sirena e alle altre azioni.
"""

from __future__ import annotations

import re
import socket
import subprocess
from pathlib import Path

AVAHI_DIR = Path("/etc/avahi/services")


def _alive(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def board_model() -> str:
    """Modello della scheda, es. "Raspberry Pi 4 Model B Rev 1.2" (vuoto se non è un Raspberry)."""
    try:
        return Path("/proc/device-tree/model").read_text(errors="replace").strip("\x00 \n")
    except OSError:
        return ""


def rtsp_credentials(path: Path = Path("/etc/mirto-node/rtsp")) -> tuple[str, str] | None:
    """Utente e password dello stream (riga ``utente:password``), se il nodo li ha impostati."""
    try:
        user, _, pwd = path.read_text().strip().partition(":")
        return (user, pwd) if user and pwd else None
    except OSError:
        return None


def find_cameras(folder: Path = AVAHI_DIR, probe=_alive) -> list[dict]:
    """[{id, nome, porta, percorso, in_linea}] per ogni annuncio ``_rtsp._tcp`` sul nodo."""
    out = []
    try:
        files = sorted(folder.glob("*.service"))
    except OSError:
        return out
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        if "_rtsp._tcp" not in text:
            continue
        port = re.search(r"<port>\s*(\d+)\s*</port>", text)
        path = re.search(r"<txt-record>\s*path=([^<\s]+)\s*</txt-record>", text)
        port_n = int(port.group(1)) if port else 554
        p = path.group(1) if path else "/"
        p = p if p.startswith("/") else "/" + p
        cam_id = p.strip("/").replace("/", "_") or "cam"
        out.append({"id": cam_id, "nome": cam_id, "porta": port_n, "percorso": p, "in_linea": bool(probe(port_n))})
    return out


def host_addresses() -> list[str]:
    """Indirizzi IPv4 del Raspberry (``hostname -I``), per far riconoscere a Mirto le sue camere."""
    try:
        res = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=3)
        return [a for a in res.stdout.split() if ":" not in a]
    except Exception:  # noqa: BLE001
        return []


def host_name() -> str:
    name = socket.gethostname()
    return name if "." in name else name + ".local"
