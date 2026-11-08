# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Nucleo di Mirto: collega camere, analisi, archivio, notifiche e azioni.

Usato sia dall'interfaccia grafica sia dalla modalità senza interfaccia.
"""

from __future__ import annotations

import logging
import signal
import threading
from pathlib import Path
import time
from typing import Callable

import cv2

from .cameras.manager import CameraManager
from .cameras.source import STATUS_OFFLINE, STATUS_ONLINE, CameraSource
from .config import Config, data_dir
from .credentials import SecretStore
from .events import Event, EventStore
from .notify.dispatcher import NotificationDispatcher
from .notify.telegram import BotHandlers
from .i18n import _

log = logging.getLogger(__name__)

OFFLINE_ALERT_SECONDS = 120  # avviso se una camera resta non raggiungibile per 2 minuti

# Versione del messaggio «immagine troppo scura». Si alza **quando il testo cambia**: chi l'aveva
# già sentito lo risente una volta, e una volta sola. Senza, un avviso che si dice una volta per
# sempre resta per sempre quello che era — anche se era sbagliato, come la versione 1.
AVVISO_BUIO = 2


class MirtoCore:
    def __init__(self, config: Config | None = None, secrets: SecretStore | None = None,
                 detector=None, dispatcher_kwargs: dict | None = None, **manager_kwargs):
        self.config = config or Config()
        self.secrets = secrets or SecretStore()
        # configurazioni di prima di questa versione: credenziali delle camere fuori dal file e nel portachiavi
        from .netutil import move_camera_credentials  # noqa: PLC0415

        moved = move_camera_credentials(self.config, self.secrets)
        if moved:
            log.info("Credenziali di %d camer%s spostate nel portachiavi", moved, "a" if moved == 1 else "e")
        manager_kwargs.setdefault("secrets", self.secrets)
        self.cameras = CameraManager(self.config, **manager_kwargs)
        self.store = EventStore(data_dir(), retention_days=int(self.config.get("general.retention_days", 30)))
        self.detector = detector
        self.pipelines: dict = {}
        # Pause degli avvisi salvate in configurazione: restano anche dopo un riavvio
        now = time.time()
        self.paused_until = float(self.config.get("general.paused_until", 0) or 0)
        if self.paused_until < now:
            self.paused_until = 0.0
        self.camera_paused: dict[str, float] = {
            k: float(v) for k, v in (self.config.get("general.camera_paused") or {}).items() if float(v) > now
        }
        self.actions = None  # ActionEngine, se configurato (vedi enable_actions)
        self.handlers = CoreHandlers(self)
        from .schedule import Scheduler  # noqa: PLC0415

        self.scheduler = Scheduler(self)
        from .presence import PresenceMonitor  # noqa: PLC0415

        self.presence = PresenceMonitor(self.config, self.set_armed,
                                        lambda: bool(self.config.get("actions.armed", False)))
        self.notifier = NotificationDispatcher(self.config, self.secrets, self.store, self.handlers,
                                               **(dispatcher_kwargs or {}))
        from .recording.manager import RecordingManager  # noqa: PLC0415

        self.recording = RecordingManager(self.config, self.cameras, data_dir(), notify=self.notifier.notify_text)
        self.recording.on_clip.append(self._clip_ready)
        self.recording.node_client_for = self._node_client_for
        from .telemetry import Telemetry  # noqa: PLC0415

        self.telemetry = Telemetry(self.config, self.telemetry_stats, on_update=self._new_version)
        self.on_update: list = []  # callback(versione) quando esce una versione nuova
        self._lock = threading.RLock()
        self._started = False
        self.started_at = 0.0
        self._stop = threading.Event()
        self._maint_thread: threading.Thread | None = None
        self._offline_since: dict[str, float] = {}
        self._offline_alerted: set[str] = set()
        self._waiting: dict[str, CameraSource] = {}   # camere in attesa del rilevatore
        self._loader: threading.Thread | None = None
        self.audio = None                              # AudioMonitor (ascolto dei microfoni), creato all'avvio
        self._node_mics: dict[str, object] = {}        # nodo -> NodeMicSource
        self._audio_lock = threading.RLock()
        self.species = None                            # classificatore delle specie degli uccelli
        self._species_state = ""                       # "", "loading", "ready", "error: …"
        self._species_retry_at = 0.0
        # Ascoltatori esterni (es. interfaccia)
        self.on_event: list[Callable[[Event], None]] = []
        self.on_camera_state: list[Callable[[str, dict], None]] = []
        self.on_message: list[Callable[[str], None]] = []
        self.on_clip: list[Callable[[str, object], None]] = []   # (id evento, file della clip)
        self.cameras.on_source_added.append(self._source_added)
        self.cameras.on_source_removed.append(self._source_removed)
        self.cameras.on_status.append(self._camera_status)

    # --- rilevatore (caricato in background, con nuovi tentativi) ----------
    def _start_loader(self) -> None:
        with self._lock:
            if self._loader and self._loader.is_alive():
                return
            self._loader = threading.Thread(target=self._load_detector, name="carica-modello", daemon=True)
            self._loader.start()

    def _load_detector(self) -> None:
        from .detection.models import ensure_model  # noqa: PLC0415
        from .detection.person import PersonDetector  # noqa: PLC0415

        while not self._stop.is_set():
            try:
                path, spec = ensure_model(self.config.get("detection.model", "yolox_nano"))
                detector = PersonDetector(path, spec.input_size)
                break
            except Exception as exc:
                log.error("Rilevatore persone non disponibile: %s", exc)
                self._message(_('Rilevatore persone non disponibile ({exc}): nuovo tentativo tra 5 minuti.', exc=exc))
                if self._stop.wait(300):
                    return
        else:
            return
        with self._lock:
            self.detector = detector
            waiting = list(self._waiting.values())
            self._waiting.clear()
        self._message(_('Rilevatore persone pronto.'))
        for src in waiting:
            if self.cameras.sources.get(src.info.id) is src:
                self._create_pipeline(src)

    # --- specie degli uccelli (secondo modello, caricato solo se serve) -----
    def species_classifier(self):
        """Classificatore pronto, oppure None (e in quel caso ne avvia il caricamento in background)."""
        from .detection.pipeline import needs_species  # noqa: PLC0415

        if self.species is not None:
            return self.species
        if not needs_species(self.config):
            return None
        with self._lock:
            if self._species_state == "loading" or time.time() < self._species_retry_at:
                return None
            self._species_state = "loading"
        threading.Thread(target=self._load_species, name="carica-specie", daemon=True).start()
        return None

    def _load_species(self) -> None:
        from .detection.models import ensure_asset  # noqa: PLC0415
        from .detection.species import SpeciesClassifier  # noqa: PLC0415

        try:
            clf = SpeciesClassifier(ensure_asset("birds_model"), ensure_asset("birds_labels"))
        except Exception as exc:  # noqa: BLE001
            log.error("Classificatore delle specie non disponibile: %s", exc)
            with self._lock:
                self._species_state = f"error: {exc}"
                self._species_retry_at = time.time() + 1800
            self._message(_('Riconoscimento delle specie di uccelli non disponibile ({exc}): '
                            'gli uccelli restano "Uccello". Nuovo tentativo tra 30 minuti.', exc=exc))
            return
        with self._lock:
            self.species = clf
            self._species_state = "ready"
        self._message(_('Riconoscimento delle specie di uccelli pronto.'))

    # --- sorgenti → analisi -----------------------------------------------
    def _source_added(self, source: CameraSource) -> None:
        self.recording.poke()
        with self._lock:
            if self.detector is None:
                self._waiting[source.info.id] = source
                start = True
            else:
                start = False
        if start:
            self._start_loader()
        else:
            self._create_pipeline(source)

    def _create_pipeline(self, source: CameraSource) -> None:
        from .detection.pipeline import CameraPipeline  # noqa: PLC0415

        pipe = CameraPipeline(source, self.detector, self.config, on_event=self.handle_event,
                              on_action=self.handle_action, on_state=self._camera_state,
                              on_detect=self.handle_detection, species=self.species_classifier)
        pipe.paused_until = max(self.paused_until, self.camera_paused.get(source.info.id, 0.0))
        with self._lock:
            old = self.pipelines.pop(source.info.id, None)
            self.pipelines[source.info.id] = pipe
        if old:
            old.stop()
        pipe.start()

    def _source_removed(self, cam_id: str) -> None:
        self.recording.poke()
        with self._lock:
            self._waiting.pop(cam_id, None)
            pipe = self.pipelines.pop(cam_id, None)
        self._offline_since.pop(cam_id, None)
        self._offline_alerted.discard(cam_id)
        if pipe:
            # come per la camera: si chiede lo stop e si aspetta in sottofondo, mai sul thread della finestra
            pipe.request_stop()
            self.cameras.reap(lambda p=pipe: p.join(timeout=5.0))

    def _camera_status(self, cam_id: str, status: str) -> None:
        if status == STATUS_OFFLINE:
            self._offline_since.setdefault(cam_id, time.time())
        elif status == STATUS_ONLINE:
            self._offline_since.pop(cam_id, None)
            if cam_id in self._offline_alerted:
                self._offline_alerted.discard(cam_id)
                self.notifier.notify_text(_('✅ Camera {camera_name} di nuovo in linea', camera_name=self.camera_name(cam_id)))

    def _camera_state(self, cam_id: str, change: dict) -> None:
        if change.get("dark") is True:
            self._buio_una_volta(cam_id)
        for cb in self.on_camera_state:
            cb(cam_id, change)

    def _buio_una_volta(self, cam_id: str) -> None:
        """«Immagine troppo scura», **una volta per camera**, con il rimedio vero.

        Marco, 25/10: *«la camera del Raspberry 3B non è NoIR, quindi non ha visione notturna»*. La
        frase era vera e tornava ogni notte, per sempre, su una camera che non può risolverla: un
        avviso che non si può chiudere e che si ripete insegna a ignorare gli avvisi, e il prossimo
        che conta passa inosservato con gli altri.

        Non si azzera quando torna chiaro: tornerebbe ogni sera, che è esattamente il difetto. Il
        rimedio è fisico — una luce, o dei faretti a infrarossi — quindi è una cosa da sapere una
        volta, non uno stato da sorvegliare. Il contrassegno sulla piastrella, quello sì, resta
        acceso finché l'immagine è scura: quello è lo stato di adesso, e non parla a nessuno.

        «Una volta sola» però vale per **questo** testo, non per sempre: accanto al nome della camera
        si segna `AVVISO_BUIO`. Serviva: fino all'8/11 la frase diceva di procurarsi «un modulo NoIR
        con illuminatore» — cioè, a chi la NoIR ce l'ha già, di ricomprare quello che ha, e a chi ha
        una webcam USB una cosa che per lei non esiste. Detta una volta per sempre, sarebbe rimasta
        sbagliata per sempre, senza nessun modo di rimediare se non azzerare la configurazione.
        """
        segno = f"{cam_id}#{AVVISO_BUIO}"
        dette = list(self.config.get("cameras.dark_told") or [])
        if segno in dette:
            return
        self._message(_('Immagine troppo scura su {camera_name}: al buio questa camera non vede, e '
                        'non è un\'impostazione da cambiare. Serve una luce dove guarda; se la '
                        'camera è il modello NoIR servono invece faretti a infrarossi, che è il modo '
                        'in cui quelle camere vedono al buio. Lo dico una volta sola.',
                        camera_name=self.camera_name(cam_id)))
        # Via le versioni vecchie della stessa camera: l'elenco non deve crescere a ogni ritocco.
        dette = [d for d in dette if d != cam_id and not d.startswith(f"{cam_id}#")]
        self.config.set("cameras.dark_told", dette + [segno])
        try:
            self.config.save()
        except Exception:
            log.exception("Salvataggio di «avviso del buio già dato» fallito")

    def camera_name(self, cam_id: str) -> str:
        src = self.cameras.sources.get(cam_id)
        return src.info.name if src else cam_id

    def _message(self, text: str) -> None:
        log.info(text)
        for cb in self.on_message:
            try:
                cb(text)
            except Exception:
                log.exception("Errore nel callback messaggi")

    # --- eventi -----------------------------------------------------------
    def handle_event(self, ev: Event) -> None:
        """Evento confermato (soglia notifiche): archivio + notifiche."""
        try:
            self.store.save(ev)
            self.recording.request_clip(ev)
        except Exception:
            log.exception("Salvataggio evento fallito")
        is_person = getattr(ev, "label", "person") == "person"
        extra = self.actions.confirmation_buttons(ev) if self.actions and is_person else []
        self.notifier.notify_event(ev, extra)
        for cb in self.on_event:
            try:
                cb(ev)
            except Exception:
                log.exception("Errore nel callback eventi")

    def _node_client_for(self, cam_id: str):
        """Client del nodo che trasmette la camera ``cam_id`` (per le registrazioni fatte sul nodo), o None."""
        if self.actions is None:
            return None
        for net in self.config.get("cameras.network") or []:
            if f"net:{net.get('id')}" == cam_id and net.get("node"):
                return self.actions.clients.get(str(net["node"]))
        return None

    def _clip_ready(self, event_id: str, path) -> None:
        """Clip di un evento pronta: su Telegram come risposta alla foto, e all'interfaccia (Archivio)."""
        row = self.store.get(event_id)
        if row is None:  # evento cancellato mentre la clip si preparava: niente file orfani
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass
            return
        when = time.strftime("%H:%M:%S", time.localtime(row.get("ts") or time.time()))
        self.notifier.notify_clip(event_id, path, _("🎞 {camera} – {when}", camera=row.get("camera_name", ""),
                                                    when=when))
        for cb in self.on_clip:
            try:
                cb(event_id, path)
            except Exception:
                log.exception("Errore nel callback clip")

    # --- suoni ----------------------------------------------------------------
    def _sound_classifier(self):
        from .audio.classifier import SoundClassifier  # noqa: PLC0415
        from .detection.models import ensure_asset  # noqa: PLC0415

        try:
            clf = SoundClassifier(ensure_asset("yamnet_model"), ensure_asset("yamnet_labels"))
        except Exception as exc:  # noqa: BLE001
            log.error("Riconoscimento dei suoni non disponibile: %s", exc)
            self._message(_('Riconoscimento dei suoni non disponibile ({exc}).', exc=exc))
            raise
        self._message(_('Riconoscimento dei suoni pronto.'))
        return clf

    def _sound_snapshot(self, cam_id: str):
        src = self.cameras.sources.get(cam_id)
        if src is None:
            return None
        frame, _ts, _seq = src.latest()
        return None if frame is None else frame.copy()

    def mac_mic_camera(self) -> str:
        """Camera da fotografare quando il microfono del Mac sente qualcosa."""
        chosen = self.config.get("audio.mac_camera", "")
        if chosen and chosen in self.cameras.sources:
            return chosen
        srcs = list(self.cameras.sources.values())
        local = [s for s in srcs if not str(s.info.id).startswith("net:")]
        pick = local or srcs
        return pick[0].info.id if pick else ""

    def ensure_audio(self):
        """Crea (una volta) il monitor dei suoni."""
        with self._audio_lock:
            return self._ensure_audio()

    def _ensure_audio(self):
        if self.audio is None:
            from .audio.monitor import AudioMonitor  # noqa: PLC0415

            self.audio = AudioMonitor(self.config, self._sound_classifier, self.handle_sound_event,
                                      snapshot=self._sound_snapshot)
        return self.audio

    def sync_audio_sources(self) -> None:
        """Microfoni dei nodi da ascoltare, secondo la configurazione e ciò che i nodi dichiarano."""
        with self._audio_lock:
            self._sync_audio_sources()

    def _sync_audio_sources(self) -> None:
        from .audio.monitor import MicSource, NodeMicSource  # noqa: PLC0415

        from .actions.pannello_audio import ascolta_microfono  # noqa: PLC0415

        wanted = {}
        if self.actions:
            scelte = self.config.get("audio.node_mics", True)
            for nd in self.config.get("actions.nodes") or []:
                nid = str(nd["id"])
                meta = self.actions.node_meta.get(nid) or {}
                # Due condizioni, due posti: il nodo deve avere il microfono acceso (Nodi → Audio del
                # nodo) e Mirto deve volerlo ascoltare — scelta che da oggi è **di quel nodo**, e sta
                # accanto al suo microfono invece che in «Suoni» per tutti (24/10).
                if ((meta.get("audio") or {}).get("microfono") and nid in self.actions.clients
                        and ascolta_microfono(scelte, nid)):
                    wanted[nid] = nd
        for nid in list(self._node_mics):
            # nodo non più da ascoltare, oppure riabbinato (chiave o indirizzo nuovi): si riparte da capo
            if nid not in wanted or self._node_mics[nid].client is not self.actions.clients.get(nid):
                self._node_mics.pop(nid).stop()
                if self.audio:
                    self.audio.remove_source(f"node:{nid}")
        for nid, nd in wanted.items():
            if nid in self._node_mics:
                continue
            mon = self._ensure_audio()
            cam = next((c["id"] for c in self.config.get("cameras.network") or [] if str(c.get("node")) == nid), "")
            mon.add_source(MicSource(f"node:{nid}", _("{name} (microfono)", name=nd.get("name") or nid),
                                     camera_id=f"net:{cam}" if cam else ""))
            self._node_mics[nid] = NodeMicSource(self.actions.clients[nid], mon, f"node:{nid}").start()

    def handle_sound_event(self, ev: Event) -> None:
        """Suono confermato: archivio e avviso (se è tra i suoni da segnalare), dissuasori, azioni."""
        kind = ev.label[len("sound:"):]
        always = kind in (self.config.get("audio.always_notify") or [])
        if self.actions and self.actions.recently_active() and not always:
            # sirena e dissuasori fanno rumore (spari, gracchi, allarmi): il sistema non deve "sentire sé stesso"
            log.info("Suono %s ignorato: un nodo sta suonando", kind)
            return
        now = time.time()
        muted = now < self.paused_until or now < self.camera_paused.get(ev.camera_id, 0.0)
        if (kind in (self.config.get("audio.watch") or []) and not muted) or always:
            try:
                self.store.save(ev)
                self.recording.request_clip(ev)  # clip della camera associata al microfono, se registra
            except Exception:
                log.exception("Salvataggio evento fallito")
            extra = self.actions.confirmation_buttons(ev) if self.actions else []
            self.notifier.notify_event(ev, extra, force=always)
            for cb in self.on_event:
                try:
                    cb(ev)
                except Exception:
                    log.exception("Errore nel callback eventi")
        if self.actions:
            try:
                # Una chiamata sola: dal 23/10 il motore ha una strada sola, e dentro decide chi
                # scatta. Prima qui se ne chiamavano due — i dissuasori e poi, se serviva, le azioni
                # antintrusione — e a non far partire la stessa azione due volte era il filtro sul
                # tipo. Tolto quello, due chiamate vorrebbero dire due avvisi per lo stesso suono.
                self.actions.on_detection(ev)
            except Exception:
                log.exception("Errore nelle azioni per un suono")

    def handle_detection(self, ev: Event) -> None:
        """Animale, uccello o suono confermato: dissuasori (indipendenti da protezione e silenzio)."""
        if self.actions:
            try:
                self.actions.on_detection(ev)
            except Exception:
                log.exception("Errore nei dissuasori")

    def handle_action(self, ev: Event) -> None:
        """Conferma "forte" (soglia azioni): avvia la sequenza sui nodi, se inserito."""
        if self.actions:
            self.actions.on_intrusion(ev)

    # --- prove (menu Prove dell'interfaccia) -------------------------------
    def send_test_alert(self) -> str:
        """Invia un avviso di PROVA completo (Telegram + email + SMS) con la foto dal vivo."""
        import numpy as np  # noqa: PLC0415

        from .detection.person import annotate  # noqa: PLC0415

        srcs = list(self.cameras.sources.values())
        frame, name, cam_id = None, "Prova", "prova"
        for s in srcs:
            f, _ts, _seq = s.latest()
            if f is not None:
                frame, name, cam_id = f, s.info.name, s.info.id
                break
        if frame is None:
            frame = np.full((480, 640, 3), 60, np.uint8)
        ev = Event(camera_id=cam_id, camera_name=name, score=1.0, image=frame, is_test=True)
        ev.image = annotate(frame, [], caption=f"PROVA - {name}  {ev.when_text()}")
        self.notifier.notify_event(ev, force=True)
        return _('Avviso di prova in invio su Telegram, email ed eventuali SMS (anche con la protezione disattivata).')

    def snapshot_camera(self, cam_id: str, folder):
        """Salva una foto della camera ``cam_id``; restituisce il file o None se non c'è immagine."""
        from pathlib import Path  # noqa: PLC0415

        src = self.cameras.sources.get(cam_id)
        if src is None:
            return None
        frame, _ts, _seq = src.latest()
        if frame is None:
            return None
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        safe = "".join(ch if ch.isalnum() else "_" for ch in src.info.name)[:40]
        path = folder / f"foto_{safe}_{time.strftime('%Y%m%d-%H%M%S')}.jpg"
        cv2.imwrite(str(path), frame)
        return path

    def stop_all_visible(self) -> bool:
        """Il pulsante "Ferma tutto" serve? **Quando c'è qualcosa da fermare.** Punto.

        Fino al 25/10 c'era anche «e la protezione è attiva», e veniva da quando un'azione partiva
        solo a protezione inserita. Ma i dissuasori partono **sempre**, per scelta, e l'allarme fumo
        pure: quindi poteva suonare qualcosa in giardino e il pulsante per fermarlo non c'era.
        Marco, provando: *«quando un evento scatta senza protezione attiva non si riesce a fare il
        ferma tutto»*.

        È il difetto peggiore di tutta la serata, e non perché sia complicato: un comando di
        emergenza che sparisce proprio nel caso in cui serve è peggio che non averlo — chi lo cerca
        e non lo trova non conclude «non serve», conclude «non funziona niente».
        """
        return bool(self.actions and self.actions.has_activity())

    def accendi_o_spegni_azione(self, nome: str) -> str:
        """Accende o spegne un'azione **per nome**, e lo salva. Torna il nome dello stato nuovo.

        Vive qui e non nella finestra perché non è una cosa dell'interfaccia: è una decisione sul
        programma, e chiunque può volerla chiedere — la colonna *Sistema*, domani un comando di
        Telegram. Vuota se quel nome non esiste: chi chiama lo dice a modo suo.
        """
        voci = list(self.config.get("actions.items") or [])
        for a in voci:
            if str(a.get("name") or "") != str(nome):
                continue
            a["enabled"] = not a.get("enabled", True)
            self.config.set("actions.items", voci)
            try:
                self.config.save()
            except Exception:
                log.exception("Salvataggio dell'azione %s fallito", nome)
            if self.actions:
                self.actions.reload()
            return "accesa" if a["enabled"] else "spenta"
        return ""

    def prank(self, node_id: str, minuti: float) -> str:
        """Accende o spegne la modalità Prank su un nodo. Vuota se non c'è nessun motore delle azioni.

        Sta qui per la stessa ragione di ``accendi_o_spegni_azione``: non è una cosa
        dell'interfaccia, è una decisione sul programma, e la chiederanno più posti di uno — la
        colonna *Sistema*, la scheda del nodo, domani un comando di Telegram.
        """
        if not self.actions:
            return ""
        return self.actions.prank(str(node_id), float(minuti))

    def volume_cassa(self, node_id: str, percento: int) -> str:
        """Il volume della cassa di un nodo. Vuota se non c'è nessun motore delle azioni."""
        if not self.actions:
            return ""
        return self.actions.volume(str(node_id), int(percento))

    def snapshot_all(self, folder) -> list:
        """Salva una foto da ogni camera attiva nella cartella indicata. Restituisce i file creati."""
        from pathlib import Path  # noqa: PLC0415

        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        out = []
        stamp = time.strftime("%Y%m%d-%H%M%S")
        for s in list(self.cameras.sources.values()):
            f, _ts, _seq = s.latest()
            if f is None:
                continue
            safe = "".join(ch if ch.isalnum() else "_" for ch in s.info.name)[:40]
            path = folder / f"foto_{safe}_{stamp}.jpg"
            cv2.imwrite(str(path), f)
            out.append(path)
        return out

    def pause(self, seconds: float, cam_id: str | None = None) -> None:
        """Silenzia gli avvisi di una camera (o di tutte) per ``seconds`` (0 = riattiva)."""
        until = time.time() + seconds if seconds > 0 else 0.0
        with self._lock:
            if cam_id is None:
                self.paused_until = until
                if not until:
                    self.camera_paused.clear()  # "Riattiva" riattiva tutto, anche le singole camere
                pipes = list(self.pipelines.values())
            else:
                if until:
                    self.camera_paused[cam_id] = until
                else:
                    self.camera_paused.pop(cam_id, None)
                pipes = [self.pipelines[cam_id]] if cam_id in self.pipelines else []
        for p in pipes:
            p.paused_until = max(self.paused_until, self.camera_paused.get(p.source.info.id, 0.0))
        self.config.set("general.paused_until", self.paused_until)
        self.config.set("general.camera_paused", dict(self.camera_paused))
        try:
            self.config.save()
        except Exception:
            log.exception("Salvataggio della pausa fallito")

    def set_armed(self, armed: bool, source: str = "") -> str:
        """Inserisce/disinserisce (anche senza nodi configurati) e lo comunica su Telegram."""
        if self.actions:
            msg = self.actions.set_armed(armed)
        else:
            self.config.set("actions.armed", bool(armed))
            self.config.save()
            msg = _('🔒 Protezione ATTIVA.') if armed else _('🔓 Protezione DISATTIVATA.')
        self.recording.poke()  # camere che registrano solo con la protezione attiva
        if source:
            # avviso inviato anche quando si disinserisce (notify_text verrebbe bloccato)
            icon = "👥" if source.startswith(("presenza", "presence")) else "⏰"
            self.notifier.send_text_now(f"{icon} {msg}\n" + _("(automatico: {source})", source=source))
        return msg

    def startup_state(self) -> dict:
        """Cosa è ancora in avvio: per mostrare "⏳ Avvio in corso" invece di falsi errori."""
        bot = self.notifier.bot
        recent = (time.time() - self.started_at) < 15 if self.started_at else True
        srcs = list(self.cameras.sources.values())
        return {
            "core": not self._started,
            "telegram": (bot is not None and getattr(bot, "state", "online") == "starting")
                        or (bot is None and not self._started),
            "detector": self.detector is None and (bool(srcs) or not self._started),
            "cameras": (not srcs and recent) or any(s.status == "connecting" for s in srcs),
        }

    def schedule_line(self) -> str:
        """Una riga sul prossimo cambio programmato (per /stato e per l'interfaccia)."""
        from datetime import datetime  # noqa: PLC0415

        from .schedule import action_word, describe_when  # noqa: PLC0415

        if not self.config.get("schedule.rules"):
            return _('⏰ Nessuna programmazione')
        if not self.scheduler.enabled:
            return _('⏰ Programmazione sospesa')
        nxt = self.scheduler.next()
        if not nxt:
            return _('⏰ Nessun cambio programmato in arrivo')
        t, r = nxt
        return "⏰ " + _("Prossimo: {when} → {what} ({name})", when=describe_when(t, datetime.now()),
                         what=action_word(r.azione), name=r.nome)

    def alert_state(self) -> dict:
        """Stato degli avvisi in un colpo d'occhio (usato dall'interfaccia e da /stato).

        ``mode``: "disarmed" (nessun avviso), "paused" (inserito ma avvisi sospesi), "active".
        """
        now = time.time()
        armed = bool(self.config.get("actions.armed", False))
        paused = self.paused_until if self.paused_until > now else 0.0
        cams = {k: v for k, v in self.camera_paused.items() if v > now}
        mode = "disarmed" if not armed else "paused" if paused else "active"
        return {"mode": mode, "armed": armed, "paused_until": paused, "camera_paused": cams,
                "last_sent": self.notifier.last_sent}

    # --- ciclo di vita ----------------------------------------------------
    def _maintenance(self) -> None:
        last_purge = 0.0
        last_audio_sync = time.time() - 45  # primo controllo dei microfoni dopo ~15 s (nodi già letti)
        while not self._stop.wait(15):
            now = time.time()
            for cam_id, since in list(self._offline_since.items()):
                if now - since >= OFFLINE_ALERT_SECONDS and cam_id not in self._offline_alerted:
                    self._offline_alerted.add(cam_id)
                    self.notifier.notify_text(_('⚠️ Camera {camera_name} non raggiungibile da 2 minuti', camera_name=self.camera_name(cam_id)))
            if now - last_audio_sync >= 60:
                last_audio_sync = now
                try:
                    self.sync_audio_sources()
                except Exception:
                    log.exception("Aggiornamento dei microfoni fallito")
            if now - last_purge >= 3600:
                last_purge = now
                try:
                    self.store.purge()
                except Exception:
                    log.exception("Pulizia archivio fallita")

    # --- statistiche anonime (vedi telemetry.py) -----------------------------------------------
    def telemetry_stats(self) -> dict:
        """Solo conteggi e interruttori, niente nomi né indirizzi: telemetry.payload li mette in fasce."""
        c = self.config
        n_cams = len(dict(getattr(self.cameras, "sources", {}) or {}))
        modes = set((c.get("recording.cameras") or {}).values()) | {c.get("recording.mode", "events")}
        if not c.get("recording.enabled", True):
            rec = "off"
        else:
            rec = modes.pop() if len(modes) == 1 else "mixed"
        actions = c.get("actions.items") or []
        return {
            "cameras": n_cams,
            "nodes": len(c.get("actions.nodes") or []),
            "recording": rec,
            "features": {
                "telegram": c.get("notifications.telegram.enabled", False),
                "email": c.get("notifications.email.enabled", False),
                "sms": c.get("notifications.sms.enabled", False),
                "presence": c.get("presence.enabled", False),
                "schedule": bool(c.get("schedule.rules")),
                "mic": c.get("audio.mac_mic", False),
                "actions": bool(actions),
                "deterrent": any(str(a.get("kind", "")).lower() == "deterrent" for a in actions if isinstance(a, dict)),
                "watch_more": (c.get("detection.watch") or ["person"]) != ["person"],
            },
        }

    def _new_version(self, version: str) -> bool:
        """Mostra l'avviso di nuova versione; False se nessuno l'ha potuto mostrare (senza finestra)."""
        log.info("Disponibile Mirto %s", version)
        shown = False
        for cb in list(self.on_update):
            try:
                cb(version)
                shown = True
            except Exception:  # noqa: BLE001
                log.exception("Avviso di nuova versione non riuscito")
        return shown

    def start(self, threaded_discovery: bool = True) -> None:
        if self._started:
            return
        self._started = True
        self.started_at = time.time()
        self._stop.clear()
        self.notifier.start()
        if self.actions:
            self.actions.start()
        self._maint_thread = threading.Thread(target=self._maintenance, name="manutenzione", daemon=True)
        self._maint_thread.start()
        self.scheduler.start()
        self.presence.start()
        self.cameras.start(threaded_discovery=threaded_discovery)
        self.recording.start()
        self.telemetry.start()
        if self.config.get("general.prevent_sleep", True):
            from .system.power import prevent_sleep  # noqa: PLC0415

            prevent_sleep(True)
        log.info("Mirto avviato")

    def stop(self) -> None:
        if not self._started:
            self.store.close()  # mai avviato: resta solo l'archivio da chiudere
            return
        self._stop.set()
        self.telemetry.stop()
        self.scheduler.stop()
        self.presence.stop()
        with self._lock:
            pipes = list(self.pipelines.values())
            self.pipelines.clear()
        for p in pipes:
            p.request_stop()
        self.recording.stop()
        self.cameras.stop()
        for p in pipes:
            p.join(timeout=1.0)
        for mic in self._node_mics.values():
            mic.stop()
        self._node_mics.clear()
        if self.audio:
            self.audio.stop()
            self.audio = None
        if self.actions:
            self.actions.stop()
        self.notifier.stop()
        from .system.power import prevent_sleep  # noqa: PLC0415

        prevent_sleep(False)
        self.store.close()  # chiude l'archivio SQLite: senza, su Windows il file resta bloccato
        self._started = False
        log.info("Mirto fermato")

    def reload_config(self) -> None:
        """Da chiamare dopo aver modificato e salvato la configurazione."""
        self.cameras.reload()
        self.recording.reload()
        self.notifier.last_error.clear()  # nuova configurazione: gli errori precedenti non valgono più
        self.notifier.setup_bot()
        if self.actions:
            self.actions.reload()
        try:
            self.sync_audio_sources()
        except Exception:
            log.exception("Aggiornamento dei microfoni fallito")


class CoreHandlers(BotHandlers):
    """Risposte ai comandi Telegram, implementate sul nucleo."""

    def __init__(self, core: MirtoCore):
        self.core = core

    def status_text(self) -> str:
        c = self.core
        st = c.alert_state()
        hhmm = lambda t: time.strftime("%H:%M", time.localtime(t))  # noqa: E731
        head = {"disarmed": _('🔓 Protezione DISATTIVATA: nessun avviso'),
                "paused": _('🔕 Protezione attiva ma avvisi IN PAUSA fino alle {hhmm}', hhmm=hhmm(st['paused_until'])),
                "active": _('🔒 Protezione ATTIVA: avvisi attivi')}[st["mode"]]
        lines = [_('📋 Stato Mirto'), head]
        srcs = list(c.cameras.sources.values())
        if not srcs:
            lines.append(_('Nessuna camera attiva.'))
        for s in srcs:
            icon = "🟢" if s.status == STATUS_ONLINE else "🔴" if s.status == STATUS_OFFLINE else "🟡"
            muted = st["camera_paused"].get(s.info.id)
            lines.append(f"{icon} {s.info.name}" + (_(' (silenziata fino alle {hhmm})', hhmm=hhmm(muted)) if muted else ""))
        if c.actions and c.config.get("actions.nodes"):
            lines.extend(c.actions.status_lines()[1:])  # la prima riga (inserito) è già in testa
        lines.append(c.schedule_line())
        if c.audio and c.audio.sources:
            mics = ", ".join(("🎙 " if m["live"] else "⚠ ") + m["name"] for m in c.audio.status())
            lines.append(_("Suoni: {mics}", mics=mics))
        rec = c.recording.status()
        if rec["enabled"] and rec["recorders"]:
            on = sum(1 for r in rec["recorders"] if r["state"] == "recording")
            u = rec.get("usage") or {}
            lines.append(_("🎞 Registrazione: {on}/{n} camere, {used:.1f} GB usati, {free:.0f} GB liberi",
                           on=on, n=len(rec["recorders"]), used=u.get("used", 0) / 1e9, free=u.get("free", 0) / 1e9)
                         + (_(" · SOSPESA (disco pieno)") if rec["paused_disk"] else ""))
        if c.presence.summary():
            lines.append(c.presence.summary())
        return "\n".join(lines)

    def schedule(self, arg: str) -> str:
        arg = arg.strip().lower()
        c = self.core
        if arg in ("sospendi", "off", "stop", "pause", "suspend"):
            c.config.set("schedule.enabled", False)
            c.config.save()
            return _('⏰ Programmazione SOSPESA: nessun cambio automatico finché non la riattivi (/programma riattiva).')
        if arg in ("riattiva", "on", "attiva", "resume"):
            c.config.set("schedule.enabled", True)
            c.config.save()
            return _('⏰ Programmazione RIATTIVATA.\n') + c.schedule_line()
        return c.scheduler.status_text()

    def snapshot(self, camera_query: str):
        c = self.core
        srcs = list(c.cameras.sources.values())
        q = camera_query.strip().lower()
        chosen = next((s for s in srcs if q and q in s.info.name.lower()), srcs[0] if srcs else None)
        if not chosen:
            return None
        frame, ts, _meta = chosen.latest()
        if frame is None:
            return None
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        when = time.strftime("%d/%m/%Y %H:%M:%S", time.localtime(ts))
        return (buf.tobytes(), f"📷 {chosen.info.name} – {when}") if ok else None

    def clip(self, camera_query: str, seconds: int):
        c = self.core
        srcs = list(c.cameras.sources.values())
        q = camera_query.strip().lower()
        chosen = next((s for s in srcs if q and q in s.info.name.lower()), None)
        if chosen is None:
            recording = [s for s in srcs if c.recording.mode_for(s.info.id) != "off"]
            chosen = recording[0] if recording and not q else None
        if chosen is None:
            return _("Camera non trovata.") if q else _("Nessuna camera registra.")
        path, err = c.recording.live_clip(chosen.info.id, seconds)
        if path is None:
            return err
        try:
            data = path.read_bytes()
        finally:
            path.unlink(missing_ok=True)
        when = time.strftime("%d/%m/%Y %H:%M:%S")
        return data, _("🎞 {camera} – ultimi {s} s – {when}", camera=chosen.info.name, s=seconds, when=when), \
            f"Mirto_{''.join(ch if ch.isalnum() else '_' for ch in chosen.info.name)[:40]}.mp4"

    def pause(self, minutes: float) -> str:
        self.core.pause(minutes * 60)
        return _('Avvisi sospesi per {minutes:g} minuti.', minutes=minutes)

    def resume(self) -> str:
        self.core.pause(0)
        return _('Avvisi riattivati.')

    def false_alarm(self, event_id: str) -> str:
        self.core.store.update(event_id, false_alarm=1)
        return _('Segnato come falso allarme: servirà a regolare le soglie.')

    def mute_event_camera(self, event_id: str, seconds: float) -> str:
        ev = self.core.store.get(event_id)
        if not ev:
            return _('Evento non trovato.')
        self.core.pause(seconds, ev["camera_id"])
        return _('Camera {camera_name} silenziata per {v:.0f} minuti.', camera_name=ev['camera_name'], v=seconds / 60)

    # Le operazioni sulle azioni passano all'ActionEngine, se presente
    def _act(self, name: str, *args) -> str:
        if not self.core.actions:
            return _('Azioni non configurate.')
        return getattr(self.core.actions, name)(*args)

    def arm(self, armed: bool) -> str:
        return self.core.set_armed(armed)

    def stop_all(self) -> str:
        return self._act("stop_all")

    def confirm_action(self, token: str, user: str) -> str:
        return self._act("confirm", token, user)

    def listen(self, seconds: int):
        return self.core.actions.listen(seconds) if self.core.actions else None

    def play_voice(self, audio: bytes, mime: str) -> str:
        return self._act("play_audio", audio, mime)


def run_headless() -> int:
    from .cameras.permissions import ensure_camera_permission  # noqa: PLC0415

    allowed, msg = ensure_camera_permission()
    if not allowed:
        log.error(msg)
    core = MirtoCore()
    from .actions.engine import enable_actions  # noqa: PLC0415

    enable_actions(core)
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    core.start()
    log.info("Modalità senza interfaccia: Ctrl+C per uscire")
    stop.wait()
    core.stop()
    return 0
