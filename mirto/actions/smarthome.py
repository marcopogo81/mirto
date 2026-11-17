# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Quello che Mirto sa della domotica di casa: il broker, i dispositivi, e come si comandano.

**La regola che decide tutto il resto: l'utente non configura, riconosce.** È lo stesso patto dei
nodi Raspberry — si accende il dispositivo, Mirto lo trova, tu lo riconosci dal nome. Nessuno sa cos'è
un «argomento MQTT», e chi lo sa non dovrebbe digitarlo. Perciò gli argomenti **non si chiedono mai**
come primo passo: si leggono.

**Le tre vie per trovare i dispositivi, in quest'ordine.**

1. **L'annuncio generico.** Chi è educato pubblica un messaggio *ritenuto* su
   ``homeassistant/<componente>/…/config`` che contiene nome, marca, e — la cosa che conta —
   **dove sta il suo stato e dove ascolta i comandi**. Mirto li legge, non li ricostruisce: è il
   senso dello standard, ed è il motivo per cui il giorno che ESPHome cambiasse i suoi argomenti qui
   non si romperebbe niente.
2. **La lingua della marca.** Zigbee2MQTT di base *non* fa l'annuncio generico, ma pubblica sempre
   l'elenco dei suoi dispositivi, ritenuto, su ``zigbee2mqtt/bridge/devices``. Da lì si deriva tutto:
   lo stato su ``zigbee2mqtt/<nome>``, i comandi su ``zigbee2mqtt/<nome>/set``. E la marca non si
   chiede a nessuno: si riconosce dagli argomenti che esistono sul broker.
3. **L'ascolto grezzo.** Per chi non fa né l'una né l'altra: Mirto sta a sentire tutto per qualche
   secondo e mostra **chi sta parlando**. Si sceglie a mano, e si adotta. Brutto, ma non è un vicolo
   cieco — e un vicolo cieco è la sola cosa che non ci si può permettere a casa di un estraneo.

**Niente adattatori per marche che il simulatore non sa imitare.** Tasmota, per dire, l'annuncio lo
fa solo dietro un'opzione e usa le chiavi abbreviate (``stat_t`` invece di ``state_topic``).
Scriverlo adesso vorrebbe dire consegnare a qualcun altro il compito di scoprirlo rotto: quando la
casa finta saprà fare un Tasmota, si scriverà, e non un minuto prima.

**Mirto legge e comanda, e basta.** Non apre nessuna porta: è lui a collegarsi al broker, in uscita,
come fa con Telegram. Che Mirto *obbedisca* a comandi presi dal broker è un'altra cosa, sta scritta
nel piano come passo a parte, ed è spenta.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from . import mqtt

log = logging.getLogger(__name__)

# Come un dispositivo dice e riceve lo stato.
SEMPLICE = "semplice"        # «ON» / «OFF» secchi: ESPHome e quasi tutti
JSON_STATO = "json_stato"    # {"state": "ON"}: Zigbee2MQTT

# Le marche che sappiamo riconoscere. «sconosciuta» è il dispositivo adottato a orecchio.
ESPHOME, ZIGBEE, SCONOSCIUTA = "esphome", "zigbee2mqtt", "sconosciuta"

# I due comandi che un dispositivo di casa sa fare, e si fermano qui: clima, serrande e tende hanno
# stati che non sono «acceso o spento» e nemmeno compaiono nell'elenco (vedi «COMPONENTI»).
#
# **Sono identificativi, non scritte.** Finiscono nella configurazione, dove devono restare uguali
# per sempre: una configurazione scritta in inglese non deve diventare illeggibile il giorno che si
# cambia lingua. Quello che si legge sullo schermo è la «descrizione», che si traduce dove si mostra.
ACCENDI, SPEGNI = "accendi", "spegni"
COMANDI = (ACCENDI, SPEGNI)

PREFISSO_ANNUNCIO = "homeassistant"
PREFISSO_ZIGBEE = "zigbee2mqtt"
ELENCO_ZIGBEE = f"{PREFISSO_ZIGBEE}/bridge/devices"

# L'annuncio generico ha due forme, con e senza il pezzo di mezzo che raggruppa i dispositivi di uno
# stesso apparecchio: «homeassistant/light/salotto/config» e «homeassistant/light/casa/salotto/config».
ARGOMENTI_ANNUNCIO = (f"{PREFISSO_ANNUNCIO}/+/+/config", f"{PREFISSO_ANNUNCIO}/+/+/+/config")

# Quello che si sente in giro e non è un dispositivo: roba di servizio, da non proporre mai.
NON_SONO_DISPOSITIVI = (f"{PREFISSO_ANNUNCIO}/", f"{PREFISSO_ZIGBEE}/bridge/", "mirto/", "$")

# I componenti di Home Assistant che sappiamo mostrare. Gli altri (clima, serrande, tende) hanno
# stati che non sono «acceso o spento», e fingere di saperli comandare sarebbe peggio di ignorarli.
COMPONENTI = {"light": "luce", "switch": "presa", "binary_sensor": "sensore", "sensor": "sensore"}


@dataclass
class Dispositivo:
    """Un dispositivo di casa, come Mirto lo ha capito.

    ``argomento_comando`` vuoto vuol dire che si guarda e basta: un sensore di movimento non si
    accende. ``acceso`` a ``None`` vuol dire «non lo so ancora»: è diverso da «spento», e mostrarlo
    come spento sarebbe una bugia a cui qualcuno crederebbe.
    """

    identificativo: str
    nome: str
    marca: str = SCONOSCIUTA
    tipo: str = "luce"
    argomento_stato: str = ""
    argomento_comando: str = ""
    argomento_vita: str = ""            # dove dice se è vivo, se lo dice
    forma: str = SEMPLICE
    parola_accesa: str = "ON"
    parola_spenta: str = "OFF"
    acceso: bool | None = None
    vivo: bool = True
    visto: float = field(default_factory=time.time)
    adottato: bool = False              # preso a orecchio: quello che sappiamo è un'ipotesi

    @property
    def comandabile(self) -> bool:
        return bool(self.argomento_comando)

    def carico_per(self, acceso: bool) -> bytes:
        parola = self.parola_accesa if acceso else self.parola_spenta
        if self.forma == JSON_STATO:
            return json.dumps({"state": parola}).encode()
        return parola.encode()

    def leggi(self, carico: bytes) -> bool | None:
        """Dal messaggio al nuovo stato, o ``None`` se non si capisce.

        Legge con larghezza di manica di proposito: un dispositivo adottato a orecchio dice le cose
        a modo suo, e rifiutare tutto quello che non è nella forma attesa vorrebbe dire mostrare
        «non lo so» a vita accanto a un dispositivo che sta parlando.
        """
        testo = carico.decode("utf-8", "replace").strip()
        if not testo:
            return None
        if testo[:1] in "{[":
            try:
                dentro = json.loads(testo)
            except ValueError:
                return None
            if not isinstance(dentro, dict):
                return None
            for chiave in ("state", "value", "POWER", "1"):
                if chiave in dentro:
                    return self._parola(dentro[chiave])
            return None
        return self._parola(testo)

    def _parola(self, valore) -> bool | None:
        if isinstance(valore, bool):
            return valore
        parola = str(valore).strip()
        if parola.upper() == str(self.parola_accesa).upper():
            return True
        if parola.upper() == str(self.parola_spenta).upper():
            return False
        if parola.upper() in ("ON", "TRUE", "1", "OPEN", "DETECTED"):
            return True
        if parola.upper() in ("OFF", "FALSE", "0", "CLOSED", "CLEAR"):
            return False
        return None


def _nome_decente(grezzo: str, ripiego: str) -> str:
    """Un nome che si possa leggere in un elenco, senza trattini bassi né argomenti interi."""
    nome = (grezzo or "").strip()
    if not nome:
        nome = ripiego.rsplit("/", 1)[-1]
    nome = nome.replace("_", " ").strip()
    return nome[:1].upper() + nome[1:] if nome else ripiego


class CasaSmart:
    """Il collegamento al broker e i dispositivi che ci si trovano.

    ``su_cambio(dispositivo)`` viene richiamato **dal filo che legge dal broker**: chi lo usa da
    un'interfaccia deve rimbalzare il lavoro sul filo giusto, come si fa per gli avvisi dei nodi.
    Vale sia quando compare un dispositivo nuovo, sia quando uno cambia stato.
    """

    def __init__(self, indirizzo: str, porta: int = 1883, *, utente: str | None = None,
                 password: str | None = None, tls: bool = False,
                 su_cambio: Callable[[Dispositivo], None] | None = None,
                 nome: str = "mirto",
                 testamento: tuple[str, bytes, bool] | None = None) -> None:
        self.indirizzo, self.porta = indirizzo, porta
        self.utente, self.password, self.tls = utente, password, tls
        self.su_cambio = su_cambio
        self.nome = nome
        # Cosa dire se Mirto spira. Si dichiara **al collegamento**, perché lo manda il broker e non
        # Mirto: un messaggio che Mirto manda da sé, se è morto, non lo manda. Resta `None` quando il
        # racconto è spento, e allora Mirto non apre bocca su questo broker (passo 5).
        self.testamento = testamento
        self._cliente: mqtt.Cliente | None = None
        self._dispositivi: dict[str, Dispositivo] = {}
        self._per_argomento: dict[str, list[str]] = {}    # argomento -> identificativi
        self._chi_parla: dict[str, bytes] = {}            # l'ascolto grezzo
        self._grezzo = False
        self._lucchetto = threading.RLock()

    # --- vita -----------------------------------------------------------------
    def collega(self, secondi: float = 5.0) -> "CasaSmart":
        """Si collega e si mette in ascolto delle prime due vie. Solleva ``mqtt.ErroreMqtt``."""
        self._cliente = mqtt.Cliente(
            self.indirizzo, self.porta, nome=self.nome, utente=self.utente, password=self.password,
            tls=self.tls, riprova=True, su_messaggio=self._arrivato,
            testamento=self.testamento).collega(secondi)
        self._cliente.iscrivi(*ARGOMENTI_ANNUNCIO, ELENCO_ZIGBEE)
        return self

    def chiudi(self) -> None:
        if self._cliente is not None:
            self._cliente.chiudi()
            self._cliente = None

    def collegata(self) -> bool:
        return self._cliente is not None and self._cliente.collegato()

    def pubblica(self, argomento: str, carico: bytes, ritenuto: bool = False) -> bool:
        """Mette un messaggio sul broker. False se non siamo collegati, senza sollevare.

        Serve al racconto di Mirto (passo 5) e passa dallo **stesso collegamento** che ascolta la
        casa: due collegamenti allo stesso broker sarebbero due cose da tenere in piedi, due modi di
        cadere e due righe da capire nel pannello Sistema, per nessun guadagno.
        """
        cliente = self._cliente
        if cliente is None or not cliente.collegato():
            return False
        cliente.pubblica(argomento, carico, ritenuto=ritenuto)
        return True

    def __enter__(self) -> "CasaSmart":
        return self.collega()

    def __exit__(self, *_: object) -> None:
        self.chiudi()

    # --- cosa c'è in casa -----------------------------------------------------
    def dispositivi(self) -> list[Dispositivo]:
        """In ordine di nome: in un elenco che cresce mentre guardi, l'ordine d'arrivo disorienta."""
        with self._lucchetto:
            return sorted(self._dispositivi.values(), key=lambda d: d.nome.lower())

    def dispositivo(self, identificativo: str) -> Dispositivo | None:
        with self._lucchetto:
            return self._dispositivi.get(identificativo)

    def comanda(self, identificativo: str, acceso: bool) -> bool:
        """Accende o spegne. Falso se non esiste, non si comanda, o il broker non c'è."""
        d = self.dispositivo(identificativo)
        if d is None or not d.comandabile or self._cliente is None:
            return False
        try:
            self._cliente.pubblica(d.argomento_comando, d.carico_per(acceso))
        except mqtt.ErroreMqtt as exc:
            log.warning("smarthome: comando a %s non partito (%s)", identificativo, exc)
            return False
        return True

    # --- la terza via: l'ascolto grezzo ---------------------------------------
    def ascolta_tutto(self, acceso: bool = True) -> None:
        """Sta a sentire **tutto** quello che passa, per trovare chi non si annuncia.

        Si accende a mano e si spegne: iscriversi a ``#`` per sempre vuol dire tenersi in casa ogni
        messaggio di ogni dispositivo, comprese le istantanee se qualcuno ne pubblica.
        """
        if self._cliente is None or acceso == self._grezzo:
            return
        self._grezzo = acceso
        if acceso:
            self._cliente.iscrivi("#")
        else:
            self._cliente.disiscrivi("#")

    def chi_parla(self) -> list[tuple[str, bytes]]:
        """Gli argomenti sentiti passare che non sono di nessun dispositivo già noto."""
        with self._lucchetto:
            noti = set(self._per_argomento)
            return sorted((a, c) for a, c in self._chi_parla.items() if a not in noti)

    def adotta(self, argomento: str, nome: str, argomento_comando: str | None = None) -> Dispositivo:
        """Prende un argomento sentito a orecchio e ne fa un dispositivo.

        Il comando si immagina: dove un dispositivo dice il suo stato, quasi sempre ascolta gli
        ordini su ``<argomento>/set``. È un'ipotesi, e il dispositivo resta segnato come
        ``adottato`` proprio per questo: nell'elenco va detto che non si è annunciato da sé.
        """
        comando = argomento_comando if argomento_comando is not None else f"{argomento}/set"
        d = Dispositivo(identificativo=f"grezzo:{argomento}", nome=nome, marca=SCONOSCIUTA,
                        argomento_stato=argomento, argomento_comando=comando, adottato=True)
        # Lo stato di partenza lo mette «_metti», che lo ripesca da quello che si è già sentito
        # passare: qui lo facevo una seconda volta, e il doppione teneva in piedi una prova finta.
        self._metti(d)
        return d

    def dimentica(self, identificativo: str) -> bool:
        with self._lucchetto:
            d = self._dispositivi.pop(identificativo, None)
            if d is None:
                return False
            for argomento, chi in list(self._per_argomento.items()):
                restano = [i for i in chi if i != identificativo]
                if restano:
                    self._per_argomento[argomento] = restano
                else:
                    self._per_argomento.pop(argomento, None)
        return True

    # --- quello che arriva ----------------------------------------------------
    def _arrivato(self, argomento: str, carico: bytes, _ritenuto: bool) -> None:
        try:
            if argomento.startswith(f"{PREFISSO_ANNUNCIO}/") and argomento.endswith("/config"):
                self._annuncio(argomento, carico)
                return
            if argomento == ELENCO_ZIGBEE:
                self._elenco_zigbee(carico)
                return
            if self._stato_di_qualcuno(argomento, carico):
                return
            if self._grezzo and not argomento.startswith(NON_SONO_DISPOSITIVI):
                with self._lucchetto:
                    self._chi_parla[argomento] = carico
        except Exception:  # noqa: BLE001 - un messaggio storto non deve spegnere l'ascolto
            log.exception("smarthome: messaggio su %s non trattabile", argomento)

    def _stato_di_qualcuno(self, argomento: str, carico: bytes) -> bool:
        with self._lucchetto:
            interessati = list(self._per_argomento.get(argomento, ()))
        toccati = []
        for identificativo in interessati:
            d = self.dispositivo(identificativo)
            if d is None:
                continue
            if argomento == d.argomento_vita:
                vivo = carico.decode("utf-8", "replace").strip().lower() not in ("offline", "")
                if vivo != d.vivo:
                    d.vivo, d.visto = vivo, time.time()
                    toccati.append(d)
                continue
            nuovo = d.leggi(carico)
            if nuovo is None:
                continue
            d.visto = time.time()
            if nuovo != d.acceso:
                d.acceso = nuovo
                toccati.append(d)
        for d in toccati:
            self._dillo(d)
        return bool(interessati)

    def _annuncio(self, argomento: str, carico: bytes) -> None:
        """Via 1. Un annuncio vuoto vuol dire che quel dispositivo non c'è più: è così che si cancella."""
        pezzi = argomento.split("/")
        componente = pezzi[1] if len(pezzi) > 2 else ""
        if not carico:
            self.dimentica(f"annuncio:{argomento}")
            return
        try:
            dentro = json.loads(carico.decode("utf-8", "replace"))
        except ValueError:
            log.info("smarthome: annuncio illeggibile su %s", argomento)
            return
        if not isinstance(dentro, dict) or componente not in COMPONENTI:
            return
        stato = str(dentro.get("state_topic") or "")
        if not stato:
            return                      # senza il suo stato non c'è niente da mostrare
        d = Dispositivo(
            identificativo=f"annuncio:{argomento}",
            nome=_nome_decente(str(dentro.get("name") or ""), argomento),
            marca=self._marca_dall_annuncio(dentro),
            tipo=COMPONENTI[componente],
            argomento_stato=stato,
            argomento_comando=str(dentro.get("command_topic") or ""),
            argomento_vita=str(dentro.get("availability_topic") or ""),
            parola_accesa=str(dentro.get("payload_on") or "ON"),
            parola_spenta=str(dentro.get("payload_off") or "OFF"),
        )
        self._metti(d)

    @staticmethod
    def _marca_dall_annuncio(dentro: dict) -> str:
        chi = str((dentro.get("device") or {}).get("manufacturer") or "")
        return ESPHOME if "esphome" in chi.lower() else (chi or SCONOSCIUTA)

    def _elenco_zigbee(self, carico: bytes) -> None:
        """Via 2. L'elenco è una fotografia: chi non c'è più va tolto, non lasciato lì."""
        try:
            elenco = json.loads(carico.decode("utf-8", "replace"))
        except ValueError:
            return
        if not isinstance(elenco, list):
            return
        visti = set()
        for voce in elenco:
            if not isinstance(voce, dict):
                continue
            nome = str(voce.get("friendly_name") or "").strip()
            if not nome or nome.startswith("bridge"):
                continue
            identificativo = f"zigbee:{nome}"
            visti.add(identificativo)
            self._metti(Dispositivo(
                identificativo=identificativo,
                nome=_nome_decente(nome, nome),
                marca=ZIGBEE,
                tipo="sensore" if not self._zigbee_si_comanda(voce) else "luce",
                argomento_stato=f"{PREFISSO_ZIGBEE}/{nome}",
                argomento_comando=(f"{PREFISSO_ZIGBEE}/{nome}/set"
                                   if self._zigbee_si_comanda(voce) else ""),
                forma=JSON_STATO,
            ))
        with self._lucchetto:
            spariti = [i for i in self._dispositivi if i.startswith("zigbee:") and i not in visti]
        for i in spariti:
            self.dimentica(i)

    @staticmethod
    def _zigbee_si_comanda(voce: dict) -> bool:
        """Zigbee2MQTT dice cosa si può scrivere con un bit del campo «access» (il 2, che vale 2)."""
        for esposto in (voce.get("definition") or {}).get("exposes") or []:
            if not isinstance(esposto, dict):
                continue
            for pezzo in esposto.get("features") or [esposto]:
                if isinstance(pezzo, dict) and pezzo.get("property") == "state":
                    return bool(int(pezzo.get("access") or 0) & 0b10)
        return False

    # --- tenere il registro ---------------------------------------------------
    def _metti(self, d: Dispositivo) -> None:
        """Mette o aggiorna un dispositivo, e si iscrive a dove parla."""
        with self._lucchetto:
            vecchio = self._dispositivi.get(d.identificativo)
            if vecchio is not None:
                # si riannuncia: lo stato che sapevamo non si butta via, è l'unica cosa che abbiamo
                d.acceso, d.vivo = vecchio.acceso, vecchio.vivo
            self._dispositivi[d.identificativo] = d
            nuovi = []
            for argomento in (d.argomento_stato, d.argomento_vita):
                if not argomento:
                    continue
                chi = self._per_argomento.setdefault(argomento, [])
                if d.identificativo not in chi:
                    chi.append(d.identificativo)
                if len(chi) == 1:
                    nuovi.append(argomento)
            ultimo = self._chi_parla.get(d.argomento_stato)
        if ultimo and d.acceso is None:
            # lo avevamo già sentito parlare durante l'ascolto grezzo: non ripartire da «non so»
            d.acceso = d.leggi(ultimo)
        if nuovi and self._cliente is not None:
            self._cliente.iscrivi(*nuovi)
        if vecchio is None or vecchio.nome != d.nome:
            self._dillo(d)

    def _dillo(self, d: Dispositivo) -> None:
        if self.su_cambio:
            try:
                self.su_cambio(d)
            except Exception:  # noqa: BLE001 - chi ascolta non deve poter spegnere l'ascolto
                log.exception("smarthome: su_cambio è esploso per %s", d.identificativo)


@dataclass
class CambioStato:
    """Un dispositivo di casa ha cambiato stato: è un evento, come quello di una camera.

    Serve a far scattare le azioni (passo 3 del piano domotica). Porta la chiave col prefisso —
    ``smart:luce_scale`` — e non l'identificativo nudo, perché è così che un blocco scrive dove
    ascolta, e confrontare due cose scritte nello stesso modo è l'unico confronto che non si sbaglia.

    **Un cambio, non una lettura.** Quando Mirto si collega, il broker gli riconsegna lo stato di
    tutti i dispositivi: imparare che la luce è accesa non è «la luce si è accesa». Chi costruisce
    questi eventi (il motore) deve saperlo, o chi accende Mirto la sera con una luce accesa si sente
    partire la musica di benvenuto. Qui non si può distinguere: lo sa solo chi ricorda il prima.
    """

    dispositivo: str            # «smart:<identificativo>», come lo scrive un blocco
    nome: str                   # come si chiama, per i messaggi
    acceso: bool
    id: str = ""                # perché somigli a un evento di camera nei messaggi e nel controllo

    def targets(self) -> set:
        """Cosa «ha riconosciuto»: il verso del cambio. Tiene insieme il modello con le camere."""
        return {"acceso" if self.acceso else "spento"}

    @property
    def score(self) -> float:
        """Un cambio di stato è certo: non è un riconoscimento, è una notizia."""
        return 1.0

    @property
    def camera_name(self) -> str:
        """Non viene da una camera, e i messaggi che chiedono «dove» devono dire il dispositivo."""
        return self.nome


def chiave_di(identificativo: str) -> str:
    """``"luce_scale"`` → ``"smart:luce_scale"``: come un dispositivo smart si scrive in un blocco."""
    return "smart:" + str(identificativo or "").strip()


def dispositivi_per_le_azioni(dispositivi) -> list[dict]:
    """I dispositivi di casa nella forma che il wizard già conosce: ``{chiave, nome}``.

    La stessa forma dei nodi (``{"chiave": "nodo:n1", "nome": "Ingresso"}``), perché per chi
    configura un'azione una lampadina smart e un relè di un nodo sono la stessa cosa: un posto dove
    far succedere qualcosa. **Solo i comandabili**: un sensore di movimento non si accende, e
    offrirlo vorrebbe dire lasciar costruire un pezzo che non partirà mai — la stessa ragione per cui
    «questo computer» non è nell'elenco delle uscite.
    """
    return [{"chiave": chiave_di(d.identificativo), "nome": d.nome or d.identificativo}
            for d in (dispositivi or []) if d.comandabile]


def comandi_di(dispositivo) -> list[dict]:
    """I comandi di un dispositivo di casa, nella forma in cui il nodo dichiara i suoi.

    Un nodo dice *«ho un comando che si chiama luce_ingresso, di tipo light»*; qui si dice la stessa
    cosa di una lampadina smart, e il wizard non deve imparare niente di nuovo. Il ``tipo`` è quello
    del nodo (``light``, ``generic``) e non il nostro (``luce``, ``presa``): è il vocabolario che la
    striscia dei pezzi già parla, e tradurlo qui costa due righe invece di un ramo in più là.

    **Nessun limite di legge.** I tetti dei tre minuti nascono dal tipo dichiarato dal nodo, che
    sulle sue morsettiere sa cosa c'è attaccato; una presa smart non lo dichiara e nessuno può
    saperlo da qui. Chi attacca una sirena a una presa smart ha una sirena senza budget, e **va
    scritto in guida** invece di finto-risolto con un tetto inventato.
    """
    from ..i18n import _  # noqa: PLC0415

    if not getattr(dispositivo, "comandabile", False):
        return []
    nome = dispositivo.nome or dispositivo.identificativo
    tipo = "light" if dispositivo.tipo == "luce" else "generic"
    return [
        {"nome": ACCENDI, "tipo": tipo, "descrizione": _('Accendi {nome}', nome=nome),
         "dispositivo": chiave_di(dispositivo.identificativo)},
        {"nome": SPEGNI, "tipo": tipo, "descrizione": _('Spegni {nome}', nome=nome),
         "dispositivo": chiave_di(dispositivo.identificativo)},
    ]


class Domotica:
    """Tiene in piedi il collegamento alla domotica per tutta la vita del programma.

    **Perché esiste, e perché non sta nelle impostazioni.** Il collegamento era nato dentro la
    scheda *SmartHome*: si configurava, funzionava, si chiudeva la finestra e spariva tutto. Il
    difetto non era il salvataggio — era il posto. Una cosa che deve restare viva mentre Mirto
    lavora non può vivere dentro una finestra, come non ci vivono i nodi né la presenza.

    E non è solo per ricordarsene: dal pannello *Sistema* si deve vedere se la domotica c'è, e le
    Azioni dovranno comandare le luci quando nessuna finestra è aperta.

    Si riapre da sé quando le impostazioni cambiano: ``riapri()``. Non solleva mai — se il broker di
    casa è spento, Mirto deve partire lo stesso, e dirlo.
    """

    def __init__(self, config, secrets) -> None:
        self.config, self.secrets = config, secrets
        self.casa: CasaSmart | None = None
        # Il racconto di Mirto sul broker (passo 5), oppure `None` — e `None` è il caso normale,
        # perche' e' spento finche' non lo accendi. Lo tiene questo oggetto e non il motore, perche'
        # deve dire «sono qui» appena il collegamento c'e', e «me ne vado» prima che sparisca:
        # tutte e due le cose le sa solo chi apre e chiude il collegamento.
        self.racconto = None
        self.errore = ""
        # Le camere di adesso, come coppie `(id, nome)`. Le sa il nucleo, non questo oggetto: qui si
        # tengono solo per poter ripubblicare la scoperta quando il collegamento torna dopo una
        # caduta, senza dover chiedere a nessuno.
        self._camere: list[tuple[str, str]] = []
        self._lucchetto = threading.RLock()
        self._ascoltatori: list[Callable[[Dispositivo], None]] = []

    # --- vita -----------------------------------------------------------------
    def start(self) -> None:
        self.riapri()

    def stop(self) -> None:
        with self._lucchetto:
            casa, self.casa = self.casa, None
            racconto, self.racconto = self.racconto, None
        # «Me ne vado» **prima** di chiudere, e non si conta sul testamento: quello lo manda il
        # broker quando si accorge che il collegamento e' caduto, e puo' volerci un keepalive —
        # chiudendo per bene, Home Assistant lo sa subito invece che fra un minuto.
        if racconto is not None:
            racconto.basta()        # i timer in attesa non tengono in piedi Mirto che si chiude
        if racconto is not None and casa is not None:
            racconto.me_ne_vado()
        if casa is not None:
            casa.chiudi()

    def riapri(self) -> bool:
        """Richiude e riapre col quello che c'è scritto adesso nelle impostazioni."""
        from ..credentials import SMARTHOME_PASSWORD  # noqa: PLC0415

        self.stop()
        self.errore = ""
        dati = self.config.get("smarthome") or {}
        host = str(dati.get("host") or "").strip()
        if not dati.get("enabled") or not host:
            return False
        from .racconto import PREFISSO, Racconto, testamento  # noqa: PLC0415

        # Il racconto e' una scelta a parte dal collegamento: chi vuole solo accendere le luci di
        # casa non deve ritrovarsi lo stato del suo antifurto su una bacheca condivisa per aver
        # configurato un broker. L'ha scritto `PRIVACY.md` il 17/11, in due lingue.
        parla = bool(dati.get("publish"))
        prefisso = str(dati.get("prefix") or PREFISSO).strip("/ ") or PREFISSO
        try:
            casa = CasaSmart(host, int(dati.get("port") or 1883),
                             utente=str(dati.get("username") or "") or None,
                             password=self.secrets.get(SMARTHOME_PASSWORD) or None,
                             tls=bool(dati.get("tls")),
                             su_cambio=self._dillo_a_tutti,
                             testamento=testamento(prefisso) if parla else None).collega()
        except Exception as exc:  # noqa: BLE001 - un broker spento non deve impedire l'avvio
            self.errore = str(exc)
            log.warning("domotica: non collegata a %s (%s)", host, exc)
            return False
        for voce in dati.get("adopted") or []:
            try:
                casa.adotta(str(voce.get("topic") or ""), str(voce.get("name") or ""),
                            voce.get("command_topic"))
            except Exception:  # noqa: BLE001
                log.exception("domotica: non ho potuto riprendere %s", voce)
        with self._lucchetto:
            self.casa = casa
            self.racconto = (Racconto(casa.pubblica, prefisso,
                                      istantanee=bool(dati.get("publish_snapshots")),
                                      quanto_dura=float(dati.get("camera_state_seconds") or 0))
                             if parla else None)
        if self.racconto is not None:
            self.racconto.sono_qui()
            self.racconto.protezione(bool(self.config.get("actions.armed", False)))
            # Si fa trovare da Home Assistant (passo 6). Va fatto **dopo** `sono_qui()`: le voci
            # dicono dove si vede se Mirto c'è, e un Home Assistant che leggesse la configurazione
            # prima dello stato mostrerebbe per un istante dei sensori «non disponibili».
            self.scopri(self._camere)
        else:
            # Il racconto è spento: se avevamo pubblicato, ci si toglie di torno invece di lasciare
            # in Home Assistant dei sensori che non parleranno mai più.
            self._dimenticami(casa, prefisso)
        return True

    def scopri(self, camere) -> int:
        """Si fa trovare da Home Assistant per queste camere. Torna quante voci ha pubblicato.

        ``camere`` sono coppie ``(id, nome)``: l'**id** fa l'identificativo univoco, che deve restare
        uguale per sempre, e il **nome** l'etichetta. Le sa il nucleo, che chiama questo metodo
        quando una camera arriva o se ne va.
        """
        from . import scoperta  # noqa: PLC0415

        with self._lucchetto:
            self._camere = [(str(a), str(b)) for a, b in (camere or [])]
            racconto, casa, elenco = self.racconto, self.casa, list(self._camere)
        if racconto is None or casa is None:
            return 0
        from .. import __version__  # noqa: PLC0415

        quante = 0
        for argomento, carico, ritenuto in scoperta.voci(
                racconto.prefisso, elenco, __version__, racconto.istantanee):
            quante += 1 if casa.pubblica(argomento, carico, ritenuto) else 0
        if quante:
            # Si segna **in configurazione, con l'elenco dentro**, e non solo in memoria. Il giorno
            # che il racconto si spegne bisogna andare a cancellare, e a quel punto l'elenco in
            # memoria è vuoto — perché chi spegne il racconto lo fa tipicamente dopo un riavvio.
            # Senza l'elenco scritto resterebbero orfani i sensori delle camere e si cancellerebbe
            # solo quello della protezione: il caso più facile, fatto peggio.
            self.config.set("homeassistant.published", True)
            self.config.set("homeassistant.cameras", [[a, b] for a, b in elenco])
        return quante

    def _dimenticami(self, casa, prefisso: str) -> None:
        """Cancella le voci della scoperta, **solo se le avevamo pubblicate**.

        Succede quando si spegne il racconto: senza questo, in Home Assistant resterebbero i sensori
        di Mirto per sempre, grigi e non cancellabili dalla sua interfaccia — bisognerebbe andarli a
        togliere dal broker con un client MQTT. Lasciare quel lavoro a qualcun altro per
        risparmiarsi dieci righe è il genere di cosa che si giudica male, e giustamente.

        Il segno in configurazione serve anche al verso opposto: non scrivere messaggi vuoti sul
        broker di chi non ha mai acceso niente. Cancellare è gentile; scrivere a casa di chi non ha
        chiesto niente no.
        """
        from . import scoperta  # noqa: PLC0415

        if not self.config.get("homeassistant.published", False):
            return
        # Dall'elenco scritto, non da quello in memoria: vedi `scopri()`.
        camere = [(str(c[0]), str(c[1])) for c in (self.config.get("homeassistant.cameras") or [])
                  if isinstance(c, (list, tuple)) and len(c) >= 2]
        for argomento, carico, ritenuto in scoperta.cancellazioni(prefisso, camere):
            casa.pubblica(argomento, carico, ritenuto)
        self.config.set("homeassistant.published", False)
        self.config.set("homeassistant.cameras", [])

    # --- chi vuole saperlo ----------------------------------------------------
    def ascolta(self, chi: Callable[[Dispositivo], None]) -> None:
        """Si fa avvisare quando un dispositivo cambia. Serve alle Azioni e all'interfaccia."""
        with self._lucchetto:
            if chi not in self._ascoltatori:
                self._ascoltatori.append(chi)

    def non_ascoltare(self, chi) -> None:
        with self._lucchetto:
            if chi in self._ascoltatori:
                self._ascoltatori.remove(chi)

    def _dillo_a_tutti(self, d: Dispositivo) -> None:
        with self._lucchetto:
            chiunque = list(self._ascoltatori)
        for chi in chiunque:
            try:
                chi(d)
            except Exception:  # noqa: BLE001 - chi ascolta non deve poter spegnere l'ascolto
                log.exception("domotica: un ascoltatore è esploso")

    # --- per il pannello Sistema ----------------------------------------------
    def dispositivi(self) -> list[Dispositivo]:
        casa = self.casa
        return casa.dispositivi() if casa is not None else []

    def comanda(self, identificativo: str, acceso: bool) -> bool:
        casa = self.casa
        return bool(casa is not None and casa.comanda(identificativo, acceso))

    def stato(self) -> dict:
        """Quello che il riquadro «Sistema» deve mostrare, in una riga sola."""
        dati = self.config.get("smarthome") or {}
        casa = self.casa
        dispositivi = casa.dispositivi() if casa is not None else []
        return {
            "attiva": bool(dati.get("enabled")) and bool(str(dati.get("host") or "").strip()),
            "host": str(dati.get("host") or ""),
            "collegata": bool(casa is not None and casa.collegata()),
            "quanti": len(dispositivi),
            "accesi": sum(1 for d in dispositivi if d.acceso),
            "muti": sum(1 for d in dispositivi if not d.vivo),
            "errore": self.errore,
        }
