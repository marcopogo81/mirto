# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Una casa smart finta: dispositivi che si comportano come le marche vere, e il broker dentro.

**Perché esiste.** Nessuno di noi ha un impianto domotico. Senza questo file la scheda SmartHome di
Mirto si scriverebbe alla cieca e si scoprirebbe rotta a casa di qualcun altro. Con questo file si
prova tutto, su ``127.0.0.1``, senza installare niente e senza comprare niente.

**Le tre marche, e perché proprio queste.** Non sono tre nomi scelti per fare scena: sono i tre modi
davvero diversi in cui un dispositivo si fa trovare, e per ciascuno Mirto deve avere una risposta.

* **ESPHome** — *il bravo.* Pubblica un annuncio **ritenuto** su ``homeassistant/<tipo>/…/config`` e
  dentro l'annuncio **dice lui dove stanno il suo stato e i suoi comandi**. Chi lo legge non deve
  indovinare nessun argomento, e per questo i nostri argomenti qui sotto possono anche non combaciare
  al byte con quelli di un ESPHome vero: quello che conta, e che Mirto deve fare, è **leggere
  l'annuncio invece di presumere la forma**. Se un giorno ESPHome cambiasse i suoi argomenti, Mirto
  continuerebbe a funzionare, e questo è il motivo per cui lo standard esiste.
* **Zigbee2MQTT** — *il silenzioso.* L'annuncio generico di base **non lo fa** (``homeassistant:
  enabled`` è falso), quindi chi cerca solo quello non vede niente. Ma pubblica **sempre** l'elenco dei
  suoi dispositivi, ritenuto, su ``zigbee2mqtt/bridge/devices``, lo stato su ``zigbee2mqtt/<nome>`` e
  accetta comandi su ``zigbee2mqtt/<nome>/set``. Verificato sulla sua documentazione il 14/11/2026: è
  la via 2 del piano, e funziona. Qui gli argomenti sono quelli veri, perché qui Mirto li **deriva**.
* **Il maleducato** — non si annuncia e non parla nessuna lingua che conosciamo: argomenti inventati,
  carichi inventati. Si prende solo con l'ascolto grezzo e si configura a mano. Esiste perché senza di
  lui scriveremmo una scheda SmartHome che funziona solo con i dispositivi gentili, e la terza via del
  piano non la proveremmo mai.

**Il broker se lo porta dentro** (``broker.py``), perché un simulatore che prima ti chiede di
installare Mosquitto non simula niente. Si può però puntare a un broker vero, e allora si prova anche
quello.
"""

from __future__ import annotations

import errno
import json
import logging
import threading
from collections.abc import Callable

from . import mqtt
from .broker import Broker

log = logging.getLogger(__name__)

ESPHOME, ZIGBEE, MALEDUCATO = "esphome", "zigbee2mqtt", "maleducato"
MARCHE = (ESPHOME, ZIGBEE, MALEDUCATO)

# Come si chiamano le marche per chi guarda la finestra, e cosa comportano.
DESCRIZIONE_MARCHE = {
    ESPHOME: "si annuncia da sé (Mirto lo trova subito)",
    ZIGBEE: "non si annuncia, ma parla la sua lingua (Mirto lo trova dal suo elenco)",
    MALEDUCATO: "non si annuncia e non parla nessuna lingua nota (va preso a orecchio)",
}

# «Indirizzo già in uso» ha un numero diverso per ogni sistema: 48 su macOS, 98 su Linux, 10048 su
# Windows. Scritti a mano, i primi due passavano e il terzo no — e la CI del 15/11 l'ha scoperto
# sui soli lavori Windows, che e' l'unico posto dove nessuno di noi due guarda. I nomi di «errno»
# li sanno tutti e tre, e non si sbagliano.
OCCUPATA = {errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", errno.EADDRINUSE)}

LUCE, PRESA, SENSORE = "luce", "presa", "sensore"
TIPI = (LUCE, PRESA, SENSORE)
# Come si chiama questo tipo di cosa nel linguaggio di Home Assistant, che è quello dell'annuncio.
COMPONENTE_HA = {LUCE: "light", PRESA: "switch", SENSORE: "binary_sensor"}

PREFISSO_ANNUNCIO = "homeassistant"
PREFISSO_ZIGBEE = "zigbee2mqtt"


class DispositivoFinto:
    """Un dispositivo. La marca decide **come si fa trovare** e **come si fa comandare**, non altro.

    ``acceso`` è lo stato. Un sensore non si comanda: cambia da sé (o lo cambia una mano nella
    finestra del simulatore), e questo è il verso che serve a provare i trigger.
    """

    def __init__(self, identificativo: str, nome: str, tipo: str = LUCE,
                 marca: str = ESPHOME, acceso: bool = False) -> None:
        if tipo not in TIPI:
            raise ValueError(f"tipo sconosciuto: {tipo}")
        if marca not in MARCHE:
            raise ValueError(f"marca sconosciuta: {marca}")
        self.identificativo, self.nome = identificativo, nome
        self.tipo, self.marca, self.acceso = tipo, marca, acceso

    @property
    def comandabile(self) -> bool:
        return self.tipo != SENSORE

    def __repr__(self) -> str:   # pragma: no cover - comodità nel debug
        return f"<{self.marca} {self.tipo} {self.identificativo} {'acceso' if self.acceso else 'spento'}>"

    # --- dove parla, secondo la sua marca -------------------------------------
    def argomento_stato(self) -> str:
        if self.marca == ESPHOME:
            return f"{self.identificativo}/{COMPONENTE_HA[self.tipo]}/{self.identificativo}/state"
        if self.marca == ZIGBEE:
            return f"{PREFISSO_ZIGBEE}/{self.nome}"
        return f"tuya/{self.identificativo}/dps"            # il maleducato

    def argomento_comando(self) -> str | None:
        if not self.comandabile:
            return None
        if self.marca == ESPHOME:
            return f"{self.identificativo}/{COMPONENTE_HA[self.tipo]}/{self.identificativo}/command"
        if self.marca == ZIGBEE:
            return f"{PREFISSO_ZIGBEE}/{self.nome}/set"
        return f"tuya/{self.identificativo}/dps/set"        # il maleducato

    def argomenti_da_ascoltare(self) -> list[str]:
        """Tutti i modi in cui questo dispositivo accetta ordini."""
        comando = self.argomento_comando()
        if comando is None:
            return []
        if self.marca == ZIGBEE:
            # Zigbee2MQTT accetta il JSON su «/set» e, come scorciatoia, «/set/state» con «ON» secco
            return [comando, f"{comando}/state"]
        return [comando]

    # --- come dice il suo stato, secondo la sua marca -------------------------
    def carico_stato(self) -> bytes:
        if self.marca == ESPHOME:
            return b"ON" if self.acceso else b"OFF"
        if self.marca == ZIGBEE:
            return json.dumps({"state": "ON" if self.acceso else "OFF"}).encode()
        return json.dumps({"1": self.acceso}).encode()      # il maleducato: «1» vuol dire chissà cosa

    def leggi_comando(self, argomento: str, carico: bytes) -> bool | None:
        """Da un comando arrivato al nuovo stato, o ``None`` se non si capisce.

        Accetta anche le forme che le marche vere accettano e che si tende a non provare: ``ON``
        secco, ``{"state": "ON"}``, e la scorciatoia ``/set/state`` di Zigbee2MQTT.
        """
        testo = carico.decode("utf-8", "replace").strip()
        if not testo:
            return None
        if testo[:1] in "{[":
            try:
                dentro = json.loads(testo)
            except ValueError:
                return None
            if isinstance(dentro, dict):
                for chiave in ("state", "1"):
                    if chiave in dentro:
                        return self._parola(dentro[chiave])
            return None
        return self._parola(testo)

    @staticmethod
    def _parola(valore) -> bool | None:
        if isinstance(valore, bool):
            return valore
        parola = str(valore).strip().upper()
        if parola in ("ON", "TRUE", "1", "ACCESA", "ACCESO"):
            return True
        if parola in ("OFF", "FALSE", "0", "SPENTA", "SPENTO"):
            return False
        return None

    # --- come si annuncia, se si annuncia ------------------------------------
    def annuncio(self) -> tuple[str, bytes] | None:
        """L'annuncio generico ritenuto: ``(argomento, carico)``, oppure ``None`` se non si annuncia.

        Solo ESPHome. Ed è l'annuncio stesso a dire dove stanno stato e comandi: è quello che Mirto
        deve leggere, invece di ricostruire gli argomenti da sé.
        """
        if self.marca != ESPHOME:
            return None
        componente = COMPONENTE_HA[self.tipo]
        dentro = {
            "name": self.nome,
            "unique_id": f"{self.identificativo}_{componente}",
            "state_topic": self.argomento_stato(),
            "availability_topic": f"{self.identificativo}/status",
            "payload_available": "online",
            "payload_not_available": "offline",
            "device": {
                "identifiers": [self.identificativo],
                "name": self.nome,
                "manufacturer": "ESPHome (finto)",
                "model": "mirto-smarthome-finta",
            },
        }
        if self.comandabile:
            dentro["command_topic"] = self.argomento_comando()
            dentro["payload_on"], dentro["payload_off"] = "ON", "OFF"
        argomento = f"{PREFISSO_ANNUNCIO}/{componente}/{self.identificativo}/config"
        return argomento, json.dumps(dentro).encode()

    def voce_dell_elenco_zigbee(self) -> dict:
        """Com'è fatta la voce di questo dispositivo in ``zigbee2mqtt/bridge/devices``."""
        return {
            "friendly_name": self.nome,
            "ieee_address": f"0x{self.identificativo[:16]:<016}".replace(" ", "0"),
            "type": "EndDevice" if self.tipo == SENSORE else "Router",
            "supported": True,
            "definition": {
                "model": "mirto-finto",
                "vendor": "Mirto (finto)",
                "description": f"{self.tipo} finta per le prove",
                "exposes": [{"type": "switch" if self.comandabile else "binary",
                             "features": [{"property": "state", "access": 7 if self.comandabile else 1}]}],
            },
        }


def casa_di_prova() -> list[DispositivoFinto]:
    """Una casa già arredata: una per marca, più il sensore che serve a provare i trigger.

    Chi apre il simulatore deve vedere subito tutti e tre i casi, non una casa vuota da riempire a
    mano: il caso che non si prova è il caso che si rompe.
    """
    return [
        DispositivoFinto("luce_scale", "Luce delle scale", LUCE, ESPHOME),
        DispositivoFinto("presa_tv", "Presa della TV", PRESA, ESPHOME),
        DispositivoFinto("luce_cucina", "Luce della cucina", LUCE, ZIGBEE),
        DispositivoFinto("movimento_corridoio", "Movimento in corridoio", SENSORE, ZIGBEE),
        DispositivoFinto("luce_garage", "Luce del garage", LUCE, MALEDUCATO),
    ]


class CasaFinta:
    """La casa: tiene i dispositivi, parla col broker, e dice a chi guarda cosa succede.

    Due versi, ed è il motivo per cui esiste:

    * **da dentro verso fuori** — ``cambia()`` è la mano che gira l'interruttore sul muro: pubblica il
      nuovo stato, e in Mirto deve partire un trigger;
    * **da fuori verso dentro** — un comando di Mirto arriva su un argomento ``/set``, il dispositivo
      cambia stato per davvero e lo ripubblica, così chi guarda la finestra vede la lampadina
      accendersi.

    ``su_cambio(dispositivo, da_chi)`` viene richiamato **dal filo che legge dal broker**: la finestra
    del simulatore deve rimbalzare il lavoro sul filo dell'interfaccia, come si fa per gli avvisi.
    """

    def __init__(self, dispositivi: list[DispositivoFinto] | None = None, *,
                 indirizzo: str | None = None, porta: int = 0,
                 su_cambio: Callable[[DispositivoFinto, str], None] | None = None) -> None:
        self.dispositivi: dict[str, DispositivoFinto] = {}
        self.su_cambio = su_cambio
        self._mio_broker = Broker(porta=porta) if indirizzo is None else None
        self._indirizzo = indirizzo or "127.0.0.1"
        self._porta = porta
        self._cliente: mqtt.Cliente | None = None
        self._lucchetto = threading.RLock()
        self._iniziali = list(dispositivi) if dispositivi is not None else casa_di_prova()

    # --- vita -----------------------------------------------------------------
    @property
    def porta(self) -> int:
        return self._porta

    @property
    def broker_proprio(self) -> bool:
        """Se il broker è quello che ci siamo portati dentro, e non uno vero là fuori."""
        return self._mio_broker is not None

    def avvia(self) -> "CasaFinta":
        if self._mio_broker is not None:
            self._mio_broker.avvia()
            self._porta = self._mio_broker.porta
        self._cliente = mqtt.Cliente(
            self._indirizzo, self._porta, nome="casa-finta", riprova=True,
            su_messaggio=self._arrivato,
            testamento=(f"{PREFISSO_ZIGBEE}/bridge/state", json.dumps({"state": "offline"}).encode(), True),
        ).collega()
        self._cliente.pubblica(f"{PREFISSO_ZIGBEE}/bridge/state",
                               json.dumps({"state": "online"}).encode(), ritenuto=True)
        for d in self._iniziali:
            self.aggiungi(d)
        return self

    def ferma(self) -> None:
        if self._cliente is not None:
            self._cliente.chiudi()
            self._cliente = None
        if self._mio_broker is not None:
            self._mio_broker.ferma()

    def __enter__(self) -> "CasaFinta":
        return self.avvia()

    def __exit__(self, *_: object) -> None:
        self.ferma()

    # --- i dispositivi --------------------------------------------------------
    def aggiungi(self, dispositivo: DispositivoFinto) -> DispositivoFinto:
        """Mette un dispositivo in casa: si annuncia (se la sua marca lo fa) e dice com'è adesso."""
        with self._lucchetto:
            self.dispositivi[dispositivo.identificativo] = dispositivo
        annuncio = dispositivo.annuncio()
        if annuncio:
            self._pubblica(annuncio[0], annuncio[1], ritenuto=True)
        if dispositivo.marca == ESPHOME:
            self._pubblica(f"{dispositivo.identificativo}/status", b"online", ritenuto=True)
        if self._cliente is not None:
            for argomento in dispositivo.argomenti_da_ascoltare():
                self._cliente.iscrivi(argomento)
        self._rifa_elenco_zigbee()
        self._dillo(dispositivo)
        return dispositivo

    def togli(self, identificativo: str) -> bool:
        """Lo stacca dal muro. L'annuncio si cancella con un ritenuto **vuoto**: è così che si fa.

        Senza questo, chi aveva già letto l'annuncio continuerebbe a vedere un dispositivo che non
        c'è più — e il primo a farlo sarebbe Home Assistant, non Mirto.
        """
        with self._lucchetto:
            dispositivo = self.dispositivi.pop(identificativo, None)
        if dispositivo is None:
            return False
        annuncio = dispositivo.annuncio()
        if annuncio:
            self._pubblica(annuncio[0], b"", ritenuto=True)
        if dispositivo.marca == ESPHOME:
            self._pubblica(f"{dispositivo.identificativo}/status", b"offline", ritenuto=True)
        for argomento in dispositivo.argomenti_da_ascoltare():
            try:
                if self._cliente is not None:
                    self._cliente.disiscrivi(argomento)
            except mqtt.ErroreMqtt:
                pass
        self._rifa_elenco_zigbee()
        return True

    def cambia(self, identificativo: str, acceso: bool, da_chi: str = "finestra") -> bool:
        """La mano che gira l'interruttore sul muro. È il verso che fa partire i trigger di Mirto."""
        with self._lucchetto:
            dispositivo = self.dispositivi.get(identificativo)
        if dispositivo is None:
            return False
        dispositivo.acceso = acceso
        self._dillo(dispositivo)
        if self.su_cambio:
            self.su_cambio(dispositivo, da_chi)
        return True

    def elenco(self) -> list[DispositivoFinto]:
        with self._lucchetto:
            return list(self.dispositivi.values())

    # --- i messaggi -----------------------------------------------------------
    def _pubblica(self, argomento: str, carico: bytes, ritenuto: bool = False) -> None:
        if self._cliente is None:
            return
        try:
            self._cliente.pubblica(argomento, carico, ritenuto=ritenuto)
        except mqtt.ErroreMqtt as exc:
            log.warning("casa finta: non ho potuto pubblicare su %s (%s)", argomento, exc)

    def _dillo(self, dispositivo: DispositivoFinto) -> None:
        """Pubblica lo stato di un dispositivo, ritenuto: chi arriva dopo deve poterlo sapere."""
        self._pubblica(dispositivo.argomento_stato(), dispositivo.carico_stato(), ritenuto=True)

    def _rifa_elenco_zigbee(self) -> None:
        """Ripubblica ``zigbee2mqtt/bridge/devices``, ritenuto, com'è fatto quello vero.

        È l'unico modo in cui i dispositivi Zigbee si fanno trovare, perché l'annuncio generico di
        base non lo fanno. Si rifà da zero a ogni aggiunta e a ogni rimozione: l'elenco è una
        fotografia, non una cronaca.
        """
        voci = [d.voce_dell_elenco_zigbee() for d in self.elenco() if d.marca == ZIGBEE]
        self._pubblica(f"{PREFISSO_ZIGBEE}/bridge/devices", json.dumps(voci).encode(), ritenuto=True)

    def _arrivato(self, argomento: str, carico: bytes, _ritenuto: bool) -> None:
        """Un comando da fuori. Se è per un nostro dispositivo, cambia davvero e si ripubblica."""
        for dispositivo in self.elenco():
            if argomento in dispositivo.argomenti_da_ascoltare():
                nuovo = dispositivo.leggi_comando(argomento, carico)
                if nuovo is None:
                    log.info("casa finta: comando non capito su %s: %r", argomento, carico[:40])
                    return
                dispositivo.acceso = nuovo
                self._dillo(dispositivo)
                if self.su_cambio:
                    self.su_cambio(dispositivo, "comando")
                return


def accendi(porta: int = 0) -> tuple["CasaFinta | None", str]:
    """Accende la casa finta. Se non ci riesce lo dice **in italiano**, non con una traccia di stack.

    Il caso che capita davvero, e che è capitato: ne lanci un secondo mentre il primo è ancora
    aperto, e la porta 1883 è occupata. Un ``OSError: Address already in use`` buttato addosso a chi
    sta provando la domotica non dice né cos'è successo né cosa fare, e sembra che il simulatore sia
    rotto. Restituisce ``(casa, "")`` oppure ``(None, perché)``.
    """
    casa = CasaFinta(porta=porta)
    try:
        casa.avvia()
    except OSError as exc:
        quale = porta or 1883
        if getattr(exc, "errno", None) in OCCUPATA:
            return None, (f"La porta {quale} è già occupata: probabilmente c'è un'altra casa finta "
                          f"aperta, o un broker vero (Mosquitto).\n"
                          f"Chiudi quella che c'è, oppure apri questa su una porta libera:\n"
                          f"    python -m mirto --simulatore-smarthome --porta-broker 0\n"
                          f"(la porta scelta viene stampata all'avvio, e va scritta in Mirto).")
        return None, f"Non riesco ad aprire il broker sulla porta {quale}: {exc}"
    return casa, ""
