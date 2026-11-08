# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Analisi per camera: movimento → persona (o altra categoria scelta) → evento.

Le categorie sorvegliate sono in ``detection.watch`` (predefinito: solo ``person``).
Due livelli di conferma, entrambi configurabili:

* **notifica**: confidenza ≥ ``detection.person_threshold`` (0,40) per le persone,
  ≥ ``detection.object_threshold`` (0,50) per animali, veicoli e oggetti,
  in almeno ``confirm_frames`` (2) degli ultimi ``window_frames`` (5) frame;
  ogni categoria ha la sua pausa anti-spam;
* **azioni** (solo persone): confidenza ≥ ``actions.person_threshold`` (0,60) in 3 frame su 5;
* **dissuasori** (animali, uccelli): le categorie scelte come obiettivo di un'azione "dissuasore"
  vengono controllate anche se non sono in ``detection.watch``; superata la soglia di notifica
  si chiama ``on_detect`` (anche con gli avvisi silenziati). Per gli uccelli, se serve, un secondo
  modello dice il gruppo (piccioni, corvidi…).

Ogni livello scatta al massimo una volta per pausa anti-spam
(``notifications.cooldown_seconds``) per camera. "Silenzia" sospende solo gli
avvisi: con il sistema inserito le azioni restano attive (il disinserimento le ferma).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Callable

from ..cameras.source import CameraSource
from ..config import Config
from ..events import Event
from .motion import MotionDetector, brightness
from .labels import INDEX, KEYS, watched_indices
from .person import PERSON_CLASS, PersonDetector, annotate

log = logging.getLogger(__name__)

MOTION_HOLD_SECONDS = 3.0  # continua a cercare persone per qualche secondo dopo il movimento
BIRD_CLASS = INDEX["bird"]


def deterrent_indices(config: Config) -> list[int]:
    """Categorie COCO richieste dalle azioni "dissuasore" attive ("bird:corvid" -> bird)."""
    out = set()
    for a in config.get("actions.items") or []:
        if a.get("kind") != "deterrent" or not a.get("enabled", True):
            continue
        for t in a.get("targets") or []:
            base = "bird" if str(t).startswith("bird:") else str(t)
            if base in INDEX and base != "person":
                out.add(INDEX[base])
    return sorted(out)


def needs_species(config: Config) -> bool:
    """Il classificatore delle specie serve solo se qualcuno lo usa (avvisi o dissuasori)."""
    if config.get("detection.bird_species", True) and "bird" in (config.get("detection.watch") or []):
        return True
    return any(str(t).startswith("bird:") for a in (config.get("actions.items") or [])
               if a.get("kind") == "deterrent" and a.get("enabled", True) for t in (a.get("targets") or []))


class CameraPipeline:
    def __init__(
        self,
        source: CameraSource,
        detector: PersonDetector,
        config: Config,
        on_event: Callable[[Event], None],
        on_action: Callable[[Event], None] | None = None,
        on_state: Callable[[str, dict], None] | None = None,
        clock: Callable[[], float] = time.time,
        on_detect: Callable[[Event], None] | None = None,
        species: Callable[[], object] | None = None,
    ):
        self.source = source
        self.detector = detector
        self.config = config
        self.on_event = on_event
        self.on_action = on_action
        self.on_state = on_state
        self.clock = clock
        self.on_detect = on_detect
        self.species = species      # restituisce il classificatore delle specie (o None se non serve/non c'è)
        ov = (config.get("cameras.overrides") or {}).get(source.info.id, {})
        self.motion = MotionDetector(
            min_area=config.get("detection.motion_min_area", 0.01),
            sensitivity=ov.get("motion_sensitivity", 0.5),
            ignore_zones=ov.get("ignore_zones", []),
        )
        n_win = int(config.get("detection.window_frames", 5))
        a_win = int(config.get("actions.window_frames", 5))
        self._n_win = n_win
        self.windows: dict[int, deque[float]] = {}   # per categoria: punteggi degli ultimi frame
        self.action_window: deque[float] = deque(maxlen=a_win)
        self.last_notify: dict[int, float] = {}       # per categoria
        self.last_action = 0.0
        self.motion_until = 0.0
        self.dark = False
        self.moving = False
        self.paused_until = 0.0         # "Silenzia" da Telegram
        self._best: dict[int, tuple[float, object, list]] = {}  # per categoria: punteggio, frame, rilevamenti
        self._best_raw: dict[int, tuple[object, list]] = {}     # frame non annotato dell'ultimo evento (specie)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # --- ciclo ----------------------------------------------------------
    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"analisi-{self.source.info.name}", daemon=True)
        self._thread.start()

    def request_stop(self) -> None:
        self._stop.set()

    def join(self, timeout: float = 5.0) -> None:
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None

    def stop(self) -> None:
        self.request_stop()
        self.join()

    def _run(self) -> None:
        period = 1.0 / max(0.5, float(self.config.get("detection.analysis_fps", 5)))
        seq = -1
        while not self._stop.is_set():
            t0 = time.monotonic()
            frame, _ts, new_seq = self.source.wait_frame(seq, timeout=1.0)
            if frame is not None and new_seq != seq:
                seq = new_seq
                try:
                    self.process(frame)
                except Exception:
                    log.exception("Errore di analisi su %s", self.source.info.name)
            self._stop.wait(max(0.0, period - (time.monotonic() - t0)))

    # --- singolo frame (testabile) -------------------------------------
    def process(self, frame) -> None:
        now = self.clock()
        cam = self.source.info

        is_dark = brightness(frame) < float(self.config.get("detection.dark_threshold", 25))
        if is_dark != self.dark:
            self.dark = is_dark
            self._state({"dark": is_dark})

        moving = self.motion.update(frame)
        if moving:
            self.motion_until = now + MOTION_HOLD_SECONDS
        active = now <= self.motion_until
        if active != self.moving:
            self.moving = active
            self._state({"motion": active})
        if not active:
            self.windows.clear()
            self.action_window.clear()
            self._best.clear()
            self._best_raw.clear()
            return

        notify_watch = watched_indices(self.config.get("detection.watch"))
        deterrents = deterrent_indices(self.config)
        # con i dissuasori le persone si cercano SEMPRE: se c'è qualcuno, nessun suono parte
        watch = sorted(set(notify_watch) | set(deterrents) | ({PERSON_CLASS} if deterrents else set()))
        n_thr = float(self.config.get("detection.person_threshold", 0.40))
        o_thr = float(self.config.get("detection.object_threshold", 0.50))
        a_thr = float(self.config.get("actions.person_threshold", 0.60))
        thr_of = {c: (n_thr if c == PERSON_CLASS else o_thr) for c in watch}
        low = min(list(thr_of.values()) + ([a_thr] if PERSON_CLASS in watch else []))
        dets = self.detector.detect(frame, threshold=low, classes=watch)
        top: dict[int, float] = {}
        for d in dets:
            top[d.cls] = max(top.get(d.cls, 0.0), d.score)
        for c in list(self.windows):
            if c not in watch:  # categoria tolta dalle Impostazioni
                self.windows.pop(c, None)
                self._best.pop(c, None)
        for c in watch:
            self.windows.setdefault(c, deque(maxlen=self._n_win)).append(top.get(c, 0.0))
            if c in top and (c not in self._best or top[c] > self._best[c][0]):
                self._best[c] = (top[c], frame, dets)
        person_watched = PERSON_CLASS in notify_watch  # avvisi e azioni per le persone: solo se scelte
        self.action_window.append(top.get(PERSON_CLASS, 0.0) if person_watched else 0.0)

        cooldown = float(self.config.get("notifications.cooldown_seconds", 120))
        confirm = int(self.config.get("detection.confirm_frames", 2))
        a_ok = person_watched and \
            sum(s >= a_thr for s in self.action_window) >= int(self.config.get("actions.confirm_frames", 3))

        person_near = any(s >= n_thr for s in self.windows.get(PERSON_CLASS, ()))  # persona negli ultimi frame
        for c in watch:
            if c == PERSON_CLASS and not person_watched:
                continue  # cercata solo per fermare i dissuasori
            n_ok = sum(s >= thr_of[c] for s in self.windows[c]) >= confirm
            is_person = c == PERSON_CLASS
            if n_ok and now - self.last_notify.get(c, 0.0) >= cooldown and c in self._best:
                self.last_notify[c] = now
                ev = self._make_event(now, c, action_level=is_person and a_ok)
                if c == BIRD_CLASS:
                    self._add_species(ev, c)
                if c in notify_watch and now >= self.paused_until:  # "Silenzia" ferma solo gli avvisi
                    log.info("%s su %s (%.0f%%)", KEYS[c], cam.name, ev.score * 100)
                    self.on_event(ev)
                if not is_person and self.on_detect:  # dissuasori: anche con avvisi silenziati
                    if person_near:
                        log.info("Dissuasori non avviati su %s: c'è anche una persona", cam.name)
                    else:
                        self.on_detect(ev)
                if is_person and a_ok:
                    self.last_action = now
                    if self.on_action:
                        self.on_action(ev)
                self._best.pop(c, None)  # la prossima foto sarà quella del nuovo episodio
            elif is_person and a_ok and self.on_action and now - self.last_action >= cooldown and c in self._best:
                # conferma "forte" arrivata dopo la notifica: avvia le azioni
                self.last_action = now
                ev = self._make_event(now, c, action_level=True)
                self.on_action(ev)
                self._best.pop(c, None)

    def _make_event(self, now: float, cls: int, action_level: bool) -> Event:
        score, frame, dets = self._best[cls]
        self._best_raw[cls] = (frame, dets)
        cam = self.source.info
        n_thr = float(self.config.get("detection.person_threshold", 0.40))
        o_thr = float(self.config.get("detection.object_threshold", 0.50))
        # nella foto solo i riquadri sopra la propria soglia (il rilevatore lavora alla soglia più bassa)
        dets = [d for d in dets if d.score >= (n_thr if d.cls == PERSON_CLASS else o_thr)]
        ev = Event(camera_id=cam.id, camera_name=cam.name, score=score, image=frame, ts=now,
                   boxes=[d.box for d in dets if d.cls == cls], action_level=action_level, label=KEYS[cls])
        ev.image = annotate(frame, dets, caption=f"{cam.name}  {ev.when_text()}")
        return ev

    def _add_species(self, ev: Event, cls: int) -> None:
        """Gruppo e specie dell'uccello più grande nella foto (se il classificatore c'è e serve)."""
        clf = self.species() if self.species else None
        if clf is None or cls not in self._best_raw:
            return
        frame, dets = self._best_raw.pop(cls)
        birds = [d for d in dets if d.cls == cls]
        if not birds:
            return
        d = max(birds, key=lambda b: (b.box[2] - b.box[0]) * (b.box[3] - b.box[1]))
        try:
            res = clf.classify(frame, d.box)
        except Exception:  # noqa: BLE001  (la specie è un di più: l'evento resta valido)
            log.exception("Classificazione della specie non riuscita")
            return
        if res is None:
            return
        min_score = float(self.config.get("detection.species_threshold", 0.35))
        if res.group and res.group_score >= min_score:
            ev.species_group = res.group
            ev.species = res.display
        log.info("Uccello su %s: %s (gruppo %s %.0f%%, specie %.0f%%)", ev.camera_name, res.scientific,
                 res.group or "-", res.group_score * 100, res.score * 100)

    def _state(self, change: dict) -> None:
        if self.on_state:
            self.on_state(self.source.info.id, change)
