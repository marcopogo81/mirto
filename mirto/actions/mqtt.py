# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""MQTT 3.1.1: il formato dei pacchetti e un client, scritti in casa.

**Perché non una libreria.** Ne esistono di buone (``paho-mqtt``), ma di MQTT ci serve un angolo
piccolo e fermo da dieci anni: collegarsi, iscriversi, pubblicare, i messaggi **ritenuti** e il
**testamento**. Sta in meno di trecento righe, non invecchia, e tiene Mirto installabile senza che
nessuno debba scaricare niente — la stessa ragione per cui il nodo si è scritto in casa il suo server
HTTPS con la firma HMAC invece di portarsi dentro un framework.

**Perché il formato dei pacchetti sta qui e non nel broker finto.** Il client serve in casa, davanti a
un broker vero; il broker finto serve solo a provare. Il pezzo comune — come si scrivono e si leggono i
byte — appartiene a chi lavora, e il simulatore se lo prende da qui. Al contrario avremmo il programma
che dipende dal suo banco di prova.

**Qualità di servizio.** Si pubblica in qualità 0 o 1; la 1 aspetta il riscontro del broker. Niente
qualità 2 e niente sessioni persistenti: per gli eventi di una casa — «la luce è accesa», «qualcuno è
passato» — l'ultimo messaggio ritenuto vale più di una coda di quelli vecchi.
"""

from __future__ import annotations

import logging
import socket
import ssl
import struct
import threading
import time
from collections.abc import Callable

log = logging.getLogger(__name__)

# I tipi di pacchetto (il numero sta nei quattro bit alti del primo byte).
CONNECT, CONNACK, PUBLISH, PUBACK = 1, 2, 3, 4
PUBREC, PUBREL, PUBCOMP = 5, 6, 7
SUBSCRIBE, SUBACK, UNSUBSCRIBE, UNSUBACK = 8, 9, 10, 11
PINGREQ, PINGRESP, DISCONNECT = 12, 13, 14

PROTOCOLLO, LIVELLO = "MQTT", 4          # 4 = MQTT 3.1.1

# Le bandiere del CONNECT, nell'ordine in cui stanno nel byte.
B_UTENTE, B_PASSWORD, B_TESTAMENTO_RITENUTO = 0x80, 0x40, 0x20
B_TESTAMENTO, B_PULITA = 0x04, 0x02

# I motivi per cui un broker può rifiutare il collegamento (secondo byte del CONNACK).
RIFIUTI = {
    1: "il broker non parla questa versione di MQTT",
    2: "il broker non accetta questo nome di client",
    3: "il broker non è disponibile",
    4: "utente o password sbagliati",
    5: "non autorizzato",
}


class ErroreMqtt(Exception):
    """Qualcosa è andato storto col broker. Il messaggio è scritto per essere mostrato a chi legge."""


def riattacca(sock: socket.socket | None) -> None:
    """Chiude un socket **facendolo sapere all'altro capo**, e non solleva mai.

    Il solo ``close()`` non basta e il perché costa una mattinata se non è scritto: se un altro filo
    è fermo in lettura su quel socket, la chiusura non manda il saluto TCP finché quel filo non si
    sveglia — e quel filo si sveglia quando arriva qualcosa, cioè mai. Dall'altra parte uno resta a
    credersi collegato a vita. ``shutdown`` invece il saluto lo manda subito, e sveglia chi aspetta.
    """
    if sock is None:
        return
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass                        # già morto, o mai collegato: va bene così
    try:
        sock.close()
    except OSError:
        pass


# --- il formato dei pacchetti ------------------------------------------------

def leggi_lunghezza(dati: bytes, i: int) -> tuple[int, int]:
    """La «variable byte integer» di MQTT: sette bit per byte, l'ottavo dice «continua».

    Restituisce ``(valore, indice dopo)``. Solleva ``ValueError`` se i byte non bastano ancora: chi
    legge dalla rete lo usa per sapere che deve aspettare, non che si è rotto qualcosa.
    """
    valore = moltiplicatore = 0
    for _ in range(4):
        if i >= len(dati):
            raise ValueError("incompleto")
        b = dati[i]
        i += 1
        valore += (b & 0x7F) << moltiplicatore
        if not b & 0x80:
            return valore, i
        moltiplicatore += 7
    raise ValueError("lunghezza malformata")


def scrivi_lunghezza(n: int) -> bytes:
    fuori = bytearray()
    while True:
        b = n % 128
        n //= 128
        fuori.append(b | (0x80 if n else 0))
        if not n:
            return bytes(fuori)


def leggi_stringa(dati: bytes, i: int) -> tuple[str, int]:
    """Una stringa MQTT: due byte di lunghezza, poi UTF-8."""
    if i + 2 > len(dati):
        raise ValueError("incompleto")
    n = struct.unpack_from("!H", dati, i)[0]
    i += 2
    if i + n > len(dati):
        raise ValueError("incompleto")
    return dati[i:i + n].decode("utf-8", "replace"), i + n


def scrivi_stringa(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack("!H", len(b)) + b


def pacchetto(tipo: int, corpo: bytes = b"", bandiere: int = 0) -> bytes:
    """Un pacchetto intero: tipo e bandiere nel primo byte, poi la lunghezza, poi il corpo."""
    return bytes([(tipo << 4) | bandiere]) + scrivi_lunghezza(len(corpo)) + corpo


def un_pacchetto(buffer: bytes) -> tuple[int, int, bytes, int] | None:
    """Stacca il primo pacchetto intero da ``buffer``.

    Restituisce ``(tipo, bandiere, corpo, byte consumati)``, oppure ``None`` se il pacchetto non è
    ancora arrivato per intero — che sulla rete è la normalità, non un errore.
    """
    if len(buffer) < 2:
        return None
    try:
        resto, inizio = leggi_lunghezza(buffer, 1)
    except ValueError:
        return None
    if len(buffer) < inizio + resto:
        return None
    return buffer[0] >> 4, buffer[0] & 0x0F, buffer[inizio:inizio + resto], inizio + resto


def combacia(filtro: str, argomento: str) -> bool:
    """Se un argomento ricade sotto un filtro di iscrizione. ``+`` è un livello, ``#`` è il resto.

    Le due regole che si sbagliano sempre, e che hanno una prova ciascuna: ``#`` prende anche il
    livello su cui sta (``casa/#`` prende ``casa``), e né ``+`` né ``#`` prendono gli argomenti che
    cominciano con ``$``, che per convenzione sono roba del broker.
    """
    if argomento.startswith("$") and filtro[:1] in ("+", "#"):
        return False
    f, a = filtro.split("/"), argomento.split("/")
    for i, pezzo in enumerate(f):
        if pezzo == "#":
            return i <= len(a)
        if i >= len(a):
            return False
        if pezzo not in ("+", a[i]):
            return False
    return len(f) == len(a)


def connetti_pacchetto(nome: str, *, pulita: bool = True, keepalive: int = 60,
                       utente: str | None = None, password: str | None = None,
                       testamento: tuple[str, bytes, bool] | None = None) -> bytes:
    """Il CONNECT. ``testamento`` è ``(argomento, carico, ritenuto)``: cosa dire se spariamo."""
    bandiere = B_PULITA if pulita else 0
    corpo = scrivi_stringa(PROTOCOLLO) + bytes([LIVELLO])
    coda = scrivi_stringa(nome)
    if testamento:
        arg, carico, ritenuto = testamento
        bandiere |= B_TESTAMENTO | (B_TESTAMENTO_RITENUTO if ritenuto else 0)
        coda += scrivi_stringa(arg) + struct.pack("!H", len(carico)) + carico
    if utente is not None:
        bandiere |= B_UTENTE
        coda += scrivi_stringa(utente)
    if password is not None:
        bandiere |= B_PASSWORD
        b = password.encode("utf-8")
        coda += struct.pack("!H", len(b)) + b
    corpo += bytes([bandiere]) + struct.pack("!H", keepalive) + coda
    return pacchetto(CONNECT, corpo)


# --- il client ---------------------------------------------------------------

class Cliente:
    """Un collegamento a un broker MQTT, con un filo che legge e richiama chi ascolta.

    ``su_messaggio(argomento, carico, ritenuto)`` viene richiamato **dal filo di lettura**: chi lo usa
    da un'interfaccia grafica deve rimbalzare il lavoro sul filo giusto, come si fa già per gli avvisi
    dei nodi.

    Si ricollega da solo se ``riprova`` è vero: un broker di casa si riavvia, e un client che dopo un
    riavvio resta zitto per sempre è un difetto che si scopre dopo settimane. Al ritorno si riscrive
    alle stesse iscrizioni, perché la sessione è pulita e il broker non le ricorda.
    """

    def __init__(self, indirizzo: str, porta: int = 1883, *, nome: str = "mirto",
                 utente: str | None = None, password: str | None = None,
                 testamento: tuple[str, bytes, bool] | None = None,
                 su_messaggio: Callable[[str, bytes, bool], None] | None = None,
                 tls: bool = False, keepalive: int = 60, riprova: bool = False) -> None:
        self.indirizzo, self.porta, self.nome = indirizzo, porta, nome
        self.utente, self.password, self.testamento = utente, password, testamento
        self.su_messaggio = su_messaggio
        self.tls, self.keepalive, self.riprova = tls, keepalive, riprova
        self._sock: socket.socket | None = None
        self._iscrizioni: list[str] = []
        self._identificativo = 0
        self._riscontri: set[int] = set()
        self._scrivi = threading.Lock()
        self._filo: threading.Thread | None = None
        self._addio = threading.Event()
        self._collegato = threading.Event()

    # --- vita -----------------------------------------------------------------
    def collega(self, secondi: float = 5.0) -> "Cliente":
        """Si collega e aspetta il CONNACK. Solleva ``ErroreMqtt`` se il broker dice no."""
        self._apri(secondi)
        self._filo = threading.Thread(target=self._leggi, daemon=True, name="mqtt-client")
        self._filo.start()
        if not self._collegato.wait(secondi):
            self.chiudi()
            raise ErroreMqtt(f"il broker {self.indirizzo}:{self.porta} non ha risposto")
        return self

    def _apri(self, secondi: float) -> None:
        try:
            sock = socket.create_connection((self.indirizzo, self.porta), timeout=secondi)
            if self.tls:
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.indirizzo)
        except OSError as exc:
            raise ErroreMqtt(f"non riesco a collegarmi a {self.indirizzo}:{self.porta} ({exc})") from exc
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(None)
        self._sock = sock
        self._manda(connetti_pacchetto(self.nome, keepalive=self.keepalive, utente=self.utente,
                                       password=self.password, testamento=self.testamento))

    def chiudi(self) -> None:
        """Saluta e chiude. Un addio detto è un addio pulito: il testamento non si esegue."""
        self._addio.set()
        self._collegato.clear()
        if self._sock:
            try:
                self._manda(pacchetto(DISCONNECT))
            except Exception:  # noqa: BLE001
                pass
            riattacca(self._sock)
            self._sock = None

    def collegato(self) -> bool:
        return self._collegato.is_set()

    def aspetta_collegato(self, secondi: float = 5.0) -> bool:
        return self._collegato.wait(secondi)

    def __enter__(self) -> "Cliente":
        return self.collega()

    def __exit__(self, *_: object) -> None:
        self.chiudi()

    # --- uscita ---------------------------------------------------------------
    def _manda(self, dati: bytes) -> None:
        with self._scrivi:
            sock = self._sock
            if sock is None:
                raise ErroreMqtt("non collegato al broker")
            try:
                sock.sendall(dati)
            except OSError as exc:
                raise ErroreMqtt(f"collegamento col broker interrotto ({exc})") from exc

    def _prossimo(self) -> int:
        self._identificativo = self._identificativo % 65535 + 1
        return self._identificativo

    def pubblica(self, argomento: str, carico: bytes | str = b"", *, ritenuto: bool = False,
                 qos: int = 0, secondi: float = 5.0) -> None:
        """Pubblica. Con ``qos=1`` aspetta il riscontro del broker, e se non arriva solleva."""
        if isinstance(carico, str):
            carico = carico.encode("utf-8")
        bandiere = (0x01 if ritenuto else 0) | ((qos & 0x03) << 1)
        corpo = scrivi_stringa(argomento)
        identificativo = 0
        if qos:
            identificativo = self._prossimo()
            corpo += struct.pack("!H", identificativo)
        self._manda(pacchetto(PUBLISH, corpo + carico, bandiere))
        if not qos:
            return
        fine = time.monotonic() + secondi
        while time.monotonic() < fine:
            if identificativo in self._riscontri:
                self._riscontri.discard(identificativo)
                return
            time.sleep(0.005)
        raise ErroreMqtt(f"il broker non ha riscontrato la pubblicazione su {argomento}")

    def iscrivi(self, *filtri: str) -> None:
        """Si iscrive. I filtri si ricordano, per riscriversi se il collegamento cade e torna."""
        nuovi = [f for f in filtri if f not in self._iscrizioni]
        self._iscrizioni.extend(nuovi)
        if nuovi and self._collegato.is_set():
            self._manda_iscrizione(nuovi)

    def _manda_iscrizione(self, filtri: list[str]) -> None:
        corpo = struct.pack("!H", self._prossimo())
        for f in filtri:
            corpo += scrivi_stringa(f) + b"\x00"       # qualità 0
        self._manda(pacchetto(SUBSCRIBE, corpo, bandiere=0x02))

    def disiscrivi(self, *filtri: str) -> None:
        self._iscrizioni = [f for f in self._iscrizioni if f not in filtri]
        corpo = struct.pack("!H", self._prossimo())
        for f in filtri:
            corpo += scrivi_stringa(f)
        self._manda(pacchetto(UNSUBSCRIBE, corpo, bandiere=0x02))

    # --- entrata --------------------------------------------------------------
    def _leggi(self) -> None:
        ultimo_ping = time.monotonic()
        buffer = b""
        while not self._addio.is_set():
            sock = self._sock
            if sock is None:
                if not self._riprendi():
                    return
                buffer = b""
                continue
            try:
                sock.settimeout(1.0)
                pezzo = sock.recv(65536)
            except TimeoutError:
                if time.monotonic() - ultimo_ping > max(1, self.keepalive // 2):
                    ultimo_ping = time.monotonic()
                    try:
                        self._manda(pacchetto(PINGREQ))
                    except ErroreMqtt:
                        self._caduto()
                continue
            except OSError:
                self._caduto()
                continue
            if not pezzo:
                self._caduto()
                continue
            buffer += pezzo
            while True:
                preso = un_pacchetto(buffer)
                if preso is None:
                    break
                tipo, bandiere, corpo, quanti = preso
                buffer = buffer[quanti:]
                try:
                    self._tratta(tipo, bandiere, corpo)
                except Exception:  # noqa: BLE001
                    log.exception("mqtt: pacchetto di tipo %s non trattabile", tipo)

    def _tratta(self, tipo: int, bandiere: int, corpo: bytes) -> None:
        if tipo == CONNACK:
            esito = corpo[1] if len(corpo) > 1 else 0
            if esito:
                log.warning("mqtt: il broker ha rifiutato il collegamento: %s",
                            RIFIUTI.get(esito, esito))
                self._addio.set()
                return
            self._collegato.set()
            if self._iscrizioni:
                self._manda_iscrizione(list(self._iscrizioni))
        elif tipo == PUBLISH:
            argomento, i = leggi_stringa(corpo, 0)
            qos = (bandiere >> 1) & 0x03
            identificativo = 0
            if qos:
                identificativo = struct.unpack_from("!H", corpo, i)[0]
                i += 2
            carico = corpo[i:]
            if qos == 1:
                self._manda(pacchetto(PUBACK, struct.pack("!H", identificativo)))
            if self.su_messaggio:
                self.su_messaggio(argomento, carico, bool(bandiere & 0x01))
        elif tipo == PUBACK:
            self._riscontri.add(struct.unpack_from("!H", corpo, 0)[0])

    def _caduto(self) -> None:
        self._collegato.clear()
        with self._scrivi:
            riattacca(self._sock)
            self._sock = None
        if not self.riprova:
            self._addio.set()

    def _riprendi(self) -> bool:
        """Riprova a collegarsi, aspettando sempre un po' di più. Falso se è ora di smettere."""
        attesa = 0.5
        while not self._addio.is_set():
            if self._addio.wait(attesa):
                return False
            try:
                self._apri(5.0)
                return True
            except ErroreMqtt:
                attesa = min(attesa * 2, 30.0)
        return False
