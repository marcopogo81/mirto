# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Eventi di rilevamento e archivio locale (SQLite + foto JPEG)."""

from __future__ import annotations

import logging
import shutil
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from .i18n import _

log = logging.getLogger(__name__)


@dataclass
class Event:
    camera_id: str
    camera_name: str
    score: float
    image: np.ndarray                       # frame annotato
    ts: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    boxes: list = field(default_factory=list)
    action_level: bool = False              # soglia "azioni" superata
    image_path: str | None = None
    telegram_link: str | None = None
    false_alarm: bool = False
    is_test: bool = False                   # avviso di prova (menu Prove)
    label: str = "person"                   # categoria COCO rilevata (person, car, dog…) o "sound:<tipo>"
    species: str = ""                       # uccelli: gruppo e specie per gli avvisi ("Corvidi (Carrion Crow)")
    species_group: str = ""                 # uccelli: gruppo ("corvid") -> obiettivo "bird:corvid"
    detail: str = ""                        # testo in più per gli avvisi (es. tipo di suono, microfono)
    _jpeg: bytes | None = field(default=None, repr=False)

    @property
    def when(self) -> datetime:
        return datetime.fromtimestamp(self.ts)

    def when_text(self) -> str:
        return self.when.strftime("%d/%m/%Y %H:%M:%S")

    def title(self) -> str:
        if self.is_test:
            return _('Mirto: avviso di PROVA – camera {camera_name} – {strftime}', camera_name=self.camera_name, strftime=self.when.strftime('%d/%m/%Y %H:%M'))
        return _("Mirto: {what} – camera {camera} – {when}", what=self.short_text().lower(), camera=self.camera_name,
                 when=self.when.strftime('%d/%m/%Y %H:%M'))

    def targets(self) -> set[str]:
        """Chiavi che le azioni possono avere come obiettivo: "person", "dog", "bird:corvid", "sound:glass"…"""
        out = {self.label}
        if self.species_group:
            out.add("bird:" + self.species_group)
        return out

    def headline(self) -> str:
        """"🚨 Persona rilevata", "🐾 Rilevato: Cane"…"""
        from .detection.labels import headline  # noqa: PLC0415

        text = headline(self.label)
        return f"{text} · {self.species}" if self.species else text

    def short_text(self) -> str:
        from .detection.labels import short  # noqa: PLC0415

        text = short(self.label)
        return f"{text} · {self.species}" if self.species else text

    def freeze(self) -> None:
        """Tiene solo il JPEG compresso (~0,5 MB) e libera l'immagine piena (per le code di ritentativo)."""
        if self._jpeg is None:
            self._jpeg = self.jpeg()
        self.image = None

    def jpeg(self, max_side: int = 1920, max_bytes: int = 500_000) -> bytes:
        """JPEG ridimensionato e compresso per l'invio (sotto ``max_bytes`` se possibile)."""
        if self._jpeg is not None:
            return self._jpeg
        img = self.image
        h, w = img.shape[:2]
        scale = min(1.0, max_side / max(h, w))
        if scale < 1.0:
            img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        data = b""
        for quality in (88, 80, 70, 60, 50, 40):
            ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
            data = buf.tobytes() if ok else b""
            if len(data) <= max_bytes:
                break
        # Lo stesso avviso va su Telegram, per email e nella cartella degli eventi: senza questa riga
        # la foto veniva ricompressa per ognuno, fino a sei tentativi di qualità per volta (Q-06).
        self._jpeg = data
        return data

    @property
    def clip_path(self) -> Path | None:
        """Clip video dell'evento (stesso nome della foto, .mp4), se è stata registrata."""
        if not self.image_path:
            return None
        p = Path(self.image_path).with_suffix(".mp4")
        return p if p.exists() else None

    def filename(self) -> str:
        safe = "".join(ch if ch.isalnum() else "_" for ch in self.camera_name)[:40]
        return f"Mirto_{safe}_{self.when.strftime('%Y%m%d-%H%M%S')}.jpg"


class EventStore:
    """Salva eventi e foto in locale e cancella quelli più vecchi di ``retention_days``."""

    def __init__(self, root: Path, retention_days: int = 30):
        self.root = Path(root)
        self.images = self.root / "events"
        self.images.mkdir(parents=True, exist_ok=True)
        self.retention_days = retention_days
        self._lock = threading.Lock()
        self.db = sqlite3.connect(str(self.root / "events.sqlite"), check_same_thread=False)
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY, ts REAL, camera_id TEXT, camera_name TEXT,
                score REAL, action_level INTEGER, image_path TEXT,
                telegram_link TEXT, false_alarm INTEGER DEFAULT 0)"""
        )
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(events)")}
        if "label" not in cols:  # archivi creati prima delle categorie: tutto era "persona"
            self.db.execute("ALTER TABLE events ADD COLUMN label TEXT DEFAULT 'person'")
        if "detail" not in cols:  # specie degli uccelli, tipo di suono
            self.db.execute("ALTER TABLE events ADD COLUMN detail TEXT DEFAULT ''")
        self.db.commit()

    def save(self, ev: Event) -> Event:
        day = self.images / ev.when.strftime("%Y-%m-%d")
        day.mkdir(parents=True, exist_ok=True)
        path = day / f"{ev.when.strftime('%H%M%S')}_{ev.id}.jpg"
        cv2.imwrite(str(path), ev.image, [cv2.IMWRITE_JPEG_QUALITY, 90])
        ev.image_path = str(path)
        with self._lock:
            self.db.execute(
                "INSERT OR REPLACE INTO events (id, ts, camera_id, camera_name, score, action_level, image_path, "
                "telegram_link, false_alarm, label, detail) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (ev.id, ev.ts, ev.camera_id, ev.camera_name, ev.score, int(ev.action_level),
                 ev.image_path, ev.telegram_link, int(ev.false_alarm), ev.label, ev.species or ev.detail),
            )
            self.db.commit()
        return ev

    def update(self, event_id: str, **fields) -> None:
        allowed = {"telegram_link", "false_alarm", "action_level"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return
        cols = ", ".join(f"{k}=?" for k in sets)
        with self._lock:
            self.db.execute(f"UPDATE events SET {cols} WHERE id=?", (*sets.values(), event_id))  # noqa: S608
            self.db.commit()

    def recent(self, limit: int = 50) -> list[dict]:
        with self._lock:
            cur = self.db.execute(
                "SELECT id, ts, camera_id, camera_name, score, action_level, image_path, telegram_link, false_alarm, "
                "label, detail "
                "FROM events ORDER BY ts DESC LIMIT ?", (limit,)
            )
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def ultimo_per_camera(self) -> dict[str, float]:
        """``{id della camera: quando è stato l'ultimo evento}``, in una sola interrogazione.

        Serve alla vista di flotta per dire da quanto un nodo non vede niente — che è il guasto che
        non fa rumore: la camera ha smesso di guardare, il nodo resta in linea e verde, e non succede
        nulla. Una domanda sola e non una per camera: con otto nodi sarebbero otto letture a ogni
        aggiornamento della schermata, ogni due secondi.
        """
        with self._lock:
            cur = self.db.execute("SELECT camera_id, MAX(ts) FROM events GROUP BY camera_id")
            return {str(cam): float(quando or 0) for cam, quando in cur.fetchall() if cam}

    def between(self, t0: float, t1: float, camera_id: str | None = None) -> list[dict]:
        """Eventi tra ``t0`` e ``t1`` (di una camera, se indicata), dal più vecchio (per la vista Rivedi)."""
        sql = ("SELECT id, ts, camera_id, camera_name, score, image_path, label, detail, false_alarm "
               "FROM events WHERE ts >= ? AND ts < ?")
        args: list = [t0, t1]
        if camera_id:
            sql += " AND camera_id = ?"
            args.append(camera_id)
        with self._lock:
            cur = self.db.execute(sql + " ORDER BY ts", args)
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def get(self, event_id: str) -> dict | None:
        with self._lock:
            cur = self.db.execute("SELECT * FROM events WHERE id=?", (event_id,))
            row = cur.fetchone()
            return dict(zip([c[0] for c in cur.description], row)) if row else None

    def delete(self, event_ids: list[str]) -> int:
        """Elimina gli eventi indicati e le loro foto. Restituisce quanti ne ha eliminati."""
        if not event_ids:
            return 0
        marks = ",".join("?" * len(event_ids))
        with self._lock:
            rows = self.db.execute(f"SELECT id, image_path FROM events WHERE id IN ({marks})",  # noqa: S608
                                   list(event_ids)).fetchall()
            self.db.execute(f"DELETE FROM events WHERE id IN ({marks})", list(event_ids))  # noqa: S608
            self.db.commit()
        for _id, path in rows:
            if path:
                for f in (Path(path), Path(path).with_suffix(".mp4")):  # foto e clip dell'evento
                    try:
                        f.unlink(missing_ok=True)
                    except OSError as exc:  # es. Windows: video aperto in un lettore
                        log.warning("Impossibile cancellare %s: %s", f, exc)
        return len(rows)

    def delete_all(self) -> int:
        with self._lock:
            ids = [r[0] for r in self.db.execute("SELECT id FROM events").fetchall()]
        return self.delete(ids)

    def purge(self, now: float | None = None) -> int:
        """Elimina eventi e foto oltre il periodo di conservazione. Restituisce quanti."""
        cutoff = (now or time.time()) - self.retention_days * 86400
        with self._lock:
            rows = self.db.execute("SELECT id, image_path FROM events WHERE ts < ?", (cutoff,)).fetchall()
            self.db.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
            self.db.commit()
        for _id, path in rows:
            if path:
                for f in (Path(path), Path(path).with_suffix(".mp4")):  # foto e clip dell'evento
                    try:
                        f.unlink(missing_ok=True)
                    except OSError as exc:  # es. Windows: video aperto in un lettore
                        log.warning("Impossibile cancellare %s: %s", f, exc)
        # cartelle giornaliere rimaste vuote
        for day in self.images.iterdir():
            if day.is_dir() and not any(day.iterdir()):
                shutil.rmtree(day, ignore_errors=True)
        if rows:
            log.info("Archivio: eliminati %d eventi più vecchi di %d giorni", len(rows), self.retention_days)
        return len(rows)

    def close(self) -> None:
        """Chiude la connessione SQLite. Si può chiamare più volte."""
        with self._lock:
            try:
                self.db.close()
            except sqlite3.Error as exc:  # già chiusa, o chiusa male: non deve impedire l'uscita
                log.debug("Archivio già chiuso: %s", exc)
