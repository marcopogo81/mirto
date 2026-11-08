# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Registratore di una camera: un processo ffmpeg sorvegliato e riavviato se si ferma.

Due modi:

* **copia** (camere di rete RTSP: nodi, camere IP): ffmpeg legge il flusso e lo scrive così com'è, senza
  ricodificarlo. Il Mac quasi non lavora. È una seconda connessione, indipendente da quella del rilevamento.
* **fotogrammi** (camere locali: Mac, USB, iPhone; camere HTTP/MJPEG): i fotogrammi che Mirto già legge per
  il rilevamento vengono passati a ffmpeg a ritmo costante (``local_fps``) e codificati in H.264, con il
  codificatore hardware se c'è.

ffmpeg scrive spezzoni MP4 frammentati (leggibili anche mentre vengono scritti) tagliati allo scoccare del
minuto, con l'ora di inizio (UTC) nel nome. L'audio **non** viene registrato, tranne quando un nodo
pubblica il proprio microfono accanto al video e qualcuno l'ha acceso: in quel caso si registra il flusso
che il nodo manda, com'è (vedi `node/mirto_node/flusso_audio.py`).
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

from .ffmpeg import FFmpegInfo, popen_flags, utc_env
from .store import STRFTIME_PATTERN, RecordingStore, parse_start

log = logging.getLogger(__name__)

STALL_SECONDS = 30        # nessun byte nuovo per tanto tempo: il flusso è bloccato, si riavvia ffmpeg
FRAME_STALE_SECONDS = 3   # nessun fotogramma nuovo dalla camera: pausa (buco nella registrazione)

STATE_OFF = "off"
STATE_STARTING = "starting"
STATE_RECORDING = "recording"
STATE_WAITING = "waiting"   # camera senza immagini
STATE_ERROR = "error"


def segment_output(folder: Path, seconds: int) -> list[str]:
    # "%" nel percorso verrebbe letto da -strftime come un codice di data: si raddoppia
    pattern = os.path.join(str(folder).replace("%", "%%"), STRFTIME_PATTERN)
    return ["-f", "segment", "-segment_time", str(int(seconds)), "-segment_atclocktime", "1",
            "-reset_timestamps", "1", "-strftime", "1", "-segment_format", "mp4",
            "-segment_format_options", "movflags=+frag_keyframe+empty_moov+default_base_moof", pattern]


def is_copy_source(info) -> bool:
    url = (getattr(info, "url", None) or "").lower()
    return getattr(info, "kind", "") == "network" and url.startswith(("rtsp://", "rtsps://"))


def output_size(w: int, h: int, max_width: int) -> tuple[int, int]:
    if max_width and w > max_width:
        h = int(round(h * max_width / w / 2)) * 2
        w = max_width
    return w - w % 2, h - h % 2


def bitrate_kbps(w: int, h: int, fps: float) -> int:
    """Circa 1 Mbit/s per 720p a 10 fps: abbastanza per riconoscere un volto, ~10 GB al giorno."""
    return int(min(4000, max(300, w * h * fps * 0.00011)))


class CameraRecorder:
    def __init__(self, cam_id: str, name: str, source, store: RecordingStore, ffmpeg: FFmpegInfo,
                 segment_seconds: int = 60, local_fps: float = 10, local_max_width: int = 1280):
        self.cam_id = cam_id
        self.name = name
        self.source = source
        # Dove prendere il flusso **con l'audio**, se c'è: una funzione che risponde «"" » quando non
        # c'è niente da prendere. Arriva da fuori (il motore delle azioni la collega) perché saperlo
        # vuol dire chiedere al nodo, e un registratore non parla con i nodi.
        self.audio_resolver = None
        self.store = store
        self.ffmpeg = ffmpeg
        self.segment_seconds = segment_seconds
        self.local_fps = max(1.0, float(local_fps))
        self.local_max_width = int(local_max_width)
        self.folder = store.cam_dir(cam_id, name)
        self.copy_mode = is_copy_source(source.info)
        self.state = STATE_OFF
        self.error = ""
        self.started_at = 0.0
        self.restarts = 0
        self.encoder = "copy" if self.copy_mode else ffmpeg.encoder
        self._proc: subprocess.Popen | None = None
        self._stderr: deque[str] = deque(maxlen=20)
        self.last_progress = time.monotonic()   # ultimo byte scritto (copia) o fotogramma passato (fotogrammi)
        self.clock_warning = ""
        self._codec: dict[str, str] = {}       # indirizzo -> codec video (per l'etichetta hvc1 dell'H.265)
        self._stop = threading.Event()
        self._kick = threading.Event()   # riavvio richiesto (es. indirizzo della camera cambiato)
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # --- API ------------------------------------------------------------------
    def start(self) -> "CameraRecorder":
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self.state = STATE_STARTING
        self._thread = threading.Thread(target=self._run, name=f"reg-{self.name}", daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout: float = 6.0) -> None:
        self._stop.set()
        self._kick.set()
        self._finish_process(from_worker=False)
        if self._thread:
            self._thread.join(timeout)
        self.state = STATE_OFF

    def restart(self) -> None:
        """Riapre ffmpeg (indirizzo cambiato, oppure registrazione bloccata)."""
        self._kick.set()
        self._finish_process(from_worker=False)

    def stalled(self, now: float | None = None) -> bool:
        """ffmpeg acceso ma fermo da troppo tempo (es. codificatore bloccato dopo la sospensione del Mac)."""
        return self.running and (now or time.monotonic()) - self.last_progress > STALL_SECONDS * 2

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def open_path(self) -> Path | None:
        """Lo spezzone che ffmpeg sta scrivendo adesso (il più recente), se sta registrando."""
        if not self.running:
            return None
        return self.store.newest(self.folder)[0]

    def status(self) -> dict:
        return {"id": self.cam_id, "name": self.name, "state": self.state, "error": self.error or self.clock_warning,
                "mode": "copy" if self.copy_mode else "frames", "encoder": self.encoder,
                "since": self.started_at, "restarts": self.restarts}

    # --- processo ffmpeg ---------------------------------------------------------
    def _spawn(self, cmd: list[str]) -> subprocess.Popen:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                env=utc_env(), **popen_flags())
        self._stderr.clear()
        self.last_progress = time.monotonic()
        threading.Thread(target=self._read_stderr, args=(proc,), name=f"reg-err-{self.name}", daemon=True).start()
        with self._lock:
            self._proc = proc
        self.store.remember_pid(proc.pid)
        return proc

    def _read_stderr(self, proc: subprocess.Popen) -> None:
        try:
            for raw in iter(proc.stderr.readline, b""):
                line = raw.decode("utf-8", "replace").strip()
                if line:
                    self._stderr.append(line)
        except (OSError, ValueError):
            pass
        finally:
            try:
                proc.stderr.close()
            except OSError:
                pass

    def _last_error(self, fallback: str) -> str:
        for line in reversed(self._stderr):
            # niente credenziali nei messaggi (rtsp://utente:password@…)
            if "://" in line and "@" in line:
                continue
            return line[:200]
        return fallback

    def _finish_process(self, from_worker: bool = True) -> None:
        """Chiude ffmpeg. Dal thread del registratore: fine pulita (``q`` o fine dei fotogrammi). Da un altro thread
        (stop, riavvio) non si tocca mai l'ingresso dei fotogrammi, che può essere bloccato in scrittura: si manda il
        segnale di chiusura, e dopo 4 s si uccide il processo."""
        with self._lock:
            proc = self._proc
        if proc is None:
            return
        if proc.poll() is None:
            try:
                if self.copy_mode:
                    proc.stdin.write(b"q")  # in copia nessun altro scrive sull'ingresso
                    proc.stdin.flush()
                elif from_worker:
                    proc.stdin.close()
                else:
                    proc.terminate()
            except (OSError, ValueError):
                pass
            try:
                proc.wait(4)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(2)
                except subprocess.TimeoutExpired:
                    log.error("ffmpeg (%s) non si chiude", self.name)
                    return
        if from_worker:
            try:
                proc.stdin.close()
            except (OSError, ValueError):
                pass
        self.store.forget_pid(proc.pid)

    # --- ciclo principale -----------------------------------------------------------
    def _run(self) -> None:
        backoff = 2.0
        while not self._stop.is_set():
            self._kick.clear()
            t0 = time.monotonic()
            try:
                ok = self._copy_once() if self.copy_mode else self._frames_once()
            except Exception as exc:  # noqa: BLE001
                log.exception("Registrazione di %s interrotta", self.name)
                ok, self.error = False, str(exc)
            self._finish_process()
            if self._stop.is_set():
                break
            if self._kick.is_set():
                continue
            if ok is None:  # in attesa di immagini: nessun errore, si riprova presto
                self._stop.wait(1.0)
                continue
            if time.monotonic() - t0 > 120:
                backoff = 2.0
            self.state = STATE_ERROR
            self.restarts += 1
            log.warning("Registrazione di %s ferma (%s): nuovo tentativo tra %.0f s", self.name, self.error, backoff)
            self._stop.wait(backoff)
            backoff = min(backoff * 2, 60.0)
        self.state = STATE_OFF

    def _mark_recording(self) -> None:
        self.last_progress = time.monotonic()
        if self.state != STATE_RECORDING:
            self.state = STATE_RECORDING
            self.error = ""
            self.started_at = time.time()
            log.info("Registrazione di %s avviata (%s)", self.name, self.encoder)
            newest, _size = self._newest_size()
            start = parse_start(newest.name) if newest else None
            if start is not None and abs(start - time.time()) > 300:  # ffmpeg non ha usato l'ora UTC
                self.clock_warning = f"orari dei file sfasati di {(start - time.time()) / 3600:+.1f} h"
                log.warning("Registrazione di %s: %s", self.name, self.clock_warning)

    def _newest_size(self) -> tuple[Path | None, int]:
        return self.store.newest(self.folder)

    def _url_con_audio(self) -> str:
        """L'indirizzo del flusso con l'audio, o "" se non c'è niente da prendere.

        Non solleva mai: se chiedere al nodo non riesce, si registra come si è sempre registrato. Una
        registrazione muta è un peccato; una registrazione che non parte perché il nodo non risponde
        è una casa non sorvegliata.
        """
        if self.audio_resolver is None:
            return ""
        try:
            return str(self.audio_resolver(self.cam_id) or "")
        except Exception:  # noqa: BLE001
            log.debug("Flusso con audio di %s non disponibile", self.name, exc_info=True)
            return ""

    def _probe_codec(self, url: str) -> str:
        """Codec video del flusso ("h264", "hevc"…), letto una volta per indirizzo."""
        if url not in self._codec:
            codec = ""
            try:
                res = subprocess.run([self.ffmpeg.path, "-hide_banner", "-rtsp_transport", "tcp", "-timeout",
                                      "5000000", "-i", url], capture_output=True, timeout=20, env=utc_env(),
                                     **popen_flags())
                for line in res.stderr.decode("utf-8", "replace").splitlines():
                    if "Video:" in line:
                        codec = line.split("Video:", 1)[1].strip().split()[0].strip(",")
                        break
            except (OSError, subprocess.SubprocessError):
                pass
            if not codec:
                return ""  # camera non raggiungibile: si riproverà
            self._codec[url] = codec
        return self._codec[url]

    def _copy_once(self) -> bool:
        # Nota: l'indirizzo (con utente e password della camera) è tra gli argomenti di ffmpeg, quindi visibile
        # nell'elenco dei processi agli altri utenti dello stesso computer. Su un Mac personale va bene; è
        # dichiarato in SECURITY.md. Nei registri l'indirizzo passa sempre da redact_url.
        url = self.source.stream_url()
        indirizzo = self.source.info.url or ""
        # «-an» è stato una promessa, non una dimenticanza: fino al 26/10 l'interfaccia e le due guide
        # dicevano che l'audio non viene mai registrato. Dal 26/10 un **nodo** può pubblicare il
        # microfono accanto al video, e quando lo fa quel flusso si registra com'è. Resta una scelta
        # esplicita, per nodo, spenta di serie: qui non si decide niente, si usa quello che il nodo
        # dice di stare pubblicando.
        con_audio = self._url_con_audio()
        if con_audio:
            url = con_audio
            # «?» perché l'audio può non esserci in questo istante (il muxer sul nodo è appena
            # ripartito): senza, ffmpeg morirebbe e si perderebbe anche il video.
            audio = ["-map", "0:a:0?", "-c:a", "copy"]
        else:
            audio = ["-an"]
        tag = ["-tag:v", "hvc1"] if self._probe_codec(url) == "hevc" else []  # H.265 apribile su Mac e iPhone
        cmd = [self.ffmpeg.path, "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp",
               "-timeout", "10000000", "-i", url, "-map", "0:v:0", "-c", "copy", *tag, *audio,
               *segment_output(self.folder, self.segment_seconds)]
        proc = self._spawn(cmd)
        self.state = STATE_STARTING
        last_key, last_change = self._newest_size(), time.monotonic()
        while not self._stop.is_set() and not self._kick.is_set():
            if proc.poll() is not None:
                self.error = self._last_error(f"ffmpeg terminato (codice {proc.returncode})")
                return False
            # Qui si guarda l'**indirizzo**, non stream_url(): quello rilegge le credenziali dal
            # portachiavi, e questo giro passa ogni due secondi. Su un'app non firmata voleva dire una
            # richiesta di consenso ogni due secondi, per sempre (22/10). Se cambiano le credenziali
            # ffmpeg fallisce, questo pezzo riparte e stream_url() le rilegge li': una volta, non mille.
            if (self.source.info.url or "") != indirizzo:  # la camera ha cambiato indirizzo
                log.info("Registrazione di %s: nuovo indirizzo, riavvio", self.name)
                self._kick.set()
                return True
            key = self._newest_size()
            now = time.monotonic()
            if key != last_key and key[1] > 0:
                last_key, last_change = key, now
                self._mark_recording()
            elif now - last_change > STALL_SECONDS:
                self.error = f"nessun dato dalla camera da {STALL_SECONDS} s"
                return False
            self._stop.wait(2.0)
        return True

    def _frames_once(self) -> bool | None:
        frame, ts, _seq = self.source.latest()
        if frame is None or time.time() - ts > FRAME_STALE_SECONDS:
            if self.state != STATE_WAITING:
                self.state = STATE_WAITING
            return None
        if not self.ffmpeg.encoder:
            self.error = "nessun codificatore H.264"
            return False
        h, w = frame.shape[:2]
        ow, oh = output_size(w, h, self.local_max_width)
        fps = self.local_fps
        self.encoder = self.ffmpeg.encoder
        cmd = [self.ffmpeg.path, "-hide_banner", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
               "-s", f"{w}x{h}", "-r", f"{fps:g}", "-i", "-"]
        if (ow, oh) != (w, h):
            cmd += ["-vf", f"scale={ow}:{oh}"]
        cmd += ["-pix_fmt", "yuv420p", *self.ffmpeg.encoder_args(bitrate_kbps(ow, oh, fps)),
                "-g", str(int(fps * 2)), *segment_output(self.folder, self.segment_seconds)]
        proc = self._spawn(cmd)
        self.state = STATE_STARTING
        step = 1.0 / fps
        next_t = time.monotonic()
        written = 0
        while not self._stop.is_set() and not self._kick.is_set():
            frame, ts, _seq = self.source.latest()
            if frame is None or time.time() - ts > FRAME_STALE_SECONDS:
                self.state = STATE_WAITING  # buco: si chiude lo spezzone, si riparte quando tornano le immagini
                return None
            if frame.shape[:2] != (h, w):
                self._kick.set()  # risoluzione cambiata
                return True
            now = time.monotonic()
            if now - next_t > 5:  # troppo indietro (computer sospeso o ffmpeg lento): si riparte pulito
                self.error = "registrazione rimasta indietro, riavvio"
                return False
            try:
                data = frame.tobytes() if frame.flags["C_CONTIGUOUS"] else frame.copy().tobytes()
                while next_t <= now:  # un fotogramma per ogni passo: il tempo del video resta quello vero
                    proc.stdin.write(data)
                    next_t += step
                    written += 1
                self.last_progress = time.monotonic()
            except (BrokenPipeError, OSError, ValueError):
                time.sleep(0.2)
                self.error = self._last_error("ffmpeg si è chiuso")
                return False
            if written >= fps:  # almeno un secondo scritto senza errori
                self._mark_recording()
            if proc.poll() is not None:
                self.error = self._last_error(f"ffmpeg terminato (codice {proc.returncode})")
                return False
            self._stop.wait(max(0.0, next_t - time.monotonic()))
        return True
