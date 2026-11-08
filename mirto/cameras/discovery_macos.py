# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Scoperta camere su macOS tramite AVFoundation (PyObjC).

L'ID stabile è ``AVCaptureDevice.uniqueID``. L'indice per OpenCV segue lo
stesso ordine usato dal backend AVFoundation di OpenCV: prima i dispositivi
``AVMediaTypeVideo``, poi quelli ``AVMediaTypeMuxed``.

Nota: se una versione futura di OpenCV cambiasse questo ordine, l'indice si può
forzare per camera in configurazione (``cameras.overrides.<id>.opencv_index``)
e verificare con ``python -m mirto --prova-camera <ID>``.
"""

from __future__ import annotations

from .discovery import CameraInfo


def discover() -> list[CameraInfo]:
    import AVFoundation  # noqa: PLC0415  (pyobjc-framework-AVFoundation)
    import cv2  # noqa: PLC0415

    video = list(AVFoundation.AVCaptureDevice.devicesWithMediaType_(AVFoundation.AVMediaTypeVideo) or [])
    muxed = list(AVFoundation.AVCaptureDevice.devicesWithMediaType_(AVFoundation.AVMediaTypeMuxed) or [])
    cams: list[CameraInfo] = []

    def device_type(dev) -> str:
        try:  # es. AVCaptureDeviceTypeBuiltInWideAngleCamera, …External, …ContinuityCamera
            return str(dev.deviceType() or "")
        except Exception:  # noqa: BLE001  (macOS vecchi)
            return ""

    for index, dev in enumerate(video + muxed):
        cams.append(
            CameraInfo(
                id=f"mac:{dev.uniqueID()}",
                name=str(dev.localizedName()),
                index=index,
                api=cv2.CAP_AVFOUNDATION,
                extra={"model": str(dev.modelID() or ""), "connected": bool(dev.isConnected()),
                       "device_type": device_type(dev)},
            )
        )
    return cams
