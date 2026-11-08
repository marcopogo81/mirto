# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Permesso di accesso alla camera su macOS.

OpenCV su macOS chiede il permesso da solo, ma lo fa male: la prima volta la
camera non si apre (immagini nere, bollino verde spento) e, se la richiesta parte
da un thread secondario, non si apre affatto. Mirto quindi:

1. controlla lo stato del permesso con AVFoundation;
2. se non è ancora stato deciso, mostra la richiesta e ATTENDE la risposta;
3. solo dopo apre la camera, dicendo a OpenCV di non chiedere di nuovo
   (``OPENCV_AVFOUNDATION_SKIP_AUTH=1``).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from ..i18n import N_, _
from ..piattaforma import sistema

log = logging.getLogger(__name__)

# Stati di AVAuthorizationStatus
NOT_DETERMINED, RESTRICTED, DENIED, AUTHORIZED = 0, 1, 2, 3

HELP_DENIED = (
    N_("Accesso alla camera negato. Apri Impostazioni di Sistema → Privacy e sicurezza → Fotocamera e attiva l'app da cui avvii Mirto (es. Terminale o Mirto), poi chiudi e riapri quell'app.")
)


def prepare_opencv() -> None:
    """Da chiamare prima di aprire camere: Mirto gestisce il permesso al posto di OpenCV."""
    if sistema() == "darwin":
        os.environ.setdefault("OPENCV_AVFOUNDATION_SKIP_AUTH", "1")


def _spin(seconds: float) -> None:
    """Fa girare il run loop principale (serve alla finestra di richiesta del permesso)."""
    try:
        from Foundation import NSDate, NSRunLoop  # noqa: PLC0415

        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(seconds))
    except Exception:
        time.sleep(seconds)


def camera_permission_status() -> int:
    if sistema() != "darwin":
        return AUTHORIZED
    import AVFoundation  # noqa: PLC0415

    return int(AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(AVFoundation.AVMediaTypeVideo))


def ensure_camera_permission(timeout: float = 120.0) -> tuple[bool, str]:
    """Restituisce (autorizzato, messaggio). Su sistemi diversi da macOS è sempre autorizzato."""
    prepare_opencv()
    if sistema() != "darwin":
        return True, ""
    try:
        import AVFoundation  # noqa: PLC0415
    except ImportError:
        return True, _('PyObjC non installato: impossibile verificare il permesso Camera')

    status = camera_permission_status()
    if status == AUTHORIZED:
        return True, ""
    if status in (DENIED, RESTRICTED):
        return False, _(HELP_DENIED)
    # Non ancora deciso: mostra la richiesta e attendi la risposta dell'utente
    answered = threading.Event()
    result = {"granted": False}

    def handler(granted):
        result["granted"] = bool(granted)
        answered.set()

    log.info("Richiesta del permesso Camera in corso: rispondi alla finestra di macOS")
    AVFoundation.AVCaptureDevice.requestAccessForMediaType_completionHandler_(AVFoundation.AVMediaTypeVideo, handler)
    end = time.time() + timeout
    while not answered.is_set() and time.time() < end:
        _spin(0.1)
    if not answered.is_set():
        return False, _('Nessuna risposta alla richiesta del permesso Camera.')
    if not result["granted"]:
        return False, _(HELP_DENIED)
    time.sleep(0.5)  # lascia al sistema il tempo di attivare il permesso
    return True, _('Permesso Camera concesso.')
