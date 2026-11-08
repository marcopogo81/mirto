# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Rilevamento del movimento con sottrazione dello sfondo (MOG2), su immagine ridotta."""

from __future__ import annotations

import cv2
import numpy as np


class MotionDetector:
    """Segnala quando una parte sufficiente dell'immagine cambia.

    ``ignore_zones``: rettangoli in coordinate normalizzate ``[x, y, w, h]`` (0–1)
    da non considerare (es. una tenda, un albero, una TV).
    ``sensitivity``: 0–1, più alta = rileva cambiamenti più piccoli.
    """

    WIDTH = 320
    WARMUP_FRAMES = 15

    def __init__(self, min_area: float = 0.01, sensitivity: float = 0.5, ignore_zones=None):
        self.min_area = float(min_area)
        var_threshold = 16 + (1.0 - float(sensitivity)) * 48  # 16 (sensibile) … 64 (poco sensibile)
        self.bg = cv2.createBackgroundSubtractorMOG2(history=300, varThreshold=var_threshold, detectShadows=True)
        self.ignore_zones = ignore_zones or []
        self._mask: np.ndarray | None = None
        self._frames = 0
        self.last_ratio = 0.0
        self._kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))

    def _prepare(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        small = cv2.resize(frame, (self.WIDTH, max(1, int(h * self.WIDTH / w))), interpolation=cv2.INTER_AREA)
        small = cv2.GaussianBlur(small, (5, 5), 0)
        if self._mask is None or self._mask.shape != small.shape[:2]:
            mask = np.full(small.shape[:2], 255, np.uint8)
            sh, sw = mask.shape
            for x, y, zw, zh in self.ignore_zones:
                mask[int(y * sh): int((y + zh) * sh), int(x * sw): int((x + zw) * sw)] = 0
            self._mask = mask
        return small

    def update(self, frame: np.ndarray) -> bool:
        small = self._prepare(frame)
        fg = self.bg.apply(small)
        self._frames += 1
        if self._frames <= self.WARMUP_FRAMES:  # lo sfondo si sta ancora formando
            self.last_ratio = 0.0
            return False
        fg = np.where(fg == 255, 255, 0).astype(np.uint8)  # 127 = ombre: ignorate
        fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, self._kernel)
        fg = cv2.bitwise_and(fg, self._mask)
        valid = max(1, int(np.count_nonzero(self._mask)))
        self.last_ratio = float(np.count_nonzero(fg)) / valid
        return self.last_ratio >= self.min_area


def brightness(frame: np.ndarray) -> float:
    """Luminosità media 0–255 (per avvisare quando l'immagine è troppo scura)."""
    small = cv2.resize(frame, (64, 36), interpolation=cv2.INTER_AREA)
    return float(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).mean())
