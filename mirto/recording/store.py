# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Spezzoni registrati su disco.

Struttura: ``<cartella>/<camera>/AAAAMMGG-hhmmss.mp4``, un file per spezzone (di serie 60 s). L'ora nel nome è
l'inizio dello spezzone in **UTC** (niente doppioni al cambio dell'ora legale). La fine di uno spezzone è l'inizio
del successivo, oppure l'ora dell'ultima scrittura. Ogni cartella ha un ``camera.json`` con nome e ID della camera,
così i file restano comprensibili anche senza Mirto.

Non serve un database: l'elenco si ricava dai nomi, quindi non può mai andare fuori sincrono con i file.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

NAME_RE = re.compile(r"^(\d{8})-(\d{6})\.mp4$")
STRFTIME_PATTERN = "%Y%m%d-%H%M%S.mp4"
MAX_SEGMENT_SECONDS = 15 * 60  # oltre, uno spezzone "aperto" è sicuramente stato interrotto


def dimensione_vera(percorso: Path) -> int:
    """Quanto è grande davvero il file, anche mentre ffmpeg gli sta scrivendo dentro.

    Su Windows i dati della voce di cartella — quelli che tornano da ``os.scandir`` e da ``os.stat`` —
    **non si aggiornano** finché qualcuno tiene il file aperto: uno spezzone che cresce sembra fermo.
    Il registratore capisce che il flusso è vivo proprio guardando se l'ultimo file cresce, quindi su
    Windows dichiarava «nessun dato dalla camera da 30 s», ammazzava ffmpeg e ripartiva — all'infinito,
    mentre gli spezzoni finivano sul disco regolarmente (trovato su un PC Windows 11 vero, W-01, 19/10:
    nel registro «cancellati 2 spezzoni» e «nessun dato» nello stesso minuto).

    Aprire il file e andare in fondo chiede la dimensione **al file**, non alla cartella, e quel numero
    è giusto su tutti i sistemi. Se il file non si può aprire si ripiega sulla voce di cartella, che su
    Unix è comunque esatta.
    """
    try:
        with open(percorso, "rb") as f:
            return f.seek(0, os.SEEK_END)
    except OSError:
        try:
            return percorso.stat().st_size
        except OSError:
            return 0


@dataclass
class Segment:
    path: Path
    start: float        # epoch, UTC
    end: float
    size: int

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def parse_start(name: str) -> float | None:
    m = NAME_RE.match(name)
    if not m:
        return None
    try:
        return float(calendar.timegm(time.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")))
    except ValueError:
        return None


def safe_dir_name(cam_id: str) -> str:
    """Nome di cartella leggibile e sicuro per un ID di camera qualunque (unico grazie all'impronta)."""
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", cam_id).strip("_")[:40] or "camera"
    return f"{base}-{hashlib.sha1(cam_id.encode()).hexdigest()[:6]}"


class RecordingStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def cam_dir(self, cam_id: str, name: str | None = None) -> Path:
        d = self.root / safe_dir_name(cam_id)
        if name is not None:
            d.mkdir(parents=True, exist_ok=True)
            meta = d / "camera.json"
            data = {"id": cam_id, "name": name}
            try:
                if not meta.exists() or json.loads(meta.read_text(encoding="utf-8")) != data:
                    meta.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            except (OSError, ValueError):
                pass
        return d

    def cameras(self) -> dict[str, Path]:
        """ID camera -> cartella, per tutte le cartelle presenti (anche di camere non più collegate)."""
        out = {}
        if not self.root.is_dir():
            return out
        for d in self.root.iterdir():
            meta = d / "camera.json"
            if d.is_dir() and meta.exists():
                try:
                    out[json.loads(meta.read_text(encoding="utf-8"))["id"]] = d
                except (OSError, ValueError, KeyError):
                    continue
        return out

    def camera_name(self, cam_id: str) -> str:
        try:
            return json.loads((self.cam_dir(cam_id) / "camera.json").read_text(encoding="utf-8")).get("name", cam_id)
        except (OSError, ValueError):
            return cam_id

    @staticmethod
    def segments_in(folder: Path, open_path: Path | None = None, now: float | None = None,
                    t0: float | None = None, t1: float | None = None) -> list[Segment]:
        """Spezzoni di una cartella, in ordine. ``open_path`` è quello che ffmpeg sta ancora scrivendo.
        Con ``t0``/``t1`` si leggono solo i file che possono cadere in quell'intervallo (scelti dal nome)."""
        items = []
        try:
            entries = list(os.scandir(folder))
        except OSError:
            return []
        for e in entries:
            start = parse_start(e.name)
            if start is None:
                continue
            if (t1 is not None and start > t1) or (t0 is not None and start < t0 - MAX_SEGMENT_SECONDS):
                continue
            try:
                st = e.stat()
            except OSError:
                continue
            items.append((start, Path(e.path), st.st_size, st.st_mtime))
        items.sort()
        now = now or time.time()
        out = []
        for i, (start, path, size, mtime) in enumerate(items):
            if open_path is not None and path == open_path:
                end = now
            elif i + 1 < len(items) and items[i + 1][0] - start <= MAX_SEGMENT_SECONDS:
                end = min(items[i + 1][0], max(mtime, start))
            else:
                end = max(mtime, start)
            out.append(Segment(path, start, end, size))
        return out

    # --- processi ffmpeg (per chiudere quelli rimasti dopo un arresto improvviso di Mirto) ---------------
    @property
    def pid_dir(self) -> Path:
        return self.root / ".pids"

    def remember_pid(self, pid: int) -> None:
        try:
            self.pid_dir.mkdir(parents=True, exist_ok=True)
            (self.pid_dir / str(pid)).touch()
        except OSError:
            pass

    def forget_pid(self, pid: int) -> None:
        try:
            (self.pid_dir / str(pid)).unlink(missing_ok=True)
        except OSError:
            pass

    def stale_pids(self) -> list[int]:
        try:
            return [int(p.name) for p in self.pid_dir.iterdir() if p.name.isdigit()]
        except OSError:
            return []

    @staticmethod
    def newest(folder: Path) -> tuple[Path | None, int]:
        """Lo spezzone più recente e la sua dimensione, senza leggere le informazioni di tutti i file."""
        best = None
        try:
            with os.scandir(folder) as it:
                for e in it:
                    if NAME_RE.match(e.name) and (best is None or e.name > best.name):
                        best = e
        except OSError:
            return None, 0
        if best is None:
            return None, 0
        percorso = Path(best.path)
        return percorso, dimensione_vera(percorso)

    def segments(self, cam_id: str, open_path: Path | None = None, now: float | None = None) -> list[Segment]:
        return self.segments_in(self.cam_dir(cam_id), open_path, now)

    @staticmethod
    def between(segments: list[Segment], t0: float, t1: float) -> list[Segment]:
        return [s for s in segments if s.start <= t1 and s.end >= t0]

    def usage(self) -> dict:
        """Byte usati dalle registrazioni, spazio libero sul disco, inizio della registrazione più vecchia."""
        used, oldest = 0, None
        for d in self.cameras().values():
            folders = [d] + [x for x in d.iterdir() if x.is_dir()]  # compreso il buffer della modalità "eventi"
            for folder in folders:
                for s in self.segments_in(folder):
                    used += s.size
                    oldest = s.start if oldest is None else min(oldest, s.start)
        free = total = 0
        try:
            du = shutil.disk_usage(self.root if self.root.exists() else self.root.parent)
            free, total = du.free, du.total
        except OSError:
            pass
        return {"used": used, "free": free, "total": total, "oldest": oldest}

    @staticmethod
    def delete(seg: Segment) -> bool:
        try:
            seg.path.unlink()
            return True
        except FileNotFoundError:
            return True
        except OSError as exc:
            log.warning("Impossibile cancellare %s: %s", seg.path, exc)
            return False
