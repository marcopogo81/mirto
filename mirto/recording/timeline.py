# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Logica della vista Rivedi, senza interfaccia: giornate, tratti registrati, punto da riprodurre, eventi.

Tutti i tempi sono secondi epoch; le giornate sono quelle dell'ora locale (mezzanotte → mezzanotte, anche nei
giorni del cambio dell'ora, che durano 23 o 25 ore).
"""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass

from .store import Segment

GAP_TOLERANCE = 3.0   # due spezzoni distanti meno di così sono un unico tratto continuo


def day_bounds(day: dt.date) -> tuple[float, float]:
    """Inizio e fine (epoch) della giornata locale ``day``."""
    start = dt.datetime.combine(day, dt.time.min)
    end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time.min)
    return time.mktime(start.timetuple()), time.mktime(end.timetuple())


def spans(segments: list[Segment], t0: float, t1: float, gap: float = GAP_TOLERANCE) -> list[tuple[float, float]]:
    """Tratti registrati dentro [t0, t1], uniti quando i buchi sono più corti di ``gap`` secondi."""
    out: list[list[float]] = []
    for s in sorted(segments, key=lambda x: x.start):
        a, b = max(s.start, t0), min(s.end, t1)
        if b <= a:
            continue
        if out and a - out[-1][1] <= gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


@dataclass
class Position:
    segment: Segment
    offset: float      # secondi dall'inizio dello spezzone
    exact: bool        # False: in ``t`` non c'era registrazione, si parte dal tratto successivo


def locate(segments: list[Segment], t: float) -> Position | None:
    """Lo spezzone da aprire per vedere l'istante ``t`` (o il primo dopo, se in ``t`` c'è un buco)."""
    segs = sorted(segments, key=lambda x: x.start)
    for s in segs:
        if s.start <= t < max(s.end, s.start + 0.5):
            return Position(s, t - s.start, True)
    after = next((s for s in segs if s.start > t), None)
    return Position(after, 0.0, False) if after else None


def next_segment(segments: list[Segment], current: Segment) -> Segment | None:
    """Lo spezzone che viene dopo ``current`` (per la riproduzione continua)."""
    segs = sorted(segments, key=lambda x: x.start)
    return next((s for s in segs if s.start > current.start), None)


def clip_segments(events: list[dict], segments: list[Segment], pre: float, post: float) -> list[Segment]:
    """Clip degli eventi (accanto alla foto) come spezzoni guardabili, dove non c'è già una registrazione.

    Servono per le camere in modalità «solo eventi»: sulla barra compaiono come tratti e si guardano con un clic."""
    from pathlib import Path  # noqa: PLC0415

    out = []
    for e in events:
        img = e.get("image_path") or ""
        if not img:
            continue
        clip = Path(img).with_suffix(".mp4")
        if not clip.exists():
            continue
        a, b = e["ts"] - pre, e["ts"] + post
        if any(s.start < b and s.end > a for s in segments):
            continue  # c'è già registrazione in quel periodo: niente doppioni (pezzi visti due volte)
        try:
            size = clip.stat().st_size
        except OSError:
            continue
        out.append(Segment(clip, a, b, size))
    return out


def covered(segments: list[Segment], a: float, b: float) -> float:
    """Quanti secondi di [a, b] sono coperti dagli spezzoni (che possono sovrapporsi)."""
    total = 0.0
    for x, y in spans(segments, a, b, gap=0.0):
        total += y - x
    return total


def playable_events(events: list[dict], segments: list[Segment]) -> list[dict]:
    """Gli eventi che si possono guardare: c'è un video (registrazione o clip) nel loro istante."""
    return [e for e in events if any(s.start - 1 <= e["ts"] <= s.end + 1 for s in segments)]


def event_color_kind(label: str) -> str:
    """"person" (rosso), "sound" (azzurro) o "other" (viola: animali, veicoli, oggetti)."""
    if label in ("", "person"):
        return "person"
    if label.startswith("sound:"):
        return "sound"
    return "other"


def group_events(events: list[dict], t0: float, t1: float, x_of, min_px: float = 4.0) -> list[tuple]:
    """Eventi della finestra visibile raggruppati per posizione: ``(x, genere, quanti)``.

    Due eventi più vicini di ``min_px`` diventano un puntino solo, un po' più grande: altrimenti una
    giornata piena riempie la barra di blocchi indistinguibili. Il genere del gruppo è il più
    importante fra quelli dentro — una persona vince su un animale, che vince su un suono — perché il
    puntino deve dire «qui c'è qualcosa da guardare», non fare una media.

    Sta qui, e non nel disegno, per due motivi: si può provare senza aprire una finestra, e la barra
    può calcolarlo **una volta sola** invece che a ogni ridisegno. Prima si ordinava e si raggruppava
    tutto a ogni `paintEvent`, cioè dieci volte al secondo mentre il video va: con pochi eventi non si
    nota, con una giornata piena la barra diventa lenta (visto sul PC Windows il 19/10, dopo qualche
    ora di registrazione).
    """
    gruppi: list[list] = []
    for e in sorted((e for e in events if t0 <= e["ts"] <= t1), key=lambda e: e["ts"]):
        x = x_of(e["ts"])
        genere = event_color_kind(e.get("label") or "person")
        if gruppi and x - gruppi[-1][0] < min_px:
            gruppi[-1][1].append(genere)
        else:
            gruppi.append([x, [genere]])
    fuori = []
    for x, generi in gruppi:
        principale = "person" if "person" in generi else ("other" if "other" in generi else "sound")
        fuori.append((x, principale, len(generi)))
    return fuori


def neighbor_event(events: list[dict], t: float, forward: bool) -> dict | None:
    """L'evento più vicino dopo (o prima) dell'istante ``t``; lo scarto di 1 s evita di restare fermi sullo stesso."""
    if forward:
        later = [e for e in events if e["ts"] > t + 1]
        return min(later, key=lambda e: e["ts"]) if later else None
    earlier = [e for e in events if e["ts"] < t - 1]
    return max(earlier, key=lambda e: e["ts"]) if earlier else None


def fmt_clock(t: float, seconds: bool = True) -> str:
    return time.strftime("%H:%M:%S" if seconds else "%H:%M", time.localtime(t))
