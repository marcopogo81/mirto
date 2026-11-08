# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Registrazione di tutte le camere: avvio e arresto dei registratori, clip degli eventi, spazio su disco.

Modalità per camera (``recording.cameras[<id>]``, oppure ``recording.mode`` di serie):

* ``off``        — nessuna registrazione (solo la foto dell'evento, come prima);
* ``events``     — si registra di continuo in ``<camera>/buffer`` ma si tengono solo pochi minuti: a ogni
                   evento si ritaglia la clip (``pre_seconds`` prima, ``post_seconds`` dopo) accanto alla foto;
* ``continuous`` — si tiene tutto in ``<camera>/`` per ``retention_days`` giorni; le clip degli eventi si fanno
                   comunque, perché servono all'Archivio e a Telegram.

Lo spazio non finisce mai: oltre il limite (``max_gb``, oppure ``max_disk_fraction`` dello spazio disponibile) o
con meno di ``min_free_gb`` liberi si cancellano gli spezzoni più vecchi. Se non c'è più niente da cancellare la
registrazione si sospende e arriva un avviso.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..i18n import _
from .ffmpeg import FFmpegInfo, detect, popen_flags
from .recorder import STATE_ERROR, STATE_RECORDING, CameraRecorder
from .store import RecordingStore, Segment

log = logging.getLogger(__name__)

MODES = ("off", "events", "continuous")
BUFFER_DIR = "buffer"
MIN_READABLE_BYTES = 2048      # uno spezzone appena aperto non ha ancora frammenti leggibili
CLIP_MARGIN_SECONDS = 4        # dopo la fine della clip si aspetta il fotogramma chiave successivo
CLIP_GIVE_UP_SECONDS = 180     # se dopo 3 minuti non c'è niente da ritagliare, si rinuncia
ALERT_AFTER_SECONDS = 120      # registrazione ferma da 2 minuti: avviso
CLIP_MAX_WAIT_SECONDS = 20     # al massimo tanto dopo la fine della clip per avere il frammento che la copre
KEEP_RECENT_SECONDS = 86400    # con il disco pieno per colpa d'altro non si cancellano mai le ultime 24 ore


@dataclass
class RemoteSegment(Segment):
    """Spezzone registrato sul nodo. ``path`` è dove si trova (o si troverà) la copia scaricata sul Mac."""

    name: str = ""
    node: str = ""


NODE_CACHE_BYTES = 2_000_000_000   # copie locali degli spezzoni dei nodi: al massimo 2 GB…
NODE_CACHE_SECONDS = 2 * 86400     # …e non più vecchie di 2 giorni


@dataclass
class ClipJob:
    event_id: str
    cam_id: str
    t0: float
    t1: float
    out: Path
    due: float
    give_up: float


def ffmpeg_concat_clip(ffmpeg: str, segments: list[Segment], t0: float, t1: float, out: Path,
                       workdir: Path) -> str:
    """Ritaglia [t0, t1] dagli spezzoni (senza ricodifica). Restituisce "" se va bene, altrimenti l'errore."""
    segs = [s for s in segments if s.size >= MIN_READABLE_BYTES]
    if not segs:
        return "nessuno spezzone"
    workdir.mkdir(parents=True, exist_ok=True)
    lst = workdir / f"{out.stem}.txt"
    righe = []
    for i, s in enumerate(segs):
        righe.append(f"file '{str(s.path).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'\n")
        # «duration» dice al concat quanto dura il pezzo invece di farglielo leggere dall'intestazione del
        # file: negli MP4 frammentati che scrive il registratore quella durata può essere 0, e allora il
        # salto a «dieci secondi prima dell'evento» finisce nello spezzone sbagliato. La durata la
        # conosciamo dai nomi dei file, che sono orari: per ogni spezzone è l'inizio del successivo.
        # L'ultimo non si dichiara — nessun inizio dipende da lui, ed è l'unico la cui durata non
        # conosciamo bene, perché può essere ancora in scrittura (Q-05).
        if i + 1 < len(segs) and s.duration > 0:
            righe.append(f"duration {s.duration:.3f}\n")
    lst.write_text("".join(righe), encoding="utf-8")
    offset = max(0.0, t0 - segs[0].start)
    tmp = out.with_suffix(".part.mp4")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{offset:.2f}", "-f", "concat", "-safe", "0",
           "-i", str(lst), "-t", f"{max(1.0, t1 - t0):.2f}", "-map", "0:v:0", "-c", "copy",
           "-movflags", "+faststart", str(tmp)]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=120, **popen_flags())
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)
    finally:
        lst.unlink(missing_ok=True)
    if not tmp.exists() or tmp.stat().st_size < MIN_READABLE_BYTES:
        tmp.unlink(missing_ok=True)
        err = res.stderr.decode("utf-8", "replace").strip().splitlines()
        return (err[-1] if err else "clip vuota")[:200]
    tmp.replace(out)
    return ""


def kill_if_ffmpeg(pid: int) -> bool:
    """Chiude il processo ``pid`` se è (ancora) un ffmpeg. Il controllo del nome evita di colpire un altro
    programma che nel frattempo ha ricevuto lo stesso numero di processo."""
    import os  # noqa: PLC0415
    import signal  # noqa: PLC0415
    import sys  # noqa: PLC0415

    try:
        if sys.platform == "win32":
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"], capture_output=True,
                                 text=True, timeout=10, **popen_flags()).stdout
            if "ffmpeg" not in out.lower():
                return False
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=10, **popen_flags())
            return True
        out = subprocess.run(["ps", "-o", "comm=", "-p", str(pid)], capture_output=True, text=True,
                             timeout=10).stdout
        if "ffmpeg" not in out.lower():
            return False
        os.kill(pid, signal.SIGTERM)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


class RecordingManager:
    def __init__(self, config, cameras, data_root: Path, notify: Callable[[str], None] | None = None,
                 clock=time.time, ffmpeg_detect=detect):
        self.config = config
        self.cameras = cameras          # CameraManager (sources: id -> CameraSource)
        self.data_root = Path(data_root)
        self.notify = notify or (lambda text: None)
        self.clock = clock
        self._detect = ffmpeg_detect
        self.ffmpeg: FFmpegInfo | None = None
        self.recorders: dict[str, CameraRecorder] = {}
        self.jobs: list[ClipJob] = []
        self.on_clip: list[Callable[[str, Path], None]] = []
        self.paused_disk = False
        self.last_usage: dict = {}
        self.last_cleanup = 0.0
        self._down_since: dict[str, float] = {}
        self._alerted: set[str] = set()
        self._lock = threading.RLock()
        self._warned_folder = ""
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self.store = RecordingStore(self.root)
        self.node_client_for: Callable[[str], object | None] = lambda cam_id: None  # camera del nodo → client
        # Camera del nodo → indirizzo del flusso **con l'audio**, se quel nodo lo sta pubblicando.
        # La collega il motore delle azioni, che è l'unico che parla con i nodi.
        self.audio_resolver: Callable[[str], str] | None = None

    # --- configurazione ---------------------------------------------------------
    def cfg(self, key: str, default=None):
        return self.config.get(f"recording.{key}", default)

    @property
    def root(self) -> Path:
        folder = (self.cfg("folder", "") or "").strip()
        if folder:
            p = Path(folder).expanduser()
            if p.is_absolute():
                return p
            if folder != self._warned_folder:
                self._warned_folder = folder
                log.warning("Cartella delle registrazioni non valida (serve un percorso completo): %s", folder)
        return self.data_root / "recordings"

    @property
    def enabled(self) -> bool:
        return bool(self.cfg("enabled", True))

    def mode_for(self, cam_id: str) -> str:
        mode = (self.cfg("cameras", {}) or {}).get(cam_id) or self.cfg("mode", "events")
        return mode if mode in MODES else "events"

    def armed_only(self, cam_id: str) -> bool:
        """La camera registra solo con la protezione attiva? (``recording.armed_only[<id>]``)"""
        return bool((self.cfg("armed_only", {}) or {}).get(cam_id, False))

    def armed(self) -> bool:
        return bool(self.config.get("actions.armed", False))

    def active_for(self, cam_id: str) -> bool:
        """La camera deve registrare adesso? (modalità non spenta e, se richiesto, protezione attiva)"""
        return self.mode_for(cam_id) != "off" and (self.armed() or not self.armed_only(cam_id))

    def folder_for(self, cam_id: str, name: str) -> Path:
        d = self.store.cam_dir(cam_id, name)
        return d / BUFFER_DIR if self.mode_for(cam_id) == "events" else d

    # --- ciclo di vita -------------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="registrazione", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 8.0) -> None:
        """Ferma tutti i registratori in parallelo, entro ``timeout`` secondi in totale."""
        self._stop.set()
        self._wake.set()
        with self._lock:
            recs = list(self.recorders.values())
            self.recorders.clear()
        self._stop_all(recs, timeout)
        if self._thread:
            self._thread.join(2)

    @staticmethod
    def _stop_all(recs: list, timeout: float = 8.0) -> None:
        threads = [threading.Thread(target=r.stop, name=f"stop-{r.name}", daemon=True) for r in recs]
        for t in threads:
            t.start()
        end = time.monotonic() + timeout
        for t in threads:
            t.join(max(0.0, end - time.monotonic()))

    def reload(self) -> None:
        """Configurazione cambiata (cartella, modalità): i registratori si riallineano nel loro thread, così
        l'interfaccia non aspetta mai ffmpeg."""
        self._wake.set()

    def _apply_root(self) -> None:
        new_root = self.root
        if new_root == self.store.root:
            return
        with self._lock:
            recs = list(self.recorders.values())
            self.recorders.clear()
        self._stop_all(recs)
        self.store = RecordingStore(new_root)
        self._kill_stale()

    def _kill_stale(self) -> None:
        """ffmpeg rimasti accesi da un arresto improvviso di Mirto: si chiudono (solo se sono davvero ffmpeg)."""
        for pid in self.store.stale_pids():
            if kill_if_ffmpeg(pid):
                log.warning("Chiuso un ffmpeg rimasto acceso (processo %d)", pid)
            self.store.forget_pid(pid)

    def poke(self) -> None:
        """Camere aggiunte o tolte: riallinea i registratori subito invece che entro 10 s."""
        self._wake.set()

    def ensure_ffmpeg(self) -> FFmpegInfo:
        if self.ffmpeg is None:
            self.ffmpeg = self._detect(self.cfg("ffmpeg", "") or "", self.cfg("encoder", "") or "")
        return self.ffmpeg

    # --- registratori -----------------------------------------------------------------
    def sync(self) -> None:
        """Avvia o ferma i registratori secondo camere collegate, modalità, ffmpeg e spazio."""
        sources = dict(getattr(self.cameras, "sources", {}) or {})
        want: dict[str, object] = {}
        if self.enabled and not self.paused_disk:
            ff = self.ensure_ffmpeg()
            if ff.ok:
                try:
                    self.store.ensure()
                except OSError as exc:
                    log.error("Cartella delle registrazioni non disponibile (%s): %s", self.root, exc)
                    sources = {}
                want = {cid: src for cid, src in sources.items() if self.active_for(cid)}
        folders: dict[str, Path] = {}
        for cid, src in want.items():  # creazione delle cartelle fuori dal lucchetto (disco di rete lento)
            try:
                folders[cid] = self.folder_for(cid, src.info.name)
                folders[cid].mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                log.error("Cartella della camera %s non disponibile: %s", src.info.name, exc)
        stop = []
        with self._lock:
            for cid, rec in list(self.recorders.items()):
                src = want.get(cid)
                if src is None or rec.source is not src or rec.folder != folders.get(cid):
                    stop.append(self.recorders.pop(cid))
                else:
                    rec.name = src.info.name
        self._stop_all(stop)
        new = []
        with self._lock:
            if self._stop.is_set():  # Mirto si sta chiudendo: niente registratori nuovi
                return
            for cid, src in want.items():
                if cid in self.recorders or cid not in folders:
                    continue
                rec = CameraRecorder(cid, src.info.name, src, self.store, self.ffmpeg,
                                     segment_seconds=int(self.cfg("segment_seconds", 60)),
                                     local_fps=float(self.cfg("local_fps", 10)),
                                     local_max_width=int(self.cfg("local_max_width", 1280)))
                rec.folder = folders[cid]
                rec.audio_resolver = self.audio_resolver
                self.recorders[cid] = rec
                new.append(rec)
        for rec in new:
            rec.start()

    def _segment_dirs(self) -> dict[str, list[Path]]:
        out: dict[str, list[Path]] = {}
        for cid, d in self.store.cameras().items():
            out[cid] = [d, d / BUFFER_DIR]
        return out

    def _open_paths(self) -> set[Path]:
        with self._lock:
            recs = list(self.recorders.values())
        return {p for p in (r.open_path() for r in recs) if p is not None}

    def segments(self, cam_id: str, buffer: bool | None = None, t0: float | None = None,
                 t1: float | None = None) -> list[Segment]:
        """Spezzoni di una camera (``buffer``: None = tutti, True = solo buffer, False = solo continui), facoltativamente
        solo quelli tra ``t0`` e ``t1`` (scelti dal nome, senza leggere gli altri file)."""
        d = self.store.cam_dir(cam_id)
        opened = self._open_paths()
        now = self.clock()
        dirs = [d, d / BUFFER_DIR] if buffer is None else ([d / BUFFER_DIR] if buffer else [d])
        segs: list[Segment] = []
        for folder in dirs:
            for s in self.store.segments_in(folder, now=now, t0=t0, t1=t1):
                if s.path in opened:
                    s.end = now
                segs.append(s)
        return sorted(segs, key=lambda s: s.start)

    # --- clip degli eventi ------------------------------------------------------------
    def request_clip(self, ev) -> bool:
        """Prenota la clip di un evento salvato (``ev.image_path`` → stesso nome, .mp4)."""
        cam_id = getattr(ev, "camera_id", "")
        img = getattr(ev, "image_path", "")
        if not img or not self.enabled or not self.active_for(cam_id):
            return False
        with self._lock:
            known = cam_id in self.recorders
        if not known and not self.store.cam_dir(cam_id).exists():
            return False
        pre, post = float(self.cfg("pre_seconds", 10)), float(self.cfg("post_seconds", 20))
        job = ClipJob(ev.id, cam_id, ev.ts - pre, ev.ts + post, Path(img).with_suffix(".mp4"),
                      due=ev.ts + post + CLIP_MARGIN_SECONDS, give_up=ev.ts + post + CLIP_GIVE_UP_SECONDS)
        with self._lock:
            self.jobs.append(job)
        self._wake.set()
        return True

    @staticmethod
    def _covers(segs: list[Segment], t: float) -> bool:
        """Sul disco ci sono già le immagini fino all'istante ``t``? (uno spezzone successivo è iniziato, oppure
        l'ultimo è stato scritto dopo ``t``: ffmpeg scrive un frammento a ogni fotogramma chiave)"""
        if any(s.start > t for s in segs):
            return True
        if not segs:
            return False
        try:
            return segs[-1].path.stat().st_mtime >= t + 1
        except OSError:
            return False

    def _process_jobs(self) -> None:
        now = self.clock()
        with self._lock:
            due = [j for j in self.jobs if j.due <= now]
        for job in due:
            all_segs = self.segments(job.cam_id)
            if not self._covers(all_segs, job.t1) and self.clock() < job.t1 + CLIP_MAX_WAIT_SECONDS:
                job.due = self.clock() + 2  # il frammento con la fine della clip non è ancora su disco
                continue
            segs = self.store.between(all_segs, job.t0, job.t1)
            err = ffmpeg_concat_clip(self.ffmpeg.path, segs, job.t0, job.t1, job.out, self.root / ".tmp") \
                if (segs and self.ffmpeg and self.ffmpeg.ok) else "nessuna registrazione per l'evento"
            with self._lock:
                if err and self.clock() < job.give_up:
                    job.due = self.clock() + 15  # si riprova (es. ffmpeg appena ripartito)
                    continue
                if job in self.jobs:
                    self.jobs.remove(job)
            if err:
                log.info("Clip dell'evento %s non creata: %s", job.event_id, err)
                continue
            log.info("Clip dell'evento %s pronta (%s)", job.event_id, job.out.name)
            for cb in list(self.on_clip):
                try:
                    cb(job.event_id, job.out)
                except Exception:
                    log.exception("Errore nella gestione della clip")

    def live_clip(self, cam_id: str, seconds: float) -> tuple[Path | None, str]:
        """Clip degli ultimi ``seconds`` secondi (per /clip su Telegram). Restituisce (file, "") o (None, errore)."""
        if self.mode_for(cam_id) == "off" or not self.enabled:
            return None, _("La registrazione di questa camera è spenta (Impostazioni → Registrazione).")
        if not self.active_for(cam_id):
            return None, _("Questa camera registra solo con la protezione attiva.")
        if not (self.ffmpeg and self.ffmpeg.ok):
            return None, _("Registrazione non disponibile.")
        now = self.clock()
        segs = self.store.between(self.segments(cam_id), now - seconds, now)
        if not segs:
            return None, _("Questa camera non sta registrando in questo momento.")
        out = self.root / ".tmp" / f"clip-{int(now)}-{abs(hash(cam_id)) % 10000}.mp4"
        err = ffmpeg_concat_clip(self.ffmpeg.path, segs, now - seconds, now, out, self.root / ".tmp")
        return (None, err) if err else (out, "")

    # --- spazio e conservazione -----------------------------------------------------------
    def _protected(self) -> list[tuple[str, float, float]]:
        with self._lock:
            return [(j.cam_id, j.t0, j.t1) for j in self.jobs]

    def cleanup(self) -> dict:
        """Cancella il buffer superato, le registrazioni oltre i giorni di conservazione e quelle oltre il limite
        di spazio. Restituisce l'uso del disco."""
        now = self.clock()
        opened = self._open_paths()
        protected = self._protected()
        pre, post = float(self.cfg("pre_seconds", 10)), float(self.cfg("post_seconds", 20))
        seg_s = float(self.cfg("segment_seconds", 60))
        buffer_keep = max(180.0, pre + post + 2 * seg_s)
        keep_days = float(self.cfg("retention_days", 7))
        removed = 0
        everything: list[tuple[str, Segment]] = []

        def busy(cid: str, s: Segment) -> bool:
            return s.path in opened or any(c == cid and s.start <= t1 and s.end >= t0 for c, t0, t1 in protected)

        for cid, (main, buf) in self._segment_dirs().items():
            for s in self.store.segments_in(buf, now=now):
                if s.end < now - buffer_keep and not busy(cid, s):
                    removed += self.store.delete(s)
                else:
                    everything.append((cid, s))
            for s in self.store.segments_in(main, now=now):
                if s.end < now - keep_days * 86400 and not busy(cid, s):
                    removed += self.store.delete(s)
                else:
                    everything.append((cid, s))
        usage = self.store.usage()
        used, free = usage["used"], usage["free"]
        if not usage.get("total"):
            # spazio non misurabile (disco esterno staccato, cartella di rete bloccata): non si cancella niente
            log.warning("Spazio del disco delle registrazioni non misurabile: nessuna cancellazione per spazio")
            usage.update(limit=0)
            self.last_usage = usage
            self.last_cleanup = now
            return usage
        max_gb = float(self.cfg("max_gb", 0) or 0)
        limit = max_gb * 1e9 if max_gb > 0 else float(self.cfg("max_disk_fraction", 0.5)) * (used + free)
        min_free = float(self.cfg("min_free_gb", 2)) * 1e9
        everything.sort(key=lambda x: x[1].start)
        for cid, s in everything:
            over_quota = used > limit
            if not over_quota and free >= min_free:
                break
            if busy(cid, s):
                continue
            if not over_quota and s.end > now - KEEP_RECENT_SECONDS:
                break  # disco pieno per colpa d'altro: le ultime 24 ore non si toccano, si sospende
            if self.store.delete(s):
                used -= s.size
                free += s.size
                removed += 1
        if removed:
            log.info("Registrazioni: cancellati %d spezzoni (conservazione e spazio)", removed)
        usage.update(used=used, free=free, limit=limit)
        # niente spazio nemmeno dopo aver cancellato: si sospende tutto
        full = free < min_free
        if full and not self.paused_disk:
            self.paused_disk = True
            self.notify(_("⚠️ Registrazione sospesa: il disco delle registrazioni è pieno ({free} GB liberi).",
                          free=f"{free / 1e9:.1f}"))
        elif not full and self.paused_disk and free >= min_free * 1.5:
            self.paused_disk = False
            self.notify(_("✅ Registrazione ripresa: c'è di nuovo spazio sul disco."))
        self.last_usage = usage
        self.last_cleanup = now
        try:
            self._prune_node_cache()
        except OSError:
            pass
        return usage

    # --- avvisi ------------------------------------------------------------------------------
    def _check_alerts(self) -> None:
        now = self.clock()
        with self._lock:
            recs = list(self.recorders.values())
        for r in recs:
            if r.state == STATE_ERROR:
                since = self._down_since.setdefault(r.cam_id, now)
                if now - since >= ALERT_AFTER_SECONDS and r.cam_id not in self._alerted:
                    self._alerted.add(r.cam_id)
                    self.notify(_("⚠️ Registrazione di {camera} ferma: {error}", camera=r.name, error=r.error))
            elif r.state == STATE_RECORDING:
                self._down_since.pop(r.cam_id, None)
                if r.cam_id in self._alerted:
                    self._alerted.discard(r.cam_id)
                    self.notify(_("✅ Registrazione di {camera} ripresa", camera=r.name))

    def _watchdog(self) -> None:
        with self._lock:
            recs = list(self.recorders.values())
        for r in recs:
            if r.stalled():
                log.warning("Registrazione di %s bloccata: riavvio di ffmpeg", r.name)
                r.error = "registrazione bloccata, riavvio"
                r.restart()

    def _run(self) -> None:
        try:
            self._kill_stale()
            self.ensure_ffmpeg()
        except Exception:  # noqa: BLE001
            log.exception("Avvio della registrazione non riuscito")
        last_sync = 0.0
        last_armed = self.armed()
        while not self._stop.is_set():
            now = self.clock()
            if self.armed() != last_armed:  # protezione cambiata: le camere "solo a protezione attiva" seguono subito
                last_armed = self.armed()
                self._wake.set()
            try:
                if now - last_sync >= 10 or self._wake.is_set():
                    self._wake.clear()
                    last_sync = now
                    self._apply_root()
                    self.sync()
                    self._check_alerts()
                    self._watchdog()
                if now - self.last_cleanup >= 60:
                    self.cleanup()
                if self.jobs and self.ffmpeg is not None:
                    self._process_jobs()
            except Exception:
                log.exception("Errore nella gestione delle registrazioni")
            self._wake.wait(2.0)

    # --- stato per l'interfaccia ----------------------------------------------------------------
    def _spazio(self) -> dict:
        """Spazio del disco delle registrazioni, anche a registrazione spenta.

        Lo misurava solo il giro di pulizia, che gira quando si registra: spenta la registrazione, la
        riga «Spazio» spariva da «Sistema». Ma quanto spazio resta sul computer è un'informazione che
        serve sempre — anche, e soprattutto, per decidere se riaccendere la registrazione. Chiesto da
        Marco il 21/10, provando il pannello del disco.
        """
        uso = dict(self.last_usage)
        if uso.get("total"):
            return uso
        try:
            return self.store.usage()
        except Exception:  # noqa: BLE001
            return uso

    def status(self) -> dict:
        with self._lock:
            recs = [r.status() for r in self.recorders.values()]
        ff = self.ffmpeg
        return {"enabled": self.enabled, "root": str(self.root), "paused_disk": self.paused_disk,
                "ffmpeg": ff.path if ff else None, "ffmpeg_version": ff.version if ff else "",
                "encoder": ff.encoder if ff else "", "ffmpeg_error": ff.error if ff else "",
                "recorders": recs, "usage": self._spazio(), "pending_clips": len(self.jobs),
                "waiting_armed": [cid for cid in (getattr(self.cameras, "sources", {}) or {})
                                  if self.mode_for(cid) != "off" and not self.active_for(cid)]}

    def camera_state(self, cam: dict) -> tuple[str, str]:
        """Cosa sta facendo adesso la registrazione di una camera (per la scheda Impostazioni → Registrazione).

        ``cam`` è una voce di ``CameraManager.all_cameras()``. Restituisce (tipo, testo) con tipo
        "ok" (registra), "paused" (in pausa o in avvio), "off" (non registra), "error"."""
        cid = cam["id"]
        mode = self.mode_for(cid)
        if not cam.get("enabled", True):
            return "off", _("camera spenta (Tutte le cam)")
        if not cam.get("connected", True):
            return "off", _("camera non collegata")
        if not self.enabled:
            return "off", _("registrazione spenta")
        if mode == "off":
            return "off", _("non registra (solo la foto degli eventi)")
        ff = self.ffmpeg
        if ff is not None and not ff.ok:
            return "error", ff.error or _("ffmpeg non disponibile")
        if self.paused_disk:
            return "error", _("sospesa: disco pieno")
        if not self.active_for(cid):
            return "paused", _("in pausa: la protezione è disattivata")
        with self._lock:
            rec = self.recorders.get(cid)
        if rec is None or rec.state in ("starting", "off"):
            return "paused", _("avvio…")
        if rec.state == "waiting":
            return "paused", _("in attesa di immagini dalla camera")
        if rec.state == "error":
            return "error", _("ferma: {error}", error=rec.error or "?")
        if mode == "continuous":
            return "ok", _("registra tutto")
        return "ok", _("registra le clip degli eventi")

    # --- vista Rivedi ---------------------------------------------------------------------------
    def recorded_cameras(self) -> list[tuple[str, str]]:
        """(ID, nome) delle camere che hanno registrazioni, anche se oggi non sono collegate."""
        out = []
        for cid in self.store.cameras():
            out.append((cid, self.store.camera_name(cid)))
        return sorted(out, key=lambda x: x[1].lower())

    def export(self, cam_id: str, t0: float, t1: float, out: Path, segments: list[Segment] | None = None) -> str:
        """Salva in ``out`` il video da ``t0`` a ``t1`` (senza ricodifica). "" se va bene, altrimenti l'errore.
        ``segments``: quelli da usare (la vista Rivedi passa anche le clip degli eventi)."""
        ff = self.ensure_ffmpeg()
        if not ff.ok:
            return ff.error or _("ffmpeg non disponibile")
        segs = self.store.between(segments if segments is not None else self.segments(cam_id, t0=t0, t1=t1), t0, t1)
        for s in segs:  # pezzi che stanno solo sul nodo: si scaricano prima
            if isinstance(s, RemoteSegment):
                try:
                    self.fetch(cam_id, s)
                except Exception as exc:  # noqa: BLE001
                    return _("Spezzone {name} non scaricabile dal nodo: {exc}", name=s.name, exc=exc)
        if not segs:
            return _("Nessuna registrazione in quell'intervallo.")
        return ffmpeg_concat_clip(ff.path, segs, t0, t1, Path(out), self.root / ".tmp")

    def frame_at(self, segment: Segment, offset: float, out: Path) -> str:
        """Salva in ``out`` (JPEG) il fotogramma a ``offset`` secondi dall'inizio dello spezzone."""
        ff = self.ensure_ffmpeg()
        if not ff.ok:
            return ff.error or _("ffmpeg non disponibile")
        cmd = [ff.path, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{max(0.0, offset):.2f}", "-i",
               str(segment.path), "-frames:v", "1", "-q:v", "2", str(out)]
        try:
            res = subprocess.run(cmd, capture_output=True, timeout=30, **popen_flags())
        except (OSError, subprocess.SubprocessError) as exc:
            return str(exc)
        if res.returncode != 0 or not Path(out).exists():
            err = res.stderr.decode("utf-8", "replace").strip().splitlines()
            return (err[-1] if err else _("foto non riuscita"))[:200]
        return ""

    # --- registrazioni sui nodi (API 1.4) -----------------------------------------------------------
    def node_cache_dir(self, cam_id: str) -> Path:
        return self.root / ".node-cache" / self.store.cam_dir(cam_id).name

    def node_segments(self, cam_id: str, t0: float, t1: float) -> list[RemoteSegment] | None:
        """Spezzoni che il nodo di questa camera ha registrato tra ``t0`` e ``t1`` (chiamata di rete: mai dal
        thread dell'interfaccia). Lista vuota se la camera non è di un nodo; None se il nodo non risponde (così chi
        chiama tiene quello che sapeva già)."""
        client = self.node_client_for(cam_id)
        if client is None:
            return []
        try:
            res = client.recordings(t0, t1)
        except Exception as exc:  # noqa: BLE001  (nodo spento, versione vecchia senza registrazione…)
            log.info("Registrazioni del nodo per %s non disponibili: %s", cam_id, exc)
            return None
        cache = self.node_cache_dir(cam_id)
        node = getattr(getattr(client, "node", None), "name", "")
        out = []
        for s in (res or {}).get("spezzoni") or []:
            try:
                out.append(RemoteSegment(cache / s["nome"], float(s["inizio"]), float(s["fine"]), int(s["byte"]),
                                         name=s["nome"], node=node))
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def fetch(self, cam_id: str, seg: RemoteSegment, stop=None) -> Path:
        """Copia locale di uno spezzone del nodo (scaricato solo se manca o è cambiato, es. quello in scrittura)."""
        client = self.node_client_for(cam_id)
        if client is None:
            raise RuntimeError(_("Il nodo di questa camera non è raggiungibile."))
        try:
            if seg.path.exists() and seg.path.stat().st_size == seg.size:
                return seg.path
        except OSError:
            pass
        # lo spezzone che il nodo sta ancora scrivendo cresce: la copia vale per quello che è stato scaricato
        seg.size = client.download_recording(seg.name, seg.path, stop=stop)
        return seg.path

    def _prune_node_cache(self) -> None:
        base = self.root / ".node-cache"
        if not base.is_dir():
            return
        files = sorted((p for p in base.rglob("*.mp4") if p.is_file()), key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in files)
        now = time.time()
        for p in files:
            try:
                st = p.stat()
                if total > NODE_CACHE_BYTES or now - st.st_mtime > NODE_CACHE_SECONDS:
                    p.unlink()
                    total -= st.st_size
            except OSError:
                continue

