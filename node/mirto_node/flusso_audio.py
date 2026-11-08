# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Il flusso della camera con l'audio del microfono accanto, pubblicato dal nodo.

Marco, 25/10: *«la registrazione del video non contiene l'audio pur avendo il microfono agganciato;
vorrei che quando parte la registrazione di un evento partisse anche la registrazione del microfono»*.

**Perché nel nodo, e non sul Mac.** Mescolare lì vorrebbe dire due ingressi ffmpeg — il video via RTSP
e l'audio via ``GET /audio/flusso`` — che arrivano per strade diverse, con ritardi diversi: la
sincronia verrebbe a caso, e ogni clip sarebbe sincronizzata un po' diversamente dalla precedente.
Mescolato qui, l'audio e il video stanno in **un** flusso, e tutti quelli che lo leggono — la
registrazione continua, la clip di un evento, la vista dal vivo — lo trovano già insieme.

**Perché l'audio arriva da stdin e non da ALSA.** Il microfono di questo nodo lo legge un ``arecord``
solo, condiviso fra chi ascolta (``mic.Microphone``): è quello che permette al riconoscimento dei suoni
e all'``/ascolta`` di Telegram di convivere. Un ffmpeg che aprisse ALSA per conto suo sarebbe un
**secondo** lettore, e su una scheda che non si apre due volte vorrebbe dire che accendere l'audio
nelle registrazioni spegne il riconoscimento dei suoni — un difetto che si scoprirebbe in casa di un
tester. Qui il muxer diventa semplicemente il terzo ascoltatore di quell'unico ``arecord``.

**Il flusso con l'audio è un percorso a parte** (``cam-audio``), non il percorso della camera. Se
questo processo muore, si perde l'audio: la camera, la vista dal vivo e la registrazione normale
restano in piedi. Pubblicare direttamente su ``cam`` sarebbe stato più semplice e avrebbe legato la
camera di un prodotto di sorveglianza alla salute di un ffmpeg.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time

log = logging.getLogger(__name__)

RATE = 16000
ATTESA_RIPARTENZA = 5.0      # dopo una caduta: tanto basta se la camera stava riavviandosi
AUDIO_BITRATE = "64k"        # una voce a 16 kHz mono: di più non aggiunge niente da sentire


def comando(ffmpeg: str, camera_url: str, destinazione: str, rate: int = RATE) -> list[str]:
    """Il comando del muxer. Fuori dalla classe perché così una prova lo può leggere senza eseguirlo.

    Il video si **copia** (``-c:v copy``): ricodificarlo su un Raspberry 3B vorrebbe dire un core
    sempre occupato e la qualità peggiore di quella che la camera dà già. L'audio si codifica in AAC
    perché è quello che un MP4 sa contenere e che un telefono sa aprire.

    ``-use_wallclock_as_timestamps`` sull'ingresso del PCM: quei byte non portano un orario, e senza
    questo ffmpeg li numererebbe come se arrivassero a velocità perfetta — un ritardo che cresce per
    tutta la durata della registrazione. Marcati all'arrivo, l'errore resta quello della strada fra
    ``arecord`` e qui, cioè i centesimi di secondo.
    """
    return [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostats",
            "-rtsp_transport", "tcp", "-i", camera_url,
            "-use_wallclock_as_timestamps", "1", "-f", "s16le", "-ar", str(rate), "-ac", "1", "-i", "pipe:0",
            "-map", "0:v:0", "-c:v", "copy", "-map", "1:a:0", "-c:a", "aac", "-b:a", AUDIO_BITRATE,
            "-f", "rtsp", "-rtsp_transport", "tcp", destinazione]


class FlussoConAudio:
    """Tiene in piedi il muxer: se cade, riparte; se il microfono sparisce, lo dice e riprova."""

    def __init__(self, camera_url: str, destinazione: str, mic, rate: int = RATE,
                 popen=subprocess.Popen, clock=time.time, attesa: float = ATTESA_RIPARTENZA,
                 ffmpeg: str = "ffmpeg"):
        self.camera_url, self.destinazione, self.mic = camera_url, destinazione, mic
        self.rate, self.attesa, self.ffmpeg = rate, attesa, ffmpeg
        self._popen, self.clock = popen, clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc = None
        self.errore = ""
        self.da = 0.0            # da quando sta pubblicando senza interruzioni (0 = non pubblica)

    # --- fuori ----------------------------------------------------------------
    def start(self) -> "FlussoConAudio":
        if self._thread is None:
            self._thread = threading.Thread(target=self._sorveglia, name="flusso-audio", daemon=True)
            self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._ammazza()
        t = self._thread
        if t is not None:
            t.join(timeout=5)
        self._thread = None

    def attivo(self) -> bool:
        return bool(self.da) and self._proc is not None and self._proc.poll() is None

    def status(self) -> dict:
        """Come lo vede Mirto. ``attivo`` falso **con** un errore è diverso da falso senza: il primo
        è qualcosa da sistemare, il secondo è una cosa che sta partendo."""
        st = {"attivo": self.attivo(), "percorso": self.destinazione.rsplit("/", 1)[-1]}
        if st["attivo"]:
            st["da_s"] = int(max(0.0, self.clock() - self.da))
        if self.errore:
            st["errore"] = self.errore[:200]
        return st

    # --- dentro ---------------------------------------------------------------
    def _sorveglia(self) -> None:
        while not self._stop.is_set():
            try:
                self._un_giro()
            except Exception as exc:  # noqa: BLE001  (un sorvegliante non muore: è il suo mestiere)
                self.errore = str(exc)[:200]
                log.warning("Flusso con audio fermo: %s", self.errore)
            self.da = 0.0
            self._ammazza()
            self._stop.wait(self.attesa)

    def _un_giro(self) -> None:
        from .mic import MicError  # noqa: PLC0415

        try:
            self._proc = self._popen(comando(self.ffmpeg, self.camera_url, self.destinazione, self.rate),
                                     stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.PIPE)
        except OSError as exc:
            # ffmpeg manca: è un difetto dell'installazione, non un intoppo. Si dice una volta e in
            # chiaro, perché il rimedio non è aspettare.
            raise RuntimeError(f"ffmpeg non avviabile ({exc}): l'audio nelle registrazioni ha bisogno di ffmpeg "
                               "sul nodo (lo installa  sudo ./node/install.sh)") from exc
        proc = self._proc
        self.errore = ""
        self.da = self.clock()
        log.info("Flusso con audio: pubblico su %s", self.destinazione)
        try:
            for pezzo in self.mic.stream(self._stop):
                if proc.poll() is not None:
                    break
                try:
                    proc.stdin.write(pezzo)
                    proc.stdin.flush()
                except (BrokenPipeError, ValueError, OSError):
                    break
        except MicError as exc:
            raise RuntimeError(f"microfono: {exc}") from exc
        finally:
            self.da = 0.0
        if proc.poll() not in (None, 0) and not self._stop.is_set():
            raise RuntimeError(self._perche_e_morto(proc))

    def _perche_e_morto(self, proc) -> str:
        err = b""
        try:
            err = proc.stderr.read() if proc.stderr else b""
        except Exception:  # noqa: BLE001
            pass
        detto = err.decode(errors="replace").strip().splitlines()
        return (detto[-1] if detto else f"ffmpeg terminato (codice {proc.returncode})")[:200]

    def _ammazza(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        for chiudi in (lambda: proc.stdin and proc.stdin.close(), proc.terminate):
            try:
                chiudi()
            except Exception:  # noqa: BLE001
                pass
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
