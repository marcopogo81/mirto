# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Un broker MQTT minimo, per far funzionare la casa finta senza installare niente.

**Perché ce lo scriviamo invece di usarne uno.** Il simulatore serve a provare la domotica a chi un
impianto non ce l'ha — Marco, e chiunque voglia contribuire. Un simulatore che prima ti chiede di
installare Mosquitto e configurarlo non simula niente: sposta solo il problema di un passo. Qui dentro
c'è lo stretto necessario perché un client vero ci si colleghi e due programmi si parlino.

**Cosa fa e cosa non fa.** Parla MQTT 3.1.1 (il dialetto che parlano tutti: ESPHome, Zigbee2MQTT,
Tasmota, Home Assistant) limitatamente a: collegarsi, iscriversi, pubblicare, i messaggi **ritenuti** e
il **testamento**. Accetta le pubblicazioni di qualità 1 e le riscontra, ma le riconsegna in qualità 0:
per una casa finta su `localhost` non cambia niente, e per un broker vero non ci siamo. Non ha sessioni
persistenti, niente qualità 2, nessuna autenticazione.

**Non è un broker da usare in casa, e non deve diventarlo.** Niente password, niente ACL, niente
limiti: chiunque arrivi può leggere e scrivere tutto. Sta in ascolto su ``127.0.0.1`` di proposito. Se
un giorno serve un broker vero, si installa Mosquitto come fanno tutti.

Il formato dei pacchetti sta in ``mqtt.py``, insieme al client: lì è codice che lavora in casa di
qualcuno, qui è un banco di prova che se lo prende in prestito.
"""

from __future__ import annotations

import logging
import socket
import struct
import sys
import threading
import time

from .mqtt import (
    CONNACK,
    CONNECT,
    DISCONNECT,
    PINGREQ,
    PINGRESP,
    PUBACK,
    PUBLISH,
    SUBACK,
    SUBSCRIBE,
    UNSUBACK,
    UNSUBSCRIBE,
    combacia,
    leggi_stringa,
    pacchetto,
    riattacca,
    un_pacchetto,
)

log = logging.getLogger(__name__)


class _Cliente(threading.Thread):
    """Un collegamento. Legge pacchetti finché il socket regge, poi se ne va in silenzio."""

    def __init__(self, broker: "Broker", sock: socket.socket) -> None:
        super().__init__(daemon=True, name="mqtt-cliente")
        self.broker = broker
        self.sock = sock
        self.nome = ""
        self.iscrizioni: list[str] = []
        self.testamento: tuple[str, bytes, bool] | None = None
        self._scrivi = threading.Lock()
        self._vivo = True

    # --- uscita ---------------------------------------------------------------
    def manda(self, dati: bytes) -> None:
        with self._scrivi:
            try:
                self.sock.sendall(dati)
            except OSError:
                self._vivo = False

    def pubblica_a_me(self, argomento: str, carico: bytes, ritenuto: bool = False) -> None:
        corpo = struct.pack("!H", len(argomento.encode("utf-8"))) + argomento.encode("utf-8") + carico
        self.manda(pacchetto(PUBLISH, corpo, bandiere=0x01 if ritenuto else 0))

    # --- entrata --------------------------------------------------------------
    def run(self) -> None:
        buffer = b""
        try:
            while self._vivo:
                try:
                    pezzo = self.sock.recv(65536)
                except OSError:
                    break
                if not pezzo:
                    break
                buffer += pezzo
                while self._vivo:
                    preso = un_pacchetto(buffer)
                    if preso is None:
                        break
                    tipo, bandiere, corpo, quanti = preso
                    buffer = buffer[quanti:]
                    try:
                        self._tratta(tipo, bandiere, corpo)
                    except Exception:  # noqa: BLE001
                        log.exception("broker: pacchetto di tipo %s non trattabile", tipo)
        finally:
            self._addio()

    def _tratta(self, tipo: int, bandiere: int, corpo: bytes) -> None:
        if tipo == CONNECT:
            self._connetti(corpo)
        elif tipo == PUBLISH:
            self._pubblica(bandiere, corpo)
        elif tipo == SUBSCRIBE:
            self._iscrivi(corpo)
        elif tipo == UNSUBSCRIBE:
            self._disiscrivi(corpo)
        elif tipo == PINGREQ:
            self.manda(pacchetto(PINGRESP))
        elif tipo == DISCONNECT:
            # un addio detto è un addio pulito: il testamento non si esegue
            self.testamento = None
            self._vivo = False

    def _connetti(self, corpo: bytes) -> None:
        _protocollo, i = leggi_stringa(corpo, 0)
        i += 1                      # livello del protocollo
        bandiere = corpo[i]
        i += 3                      # bandiere + keepalive
        self.nome, i = leggi_stringa(corpo, i)
        if bandiere & 0x04:         # testamento
            arg, i = leggi_stringa(corpo, i)
            n = struct.unpack_from("!H", corpo, i)[0]
            i += 2
            self.testamento = (arg, corpo[i:i + n], bool(bandiere & 0x20))
            i += n
        self.manda(pacchetto(CONNACK, b"\x00\x00"))

    def _pubblica(self, bandiere: int, corpo: bytes) -> None:
        argomento, i = leggi_stringa(corpo, 0)
        qos = (bandiere >> 1) & 0x03
        if qos:
            identificativo = struct.unpack_from("!H", corpo, i)[0]
            i += 2
            self.manda(pacchetto(PUBACK, struct.pack("!H", identificativo)))
        self.broker.consegna(argomento, corpo[i:], ritenuto=bool(bandiere & 0x01))

    def _iscrivi(self, corpo: bytes) -> None:
        identificativo = struct.unpack_from("!H", corpo, 0)[0]
        i, esiti, filtri = 2, bytearray(), []
        while i < len(corpo):
            filtro, i = leggi_stringa(corpo, i)
            i += 1                  # la qualità chiesta: la concediamo sempre come 0
            filtri.append(filtro)
            esiti.append(0)
        self.broker.iscrivi(
            self, filtri,
            # il SUBACK va **prima** dei ritenuti: chi si iscrive deve prima sapere di esserlo
            lambda: self.manda(pacchetto(SUBACK, struct.pack("!H", identificativo) + bytes(esiti))))

    def _disiscrivi(self, corpo: bytes) -> None:
        identificativo = struct.unpack_from("!H", corpo, 0)[0]
        i = 2
        while i < len(corpo):
            filtro, i = leggi_stringa(corpo, i)
            self.iscrizioni = [f for f in self.iscrizioni if f != filtro]
        self.manda(pacchetto(UNSUBACK, struct.pack("!H", identificativo)))

    def _addio(self) -> None:
        self._vivo = False
        self.broker.togli(self)
        if self.testamento:
            arg, carico, ritenuto = self.testamento
            self.broker.consegna(arg, carico, ritenuto=ritenuto)
        riattacca(self.sock)


class Broker:
    """Il broker. Si apre con ``avvia()``, si chiude con ``ferma()``, e dice su che porta sta.

    Porta 0 vuol dire «scegline una libera»: le prove ne fanno partire uno per prova senza inciampare
    l'una nell'altra, ed è il motivo per cui ``porta`` si legge **dopo** l'avvio.
    """

    def __init__(self, indirizzo: str = "127.0.0.1", porta: int = 1883) -> None:
        self.indirizzo, self.porta = indirizzo, porta
        self._sock: socket.socket | None = None
        self._clienti: list[_Cliente] = []
        self._ritenuti: dict[str, bytes] = {}
        self._lucchetto = threading.RLock()
        self._filo: threading.Thread | None = None
        self._chiuso = False

    # --- vita -----------------------------------------------------------------
    def avvia(self) -> "Broker":
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if sys.platform != "win32":
            # Altrove SO_REUSEADDR vuol dire «riusa una porta lasciata in TIME_WAIT», e serve:
            # senza, due prove di fila sulla stessa porta non si riaprono. Su Windows vuol dire
            # un'altra cosa — «lega questa porta **anche se** qualcuno ce l'ha già» — e quindi due
            # case finte prenderebbero la stessa porta senza che nessuna delle due se ne accorga:
            # la seconda ruberebbe metà dei collegamenti alla prima, in silenzio. Lì il
            # comportamento giusto è quello di serie, che rifiuta.
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((self.indirizzo, self.porta))
            s.listen(32)
        except OSError:
            s.close()           # se la porta è occupata non ci lasciamo dietro un socket aperto
            raise
        self.porta = s.getsockname()[1]
        self._sock = s
        self._filo = threading.Thread(target=self._accogli, daemon=True, name="mqtt-broker")
        self._filo.start()
        log.info("broker finto in ascolto su %s:%s", self.indirizzo, self.porta)
        return self

    def ferma(self) -> None:
        self._chiuso = True
        riattacca(self._sock)
        self._sock = None
        with self._lucchetto:
            clienti = list(self._clienti)
        for c in clienti:
            c.testamento = None     # chiudiamo noi: non è morto nessuno, nessun testamento
            riattacca(c.sock)

    def __enter__(self) -> "Broker":
        return self.avvia()

    def __exit__(self, *_: object) -> None:
        self.ferma()

    def _accogli(self) -> None:
        while not self._chiuso:
            try:
                sock, _ = self._sock.accept()  # type: ignore[union-attr]
            except OSError:
                return
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            cliente = _Cliente(self, sock)
            with self._lucchetto:
                self._clienti.append(cliente)
            cliente.start()

    # --- messaggi -------------------------------------------------------------
    # Il lucchetto si tiene per tutta la consegna, e anche per tutta l'iscrizione. Costa (due
    # consegne non si sovrappongono) e di solito tenere un lucchetto mentre si scrive su un socket è
    # una cattiva idea, ma qui paga un difetto trovato il 14/11 che altrimenti non si chiude: se un
    # messaggio ritenuto viene pubblicato nell'istante esatto in cui qualcuno si iscrive, quel
    # qualcuno lo riceve **due volte** — una perché è già iscritto, una dal magazzino dei ritenuti.
    # A un client vero arriva un dispositivo doppio, e la prova che lo scopre fallisce una volta su
    # quindici: il genere di difetto che in CI si liquida come «sarà stata la rete». I messaggi qui
    # sono piccoli e il broker sta su 127.0.0.1: il prezzo è nullo, la certezza no.
    def consegna(self, argomento: str, carico: bytes, ritenuto: bool = False) -> None:
        """Manda un messaggio a chi si è iscritto. Un ritenuto **vuoto** cancella quello di prima."""
        with self._lucchetto:
            if ritenuto:
                if carico:
                    self._ritenuti[argomento] = carico
                else:
                    self._ritenuti.pop(argomento, None)
            for c in list(self._clienti):
                if any(combacia(f, argomento) for f in c.iscrizioni):
                    c.pubblica_a_me(argomento, carico)

    def iscrivi(self, cliente: _Cliente, filtri: list[str], riscontra) -> None:
        """Iscrive e consegna i ritenuti **senza che niente possa infilarsi in mezzo**.

        ``riscontra`` manda il SUBACK, e viene chiamato qui dentro perché l'ordine conta: prima il
        riscontro, poi i ritenuti, e nel frattempo nessuna pubblicazione.
        """
        with self._lucchetto:
            cliente.iscrizioni.extend(filtri)
            riscontra()
            for filtro in filtri:
                for argomento, carico in self._ritenuti.items():
                    if combacia(filtro, argomento):
                        cliente.pubblica_a_me(argomento, carico, ritenuto=True)

    def ritenuti_per(self, filtro: str) -> list[tuple[str, bytes]]:
        with self._lucchetto:
            return [(a, c) for a, c in self._ritenuti.items() if combacia(filtro, a)]

    def togli(self, cliente: _Cliente) -> None:
        with self._lucchetto:
            if cliente in self._clienti:
                self._clienti.remove(cliente)

    def quanti_collegati(self) -> int:
        with self._lucchetto:
            return len(self._clienti)

    def aspetta_collegati(self, quanti: int, secondi: float = 2.0) -> bool:
        """Comodo nelle prove: aspetta che si siano collegati in ``quanti``, o si arrende."""
        fine = time.monotonic() + secondi
        while time.monotonic() < fine:
            if self.quanti_collegati() >= quanti:
                return True
            time.sleep(0.01)
        return False
