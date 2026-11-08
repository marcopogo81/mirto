# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Specie degli uccelli: secondo passaggio dopo YOLOX, solo sui riquadri "bird".

Modello: classificatore MobileNet V2 degli uccelli di iNaturalist (Google AIY "birds_V1",
965 specie, licenza Apache 2.0), nella versione TFLite pubblicata da Google Coral.
Gira con il primo motore disponibile: ``ai_edge_litert``, ``tflite_runtime`` oppure
il modulo DNN di OpenCV (già tra le dipendenze di Mirto).

Il modello conosce soprattutto specie nordamericane: per questo le specie sono
raggruppate per **genere** in gruppi utili ai dissuasori (piccioni, corvidi,
storni…). Una cornacchia grigia scambiata per una cornacchia americana resta
comunque un "corvide", ed è quello che serve per scegliere il suono giusto.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from ..i18n import N_, _

log = logging.getLogger(__name__)

# Gruppi di uccelli: chiave -> (nome, generi). Le chiavi finiscono in configurazione ("bird:corvid").
GROUPS: dict[str, tuple[str, list[str]]] = {
    "pigeon": (N_("Piccioni e tortore"), ["Columba", "Streptopelia", "Patagioenas", "Zenaida"]),
    "corvid": (N_("Corvi, cornacchie, gazze"), ["Corvus", "Pica", "Garrulus", "Coloeus", "Cyanocitta", "Aphelocoma"]),
    "starling": (N_("Storni"), ["Sturnus"]),
    "sparrow": (N_("Passeri"), ["Passer"]),
    "gull": (N_("Gabbiani"), ["Larus", "Chroicocephalus", "Leucophaeus", "Ichthyaetus", "Rissa"]),
    "heron": (N_("Aironi e garzette"), ["Ardea", "Egretta", "Nycticorax", "Butorides", "Bubulcus"]),
    "cormorant": (N_("Cormorani"), ["Phalacrocorax", "Nannopterum", "Microcarbo"]),
    "parakeet": (N_("Parrocchetti"), ["Psittacula", "Myiopsitta"]),
    "blackbird": (N_("Merli e tordi"), ["Turdus"]),
    "raptor": (N_("Rapaci"), ["Accipiter", "Buteo", "Falco", "Aquila", "Milvus", "Circus", "Haliaeetus"]),
}
GROUP_PREFIX = "bird:"


def group_key(gid: str) -> str:
    return GROUP_PREFIX + gid


def group_name(gid: str) -> str:
    return _(GROUPS[gid][0]) if gid in GROUPS else gid


@dataclass
class SpeciesResult:
    scientific: str                  # "Corvus corone"
    common: str                      # "Carrion Crow"
    score: float                     # probabilità della specie
    group: str = ""                  # "corvid" (vuoto se la specie non è in nessun gruppo)
    group_score: float = 0.0         # somma delle probabilità delle specie dello stesso gruppo
    top: list = field(default_factory=list)  # [(etichetta, punteggio)] le prime 3, per la diagnosi

    @property
    def display(self) -> str:
        """Testo per gli avvisi: il gruppo (affidabile) e, tra parentesi, la specie più probabile."""
        if self.group:
            return f"{group_name(self.group)} ({self.common or self.scientific})"
        return self.common or self.scientific


def parse_labels(text: str) -> list[tuple[str, str]]:
    """Righe "123  Corvus corone (Carrion Crow)" -> [(scientifico, comune)] nell'ordine del modello."""
    out: list[tuple[str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(\d+)\s+(.*)$", line)
        name = m.group(2) if m else line
        sm = re.match(r"^(.*?)\s*\((.*)\)\s*$", name)
        sci, common = (sm.group(1), sm.group(2)) if sm else (name, "")
        if m:
            idx = int(m.group(1))
            while len(out) < idx:
                out.append(("", ""))
        out.append((sci.strip(), common.strip()))
    return out


def genus_group(scientific: str) -> str:
    genus = scientific.split(" ")[0] if scientific else ""
    for gid, (_name, genera) in GROUPS.items():
        if genus in genera:
            return gid
    return ""


class _Backend:
    """Esecuzione del modello TFLite con il primo motore disponibile."""

    def __init__(self, model_path: Path):
        self.kind = ""
        self.size = (224, 224)
        errors = []
        for loader in (self._litert, self._tflite_runtime, self._opencv):
            try:
                loader(model_path)
                return
            except Exception as exc:  # noqa: BLE001  (si prova il motore successivo)
                errors.append(f"{loader.__name__.strip('_')}: {exc}")
        raise RuntimeError(_("Nessun motore per il modello delle specie: installa ai-edge-litert (pip install ai-edge-litert). Dettagli: {errors}",
                             errors="; ".join(errors)))

    def _setup_interpreter(self, interp) -> None:
        interp.allocate_tensors()
        self.interp = interp
        self.inp = interp.get_input_details()[0]
        self.out = interp.get_output_details()[0]
        self.size = (int(self.inp["shape"][1]), int(self.inp["shape"][2]))

    def _litert(self, path: Path) -> None:
        from ai_edge_litert.interpreter import Interpreter  # noqa: PLC0415

        self._setup_interpreter(Interpreter(model_path=str(path), num_threads=2))
        self.kind = "litert"

    def _tflite_runtime(self, path: Path) -> None:
        from tflite_runtime.interpreter import Interpreter  # noqa: PLC0415

        self._setup_interpreter(Interpreter(model_path=str(path), num_threads=2))
        self.kind = "tflite_runtime"

    def _opencv(self, path: Path) -> None:
        self.net = cv2.dnn.readNetFromTFLite(str(path))
        self.kind = "opencv"

    def run(self, rgb: np.ndarray) -> np.ndarray:
        """Immagine RGB già ridimensionata -> probabilità (somma 1)."""
        if self.kind == "opencv":
            blob = rgb.astype(np.float32)[None]  # NHWC 0..255: OpenCV gestisce la quantizzazione del modello
            self.net.setInput(blob)
            raw = np.asarray(self.net.forward(), dtype=np.float32).reshape(-1)
        else:
            x = rgb[None]
            if self.inp["dtype"] == np.uint8:
                x = x.astype(np.uint8)
            else:
                x = (x.astype(np.float32) - 127.5) / 127.5
            self.interp.set_tensor(self.inp["index"], x)
            self.interp.invoke()
            raw = self.interp.get_tensor(self.out["index"]).reshape(-1).astype(np.float32)
            scale, zero = self.out.get("quantization", (0.0, 0))
            if self.out["dtype"] == np.uint8 and scale:
                raw = (raw - zero) * scale
        return _to_probs(raw)


def _to_probs(raw: np.ndarray) -> np.ndarray:
    raw = np.asarray(raw, dtype=np.float32)
    if raw.min() >= 0 and 0.5 <= raw.sum() <= 1.5:  # già probabilità
        return raw / raw.sum()
    if raw.min() >= 0 and raw.max() > 1.5:  # punteggi quantizzati 0..255 non normalizzati
        return raw / max(raw.sum(), 1e-6)
    e = np.exp(raw - raw.max())  # logit
    return e / e.sum()


class SpeciesClassifier:
    """Classifica il ritaglio di un uccello. Thread-safe (un'inferenza alla volta)."""

    def __init__(self, model_path: Path, labels_path: Path, backend=None):
        self.labels = parse_labels(Path(labels_path).read_text(encoding="utf-8"))
        self.label_groups = [genus_group(sci) for sci, _common in self.labels]
        self.backend = backend or _Backend(Path(model_path))
        self._lock = threading.Lock()
        log.info("Classificatore delle specie pronto (%s, %d specie)", self.backend.kind or "?", len(self.labels))

    @staticmethod
    def crop(frame: np.ndarray, box: tuple[int, int, int, int], margin: float = 0.15) -> np.ndarray:
        """Ritaglio quadrato attorno al riquadro, con un po' di margine (come in addestramento)."""
        x1, y1, x2, y2 = box
        h, w = frame.shape[:2]
        side = max(x2 - x1, y2 - y1) * (1 + 2 * margin)
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        a, b = int(max(0, cx - side / 2)), int(max(0, cy - side / 2))
        c, d = int(min(w, cx + side / 2)), int(min(h, cy + side / 2))
        return frame[b:d, a:c] if d > b and c > a else frame

    def classify(self, frame_bgr: np.ndarray, box: tuple[int, int, int, int] | None = None) -> SpeciesResult | None:
        img = self.crop(frame_bgr, box) if box else frame_bgr
        if img.size == 0:
            return None
        rgb = cv2.cvtColor(cv2.resize(img, self.backend.size[::-1], interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)
        with self._lock:
            probs = self.backend.run(rgb)
        n = min(len(probs), len(self.labels))
        if n == 0:
            return None
        probs = probs[:n]
        order = np.argsort(probs)[::-1]
        best = int(order[0])
        sci, common = self.labels[best]
        if not sci or sci.lower() == "background":
            best = next((int(i) for i in order if self.labels[int(i)][0] and self.labels[int(i)][0].lower() != "background"), best)
            sci, common = self.labels[best]
        # gruppo = quello con la probabilità complessiva più alta (più robusto della singola specie)
        sums: dict[str, float] = {}
        for i in range(n):
            g = self.label_groups[i]
            if g:
                sums[g] = sums.get(g, 0.0) + float(probs[i])
        gid = max(sums, key=sums.get) if sums else ""
        gscore = sums.get(gid, 0.0)
        top = [(f"{self.labels[int(i)][0]} ({self.labels[int(i)][1]})", float(probs[int(i)])) for i in order[:3]]
        return SpeciesResult(sci, common, float(probs[best]), gid, gscore, top)
