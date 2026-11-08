# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Microfono del Mac (o del PC) per il riconoscimento dei suoni, con Qt Multimedia.

Si accende e si spegne da solo seguendo ``audio.mac_mic`` (Impostazioni → Suoni). L'audio passa
in memoria al monitor dei suoni (``core.audio``) e non viene mai salvato né inviato.
"""

from __future__ import annotations

import logging
import time

import numpy as np
from PySide6.QtCore import QObject, QTimer

from ..audio.classifier import to_mono_16k
from ..i18n import _, questo_computer

log = logging.getLogger(__name__)

SOURCE_ID = "mac"


class MacMicCapture(QObject):
    def __init__(self, core, parent=None):
        super().__init__(parent)
        self.core = core
        self.source = None
        self.io = None
        self.fmt = None
        self.error = ""
        self._asked = False
        self.started_at = 0.0
        self.peak = 0.0
        self._silent = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.sync)
        self.timer.start(3000)
        QTimer.singleShot(1500, self.sync)

    @property
    def running(self) -> bool:
        return self.source is not None

    def sync(self) -> None:
        want = bool(self.core.config.get("audio.mac_mic", False))
        if want and not self.running:
            self.start()
        elif not want and self.running:
            self.stop()
        elif self.running and self.core.audio and SOURCE_ID in self.core.audio.sources:
            self.core.audio.sources[SOURCE_ID].camera_id = self.core.mac_mic_camera()

    def _permission_ok(self) -> bool:
        """macOS: chiede il permesso Microfono una volta (Qt 6.5+).

        Solo nel pacchetto .app: avviando da Terminale la richiesta di Qt non funziona (serve
        NSMicrophoneUsageDescription nell'Info.plist del programma), e il permesso lo chiede macOS
        all'app da cui si avvia Mirto (es. Terminale) appena si apre il microfono.
        """
        import sys  # noqa: PLC0415

        if not getattr(sys, "frozen", False):
            return True
        try:
            from PySide6.QtCore import QMicrophonePermission, Qt  # noqa: PLC0415
            from PySide6.QtWidgets import QApplication  # noqa: PLC0415
        except ImportError:
            return True
        app = QApplication.instance()
        perm = QMicrophonePermission()
        status = app.checkPermission(perm)
        if status == Qt.PermissionStatus.Granted:
            return True
        if status == Qt.PermissionStatus.Undetermined and not self._asked:
            self._asked = True
            app.requestPermission(perm, self, lambda _p: self.sync())
            return False
        if status == Qt.PermissionStatus.Denied:
            self.error = _("Permesso Microfono negato: Impostazioni di Sistema → Privacy e sicurezza → Microfono")
        return False

    def start(self) -> None:
        try:
            from PySide6.QtMultimedia import QAudioSource, QMediaDevices  # noqa: PLC0415
        except ImportError as exc:
            self.error = _("Qt Multimedia non disponibile: {exc}", exc=exc)
            return
        if not self._permission_ok():
            return
        dev = QMediaDevices.defaultAudioInput()
        if dev.isNull():
            self.error = _("Nessun microfono trovato")
            return
        self.fmt = dev.preferredFormat()
        self.source = QAudioSource(dev, self.fmt, self)
        self.io = self.source.start()
        if self.io is None:
            self.error = _("Microfono non avviabile")
            self.source = None
            return
        self.io.readyRead.connect(self._read)
        from ..audio.monitor import MicSource  # noqa: PLC0415

        self.core.ensure_audio().add_source(MicSource(SOURCE_ID, _("Microfono del {computer}", computer=questo_computer()),
                                                      camera_id=self.core.mac_mic_camera()))
        self.error = ""
        self.started_at = time.time()
        self.peak = 0.0
        log.info("Microfono del Mac in ascolto (%s, %d Hz, %d canali)", dev.description(),
                 self.fmt.sampleRate(), self.fmt.channelCount())

    def _read(self) -> None:
        if self.io is None or self.fmt is None:
            return
        data = bytes(self.io.readAll().data())
        if not data:
            return
        from PySide6.QtMultimedia import QAudioFormat  # noqa: PLC0415

        sf = self.fmt.sampleFormat()
        dtype = {QAudioFormat.SampleFormat.Int16: np.int16, QAudioFormat.SampleFormat.Int32: np.int32,
                 QAudioFormat.SampleFormat.Float: np.float32, QAudioFormat.SampleFormat.UInt8: np.uint8}.get(sf)
        if dtype is None:
            return
        width = np.dtype(dtype).itemsize
        arr = np.frombuffer(data[: len(data) // width * width], dtype=dtype)
        samples = to_mono_16k(arr, self.fmt.sampleRate(), self.fmt.channelCount())
        if len(samples):
            self.peak = max(self.peak, float(np.abs(samples).max()))
        # solo zeri per 10 s: di solito è il permesso Microfono negato (macOS manda silenzio, non un errore)
        if self.peak == 0.0 and time.time() - self.started_at > 10:
            self.error = _("Il microfono manda solo silenzio: controlla Impostazioni di Sistema → Privacy e "
                           "sicurezza → Microfono (l'app da cui avvii Mirto, es. Terminale)")
            self._silent = True
        elif self.peak > 0 and self._silent:
            self.error, self._silent = "", False
        if self.core.audio:
            self.core.audio.feed(SOURCE_ID, samples)

    def stop(self) -> None:
        if self.source is not None:
            try:
                self.source.stop()
            except Exception:  # noqa: BLE001
                pass
        self.source = None
        self.io = None
        if self.core.audio:
            self.core.audio.remove_source(SOURCE_ID)
        log.info("Microfono del Mac spento")
