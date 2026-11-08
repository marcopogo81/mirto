# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Riconoscimento con YOLOX in formato ONNX (ONNX Runtime): persone e, a scelta, le altre categorie COCO.

Usa l'accelerazione hardware se disponibile: CoreML su macOS, DirectML su Windows.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass

import cv2
import numpy as np

log = logging.getLogger(__name__)

from .labels import KEYS, display_name

PERSON_CLASS = 0  # COCO: 0 = persona


@dataclass
class Detection:
    box: tuple[int, int, int, int]  # x1, y1, x2, y2 in pixel dell'immagine originale
    score: float
    cls: int = PERSON_CLASS          # indice della categoria COCO

    @property
    def label(self) -> str:
        return KEYS[self.cls] if 0 <= self.cls < len(KEYS) else str(self.cls)


def _inferenze_in_parallelo() -> int:
    """Quante inferenze possono girare insieme.

    Prima ce n'era **una sola**, con un `Lock` su tutto il rilevatore: con quattro camere, la quarta
    aspettava le altre tre e la promessa «due fotogrammi su cinque» diventava, per lei, qualche secondo
    (Q-03 del piano). Il `Lock` non serviva alla correttezza — `InferenceSession.run` si può chiamare da
    più thread — serviva a non prendersi tutta la CPU; per quello bastano due cose insieme:
    `intra_op_num_threads = 2` dentro ogni inferenza e un tetto al numero di inferenze contemporanee.

    Il tetto è metà dei core, mai meno di **due**: con una macchina piccola due camere non devono
    comunque mettersi in fila una dietro l'altra, e a quel punto è il sistema operativo a decidere come
    alternarle — che è il suo mestiere.
    """
    return max(2, (os.cpu_count() or 4) // 2)


INFERENZE_IN_PARALLELO = _inferenze_in_parallelo()


def _providers() -> list[str]:
    import onnxruntime as ort  # noqa: PLC0415

    available = ort.get_available_providers()
    preferred = ["CoreMLExecutionProvider", "DmlExecutionProvider", "CPUExecutionProvider"]
    return [p for p in preferred if p in available] or available


class PersonDetector:
    def __init__(self, model_path: str, input_size: int, providers: list[str] | None = None):
        import onnxruntime as ort  # noqa: PLC0415

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2  # lascia CPU libera per il resto del sistema
        try:
            self.session = ort.InferenceSession(str(model_path), opts, providers=providers or _providers())
        except Exception as exc:  # es. CoreML non compatibile: ripiego su CPU
            log.warning("Accelerazione non disponibile (%s): uso la CPU", exc)
            self.session = ort.InferenceSession(str(model_path), opts, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        self.size = (input_size, input_size)
        self._grid, self._strides = self._make_grid()
        # tetto alle inferenze contemporanee invece di una alla volta: vedi _inferenze_in_parallelo()
        self._posti = threading.BoundedSemaphore(INFERENZE_IN_PARALLELO)
        log.info("Rilevatore persone pronto (%s, fino a %d inferenze insieme)",
                 ", ".join(self.session.get_providers()), INFERENZE_IN_PARALLELO)

    def _make_grid(self):
        grids, strides = [], []
        for s in (8, 16, 32):
            hs, ws = self.size[0] // s, self.size[1] // s
            xv, yv = np.meshgrid(np.arange(ws), np.arange(hs))
            g = np.stack((xv, yv), 2).reshape(-1, 2)
            grids.append(g)
            strides.append(np.full((g.shape[0], 1), s))
        return np.concatenate(grids).astype(np.float32), np.concatenate(strides).astype(np.float32)

    def _preprocess(self, img: np.ndarray) -> tuple[np.ndarray, float]:
        h, w = self.size
        padded = np.full((h, w, 3), 114, np.uint8)
        r = min(h / img.shape[0], w / img.shape[1])
        resized = cv2.resize(img, (int(img.shape[1] * r), int(img.shape[0] * r)), interpolation=cv2.INTER_LINEAR)
        padded[: resized.shape[0], : resized.shape[1]] = resized
        return np.ascontiguousarray(padded.transpose(2, 0, 1), dtype=np.float32)[None], r

    def detect(self, frame: np.ndarray, threshold: float = 0.3, nms: float = 0.45,
               classes: list[int] | None = None) -> list[Detection]:
        """Rilevamenti delle categorie ``classes`` (indici COCO; predefinito: solo persone)."""
        classes = list(classes) if classes else [PERSON_CLASS]
        x, ratio = self._preprocess(frame)
        with self._posti:
            out = self.session.run(None, {self.input_name: x})[0][0]
        cls_scores = out[:, 5:][:, classes]                 # solo le categorie sorvegliate
        best = cls_scores.argmax(axis=1)
        scores = out[:, 4] * cls_scores[np.arange(len(best)), best]
        keep = scores >= threshold
        if not keep.any():
            return []
        xy = (out[keep, :2] + self._grid[keep]) * self._strides[keep]
        wh = np.exp(out[keep, 2:4]) * self._strides[keep]
        scores = scores[keep]
        cls_ids = np.asarray(classes)[best[keep]]
        boxes = np.concatenate([xy - wh / 2, wh], axis=1) / ratio  # x, y, w, h
        # NMS separata per categoria: spostando i riquadri, categorie diverse non si sovrappongono
        shifted = boxes.copy()
        shifted[:, :2] += cls_ids[:, None] * 10000.0
        idx = cv2.dnn.NMSBoxes(shifted.tolist(), scores.tolist(), threshold, nms)
        dets = []
        fh, fw = frame.shape[:2]
        for i in np.array(idx).flatten():
            bx, by, bw, bh = boxes[i]
            x1, y1 = max(0, int(bx)), max(0, int(by))
            x2, y2 = min(fw - 1, int(bx + bw)), min(fh - 1, int(by + bh))
            dets.append(Detection((x1, y1, x2, y2), float(scores[i]), int(cls_ids[i])))
        dets.sort(key=lambda d: d.score, reverse=True)
        return dets


def annotate(frame: np.ndarray, dets: list[Detection], caption: str = "") -> np.ndarray:
    """Disegna i riquadri delle persone e una didascalia (camera, ora)."""
    img = frame.copy()
    for d in dets:
        x1, y1, x2, y2 = d.box
        color = (0, 0, 255) if d.cls == PERSON_CLASS else (240, 155, 91)  # rosso persone, blu il resto
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
        label = f"{display_name(d.label)} {d.score:.0%}"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
        cv2.rectangle(img, (x1, max(0, y1 - th - 8)), (x1 + tw + 6, y1), color, -1)
        cv2.putText(img, label, (x1 + 3, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    if caption:
        cv2.rectangle(img, (0, img.shape[0] - 28), (img.shape[1], img.shape[0]), (0, 0, 0), -1)
        cv2.putText(img, caption, (8, img.shape[0] - 9), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
    return img
