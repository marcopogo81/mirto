# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Mirto si racconta sul broker: eventi, protezione, stato per camera.

Passo 5 del piano domotica, e chiude **C-04** — l'unica priorità 1 del cancello del lancio mai fatta,
promessa da ottobre: *«prima MQTT (eventi e stato inserito/disinserito), poi integrazione HA»*.

**Il verso opposto a tutto il resto della domotica.** Fin qui Mirto leggeva la casa e la comandava;
qui parla, e quello che dice lo sente chiunque sia attaccato al broker. Da questo discendono le tre
decisioni che contano, e sono tutte sul *non* dire:

1. **È spento finché non lo accendi.** Non è prudenza generica: il 17/11 `PRIVACY.md` ha scritto, in
   due lingue, *«Mirto publishes nothing of its own on the broker today… if a future version does,
   this page will say so before it ships, and it will be off until you turn it on»*. Quella frase è
   un impegno preso per iscritto davanti a chi legge, e questo modulo la rispetta.
2. **Le istantanee sono una scelta a parte, e anche quella spenta.** Un JPEG su `mirto/<camera>/
   snapshot` è un fotogramma di casa su una bacheca condivisa: lo vede qualunque cosa sia attaccata
   al broker, compresa la presa col firmware scritto male. Chi accende «raccontami» non ha chiesto
   questo, e metterlo nello stesso interruttore sarebbe far dire sì a una cosa chiedendone un'altra.
3. **Non si pubblica niente che un avviso non direbbe.** Gli eventi passano dallo stesso cancello di
   Telegram, email e SMS: a casa disinserita non parte un avviso, e non parte nemmeno un messaggio
   sul broker. Lo stato della protezione invece sì, sempre: è il suo mestiere dire *«sono
   disinserito»*, e un'integrazione che non lo sapesse sarebbe peggio che non averla.

**Cosa è stato e cosa è notizia**, perché su MQTT è la distinzione che conta. Lo stato — sono vivo,
sono inserito — si pubblica **ritenuto**: resta sul broker e chi arriva dopo lo trova, che è il
motivo per cui Home Assistant non mostra «sconosciuto» appena si riavvia. Un evento no: è successo in
un istante, e riconsegnarlo a chi si collega mezz'ora dopo vorrebbe dire far suonare un allarme per
una cosa finita (ed è lo stesso difetto, al rovescio, che al passo 3 faceva partire la musica di
benvenuto all'avvio).

Il testamento — ``mirto/status`` a ``offline`` — lo dichiara il client al momento del collegamento,
quindi lo manda il **broker** se Mirto muore male. È l'unico modo di non restare «online» per sempre
dopo un crash: un messaggio che Mirto manda da sé non lo manda, se è morto.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Callable

log = logging.getLogger(__name__)

# Il ramo sotto cui Mirto parla. Si lascia configurabile perché su un broker di casa può esserci già
# qualcos'altro che si chiama così, e cambiare un prefisso non deve voler dire cambiare il codice.
PREFISSO = "mirto"

ONLINE, OFFLINE = b"online", b"offline"
ACCESO, SPENTO = b"ON", b"OFF"


def argomento_stato(prefisso: str = PREFISSO) -> str:
    """Dove Mirto dice se c'è. È anche il suo testamento."""
    return f"{prefisso}/status"


def argomento_protezione(prefisso: str = PREFISSO) -> str:
    return f"{prefisso}/armed"


def argomento_evento(prefisso: str = PREFISSO) -> str:
    return f"{prefisso}/event"


def pulisci_nome(nome: str) -> str:
    """Il nome di una camera come pezzo di argomento MQTT.

    I nomi delle camere li scrive chi la installa — *«Ingresso»*, *«Garage / retro»*, *«Cam #2»* — e
    su MQTT ``/``, ``+`` e ``#`` non sono lettere: una barra spezzerebbe l'argomento in due livelli e
    un ``#`` lo renderebbe un carattere jolly, cioè un argomento che non si può più nemmeno nominare.
    Qui si tengono lettere, numeri, ``-`` e ``_``, e tutto il resto diventa ``_``.

    **Due nomi diversi possono dare lo stesso argomento**, e non c'è modo di evitarlo: qualunque
    ripulitura butta via qualcosa, quindi *«Garage / retro»* e *«Garage - retro»* finiscono insieme.
    Non si risolve di nascosto aggiungendo un numero — un argomento che cambia perché ne è comparso
    un altro rompe le automazioni già scritte — si **fa vedere**: `argomenti_che_si_pestano()` dice
    quali nomi si pestano i piedi, e la scheda SmartHome lo mostra. Senza quello, due camere si
    sovrascriverebbero lo stato in Home Assistant e nessuno capirebbe perché una delle due mente.
    """
    lettere = [c if (c.isalnum() or c in "-_") else "_" for c in str(nome or "").strip()]
    # Un «_» di troppo non rompe niente ma si legge male in Home Assistant: «garage___retro» era il
    # risultato di « / ». Si schiacciano le file di underscore in uno.
    pulito = "_".join(p for p in "".join(lettere).split("_") if p).strip("-")
    # Un nome fatto solo di segni resterebbe vuoto, e un argomento con un livello senza nome è un
    # argomento rotto: meglio una parola brutta.
    return pulito.lower() or "camera"


def argomenti_che_si_pestano(nomi) -> dict:
    """``{argomento: [i nomi che ci finiscono]}``, solo per gli argomenti con più di un nome.

    Si mostra a chi configura invece di risolverlo da soli: cambiare un nome di camera è una cosa che
    sa fare lui in dieci secondi, mentre un argomento che Mirto si inventa per sbrogliarsi è un
    argomento che nessuno trova in Home Assistant.
    """
    dove: dict = {}
    for nome in nomi or []:
        if str(nome or "").strip():
            dove.setdefault(pulisci_nome(nome), []).append(str(nome))
    return {a: n for a, n in dove.items() if len(n) > 1}


def argomenti_camera(nome: str, prefisso: str = PREFISSO) -> dict:
    base = f"{prefisso}/{pulisci_nome(nome)}"
    return {"stato": f"{base}/state", "evento": f"{base}/event", "istantanea": f"{base}/snapshot"}


def carico_evento(ev) -> bytes:
    """L'evento in JSON: cosa, dove, quando, quanto sicuro.

    **Senza il fotogramma.** Il JPEG è un messaggio a parte, sotto un interruttore a parte: così chi
    vuole far accendere una luce quando passa qualcuno non si ritrova le immagini di casa su una
    bacheca condivisa per averlo chiesto.

    I nomi dei campi sono quelli che un'automazione legge, in inglese come il resto del contratto, e
    non si traducono: una traduzione cambierebbe il nome di un campo a chi ha già scritto
    l'automazione.
    """
    quando = float(getattr(ev, "ts", 0) or time.time())
    dati = {
        "id": str(getattr(ev, "id", "") or ""),
        "camera": str(getattr(ev, "camera_name", "") or ""),
        "label": str(getattr(ev, "label", "") or ""),
        "score": round(float(getattr(ev, "score", 0) or 0), 3),
        "at": int(quando),
        "test": bool(getattr(ev, "is_test", False)),
    }
    dettaglio = str(getattr(ev, "detail", "") or "")
    if dettaglio:
        dati["detail"] = dettaglio
    specie = str(getattr(ev, "species", "") or "")
    if specie:
        dati["species"] = specie
    return json.dumps(dati, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class Racconto:
    """Dice le cose sul broker. Non sa cos'è un broker: riceve una funzione che pubblica.

    Fatto così perché la parte che vale — **quali argomenti, quali carichi, ritenuto o no** — si
    prova contro un elenco in memoria, senza rete e senza broker, e perché il giorno che si pubblica
    anche altrove (un webhook, dice il piano) cambia solo chi gliela passa.

    Non solleva mai. Un broker che cade mentre Mirto sta mandando un avviso non deve togliere
    l'avviso: la foto su Telegram parte comunque, e nel registro resta scritto perché il broker no.
    """

    def __init__(self, pubblica: Callable[[str, bytes, bool], bool], prefisso: str = PREFISSO,
                 istantanee: bool = False, programma: Callable | None = None,
                 quanto_dura: float = 30.0) -> None:
        self._pubblica = pubblica
        self.prefisso = str(prefisso or PREFISSO).strip("/ ") or PREFISSO
        self.istantanee = bool(istantanee)
        self.quanto_dura = float(quanto_dura)
        self._programma = programma or self._timer
        self._timers: dict = {}
        self._lucchetto = threading.Lock()

    # --- quello che è stato: ritenuto ----------------------------------------
    def sono_qui(self) -> None:
        """«Sono vivo». Ritenuto, perché chi si collega dopo deve trovarlo."""
        self._dillo(argomento_stato(self.prefisso), ONLINE, True)

    def me_ne_vado(self) -> None:
        """«Me ne vado», detto per bene quando Mirto si chiude da sé.

        Il testamento che il broker manderebbe dice la stessa cosa, ma solo quando si accorge che il
        collegamento è caduto, e può volerci il tempo di un keepalive. Dirlo prima di chiudere
        significa che Home Assistant lo sa subito.
        """
        self._dillo(argomento_stato(self.prefisso), OFFLINE, True)

    def protezione(self, inserita: bool) -> None:
        """Inserito o disinserito. **Sempre**, anche a casa disinserita: è il suo mestiere dirlo."""
        self._dillo(argomento_protezione(self.prefisso), ACCESO if inserita else SPENTO, True)

    # --- quello che è notizia: non ritenuto ----------------------------------
    def evento(self, ev) -> None:
        """Un evento, sul ramo di tutti e su quello della sua camera. **Non ritenuto.**

        Due argomenti per la stessa notizia non sono uno spreco: chi scrive un'automazione per una
        camera sola non deve filtrare in YAML quello che il broker sa filtrare da sé, e chi le vuole
        tutte si iscrive a uno.
        """
        carico = carico_evento(ev)
        self._dillo(argomento_evento(self.prefisso), carico, False)
        camera = str(getattr(ev, "camera_name", "") or "")
        if camera:
            self._dillo(argomenti_camera(camera, self.prefisso)["evento"], carico, False)

    def istantanea(self, ev) -> bool:
        """Il fotogramma, **solo se le istantanee sono accese**. Torna True se è partito.

        Il controllo sta qui e non in chi chiama, perché un fotogramma su una bacheca condivisa è la
        cosa più facile da far scappare per sbaglio: se l'interruttore lo guardasse chi chiama,
        basterebbe un posto nuovo che chiama per far uscire le immagini di casa senza che nessuno
        abbia cambiato un'impostazione.
        """
        if not self.istantanee:
            return False
        jpeg = getattr(ev, "jpeg", None)
        dati = jpeg() if callable(jpeg) else jpeg
        if not dati:
            return False
        camera = str(getattr(ev, "camera_name", "") or "")
        if not camera:
            return False
        # Ritenuta: l'ultima foto di quella camera è uno stato («com'era l'ultima volta»), ed è
        # quello che una dashboard vuole mostrare quando la si apre.
        return self._dillo(argomenti_camera(camera, self.prefisso)["istantanea"], bytes(dati), True)

    def camera(self, nome: str, succede_qualcosa: bool) -> None:
        """Lo stato di una camera: sta vedendo qualcosa, o è tranquilla. Ritenuto.

        **E quando è accesa, si programma lo spegnimento.** Senza, uno stato ritenuto a ``ON`` resta
        ``ON`` per sempre: un evento è un istante, ma su MQTT non esiste un «istante» — esiste quello
        che hai detto per ultimo, e quello che hai detto per ultimo resta sul broker. Home Assistant
        mostrerebbe *«Ingresso: sta vedendo qualcosa»* da stasera fino al prossimo riavvio, che è il
        difetto che rende inutile un'integrazione: non sbaglia una volta, mente sempre.

        Un evento nuovo sulla stessa camera **rimanda** lo spegnimento invece di aggiungerne un
        secondo, come fa la luce di cortesia di un portone.
        """
        if not nome:
            return
        argomento = argomenti_camera(nome, self.prefisso)["stato"]
        self._dillo(argomento, ACCESO if succede_qualcosa else SPENTO, True)
        with self._lucchetto:
            vecchio = self._timers.pop(argomento, None)
        if vecchio is not None:
            vecchio.cancel()
        if succede_qualcosa and self.quanto_dura > 0:
            t = self._programma(self.quanto_dura, lambda: self.camera(nome, False))
            if t is not None:
                with self._lucchetto:
                    self._timers[argomento] = t

    def basta(self) -> None:
        """Butta i timer in attesa. La chiama chi chiude: un timer vivo tiene in piedi il programma."""
        with self._lucchetto:
            timers, self._timers = list(self._timers.values()), {}
        for t in timers:
            t.cancel()

    @staticmethod
    def _timer(secondi: float, cosa: Callable):
        t = threading.Timer(secondi, cosa)
        t.daemon = True     # un timer non deve tenere in piedi Mirto che si sta chiudendo
        t.start()
        return t

    # --- il tubo --------------------------------------------------------------
    def _dillo(self, argomento: str, carico: bytes, ritenuto: bool) -> bool:
        try:
            return bool(self._pubblica(argomento, carico, ritenuto))
        except Exception:  # noqa: BLE001 - un broker che cade non porta via un avviso
            log.warning("racconto: non ho potuto pubblicare su %s", argomento, exc_info=True)
            return False


def testamento(prefisso: str = PREFISSO) -> tuple[str, bytes, bool]:
    """Cosa dire se Mirto spira: ``(argomento, carico, ritenuto)``, da dare al client al collegamento.

    Lo manda il **broker**, non Mirto, ed è l'unico modo di non restare «online» per sempre dopo un
    crash o una presa staccata: un messaggio che Mirto manda da sé, se è morto, non lo manda.
    """
    return (argomento_stato(prefisso), OFFLINE, True)
