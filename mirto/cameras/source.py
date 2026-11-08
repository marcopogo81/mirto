# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Acquisizione da una camera in un thread dedicato, con riconnessione automatica."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable

import numpy as np

from .discovery import CameraInfo
from .permissions import prepare_opencv
from ..i18n import _

prepare_opencv()

log = logging.getLogger(__name__)

# Per le camere RTSP: trasporto TCP (più affidabile del UDP sulle reti domestiche)
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
NETWORK_TIMEOUT_MS = 5000  # apertura e lettura delle camere di rete

STATUS_CONNECTING = "connecting"
STATUS_ONLINE = "online"
STATUS_OFFLINE = "offline"
STATUS_STOPPED = "stopped"


def open_capture(info: CameraInfo):
    """Apre ``cv2.VideoCapture`` per una camera locale o di rete."""
    import cv2  # noqa: PLC0415

    if info.kind == "network":
        # tempi massimi di apertura e lettura: se la camera sparisce dalla rete (cavo
        # staccato, IP cambiato) la lettura non resta bloccata e la camera si riapre
        params = []
        for prop, ms in (("CAP_PROP_OPEN_TIMEOUT_MSEC", NETWORK_TIMEOUT_MS), ("CAP_PROP_READ_TIMEOUT_MSEC", NETWORK_TIMEOUT_MS)):
            if hasattr(cv2, prop):
                params += [getattr(cv2, prop), ms]
        if params:
            try:
                return cv2.VideoCapture(info.url, cv2.CAP_FFMPEG, params)
            except TypeError:  # OpenCV vecchio senza parametri
                pass
        return cv2.VideoCapture(info.url, cv2.CAP_FFMPEG)
    if info.api is not None:
        return cv2.VideoCapture(info.index, info.api)
    return cv2.VideoCapture(info.index)


class CameraSource:
    """Legge continuamente i frame e tiene sempre disponibile l'ultimo.

    Se la camera smette di rispondere, la riapre con attese crescenti
    (1 s, 2 s, 4 s… fino a ``max_backoff``).
    """

    def __init__(
        self,
        info: CameraInfo,
        capture_factory: Callable[[CameraInfo], object] = open_capture,
        max_backoff: float = 30.0,
        read_timeout: float = 5.0,
        on_status: Callable[[str, str], None] | None = None,
    ):
        self.info = info
        self._factory = capture_factory
        self.max_backoff = max_backoff
        self.read_timeout = read_timeout
        self.on_status = on_status
        # lo imposta il CameraManager: «utente:password» della camera, dal portachiavi (mai in info.url)
        self.credentials: Callable[[], str] | None = None
        self.status = STATUS_STOPPED
        self._frame: np.ndarray | None = None
        self._ts = 0.0
        self._seq = 0
        self._lock = threading.Lock()
        self._new_frame = threading.Condition(self._lock)
        self._stop = threading.Event()
        self._reopen = threading.Event()
        self._thread: threading.Thread | None = None
        self.fps = 0.0

    # --- API pubblica -------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"cam-{self.info.name}", daemon=True)
        self._thread.start()

    def request_stop(self) -> None:
        """Chiede l'arresto senza attendere (per fermare molte camere in parallelo)."""
        self._stop.set()
        with self._lock:
            self._new_frame.notify_all()

    def join(self, timeout: float = 5.0) -> None:
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None
        self._set_status(STATUS_STOPPED)

    def stop(self) -> None:
        self.request_stop()
        self.join()

    def stream_url(self) -> str:
        """Indirizzo con cui si apre davvero il flusso: quello salvato più le credenziali del portachiavi.

        ``info.url`` resta senza utente e password, così non finisce nei registri né in configurazione.
        """
        from ..netutil import with_credentials  # noqa: PLC0415

        url = self.info.url or ""
        try:
            creds = self.credentials() if self.credentials is not None else ""
        except Exception:  # noqa: BLE001
            log.exception("Credenziali della camera %s non leggibili", self.info.name)
            creds = ""
        return with_credentials(url, creds) if creds else url

    def update_info(self, info: CameraInfo) -> None:
        """Nuovo indice o URL: la camera viene riaperta."""
        self.info = info
        self._reopen.set()

    def latest(self) -> tuple[np.ndarray | None, float, int]:
        with self._lock:
            return self._frame, self._ts, self._seq

    def wait_frame(self, after_seq: int, timeout: float = 1.0) -> tuple[np.ndarray | None, float, int]:
        """Attende un frame più nuovo di ``after_seq``."""
        with self._lock:
            if self._seq <= after_seq:
                self._new_frame.wait(timeout)
            return self._frame, self._ts, self._seq

    # --- interno ------------------------------------------------------
    def _set_status(self, status: str) -> None:
        if status != self.status:
            self.status = status
            log.info("Camera %s: %s", self.info.name, status)
            if self.on_status:
                try:
                    self.on_status(self.info.id, status)
                except Exception:
                    log.exception("Errore nel callback di stato")

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            self._set_status(STATUS_CONNECTING)
            self._reopen.clear()
            cap = None
            try:
                cap = self._factory(self.info)
                if cap is None or not cap.isOpened():
                    raise RuntimeError(_('impossibile aprire la camera'))
                backoff = 1.0
                self._read_loop(cap)
            except Exception as exc:
                log.warning("Camera %s: %s", self.info.name, exc)
            finally:
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass
            if self._stop.is_set():
                break
            if self._reopen.is_set():
                continue
            self._set_status(STATUS_OFFLINE)
            self._stop.wait(backoff)
            backoff = min(backoff * 2, self.max_backoff)

    def _read_loop(self, cap) -> None:
        last_ok = time.monotonic()
        count, t0 = 0, time.monotonic()
        while not self._stop.is_set() and not self._reopen.is_set():
            ok, frame = cap.read()
            now = time.monotonic()
            if not ok or frame is None:
                if now - last_ok > self.read_timeout:
                    raise RuntimeError(_('nessuna immagine da %.0f s') % self.read_timeout)
                time.sleep(0.05)
                continue
            last_ok = now
            with self._lock:
                self._frame = frame
                self._ts = time.time()
                self._seq += 1
                self._new_frame.notify_all()
            if self.status != STATUS_ONLINE:
                self._set_status(STATUS_ONLINE)
            count += 1
            if now - t0 >= 2.0:
                self.fps = count / (now - t0)
                count, t0 = 0, now
