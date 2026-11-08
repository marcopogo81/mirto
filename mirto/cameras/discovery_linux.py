# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Scoperta camere su Linux (V4L2). Utile per sviluppo e test.

L'ID stabile è il collegamento in ``/dev/v4l/by-id`` se presente, altrimenti
``/dev/v4l/by-path``, altrimenti il nome del dispositivo.
"""

from __future__ import annotations

import os
from pathlib import Path

from .discovery import CameraInfo

SYS = Path("/sys/class/video4linux")


def _stable_links() -> dict[str, str]:
    links: dict[str, str] = {}
    for folder in ("/dev/v4l/by-path", "/dev/v4l/by-id"):  # by-id vince se esiste
        p = Path(folder)
        if p.is_dir():
            for link in p.iterdir():
                links[os.path.realpath(link)] = str(link)
    return links


def discover() -> list[CameraInfo]:
    import cv2  # noqa: PLC0415

    if not SYS.is_dir():
        return []
    links = _stable_links()
    cams: list[CameraInfo] = []
    for node in sorted(SYS.iterdir(), key=lambda p: int(p.name.replace("video", "") or 0)):
        # Solo il nodo di acquisizione principale (index 0) di ogni dispositivo
        idx_file = node / "index"
        if idx_file.exists() and idx_file.read_text().strip() != "0":
            continue
        dev = f"/dev/{node.name}"
        name = (node / "name").read_text().strip() if (node / "name").exists() else node.name
        cams.append(
            CameraInfo(
                id=f"v4l:{links.get(dev, dev)}",
                name=name,
                index=int(node.name.replace("video", "")),
                api=cv2.CAP_V4L2,
            )
        )
    return cams
