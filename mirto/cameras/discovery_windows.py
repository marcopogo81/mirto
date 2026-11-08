# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Scoperta camere su Windows tramite Media Foundation (libreria cv2_enumerate_cameras).

L'ID stabile è il percorso del dispositivo (*symbolic link*), che non cambia
quando si collegano altre camere.
"""

from __future__ import annotations

from .discovery import CameraInfo


def discover() -> list[CameraInfo]:
    import cv2  # noqa: PLC0415
    from cv2_enumerate_cameras import enumerate_cameras  # noqa: PLC0415

    cams: list[CameraInfo] = []
    for info in enumerate_cameras(cv2.CAP_MSMF):
        stable = info.path or f"{info.vid}:{info.pid}:{info.name}"
        cams.append(
            CameraInfo(
                id=f"win:{stable}",
                name=info.name,
                index=info.index,
                api=info.backend,
                extra={"vid": info.vid, "pid": info.pid},
            )
        )
    return cams
