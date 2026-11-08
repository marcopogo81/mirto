# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Scoperta delle camere locali con ID stabile.

Ogni camera è identificata da un ID che non cambia quando si collegano o
scollegano altri dispositivi:

* macOS   → ``uniqueID`` di AVFoundation (vedi ``discovery_macos``)
* Windows → percorso del dispositivo (*symbolic link*) di Media Foundation
* Linux   → collegamento in ``/dev/v4l/by-id`` (o percorso del bus)

Per aprire la camera serve invece l'indice di OpenCV, che può cambiare: per
questo l'indice viene ricalcolato a ogni scansione e non va mai salvato.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable
from ..i18n import _

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CameraInfo:
    id: str                       # ID stabile
    name: str                     # nome leggibile
    kind: str = "local"           # "local" o "network"
    index: int | None = None      # indice OpenCV (solo locali, può cambiare)
    api: int | None = None        # backend OpenCV (es. cv2.CAP_AVFOUNDATION)
    url: str | None = None        # solo camere di rete
    extra: dict = field(default_factory=dict, compare=False, hash=False)

    @property
    def open_target(self):
        """Argomento da passare a ``cv2.VideoCapture``."""
        return self.url if self.kind == "network" else self.index


_probe_cache: tuple[float, list[CameraInfo]] = (0.0, [])
_probe_lock = threading.Lock()
_probe_thread: threading.Thread | None = None
PROBE_INTERVAL = 60.0  # la ricerca per indice apre le camere: non ripeterla di continuo


def discover_local_cameras(busy=()) -> list[CameraInfo]:
    """Elenca le camere locali del sistema operativo corrente.

    ``busy``: indici già aperti da Mirto, che la ricerca per indice (il ripiego) non deve toccare.
    """
    try:
        if sys.platform == "darwin":
            from .discovery_macos import discover  # noqa: PLC0415
        elif sys.platform.startswith("win"):
            from .discovery_windows import discover  # noqa: PLC0415
        else:
            from .discovery_linux import discover  # noqa: PLC0415
        return discover()
    except Exception as exc:
        return _probe_fallback(set(busy), exc)


def _probe_fallback(busy: set, exc: Exception) -> list[CameraInfo]:
    """Elenco dall'ultima ricerca per indice, rinfrescato in sottofondo.

    La ricerca per indice apre davvero le camere: la prima volta (all'avvio, quando ancora non c'è niente
    di aperto) si fa subito, poi si rinnova in un thread suo, così non ferma mai la finestra.
    """
    global _probe_cache, _probe_thread

    with _probe_lock:
        when, cams = _probe_cache
        if time.monotonic() - when < PROBE_INTERVAL or (_probe_thread is not None and _probe_thread.is_alive()):
            return list(cams)
        log.warning("Scoperta nativa non disponibile (%s): uso la ricerca per indice", exc)
        if not when:  # primo giro: nessuna camera aperta, si può aspettare
            _probe_cache = (time.monotonic(), discover_by_probing(busy=busy))
            return list(_probe_cache[1])

        def refresh() -> None:
            global _probe_cache
            try:
                found = discover_by_probing(busy=busy)
            except Exception:  # noqa: BLE001
                log.exception("Ricerca delle camere per indice fallita")
                return
            _probe_cache = (time.monotonic(), found)

        _probe_thread = threading.Thread(target=refresh, name="camera-probe", daemon=True)
        _probe_thread.start()
        return list(cams)


def discover_by_probing(max_index: int = 5, busy=()) -> list[CameraInfo]:
    """Ripiego: prova gli indici 0..max_index. Gli ID NON sono stabili.

    Gli indici già aperti da Mirto (``busy``) si danno per presenti **senza** aprirli: con Media Foundation
    su Windows la camera è esclusiva, e una seconda apertura la farebbe risultare scollegata — Mirto la
    fermerebbe e riaprirebbe a ogni scansione (sfarfallio e buchi nella registrazione).
    """
    import cv2  # noqa: PLC0415

    busy = set(busy)
    found = []
    for i in range(max_index):
        if i in busy:
            found.append(CameraInfo(id=f"index:{i}", name=_('Camera {i}', i=i), index=i))
            continue
        cap = cv2.VideoCapture(i)
        try:
            if cap.isOpened():
                found.append(CameraInfo(id=f"index:{i}", name=_('Camera {i}', i=i), index=i))
        finally:
            cap.release()
    return found


class DiscoveryWatcher:
    """Confronta periodicamente l'elenco delle camere e segnala aggiunte e rimozioni.

    ``scan()`` può essere chiamato dal thread principale (consigliato su macOS,
    dove AVFoundation aggiorna l'elenco tramite il run loop principale) oppure
    da un thread interno con ``start()``.
    """

    def __init__(
        self,
        on_added: Callable[[CameraInfo], None],
        on_removed: Callable[[CameraInfo], None],
        on_changed: Callable[[CameraInfo], None] | None = None,
        discover: Callable[[], list[CameraInfo]] = discover_local_cameras,
        interval: float = 3.0,
        missing_before_removed: int = 2,
    ):
        self.on_added = on_added
        self.on_removed = on_removed
        self.on_changed = on_changed
        self._discover = discover
        self.interval = interval
        # una sola scansione a vuoto non basta per dire "scollegata": capita che l'elenco del sistema
        # salti un giro, e fermare la camera significa interrompere anche la registrazione
        self.missing_before_removed = max(1, int(missing_before_removed))
        self._missing: dict[str, int] = {}
        self.known: dict[str, CameraInfo] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def scan(self) -> None:
        try:
            current = {c.id: c for c in self._discover()}
        except Exception as exc:
            log.error("Scansione camere fallita: %s", exc)
            return
        for cid, cam in current.items():
            self._missing.pop(cid, None)
            old = self.known.get(cid)
            if old is None:
                log.info("Camera collegata: %s (%s)", cam.name, cid)
                self.on_added(cam)
            elif (old.index, old.api) != (cam.index, cam.api) and self.on_changed:
                log.info("Camera %s: indice cambiato %s → %s", cam.name, old.index, cam.index)
                self.on_changed(cam)
        for cid, cam in self.known.items():
            if cid in current:
                continue
            self._missing[cid] = self._missing.get(cid, 0) + 1
            if self._missing[cid] < self.missing_before_removed:
                log.debug("Camera %s non vista in questa scansione (%d): aspetto la prossima",
                          cam.name, self._missing[cid])
                current[cid] = cam  # per Mirto è ancora collegata
                continue
            self._missing.pop(cid, None)
            log.info("Camera scollegata: %s (%s)", cam.name, cid)
            self.on_removed(cam)
        self.known = current

    def start(self) -> None:
        if self._thread:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="camera-discovery", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.scan()
            self._stop.wait(self.interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
