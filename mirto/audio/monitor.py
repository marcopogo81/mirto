# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Ascolto continuo dei microfoni e creazione degli eventi audio.

Sorgenti:

* **microfono del Mac**: l'interfaccia (Qt) cattura l'audio e lo passa a ``feed()``;
* **microfono di un nodo**: ``NodeMicSource`` legge il flusso firmato ``GET /audio/flusso`` (API 1.3).

Per ogni sorgente un thread analizza finestre di 0,975 s ogni 0,49 s con YAMNet. Un tipo di suono
diventa un evento quando supera la sua soglia in abbastanza finestre consecutive (``classes.SOUNDS``),
con una pausa anti-raffica per sorgente e tipo. L'evento porta la foto della camera associata al
microfono (se c'è) e passa da ``core.handle_sound_event``: archivio, avvisi, dissuasori e azioni.

Questo audio non viene mai registrato né inviato: resta in memoria per pochi secondi. Non è il
microfono di un nodo dentro le registrazioni, che è un'altra catena e una scelta esplicita
(`node/mirto_node/flusso_audio.py`).
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from ..i18n import _
from .classes import SOUNDS, sound_key
from .classifier import WINDOW

log = logging.getLogger(__name__)

HOP = WINDOW // 2  # una finestra ogni ~0,49 s


@dataclass
class MicSource:
    id: str                        # "mac" o "node:<id>"
    name: str                      # "Microfono del Mac", "Ingresso (microfono)"
    camera_id: str = ""            # camera da fotografare quando si sente qualcosa
    queue: "queue.Queue[np.ndarray]" = field(default_factory=lambda: queue.Queue(maxsize=200))
    last_audio: float = 0.0
    level: float = 0.0             # livello RMS dell'ultima finestra (per l'interfaccia)
    # Il **picco** tenuto per qualche secondo, e da quando. Serve a dire se il guadagno va bene, e
    # l'RMS non lo sa dire: fra una parola e l'altra scende a zero. Senza questo, la barretta di
    # *Sistema* restava vuota con un microfono al minimo e non diceva perché (26/10).
    picco: float = 0.0
    picco_quando: float = 0.0
    error: str = ""
    last_heard: tuple = ()         # (tipo, punteggio, ora) del suono più probabile sentito di recente


class AudioMonitor:
    def __init__(self, config, classifier_factory: Callable[[], object], on_event: Callable,
                 snapshot: Callable[[str], np.ndarray | None] | None = None, clock=time.time):
        self.config = config
        self._factory = classifier_factory
        self.classifier = None
        self.on_event = on_event
        self.snapshot = snapshot
        self.clock = clock
        self.sources: dict[str, MicSource] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._last: dict[tuple[str, str], float] = {}  # (sorgente, tipo) -> ultimo evento
        self._loading = False
        self._retry_at = 0.0

    # --- sorgenti ---------------------------------------------------------
    def add_source(self, src: MicSource) -> MicSource:
        with self._lock:
            old = self.sources.get(src.id)
            if old is not None:
                old.name, old.camera_id = src.name, src.camera_id
                return old
            self.sources[src.id] = src
            t = threading.Thread(target=self._run, args=(src,), name=f"suoni-{src.id}", daemon=True)
            self._threads[src.id] = t
        t.start()
        return src

    def remove_source(self, source_id: str) -> None:
        with self._lock:
            src = self.sources.pop(source_id, None)
            self._threads.pop(source_id, None)
        if src is not None:
            try:
                src.queue.put_nowait(None)  # sveglia il thread, che vede di non essere più tra le sorgenti
            except queue.Full:
                pass

    def feed(self, source_id: str, samples_16k: np.ndarray) -> None:
        """Nuovo audio (mono, 16 kHz, float32) da una sorgente. Non blocca: se l'analisi è indietro, scarta."""
        src = self.sources.get(source_id)
        if src is None:
            return
        src.last_audio = self.clock()
        try:
            src.queue.put_nowait(np.asarray(samples_16k, dtype=np.float32))
        except queue.Full:
            pass

    def stop(self) -> None:
        self._stop.set()
        for sid in list(self.sources):
            self.remove_source(sid)

    # --- configurazione ---------------------------------------------------
    def watched(self) -> set[str]:
        """Tipi di suono da riconoscere: quelli degli avvisi più quelli usati come obiettivo dalle azioni."""
        kinds = {k for k in (self.config.get("audio.watch") or []) + (self.config.get("audio.always_notify") or [])
                 if k in SOUNDS}
        for a in self.config.get("actions.items") or []:
            if a.get("enabled", True):
                for t in a.get("targets") or []:
                    if str(t).startswith("sound:") and str(t)[6:] in SOUNDS:
                        kinds.add(str(t)[6:])
        return kinds

    # --- analisi ------------------------------------------------------------
    def _classifier(self):
        """Il modello si carica (e la prima volta si scarica) fuori da ogni lucchetto: l'interfaccia non si
        blocca. Se non riesce si riprova dopo 30 minuti, non a ogni finestra."""
        if self.classifier is not None:
            return self.classifier
        with self._lock:
            if self._loading or self.clock() < self._retry_at:
                return None
            self._loading = True
        try:
            clf = self._factory()
        except Exception:  # noqa: BLE001  (il messaggio lo dà già la fabbrica)
            clf = None
        with self._lock:
            self._loading = False
            if clf is None:
                self._retry_at = self.clock() + 1800
            else:
                self.classifier = clf
        return clf

    def _run(self, src: MicSource) -> None:
        buf = np.zeros(0, np.float32)
        history: dict[str, deque] = {}
        while not self._stop.is_set() and self.sources.get(src.id) is src:
            try:
                chunk = src.queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if chunk is None:
                continue
            buf = np.concatenate([buf, chunk])[-WINDOW * 4:]
            while len(buf) >= WINDOW:
                window, buf = buf[:WINDOW], buf[HOP:]
                try:
                    self.analyze(src, window, history)
                except Exception as exc:  # noqa: BLE001
                    src.error = str(exc)
                    log.exception("Analisi dei suoni non riuscita (%s)", src.name)
                    if self._stop.wait(30):
                        return

    def analyze(self, src: MicSource, window: np.ndarray, history: dict[str, deque]) -> list:
        """Una finestra di 0,975 s: aggiorna lo storico e crea gli eventi confermati. Restituisce gli eventi."""
        from ..actions.pannello_audio import picco_tenuto  # noqa: PLC0415

        src.level = float(np.sqrt(np.mean(np.square(window)))) if len(window) else 0.0
        src.picco, src.picco_quando = picco_tenuto(
            float(np.abs(window).max()) if len(window) else 0.0, src.picco, src.picco_quando, self.clock())
        kinds = self.watched()
        if not kinds:
            return []
        clf = self._classifier()
        if clf is None:
            return []
        if src.level < 1e-4:  # silenzio vero: inutile far lavorare il modello
            scores = {}
        else:
            scores = clf.kinds(window)
            if scores:
                best = max(scores, key=scores.get)
                if scores[best] >= 0.2:  # per l'interfaccia: "ultimo suono sentito"
                    src.last_heard = (best, scores[best], self.clock())
        now = self.clock()
        cooldown = float(self.config.get("audio.cooldown_seconds", self.config.get("notifications.cooldown_seconds", 120)))
        thresholds = self.config.get("audio.thresholds") or {}
        events = []
        for kind in kinds:
            _name, _emoji, _classes, thr, frames = SOUNDS[kind]
            thr = float(thresholds.get(kind, thr))
            h = history.setdefault(kind, deque(maxlen=max(frames, 1) + 1))
            h.append(scores.get(kind, 0.0))
            recent = list(h)[-frames:]
            if len(recent) < frames or min(recent) < thr:
                continue
            if now - self._last.get((src.id, kind), -1e12) < cooldown:
                continue
            self._last[(src.id, kind)] = now
            h.clear()
            ev = self._make_event(src, kind, max(recent))
            log.info("Suono %s da %s (%.0f%%)", kind, src.name, ev.score * 100)
            events.append(ev)
            try:
                self.on_event(ev)
            except Exception:
                log.exception("Errore nella gestione dell'evento audio")
        return events

    def _make_event(self, src: MicSource, kind: str, score: float):
        from ..events import Event  # noqa: PLC0415

        image = None
        if self.snapshot and src.camera_id:
            try:
                image = self.snapshot(src.camera_id)
            except Exception:  # noqa: BLE001
                image = None
        if image is None:
            image = sound_card(_(SOUNDS[kind][0]), src.name)
        ev = Event(camera_id=src.camera_id or f"mic:{src.id}", camera_name=src.name, score=score,
                   image=image, ts=self.clock(), label=sound_key(kind))
        ev.detail = _("sentito da {source}", source=src.name)
        return ev

    @property
    def model_state(self) -> str:
        """"ready", "loading", "error" o "" (non ancora richiesto)."""
        if self.classifier is not None:
            return "ready"
        if self._loading:
            return "loading"
        return "error" if self._retry_at else ""

    def status(self) -> list[dict]:
        now = self.clock()
        return [{"id": s.id, "name": s.name, "live": now - s.last_audio < 5, "level": s.level,
                 "picco": s.picco, "error": s.error,
                 "last_heard": s.last_heard} for s in list(self.sources.values())]


def sound_card(title: str, subtitle: str) -> np.ndarray:
    """Immagine per un suono senza camera associata: riquadro scuro con il tipo di suono."""
    import cv2  # noqa: PLC0415

    img = np.full((360, 640, 3), (40, 33, 26), np.uint8)
    cv2.putText(img, title.encode("ascii", "replace").decode(), (30, 170), cv2.FONT_HERSHEY_SIMPLEX, 1.4,
                (255, 255, 255), 3, cv2.LINE_AA)
    cv2.putText(img, subtitle.encode("ascii", "replace").decode(), (30, 230), cv2.FONT_HERSHEY_SIMPLEX, 0.9,
                (200, 200, 200), 2, cv2.LINE_AA)
    return img


class NodeMicSource:
    """Legge il microfono di un nodo: ``GET /audio/flusso`` firmato, PCM 16 bit mono a 16 kHz.

    Il flusso si riapre da solo se cade (nodo riavviato, Wi-Fi assente), con attese crescenti.
    """

    def __init__(self, client, monitor: AudioMonitor, source_id: str):
        self.client = client
        self.monitor = monitor
        self.source_id = source_id
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"mic-{source_id}", daemon=True)

    def start(self) -> "NodeMicSource":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        backoff = 2.0
        while not self._stop.is_set():
            src = self.monitor.sources.get(self.source_id)
            try:
                for chunk in self.client.stream_audio(stop=self._stop):
                    pcm = np.frombuffer(chunk[: len(chunk) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
                    self.monitor.feed(self.source_id, pcm)
                    backoff = 2.0
                    if src is not None:
                        src.error = ""
            except Exception as exc:  # noqa: BLE001
                if src is not None:
                    src.error = str(exc)
                log.info("Microfono del nodo %s: %s (nuovo tentativo tra %.0f s)", self.source_id, exc, backoff)
            if self._stop.wait(backoff):
                return
            backoff = min(backoff * 2, 120.0)

