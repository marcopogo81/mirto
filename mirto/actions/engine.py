# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Motore delle azioni: decide quando e cosa attivare sui nodi.

Regole di sicurezza applicate qui (e, indipendentemente, anche dal nodo):

* le azioni partono SOLO con il sistema **inserito**;
* la sirena non può durare più di **3 minuti** (limite di legge);
* ogni uscita ha una durata massima e si spegne da sola;
* "Ferma tutto" raggiunge tutti i nodi in parallelo;
* un nodo non raggiungibile non blocca gli altri.

Formato di un'azione in configurazione (``actions.items``)::

    - name: Sirena ingresso      # nome mostrato
      node: ingresso             # id del nodo
      action: sirena             # nome dell'azione sul nodo (GET /azioni)
      kind: siren                # siren | light | audio | generic | deterrent
      mode: auto                 # auto | confirm (pulsante su Telegram)
      delay: 10                  # secondi di attesa prima di attivare
      duration: 180              # secondi di attivazione
      confirm_timeout: 60        # solo mode=confirm
      on_timeout: skip           # skip | run (run solo con conferma "forte" del rilevamento)
      cameras: []                # vuoto = tutte
      min_confidence: 0.6
      enabled: true
"""

from __future__ import annotations

import json
import logging
import re
import secrets as pysecrets
import random
import threading
import time
import dataclasses
import types
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from ..config import data_dir
from .blocchi import NODO, blocchi_di, dispositivo_di, obiettivi_di, scatta
from .presets import MANUAL_KINDS
from .passi import (passi_di, problemi as problemi_catena, quanto_dura,
                    quanto_dura_il_gruppo, quanto_suona, scegli_a_turno, srotola)
from .nodes import NodeClient
from ..i18n import _, questo_computer

log = logging.getLogger(__name__)

SIREN_MAX_SECONDS = 180
SIREN_WINDOW_SECONDS = 1800  # nei 30 minuti la sirena suona al massimo 180 s in totale
GENERIC_MAX_SECONDS = 1800
CONFIRM_TTL_SECONDS = 600  # validità massima di un pulsante di conferma
META_EVERY_SECONDS = 600  # ogni quanto rileggere la "carta d'identità" dei nodi (GET /info)
KINDS = {"siren", "light", "audio", "generic", "deterrent"}
# Tipi delle versioni precedenti: diventano uscite generiche, sempre su conferma da Telegram (Mirto è solo
# software: che cosa è collegato a un'uscita lo decide chi installa, e ne risponde).
LEGACY_KINDS = {"fog": "generic", "lock": "generic"}
DETERRENT_MAX_SECONDS = 30       # un dissuasore suona al massimo 30 s per volta (lo impone anche il nodo)
DETERRENT_MIN_COOLDOWN = 60      # pausa minima tra due scatti dello stesso dissuasore
DETERRENT_MAX_PER_DAY = 200      # limite massimo impostabile per giorno
# Secondi massimi per **comando**, dal tipo che il nodo dichiara. Gli altri tipi (light, generic) non
# hanno un limite proprio: rispondono alla durata dell'azione e a GENERIC_MAX_SECONDS.
LIMITI_PER_TIPO = {"siren": SIREN_MAX_SECONDS, "deterrent": DETERRENT_MAX_SECONDS}
# La prova di una catena non salvata: ogni pezzo accorciato a pochi secondi (la durata dell'azione è
# anche il tetto di ogni passo, quindi basta passarla corta) e un nome suo, che non può scontrarsi con
# quello di un'azione vera perché le azioni vere non possono avere le parentesi nel nome.
PROVA_MAX_SECONDI = 5.0
NOME_PROVA = "(prova)"
# Obiettivi ammessi oltre alla persona per le azioni antintrusione: solo suoni (es. vetro rotto)
DETERRENT_SOUND_TARGETS = {"sound:bird", "sound:dog"}  # suoni di animali: gli unici ammessi per i dissuasori
SOUNDLESS_SECONDS = 10           # dopo un'azione dei nodi i suoni si ignorano: il nodo non deve "sentirsi" da solo
INTRUSION_SOUND_TARGETS = {"sound:glass", "sound:scream", "sound:alarm", "sound:smoke_alarm", "sound:knock",
                           "sound:gunshot", "sound:speech"}
MODES = {"auto", "confirm"}
# Quando un'azione può scattare. «sempre» non guarda la protezione (il dissuasore di oggi: un uccello
# non aspetta che qualcuno inserisca l'allarme) e in cambio ha i limiti — pausa fra due scatti, tetto
# giornaliero, fascia oraria. «protetta» è l'antifurto: scatta solo a casa inserita, e non si limita.
#
# «prank» è il terzo, scelto da Marco il 26/10 («la scelta la metterei nel primo step»): un'azione
# goliardica. Non ha i limiti d'uso — quelli li sostituisce una finestra di tempo — e può avere
# «persona» fra gli obiettivi, che per ogni altro dissuasore è vietato. In cambio scatta **solo
# mentre la modalità Prank del nodo è accesa**: una finestra che apre una persona, con una durata
# scelta, che scade da sé e che «Ferma tutto» chiude. Fuori da quella finestra un'azione prank non
# parte mai, e il divieto torna quello di sempre — è l'unico modo in cui l'eccezione si può
# concedere, perché non è il programma a dichiarare che quello è uno scherzo.
#
# E **tace a casa inserita**, come una «sempre» (Marco, 26/10: *«se uno ha più azioni ed attiva la
# protezione voglio che i prank non siano attivi durante il lavoro serio»*). Inserire l'antifurto è
# dire che da adesso quello che suona vuol dire qualcosa, e uno scherzo che parte in quel momento è
# un falso allarme che ti arriva sul telefono mentre non sei in casa. La finestra sul nodo **resta
# aperta**: se disinserisci prima della scadenza i minuti che restano valgono ancora, e in cambio il
# nodo resta senza budget nel frattempo — che è il prezzo della scelta, e va detto dove si legge.
QUANDO = {"protetta", "sempre", "prank"}


@dataclass
class ActionSpec:
    name: str
    # Il nodo dell'azione: dal 23/10 è un **residuo**. Serve a leggere le configurazioni scritte prima
    # — dove era l'unico posto in cui stesse scritto dove si fanno le cose — e come dispositivo
    # predefinito dei pezzi che non ne dichiarano uno. Un'azione nuova può non averlo affatto, e i
    # suoi pezzi dicono ognuno dove si fa (vedi «dispositivi»).
    node: str = ""
    action: str = ""
    kind: str = "generic"
    mode: str = "auto"
    delay: float = 0.0
    duration: float = 60.0
    confirm_timeout: float = 60.0
    on_timeout: str = "skip"
    cameras: list = field(default_factory=list)
    min_confidence: float = 0.0
    # Una soglia per obiettivo: {"person": 0.7, "sound:glass": 0.5}. Quello che non c'è segue lo
    # standard generale, e «min_confidence» resta come soglia unica delle azioni scritte prima.
    confidenze: dict = field(default_factory=dict)
    enabled: bool = True
    targets: list = field(default_factory=lambda: ["person"])  # cosa la fa scattare: person, dog, bird:corvid, sound:glass
    sounds: list = field(default_factory=list)  # dissuasori: suoni del nodo, a rotazione casuale (vuoto = quelli del nodo)
    cooldown: float = 300.0                      # dissuasori: pausa minima tra due scatti (s)
    max_per_day: int = 40                        # dissuasori: scatti massimi al giorno
    hours: str = ""                              # facoltativo "06:00-20:30": solo in questa fascia oraria
    notify: bool = True                          # dissuasori: avvisa su Telegram quando scatta
    passi: list = field(default_factory=list)    # la catena: [{comando, suono, durata, ripeti, pausa}]
    blocchi: list = field(default_factory=list)  # i blocchi, quando l'interfaccia li scriverà (23/10)
    quando: str = ""                             # "protetta" (serve la casa inserita) o "sempre"
    _cache_blocchi: list | None = field(default=None, repr=False, compare=False)
    ripeti_tutto: int = 1                        # quante volte si ripete tutta la catena

    @classmethod
    def from_dict(cls, d: dict) -> "ActionSpec":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        spec = cls(**known)
        spec.kind = str(spec.kind).strip().lower()
        spec.mode = str(spec.mode).strip().lower()
        if spec.kind in LEGACY_KINDS:
            spec.kind = LEGACY_KINDS[spec.kind]
            spec.mode = "confirm"
        if spec.kind not in KINDS:  # le regole di sicurezza dipendono dal tipo: niente valori sconosciuti
            raise ValueError(_("tipo '{kind}' non valido (ammessi: {join})", kind=spec.kind, join=', '.join(sorted(KINDS))))
        if spec.mode not in MODES:
            raise ValueError(_("modalità '{mode}' non valida (auto o confirm)", mode=spec.mode))
        if not spec.name or not spec.action:
            raise ValueError(_('servono name e action'))
        if not spec.dispositivi():
            # Togliere l'obbligo del nodo non vuol dire accettare un'azione che non sa dove farsi: o
            # ce l'ha scritto sull'azione (come tutte quelle di prima) o su almeno un pezzo.
            raise ValueError(_('serve il nodo, oppure un pezzo che dica dove si fa'))
        spec.targets = [str(t).strip() for t in (spec.targets or []) if str(t).strip()]
        from .confidenze import pulisci  # noqa: PLC0415

        # Una soglia scritta su un obiettivo che non c'è più non vale niente e confonde chi legge il
        # file: si butta qui, dove si butta tutto il resto che non ha senso.
        spec.confidenze = pulisci(spec.confidenze, spec.targets)
        # «Quando è attiva» si legge **prima**, perché il valore `prank` cambia la regola qui sotto.
        # Si legge, non si normalizza: il valore buono lo si decide dopo, quando il tipo è definitivo,
        # altrimenti il predefinito di un'azione vecchia cambierebbe in silenzio.
        voluto = str(spec.quando or "").strip().lower()
        if spec.kind == "deterrent" and "person" in spec.targets and voluto != "prank":
            # Un'azione che scatta anche per le persone non è un «dissuasore», qualunque cosa ci sia
            # scritto nella configurazione: è un'azione come le altre, e tiene i suoi obiettivi. Fino
            # al 23/10 qui si rifiutava l'azione intera con un errore — e un'azione rifiutata non
            # scatta, il che spiega perché il suono non partiva.
            #
            # L'unica eccezione è `prank`, scelta da Marco il 26/10: un dissuasore **può** avere
            # «persona» fra gli obiettivi, ma quell'azione scatta soltanto mentre la modalità Prank
            # del nodo è accesa — una finestra che apre una persona, con una durata, e che scade da
            # sé. Fuori da quella finestra non parte, e il divieto vale come sempre. È l'unico modo
            # in cui questa eccezione si può concedere: non è il programma a dire «questo è uno
            # scherzo», è qualcuno che ha aperto la finestra sul nodo cinque minuti prima.
            spec.kind = "generic"
        if spec.kind == "deterrent":
            if not spec.targets:
                raise ValueError(_("il dissuasore '{name}' non ha obiettivi (animali o uccelli)", name=spec.name))
            # Qui c'era una lista bianca: un dissuasore poteva scattare solo per animali e uccelli, e
            # voci, urla e veicoli venivano tolti **in silenzio**. È caduta il 23/10 con tutto il
            # resto delle regole imposte: quello che chi configura ha chiesto, resta. Al suo posto c'è
            # un avviso visibile — vedi «avvisi.py» — perché una scelta discutibile va segnalata a chi
            # la fa, non corretta alle sue spalle.
            spec.mode = "auto"  # niente conferma su Telegram: un uccello non aspetta
            if voluto != "prank":
                # Un prank non ha i freni d'uso: li ha scelti chi ha aperto la finestra sul nodo, e
                # quella finestra è il freno. Una pausa di un minuto fra due scatti renderebbe
                # inutile proprio la cosa per cui esiste.
                spec.cooldown = max(float(spec.cooldown), DETERRENT_MIN_COOLDOWN)
                spec.max_per_day = max(1, min(int(spec.max_per_day), DETERRENT_MAX_PER_DAY))
        else:
            # Qui, fino al 23/10, gli animali venivano buttati via **in silenzio**: un'azione
            # antintrusione poteva scattare solo per persone e per alcuni suoni. Aveva senso finché il
            # tipo si sceglieva a mano e voleva dire «che specie di azione è questa»; adesso che si
            # sceglie cosa deve riconoscere la camera, buttare via una scelta senza dirlo sarebbe il
            # modo peggiore di non permetterla. Quello che resta è che un'azione deve riconoscere
            # qualcosa: senza obiettivi, «persona» — che è quello che un'azione vuota faceva già.
            spec.targets = spec.targets or ["person"]
        # Quando è attiva: era implicito nel tipo — «dissuasore» voleva dire «sempre», tutto il resto
        # «solo a casa protetta» — ed era una cosa importante nascosta dentro un'altra. Adesso è un
        # campo suo, e per le azioni che esistono si deduce esattamente come prima: così una
        # configurazione vecchia si comporta identica, e una nuova lo può dire in chiaro.
        spec.quando = voluto if voluto in QUANDO else ("sempre" if spec.kind == "deterrent"
                                                       else "protetta")
        if spec.hours and not _parse_hours(spec.hours):
            raise ValueError(_("fascia oraria '{hours}' non valida (es. 06:00-20:30)", hours=spec.hours))
        guai = problemi_catena(spec._passi(), spec.ripeti_tutto)
        if guai:
            raise ValueError(f"{spec.name}: {guai[0]}")
        return spec

    def _passi(self) -> list:
        """I passi di quest'azione. Un'azione senza passi — tutte quelle scritte prima del 22/10 — è
        una catena di un passo solo, costruita **leggendo**: niente da convertire sul disco."""
        return passi_di({"passi": self.passi, "action": self.action, "duration": self.duration,
                         "sounds": self.sounds})

    def dispositivi(self) -> list[str]:
        """I dispositivi che quest'azione tocca, in ordine: il primo è «il posto solito».

        Sostituisce la domanda «di che nodo è questa azione?», che dal 23/10 non ha più una risposta
        sola: un'azione può accendere una luce all'ingresso e far suonare il garage.
        """
        fuori: list[str] = []
        for p in self._passi():
            dove = p.dispositivo or (("nodo:" + self.node) if self.node else "")
            if dove and dove not in fuori:
                fuori.append(dove)
        if not fuori and self.node:
            fuori.append("nodo:" + self.node)
        return fuori

    def catena(self, tetti: dict | None = None, tetti_per=None, giro: int = 0) -> list[dict]:
        """Le chiamate da fare, nell'ordine, con le attese in mezzo. Vedi ``passi.srotola``.

        ``tetti``: i limiti per comando dichiarati dal nodo (``ActionEngine.tetti_comandi``). Senza,
        vale solo il limite del tipo di azione — che è quello che si sa quando il nodo non ha ancora
        risposto, e resta comunque più larga la rete del nodo, che i suoi limiti li impone da sé.

        ``giro``: il numero dello scatto, per i gruppi «a turno» (tappa 5). La scelta si fa qui, una
        volta, **prima** di srotolare: la catena che esce contiene solo quello che deve partire, e
        chi esegue non ha nessuna scelta da fare. Giro 0 è il primo scatto, ed è anche quello che si
        guarda per sapere quanto durerà l'azione — con una domanda sola, quindi una risposta sola.
        """
        # Il nodo dell'azione fa da dispositivo predefinito: i pezzi che non dicono dove vanno
        # finiscono lì, quelli che lo dicono comandano. È il ponte fra il modello di ieri (un nodo per
        # azione) e quello di domani (un dispositivo per blocco), e permette di spostare chi esegue
        # un pezzo per volta invece che tutto insieme.
        dove = ("nodo:" + self.node) if self.node else ""
        return srotola(scegli_a_turno(self._passi(), giro), self.safe_duration(), self.ripeti_tutto,
                       tetti, dispositivo=dove, tetti_per=tetti_per)

    def durata_catena(self, tetti: dict | None = None) -> float:
        """Quanto va avanti l'azione in tutto: è il tempo in cui risulta «in corso»."""
        return quanto_dura(self.catena(tetti))

    def safe_duration(self) -> float:
        cap = {"siren": SIREN_MAX_SECONDS, "deterrent": DETERRENT_MAX_SECONDS}.get(self.kind, GENERIC_MAX_SECONDS)
        return max(1.0, min(float(self.duration), cap))

    def _blocchi(self) -> list:
        """I blocchi di quest'azione. Un'azione scritta prima del 23/10 si converte **leggendo**.

        Si tiene da parte perché «matches» gira per ogni evento di ogni camera: convertire è poco, ma
        poco per mille volte al minuto è un modo lento di non fare niente. Le specifiche si
        ricostruiscono a ogni «reload», quindi non c'è niente da invalidare a mano.
        """
        if self._cache_blocchi is None:
            self._cache_blocchi = blocchi_di({
                "blocchi": self.blocchi, "passi": self.passi, "action": self.action,
                "duration": self.duration, "sounds": self.sounds, "targets": self.targets,
                "cameras": self.cameras, "min_confidence": self.min_confidence,
                "confidenze": self.confidenze})
        return self._cache_blocchi

    def matches(self, ev) -> bool:
        """L'evento accende quest'azione? Lo decide il **primo blocco**, non più campi sparsi.

        Prima la risposta stava in tre posti diversi (obiettivi, camere, confidenza) più una regola
        appesa al tipo dell'azione. Adesso sta in un blocco che si vede nel disegno, e che si può
        mettere anche in mezzo alla catena per farne un controllo (vedi ``blocchi.py``).

        Resta fuori dai blocchi una cosa sola: un'azione **manuale** («ascolta da Telegram») non
        scatta mai da sé, qualunque cosa riconosca. Non è una proprietà di cosa ascolta: è la
        ragione per cui quell'azione esiste.
        """
        if not self.enabled or self.kind in MANUAL_KINDS:
            return False
        return scatta(self._blocchi(), ev)

    def in_hours(self, when: float) -> bool:
        rng = _parse_hours(self.hours) if self.hours else None
        if not rng:
            return True
        lt = time.localtime(when)
        m = lt.tm_hour * 60 + lt.tm_min
        a, b = rng
        return a <= m < b if a <= b else (m >= a or m < b)  # fascia a cavallo della mezzanotte


def senza_codice(testo: str) -> str:
    """Toglie il numero del protocollo da un messaggio del nodo, lasciando le parole.

    Il nodo risponde «Nodo Ingresso: 409 limite del dissuasore raggiunto: al massimo 2 minuti ogni
    ora». La frase è già in italiano e si capisce; quel `409` no — è roba nostra, e in un messaggio
    su Telegram fa sembrare un guasto quella che è una regola di sicurezza che ha funzionato.
    """
    return re.sub(r"(:\s*)[1-5]\d\d\s+", r"\1", str(testo or "")).strip()


def perche_no(exc: Exception) -> str:
    """Perché un passo non è partito, **nella lingua di chi legge**.

    Il nodo parla italiano sempre: il suo registro, il suo comando, i suoi messaggi. Finché l'unica
    cosa che aveva da dare era una frase, quella frase finiva dentro un avviso tradotto — e un
    utente inglese leggeva «Action Halloween not run: i dissuasori hanno già suonato…». Dall'API 1.13
    il rifiuto per budget porta anche ``motivo`` e i numeri, e la frase la scriviamo noi.

    Se il motivo non lo conosciamo resta la frase del nodo, che è meglio di niente: un motivo
    sconosciuto è un nodo più nuovo di questo programma, non un errore.
    """
    dati = dict(getattr(exc, "dati", None) or {})
    motivo = str(dati.get("motivo") or "")
    if motivo in ("budget_dissuasore", "budget_sirena"):
        from ..durate import fra_quanto  # noqa: PLC0415

        minuti = max(1, int(float(dati.get("budget_s") or 0) // 60))
        riprende = fra_quanto(dati.get("riprende_fra_s"))
        quale = _('i dissuasori') if motivo == "budget_dissuasore" else _('la sirena')
        if not riprende:
            return _('{quale}: finiti i {minuti} minuti di suono consentiti', quale=quale, minuti=minuti)
        return _('{quale}: finiti i {minuti} minuti di suono consentiti, riprende fra {riprende}',
                 quale=quale, minuti=minuti, riprende=riprende)
    return senza_codice(str(exc))


def _deterrent_target_ok(t: str) -> bool:
    """Animali COCO, uccelli (anche per gruppo) e versi di animali: mai persone, voci, urla, pianti."""
    from ..detection.labels import GROUPS  # noqa: PLC0415

    animals = next(keys for gid, _n, keys in GROUPS if gid == "animali")
    return t in animals or t == "bird" or t.startswith("bird:") or t in DETERRENT_SOUND_TARGETS


def _parse_hours(text: str) -> tuple[int, int] | None:
    try:
        a, b = (x.strip() for x in str(text).split("-"))
        ha, ma = (int(x) for x in a.split(":"))
        hb, mb = (int(x) for x in b.split(":"))
        if not (0 <= ha < 24 and 0 <= hb <= 24 and 0 <= ma < 60 and 0 <= mb < 60):
            return None
        return ha * 60 + ma, hb * 60 + mb
    except (ValueError, AttributeError):
        return None


@dataclass
class _Confirmation:
    spec: ActionSpec
    event: object
    deadline: float
    created: float = field(default_factory=time.time)


class ActionEngine:
    def __init__(self, core, client_factory=NodeClient.from_config, clock=time.time):
        self.core = core
        self.config = core.config
        self._client_factory = client_factory
        self.clock = clock
        self.clients: dict[str, NodeClient] = {}
        self.node_errors: dict[str, str] = {}
        self.node_online: dict[str, bool] = {}
        self.node_info: dict[str, dict] = {}  # ultimo GET /stato di ogni nodo, con "_at" (ora della lettura)
        self.node_meta: dict[str, dict] = {}  # ultimo GET /info (carta d'identità: tipo, camere…), con "_at"
        # Cosa dichiara ogni nodo: nome del comando → tipo ("siren", "deterrent", "light", "generic").
        # È la fonte dei limiti di una catena: in una catena i comandi si mescolano, e il tetto deve
        # seguire **il comando** (una sirena tre minuti) e non l'etichetta dell'azione che lo contiene.
        self.node_commands: dict[str, dict[str, str]] = {}
        self._wake = threading.Event()
        self.specs: list[ActionSpec] = []
        self._confirm: dict[str, _Confirmation] = {}
        self._timers: list[threading.Timer] = []
        self._strong: dict[str, float] = {}  # camera → ora dell'ultima conferma forte
        self._active_until: dict[str, float] = {}  # azione → fine dell'attivazione in corso
        self._siren_log: list[tuple[float, float]] = []  # (inizio, durata) delle sirene recenti
        self._deterrent_last: dict[str, float] = {}       # dissuasore → ultimo scatto
        self._deterrent_day: dict[str, tuple[str, int]] = {}  # dissuasore → (giorno, scatti)
        self._deterrent_sound: dict[str, str] = {}        # dissuasore → ultimo suono (per non ripeterlo)
        # Azione → a che scatto siamo, per i gruppi «a turno» (tappa 5). Avanza **dopo** uno scatto
        # avvenuto: un'azione rifiutata perché già in corso, o una sirena senza budget, non consuma
        # un turno — se no la rotazione salta un'operazione senza che niente l'abbia suonata.
        self._giri: dict[str, int] = {}
        # Perché l'ultimo tentativo di un'azione non è andato: il nodo lo dice in italiano chiaro
        # («limite del dissuasore raggiunto: al massimo 2 minuti ogni ora») e fino al 25/10 quella
        # frase moriva nel registro. Chi riceveva l'avviso leggeva «non eseguita» e basta — che è
        # come il «3 da guardare» della tabella: abbastanza per preoccuparsi, niente per capire.
        self._perche_no: dict[str, str] = {}
        self.deterrents_paused_until = 0.0                 # "Ferma tutto" sospende anche i dissuasori per un'ora
        self._lock = threading.RLock()
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="azioni")
        self._stop = threading.Event()
        self._hb_thread: threading.Thread | None = None
        from .pairing import PairingWatcher  # noqa: PLC0415

        self.pairing = PairingWatcher(self.config)  # nodi nuovi da abbinare ("Nuovo nodo trovato")
        self.reload()

    # --- configurazione ---------------------------------------------------
    def reload(self) -> None:
        clients, errors = {}, {}
        for nd in self.config.get("actions.nodes") or []:
            try:
                clients[str(nd["id"])] = self._client_factory(nd, self.core.secrets)
            except Exception as exc:
                errors[str(nd.get("id"))] = str(exc)
                log.warning("Nodo %s non utilizzabile: %s", nd.get("name") or nd.get("id"), exc)
        specs = []
        for item in self.config.get("actions.items") or []:
            try:
                specs.append(ActionSpec.from_dict(item))
            except Exception as exc:
                log.error("Azione non valida %s: %s", item, exc)
        with self._lock:
            self.clients, self.node_errors, self.specs = clients, errors, specs

    @property
    def armed(self) -> bool:
        return bool(self.config.get("actions.armed", False))

    def set_armed(self, armed: bool) -> str:
        self.config.set("actions.armed", bool(armed))
        try:
            self.config.save()
        except Exception:
            log.exception("Salvataggio configurazione fallito")
        if not armed:
            self._cancel_pending()
            self._pool.submit(self._stop_nodes)  # spegne anche ciò che è già acceso
        self._audit("inserito" if armed else "disinserito")
        return (_('🔒 Protezione ATTIVA: avvisi e azioni attivi.') if armed
                else _("🔓 Protezione DISATTIVATA: nessun avviso e nessuna azione (gli eventi restano nell'archivio del {computer}).",
                       computer=questo_computer()))

    # --- ciclo di vita ----------------------------------------------------
    def start(self) -> None:
        self._stop.clear()
        if getattr(self._pool, "_shutdown", False):
            self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="azioni")
        self._hb_thread = threading.Thread(target=self._heartbeat_loop, name="nodi-heartbeat", daemon=True)
        self._hb_thread.start()
        self.pairing.start()

    def pair_new_node(self, found: dict, name: str, pin: str = "") -> dict:
        """Abbina un nodo proposto da ``pairing`` (l'utente ha visto l'impronta e scritto il PIN).

        Restituisce il nodo intero e non solo il nome: chi abbina ha bisogno anche dell'``id``, per
        chiedere al nodo com'è attaccato alla rete e offrire il Wi-Fi finché il cavo c'è ancora.
        """
        from .pairing import client_name, pair_found  # noqa: PLC0415

        node_id = pair_found(self.config, self.core.secrets, found, name, client_name(), pin)
        self.reload()
        self.pairing.ignore(found["impronta"])
        self.refresh_nodes_soon()
        nd = next(n for n in self.config.get("actions.nodes") or [] if str(n["id"]) == node_id)
        self._audit(_('nodo abbinato: {name}', name=nd['name']))
        return dict(nd)

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self.pairing.stop()
        with self._lock:
            for t in self._timers:
                t.cancel()
            self._timers.clear()
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _heartbeat_loop(self) -> None:
        while not self._stop.is_set():
            self.check_nodes()
            # con un'azione in corso lo stato dei nodi si rilegge spesso, per mostrarlo quasi in diretta
            busy = any(t > self.clock() for t in self._active_until.values())
            wait = 5.0 if busy else float(self.config.get("actions.heartbeat_seconds", 60))
            self._wake.wait(wait)
            self._wake.clear()

    def _read_meta(self, node_id: str, client, rev=None) -> None:
        """Legge la carta d'identità del nodo e allinea le sue camere in Mirto."""
        try:
            meta = client.info() or {}
        except Exception as exc:  # noqa: BLE001
            log.debug("Nodo %s: /info non disponibile (%s)", node_id, exc)
            return
        self.node_meta[node_id] = dict(meta, _at=self.clock(), _rev=rev)
        # I comandi dichiarati, col loro tipo: letti qui perché si rileggono quando il nodo cambia
        # (``info_rev``) e non a ogni evento — quando scatta qualcosa non c'è tempo di chiedere niente a
        # nessuno. In un «except» suo: se /azioni non risponde, la carta d'identità resta buona.
        try:
            self.node_commands[node_id] = {str(a.get("nome")): str(a.get("tipo") or "generic")
                                           for a in (client.actions() or []) if a.get("nome")}
        except Exception as exc:  # noqa: BLE001
            log.debug("Nodo %s: /azioni non disponibile (%s)", node_id, exc)
        self.sync_node_cameras(node_id)

    def sync_node_cameras(self, node_id: str) -> bool:
        """Crea o aggiorna in configurazione le camere dichiarate dal nodo; ricarica le camere se cambiano."""
        from .node_cameras import sync_node_cameras  # noqa: PLC0415

        node = next((n for n in self.config.get("actions.nodes") or [] if str(n["id"]) == node_id), None)
        meta = self.node_meta.get(node_id)
        if node is None or not meta:
            return False
        info = dict(meta)
        status = self.node_info.get(node_id) or {}
        info.setdefault("host", status.get("host", ""))
        info.setdefault("indirizzi", status.get("indirizzi", []))
        nets, changed = sync_node_cameras(self.config.get("cameras.network") or [], node, info,
                                          getattr(self.core, "secrets", None))
        if changed:
            self.config.set("cameras.network", nets)
            try:
                self.config.save()
            except Exception:
                log.exception("Salvataggio delle camere del nodo fallito")
            log.info("Camere del nodo %s aggiornate", node.get("name", node_id))
            cams = getattr(self.core, "cameras", None)
            if cams is not None:
                cams.reload()
        return changed

    def node_camera_url(self, cam_id: str) -> str | None:
        """Indirizzo attuale di una camera di nodo (``net:<id>``), scelto tra gli indirizzi del nodo."""
        from .node_cameras import node_camera_url  # noqa: PLC0415

        net = next((n for n in self.config.get("cameras.network") or [] if f"net:{n.get('id')}" == cam_id), None)
        if not net or not net.get("node"):
            return None
        node = next((n for n in self.config.get("actions.nodes") or [] if str(n["id"]) == str(net["node"])), None)
        if node is None:
            return None
        return node_camera_url(net, node, self.node_info.get(str(net["node"])) or {})

    def node_audio_url(self, cam_id: str) -> str:
        """L'indirizzo del flusso **con l'audio** di una camera di nodo, se quel nodo lo sta pubblicando.

        Vuoto in tutti gli altri casi, e sono quasi tutti: l'audio nelle registrazioni è spento di
        serie, si accende per nodo, e anche acceso può non essere in piedi in questo istante (il
        microfono staccato, ffmpeg appena ripartito). Chi registra chiama qui a ogni avvio e si
        regola: se c'è, registra l'audio; se no, registra come ha sempre registrato.
        """
        net = next((n for n in self.config.get("cameras.network") or [] if f"net:{n.get('id')}" == cam_id), None)
        if not net or not net.get("node"):
            return ""
        info = self.node_info.get(str(net["node"])) or {}
        audio = dict(info.get("audio_registrazione") or {})
        percorso = str(audio.get("percorso") or "")
        if not (audio.get("attivo") and percorso):
            return ""
        base = self.node_camera_url(cam_id) or ""
        if not base:
            return ""
        # Si cambia **l'ultimo pezzo** dell'indirizzo, non si ricostruisce: utente, password, porta e
        # l'indirizzo scelto fra quelli del nodo sono già stati decisi da `node_camera_url`, e
        # rifarne il conto qui vorrebbe dire due risposte alla stessa domanda.
        return base.rsplit("/", 1)[0] + "/" + percorso

    def node_hosts(self) -> set:
        """Nomi e indirizzi dei nodi abbinati (per non mostrare le loro camere tra le "trovate in rete")."""
        from .node_cameras import node_host  # noqa: PLC0415

        out = set()
        for nd in self.config.get("actions.nodes") or []:
            h = node_host(nd)
            if h:
                out |= {h.lower(), h.split(".")[0].lower()}
            st = self.node_info.get(str(nd["id"])) or {}
            if st.get("host"):
                out |= {str(st["host"]).lower(), str(st["host"]).split(".")[0].lower()}
            out |= {str(a).lower() for a in st.get("indirizzi") or []}
        return out

    def node_names(self) -> dict:
        return {str(n["id"]): n.get("name") or str(n["id"]) for n in self.config.get("actions.nodes") or []}

    def refresh_nodes_soon(self) -> None:
        """Rilegge subito lo stato dei nodi (dopo un'attivazione o un "ferma tutto")."""
        self._wake.set()

    def check_nodes(self) -> None:
        with self._lock:
            clients = dict(self.clients)
        for node_id, client in clients.items():
            try:
                info = client.status()
                online = True
                self.node_info[node_id] = dict(info or {}, _at=self.clock())
                self.node_errors.pop(node_id, None)
                self._check_node_recording(node_id, client.node.name, (info or {}).get("registrazione"))
                self._check_node_pairing(node_id, client.node.name, (info or {}).get("abbinamento"))
                meta = self.node_meta.get(node_id)
                if (meta is None or self.clock() - meta.get("_at", 0) > META_EVERY_SECONDS
                        or (info or {}).get("info_rev", meta.get("_rev")) != meta.get("_rev")):
                    self._read_meta(node_id, client, (info or {}).get("info_rev"))
            except Exception as exc:
                online = False
                self.node_errors[node_id] = str(exc)
            before = self.node_online.get(node_id)
            self.node_online[node_id] = online
            if before is not None and before != online:
                name = client.node.name
                self.core.notifier.notify_text(
                    _('✅ Nodo {name} di nuovo raggiungibile', name=name) if online else _('⚠️ Nodo {name} non risponde', name=name)
                )

    def _check_node_pairing(self, node_id: str, name: str, state) -> None:
        """Avvisi (una volta ciascuno) su chi è abbinato al nodo e sulla chiave di installazione rimasta."""
        if not isinstance(state, dict):
            return
        nodes = self.config.get("actions.nodes") or []
        nd = next((n for n in nodes if str(n.get("id")) == node_id), None)
        if nd is None:
            return
        texts, changed = [], False

        # 1. il nodo ha già un abbinamento con chiave propria ma accetta ancora quella di installazione:
        #    è condivisa e chi la conosce comanda il nodo, va ritirata (se no si ritira da sola)
        if state.get("chiave_installazione") and state.get("chiavi") and not nd.get("installazione_avvisata"):
            nd["installazione_avvisata"] = True
            changed = True
            left = int(state.get("ritiro_installazione_tra_s") or 0)
            self._audit("chiave-installazione-da-ritirare", nodo=node_id, ritiro_tra_s=left)
            texts.append(_('⚠️ Nodo {name}: accetta ancora la chiave di installazione, quella condivisa. Ritirala '
                           'sul nodo con  mirto-nodo abbina --revoca installazione  (altrimenti si ritira da '
                           'sola tra {days} giorni)', name=name, days=max(1, left // 86400) if left else 7))

        # 2. qualcuno si è abbinato al nodo e non è questo Mirto
        who = str(state.get("da") or "").strip()
        # "chiave · <indirizzo>": abbinamento con la chiave copiata a mano, non si sa chi sia: niente allarmi
        if who and not who.startswith("chiave ·") and str(nd.get("abbinato_da") or "") != who:
            from .pairing import client_name  # noqa: PLC0415

            nd["abbinato_da"] = who  # ricordato in configurazione: l'avviso non si ripete a ogni avvio
            changed = True
            if client_name().lower() not in who.lower():
                self._audit("nodo-abbinato-da-altri", nodo=node_id, da=who)
                texts.append(_('⚠️ Nodo {name} abbinato da {who}. Se non sei stato tu, sul nodo lancia  '
                               'mirto-nodo abbina --revoca-tutti', name=name, who=who))

        if changed:
            self.config.set("actions.nodes", nodes)
            try:
                self.config.save()
            except Exception:
                log.exception("Salvataggio configurazione fallito")
        for text in texts:
            self.core.notifier.notify_text(text)

    def _check_node_recording(self, node_id: str, name: str, rec: dict | None) -> None:
        """Registrazione sul nodo ferma (disco staccato, pieno, MediaMTX giù) da 5 minuti: avviso, una volta."""
        if not isinstance(rec, dict) or not rec.get("attiva", True):
            return
        since = getattr(self, "_rec_err_since", None)
        if since is None:
            self._rec_err_since, self._rec_alerted = {}, set()
        err = str(rec.get("errore") or "")
        if err:
            first = self._rec_err_since.setdefault(node_id, self.clock())
            if self.clock() - first >= 300 and node_id not in self._rec_alerted:
                self._rec_alerted.add(node_id)
                self.core.notifier.notify_text(_("⚠️ Nodo {name}: registrazione ferma ({error})", name=name, error=err))
        else:
            self._rec_err_since.pop(node_id, None)
            if node_id in self._rec_alerted:
                self._rec_alerted.discard(node_id)
                self.core.notifier.notify_text(_("✅ Nodo {name}: registrazione ripresa", name=name))

    # --- conferme da Telegram ----------------------------------------------
    def confirmation_buttons(self, ev) -> list[tuple[str, str]]:
        """Pulsanti "Attiva …" da aggiungere al messaggio Telegram dell'evento."""
        if not self.armed:
            return []
        buttons = []
        now = self.clock()
        with self._lock:
            for tok, c in list(self._confirm.items()):  # pulizia
                if now - c.created > CONFIRM_TTL_SECONDS:
                    del self._confirm[tok]
            for spec in self.specs:
                if spec.mode == "confirm" and spec.matches(ev):
                    token = pysecrets.token_urlsafe(9)
                    self._confirm[token] = _Confirmation(spec, ev, deadline=now + float(spec.confirm_timeout))
                    buttons.append((_('Attiva {name}', name=spec.name), f"ok:{token}"))
                    if spec.on_timeout == "run":
                        self._schedule(float(spec.confirm_timeout), self._confirm_timeout, token)
        return buttons

    def confirm(self, token: str, user: str) -> str:
        with self._lock:
            c = self._confirm.pop(token, None)
        if not c or self.clock() > c.deadline:
            log.info("Conferma rifiutata: pulsante scaduto o già usato")
            return _('Pulsante scaduto o già usato.')
        if not self.armed:
            return _('Protezione disattivata: azione non eseguita.')
        self._audit("conferma", azione=c.spec.name, utente=user, evento=getattr(c.event, "id", ""))
        self._pool.submit(self._activate, c.spec, c.event)
        return _('{name}: attivazione confermata.', name=c.spec.name)

    def _confirm_timeout(self, token: str) -> None:
        with self._lock:
            c = self._confirm.pop(token, None)
        if not c or not self.armed:
            return
        strong = self._strong.get(c.event.camera_id, 0)
        if self.clock() - strong <= float(c.spec.confirm_timeout) + 30:
            self._audit("timeout-esegui", azione=c.spec.name, evento=c.event.id)
            self._activate(c.spec, c.event)
        else:
            self._audit("timeout-annulla", azione=c.spec.name, evento=c.event.id)

    # --- una strada sola -----------------------------------------------------
    def on_intrusion(self, ev) -> None:
        """Conferma **forte**: qualcosa è stato riconosciuto abbastanza bene da far agire la casa."""
        if getattr(ev, "label", "person") == "person":  # solo una persona certa vale come conferma forte
            self._strong[ev.camera_id] = self.clock()
        self._scatta(ev, forte=True)

    def on_detection(self, ev) -> list[str]:
        """Qualcosa è stato riconosciuto. Restituisce i nomi delle azioni avviate."""
        return self._scatta(ev, forte=False)

    def _scatta(self, ev, forte: bool) -> list[str]:
        """Chi parte per questo evento. **Una strada sola**, dal 23/10.

        Prima ce n'erano due, e la porta la sceglieva il *tipo* dell'azione: i dissuasori di qua
        (senza protezione, con pausa e tetto giornaliero), tutto il resto di là (solo a casa
        inserita). Era comodo finché i tipi erano etichette scelte a mano, ed è diventato un muro
        appena il tipo ha smesso di essere una domanda: un'azione che scatta sia per una persona sia
        per un gatto non stava in nessuna delle due porte.

        Adesso la porta è una e si guardano due cose diverse, che prima erano confuse in una:

        * **quanto è sicuro l'evento** — ``forte``. Una persona fa agire la casa solo dopo la conferma
          forte: far suonare una sirena su un riconoscimento incerto è il modo più veloce di far
          spegnere Mirto a chi ce l'ha;
        * **quando l'azione è attiva** — ``spec.quando``. «sempre» non guarda la protezione e in
          cambio ha i limiti; «protetta» vuole la casa inserita e non si limita; «prank» non ha
          limiti d'uso e scatta **solo mentre la modalità Prank di quel nodo è accesa e la casa non
          è inserita**.

        Le due cose sono indipendenti, e tenerle separate è tutto il guadagno di questa riscrittura.
        """
        now = self.clock()
        if "person" in obiettivi_di(ev) and not forte:
            return []          # una persona vista non basta: si aspetta la conferma
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        subito, protette, partite = [], [], []
        with self._lock:
            for spec in self.specs:
                if spec.mode != "auto" or not spec.matches(ev):
                    continue
                if spec.quando == "prank":
                    # La finestra aperta sul nodo **è** il limite di un prank: dentro non ha freni,
                    # fuori non esiste. Si chiede allo stato periodico, che quel dato lo ha già:
                    # niente da domandare al nodo mentre un evento sta scattando.
                    #
                    # E la casa inserita lo zittisce: due cancelli, non uno. Un prank che parte
                    # mentre l'antifurto è attivo è un avviso che arriva sul telefono di chi non è in
                    # casa, e non c'è modo di capire dal messaggio che era uno scherzo.
                    if self.armed or now < self.deterrents_paused_until \
                            or not self.prank_attiva(spec.node):
                        continue
                    self._deterrent_last[spec.name] = now
                    subito.append(spec)
                    continue
                if spec.quando != "sempre":
                    protette.append(spec)
                    continue
                if now < self.deterrents_paused_until or not spec.in_hours(now):
                    continue
                if now - self._deterrent_last.get(spec.name, -1e12) < spec.cooldown:
                    continue
                d, n = self._deterrent_day.get(spec.name, (day, 0))
                if d != day:
                    d, n = day, 0
                if n >= spec.max_per_day:
                    if n == spec.max_per_day:  # avvisa una volta sola al giorno
                        self._deterrent_day[spec.name] = (d, n + 1)
                        self._pool.submit(self._deterrent_text, _(
                            "🔇 {name}: raggiunti i {max} scatti di oggi, riprende domani.", name=spec.name,
                            max=spec.max_per_day), spec)
                    continue
                self._deterrent_last[spec.name] = now
                self._deterrent_day[spec.name] = (d, n + 1)
                subito.append(spec)
        if protette and not self.armed:
            log.info("Riconosciuto ma sistema disinserito: nessuna azione (%s)",
                     ", ".join(s.name for s in protette))
            protette = []
        if protette:
            self.core.notifier.notify_text(
                _('🚨 Intrusione confermata ({camera_name}): avvio {join}',
                  camera_name=getattr(ev, "camera_name", ""), join=', '.join(s.name for s in protette))
            )
        for spec in protette:
            partite.append(spec)
            if spec.delay > 0:
                self._schedule(spec.delay, self._activate_if_armed, spec, ev)
            else:
                self._pool.submit(self._activate, spec, ev)
        for spec in subito:
            partite.append(spec)
            sound = self._pick_sound(spec)
            if spec.delay > 0:
                self._schedule(spec.delay, self._activate_deterrent, spec, ev, sound)
            else:
                self._pool.submit(self._activate_deterrent, spec, ev, sound)
        return [s.name for s in partite]

    def _pick_sound(self, spec: ActionSpec) -> str:
        """Suono a caso tra quelli scelti, mai lo stesso due volte di fila (gli animali si abituano)."""
        options = list(spec.sounds or [])
        if not options:
            info = self.node_meta.get(spec.node, {})
            dev = next((d for d in info.get("dispositivi", []) if spec.action in (d.get("nome"), d.get("id"))), {})
            options = list(dev.get("suoni", []))
        if not options:
            return ""
        last = self._deterrent_sound.get(spec.name)
        choices = [s for s in options if s != last] or options
        pick = random.choice(choices)
        self._deterrent_sound[spec.name] = pick
        return pick

    def _non_eseguita(self, spec: ActionSpec) -> str:
        """L'avviso che si manda quando un'azione non parte: con il motivo, se si sa.

        Il motivo c'è quasi sempre — lo dice il nodo — e fino al 25/10 lo scriveva solo il messaggio
        dei passi **dal secondo in poi**: sul primo, cioè il caso più comune, restava «non eseguita».
        """
        perche = self._perche_no.pop(spec.name, "")
        if not perche:
            return _('⚠️ Azione {name} non eseguita', name=spec.name)
        return _('⚠️ Azione {name} non eseguita: {perche}', name=spec.name, perche=perche)

    def _activate_deterrent(self, spec: ActionSpec, ev, sound: str) -> bool:
        # Stessa catena di tutti, col suono scelto a rotazione come predefinito dei passi che non ne
        # hanno uno loro: un dissuasore è un'azione come le altre, e da qui lo è anche nel codice.
        catena = self._annota(spec.catena(tetti_per=self._tetti_di, giro=self._giri.get(spec.name, 0)))
        duration = quanto_dura(catena)
        if not self._esegui_passo(spec, ev, catena, 0, sound):
            self._deterrent_text(self._non_eseguita(spec), spec)
            return False
        self._avanza_giro(spec)
        with self._lock:
            self._active_until[spec.name] = self.clock() + duration
        what = ev.short_text() if hasattr(ev, "short_text") else getattr(ev, "label", "")
        self._audit("dissuasore", azione=spec.name, nodo=spec.node,
                    dispositivi=",".join(spec.dispositivi()), suono=sound,
                    evento=getattr(ev, "id", ""), cosa=what)
        self._deterrent_text(_("🦅 {name}: {what} su {camera} → {sound}", name=spec.name, what=what,
                               camera=getattr(ev, "camera_name", ""), sound=sound or _("suono del nodo")), spec)
        self.refresh_nodes_soon()
        return True

    def _deterrent_text(self, text: str, spec: ActionSpec) -> None:
        """I dissuasori lavorano anche a protezione spenta: i loro messaggi passano comunque (se richiesti)."""
        if not spec.notify:
            return
        # Con la modalità Prank accesa, l'avviso lo dice. Il budget dei suoni non vale più: chi legge
        # «ha suonato» deve sapere che sta leggendo una casa con una regola in meno, altrimenti una
        # modalità che qualcuno ha acceso stamattina diventa invisibile proprio nei messaggi che
        # raccontano cosa succede.
        if self.prank_attiva(spec.node):
            text += _('\n🎭 Modalità Prank accesa: nessun budget sui suoni.')
        try:
            self.core.notifier.notify_text(text, force=True)
        except TypeError:  # notificatori senza "force" (test)
            self.core.notifier.notify_text(text)

    def recently_active(self, margin: float = SOUNDLESS_SECONDS) -> bool:
        """Un nodo sta suonando (sirena, dissuasore…) o ha appena smesso: i suoni sentiti ora sono suoi."""
        now = self.clock()
        with self._lock:
            return any(until + margin > now for until in self._active_until.values())

    def _schedule(self, delay: float, fn, *args) -> None:
        t = threading.Timer(delay, fn, args=args)
        t.daemon = True
        with self._lock:
            self._timers = [x for x in self._timers if x.is_alive()]
            self._timers.append(t)
        t.start()

    def _activate_if_armed(self, spec: ActionSpec, ev) -> None:
        if self.armed:
            self._activate(spec, ev)

    def _allowed_duration(self, spec: ActionSpec) -> float:
        """Durata concessa: 0 se l'azione è ancora attiva o se la sirena ha esaurito i 3 minuti."""
        now = self.clock()
        with self._lock:
            if self._active_until.get(spec.name, 0) > now:
                return 0.0
            # Tutta la catena, attese comprese: è il tempo in cui l'azione risulta «in corso», e per
            # una sirena è anche quello che consuma dal budget dei tre minuti. Contare anche le attese
            # è prudente — toglie più budget del dovuto, mai meno.
            catena = self._annota(spec.catena(tetti_per=self._tetti_di, giro=self._giri.get(spec.name, 0)))
            duration = quanto_dura(catena)
            # Quanto di questa catena è **sirena**, per il budget dei tre minuti ogni mezz'ora. Prima
            # si guardava l'etichetta dell'azione (``kind == "siren"``), e bastava finché un'azione
            # faceva una cosa sola: da quando è una catena, un passo «sirena» dentro un'azione
            # «generica» suonerebbe senza consumare niente. Ora si contano i secondi dei comandi che il
            # nodo dichiara sirena, più — se il nodo non ha ancora detto la sua — quelli dell'azione
            # che si dichiara tale: il conto può essere prudente, mai distratto.
            tipi = self.tipi_comandi(spec.node)
            sirena = quanto_suona(catena, tipi)
            if not sirena and spec.kind == "siren" and spec.action not in tipi:
                sirena = duration
            if sirena:
                self._siren_log = [(t, d) for t, d in self._siren_log if now - t < SIREN_WINDOW_SECONDS]
                used = sum(d for _t, d in self._siren_log)
                concesso = min(sirena, SIREN_MAX_SECONDS - used)
                if concesso < 1:
                    return 0.0
                self._siren_log.append((now, concesso))
                # Se il budget taglia la sirena, taglia l'azione: tenerla «in corso» più a lungo di
                # quanto le è concesso suonare vorrebbe dire bloccare il prossimo scatto per niente.
                duration = min(duration, duration - sirena + concesso)
            self._active_until[spec.name] = now + duration
            return duration

    def _esegui_passo(self, spec: ActionSpec, ev, catena: list[dict], i: int,
                      suono_predefinito: str = "") -> bool:
        """Esegue una chiamata della catena e **programma la successiva**. True se questa è partita.

        Il passo dopo si programma con gli stessi timer di tutto il resto, e non è un dettaglio: vuol
        dire che «Ferma tutto» interrompe una catena a metà come interrompe qualunque altra cosa,
        senza che qui dentro ci sia una riga che se ne occupa.

        Dopo un errore la catena **si ferma**. Andare avanti vorrebbe dire accendere la luce di una
        sequenza il cui suono non è partito: una casa lasciata a metà di qualcosa è peggio di una casa
        a cui non è successo niente.
        """
        if not (0 <= i < len(catena)):
            return False
        chiamata = catena[i]
        # **Dove** va questa chiamata lo dice la chiamata, non l'azione: dal 23/10 una catena può
        # attraversare due nodi (la luce all'ingresso, il suono in garage) e chi esegue deve
        # obbedire al pezzo. Vuoto vuol dire «quello dell'azione», che è il caso di tutte le azioni
        # scritte finora.
        dove = str(chiamata.get("dispositivo") or "") or (("nodo:" + spec.node) if spec.node else "")
        client = self._client_di(dove)
        if client is None:
            log.error("Azione %s, passo %d: dispositivo %s non disponibile", spec.name, i + 1,
                      dove or "?")
            self._audit("errore", azione=spec.name, errore=_('nodo non disponibile'), passo=i + 1)
            return False
        suono = str(chiamata.get("suono") or suono_predefinito)
        # «volte» dice al nodo quante ripetizioni esatte fare, invece di quanti secondi riempire: per
        # un suono registrato la durata la decide il suono, non chi configura (23/10).
        extra = {"suono": suono} if suono else {}
        if int(chiamata.get("volte") or 0) > 0:
            extra["volte"] = int(chiamata["volte"])
        # **Da quale uscita** di quel dispositivo (API 1.10). Vuoto vuol dire «quella configurata sul
        # nodo», cioè come ha sempre funzionato: il campo non si manda nemmeno, così un nodo vecchio
        # riceve esattamente la richiesta di prima.
        if str(chiamata.get("uscita") or ""):
            extra["uscita"] = str(chiamata["uscita"])
        try:
            esito = client.activate(chiamata["comando"], chiamata["durata"], getattr(ev, "id", ""),
                                    getattr(ev, "camera_name", ""),
                                    extra=extra or None)
        except Exception as exc:
            log.error("Azione %s, passo %d (%s) non eseguito: %s", spec.name, i + 1,
                      chiamata["comando"], exc)
            self._audit("errore", azione=spec.name, errore=str(exc), passo=i + 1)
            self._perche_no[spec.name] = perche_no(exc)
            if i:   # il primo errore lo racconta chi ha chiesto l'azione; dal secondo in poi, qui
                self.core.notifier.notify_text(
                    _('⚠️ Azione {name}: il passo {passo} non è stato eseguito ({exc})',
                      name=spec.name, passo=i + 1, exc=exc))
            # Un pezzo che parte **insieme** a un altro non porta via la catena quando fallisce. La
            # regola di fermarsi nasce dalla fila — accendere la luce di una sequenza il cui suono non
            # è partito lascia la casa a metà — ma qui il pezzo di prima **è già partito**: fermarsi
            # non riporta niente indietro, e spegne anche i pezzi dopo, che non c'entrano. Il caso che
            # succede davvero è la cassa occupata: il nodo dall'API 1.11 dice no al secondo suono, e
            # l'unica cosa che non deve avvenire è quel suono (tappa 4).
            if catena[i].get("nel_gruppo"):
                self._programma_dopo(spec, ev, catena, i, suono_predefinito)
            return False
        # Il nodo risponde «già attiva» quando quel comando è ancora in corso: la chiamata è andata a
        # buon fine ma **non ha fatto niente**. Non è un errore da fermare la catena (la casa sta già
        # facendo quella cosa), ma va scritto: è l'unico modo di accorgersi di una catena che suona una
        # volta invece di tre, e senza questa riga non si vedrebbe da nessuna parte.
        if isinstance(esito, dict) and esito.get("gia_attiva"):
            log.warning("Azione %s, passo %d (%s): il nodo lo aveva già in corso, salta (restano %s s)",
                        spec.name, i + 1, chiamata["comando"], esito.get("restano_s"))
            self._audit("passo_saltato", azione=spec.name, passo=i + 1, comando=chiamata["comando"])
        self._programma_dopo(spec, ev, catena, i, suono_predefinito)
        return True

    def _avanza_giro(self, spec: ActionSpec) -> None:
        """Lo scatto è avvenuto: la prossima volta, in un gruppo «a turno», tocca a quella dopo.

        Si chiama **dopo** che il primo pezzo è partito, non prima di provarci: uno scatto che non è
        avvenuto non è uno scatto, e contarlo vorrebbe dire saltare un'operazione che nessuno ha
        sentito. È la stessa regola della pausa fra due scatti, applicata al giro.
        """
        with self._lock:
            self._giri[spec.name] = self._giri.get(spec.name, 0) + 1

    def _programma_dopo(self, spec: ActionSpec, ev, catena: list[dict], i: int,
                        suono_predefinito: str = "") -> None:
        """Programma la chiamata dopo la ``i``-esima, con l'attesa che le spetta.

        Chi parte **insieme** non aspetta: la chiamata dopo si fa adesso, e il nodo (o l'altro nodo)
        la riceve mentre questa sta ancora andando. È tutto quello che serve al parallelo fra
        dispositivi diversi — il suono dalla cassa e la luce dal relè nello stesso istante sono due
        richieste mandate una dietro l'altra senza pause (tappa 3, 24/10).

        Chi viene **dopo un gruppo** aspetta la più lunga del gruppo, non l'ultima partita: le altre
        stanno ancora andando, e `quanto_dura` conta così. Due conti diversi sullo stesso tempo
        vogliono dire che quello che si sente è sempre l'altro (tappa 4).
        """
        if i + 1 >= len(catena):
            return
        fra = 0.0 if catena[i + 1].get("nel_gruppo") else quanto_dura_il_gruppo(catena, i)
        self._schedule(fra, self._esegui_passo, spec, ev, catena, i + 1, suono_predefinito)

    def _activate(self, spec: ActionSpec, ev) -> bool:
        if not self.armed:  # disinserito nel frattempo (o "Ferma tutto")
            return False
        duration = self._allowed_duration(spec)
        if duration <= 0:
            log.info("Azione %s non ripetuta (già attiva o limite sirena raggiunto)", spec.name)
            return False
        catena = self._annota(spec.catena(tetti_per=self._tetti_di, giro=self._giri.get(spec.name, 0)))
        if not self._esegui_passo(spec, ev, catena, 0):
            self.core.notifier.notify_text(self._non_eseguita(spec))
            return False
        self._avanza_giro(spec)
        # La riga di controllo dice **dove** è andata davvero: con una catena su due nodi, scrivere
        # il nodo dell'azione sarebbe scrivere il falso proprio nel registro che serve a capire cos'è
        # successo. Un dispositivo solo resta scritto come prima.
        dovunque = sorted({str(c.get("dispositivo") or "") for c in catena} - {""})
        self._audit("attivata", azione=spec.name, nodo=spec.node, durata=duration,
                    dispositivi=",".join(dovunque) if len(dovunque) > 1 else (dovunque[0] if dovunque else ""),
                    evento=getattr(ev, "id", ""), passi=len(catena))
        self.refresh_nodes_soon()
        return True

    def has_activity(self) -> bool:
        """C'è qualcosa in corso sui nodi: un'azione accesa, una in partenza o una conferma in attesa."""
        now = self.clock()
        with self._lock:
            return (any(t > now for t in self._active_until.values())
                    or any(t.is_alive() for t in self._timers)
                    or bool(self._confirm))

    # --- comandi manuali ----------------------------------------------------
    def _cancel_pending(self) -> None:
        with self._lock:
            for t in self._timers:
                t.cancel()
            self._timers.clear()
            self._confirm.clear()
            self._active_until.clear()

    def stop_all(self) -> str:
        """Spegne tutto su tutti i nodi e DISINSERISCE il sistema (nessuna nuova azione partirà)."""
        was_armed = self.armed
        if was_armed:
            self.config.set("actions.armed", False)
            try:
                self.config.save()
            except Exception:
                log.exception("Salvataggio configurazione fallito")
        self._cancel_pending()
        self.deterrents_paused_until = self.clock() + 3600  # anche i dissuasori si fermano per un'ora
        msg = self._stop_nodes()
        return msg + (_(' Protezione disattivata: /inserisci per riattivarla.') if was_armed else "")

    def _stop_nodes(self) -> str:
        with self._lock:
            clients = dict(self.clients)
        if not clients:
            return _('Nessun nodo configurato.')
        results = {}
        futures = {nid: self._pool.submit(c.stop_all) for nid, c in clients.items()}
        for nid, fut in futures.items():
            try:
                fut.result(timeout=15)
                results[nid] = "ok"
            except Exception as exc:
                results[nid] = _('errore: {exc}', exc=exc)
        self._audit("ferma-tutto", esiti=results)
        self.refresh_nodes_soon()
        failed = [n for n, r in results.items() if r != "ok"]
        return _('⛔ Tutto fermo.') if not failed else _('⛔ Fermati i nodi raggiungibili. Non raggiunti: {join}', join=', '.join(failed))

    def _audio_client(self) -> NodeClient | None:
        with self._lock:
            spec = next((s for s in self.specs if s.kind == "audio" and s.enabled), None)
            return self.clients.get(spec.node) if spec else None

    def play_audio(self, audio: bytes, mime: str) -> str:
        client = self._audio_client()
        if not client:
            return _('Audio non configurato.')
        try:
            client.play_audio(audio, mime)
            self._audit("audio-riproduci", byte=len(audio))
            return _('🔊 Messaggio riprodotto.')
        except Exception as exc:
            return _("Audio: errore ({exc})", exc=exc)

    def listen(self, seconds: int):
        client = self._audio_client()
        if not client:
            return None
        try:
            return client.record_audio(seconds)
        except Exception as exc:
            log.error("Registrazione audio fallita: %s", exc)
            return None

    def prova_catena(self, node_id: str, passi: list[dict], ripeti_tutto: int = 1) -> str:
        """Esegue una catena **non salvata**: è il pulsante «Prova» mentre la stai costruendo.

        La si può fare solo perché la sequenza la eseguiamo noi: il campanello lo senti prima di
        salvarlo, ed è la differenza fra un modulo e uno strumento.

        Ogni pezzo viene accorciato a ``PROVA_MAX_SECONDI``, e non per prudenza generica: la durata
        dell'azione è anche il tetto di ogni passo, quindi passarla corta accorcia tutto da sé. Chi
        prova una catena vuole sentire **cosa** fa e **in che ordine**, non restare tre minuti con una
        sirena accesa dentro una finestra di configurazione. I limiti veri valgono comunque: la sirena
        consuma il suo budget anche in prova, perché una sirena che suona è una sirena che suona.
        """
        passi = [dict(p) for p in (passi or []) if (p or {}).get("comando")]
        if not passi:
            return _('Non c\'è ancora niente da provare.')
        client = self.clients.get(str(node_id))
        if not client:
            return _('Nodo {node} non disponibile: {get}', node=node_id,
                     get=self.node_errors.get(str(node_id), ''))
        try:
            spec = ActionSpec.from_dict({"name": NOME_PROVA, "node": str(node_id),
                                         "action": str(passi[0].get("comando")), "kind": "generic",
                                         "mode": "auto", "duration": PROVA_MAX_SECONDI,
                                         "passi": passi, "ripeti_tutto": int(ripeti_tutto or 1)})
        except ValueError as exc:
            return _('Errore: {exc}', exc=exc)
        durata = self._allowed_duration(spec)
        if durata <= 0:
            return _('Una prova è già in corso, o la sirena ha esaurito i suoi tre minuti.')
        catena = self._annota(spec.catena(tetti_per=self._tetti_di, giro=self._giri.get(spec.name, 0)))
        ev = types.SimpleNamespace(id="prova", camera_name=_('Prova'), camera_id="prova", score=1.0)
        if not self._esegui_passo(spec, ev, catena, 0):
            with self._lock:
                self._active_until.pop(NOME_PROVA, None)
            return _('Il nodo non ha eseguito il primo pezzo: guarda Stato e diagnosi.')
        # Anche una prova e' uno scatto: premendo «Prova» due volte si sente la rotazione, che e'
        # l'unico modo di controllarla senza aspettare che passi un gatto.
        self._avanza_giro(spec)
        return _('Via: {n} passaggi, {s} s in tutto. Guarda (o ascolta) il nodo.',
                 n=len(catena), s=int(quanto_dura(catena)))

    def prank(self, node_id: str, minuti: float) -> str:
        """Accende la modalità Prank su un nodo per tanti minuti, o la spegne con ``0`` (API 1.13).

        La modalità vive **nel nodo**, come il budget che sospende: da qui si chiede e basta. È la
        stessa ragione per cui i limiti di durata non sono mai stati nostri — una garanzia che un
        programma può togliersi da sé non è una garanzia.
        """
        client = self.clients.get(str(node_id))
        if not client:
            return _('Nodo {node} non raggiungibile: {get}', node=node_id,
                     get=self.node_errors.get(str(node_id), ''))
        try:
            esito = client.prank(max(0.0, float(minuti)))
        except Exception as exc:  # noqa: BLE001
            log.warning("Modalità Prank su %s non cambiata: %s", node_id, exc)
            return _('Il nodo non ha accettato: {exc}', exc=perche_no(exc))
        self._audit("prank", nodo=str(node_id), minuti=int(minuti))
        self.refresh_nodes_soon()
        resta = int(((esito or {}).get("prank") or {}).get("restano_s") or 0)
        if not resta:
            return _('Modalità Prank spenta: tornano i limiti normali dei suoni.')
        from ..durate import fra_quanto  # noqa: PLC0415

        return _('Modalità Prank accesa per {quanto}: niente budget dei suoni, '
                 'restano i 30 secondi per scatto.', quanto=fra_quanto(resta))

    def volume(self, node_id: str, percento: int) -> str:
        """Porta il volume della cassa di un nodo a quella percentuale. Torna cosa dire a chi guarda."""
        client = self.clients.get(str(node_id))
        if not client:
            return _('Nodo {node} non raggiungibile: {get}', node=node_id,
                     get=self.node_errors.get(str(node_id), ''))
        try:
            esito = client.volume(int(percento))
        except Exception as exc:  # noqa: BLE001
            log.warning("Volume di %s non cambiato: %s", node_id, exc)
            return _('Il nodo non ha accettato: {exc}', exc=perche_no(exc))
        # Lo stato periodico lo rilegge comunque, ma fra due letture passano secondi: il valore che
        # il nodo ha appena confermato va messo subito, o i quadratini tornerebbero indietro sotto il
        # dito di chi li ha cliccati.
        nuovo = (esito or {}).get("volume")
        if isinstance(nuovo, dict):
            with self._lock:
                vecchio = dict(self.node_info.get(str(node_id)) or {})
                if vecchio:
                    vecchio["volume"] = nuovo
                    self.node_info[str(node_id)] = vecchio
        self.refresh_nodes_soon()
        return _('Volume della cassa: {per} %', per=int((nuovo or {}).get("percento", percento)))

    def prank_attiva(self, node_id: str = "") -> bool:
        """C'è un nodo con la modalità Prank accesa? Con ``node_id``, quel nodo.

        Lo stato periodico lo sa già: non si chiede niente al nodo per rispondere. Serve agli avvisi,
        che mentre è accesa devono dirlo — una modalità che cambia le regole di sicurezza non può
        essere invisibile proprio nel messaggio che racconta cosa ha fatto la casa.
        """
        for nid, info in (self.node_info or {}).items():
            if node_id and str(nid) != str(node_id):
                continue
            if (dict(info or {}).get("prank") or {}).get("attiva"):
                return True
        return False

    def ferma_prova(self, node_id: str) -> str:
        """Ferma una catena che si sta provando: spegne il nodo e **butta via i passi in coda**.

        Spegnere l'audio non basta: una catena è fatta di passi programmati, e il secondo partirebbe
        lo stesso qualche secondo dopo. Marco, provando un brano di mezzo minuto dentro il wizard:
        *«da qui non è possibile bloccare l'audio una volta premuto play»*.

        Non è «Ferma tutto»: **non disinserisce** la protezione. Annulla però tutta la coda, non solo
        quella della prova — i passi programmati non portano scritto di chi sono. È la direzione
        sicura: fermarsi di troppo, mai di meno.
        """
        self._cancel_pending()
        with self._lock:
            self._active_until.pop(NOME_PROVA, None)
            client = self.clients.get(str(node_id))
        if not client:
            return _('Nodo {node} non disponibile: {get}', node=node_id,
                     get=self.node_errors.get(str(node_id), ''))
        try:
            client.stop_all()
        except Exception as exc:  # noqa: BLE001
            return _('Errore: {exc}', exc=exc)
        self.refresh_nodes_soon()
        return _('Fermato.')

    def test_action(self, spec_name: str, dry_run: bool = True) -> str:
        """Pulsante "Prova azione" dell'interfaccia. In prova a vuoto verifica solo il nodo."""
        with self._lock:
            spec = next((s for s in self.specs if s.name == spec_name), None)
        if not spec:
            return _('Azione non trovata.')
        dove = self._primo_dispositivo(spec)
        client = self._client_di(dove)
        if not client:
            _tipo, quale = dispositivo_di(dove)
            return _('Nodo {node} non disponibile: {get}', node=quale or dove,
                     get=self.node_errors.get(quale, ''))
        try:
            available = {a.get("nome") for a in client.actions()}
            if spec.action not in available:
                return _("Il nodo non offre l'azione '{action}'. Disponibili: {join}", action=spec.action, join=', '.join(sorted(available)))
            if dry_run:
                return _('Prova a vuoto: il nodo risponde e l\'azione c\'è, ma non è stata accesa.')
            # **La catena intera**, non il primo comando. Fino al 24/10 qui c'era
            # `client.activate(spec.action, durata)`: un comando solo, per cinque secondi, sul primo
            # dispositivo, senza suono e senza uscita. Marco, provando dalla scheda Azioni
            # un'azione di tre pezzi su due casse: *«si sente solo la sirena, e non per un secondo
            # ma di più, e non si sentono altri suoni»*. Era vero, e non era un difetto di quella
            # azione: il pulsante faceva un'altra cosa rispetto al «Prova» del wizard, e quindi
            # provava qualcosa che non esiste.
            # Stessa strada del «Prova» del wizard, **compreso il suo tetto**: ogni pezzo è
            # accorciato a PROVA_MAX_SECONDI, perché chi prova vuole sentire **cosa** fa e **in che
            # ordine**, non restare tre minuti con una sirena accesa dentro una finestra. Un pezzo
            # più corto del tetto — un suono da un secondo — resta quello che è: i tempi si rispettano.
            prova = dataclasses.replace(spec, duration=min(spec.duration, PROVA_MAX_SECONDI),
                                        _cache_blocchi=None)
            catena = self._annota(prova.catena(tetti_per=self._tetti_di, giro=self._giri.get(spec.name, 0)))
            if not catena:
                return _('Questa azione non ha pezzi da eseguire.')
            ev = types.SimpleNamespace(id="prova", camera_name=_('Prova'), camera_id="prova", score=1.0)
            if not self._esegui_passo(prova, ev, catena, 0, suono_predefinito=self._pick_sound(spec)):
                return _('Il nodo non ha eseguito il primo pezzo: guarda Stato e diagnosi.')
            self._avanza_giro(spec)
            return _('«{name}»: {n} passaggi, {s} s in tutto. Guarda (o ascolta) il nodo.',
                     name=spec.name, n=len(catena), s=int(quanto_dura(catena)))
        except Exception as exc:
            return _('Errore: {exc}', exc=exc)

    # --- limiti dei comandi ---------------------------------------------------
    def tipi_comandi(self, node_id: str) -> dict[str, str]:
        """Nome del comando → tipo, da come lo dichiara quel nodo. Vuoto se non lo si è ancora letto."""
        return dict(self.node_commands.get(str(node_id)) or {})

    # Qui stava «_contiene_dissuasore», scritta il 22/10 per impedire a un suono di dissuasione di
    # scattare per una persona. È durata un giorno, e l'ha smontata l'esempio di Marco: uno scavalca
    # la recinzione, una voce gli dice di allontanarsi, e se dopo dieci secondi è ancora lì parte la
    # sirena. Avvisare prima di urlare è la cosa più civile che questo programma possa fare, e quella
    # regola la vietava. Quello che resta è nei blocchi, e non parla di tipi: se nell'inquadratura
    # c'è una persona e il blocco le persone non le ha chieste, non scatta (vedi «blocco_scatta»).

    def _annota(self, catena: list[dict]) -> list[dict]:
        """Scrive su ogni chiamata il **tipo** del suo comando, chiesto al nodo che la eseguirà.

        Senza, i limiti di legge si calcolerebbero con i tipi di un nodo solo: lo stesso nome può
        essere una sirena di qua e un dissuasore di là, e il budget dei tre minuti finirebbe per
        contare il pezzo sbagliato (23/10, tappa 4).
        """
        for c in catena:
            tipo, quale = dispositivo_di(str(c.get("dispositivo") or ""))
            tipi = self.tipi_comandi(quale) if tipo == NODO and quale else {}
            c["tipo"] = str(tipi.get(str(c.get("comando")), ""))
        return catena

    def _primo_dispositivo(self, spec: "ActionSpec") -> str:
        """Il «posto solito» di quest'azione: il primo dei suoi dispositivi. Vuoto se non ne ha."""
        dove = spec.dispositivi()
        return dove[0] if dove else ""

    def _client_di(self, dispositivo: str):
        """Il nodo a cui mandare questa chiamata. ``None`` se non si sa dove, o se non c'è.

        Un dispositivo che non riusciamo a risolvere **non** ripiega su un altro: meglio un pezzo che
        non parte che una sirena che suona nella stanza sbagliata. Per ora l'unico dispositivo che
        esegue comandi è un nodo: «questo computer» sa ascoltare e avvisare, non ha morsetti.
        """
        tipo, quale = dispositivo_di(dispositivo)
        if tipo != NODO or not quale:
            return None
        return self.clients.get(quale)

    def _tetti_di(self, dispositivo: str) -> dict[str, float]:
        """I limiti per comando del dispositivo di **questo** pezzo: li dichiara il nodo che lo esegue."""
        tipo, quale = dispositivo_di(dispositivo)
        return self.tetti_comandi(quale) if tipo == NODO and quale else {}

    def tetti_comandi(self, node_id: str) -> dict[str, float]:
        """Nome del comando → secondi massimi, per i comandi che hanno un limite proprio.

        Finché un'azione faceva una cosa sola, il limite stava sul *tipo di azione* scelto in Mirto.
        In una catena non basta più: una luce e una sirena possono stare nello stesso elenco, e il
        limite della sirena non deve diventare quello della luce (né viceversa). Chi comanda qui è il
        nodo, che è l'unico a sapere cosa c'è attaccato ai suoi morsetti.
        """
        return {nome: LIMITI_PER_TIPO[tipo] for nome, tipo in self.tipi_comandi(node_id).items()
                if tipo in LIMITI_PER_TIPO}

    # --- stato ----------------------------------------------------------------
    def node_overview(self) -> list[dict]:
        """Per l'interfaccia: ogni nodo con stato, ultima lettura, azioni configurate e azioni in corso."""
        now = self.clock()
        out = []
        with self._lock:
            specs = list(self.specs)
        for nd in self.config.get("actions.nodes") or []:
            nid = str(nd["id"])
            info = dict(self.node_info.get(nid) or {})
            age = now - info.get("_at", now)
            active = {k: max(0.0, float(v) - age) for k, v in (info.get("attive") or {}).items()
                      if float(v) - age > 0}
            out.append({
                "id": nid, "name": nd.get("name") or nid, "url": nd.get("url", ""), "online": self.node_online.get(nid),
                "error": self.node_errors.get(nid, ""), "info": info, "active": active,
                "meta": {k: v for k, v in (self.node_meta.get(nid) or {}).items() if k != "camere"},
                # Le azioni che **toccano** questo nodo, non quelle che gli appartengono: dal 23/10
                # un'azione può accendere una luce qui e far suonare un altro nodo, e appartenere a
                # uno solo dei due vorrebbe dire sparire dalla pagina dell'altro.
                "actions": [{"name": s.name, "action": s.action, "kind": s.kind, "mode": s.mode,
                             "enabled": s.enabled} for s in specs
                            if ("nodo:" + nid) in s.dispositivi()],
            })
        return out

    def status_lines(self) -> list[str]:
        lines = [_('🔒 Protezione ATTIVA') if self.armed else _('🔓 Protezione disattivata')]
        nodes = self.config.get("actions.nodes") or []
        for nd in nodes:
            nid = str(nd["id"])
            if nid in self.node_errors and nid not in self.clients:
                lines.append("⚠️ " + _("Nodo {name}: {error}", name=nd.get('name', nid), error=self.node_errors[nid]))
            else:
                ok = self.node_online.get(nid)
                icon = "🟢" if ok else "🔴" if ok is False else "🟡"
                lines.append(_('{icon} Nodo {get}', icon=icon, get=nd.get('name', nid)))
        return lines

    def _audit(self, what: str, **info) -> None:
        rec = {"ora": time.strftime("%Y-%m-%d %H:%M:%S"), "evento": what, **info}
        try:
            with open(data_dir() / "azioni.log", "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        except Exception:
            log.exception("Registro azioni non scrivibile")
        log.info("Azioni: %s %s", what, info)


def enable_actions(core, **kwargs) -> ActionEngine:
    core.actions = ActionEngine(core, **kwargs)
    cams = getattr(core, "cameras", None)
    if cams is not None:  # le camere dei nodi chiedono al motore l'indirizzo attuale del nodo
        cams.node_resolver = core.actions.node_camera_url
        cams.node_hosts = core.actions.node_hosts
        cams.node_names = core.actions.node_names
    rec = getattr(core, "recording", None)
    if rec is not None:   # e chi registra chiede al motore se quel nodo sta pubblicando l'audio
        rec.audio_resolver = core.actions.node_audio_url
        for r in rec.recorders.values():
            r.audio_resolver = rec.audio_resolver
    return core.actions
