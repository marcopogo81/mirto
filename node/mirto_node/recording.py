# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Registrazione sul nodo, ad anello, indipendente da Mirto.

Registra MediaMTX stesso (``record`` nella configurazione del percorso della camera): spezzoni fMP4 da un minuto
in ``<cartella>/cam/AAAAMMGG-hhmmss.mp4``, con l'ora di inizio in **UTC** (MediaMTX gira con ``TZ=UTC0``), lo
stesso formato dei file di Mirto.

Questo modulo, dentro il servizio del nodo, ogni minuto:

* accende o spegne la registrazione di MediaMTX (sua API locale, ``127.0.0.1:9997``): se c'è un disco USB
  configurato ma non è collegato, la registrazione si ferma, così MediaMTX non riempie la microSD; quando il
  disco torna riparte da sola;
* tiene libero almeno ``minimo_libero_percento`` del disco e non supera ``massimo_gb``: cancella gli spezzoni
  più vecchi (mai quello che si sta scrivendo). Così la registrazione non si blocca mai per spazio.

Senza disco USB si registra sulla microSD con poco spazio (2 GB di serie, circa 3 ore a 1,5 Mbit/s).
"""

from __future__ import annotations

import calendar
import json
import logging
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

NAME_RE = re.compile(r"(\d{8})-(\d{6})\.mp4")
MAX_LIST = 5000  # elenco senza intervallo: al massimo gli ultimi 5000 spezzoni
API = "http://127.0.0.1:9997"
CAMERA_PATH = "cam"
SD_FOLDER = Path("/var/lib/mirto-node/registrazioni")
SD_DEFAULT_GB = 2.0
MAX_SEGMENT_SECONDS = 15 * 60

# «audio»: il microfono dentro il flusso registrato. **Spento di serie**, e per nodo. Non è una
# comodità mancante, è un cambio di politica: fino al 26/10 l'interfaccia e le due guide promettevano
# che l'audio non viene mai registrato. In Italia il Garante considera la registrazione audio nella
# videosorveglianza eccedente nella gran parte dei casi, con soglie molto più severe di quelle del
# video — quindi si accende con una scelta esplicita, mai da sé.
REC_DEFAULTS = {"attiva": True, "disco": "", "massimo_gb": 0, "minimo_libero_percento": 10,
                "audio": False}

# Il percorso del flusso che porta anche l'audio. Un percorso **a parte**: se il muxer cade si perde
# l'audio, non la camera.
AUDIO_PATH = CAMERA_PATH + "-audio"
RTSP_PORT = 8554          # la porta RTSP di MediaMTX: quella di serie, e il nodo non la cambia


def recording_cfg(cfg: dict) -> dict:
    out = dict(REC_DEFAULTS)
    out.update({k: v for k, v in (cfg.get("registrazione") or {}).items() if k in REC_DEFAULTS})
    return out


SEGNALI = Path("/etc/mirto-node")   # i biglietti per mirto-boot-segnali.service stanno qui


def rec_folder(rec: dict) -> Path:
    """Cartella delle registrazioni: sul disco USB se c'è (``disco`` = punto di montaggio), altrimenti microSD."""
    disk = str(rec.get("disco") or "").strip()
    return Path(disk) / "mirto-reg" if disk else SD_FOLDER


def parse_start(name: str) -> float | None:
    m = NAME_RE.fullmatch(name)
    if not m:
        return None
    try:
        return float(calendar.timegm(time.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")))
    except ValueError:
        return None


def list_segments(folder: Path, t0: float | None = None, t1: float | None = None) -> list[dict]:
    """Spezzoni registrati, in ordine: nome, inizio, fine (epoch), byte."""
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
        items.append([start, e.name, st.st_size, st.st_mtime])
    items.sort()
    out = []
    for i, (start, name, size, mtime) in enumerate(items):
        nxt = items[i + 1][0] if i + 1 < len(items) else None
        end = min(nxt, max(mtime, start)) if nxt is not None and nxt - start <= MAX_SEGMENT_SECONDS \
            else max(mtime, start)
        if t0 is not None and end < t0:
            continue
        out.append({"nome": name, "inizio": start, "fine": end, "byte": size})
    return out


class MediaMTXApi:
    """Accende e spegne la registrazione del percorso della camera con l'API locale di MediaMTX."""

    def __init__(self, base: str = API, path: str = CAMERA_PATH, timeout: float = 5,
                 credentials: tuple[str, str] | None = None):
        self.base, self.path, self.timeout = base, path, timeout
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # solo localhost, niente proxy
        self._headers = {}
        if credentials:
            import base64  # noqa: PLC0415

            token = base64.b64encode(f"{credentials[0]}:{credentials[1]}".encode()).decode()
            self._headers["Authorization"] = f"Basic {token}"

    last_status = 0  # codice HTTP dell'ultima risposta (401 = password dell'API sbagliata)

    def recording(self) -> bool | None:
        try:
            req = urllib.request.Request(f"{self.base}/v3/config/paths/get/{self.path}", headers=self._headers)
            with self._opener.open(req, timeout=self.timeout) as r:
                self.last_status = r.status
                return bool(json.loads(r.read()).get("record"))
        except urllib.error.HTTPError as exc:
            self.last_status = exc.code
            return None
        except (OSError, ValueError, urllib.error.URLError):
            self.last_status = 0
            return None

    def set_recording(self, on: bool) -> bool:
        req = urllib.request.Request(f"{self.base}/v3/config/paths/patch/{self.path}", method="PATCH",
                                     data=json.dumps({"record": bool(on)}).encode(),
                                     headers={"Content-Type": "application/json", **self._headers})
        try:
            with self._opener.open(req, timeout=self.timeout) as r:
                return 200 <= r.status < 300
        except (OSError, urllib.error.URLError):
            return False


def mount_source(punto: str) -> str:
    """Che dispositivo e' montato su quel punto, letto da ``/proc/mounts``. "" se non si sa.

    Fuori da Linux (le prove girano anche su Mac e Windows) quel file non c'e': si risponde "" e chi
    chiama tira dritto con gli altri controlli.
    """
    try:
        with open("/proc/mounts", encoding="utf-8", errors="replace") as f:
            for riga in f:
                pezzi = riga.split()
                if len(pezzi) > 1 and pezzi[1].replace("\\040", " ") == str(punto):
                    return pezzi[0]
    except OSError:
        pass
    return ""


class NodeRecorder:
    """Sorveglia la registrazione del nodo: disco presente, spazio, anello, stato per ``/stato``."""

    def __init__(self, rec: dict, api=None, interval: float = 60.0, clock=time.time, flusso=None):
        self.cfg = rec
        self.enabled = bool(rec.get("attiva", True))
        self.disk = str(rec.get("disco") or "").strip()
        self.root = rec_folder(rec)
        self.folder = self.root / CAMERA_PATH
        # Quale percorso si sta registrando. Con l'audio acceso e il muxer in piedi si registra il
        # flusso che porta anche il microfono; appena quello cade si torna al video, invece di
        # registrare un percorso che nessuno pubblica. La differenza fra «muto» e «niente» non e'
        # un dettaglio: la seconda e' una casa non sorvegliata.
        self.percorso = CAMERA_PATH
        self.flusso = flusso          # FlussoConAudio (flusso_audio.py) o None
        gb = float(rec.get("massimo_gb") or 0)
        self.max_bytes = int((gb or (0 if self.disk else SD_DEFAULT_GB)) * 1e9)
        self.min_free_pct = float(rec.get("minimo_libero_percento", 10))
        self.api = api or MediaMTXApi()
        self.interval = interval
        self.clock = clock
        self.error = ""
        self.recording = False
        self.deleted = 0
        self.segnali = SEGNALI   # dove lasciare i biglietti per il servizio da root
        self.last_check = 0.0
        self.usage: dict = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # --- ciclo ----------------------------------------------------------------------
    def start(self) -> "NodeRecorder":
        self._thread = threading.Thread(target=self._run, name="registrazione", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        """Arresto pulito del servizio: la registrazione si spegne (senza il nodo nessuno pulirebbe il disco)."""
        self._stop.set()
        try:
            self.api.set_recording(False)
        except Exception:  # noqa: BLE001
            pass

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.check()
            except Exception:  # noqa: BLE001
                log.exception("Controllo della registrazione non riuscito")
            self._stop.wait(self.interval)

    def disk_ok(self) -> bool:
        """C'e' un disco montato **e dall'altra parte c'e' ancora qualcosa**.

        ``ismount`` da solo non basta, ed e' il difetto trovato il 22/10 staccando la chiavetta da un
        nodo acceso: Linux non toglie il punto di mount, lo lascia li' puntato a un ``/dev/sda`` che
        non esiste piu'. ``lsblk`` non vedeva piu' niente, ``findmnt`` diceva ancora «montato», e il
        nodo - che guardava solo quello - non diceva niente: la riga in Mirto restava **verde**
        mentre la registrazione non scriveva piu' un byte. E' peggio di un errore: davanti a un
        errore si fa qualcosa, davanti al verde no.

        Si guarda il dispositivo: quando la chiavetta esce, il suo nodo in ``/dev`` sparisce subito,
        ed e' il segnale piu' netto che ci sia. ``statvfs`` resta come seconda rete, per i casi in
        cui il dispositivo c'e' ma il filesystem non risponde piu'.
        """
        if not self.disk:
            return True
        if not os.path.ismount(self.disk):
            return False
        sorgente = mount_source(self.disk)
        if sorgente.startswith("/dev/") and not os.path.exists(sorgente):
            return False
        # Il nodo gira solo su Linux, ma queste prove girano anche su Windows, dove statvfs non esiste:
        # senza questo controllo la suite moriva li' (CI del 22/10, AttributeError).
        chiedi_spazio = getattr(os, "statvfs", None)
        if chiedi_spazio is not None:
            try:
                chiedi_spazio(self.disk)
            except OSError:
                return False
        return True

    def check(self) -> None:
        """Un giro: disco, anello, accensione/spegnimento della registrazione di MediaMTX."""
        with self._lock:
            self.last_check = self.clock()
            self._segui_il_flusso()
            want, error = self.enabled, ""
            if not self.enabled:
                error = ""
            elif not self.disk_ok():
                want, error = False, f"disco {self.disk} non collegato: registrazione ferma"
                self._chiedi_di_rimontare()
            else:
                try:
                    self.prepare_folder()
                except OSError as exc:
                    want, error = False, f"cartella non scrivibile: {exc}"
            if want:
                full = self.cleanup()
                if full:
                    want, error = False, "disco pieno anche dopo aver cancellato il più vecchio"
            current = self.api.recording()
            if current is None:
                error = error or ("MediaMTX rifiuta la password dell'API (rilancia install.sh)"
                                  if getattr(self.api, "last_status", 0) == 401 else
                                  "MediaMTX non risponde (camera spenta?)")
            elif current != want:
                if self.api.set_recording(want):
                    log.info("Registrazione sul nodo %s", "accesa" if want else "fermata")
                    current = want
                else:
                    error = error or "MediaMTX non accetta il comando di registrazione"
            self.recording = bool(current)
            if error and error != self.error:
                log.warning("Registrazione: %s", error)
            self.error = error

    def prepare_folder(self) -> None:
        """Crea la cartella delle registrazioni, condivisa fra MediaMTX e il nodo.

        Ci scrive MediaMTX, ma a cancellare i file vecchi (l'anello) è il nodo, che dalla
        versione 0.7 gira senza privilegi: cancellare dipende dai permessi della **cartella**,
        non del file, quindi la cartella è del gruppo ``mediamtx`` con scrittura al gruppo e
        setgid, così anche i file nuovi nascono in quel gruppo. Se non si riesce a crearla —
        un disco appena collegato appartiene a root — si lascia un biglietto al servizio da root.
        """
        for d in (self.root, self.folder):
            if d.exists():
                continue
            try:
                d.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                log.warning("Cartella delle registrazioni %s non creata: %s", d, exc)
                self._chiedi_a_root()
                return
            try:
                shutil.chown(d, "mediamtx", "mediamtx")
            except (LookupError, OSError):  # utente assente (prove sul Mac) o file system senza proprietari
                pass
            try:
                os.chmod(d, 0o2775)
            except OSError:
                pass

    def _chiedi_di_rimontare(self) -> None:
        """Chiede a chi puo' di rimettere su il disco delle registrazioni.

        La riga di ``/etc/fstab`` monta all'avvio e basta: staccando e riattaccando il disco a
        macchina accesa non c'era **niente** che rifacesse il mount, e la registrazione restava ferma
        finche' qualcuno non riavviava il nodo - mentre il pannello e la diagnosi promettevano che
        sarebbe ripartita da se'. Trovato provandolo il 22/10.

        Il biglietto porta il punto di mount, e chi lo esegue accetta solo un punto che sta gia' in
        ``/etc/fstab``: un biglietto non deve poter far montare qualcosa che nessuno ha scelto.
        """
        if not self.disk:
            return
        try:
            (Path(self.segnali) / "disco-da-rimontare").write_text(f"{self.disk}\n")
        except OSError as exc:
            log.warning("Segnale per rimontare il disco non scritto: %s", exc)

    def _chiedi_a_root(self) -> None:
        """Chiede a mirto-boot-segnali.service (che gira da root) di preparare la cartella."""
        try:
            (Path(self.segnali) / "registrazioni-da-preparare").write_text(f"{self.folder}\n")
        except OSError as exc:
            log.warning("Segnale per la cartella delle registrazioni non scritto: %s", exc)

    def _segui_il_flusso(self) -> None:
        """Se il flusso con l'audio e' in piedi si registra quello; se cade, si torna al video.

        Cambiando percorso si **spegne prima** la registrazione su quello vecchio: MediaMTX
        continuerebbe a scrivere il flusso di prima, e il disco si riempirebbe del doppio senza che
        nessuno capisse perche'.
        """
        voluto = AUDIO_PATH if (self.flusso is not None and self.flusso.attivo()) else CAMERA_PATH
        if voluto == self.percorso:
            return
        try:
            self.api.set_recording(False)
        except Exception:  # noqa: BLE001
            pass
        self.percorso, self.folder = voluto, self.root / voluto
        if hasattr(self.api, "path"):
            self.api.path = voluto
        log.info("Registrazione: da qui in avanti su «%s» (%s)", voluto,
                 "col microfono" if voluto == AUDIO_PATH else "solo video")

    def cartelle(self) -> list[Path]:
        """Le cartelle che contengono registrazioni di questo nodo: quella in uso, e l'altra se c'e'.

        Due e non una perche' accendere (o spegnere) l'audio cambia il percorso registrato, e gli
        spezzoni di prima restano dove sono. «Le registrazioni gia' fatte restino leggibili — mute,
        ma leggibili» era la cosa che non doveva rompersi, scritta nel documento prima del codice: se
        questa funzione tornasse una cartella sola, mezzo archivio sparirebbe dalla finestra Rivedi,
        e l'anello non conterebbe piu' lo spazio che occupa.
        """
        dentro = [self.folder]
        altra = self.root / (CAMERA_PATH if self.folder.name == AUDIO_PATH else AUDIO_PATH)
        if altra.exists():
            dentro.append(altra)
        return dentro

    def cleanup(self) -> bool:
        """Anello: cancella dal più vecchio finché c'è spazio. Restituisce True se il disco resta pieno."""
        cartelle = self.cartelle()
        coppie = sorted(((s["inizio"], d, s) for d in cartelle for s in list_segments(d)),
                        key=lambda x: x[0])
        # L'ultimo della cartella in uso e' quello che MediaMTX sta scrivendo: quello non si tocca.
        ultimo = next((s["nome"] for _i, d, s in reversed(coppie) if d == self.folder), "")
        segs = [s for _i, _d, s in coppie]
        used = sum(s["byte"] for s in segs)
        try:
            du = shutil.disk_usage(self.folder)
        except OSError:
            self.usage = {"usato": used}
            return False  # spazio non misurabile: non si cancella niente
        free, total = du.free, du.total
        min_free = total * self.min_free_pct / 100
        removed = 0
        for _inizio, dove, s in coppie:
            if used <= (self.max_bytes or float("inf")) and free >= min_free:
                break
            if dove == self.folder and s["nome"] == ultimo:
                continue
            try:
                (dove / s["nome"]).unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                log.warning("Impossibile cancellare %s: %s", s["nome"], exc)
                continue
            used -= s["byte"]
            free += s["byte"]
            removed += 1
        if removed:
            self.deleted += removed
            # A regime l'anello cancella uno spezzone al minuto, per sempre. A INFO quella riga riempiva
            # il registro e ci annegava dentro tutto il resto: il 22/10, in «ultimi errori del servizio»,
            # non si leggeva altro che questa frase ripetuta trenta volte, e non c'era modo di sapere se
            # ci fossero stati errori. Il caso normale va a DEBUG; resta a INFO quando ne cancella più
            # d'uno in un colpo, che non è normale e vuol dire che qualcosa si è accumulato — disco
            # riempito da altri, nodo rimasto fermo, tetto appena abbassato.
            parla = log.info if removed > 1 else log.debug
            parla("Registrazione: cancellati %d spezzoni vecchi (anello)", removed)
        rest = sorted((s for d in cartelle for s in list_segments(d)), key=lambda s: s["inizio"])
        self.usage = {"usato": used, "libero": free, "totale": total,
                      "dal": rest[0]["inizio"] if rest else None, "limite": self.max_bytes}
        return free < min_free * 0.5  # nemmeno la metà del minimo: qualcos'altro ha riempito il disco

    # --- per l'API del nodo ------------------------------------------------------------
    def status(self) -> dict:
        return {"attiva": self.enabled, "registra": self.recording, "errore": self.error,
                "disco": self.disk or "microSD", "cartella": str(self.folder),
                "percorso": self.percorso, **self.usage}

    def segments(self, t0: float | None = None, t1: float | None = None) -> list[dict]:
        """Gli spezzoni, da **tutte** le cartelle di questo nodo e in ordine di tempo.

        Accendere l'audio cambia il percorso registrato: da lì in avanti i file nascono altrove. Chi
        riguarda un pomeriggio non deve accorgersene — e soprattutto non deve trovare metà archivio
        sparito perché quel giorno qualcuno ha spuntato una casella.
        """
        cartelle = self.cartelle()
        segs = sorted((s for d in cartelle for s in list_segments(d, t0, t1)), key=lambda s: s["inizio"])
        return segs[-MAX_LIST:] if t0 is None and t1 is None else segs

    def file(self, name: str) -> Path | None:
        """Percorso di uno spezzone dal nome (solo nomi validi: niente percorsi fuori dalle cartelle)."""
        if not NAME_RE.fullmatch(name or ""):
            return None
        for d in self.cartelle():
            p = d / name
            if p.is_file():
                return p
        return None
