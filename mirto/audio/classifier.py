# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Riconoscimento dei suoni con YAMNet (Google, Apache 2.0) in formato ONNX.

Ingresso: audio mono a 16 kHz, float32 tra -1 e 1. YAMNet analizza finestre di 0,96 s
(15 600 campioni) e restituisce 521 punteggi AudioSet per finestra; qui i punteggi sono
riassunti nei tipi di ``classes.SOUNDS`` (vetro rotto, allarme fumo, cane…).
"""

from __future__ import annotations

import csv
import logging
import threading
from pathlib import Path

import numpy as np

from .classes import SOUNDS

log = logging.getLogger(__name__)

RATE = 16000
WINDOW = 15600  # 0,975 s: una finestra di YAMNet


def read_class_map(path: Path) -> list[str]:
    """``yamnet_class_map.csv`` (index,mid,display_name) -> nomi nell'ordine delle uscite."""
    names: list[str] = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            names.append(row.get("display_name", "").strip())
    return names


class SoundClassifier:
    """Punteggi dei tipi di suono per una finestra di audio. Un'inferenza alla volta (thread-safe)."""

    def __init__(self, model_path: Path | None, class_map: Path | list[str], session=None):
        names = class_map if isinstance(class_map, list) else read_class_map(Path(class_map))
        self.names = names
        lower = {n.lower(): i for i, n in enumerate(names)}
        # per ogni tipo, gli indici delle sue classi AudioSet (quelle che il modello conosce davvero)
        self.groups: dict[str, list[int]] = {}
        for kind, (_name, _emoji, classes, _thr, _frames) in SOUNDS.items():
            idx = [lower[c.lower()] for c in classes if c.lower() in lower]
            if idx:
                self.groups[kind] = idx
            else:
                log.warning("Tipo di suono %s: nessuna classe trovata nel modello", kind)
        if session is None:
            import onnxruntime as ort  # noqa: PLC0415

            opts = ort.SessionOptions()
            opts.intra_op_num_threads = 1  # leggero: gira accanto all'analisi delle camere
            session = ort.InferenceSession(str(model_path), opts, providers=["CPUExecutionProvider"])
        self.session = session
        self.input_name = session.get_inputs()[0].name
        # l'uscita dei punteggi è quella con 521 colonne (l'ordine delle uscite varia tra le conversioni)
        outs = session.get_outputs()
        self.output_name = next((o.name for o in outs if (o.shape or [None])[-1] == len(names)), outs[0].name)
        self._lock = threading.Lock()
        log.info("Riconoscimento dei suoni pronto (%d tipi, %d classi)", len(self.groups), len(names))

    def scores(self, wave: np.ndarray) -> np.ndarray:
        """Punteggi AudioSet [521] per una finestra (il massimo sui frame, se più d'uno)."""
        x = np.asarray(wave, dtype=np.float32).reshape(-1)
        if len(x) < WINDOW:
            x = np.pad(x, (0, WINDOW - len(x)))
        with self._lock:
            out = self.session.run([self.output_name], {self.input_name: x})[0]
        out = np.asarray(out, dtype=np.float32)
        return out.max(axis=0) if out.ndim == 2 else out.reshape(-1)

    def kinds(self, wave: np.ndarray) -> dict[str, float]:
        """Punteggio di ogni tipo di suono (il massimo delle sue classi)."""
        s = self.scores(wave)
        return {kind: float(s[idx].max()) for kind, idx in self.groups.items()}

    def top(self, wave: np.ndarray, n: int = 3) -> list[tuple[str, float]]:
        """Le classi AudioSet più probabili (diagnosi)."""
        s = self.scores(wave)
        order = np.argsort(s)[::-1][:n]
        return [(self.names[i] if i < len(self.names) else str(i), float(s[i])) for i in order]


def to_mono_16k(samples: np.ndarray, rate: int, channels: int = 1) -> np.ndarray:
    """Audio qualsiasi (interi o float, più canali) -> mono 16 kHz float32 tra -1 e 1."""
    x = np.asarray(samples)
    if x.dtype == np.int16:
        x = x.astype(np.float32) / 32768.0
    elif x.dtype == np.int32:
        x = x.astype(np.float32) / 2147483648.0
    elif x.dtype == np.uint8:
        x = (x.astype(np.float32) - 128.0) / 128.0
    else:
        x = x.astype(np.float32)
    if channels > 1:
        x = x[: len(x) // channels * channels].reshape(-1, channels).mean(axis=1)
    if rate != RATE and rate % RATE == 0:  # 32/48 kHz: media a gruppi (filtro e ricampionamento insieme)
        k = rate // RATE
        return x[: len(x) // k * k].reshape(-1, k).mean(axis=1).astype(np.float32)
    if rate != RATE and len(x) > 1:
        n = int(len(x) * RATE / rate)
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)
    return x
