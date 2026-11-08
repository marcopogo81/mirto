# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Servizio HTTPS del nodo: stesso contratto API del simulatore di Mirto.

Endpoint (tutti con firma HMAC, vedi ``signing.py``):

* ``GET /info``, ``GET /azioni``, ``GET /stato`` (dall'API 1.13 lo stato porta anche
  ``microfono``, ``volume``, ``prank`` e ``dissuasore_riprende_fra_s``: dati che tre schermate di
  Mirto vogliono, e che prima vivevano solo nelle risposte che si chiedono aprendo un pannello)
* ``POST /azioni/<nome>/attiva`` (``durata``), ``POST /azioni/<nome>/disattiva``, ``POST /azioni/ferma-tutto``
* ``POST /configura/nome`` ``{"nome": "Garage"}``: il nome con cui si chiama il nodo, dal wizard di
  Mirto invece che da ``mirto-nodo nome`` sul Raspberry. È solo un'etichetta: il nome in rete
  resta ``mirto-node-XXXX`` per sempre (API 1.5)
* ``GET /configura/wifi`` e ``POST /configura/wifi`` ``{"azione": "cerca"|"collega", "ssid", "password"}``:
  le reti che vede **il nodo** e il collegamento. La password non passa mai dal registro né dal biglietto
  per root: sta in un file 0600 che il nodo cancella appena l'ha usata (API 1.5)
* ``GET /configura/audio`` e ``POST /configura/audio``
  ``{"azione": "imposta"|"cerca"|"abbina"|"prova"|"microfono"}``: dove suona la sirena, comprese
  ricerca e abbinamento di una cassa Bluetooth — che girano da root e rispondono in differita, perché
  cercare dura quindici secondi (API 1.5) — e l'accensione del **microfono**, che prima si poteva solo
  scrivere a mano in ``node.toml`` con un terminale (API 1.6)
* ``GET /configura/manopole`` e ``POST /configura/manopole``
  ``{"scheda", "manopola", "valore"}``: le manopole vere di ogni scheda audio (volume, muto, guadagno
  del microfono, automatismi). Girano **senza root** — il servizio del nodo sta nel gruppo ``audio`` —
  e il nodo le ricorda da sé, perché ALSA le dimentica a ogni riavvio (API 1.6)
* ``GET /configura/abbinamenti`` e ``POST /configura/abbinamenti``
  ``{"azione": "apri"|"chiudi"|"revoca", "nome"}``: chi è abbinato, riaprire la finestra per un secondo
  computer (il PIN torna nella risposta, ma solo a finestra aperta) e staccare un abbinamento. L'ultimo
  non si stacca da qui: servirebbe il terminale per rientrare (API 1.5)
* ``GET /configura/disco`` e ``POST /configura/disco`` ``{"azione": "cerca"|"usa"|"prepara"|"limiti"}``:
  il disco USB delle registrazioni. «prepara» **formatta**, quindi vale solo per un disco che compare
  nell'ultima ricerca — che non propone mai quello da cui parte il sistema — e solo se la conferma
  ripete il nome del dispositivo (API 1.5)
* ``GET /configura/camera`` e ``POST /configura/camera`` ``{"attiva": true, "noir": false,
  "larghezza": 1280, "altezza": 720, "fps": 15}``: la camera dal wizard — serve perché i nodi avranno
  camere diverse, NoIR e normali, e nessuno deve aprire un terminale per dirlo (API 1.5)
* ``POST /audio/riproduci`` (WAV), ``POST /audio/registra`` (``durata``: WAV in base64)
* ``POST /azioni/<nome>/attiva`` accetta ``uscita`` (es. ``usb:UACDemoV10``, ``bluetooth:AA:BB:…``):
  da quale uscita suonare, per questa volta. Senza, suona da quella configurata, come sempre.
  Un'uscita chiesta **non ripiega** su un'altra: se non risponde, il nodo lo dice (API 1.10)
* ``GET /suoni``: i suoni del nodo con la loro durata, e i limiti per caricarne altri (API 1.9)
* ``POST /suoni`` ``{"nome": "voce mia", "wav": "<base64>"}`` e ``POST /suoni/togli`` ``{"nome": …}``:
  i suoni propri, caricati dalle impostazioni di Mirto (API 1.9)
* ``GET /audio/flusso``: microfono in diretta, PCM 16 bit mono 16 kHz (API 1.3, serve ``[microfono]``)
* ``POST /prank`` ``{"minuti": 30}``: la modalità Prank — i dissuasori senza budget, con il tetto dei
  30 secondi per scatto che resta. ``0`` la spegne, e la spegne anche «Ferma tutto». Sta nel nodo
  perché la regola che sospende vive qui: una garanzia che un programma può togliersi da sé non è una
  garanzia. Scade da sé: ``PRANK_MAX_MINUTI`` è il tetto (API 1.13)
* ``POST /volume`` ``{"percento": 60}``: il volume della **cassa in uso**, che è una domanda diversa da
  ``/configura/manopole`` — quella chiede «gira questa manopola di questa scheda» e vuole nomi e
  indici, questa chiede «alza il volume della cassa» e quale manopola sia lo sa il nodo. Su Bluetooth
  non si può e il rifiuto lo dice (API 1.13)
* ``GET /registrazioni?da=EPOCH&a=EPOCH``: spezzoni registrati sul nodo (API 1.4, serve ``[registrazione]``)
* ``GET /registrazioni/file?nome=AAAAMMGG-hhmmss.mp4``: scarica uno spezzone (API 1.4)

Senza firma, solo per l'abbinamento (vedi ``PairingWindow``):

* ``GET /abbina``  la finestra di abbinamento è aperta? (nome del nodo, secondi rimasti e
  ``senza_scadenza``: vero su un nodo mai abbinato, dove il PIN consegnato non scade — API 1.5)
* ``POST /abbina`` ``{"pin": "123456", "client": "…"}``: con il PIN giusto consegna una chiave
  **dedicata a quell'abbinamento**, una volta sola e solo a finestra aperta

Limiti applicati qui, qualunque cosa chieda Mirto: la sirena suona al massimo 3 minuti per volta e
3 minuti in totale ogni 30 minuti; ogni azione si spegne da sola allo scadere. Il dissuasore (suoni
contro uccelli e animali, API 1.3) suona al massimo 30 secondi per volta e 2 minuti in totale ogni
ora, e mai mentre suona la sirena.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import re
import secrets
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import API_VERSION, __version__
from .audio import (BUILTIN_SOUNDS, OUTPUTS, RATE, AudioError, AudioPlayer, alsa_cards,
                    audio_choices, casse_abbinate, chime_pcm, mic_choices, quale_cassa,
                    schermo_attaccato, siren_pcm, wav_to_pcm)
from . import mixer
from .cameras import board_model, find_cameras, host_addresses, host_name, rtsp_credentials
from .install_tools import FORMATI_BUONI, RESOLUTIONS, detect_sensor, host_slug, risoluzione_valida
from .rete import stato_rete
from .signing import Verifier
from . import fs

log = logging.getLogger(__name__)

# Quanto può durare e quanto può pesare un suono caricato da chi usa il programma. Trenta secondi
# sono già lunghi per un dissuasore o per una frase di avviso, e due megabyte di WAV sono molto più di
# quanto serva: i limiti stanno qui, dichiarati, e il nodo li dice prima che qualcuno scelga un file.
MAX_SUONO_SECONDI = 30
MAX_SUONO_BYTE = 2 * 1024 * 1024
SIREN_MAX_SECONDS = 180
SIREN_WINDOW_SECONDS = 1800
GENERIC_MAX_SECONDS = 1800
DETERRENT_MAX_SECONDS = 30
DETERRENT_WINDOW_SECONDS = 3600
DETERRENT_BUDGET_SECONDS = 120   # suono dei dissuasori al massimo 2 minuti ogni ora (rispetto dei vicini)

# --- modalità Prank -----------------------------------------------------------
# Marco, 25/10: *«una modalità Prank che permetta un uso più goliardico senza budget dei suoni, per
# chi vorrà prenderlo per divertirsi»*. Il budget dei dissuasori sparisce; **non** sparisce il tetto
# dei 30 secondi per scatto, e la modalità **scade da sé**. Senza scadenza, una modalità che
# disattiva una regola di sicurezza resterebbe accesa per dimenticanza, ed è il caso da rendere
# impossibile invece che da sconsigliare. Sta nel nodo, come il budget che toglie: se vivesse nel
# computer, un programma qualunque potrebbe accendersela da sé.
# Per quanti secondi vale la lettura del volume della cassa. `amixer` è un processo, e lo stato
# periodico passa ogni due secondi: l'unico che quel volume lo cambia è Mirto, e quando lo cambia il
# valore nuovo gli torna nella risposta — quindi dieci secondi di memoria non raccontano bugie.
VOLUME_VALE_S = 10.0

PRANK_MAX_MINUTI = 120
PRANK_MINUTI = 30                # quanto dura se nessuno dice quanto

# --- limiti del server (S-04) -------------------------------------------------
# Il nodo ascolta sulla rete di casa, e in casa può esserci anche una telecamera con un firmware
# curioso o il portatile di un ospite. Questi limiti valgono per chiunque, anche prima di sapere
# se ha una chiave: un estraneo non deve poter tenere occupato il nodo che fa suonare la sirena.
TIMEOUT_CONNESSIONE = 15          # una connessione che non parla cade da sola
CORPO_MASSIMO = 64 * 1024         # una richiesta firmata è piccola: azioni, durate, nomi
CORPI_MASSIMI = {                 # per percorso, dove serve altro
    "/abbina": 1024,              # un PIN di sei cifre e il nome del client
    "/audio/riproduci": 20 * 1024 * 1024,   # un WAV da riprodurre sulla cassa
    "/suoni": 4 * 1024 * 1024,              # un suono caricato: 2 MB di WAV, più il base64 che li gonfia
    "/aggiorna": 16 * 1024 * 1024,          # il codice del nodo, compresso, più il base64 (N-02)
}
MAX_PACCHETTO = 12 * 1024 * 1024  # il nodo compresso sta in poche centinaia di KB: il resto è sospetto
MAX_FILE_PACCHETTO = 4 * 1024 * 1024   # nessun file del nodo è grosso; un .img dentro non ci deve stare
RICHIESTE_IN_PARALLELO = 16       # oltre: 503 subito, invece di rallentare tutto per tutti


class ActionError(RuntimeError):
    """Azione rifiutata o non eseguibile (diventa una risposta di errore per Mirto).

    ``dati`` sono campi **leggibili da un programma** che finiscono nella risposta accanto a
    ``errore``. La frase in italiano serve a chi legge il registro del nodo o chiama l'API a mano;
    Mirto, che parla la lingua di chi lo usa, dalla frase non può ricavare niente — e fino al
    25/10 su Telegram arrivava l'italiano del nodo dentro un avviso tradotto. Con ``motivo`` e i
    numeri accanto, la stessa cosa si dice in inglese senza che il nodo sappia l'inglese.
    """

    def __init__(self, message: str, code: int = 409, dati: dict | None = None):
        super().__init__(message)
        self.code = code
        self.dati = dict(dati or {})


def fra_quanto(secondi: float) -> str:
    """«fra 12 minuti», «fra meno di un minuto»: un'attesa da leggere, non da calcolare.

    I secondi esatti restano nella risposta, per chi ci deve fare i conti; nella frase no — «riprende
    fra 743 secondi» costringe chi legge a dividere per sessanta, e in un avviso che arriva su
    Telegram mentre si guarda altrove è la differenza fra un'informazione e un numero.
    """
    secondi = max(0.0, float(secondi))
    if secondi < 60:
        return "fra meno di un minuto"
    minuti = int(secondi // 60)
    if minuti < 60:
        return f"fra {minuti} minut{'o' if minuti == 1 else 'i'}"
    ore = minuti // 60
    return f"fra {ore} or{'a' if ore == 1 else 'e'}"


# --- dispositivi -------------------------------------------------------------
class Device:
    kind = "generic"
    description = ""
    # Un dispositivo che suona accetta «da quale uscita» (API 1.10); gli altri no, e chi chiama lo
    # guarda qui invece di indovinarlo dal tipo.
    sceglie_uscita = False

    def start(self, seconds: float) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError


class SirenDevice(Device):
    """Sirena dalla cassa: suono "a ululato" ripetuto senza pause."""

    kind = "siren"
    sceglie_uscita = True

    def __init__(self, player: AudioPlayer, volume: int = 80):
        self.player = player
        self.description = "Sirena dalla cassa (max 3 minuti)"
        self._pcm = siren_pcm(4.0, volume, player.rate)
        self._suona: AudioPlayer | None = None   # su quale cassa sta suonando: è quella da fermare

    def start(self, seconds: float, uscita: str = "") -> None:
        dove = self.player.per(uscita)
        try:
            dove.loop(self._pcm, seconds, uscita=uscita)
        except AudioError as exc:
            raise ActionError(f"sirena non disponibile: {exc}", 503) from exc
        self._suona = dove

    def stop(self) -> None:
        # Si ferma la cassa su cui è partita, non quella configurata: da quando le casse sono più di
        # una, «ferma la sirena» fermava l'uscita del nodo e lasciava suonare quella del portico.
        (self._suona or self.player).stop()
        self._suona = None


class DeterrentDevice(Device):
    """Dissuasore per uccelli e animali: un suono (rapace, sparo, gracchio…) scelto da Mirto o a caso."""

    kind = "deterrent"
    owner = "dissuasore"     # chi sta suonando: con l'owner, fermare un suono non ne spegne un altro
    sceglie_uscita = True

    def __init__(self, player: AudioPlayer, sounds: dict[str, bytes]):
        self.player = player
        self.sounds = sounds
        self.description = "Suoni contro uccelli e animali (rapace, sparo, gracchio…)"
        self.last = ""
        self._suona: AudioPlayer | None = None   # su quale cassa sta suonando: è quella da fermare

    def names(self) -> list[str]:
        return sorted(self.sounds)

    def length(self, sound: str) -> float:
        pcm = self.sounds.get(sound, b"")
        return len(pcm) / (2 * getattr(self.player, "rate", RATE)) if pcm else 0.0

    def pick(self, sound: str = "") -> str:
        if sound in self.sounds:
            return sound
        if sound:
            raise ActionError(f"suono «{sound}» non presente sul nodo", 404)
        import random  # noqa: PLC0415

        options = [s for s in self.sounds if s != self.last] or list(self.sounds)
        if not options:
            raise ActionError("nessun suono disponibile sul nodo", 503)
        return random.choice(options)

    def start(self, seconds: float, sound: str = "", volte: int = 0, uscita: str = "") -> None:
        """``volte`` (API 1.8) = ripetizioni esatte; senza, si riempie ``seconds`` come prima.

        «Per quanti secondi» non sa dire «una volta»: la durata di uno sparo la decide lo sparo. Con
        ``volte`` il suono si sente esattamente quel numero di volte, e i secondi li calcola il nodo,
        che è l'unico a sapere quanto dura il file.
        """
        name = self.pick(sound)
        self.last = name
        dove = self.player.per(uscita)
        try:
            if volte > 0:
                dove.ripeti(self.sounds[name], volte, owner=self.owner, uscita=uscita)
            else:
                dove.loop(self.sounds[name], seconds, owner=self.owner, uscita=uscita)
        except AudioError as exc:
            raise ActionError(f"audio non disponibile: {exc}", 503) from exc
        self._suona = dove

    def stop(self) -> None:
        (self._suona or self.player).stop(owner=self.owner)  # non interrompe mai la sirena
        self._suona = None


class GeneratedAudioDevice(DeterrentDevice):
    """Audio generato dal nodo: UFO, jingle, Matrix. Suoni, non allarmi.

    Nato il 23/10 da un'idea di Marco, mentre si correggeva il difetto della sirena. Fino a stasera la
    sirena era l'unico comando che suonava **senza avere suoni** — un ululato generato al volo — ed è
    da quell'eccezione che veniva il difetto: la finestra le offriva i suoni degli altri comandi.

    Qui i suoni ci sono, hanno un nome e una lunghezza dichiarata, quindi tutto il resto del programma
    li tratta come gli altri: nell'interfaccia la domanda è «quante volte», i limiti sono quelli del
    dissuasore, e in elenco compaiono accanto agli altri suoni — perché per chi configura **un suono è
    un suono**.

    Perché non dentro la sirena (strada A, scelta da Marco il 23/10): la sirena porta un limite di
    legge — tre minuti per volta, tre minuti ogni mezz'ora — e un jingle da due secondi non deve
    mangiare il budget di un allarme, né l'allarme deve perdere il suo limite per stare insieme a lui.
    Due comandi, due limiti, un elenco solo davanti agli occhi.

    L'``owner`` è suo: fermare l'audio generato non spegne un dissuasore a metà, e viceversa. La cassa
    resta una sola, quindi chi parte dopo prende la linea — come è sempre stato fra due suoni.
    """

    owner = "audio"

    def __init__(self, player: AudioPlayer, sounds: dict[str, bytes]):
        super().__init__(player, sounds)
        self.description = "Audio generato dal nodo (UFO, jingle, Matrix…)"


class LedDevice(Device):
    """LED verde del Raspberry che lampeggia: azione di prova, senza hardware aggiuntivo.

    Lampeggia con il trigger «timer» del kernel. Scrivere «brightness» a mano non comanda questo
    LED: su un Pi 4 lo stesso LED compare più volte in ``/sys/class/leds`` (``ACT``, ``mmc0``,
    ``mmc0::``) e l'attività della microSD continua a pilotarlo, quindi la luminosità che
    scriviamo viene sovrascritta subito. Provato sul nodo vero, da root: con il trigger a
    ``none`` e la luminosità al massimo il LED resta spento; con il trigger ``timer`` lampeggia.

    Il nodo scrive solo ``trigger``, che esiste sempre e che la regola udev
    (``node/99-mirto-leds.rules``) rende scrivibile al suo gruppo. I due file con i tempi
    nascono insieme al trigger ``timer``, quindi di proprietà di root: il nodo prova a scriverli
    e, se non ci riesce, resta il mezzo secondo del kernel. Lampeggia comunque, ed è l'unica cosa
    che conta per un'azione di prova.
    """

    kind = "light"
    LAMPEGGIO_MS = 300             # acceso, e altrettanto spento: se riusciamo a scrivere i tempi
    TRIGGER_PREDEFINITO = "mmc0"   # quello di sistema sui Raspberry: l'attività della microSD

    def __init__(self, base: Path | None = None):
        self.description = "LED verde del Raspberry (prova delle luci)"
        self.base = base or next((Path("/sys/class/leds") / n for n in ("ACT", "led0")
                                  if (Path("/sys/class/leds") / n).exists()), None)
        self._saved = self.TRIGGER_PREDEFINITO

    def _write(self, name: str, value: str) -> None:
        if self.base is None:
            raise ActionError("LED del Raspberry non trovato", 503)
        try:
            (self.base / name).write_text(value)
        except OSError as exc:
            raise ActionError(f"LED non comandabile: {exc}", 503) from exc

    def _trigger_di_sistema(self) -> str:
        """Il trigger da rimettere a lampeggio finito: quello attivo adesso, se è di sistema."""
        try:
            riga = (self.base / "trigger").read_text()
            attivo = riga[riga.index("[") + 1:riga.index("]")]
        except (OSError, ValueError, TypeError):
            return self.TRIGGER_PREDEFINITO
        return attivo if attivo not in ("timer", "none") else self.TRIGGER_PREDEFINITO

    def start(self, seconds: float) -> None:
        self._saved = self._trigger_di_sistema()
        self._write("trigger", "timer")   # da qui lampeggia il kernel, non noi
        for tempo in ("delay_on", "delay_off"):
            try:
                self._write(tempo, str(self.LAMPEGGIO_MS))
            except ActionError:
                return   # sono di root: resta il mezzo secondo del kernel, e va bene così

    def stop(self) -> None:
        if self.base is None:
            return
        self._write("trigger", self._saved)


# --- stato del nodo -----------------------------------------------------------
PAIRING_SECONDS = 5 * 60
PIN_ATTEMPTS = 5                                   # PIN sbagliati prima che la finestra si chiuda
BOOT_FLAG = Path("/boot/firmware/mirto-abbina")  # file vuoto sulla microSD: riapre l'abbinamento
BOOT_PIN = Path("/boot/firmware/mirto-pin.txt")  # dove si legge il PIN al primo avvio dell'immagine
OPEN_FLAG = "apri-abbinamento"                     # lo lascia install.sh: il servizio apre e genera il PIN
# Il servizio gira senza privilegi e la partizione di avvio è FAT, di root: non può scriverci.
# Lascia questi due segnali in $CONF e ci pensa mirto-boot-segnali.service, che gira da root.
PIN_DA_MOSTRARE = "pin-da-mostrare"                # contiene il testo da mettere sulla microSD
PIN_DA_TOGLIERE = "pin-da-togliere"                # chiede di cancellare il PIN dalla microSD
CAMERA_DA_APPLICARE = "camera-da-applicare"        # chiede di rifare la configurazione di MediaMTX
AUDIO_DA_FARE = "audio-da-fare"                    # «applica», «cerca» o «abbina AA:BB:…» per l'audio
CASSE_TROVATE = "casse-trovate.json"               # l'ultima ricerca di casse, scritta da root
AUDIO_ESITO = "audio-esito.json"                   # com'è andata l'ultima richiesta sull'audio
WIFI_DA_FARE = "wifi-da-fare"                      # «cerca» o «collega» per il Wi-Fi
WIFI_DA_COLLEGARE = "wifi-da-collegare.json"       # rete e password: 0600, le cancella chi le usa
RETI_TROVATE = "reti-trovate.json"                 # l'ultima ricerca di reti, scritta da root
WIFI_ESITO = "wifi-esito.json"                     # com'è andato l'ultimo collegamento
DISCO_DA_FARE = "disco-da-fare"                    # «cerca», «usa /dev/sda1», «prepara /dev/sda», «applica»
DISCHI_TROVATI = "dischi-trovati.json"             # l'ultima ricerca di dischi, scritta da root
DISCO_ESITO = "disco-esito.json"                   # com'è andata l'ultima richiesta sul disco
MAC_CASSA = re.compile(r"^(?:[0-9A-F]{2}:){5}[0-9A-F]{2}$")
DISPOSITIVO = re.compile(r"^/dev/[a-z][a-z0-9]{2,19}$")   # un nome di dispositivo, non un percorso
NOME_MAX = 40                                      # caratteri del nome mostrato in Mirto


def chiedi_a_root(folder: Path, segnale: str, testo: str = "") -> None:
    """Lascia in ``$CONF`` un lavoro per ``mirto-boot-segnali.service``.

    Il servizio del nodo gira senza privilegi (S-03): non può scrivere nella partizione di avvio
    della microSD, che è FAT e appartiene a root, né cambiare il nome della macchina. Invece di
    dargli quei permessi, gli si fa lasciare un biglietto che un servizio da root legge ed esegue.
    """
    try:
        fs.scrivi(Path(folder) / segnale, testo)
    except OSError as exc:
        log.warning("Segnale %s non scritto: %s", segnale, exc)


class PairingWindow:
    """Finestra di abbinamento, sempre protetta da un PIN di 6 cifre.

    * Si apre per 5 minuti quando lo chiede chi ha in mano il nodo: ``mirto-nodo abbina``, primo
      avvio dell'immagine o installazione (``install.sh`` lascia il file ``apri-abbinamento``),
      oppure un file vuoto ``mirto-abbina`` nella partizione di avvio della microSD.
    * Un riavvio del servizio **non** la riapre più: prima bastava quello, e la finestra tornava
      aperta a ogni riavvio finché il nodo non era abbinato.
    * Il PIN si legge sul nodo (a schermo, o in ``mirto-pin.txt`` nella partizione di avvio al
      primo avvio dell'immagine): senza, l'abbinamento non parte. Dopo 5 PIN sbagliati la finestra
      si chiude e bisogna riaprirla dal nodo.
    * Al primo abbinamento riuscito la finestra si chiude. Un nodo è "abbinato" anche quando riceve
      una richiesta firmata valida (chiave copiata a mano, o nodi installati prima).
    """

    def __init__(self, folder, clock=time.time, seconds: int = PAIRING_SECONDS, boot_flag: Path = BOOT_FLAG,
                 boot_pin: Path = BOOT_PIN):
        self.folder = Path(folder)
        self.clock = clock
        self.seconds = seconds
        self.boot_pin = Path(boot_pin)
        self._lock = threading.RLock()
        for flag, why in ((Path(boot_flag), f"dal file {boot_flag}"),
                          (self.folder / OPEN_FLAG, "dall'installazione")):
            try:
                if flag.exists():
                    flag.unlink()
                    pin = self.open(on_boot=True)
                    log.info("Abbinamento aperto %s per %d minuti · PIN %s", why, self.seconds // 60, pin)
            except OSError as exc:
                log.warning("File di abbinamento %s non utilizzabile: %s", flag, exc)
        # Un nodo mai abbinato con un PIN illeggibile si rimette in sesto da solo: è successo il 20/10,
        # staccando la corrente nel minuto dopo l'accensione (file scritto ma non ancora su disco). Senza
        # questo, quella microSD non si sarebbe più abbinata senza aprire un terminale.
        try:
            if not self.paired() and self._pin_file.exists() and not self.pin():
                nuovo = self.open(on_boot=True)
                log.warning("PIN illeggibile (spegnimento improvviso?): ne ho generato uno nuovo · %s", nuovo)
        except OSError as exc:
            log.warning("PIN non rigenerabile: %s", exc)

    @property
    def _paired_file(self) -> Path:
        return self.folder / "abbinato"

    @property
    def _until_file(self) -> Path:
        return self.folder / "abbina_fino"

    @property
    def _pin_file(self) -> Path:
        return self.folder / "pin"

    def paired(self) -> bool:
        return self._paired_file.exists()

    def paired_info(self) -> dict:
        """``{"da": "Mirto su mac · 192.168.1.5", "quando": 1760000000}`` dell'abbinamento in corso."""
        try:
            d = json.loads(self._paired_file.read_text())
        except (OSError, ValueError):
            return {}
        return {"da": str(d.get("da", ""))[:80], "quando": int(d.get("quando", 0))}

    def open(self, seconds: int | None = None, on_boot: bool = False) -> str:
        """Apre la finestra e genera un PIN nuovo (lo restituisce: va mostrato a chi ha in mano il nodo)."""
        with self._lock:
            until = int(self.clock() + (seconds or self.seconds))
            pin = f"{secrets.randbelow(10 ** 6):06d}"
            fs.scrivi(self._until_file, str(until))
            self._save_pin({"pin": pin, "scade": until, "tentativi": 0})
            if on_boot:
                self._write_boot_pin(pin, until)
            return pin

    def open_or_current(self, seconds: int | None = None) -> tuple[str, bool]:
        """Il PIN in corso se la finestra è già aperta, altrimenti ne apre una nuova: (PIN, è nuovo?).

        Così ``mirto-nodo abbina`` mostra quello che il servizio sta già accettando: non esistono mai
        due PIN validi insieme.
        """
        with self._lock:
            pin = self.pin()
            if pin and self.remaining() != 0:
                return pin, False
            return self.open(seconds), True

    def _save_pin(self, data: dict) -> None:
        fs.scrivi(self._pin_file, json.dumps(data) + "\n")
        try:
            self._pin_file.chmod(0o600)
        except OSError as exc:
            log.warning("Permessi del PIN non impostati: %s", exc)

    def _pin_data(self) -> dict:
        try:
            data = json.loads(self._pin_file.read_text())
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_boot_pin(self, pin: str, until: int) -> None:
        """Il PIN nella partizione di avvio della microSD: al primo avvio non c'è né schermo né tastiera."""
        quanto = ("Vale finché questo nodo non viene abbinato: non scade.\n" if self.senza_scadenza()
                  else f"Vale {self.seconds // 60} minuti dall'accensione; dopo, sul nodo:  mirto-nodo abbina\n")
        testo = (f"Mirto Node · PIN per l'abbinamento: {pin}\n\n"
                 f"Scrivilo in Mirto quando compare \"Nuovo nodo trovato\".\n"
                 + quanto +
                 f"Questo file sparisce da solo quando il nodo è abbinato.\n\n"
                 f"Se perdi questo PIN: entra nel nodo con l'utente e la password di\n"
                 f"mirto-accesso.txt e lancia  sudo mirto-nodo abbina  (ne mostra uno nuovo).\n")
        try:
            self.boot_pin.write_text(testo)
        except OSError:
            self._chiedi_a_root(PIN_DA_MOSTRARE, testo)

    def _chiedi_a_root(self, segnale: str, testo: str = "") -> None:
        chiedi_a_root(self.folder, segnale, testo)

    def senza_scadenza(self) -> bool:
        """Finché il nodo non è **mai** stato abbinato, la finestra non scade da sola.

        Il PIN del primo avvio viene stampato e consegnato insieme al dispositivo: se scadesse dopo
        cinque minuti, chi accende il nodo una settimana dopo si troverebbe in mano un numero morto e
        nessun modo di abbinarlo senza aprire un terminale (deciso il 20/10 col progetto plug & play).

        Le difese restano tutte: il PIN non passa mai dalla rete, dopo cinque tentativi sbagliati la
        finestra si chiude davvero, e al **primo abbinamento riuscito** non si riapre mai più da sola.
        """
        return not self.paired()

    def pin(self) -> str:
        """Il PIN valido adesso, "" se è scaduto o non c'è."""
        d = self._pin_data()
        if not d:
            return ""
        if self.senza_scadenza():
            return str(d.get("pin", ""))
        try:
            if float(d.get("scade", 0)) <= self.clock():
                return ""
        except (TypeError, ValueError):
            return ""
        return str(d.get("pin", ""))

    def open_until(self) -> float:
        try:
            return float(self._until_file.read_text().strip())
        except (OSError, ValueError):
            return 0.0

    SENZA_SCADENZA = -1      # remaining(): aperta finché il nodo non viene abbinato la prima volta

    def remaining(self) -> int:
        """Secondi che restano, ``0`` se è chiusa, ``SENZA_SCADENZA`` se è aperta e non scade."""
        if self._pin_file.exists() and not self.pin():
            # Un file di PIN vuoto o illeggibile **non** è una finestra aperta. Il 20/10 un nodo staccato
            # dalla corrente ne aveva uno da zero byte e si presentava come abbinabile con un PIN che non
            # esisteva: l'abbinamento rispondeva «PIN scaduto» e senza terminale non se ne usciva. Un nodo
            # mai abbinato se ne fa uno nuovo da solo quando riparte (vedi il costruttore).
            self.close()
            return 0
        if self.senza_scadenza() and self.pin():
            return self.SENZA_SCADENZA
        left = max(0, int(self.open_until() - self.clock()))
        if left <= 0 and (self._until_file.exists() or self._pin_file.exists() or self.boot_pin.exists()):
            # finestra scaduta: via il PIN, compreso quello sulla microSD (la FAT non ha permessi: lo legge chiunque)
            self.close()
        return left

    def close(self) -> None:
        """Chiude la finestra e cancella il PIN (anche quello sulla microSD)."""
        for f in (self._until_file, self._pin_file):
            try:
                f.unlink(missing_ok=True)
            except OSError as exc:
                log.warning("File %s non rimosso: %s", f, exc)
        try:
            self.boot_pin.unlink(missing_ok=True)
        except OSError:
            self._chiedi_a_root(PIN_DA_TOGLIERE)   # senza privilegi: lo toglie il servizio da root

    def mark_paired(self, by: str, close: bool = False) -> None:
        with self._lock:
            if not self.paired():
                fs.scrivi(self._paired_file, json.dumps({"quando": int(self.clock()), "da": by[:80]}) + "\n")
            if close:
                self.close()

    def claim(self, client: str, pin: str) -> tuple[bool, str]:
        """Si può consegnare una chiave a questo cliente? Sì solo a finestra aperta e col PIN giusto."""
        with self._lock:
            if self.remaining() == 0:
                return False, "abbinamento chiuso: sul nodo usa  mirto-nodo abbina"
            good = self.pin()
            if not good:
                return False, "PIN scaduto: sul nodo usa  mirto-nodo abbina"
            if not hmac.compare_digest(good, str(pin or "")):
                data = self._pin_data()
                tried = int(data.get("tentativi", 0) or 0) + 1
                left = PIN_ATTEMPTS - tried
                if left <= 0:
                    self.close()
                    log.warning("Abbinamento chiuso: %d PIN sbagliati (ultimo da %s)", PIN_ATTEMPTS, client)
                    return False, "troppi PIN sbagliati: abbinamento chiuso, sul nodo usa  mirto-nodo abbina"
                data["tentativi"] = tried  # contati su file: li vedono sia il servizio sia mirto-nodo
                self._save_pin(data)
                log.warning("PIN sbagliato da %s: restano %d tentativi", client, left)
                return False, f"PIN sbagliato: restano {left} tentativi"
            fs.scrivi(self._paired_file, json.dumps({"quando": int(self.clock()), "da": client[:80]}) + "\n")
            self.close()
            return True, ""


def _local_client(ip: str) -> bool:
    """L'abbinamento si fa solo dalla rete di casa (indirizzi privati o locali)."""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if a.version == 6 and a.ipv4_mapped:
        a = a.ipv4_mapped
    return a.is_private or a.is_link_local or a.is_loopback


class Node:
    def __init__(self, name: str, key_hex: str, devices: dict[str, Device], player: AudioPlayer | None = None,
                 clock=time.time, cameras=find_cameras, addresses=host_addresses, hostname=host_name,
                 model=board_model, credentials=rtsp_credentials, pairing: PairingWindow | None = None,
                 mic=None, recorder=None, keys=None, folder: Path | None = None, rename=None,
                 camera_cfg=None, set_camera_cfg=None, audio_cfg=None, set_audio_cfg=None,
                 rec_cfg=None, set_rec_cfg=None, mic_cfg=None, set_mic_cfg=None, manopole=None,
                 cartella_suoni: Path | None = None, flusso=None):
        # il Wi-Fi non ha configurazione da scrivere in node.toml: lo tiene NetworkManager
        self.name = name
        self.folder = Path(folder) if folder is not None else None
        # Dove vivono i suoni caricati da chi usa il programma. I nostri non stanno qui: sono generati
        # da qualche riga di matematica e non esistono come file (vedi «audio.BUILTIN_SOUNDS»).
        self.cartella_suoni = (Path(cartella_suoni) if cartella_suoni is not None
                               else (self.folder / "suoni" if self.folder else None))
        self.rename = rename   # callable(nome): scrive il nome in node.toml (lo passa __main__)
        # la sezione [camera] di node.toml: leggerla e scriverla sono due funzioni passate da __main__,
        # così il server resta provabile senza un file di configurazione vero
        self.camera_cfg, self.set_camera_cfg = camera_cfg, set_camera_cfg
        self.audio_cfg, self.set_audio_cfg = audio_cfg, set_audio_cfg
        self.rec_cfg, self.set_rec_cfg = rec_cfg, set_rec_cfg   # la sezione [registrazione]
        self.mic_cfg, self.set_mic_cfg = mic_cfg, set_mic_cfg   # la sezione [microfono]
        # Le manopole delle schede audio. Le gira il nodo stesso, senza root, e le ricorda in un file
        # suo perché ALSA al riavvio le dimentica e salvarle in /var/lib/alsa vorrebbe root.
        self.manopole = manopole if manopole is not None else mixer.Manopole(cartella=self.folder)
        self.pairing = pairing
        self._key_hex = key_hex
        self.keys = keys  # KeyStore (keys.py) o None: una chiave sola, come i nodi di prova
        self._cameras, self._addresses, self._hostname = cameras, addresses, hostname
        self._model, self._credentials = model, credentials
        self.verifier = Verifier(self.all_keys())
        self._keys_stamp = keys.stamp() if keys is not None else None
        self.devices = devices
        self.player = player
        self.mic = mic  # Microphone (mic.py) o None
        self.recorder = recorder  # NodeRecorder (recording.py) o None
        # FlussoConAudio (flusso_audio.py) o None: il muxer che pubblica video e microfono insieme.
        # None vuol dire «questo nodo non lo sta facendo», e il perche' lo dice `audio_reg_state`.
        self.flusso = flusso
        self.clock = clock
        self.until: dict[str, float] = {}
        self._timers: dict[str, threading.Timer] = {}
        # Da quale cassa sta suonando ogni azione attiva (API 1.11). Serve a una domanda sola: se
        # un'altra operazione chiede la stessa cassa, quella cassa è occupata e la seconda non parte.
        self._casse: dict[str, str] = {}
        self._siren_log: list[tuple[float, float]] = []
        self._deterrent_log: list[tuple[float, float]] = []
        # Fino a quando la modalità Prank è accesa. Un istante, non un interruttore: così «scade da
        # sé» non ha bisogno di nessuno che si ricordi di spegnerla — né di un timer che un riavvio
        # del nodo porterebbe via lasciando la modalità accesa per sempre.
        self._prank_until = 0.0
        self._volume_letto: tuple[dict | None, float] = (None, 0.0)
        self._lock = threading.RLock()
        self.started = clock()

    def set_name(self, name: str) -> dict:
        """Il nome con cui una persona chiama questo nodo (``POST /configura/nome``).

        È **solo** un'etichetta. Fino al 21/10 cambiava anche il nome in rete — «Studio» diventava
        ``mirto-studio.local`` — e sembrava elegante: in mezza giornata di prove ha rotto le chiavi
        SSH salvate due volte, ha fatto collidere un nome riusato con un altro Raspberry, e ha
        costretto Mirto a riscrivere l'indirizzo salvato a ogni rinomina. Il nome in rete adesso
        resta quello di fabbrica, ``mirto-node-XXXX``, unico per sempre perché viene dall'indirizzo
        della scheda di rete: l'identità della macchina non la decide un'etichetta.

        Restituisce comunque il nome in rete, che non cambia: serve a Mirto per accorgersi se
        l'indirizzo che ha salvato è un numero e può diventare un nome.
        """
        nome = " ".join(str(name or "").split())[:NOME_MAX]
        if not host_slug(nome):
            raise ActionError("nome non valido: servono lettere o numeri", 400)
        with self._lock:
            if self.rename is not None:
                try:
                    self.rename(nome)
                except OSError as exc:
                    log.error("Nome «%s» non salvato in node.toml: %s", nome, exc)
                    raise ActionError("il nodo non riesce a salvare il nome", 500) from exc
            self.name = nome
        host = str(self._hostname() or "").removesuffix(".local")
        log.info("Nome del nodo: «%s» (chiesto da Mirto) · in rete resta %s.local", nome, host)
        return {"esito": "ok", "nome": nome, "host": host, "nome_in_rete": f"{host}.local"}

    def _file_json(self, nome: str) -> dict:
        """Un file JSON lasciato dal servizio da root (ricerca delle casse, esito): {} se non c'è."""
        if self.folder is None:
            return {}
        try:
            dati = json.loads((self.folder / nome).read_text())
        except (OSError, ValueError):
            return {}
        return dati if isinstance(dati, dict) else {}

    def wifi_state(self) -> dict:
        """Le reti che vede il nodo, a che rete è attaccato e com'è andato l'ultimo collegamento.

        L'elenco è dell'**ultima ricerca**, che fa root: cercare accende la radio e dura secondi. Conta
        che sia il nodo a vedere le reti, non il computer — è l'unico modo di sapere se da dove lo
        metterai il segnale arriva.
        """
        trovate = self._file_json(RETI_TROVATE)
        reti = [r for r in (trovate.get("reti") or []) if isinstance(r, dict)]
        return {"reti": reti, "quando": int(trovate.get("quando") or 0),
                "in_uso": next((r.get("ssid", "") for r in reti if r.get("in_uso")), ""),
                "indirizzi": list(self._addresses() or []),
                "esito": self._file_json(WIFI_ESITO)}

    def set_wifi(self, dati: dict) -> dict:
        """Cerca le reti o collega il nodo a una di esse (``POST /configura/wifi``).

        La password del Wi-Fi di casa è il segreto più delicato che passa di qui: non finisce nel
        registro, non finisce nel biglietto che legge root (che dice solo «collega»), e sta in un file
        **0600** che chi la usa cancella subito. Il nodo resta comunque attaccato al cavo, che è la rete
        di riserva se la password è sbagliata.
        """
        if self.folder is None:
            raise ActionError("questo nodo non sa configurare il Wi-Fi da Mirto", 501)
        azione = str(dati.get("azione", ""))
        if azione == "cerca":
            with self._lock:
                chiedi_a_root(self.folder, WIFI_DA_FARE, "cerca\n")
            log.info("Ricerca delle reti Wi-Fi chiesta da Mirto")
            return {"esito": "avviato", "azione": "cerca", "attesa_s": 25}
        if azione != "collega":
            raise ActionError("azione sconosciuta per il Wi-Fi", 400)
        ssid = str(dati.get("ssid", "")).strip()
        password = str(dati.get("password", ""))
        if not ssid or len(ssid.encode()) > 32:
            raise ActionError("nome della rete non valido", 400)
        if password and not 8 <= len(password) <= 63:
            raise ActionError("la password di una rete Wi-Fi sta fra 8 e 63 caratteri", 400)
        with self._lock:
            try:
                fs.scrivi(self.folder / WIFI_DA_COLLEGARE,
                          json.dumps({"ssid": ssid, "password": password,
                                      "nascosta": bool(dati.get("nascosta"))}) + "\n")
            except OSError as exc:
                log.error("Richiesta di collegamento Wi-Fi non salvata: %s", exc)
                raise ActionError("il nodo non riesce a salvare la richiesta", 500) from exc
            chiedi_a_root(self.folder, WIFI_DA_FARE, "collega\n")
        log.info("Collegamento alla rete «%s» chiesto da Mirto", ssid)   # la password non si scrive mai
        return {"esito": "avviato", "azione": "collega", "ssid": ssid, "attesa_s": 60}

    def pairings(self) -> dict:
        """Chi è abbinato a questo nodo, e com'è la finestra (``GET /configura/abbinamenti``).

        Il PIN c'è solo se la finestra è **aperta**, e chi chiede ha già una chiave valida: serve a far
        abbinare un secondo computer senza andare sul nodo a leggerlo. Un computer con una chiave può
        già fare tutto, quindi non è un segreto in più — ma non viaggia mai a finestra chiusa.
        """
        chiavi = self.keys.entries() if self.keys is not None else []
        finestra = self.pairing_state()
        if self.pairing is not None and finestra.get("aperto"):
            finestra["pin"] = self.pairing.pin()
        return {"chiavi": chiavi, "finestra": finestra,
                "chiave_installazione": bool(self.keys.install_key_hex()) if self.keys is not None else False,
                "ritiro_installazione_tra_s": self.keys.retire_in() if self.keys is not None else 0}

    def set_pairings(self, dati: dict) -> dict:
        """Riapre la finestra per un altro computer, la richiude, o stacca un abbinamento
        (``POST /configura/abbinamenti``).

        Qui non serve root: le chiavi stanno in una cartella del servizio, e la finestra la gestisce il
        servizio. Quindi si risponde subito, senza biglietti e senza ripassare.

        L'**ultimo** abbinamento non si stacca da qui: lascerebbe il nodo senza nessun Mirto, e per
        rientrare servirebbe il terminale o la microSD. Quella strada resta dov'è giusto che sia —
        davanti al nodo, con ``mirto-nodo abbina --revoca-tutti``.
        """
        if self.pairing is None or self.keys is None:
            raise ActionError("questo nodo non sa gestire gli abbinamenti da Mirto", 501)
        azione = str(dati.get("azione", ""))
        if azione == "apri":
            with self._lock:
                try:
                    pin, nuova = self.pairing.open_or_current()
                except OSError as exc:
                    log.error("Finestra di abbinamento non apribile: %s", exc)
                    raise ActionError("il nodo non riesce ad aprire l'abbinamento", 500) from exc
            log.warning("Abbinamento riaperto da Mirto%s: un altro computer può abbinarsi",
                        "" if nuova else " (era già aperto)")
            return {"esito": "ok", "azione": "apri", "pin": pin, **self.pairings()}
        if azione == "chiudi":
            with self._lock:
                self.pairing.close()
            log.info("Abbinamento chiuso da Mirto")
            return {"esito": "ok", "azione": "chiudi", **self.pairings()}
        if azione != "revoca":
            raise ActionError("azione sconosciuta per gli abbinamenti", 400)
        nome = str(dati.get("nome", "")).strip()
        nomi = [str(e.get("nome") or "") for e in self.keys.entries()]
        # La chiave dell'installazione non è un abbinamento e non compare fra le «entries», ma è la
        # cosa che Mirto chiede di togliere per prima: fino al 22/10 il pannello mandava qui il suo
        # nome e si sentiva rispondere «abbinamento sconosciuto», cioè l'unica strada indicata
        # all'utente era chiusa. Si ritira come le altre, e non conta nel numero di chi resta.
        # «and nome not in nomi»: se mai esistesse un abbinamento chiamato cosi', vince lui — un
        # nome speciale non deve poter mettere in ombra una chiave vera.
        installazione = nome in ("installazione", "chiave-installazione", "chiave") and nome not in nomi
        if installazione:
            if not self.keys.install_key_hex():
                raise ActionError("la chiave dell'installazione è già stata ritirata", 400)
        elif nome not in nomi:
            raise ActionError("abbinamento sconosciuto: ricarica l'elenco", 400)
        elif len(nomi) <= 1 and not self.keys.install_key_hex():
            raise ActionError("è l'ultimo abbinamento: staccandolo questo nodo non risponderebbe più a "
                              "nessun Mirto. Dal nodo:  mirto-nodo abbina --revoca-tutti", 409)
        with self._lock:
            quante = self.keys.revoke(nome)
            self.refresh_keys(force=True)   # la chiave staccata non deve firmare la richiesta dopo
        if installazione:
            log.warning("Chiave dell'installazione ritirata da Mirto: il nodo non la accetta più")
        else:
            log.warning("Abbinamento «%s» staccato da Mirto", nome)
        return {"esito": "ok", "azione": "revoca", "nome": nome, "revocate": quante, **self.pairings()}

    def disk_state(self) -> dict:
        """Il disco delle registrazioni: quello in uso, quelli collegati, i limiti, com'è andata (``GET /configura/disco``).

        ``dischi`` è il risultato dell'ultima ricerca, che fa root: leggere i dischi collegati vuol dire
        ``lsblk``, e la ricerca **non propone mai il disco da cui parte il sistema** — è la prima
        protezione del pezzo che formatta.
        """
        rec = dict(self.rec_cfg() if self.rec_cfg is not None else {})
        trovati = self._file_json(DISCHI_TROVATI)
        return {"dischi": [d for d in (trovati.get("dischi") or []) if isinstance(d, dict)],
                "quando": int(trovati.get("quando") or 0),
                "attiva": bool(rec.get("attiva", True)),
                "disco": str(rec.get("disco") or ""),
                "massimo_gb": int(rec.get("massimo_gb") or 0),
                "minimo_libero_percento": int(rec.get("minimo_libero_percento") or 10),
                "audio": self.audio_reg_state(),
                "formati_buoni": list(FORMATI_BUONI),
                "registrazione": self.recorder.status() if self.recorder is not None else {},
                "esito": self._file_json(DISCO_ESITO)}

    def set_disk(self, dati: dict) -> dict:
        """Cerca i dischi, ne usa uno, lo prepara, o cambia i limiti (``POST /configura/disco``).

        È l'unico pezzo della configurazione che **cancella** roba, quindi il nodo non si fida della
        finestra: «prepara» vale solo per un disco che compare nell'ultima ricerca — e la ricerca non
        propone mai quello da cui parte il sistema — e solo se la conferma ripete esattamente il nome
        del dispositivo. Chi formatta è root, che rifà i suoi controlli prima di cancellare.
        """
        if self.folder is None:
            raise ActionError("questo nodo non sa configurare il disco da Mirto", 501)
        azione = str(dati.get("azione", ""))
        if azione == "cerca":
            with self._lock:
                chiedi_a_root(self.folder, DISCO_DA_FARE, "cerca\n")
            log.info("Ricerca dei dischi chiesta da Mirto")
            return {"esito": "avviato", "azione": "cerca", "attesa_s": 15}
        if azione in ("usa", "prepara"):
            dev = str(dati.get("dispositivo", "")).strip()
            if not DISPOSITIVO.match(dev):
                raise ActionError("nome del dispositivo non valido", 400)
            trovati = self.disk_state()["dischi"]
            chiave = "path" if azione == "usa" else "disk"
            voce = next((d for d in trovati if str(d.get(chiave) or "") == dev), None)
            if voce is None:
                raise ActionError("questo disco non è fra quelli trovati sul nodo: rifai la ricerca", 400)
            if azione == "usa" and str(voce.get("fstype") or "") not in FORMATI_BUONI:
                raise ActionError("questo disco va preparato prima di usarlo", 409)
            if azione == "prepara" and str(dati.get("conferma", "")) != dev:
                raise ActionError("serve la conferma: preparare il disco cancella tutto quello che contiene", 400)
            with self._lock:
                chiedi_a_root(self.folder, DISCO_DA_FARE, f"{azione} {dev}\n")
            if azione == "prepara":
                log.warning("Preparazione (formattazione) di %s chiesta da Mirto", dev)
            else:
                log.info("Disco %s da usare per le registrazioni, chiesto da Mirto", dev)
            return {"esito": "avviato", "azione": azione, "dispositivo": dev,
                    "attesa_s": 300 if azione == "prepara" else 45}
        if azione == "audio":
            return self._set_audio_registrazione(dati)
        if azione != "limiti":
            raise ActionError("azione sconosciuta per il disco", 400)
        if self.set_rec_cfg is None:
            raise ActionError("questo nodo non sa configurare il disco da Mirto", 501)
        try:
            massimo = int(dati.get("massimo_gb", 0))
            minimo = int(dati.get("minimo_libero_percento", 10))
        except (TypeError, ValueError):
            raise ActionError("limiti non validi", 400) from None
        if not 0 <= massimo <= 65536:
            raise ActionError("il tetto va da 0 (nessuno) a 65536 GB", 400)
        if not 1 <= minimo <= 50:
            raise ActionError("lo spazio da tenere libero va dall'1 al 50 per cento", 400)
        valori = {"attiva": bool(dati.get("attiva", True)), "massimo_gb": massimo,
                  "minimo_libero_percento": minimo}
        with self._lock:
            try:
                self.set_rec_cfg(valori)
            except OSError as exc:
                log.error("Limiti della registrazione non salvati in node.toml: %s", exc)
                raise ActionError("il nodo non riesce a salvare la configurazione", 500) from exc
            chiedi_a_root(self.folder, DISCO_DA_FARE, "applica\n")
        log.info("Registrazione: %s, tetto %s, libero %s%% (chiesto da Mirto)",
                 "accesa" if valori["attiva"] else "spenta", massimo or "nessuno", minimo)
        stato = self.disk_state()
        stato["esito_richiesta"] = "ok"
        return stato

    def mic_state(self) -> dict:
        """Il microfono: configurato, quale, e se sta davvero in ascolto (nello ``/stato``, API 1.13).

        Viveva solo in ``GET /configura/audio``, che Mirto chiede **aprendo** il pannello
        dell'audio. Il 25/10 ci sono sbattuto scrivendo la riga del microfono in *Sistema*: cercavo
        quel dato nello stato periodico, dove non c'era, e la riga non compariva mai — la condizione
        sembrava giusta leggendola. Un dato che tre schermate vogliono va dove si guarda sempre.

        ``attivo`` e ``acceso_ora`` possono differire: configurato, ma la scheda non c'è più. È
        proprio la differenza da mostrare: appiattirla vorrebbe dire «nessun microfono» a chi
        ne ha uno attaccato che non funziona.
        """
        try:
            m = dict(self.mic_cfg() if self.mic_cfg is not None else {})
        except Exception:  # noqa: BLE001  (node.toml illeggibile: lo stato periodico non muore per questo)
            m = {}
        return {"attivo": bool(m.get("attivo", False)), "scheda": str(m.get("scheda", "")),
                "acceso_ora": self.mic is not None}

    def _set_audio_registrazione(self, dati: dict) -> dict:
        """Accende o spegne il microfono **dentro le registrazioni** (``POST /configura/disco``).

        Il nodo dice no prima, invece di accettare e non farlo: senza microfono acceso non c'è niente
        da registrare, e senza ``ffmpeg`` non c'è chi lo mescoli al video. Scoprirlo dopo vorrebbe
        dire un'impostazione accesa in Mirto e un silenzio nelle clip che nessuno sa spiegare — ed è
        peggio su questa funzione che su qualunque altra, perché chi l'accende lo fa **credendo** di
        aver registrato l'audio di quello che è successo.
        """
        import shutil  # noqa: PLC0415

        if self.folder is None or self.set_rec_cfg is None:
            raise ActionError("questo nodo non sa configurare la registrazione da Mirto", 501)
        acceso = bool(dati.get("attivo", False))
        if acceso:
            if self.mic is None:
                raise ActionError("il microfono del nodo è spento: accendilo prima (Nodi → Audio del nodo)",
                                  409, {"motivo": "audio_reg_senza_microfono"})
            if not shutil.which("ffmpeg"):
                raise ActionError("su questo nodo manca ffmpeg, che serve a mettere l'audio accanto al "
                                  "video: rilancia l'installazione del nodo con internet collegato",
                                  409, {"motivo": "audio_reg_senza_ffmpeg"})
        rec = dict(self.rec_cfg() if self.rec_cfg is not None else {})
        valori = {"attiva": bool(rec.get("attiva", True)), "audio": acceso}
        with self._lock:
            try:
                self.set_rec_cfg(valori)
            except OSError as exc:
                log.error("Audio nelle registrazioni non salvato in node.toml: %s", exc)
                raise ActionError("il nodo non riesce a salvare la configurazione", 500) from exc
            # Il percorso con l'audio nasce e muore in mediamtx.yml, e il muxer lo avvia il servizio:
            # tocca a root rigenerare e riavviare, come per i limiti.
            chiedi_a_root(self.folder, DISCO_DA_FARE, "applica\n")
        log.warning("Audio nelle registrazioni: %s (chiesto da Mirto)", "ACCESO" if acceso else "spento")
        stato = self.disk_state()
        stato["esito_richiesta"] = "ok"
        return stato

    def audio_reg_state(self) -> dict:
        """Il microfono nelle registrazioni: se è **acceso**, se sta **funzionando**, e se no perché.

        Tre cose e non una, perché sono tre situazioni con tre rimedi diversi: spento (si accende),
        acceso e funzionante (niente da fare), acceso e fermo (e allora il rimedio dipende). Dire
        soltanto «acceso» a un nodo senza microfono vorrebbe dire promettere l'audio e non darlo.
        """
        from .recording import AUDIO_PATH  # noqa: PLC0415

        try:
            rec = dict(self.rec_cfg() if self.rec_cfg is not None else {})
        except Exception:  # noqa: BLE001
            rec = {}
        acceso = bool(rec.get("audio"))
        st: dict = {"acceso": acceso, "percorso": AUDIO_PATH, "attivo": False}
        if self.flusso is not None:
            fl = self.flusso.status()
            st["attivo"] = bool(fl.get("attivo"))
            if fl.get("da_s") is not None:
                st["da_s"] = fl.get("da_s")
            if fl.get("errore"):
                st["perche"] = fl["errore"]
        elif acceso:
            st["perche"] = ("il microfono del nodo è spento" if self.mic is None
                            else "la camera del nodo è spenta")
        return st

    def audio_state(self) -> dict:
        """Dove suona la sirena, cosa si può scegliere, e com'è andata l'ultima richiesta.

        ``casse_trovate`` è il risultato dell'ultima ricerca: la fa root (l'abbinamento Bluetooth vuole
        privilegi) e dura una quindicina di secondi, quindi Mirto la chiede e poi ripassa — invece di
        tenere aperta una connessione e sembrare bloccato.
        """
        a = dict(self.audio_cfg() if self.audio_cfg is not None else {})
        schede = alsa_cards()
        return {"uscita": str(a.get("uscita", "bluetooth")), "scheda": str(a.get("scheda", "")),
                "bluetooth_mac": str(a.get("bluetooth_mac", "")),
                "uscite": audio_choices(schede),
                # Il microfono lo dice `mic_state()`, lo stesso che va nello stato periodico: due
                # risposte alla stessa domanda prima o poi si contraddicono.
                "microfono": {**self.mic_state(), "si_puo_scegliere": self.set_mic_cfg is not None},
                "microfoni": mic_choices(schede),
                "casse_trovate": self._file_json(CASSE_TROVATE),
                "esito": self._file_json(AUDIO_ESITO)}

    def set_audio(self, dati: dict) -> dict:
        """Uscita della sirena, ricerca e abbinamento di una cassa, suono di prova (``POST /configura/audio``).

        Quattro azioni, una sola porta: ``imposta`` salva la scelta, ``cerca`` e ``abbina`` le esegue
        root (Bluetooth), ``prova`` suona subito qui — è l'unica che non tocca niente.
        """
        azione = str(dati.get("azione", "imposta"))
        if azione == "prova":
            if self.player is None:
                raise ActionError("questo nodo non ha un'uscita audio", 501)
            try:
                self.player.loop(chime_pcm(), 1.0)
            except AudioError as exc:
                raise ActionError(f"suono non riuscito: {exc}", 503) from exc
            return {"esito": "ok", "azione": "prova"}
        if azione == "microfono":
            return self._set_microfono(dati)
        if azione == "jack":
            return self._set_jack(bool(dati.get("attivo", True)))
        if azione == "dimentica":
            return self._dimentica_cassa(str(dati.get("mac", "")).upper())
        if self.folder is None or self.set_audio_cfg is None:
            raise ActionError("questo nodo non sa configurare l'audio da Mirto", 501)
        if azione == "cerca":
            with self._lock:
                chiedi_a_root(self.folder, AUDIO_DA_FARE, "cerca\n")
            log.info("Ricerca di casse Bluetooth chiesta da Mirto")
            return {"esito": "avviato", "azione": "cerca", "attesa_s": 20}
        if azione == "abbina":
            mac = str(dati.get("mac", "")).upper()
            if not MAC_CASSA.match(mac):
                raise ActionError("indirizzo della cassa non valido", 400)
            with self._lock:
                chiedi_a_root(self.folder, AUDIO_DA_FARE, f"abbina {mac}\n")
            log.info("Abbinamento della cassa %s chiesto da Mirto", mac)
            # 60 s: prima di abbinare il nodo fa una ricerca corta, perche' BlueZ dimentica in fretta
            # una cassa che non si annuncia piu' (21/10).
            return {"esito": "avviato", "azione": "abbina", "mac": mac, "attesa_s": 60}
        if azione != "imposta":
            raise ActionError("azione sconosciuta per l'audio", 400)
        uscita = str(dati.get("uscita", ""))
        if uscita not in OUTPUTS:
            raise ActionError("uscita sconosciuta", 400)
        scheda = str(dati.get("scheda", ""))
        if scheda and scheda not in {c["id"] for c in alsa_cards() if c.get("suona", True)}:
            # «che sa suonare»: il 22/10 il microfono USB era proponibile come uscita della sirena.
            raise ActionError("scheda audio non presente su questo nodo, o non sa suonare", 400)
        valori = {"uscita": uscita, "scheda": scheda}
        if uscita == "bluetooth":
            mac = str(dati.get("bluetooth_mac", "") or (self.audio_cfg() or {}).get("bluetooth_mac", "")).upper()
            if not MAC_CASSA.match(mac):
                raise ActionError("serve una cassa Bluetooth abbinata", 400)
            valori["bluetooth_mac"] = mac
        with self._lock:
            try:
                self.set_audio_cfg(valori)
            except OSError as exc:
                log.error("Audio non salvato in node.toml: %s", exc)
                raise ActionError("il nodo non riesce a salvare la configurazione audio", 500) from exc
            chiedi_a_root(self.folder, AUDIO_DA_FARE, "applica\n")
        log.info("Uscita audio: %s%s (chiesto da Mirto)", uscita, f" · {scheda}" if scheda else "")
        stato = self.audio_state()
        stato["esito_richiesta"] = "ok"
        return stato

    def _set_microfono(self, dati: dict) -> dict:
        """Accende o spegne il microfono del nodo e scegli quale (``POST /configura/audio``, API 1.6).

        Prima si poteva solo scrivere ``[microfono] attivo = true`` in ``node.toml``, con un terminale
        e la password del foglio: una funzione che esisteva e che nessuno poteva trovare. La sezione la
        scrive il nodo, poi root riavvia il servizio — perché l'ascolto parte all'avvio, come la camera.
        """
        if self.folder is None or self.set_mic_cfg is None:
            raise ActionError("questo nodo non sa configurare il microfono da Mirto", 501)
        attivo = bool(dati.get("attivo", True))
        scheda = str(dati.get("scheda", ""))
        if attivo:
            # Si accetta solo una scheda che **registra** davvero: accendere l'ascolto su una cassa
            # vorrebbe dire un microfono che non sentirà mai niente, e un utente che non capisce perché.
            if scheda not in {m["scheda"] for m in mic_choices(alsa_cards())}:
                raise ActionError("questa scheda non registra, o non è collegata", 400)
        with self._lock:
            try:
                self.set_mic_cfg({"attivo": attivo, "scheda": scheda})
            except OSError as exc:
                log.error("Microfono non salvato in node.toml: %s", exc)
                raise ActionError("il nodo non riesce a salvare la configurazione del microfono", 500) from exc
            chiedi_a_root(self.folder, AUDIO_DA_FARE, "applica\n")
        log.info("Microfono %s%s (chiesto da Mirto)", "acceso" if attivo else "spento",
                 f" · {scheda}" if scheda and attivo else "")
        stato = self.audio_state()
        stato["esito_richiesta"] = "ok"
        return stato

    def _set_jack(self, attivo: bool) -> dict:
        """Accende o spegne l'uscita jack come dispositivo utilizzabile (API 1.7).

        Spegnerla non tocca il Raspberry: toglie il jack dai dispositivi proponibili **e dai
        ripieghi**. Su un nodo che non ha niente attaccato al jack, ripiegarci sopra vuol dire una
        sirena che suona in un buco senza che nessuno lo sappia — e una sirena che non suona e lo dice
        è meglio di una che non suona e tace.
        """
        if self.folder is None or self.set_audio_cfg is None:
            raise ActionError("questo nodo non sa configurare l'audio da Mirto", 501)
        with self._lock:
            try:
                self.set_audio_cfg({"jack": attivo})
            except OSError as exc:
                log.error("Jack non salvato in node.toml: %s", exc)
                raise ActionError("il nodo non riesce a salvare la configurazione audio", 500) from exc
            chiedi_a_root(self.folder, AUDIO_DA_FARE, "applica\n")
        log.info("Uscita jack %s (chiesto da Mirto)", "attiva" if attivo else "spenta")
        stato = self.dispositivi_state()
        stato["esito_richiesta"] = "ok"
        return stato

    def _dimentica_cassa(self, mac: str) -> dict:
        """Toglie l'abbinamento di una cassa Bluetooth (API 1.7).

        L'elenco delle casse lo tiene BlueZ e toglierne una vuole privilegi: qui si lascia il
        biglietto, come per la ricerca e l'abbinamento.
        """
        if not MAC_CASSA.match(mac):
            raise ActionError("indirizzo della cassa non valido", 400)
        if self.folder is None:
            raise ActionError("questo nodo non sa configurare l'audio da Mirto", 501)
        with self._lock:
            chiedi_a_root(self.folder, AUDIO_DA_FARE, f"dimentica {mac}\n")
        log.info("Cassa %s da dimenticare (chiesto da Mirto)", mac)
        return {"esito": "avviato", "azione": "dimentica", "mac": mac, "attesa_s": 20}

    def volume_state(self) -> dict:
        """Il volume della cassa in uso, pronto per lo stato periodico (API 1.13).

        Marco, 25/10: *«nella finestra di sistema servirebbe avere sotto la cassa il livello del
        volume modificabile al volo»*. Perché si possa cambiare al volo bisogna sapere **com'è
        adesso**, e quel dato stava solo in ``GET /configura/manopole`` — una risposta che gira
        ``amixer`` su ogni scheda del nodo, troppo per una cosa chiesta ogni due secondi.

        Qui si legge **una** manopola: quella della cassa che il nodo sta usando. E non si legge a
        ogni giro: ``VOLUME_VALE_S`` secondi di memoria, perché l'unico che la cambia è Mirto, e
        quando la cambia il valore nuovo gli torna nella risposta.

        Su una cassa Bluetooth il volume **non c'è**: il nodo manda l'audio al profilo A2DP e la
        manopola sta sulla cassa. Si dice quello, invece di mostrare un controllo che non comanda
        niente — un cursore che si muove senza che il suono cambi è peggio di nessun cursore.
        """
        p = self.player
        if p is None:
            return {"si_puo": False, "perche": "niente-cassa"}
        uscita = str(getattr(p, "in_uso", "") or getattr(p, "output", ""))
        if uscita == "bluetooth":
            return {"si_puo": False, "perche": "bluetooth", "uscita": uscita}
        scheda = str(getattr(p, "card_in_uso", "") or getattr(p, "card", "") or "")
        if not scheda:
            return {"si_puo": False, "perche": "scheda-sconosciuta", "uscita": uscita}
        now = self.clock()
        letto, quando = self._volume_letto
        if letto is None or now - quando > VOLUME_VALE_S or letto.get("scheda") != scheda:
            letto = self._leggi_volume(scheda, uscita)
            self._volume_letto = (letto, now)
        return letto

    def _leggi_volume(self, scheda: str, uscita: str) -> dict:
        """La prima manopola con un volume di quella scheda. Senza filtrare per direzione: su una
        scheda vera quella direzione mente (è la stessa ragione di ``manopole_state``)."""
        for m in self.manopole.leggi(scheda):
            vol = m.get("volume")
            if isinstance(vol, dict) and vol.get("percento") is not None:
                return {"si_puo": True, "scheda": scheda, "uscita": uscita,
                        "manopola": mixer.chiave(m), "nome": str(m.get("nome") or ""),
                        "percento": int(vol["percento"])}
        return {"si_puo": False, "perche": "senza-manopola", "scheda": scheda, "uscita": uscita}

    def set_volume(self, dati: dict) -> dict:
        """``POST /volume`` — il volume della cassa in uso, in percento (API 1.13).

        Una porta sua, e non ``POST /configura/manopole``, perché la domanda è un'altra: quella
        chiede *gira questa manopola di questa scheda*, e chi la fa deve sapere nomi e indici; questa
        chiede *alza il volume della cassa*, che è quello che vuole chi clicca su un quadratino in
        *Sistema* — e **quale** manopola sia è una cosa che sa il nodo.
        """
        try:
            quanto = int(float(dati.get("percento")))
        except (TypeError, ValueError):
            raise ActionError("percento non valido", 400) from None
        if not 0 <= quanto <= 100:
            raise ActionError("il volume sta fra 0 e 100", 400)
        stato = self.volume_state()
        if not stato.get("si_puo"):
            raise ActionError(f"su questa cassa il volume non si regola dal nodo ({stato.get('perche')})",
                              409, {"motivo": "volume_non_regolabile", "perche": str(stato.get("perche") or "")})
        if not self.manopole.gira(str(stato["scheda"]), str(stato["manopola"]), f"{quanto}%"):
            raise ActionError("la scheda non ha accettato il volume", 503)
        self._volume_letto = (None, 0.0)       # il valore vecchio non vale più: si rilegge
        log.info("Volume della cassa %s portato a %d%%", stato["scheda"], quanto)
        return {"esito": "ok", "volume": self.volume_state()}

    def manopole_state(self) -> dict:
        """Le manopole di ogni scheda audio presente (``GET /configura/manopole``, API 1.6).

        Si danno **tutte** le manopole di ogni scheda, senza filtrarle per direzione: su una scheda
        vera quella direzione mente — l'«Auto Gain Control» di un microfono USB si dichiara manopola di
        riproduzione — e nascondere la manopola che conta è peggio che mostrarne una in più.
        """
        schede = {}
        for c in alsa_cards():
            manopole = self.manopole.leggi(c["id"])
            if manopole:
                schede[c["id"]] = {"nome": c["nome"], "tipo": c["tipo"], "suona": c["suona"],
                                   "registra": c["registra"], "manopole": manopole}
        return {"schede": schede}

    def set_manopola(self, dati: dict) -> dict:
        """Gira una manopola e la ricorda (``POST /configura/manopole``, API 1.6)."""
        scheda, manopola = str(dati.get("scheda", "")), str(dati.get("manopola", ""))
        valore = str(dati.get("valore", ""))
        if scheda not in {c["id"] for c in alsa_cards()}:
            raise ActionError("scheda audio non presente su questo nodo", 400)
        if not any(mixer.chiave(m) == manopola for m in self.manopole.leggi(scheda)):
            raise ActionError("questa scheda non ha quella manopola", 400)
        try:
            fatto = self.manopole.gira(scheda, manopola, valore)
        except ValueError as exc:
            raise ActionError(str(exc), 400) from exc
        if not fatto:
            raise ActionError("la scheda non ha accettato la manopola", 503)
        stato = self.manopole_state()
        stato["esito_richiesta"] = "ok"
        return stato

    def dispositivi_state(self) -> dict:
        """I dispositivi audio del nodo, raggruppati come li mostra il pannello (``GET /configura/dispositivi``, API 1.7).

        Il pannello non chiede più «dove deve suonare la sirena»: chiede **quali dispositivi ci sono**
        e com'è regolato ognuno. Dove suona una cosa è una scelta dell'azione, non una proprietà del
        nodo — una casa può volere la sirena dalla cassa di fuori e un dissuasore da quella del
        giardino, e con una sola uscita per nodo non si può dire.

        Finché l'azione non sa scegliere (tappa 2) resta un ``predefinito``, che è l'uscita di oggi:
        così niente smette di funzionare nel frattempo.
        """
        a = dict(self.audio_cfg() if self.audio_cfg is not None else {})
        schede = alsa_cards()
        predefinito = {"uscita": str(a.get("uscita", "bluetooth")), "scheda": str(a.get("scheda", "")),
                       "bluetooth_mac": str(a.get("bluetooth_mac", ""))}

        def dispositivo(c: dict) -> dict:
            return {"scheda": c["id"], "nome": c["nome"], "manopole": self.manopole.leggi(c["id"])}

        jack = next((c for c in schede if c["tipo"] == "jack" and c.get("suona", True)), None)
        # L'HDMI c'è sempre su un Raspberry, acceso o spento: si mostra solo se dall'altra parte c'è
        # davvero uno schermo, o se è il dispositivo che il nodo sta usando (altrimenti sparirebbe
        # sotto i piedi a chi l'ha scelto).
        mostra_hdmi = schermo_attaccato() or predefinito["uscita"] == "hdmi"
        return {
            "predefinito": predefinito,
            "jack": {"presente": jack is not None, "attivo": bool(a.get("jack", True)),
                     **(dispositivo(jack) if jack else {})},
            "usb": [dispositivo(c) for c in schede if c["tipo"] == "usb" and c.get("suona", True)],
            "hdmi": [dispositivo(c) for c in schede if c["tipo"] == "hdmi"] if mostra_hdmi else [],
            "bluetooth": {"casse": casse_abbinate(),
                          "trovate": self._file_json(CASSE_TROVATE),
                          "esito": self._file_json(AUDIO_ESITO)},
        }

    def camera_state(self) -> dict:
        """Com'è la camera adesso e cosa si può scegliere (``GET /configura/camera``).

        ``sensore`` è quello che il sistema vede davvero: vuoto vuol dire che la camera è configurata
        ma non c'è, o che il cavo piatto non è in sede — il caso in cui Mirto mostrerebbe per sempre
        una camera che non trasmette.
        """
        cam = dict(self.camera_cfg() if self.camera_cfg is not None else {})
        sensore = detect_sensor()
        return {"attiva": bool(cam.get("attiva", False)), "noir": bool(cam.get("noir", False)),
                "larghezza": int(cam.get("larghezza", 1280)), "altezza": int(cam.get("altezza", 720)),
                "fps": int(cam.get("fps", 15)), "sensore": sensore,
                "vista_dal_sistema": bool(sensore),
                "risoluzioni": [{"etichetta": e, "larghezza": w, "altezza": h, "fps": f}
                                for e, w, h, f in RESOLUTIONS]}

    def set_camera(self, dati: dict) -> dict:
        """Camera accesa/spenta, NoIR e risoluzione dal wizard di Mirto (``POST /configura/camera``).

        I nodi avranno camere diverse — NoIR per chi ha i faretti a infrarossi, normali per gli altri —
        e il modello non si può indovinare: il sensore è lo stesso. Finché la scelta stava solo in
        ``mirto-nodo camera``, ogni nodo voleva un terminale.

        Il nodo esegue e non interpreta: la risoluzione dev'essere una di quelle proposte, e il lavoro
        vero (MediaMTX, annuncio in rete, riavvio dei servizi) lo fa root sul biglietto che lasciamo.
        """
        if self.set_camera_cfg is None:
            raise ActionError("questo nodo non sa configurare la camera da Mirto", 501)
        attiva = bool(dati.get("attiva", True))
        valori = {"attiva": attiva}
        if attiva:
            terna = risoluzione_valida(dati.get("larghezza"), dati.get("altezza"), dati.get("fps"))
            if terna is None:
                raise ActionError("risoluzione non fra quelle proposte", 400)
            w, h, fps = terna
            valori.update({"noir": bool(dati.get("noir", False)), "larghezza": w, "altezza": h, "fps": fps})
        with self._lock:
            try:
                self.set_camera_cfg(valori)
            except OSError as exc:
                log.error("Camera non salvata in node.toml: %s", exc)
                raise ActionError("il nodo non riesce a salvare la configurazione della camera", 500) from exc
            if self.folder is not None:
                chiedi_a_root(self.folder, CAMERA_DA_APPLICARE, "camera\n")
        log.info("Camera: %s (chiesto da Mirto)",
                 f"{valori['larghezza']}×{valori['altezza']} · {valori['fps']} fps"
                 f"{' · NoIR' if valori.get('noir') else ''}" if attiva else "spenta")
        stato = self.camera_state()
        stato["esito"] = "ok"
        return stato

    def all_keys(self) -> list[str]:
        """Le chiavi accettate adesso: una per abbinamento, o la sola chiave passata al costruttore."""
        if self.keys is None:
            return [self._key_hex] if self._key_hex else []
        return self.keys.keys()

    def refresh_keys(self, force: bool = False) -> None:
        """Rilegge le chiavi se sono cambiate su disco (``mirto-nodo abbina`` gira in un altro processo)."""
        if self.keys is None:
            return
        stamp = self.keys.stamp()
        if force or stamp != self._keys_stamp:
            self._keys_stamp = stamp
            self.verifier.set_keys(self.all_keys())

    def info(self) -> dict:
        """Carta d'identità del nodo (API 1.1): tipo, camere con credenziali, dispositivi, audio."""
        model = self._model()
        creds = self._credentials()
        cams = []
        for c in self._cameras():
            cam = {"id": c.get("id") or c["nome"], "nome": c["nome"], "rtsp_porta": c["porta"],
                   "percorso": c["percorso"]}
            if creds:
                cam["utente"], cam["password"] = creds
            cams.append(cam)
        # Che questo nodo sappia suonare da più casse insieme (API 1.11) non si dice qui: lo dice
        # già `versione_api`, e Mirto quella la guarda di suo. Un campo in più che vuol dire la
        # stessa cosa è il doppione che poi si dimentica di aggiornare, e a quel punto i due si
        # contraddicono e nessuno sa quale credere.
        audio = {"uscita": self.player.output if self.player else "", "microfono": self.mic is not None}
        extra = {"registrazione": True} if self.recorder is not None else {}
        return {"nome": self.name, "tipo": "raspberry" if "raspberry" in model.lower() else "linux",
                "modello": model, "versione_api": API_VERSION, "versione": __version__,
                "camere": cams, "dispositivi": self.actions_list(), "audio": audio,
                "azioni": list(self.devices), **extra}

    def actions_list(self) -> list[dict]:
        out = []
        for n, d in self.devices.items():
            item = {"nome": n, "tipo": d.kind, "descrizione": d.description}
            if isinstance(d, DeterrentDevice):
                item["suoni"] = d.names()
                # Quanto dura ogni suono (API 1.8). Il nodo lo sa da sempre — i WAV ce li ha in mano
                # lui — e non lo diceva a nessuno. Senza, Mirto non può chiedere «fai questo suono
                # **tre volte**»: può solo dire «fallo per N secondi», e il nodo lo ripete finché il
                # tempo scade. Chi configura chiedeva uno sparo e ne sentiva tre.
                item["durate"] = {n: round(d.length(n), 2) for n in d.names()}
            out.append(item)
        return out

    # --- i suoni di chi usa il programma ---------------------------------------
    # Caricarli dalle impostazioni invece che da dentro un'azione: sono due momenti diversi, «sto
    # preparando la mia libreria» e «sto costruendo questa azione», e chi prepara la libreria non deve
    # passare da un'azione per farlo (chiesto da Marco il 23/10).

    def _suoni_device(self) -> "DeterrentDevice | None":
        """Il dispositivo che tiene i suoni caricati: il dissuasore, se questo nodo ce l'ha."""
        for d in self.devices.values():
            if isinstance(d, DeterrentDevice) and not isinstance(d, GeneratedAudioDevice):
                return d
        return None

    def sounds_state(self) -> dict:
        """L'elenco dei suoni, con i **limiti dichiarati**.

        I limiti si dicono prima, non si scoprono dopo: chi sta per scegliere un file deve sapere
        quanto può durare e quanto può pesare, invece di aspettare un minuto per sentirsi dire di no.
        """
        d = self._suoni_device()
        nostri = set(BUILTIN_SOUNDS)
        suoni = [{"nome": n, "secondi": round(d.length(n), 2), "nostro": n in nostri}
                 for n in (d.names() if d else [])]
        return {"suoni": suoni, "massimo_s": MAX_SUONO_SECONDI, "massimo_byte": MAX_SUONO_BYTE,
                "cartella": str(self.cartella_suoni or "")}

    def aggiorna(self, dati: dict) -> dict:
        """Riceve il codice del nodo da Mirto e prepara l'installazione (N-02, API 1.12).

        **Perché il pacchetto lo manda Mirto** invece di scaricarlo il nodo: la fiducia fra i due
        esiste già e non è da inventare — l'abbinamento ha lasciato una chiave, ogni richiesta è
        firmata e la firma copre l'**impronta del corpo**, quindi questi byte arrivano da chi ha la
        chiave di questo nodo e nessun altro. Scaricarlo da internet vorrebbe dire una chiave di
        rilascio nuova da creare e proteggere, e internet sul nodo. Qui non serve niente di tutto ciò.

        **Chi fa cosa.** Questo metodo gira senza privilegi (S-03): verifica, scompatta in una
        cartella sua, e lascia un biglietto. A installare ci pensa `boot-segnali.sh`, che è di root —
        lo stesso meccanismo del nome della macchina e della camera. Non è una barriera di sicurezza
        e non fingo che lo sia: chi comanda questo servizio decide cosa root installerà. È il patto
        di qualunque aggiornamento, e vale la pena scriverlo invece di lasciarlo credere.

        La risposta torna **prima** che l'installazione cominci: `install.sh` riavvia questo servizio,
        quindi una risposta «ho finito» non potrebbe mai arrivare. Mirto guarda la versione in
        `/stato` per sapere com'è andata.
        """
        import base64  # noqa: PLC0415
        import hashlib  # noqa: PLC0415

        versione = str(dati.get("versione") or "").strip()[:32]
        impronta = str(dati.get("sha256") or "").strip().lower()
        try:
            crudo = base64.b64decode(str(dati.get("pacchetto") or ""), validate=True)
        except Exception as exc:  # noqa: BLE001
            raise ActionError(f"il pacchetto non è arrivato intero: {exc}", 400) from exc
        if not crudo:
            raise ActionError("il pacchetto è vuoto", 400)
        if len(crudo) > MAX_PACCHETTO:
            raise ActionError(f"il pacchetto pesa troppo: al massimo {MAX_PACCHETTO // 1024 // 1024} MB", 413)
        vero = hashlib.sha256(crudo).hexdigest()
        if impronta and not hmac.compare_digest(vero, impronta):
            # L'impronta dichiarata è una cintura in più sopra la firma: se non combacia, qualcosa è
            # andato storto fra chi ha costruito il pacchetto e chi l'ha mandato, e non si installa.
            raise ActionError("l'impronta del pacchetto non combacia", 400)
        if self.folder is None:
            raise ActionError("questo nodo non ha una cartella di configurazione", 400)
        dove = self.folder / "nuovo"
        try:
            fs.scompatta(crudo, fs.svuota(dove), max_file=MAX_FILE_PACCHETTO)
        except ActionError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ActionError(f"il pacchetto non si apre: {exc}", 400) from exc
        if not (dove / "install.sh").is_file() or not (dove / "mirto_node" / "__init__.py").is_file():
            raise ActionError("questo non è il codice di un nodo Mirto", 400)
        # L'esito del tentativo **di prima** si cancella adesso. Il 26/10 un «fallito» rimasto dal
        # tentativo di stamattina ha fatto dire a Mirto «il nodo non è riuscito a installare»
        # mentre l'installazione nuova stava ancora andando: un esito senza un tentativo a cui
        # appartenere è peggio di nessun esito, perché ha l'aria di una risposta.
        (self.folder / "pacchetto-esito").unlink(missing_ok=True)
        chiedi_a_root(self.folder, "pacchetto-da-installare", f"{versione}\n{vero}\n")
        log.warning("Aggiornamento %s ricevuto (%d byte): installazione in corso", versione or "?", len(crudo))
        return {"esito": "ok", "in_corso": True, "versione": versione, "da": __version__,
                "sha256": vero}

    def esito_aggiornamento(self) -> dict:
        """Com'è finita l'ultima installazione, se ce n'è stata una. Lo scrive `boot-segnali.sh`."""
        if self.folder is None:
            return {}
        testo = ""
        try:
            testo = (self.folder / "pacchetto-esito").read_text(encoding="utf-8", errors="replace")[:500]
        except OSError:
            return {}
        righe = [r.strip() for r in testo.splitlines() if r.strip()]
        if not righe:
            return {}
        return {"esito": righe[0], "versione": righe[1] if len(righe) > 1 else "",
                "messaggio": " ".join(righe[2:])[:300]}

    def add_sound(self, dati: dict) -> dict:
        """Salva un suono WAV mandato da Mirto e lo rende disponibile **subito**, senza riavvio."""
        import base64  # noqa: PLC0415
        import re as _re  # noqa: PLC0415

        d = self._suoni_device()
        if d is None or not self.cartella_suoni:
            raise ActionError("questo nodo non ha un posto dove tenere i suoni", 400)
        nome = _re.sub(r"[^\w-]", "_",
                       str(dati.get("nome") or "").strip().rsplit(".", 1)[0].lower())[:40].strip("_-")
        if not nome:
            raise ActionError("serve un nome per il suono", 400)
        if nome in BUILTIN_SOUNDS:
            raise ActionError(f"«{nome}» è un suono nostro: scegline un altro nome", 409)
        try:
            crudo = base64.b64decode(str(dati.get("wav") or ""), validate=True)
        except Exception as exc:  # noqa: BLE001
            raise ActionError(f"il suono non è arrivato intero: {exc}", 400) from exc
        if not crudo:
            raise ActionError("il suono è vuoto", 400)
        if len(crudo) > MAX_SUONO_BYTE:
            raise ActionError(f"il suono pesa troppo: al massimo {MAX_SUONO_BYTE // 1024} KB", 413)
        rate = getattr(self.player, "rate", RATE)
        try:
            # Prima si guarda **quanto dura**, poi si converte: tagliarlo di nascosto vorrebbe dire
            # far sentire mezza frase senza dirlo a nessuno.
            pcm_intero = wav_to_pcm(crudo, rate, max_seconds=MAX_SUONO_SECONDI + 1)
        except Exception as exc:  # noqa: BLE001
            raise ActionError(f"non è un WAV che so leggere: {exc}", 400) from exc
        secondi = len(pcm_intero) / (2 * rate)
        if secondi > MAX_SUONO_SECONDI:
            raise ActionError(f"il suono dura {secondi:.0f} secondi: al massimo {MAX_SUONO_SECONDI}", 413)
        self.cartella_suoni.mkdir(parents=True, exist_ok=True)
        (self.cartella_suoni / f"{nome}.wav").write_bytes(crudo)
        d.sounds[nome] = pcm_intero
        log.info("Suono «%s» caricato (%.1f s)", nome, secondi)
        return {"esito": "ok", "nome": nome, "secondi": round(secondi, 2)}

    def remove_sound(self, dati: dict) -> dict:
        d = self._suoni_device()
        nome = str(dati.get("nome") or "").strip().lower()
        if nome in BUILTIN_SOUNDS:
            raise ActionError(f"«{nome}» è un suono nostro: non è un file, è generato dal nodo", 409)
        if d is None or nome not in d.sounds:
            raise ActionError(f"suono «{nome}» non presente sul nodo", 404)
        if self.cartella_suoni:
            (self.cartella_suoni / f"{nome}.wav").unlink(missing_ok=True)
        d.sounds.pop(nome, None)
        log.info("Suono «%s» tolto", nome)
        return {"esito": "ok", "nome": nome}

    def _deterrent_budget(self, now: float) -> float:
        self._deterrent_log = [(t, d) for t, d in self._deterrent_log if now - t < DETERRENT_WINDOW_SECONDS]
        return DETERRENT_BUDGET_SECONDS - sum(d for _t, d in self._deterrent_log)

    def prank_resta(self, now: float | None = None) -> float:
        """Secondi che restano alla modalità Prank. ``0`` se è spenta: è la stessa domanda."""
        return max(0.0, self._prank_until - (self.clock() if now is None else now))

    def _deterrent_disponibile(self, now: float) -> float:
        """Quanto può suonare **adesso** un dissuasore.

        Con la modalità Prank accesa il budget non si guarda: resta il tetto per scatto, che è
        l'unica cosa che impedisce a un anello di una catena di suonare per ore.
        """
        if self.prank_resta(now):
            return float(DETERRENT_MAX_SECONDS)
        return self._deterrent_budget(now)

    @staticmethod
    def _quando_riprende(registro: list[tuple[float, float]], budget: float,
                         finestra: float, now: float) -> float:
        """Fra quanti secondi tornerà disponibile **almeno un secondo** di quel budget.

        Il nodo tiene il registro degli scatti della finestra, quindi *quando* riprende è un calcolo
        e non una stima: ogni scatto esce dalla finestra a ``t + finestra``, e il primo momento utile
        è quello in cui ne sono usciti abbastanza. Marco, 25/10: *«i messaggi sui budget devono
        essere chiari»* — e «riprende fra 12 minuti» è un'altra cosa rispetto a «limite raggiunto»,
        che dice che è inutile insistere ma non dice fino a quando.
        """
        usato = sum(d for _t, d in registro)
        for t, d in sorted(registro):
            usato -= d
            if budget - usato >= 1:
                return max(0.0, t + finestra - now)
        return 0.0

    def _cassa_di(self, dev: Device, uscita: str) -> str:
        """Su quale cassa finirebbe quest'operazione. Vuoto: non suona, o non si sa da dove."""
        if not dev.sceglie_uscita:
            return ""
        p = getattr(dev, "player", None) or self.player
        if p is None:
            return ""
        return quale_cassa(uscita, p.output, p.card, p.bt_mac)

    def _chi_occupa(self, cassa: str, now: float, tranne: str = "") -> str:
        """Quale azione sta già suonando da quella cassa, se c'è. Una cassa è una linea sola."""
        if not cassa:
            return ""
        for altra, fino in list(self.until.items()):
            if altra != tranne and fino > now and self._casse.get(altra) == cassa:
                return altra
        return ""

    def _siren_budget(self, now: float) -> float:
        self._siren_log = [(t, d) for t, d in self._siren_log if now - t < SIREN_WINDOW_SECONDS]
        return SIREN_MAX_SECONDS - sum(d for _t, d in self._siren_log)

    def _niente_budget(self, cosa: str, registro: list[tuple[float, float]], budget: float,
                       finestra: float, now: float) -> ActionError:
        """Il rifiuto per budget finito: dice quanto può suonare, e **quando riprende**."""
        riprende = self._quando_riprende(registro, budget, finestra, now)
        quanto = int(budget // 60) or 1
        if finestra == 3600:
            quando = "nell'ultima ora"
        elif finestra == 1800:
            quando = "nell'ultima mezz'ora"
        else:
            quando = f"negli ultimi {int(finestra // 60)} minuti"
        chi = "la sirena ha" if cosa == "sirena" else "i dissuasori hanno"
        frase = (f"{chi} già suonato {quanto} minuti {quando}, che è il massimo consentito: "
                 f"riprende {fra_quanto(riprende)}")
        return ActionError(frase, 409, {"motivo": f"budget_{cosa}", "riprende_fra_s": round(riprende),
                                        "budget_s": round(budget), "finestra_s": round(finestra)})

    def set_prank(self, dati: dict) -> dict:
        """``POST /prank`` — accende o spegne la modalità Prank (API 1.13).

        ``minuti`` è la durata: ``0`` spegne. La chiede il computer, la concede il nodo — come per
        ogni altra cosa — perché la regola che la modalità sospende vive qui, e una garanzia che si
        può togliere da fuori non è una garanzia.
        """
        try:
            minuti = float(dati.get("minuti", PRANK_MINUTI))
        except (TypeError, ValueError):
            raise ActionError("minuti non validi", 400) from None
        if minuti < 0:
            raise ActionError("minuti non validi", 400)
        minuti = min(minuti, PRANK_MAX_MINUTI)
        now = self.clock()
        with self._lock:
            self._prank_until = now + minuti * 60 if minuti else 0.0
            resta = self.prank_resta(now)
        log.warning("Modalità Prank %s", f"ACCESA per {minuti:.0f} minuti (niente budget dei dissuasori)"
                    if resta else "spenta")
        return {"esito": "ok", "prank": self.prank_state(now)}

    def prank_state(self, now: float | None = None) -> dict:
        """Come lo vede Mirto: accesa o no, e quanto le resta. Lo stesso dato per i tre posti che
        lo mostrano — Sistema, scheda del nodo, tabella della flotta — perché una modalità che
        cambia le regole di sicurezza non può essere scritta in tre modi diversi."""
        resta = self.prank_resta(now)
        return {"attiva": bool(resta), "restano_s": round(resta), "massimo_minuti": PRANK_MAX_MINUTI}

    def activate(self, name: str, payload: dict) -> dict:
        dev = self.devices.get(name)
        if dev is None:
            raise KeyError(name)
        try:
            duration = float(payload.get("durata", 60))
        except (TypeError, ValueError):
            raise ActionError("durata non valida", 400) from None
        duration = max(1.0, min(duration, GENERIC_MAX_SECONDS))
        now = self.clock()
        with self._lock:
            if self.until.get(name, 0) > now:
                return {"esito": "ok", "azione": name, "gia_attiva": True,
                        "restano_s": round(self.until[name] - now, 1)}
            if dev.kind == "siren":
                duration = min(duration, SIREN_MAX_SECONDS, self._siren_budget(now))
                if duration < 1:
                    raise self._niente_budget("sirena", self._siren_log, SIREN_MAX_SECONDS,
                                              SIREN_WINDOW_SECONDS, now)
            sound = ""
            # Da quale uscita suonare (API 1.10): è una richiesta precisa, non un suggerimento —
            # se quell'uscita non risponde il nodo lo dice, invece di suonare da un'altra parte.
            uscita = str(payload.get("uscita", "") or "")
            if uscita and not dev.sceglie_uscita:
                raise ActionError(f"«{name}» non suona: l'uscita non si sceglie", 400)
            # Una cassa è una linea: se qualcuno ci sta già suonando, questa operazione **non parte**
            # (API 1.11). Fino al 24/10 il nodo rispondeva «ok» e `loop()` ammazzava il suono di
            # prima: due conferme, due timer, un suono. Da un'altra cassa invece parte, ed è la
            # capacità nuova — il suono dalla bluetooth e la voce dalla cassa USB nello stesso istante.
            cassa = self._cassa_di(dev, uscita)
            occupata = self._chi_occupa(cassa, now, tranne=name)
            if occupata:
                raise ActionError(f"«{occupata}» sta già suonando da quella cassa: aspetta")
            if isinstance(dev, DeterrentDevice):
                sound = dev.pick(str(payload.get("suono", "") or ""))
                # «volte»: quante ripetizioni esatte (API 1.8). I secondi li calcola il nodo, che è
                # l'unico a sapere quanto dura il file; se le ripetizioni chieste sforano il limite
                # del dissuasore se ne fanno quante ci stanno, invece di tagliare l'ultima a metà.
                try:
                    volte = max(0, int(payload.get("volte") or 0))
                except (TypeError, ValueError):
                    volte = 0
                lungo = dev.length(sound)
                massimo = min(DETERRENT_MAX_SECONDS, self._deterrent_disponibile(now))
                if massimo < 1:
                    raise self._niente_budget("dissuasore", self._deterrent_log,
                                              DETERRENT_BUDGET_SECONDS, DETERRENT_WINDOW_SECONDS, now)
                if volte and lungo > 0:
                    volte = max(1, min(volte, int(massimo // lungo) or 1))
                    # Con le ripetizioni la durata è quella che è: un suono corto dura meno di un
                    # secondo, ed è giusto così. Il «meno di un secondo» di prima controllava il
                    # budget travestito da durata; adesso il budget si guarda per quello che è.
                    duration = min(volte * lungo + 0.2, massimo)
                else:
                    volte = 0
                    duration = min(duration, DETERRENT_MAX_SECONDS, self._deterrent_disponibile(now))
                    if duration < 1:
                        raise self._niente_budget("dissuasore", self._deterrent_log,
                                                  DETERRENT_BUDGET_SECONDS, DETERRENT_WINDOW_SECONDS, now)
                dev.start(duration, sound, volte, uscita=uscita)
                # Con la modalità Prank accesa lo scatto **non si segna**: se si segnasse, allo
                # scadere della modalità il budget risulterebbe già speso e per un'ora non suonerebbe
                # più niente — una modalità che si spegne da sé non può lasciare dietro di sé un'ora
                # di silenzio che nessuno ha chiesto. Finché è accesa non si conta; quando si spegne
                # il conto riparte da zero, e questa è la regola da dire a chi la accende.
                if not self.prank_resta(now):
                    self._deterrent_log.append((now, duration))
            elif dev.sceglie_uscita:
                dev.start(duration, uscita=uscita)
            else:
                dev.start(duration)
            if dev.kind == "siren":
                self._siren_log.append((now, duration))
            self.until[name] = now + duration
            if cassa:
                self._casse[name] = cassa
            t = threading.Timer(duration, self._expire, args=(name,))
            t.daemon = True
            self._timers[name] = t
            t.start()
        log.info("%s ACCESA per %.0f s%s (evento %s, camera %s)", name, duration,
                 f", suono {sound}" if sound else "", payload.get("evento_id", ""), payload.get("camera", ""))
        res = {"esito": "ok", "azione": name, "durata": duration}
        if sound:
            res["suono"] = sound
        return res

    def _expire(self, name: str) -> None:
        with self._lock:
            if self.until.get(name, 0) > self.clock() + 0.5:
                return  # riattivata nel frattempo
            self._off(name)
        log.info("%s spenta (scadenza)", name)

    def _off(self, name: str) -> None:
        self.until.pop(name, None)
        self._casse.pop(name, None)   # la cassa torna libera: un'altra operazione può prenderla
        t = self._timers.pop(name, None)
        if t is not None and t is not threading.current_thread():
            t.cancel()
        try:
            self.devices[name].stop()
        except Exception as exc:  # noqa: BLE001
            log.error("Spegnimento di %s fallito: %s", name, exc)

    def deactivate(self, name: str) -> dict:
        if name not in self.devices:
            raise KeyError(name)
        with self._lock:
            self._off(name)
        log.info("%s spenta (comando)", name)
        return {"esito": "ok", "azione": name}

    def stop_all(self) -> dict:
        with self._lock:
            active = list(self.until)
            # «Ferma tutto» spegne anche la modalità Prank. Chi lo premia vuole che la casa torni
            # muta: lasciare accesa la modalità che toglie il budget vorrebbe dire che il prossimo
            # scatto, un minuto dopo, può suonare come se niente fosse stato fermato.
            prank = bool(self.prank_resta())
            self._prank_until = 0.0
            for name in list(self.devices):
                self._off(name)
        log.info("FERMA TUTTO (erano accese: %s%s)", ", ".join(active) or "nessuna",
                 ", modalità Prank spenta" if prank else "")
        return {"esito": "ok", "spente": active, "prank_spenta": prank}

    def pairing_state(self, public: bool = False) -> dict:
        """Stato dell'abbinamento. ``public``: solo l'essenziale, per ``GET /abbina`` che non è firmato."""
        if self.pairing is None:
            return {"aperto": False, "scade_tra_s": 0, "abbinato": True}
        left = self.pairing.remaining()
        # Mirto si ricalcola la scadenza a ogni ricerca: a una finestra che non scade si manda la
        # durata normale, e ogni ricerca la rinnova. In più il campo esplicito, per chi vuole dirlo.
        senza = left == PairingWindow.SENZA_SCADENZA
        st = {"aperto": left != 0, "scade_tra_s": self.pairing.seconds if senza else left,
              "abbinato": self.pairing.paired(), "senza_scadenza": senza}
        if public:
            return st
        st.update(self.pairing.paired_info())  # da chi e quando: Mirto lo mostra in Sistema → Nodi
        if self.keys is not None:
            st["chiavi"] = len(self.keys.files())
            # finché è valida, chi la conosce comanda il nodo: Mirto chiede di ritirarla
            st["chiave_installazione"] = bool(self.keys.install_key_hex())
            st["ritiro_installazione_tra_s"] = self.keys.retire_in()
        return st

    def status(self) -> dict:
        now = self.clock()
        with self._lock:
            active = {k: round(v - now, 1) for k, v in self.until.items() if v > now}
            budget = max(0.0, self._siren_budget(now))
            det_budget = max(0.0, self._deterrent_budget(now))
        st = {"nome": self.name, "versione": __version__, "attive": active,
              "sirena_disponibile_s": round(budget), "attivo_da_s": int(now - self.started)}
        if any(isinstance(d, DeterrentDevice) for d in self.devices.values()):
            st["dissuasore_disponibile_s"] = round(det_budget)
            st["dissuasore_riprende_fra_s"] = round(self._quando_riprende(
                self._deterrent_log, DETERRENT_BUDGET_SECONDS, DETERRENT_WINDOW_SECONDS, now))
            # La modalità Prank sta nello stato **anche quando è spenta**: è così che Mirto sa che
            # questo nodo la sa fare, e può offrirla senza chiedere niente in più.
            st["prank"] = self.prank_state(now)
        st["abbinamento"] = self.pairing_state()
        # Com'è finita l'ultima installazione (N-02). Sta qui e non in una rotta sua perché Mirto
        # lo stato lo chiede già, e dopo un aggiornamento la prima cosa che fa è richiederlo: così
        # l'esito lo trova dove sta già guardando, invece di dover sapere di doverlo cercare.
        esito = self.esito_aggiornamento()
        if esito:
            st["aggiornamento"] = esito
        try:
            st["temperatura_c"] = round(int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000, 1)
        except (OSError, ValueError):
            pass
        if self.player is not None:
            st["audio"] = self.player.status()
        # Il microfono sta nello stato periodico dall'API 1.13: è il posto dove Mirto guarda sempre,
        # e tre schermate lo vogliono. Prima viveva solo in `GET /configura/audio`.
        st["microfono"] = self.mic_state()
        st["audio_registrazione"] = self.audio_reg_state()
        st["volume"] = self.volume_state()
        if self.recorder is not None:
            st["registrazione"] = self.recorder.status()
        # camere trasmesse da questo Raspberry e i suoi indirizzi: Mirto le collega al nodo
        try:
            st["camere"] = self._cameras()
            st["host"] = self._hostname()
            st["indirizzi"] = self._addresses()
            st["rete"] = stato_rete()          # cavo o Wi-Fi: la prima cosa da sapere se il nodo sparisce
            # impronta della carta d'identità: se cambia (es. nuova password della camera) Mirto la rilegge
            import hashlib  # noqa: PLC0415
            import json as _json  # noqa: PLC0415
            st["info_rev"] = hashlib.sha256(_json.dumps(self.info(), sort_keys=True).encode()).hexdigest()[:12]
        except Exception as exc:  # noqa: BLE001
            log.warning("Lettura delle camere del nodo fallita: %s", exc)
        return st


def _pair(node: Node, client: str, address: str, pin: str) -> tuple[str | None, str]:
    """Chiave dedicata a questo abbinamento, oppure (None, motivo del rifiuto)."""
    if node.pairing is None:
        return None, "questo nodo non si abbina da Mirto"
    ok, why = node.pairing.claim(f"{client} · {address}", pin)
    if not ok:
        return None, why
    if node.keys is None:
        return node._key_hex, ""  # nodo di prova senza cartella delle chiavi
    key = node.keys.create(client, address)
    node.refresh_keys(force=True)
    log.warning("Nodo abbinato da %s: consegnata una chiave dedicata", client)
    return key, ""


# --- HTTP ---------------------------------------------------------------------
def make_handler(node: Node):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"MirtoNode/{__version__}"

        def log_message(self, fmt, *args):
            log.debug(fmt, *args)

        def _send(self, code: int, obj) -> None:
            data = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _limite_corpo(self) -> int:
            return CORPI_MASSIMI.get(self.path.split("?")[0], CORPO_MASSIMO)

        def _body(self) -> bytes:
            """Legge il corpo, dopo aver rifiutato le lunghezze impossibili o fuori limite.

            Un «Content-Length» negativo diventava ``rfile.read(-1)``, cioè «leggi fino a quando
            la connessione si chiude»: un modo per tenere il nodo appeso senza mandare niente.
            """
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                raise ActionError("Content-Length non è un numero", 400) from None
            if n < 0:
                raise ActionError("Content-Length negativo", 400)
            if n > self._limite_corpo():
                raise ActionError("richiesta troppo grande", 413)
            return self.rfile.read(n) if n else b""

        def _precheck(self) -> bool:
            """Firma assente o scaduta: si risponde senza leggere il corpo (vedi Verifier.precheck)."""
            ok, why = node.verifier.precheck(self.headers)
            if not ok:
                log.warning("Richiesta rifiutata da %s senza leggere il corpo: %s",
                            self.client_address[0], why)
                self._send(401, {"errore": why})
            return ok

        def _auth(self, body: bytes) -> bool:
            node.refresh_keys()  # una chiave appena creata da  mirto-nodo  vale subito
            ok, why = node.verifier.verify(self.command, self.path, self.headers, body)
            if not ok:
                log.warning("Richiesta rifiutata da %s: %s", self.client_address[0], why)
                self._send(401, {"errore": why})
            elif node.pairing is not None and not node.pairing.paired():
                try:  # non deve mai far fallire una richiesta valida
                    node.pairing.mark_paired(f"chiave · {self.client_address[0]}")
                except OSError as exc:
                    log.warning("Stato dell'abbinamento non salvabile: %s", exc)
            return ok

        def _pairing(self, body: bytes) -> None:
            if not _local_client(self.client_address[0]):
                self._send(403, {"errore": "abbinamento possibile solo dalla rete di casa"})
                return
            st = node.pairing_state(public=True)
            if self.command == "GET":
                self._send(200, {"nome": node.name, "versione_api": API_VERSION, **st})
                return
            try:
                data = json.loads(body or b"{}")
                client = str(data.get("client", ""))[:60] or "Mirto"
                pin = str(data.get("pin", ""))[:20]
            except (ValueError, AttributeError):
                client, pin = "Mirto", ""
            try:
                key, why = _pair(node, client, self.client_address[0], pin)
            except OSError as exc:
                log.error("Abbinamento non salvabile: %s", exc)
                self._send(500, {"errore": "il nodo non riesce a salvare l'abbinamento"})
                return
            if key is None:
                self._send(403, {"errore": why})
            else:
                self._send(200, {"chiave": key, "nome": node.name, "versione_api": API_VERSION})

        timeout = TIMEOUT_CONNESSIONE          # lo usa socketserver sul socket della richiesta
        semaforo = threading.BoundedSemaphore(RICHIESTE_IN_PARALLELO)

        def _con_tetto(self, lavoro) -> None:
            """Esegue la richiesta solo se c'è posto: oltre il tetto, un no onesto e immediato."""
            if not self.semaforo.acquire(blocking=False):
                log.warning("Troppe richieste insieme: 503 a %s", self.client_address[0])
                self._send(503, {"errore": "troppe richieste insieme, riprova"})
                return
            try:
                lavoro()
            finally:
                self.semaforo.release()

        def do_GET(self):  # noqa: N802
            self._con_tetto(self._get)

        def do_POST(self):  # noqa: N802
            self._con_tetto(self._post)

        def _get(self):
            if self.path == "/abbina":
                self._pairing(b"")
                return
            if not self._auth(b""):
                return
            if self.path == "/info":
                self._send(200, node.info())
            elif self.path == "/azioni":
                self._send(200, {"azioni": node.actions_list()})
            elif self.path == "/stato":
                self._send(200, node.status())
            elif self.path == "/configura/camera":
                self._send(200, node.camera_state())
            elif self.path == "/configura/audio":
                self._send(200, node.audio_state())
            elif self.path == "/configura/manopole":
                self._send(200, node.manopole_state())
            elif self.path == "/configura/dispositivi":
                self._send(200, node.dispositivi_state())
            elif self.path == "/configura/wifi":
                self._send(200, node.wifi_state())
            elif self.path == "/configura/disco":
                self._send(200, node.disk_state())
            elif self.path == "/configura/abbinamenti":
                self._send(200, node.pairings())
            elif self.path == "/suoni":
                self._send(200, node.sounds_state())
            elif self.path == "/audio/flusso":
                self._stream_mic()
            elif self.path.split("?")[0] in ("/registrazioni", "/registrazioni/file"):
                self._recordings()
            else:
                self._send(404, {"errore": "percorso sconosciuto"})

        def _post(self):
            if self.path != "/abbina" and not self._precheck():
                return      # senza firma valida il corpo non si legge nemmeno
            try:
                body = self._body()
            except ActionError as exc:
                self._send(exc.code, {"errore": str(exc), **exc.dati})
                return
            if self.path == "/abbina":
                self._pairing(body)
                return
            if not self._auth(body):
                return
            parts = self.path.strip("/").split("/")
            try:
                if parts == ["audio", "riproduci"]:
                    self._play(body)
                elif parts == ["audio", "registra"]:
                    self._record(body)
                elif parts == ["suoni"]:
                    dati = json.loads(body or b"{}")
                    if not isinstance(dati, dict):
                        raise ValueError("corpo non valido")
                    self._send(200, node.add_sound(dati))
                elif parts == ["aggiorna"]:
                    dati = json.loads(body or b"{}")
                    if not isinstance(dati, dict):
                        raise ValueError("corpo non valido")
                    self._send(200, node.aggiorna(dati))
                elif parts == ["suoni", "togli"]:
                    dati = json.loads(body or b"{}")
                    if not isinstance(dati, dict):
                        raise ValueError("corpo non valido")
                    self._send(200, node.remove_sound(dati))
                elif parts == ["configura", "wifi"]:
                    dati = json.loads(body or b"{}")
                    if not isinstance(dati, dict):
                        raise ValueError("corpo non valido")
                    self._send(200, node.set_wifi(dati))
                elif parts == ["configura", "audio"]:
                    dati = json.loads(body or b"{}")
                    if not isinstance(dati, dict):
                        raise ValueError("corpo non valido")
                    self._send(200, node.set_audio(dati))
                elif parts == ["configura", "manopole"]:
                    dati = json.loads(body or b"{}")
                    if not isinstance(dati, dict):
                        raise ValueError("corpo non valido")
                    self._send(200, node.set_manopola(dati))
                elif parts == ["configura", "abbinamenti"]:
                    dati = json.loads(body or b"{}")
                    if not isinstance(dati, dict):
                        raise ValueError("corpo non valido")
                    self._send(200, node.set_pairings(dati))
                elif parts == ["configura", "disco"]:
                    dati = json.loads(body or b"{}")
                    if not isinstance(dati, dict):
                        raise ValueError("corpo non valido")
                    self._send(200, node.set_disk(dati))
                elif parts == ["configura", "camera"]:
                    dati = json.loads(body or b"{}")
                    if not isinstance(dati, dict):
                        raise ValueError("corpo non valido")
                    self._send(200, node.set_camera(dati))
                elif parts == ["configura", "nome"]:
                    dati = json.loads(body or b"{}")
                    if not isinstance(dati, dict):
                        raise ValueError("corpo non valido")
                    self._send(200, node.set_name(str(dati.get("nome", ""))))
                elif parts == ["volume"]:
                    dati = json.loads(body or b"{}")
                    if not isinstance(dati, dict):
                        raise ValueError("corpo non valido")
                    self._send(200, node.set_volume(dati))
                elif parts == ["prank"]:
                    dati = json.loads(body or b"{}")
                    if not isinstance(dati, dict):
                        raise ValueError("corpo non valido")
                    self._send(200, node.set_prank(dati))
                elif parts == ["azioni", "ferma-tutto"]:
                    self._send(200, node.stop_all())
                elif len(parts) == 3 and parts[0] == "azioni" and parts[2] in ("attiva", "disattiva"):
                    payload = json.loads(body or b"{}")
                    if parts[2] == "attiva":
                        self._send(200, node.activate(parts[1], payload))
                    else:
                        self._send(200, node.deactivate(parts[1]))
                else:
                    self._send(404, {"errore": "percorso sconosciuto"})
            except KeyError:
                self._send(404, {"errore": "azione sconosciuta"})
            except ValueError:
                self._send(400, {"errore": "JSON non valido"})
            except ActionError as exc:
                log.warning("Azione rifiutata: %s", exc)
                self._send(exc.code, {"errore": str(exc), **exc.dati})

        def _stream_mic(self) -> None:
            if node.mic is None:
                self._send(501, {"errore": "microfono non configurato sul nodo ([microfono] in node.toml)"})
                return
            from .mic import MicError  # noqa: PLC0415

            stop = threading.Event()
            try:
                gen = node.mic.stream(stop)
                first = next(gen)
            except (MicError, StopIteration) as exc:
                self._send(503, {"errore": f"microfono non disponibile: {exc}"})
                return
            self.connection.settimeout(10)  # un ascoltatore sparito non tiene occupato il microfono per sempre
            self.send_response(200)
            self.send_header("Content-Type", "audio/L16; rate=16000; channels=1")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            log.info("Microfono in ascolto da %s", self.client_address[0])
            try:
                self.wfile.write(first)
                for data in gen:
                    self.wfile.write(data)
            except (OSError, MicError) as exc:
                log.info("Ascolto del microfono finito (%s): %s", self.client_address[0], exc)
            finally:
                stop.set()
                gen.close()

        def _recordings(self) -> None:
            """Elenco degli spezzoni registrati sul nodo, oppure uno spezzone (API 1.4)."""
            from urllib.parse import parse_qs, urlsplit  # noqa: PLC0415

            rec = node.recorder
            if rec is None:
                self._send(501, {"errore": "registrazione non configurata sul nodo ([registrazione] in node.toml)"})
                return
            u = urlsplit(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if u.path == "/registrazioni":
                try:
                    t0 = float(q["da"]) if "da" in q else None
                    t1 = float(q["a"]) if "a" in q else None
                except ValueError:
                    self._send(400, {"errore": "da/a devono essere numeri (secondi epoch)"})
                    return
                self._send(200, {"spezzoni": rec.segments(t0, t1), "stato": rec.status()})
                return
            path = rec.file(q.get("nome", ""))
            if path is None:
                self._send(404, {"errore": "spezzone sconosciuto"})
                return
            try:
                size = path.stat().st_size  # quello in scrittura cresce: si manda quello che c'è adesso
                f = open(path, "rb")  # noqa: SIM115
            except OSError as exc:
                self._send(404, {"errore": f"spezzone non leggibile: {exc}"})
                return
            self.connection.settimeout(30)
            try:
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(size))
                self.end_headers()
                left = size
                while left > 0:
                    chunk = f.read(min(256 * 1024, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except OSError as exc:
                log.info("Download di %s interrotto: %s", path.name, exc)
            finally:
                f.close()

        def _record(self, body: bytes) -> None:
            if node.mic is None:
                self._send(501, {"errore": "microfono non configurato sul nodo ([microfono] in node.toml)"})
                return
            import base64  # noqa: PLC0415

            from .mic import MicError  # noqa: PLC0415

            try:
                secs = max(1.0, min(float(json.loads(body or b"{}").get("durata", 30)), 120.0))
            except (TypeError, ValueError, AttributeError):
                raise ActionError("durata non valida", 400) from None
            try:
                wav = node.mic.record(secs)
            except MicError as exc:
                raise ActionError(f"microfono non disponibile: {exc}", 503) from exc
            self._send(200, {"esito": "ok", "mime": "audio/wav", "audio_base64": base64.b64encode(wav).decode()})

        def _play(self, body: bytes) -> None:
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype not in ("audio/wav", "audio/x-wav", "audio/wave"):
                self._send(415, {"errore": f"formato {ctype or '?'} non supportato: serve un file WAV"})
                return
            if node.player is None:
                self._send(503, {"errore": "audio non configurato"})
                return
            try:
                node.player.play_wav(body)
            except AudioError as exc:
                raise ActionError(f"audio non disponibile: {exc}", 503) from exc
            self._send(200, {"esito": "ok", "byte": len(body)})

    return Handler


class _Server(ThreadingHTTPServer):
    """La stretta di mano TLS si fa nel thread della richiesta, non nel ciclo che accetta.

    Se il socket in ascolto è già avvolto in TLS, la stretta di mano avviene dentro ``accept()``,
    cioè nel ciclo che accetta le connessioni: **uno solo** che apre la porta e non dice niente lo
    tiene fermo, e il nodo smette di rispondere a chiunque — allarme compreso. Trovato provando
    S-04: con venti connessioni mute aperte, una richiesta firmata non passava più.

    Qui ``get_request`` restituisce il socket ancora in chiaro, con un timeout addosso, e il TLS lo
    avvolge ``finish_request``, che gira nel thread della richiesta: chi tace occupa un thread per
    quindici secondi e poi cade, mentre tutti gli altri passano.
    """

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, ctx: ssl.SSLContext):
        self.ctx = ctx
        super().__init__(addr, handler)

    def get_request(self):
        conn, addr = self.socket.accept()
        conn.settimeout(TIMEOUT_CONNESSIONE)
        return conn, addr

    def finish_request(self, request, client_address):
        try:
            tls = self.ctx.wrap_socket(request, server_side=True)
        except (OSError, ValueError) as exc:   # stretta di mano fallita, scaduta o interrotta
            log.debug("Stretta di mano TLS non riuscita da %s: %s", client_address[0], exc)
            return
        try:
            self.RequestHandlerClass(tls, client_address, self)
        finally:
            try:
                tls.close()
            except OSError:
                pass

    def handle_error(self, request, client_address):
        log.debug("Connessione chiusa da %s", client_address)


class NodeServer:
    def __init__(self, node: Node, cert: Path, key: Path, host: str = "0.0.0.0", port: int = 8443):
        self.node = node
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(str(cert), str(key))
        self.httpd = _Server((host, port), make_handler(node), ctx)
        self.port = self.httpd.server_address[1]
        self._thread: threading.Thread | None = None

    def start(self) -> "NodeServer":
        self._thread = threading.Thread(target=self.httpd.serve_forever, name="node-http", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        try:
            self.node.stop_all()
        finally:
            self.httpd.shutdown()
            self.httpd.server_close()
