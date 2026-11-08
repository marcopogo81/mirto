# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Microfono del nodo (USB o vivavoce): un solo ``arecord`` condiviso tra chi ascolta.

* ``GET /audio/flusso``: Mirto riceve l'audio in diretta (PCM 16 bit mono 16 kHz) e riconosce
  i suoni (vetro rotto, allarme fumo, cane…). L'audio non viene salvato sul nodo.
* ``POST /audio/registra``: registra N secondi e li restituisce come WAV (comando ``/ascolta``).

``arecord`` parte solo quando c'è almeno un ascoltatore e si ferma quando non ce ne sono più.
"""

from __future__ import annotations

import io
import logging
import queue
import subprocess
import threading
import time
import wave

log = logging.getLogger(__name__)

RATE = 16000
CHUNK = 3200  # 0,1 s di audio
# Quattro e non tre: dal 26/10 uno di questi posti lo tiene **sempre** occupato il muxer che
# pubblica il flusso con l'audio (`flusso_audio.py`), quindi con tre ne restavano due e un `/ascolta`
# da Telegram mentre il riconoscimento dei suoni lavora si sentiva dire «troppi ascolti».
MAX_LISTENERS = 4


class MicError(RuntimeError):
    pass


def alsa_capture_device(card: str) -> str:
    """Scheda ALSA per nome (stabile anche se cambia numero), oppure quella predefinita."""
    return f"plughw:CARD={card}" if card else "default"


class Microphone:
    def __init__(self, card: str = "", popen=subprocess.Popen, rate: int = RATE, manopole=None):
        self.card = card
        self.rate = rate
        # mixer.Manopole, o None. Un microfono USB nasce col guadagno al minimo e l'automatismo
        # acceso, e in quello stato una voce arriva all'1% del fondo scala: il riconoscimento non
        # sente niente e nessuno capisce perche' (misurato il 22/10, vedi mixer.py).
        self.manopole = manopole
        self._popen = popen
        self._proc = None
        self._listeners: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.last_error = ""

    @property
    def device(self) -> str:
        return alsa_capture_device(self.card)

    def listeners(self) -> int:
        with self._lock:
            return len(self._listeners)

    def subscribe(self) -> queue.Queue:
        with self._lock:
            if len(self._listeners) >= MAX_LISTENERS:
                raise MicError("troppi ascolti contemporanei")
            q: queue.Queue = queue.Queue(maxsize=100)
            self._listeners.append(q)
            if self._proc is None:
                self._start()
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._listeners:
                self._listeners.remove(q)
            if not self._listeners:
                self._stop_proc()

    def _start(self) -> None:
        if self.manopole is not None and self.card:
            self.manopole.prepara(self.card, "cattura")
        try:
            self._proc = self._popen(["arecord", "-q", "-D", self.device, "-f", "S16_LE", "-r", str(self.rate),
                                      "-c", "1", "-t", "raw"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as exc:
            self.last_error = f"arecord non avviabile: {exc}"
            raise MicError(self.last_error) from exc
        self.last_error = ""
        proc = self._proc
        self._thread = threading.Thread(target=self._pump, args=(proc,), name="microfono", daemon=True)
        self._thread.start()

    def _pump(self, proc) -> None:
        while True:
            data = proc.stdout.read(CHUNK) if proc.stdout else b""
            if not data:
                break
            with self._lock:
                targets = list(self._listeners)
            for q in targets:
                try:
                    q.put_nowait(data)
                except queue.Full:  # ascoltatore lento: perde un pezzo, gli altri no
                    pass
        err = b""
        try:
            err = proc.stderr.read() if proc.stderr else b""
        except Exception:  # noqa: BLE001
            pass
        with self._lock:
            mine = self._proc is proc
            if mine:
                self._proc = None
                self.last_error = (err.decode(errors="replace").strip() or "microfono scollegato")[:200]
                # Se il microfono e' stato staccato, quando torna sara' ai valori di fabbrica suoi:
                # le manopole vanno rifatte, altrimenti riparte sordo.
                if self.manopole is not None and self.card:
                    self.manopole.dimentica(self.card)
            targets = list(self._listeners) if mine else []
        if mine:
            log.warning("Microfono fermo: %s", self.last_error)
        for q in targets:  # avvisa gli ascoltatori che il flusso è finito, senza mai bloccarsi
            try:
                q.put_nowait(None)
            except queue.Full:
                try:
                    q.get_nowait()  # coda piena (ascoltatore lento): si fa posto togliendo il pezzo più vecchio
                    q.put_nowait(None)
                except (queue.Empty, queue.Full):
                    pass

    def _stop_proc(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.terminate()
            except Exception:  # noqa: BLE001
                pass

    def stream(self, stop: threading.Event | None = None):
        """Pezzi di PCM finché il microfono funziona (o fino a ``stop``)."""
        q = self.subscribe()
        try:
            while not (stop is not None and stop.is_set()):
                try:
                    data = q.get(timeout=5)
                except queue.Empty:
                    raise MicError(self.last_error or "il microfono non manda audio") from None
                if data is None:
                    raise MicError(self.last_error or "microfono scollegato")
                yield data
        finally:
            self.unsubscribe(q)

    def record(self, seconds: float) -> bytes:
        """Registra ``seconds`` secondi e restituisce un WAV."""
        need = int(seconds * self.rate) * 2
        buf = bytearray()
        deadline = time.monotonic() + seconds + 10
        for data in self.stream():
            buf.extend(data)
            if len(buf) >= need or time.monotonic() > deadline:
                break
        out = io.BytesIO()
        with wave.open(out, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.rate)
            w.writeframes(bytes(buf[:need]))
        return out.getvalue()

    def close(self) -> None:
        with self._lock:
            self._listeners.clear()
            self._stop_proc()
