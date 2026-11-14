# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""MQTT: il formato dei pacchetti, il client di casa e il broker finto del simulatore.

**Come sono fatte queste prove, e perché così.** Client e broker sono scritti dalla stessa mano. Se si
controllassero soltanto a vicenda, un byte sbagliato nel formato si annullerebbe — il client scrive male,
il broker legge male allo stesso modo, e la prova passa. Quindi:

1. il formato è verificato contro **byte scritti a mano dalla specifica MQTT 3.1.1**, non contro il
   nostro stesso codice;
2. il broker è interrogato anche da un **client grezzo** che in questo file si scrive i pacchetti da sé
   e non importa niente da ``mirto.actions.mqtt``.

Un broker vero (Mosquitto) non si può installare qui, e la terza prova — che un dispositivo vero ci
parli — resta in *Prove manuali*.
"""
import socket
import struct
import threading
import time
import unittest

from mirto.actions import mqtt
from mirto.actions.broker import Broker


# --- un client grezzo, scritto a mano, che non usa il codice di Mirto --------

def _esatti(sock, quanti: int, secondi: float = 2.0) -> bytes:
    sock.settimeout(secondi)
    dati = b""
    while len(dati) < quanti:
        pezzo = sock.recv(quanti - len(dati))
        if not pezzo:
            raise AssertionError("il broker ha chiuso prima del tempo")
        dati += pezzo
    return dati


def _leggi_uno(sock, secondi: float = 2.0):
    """Un pacchetto, letto a mano: ``(tipo, bandiere, corpo)``."""
    testa = _esatti(sock, 1, secondi)[0]
    resto = moltiplicatore = 0
    while True:
        b = _esatti(sock, 1, secondi)[0]
        resto += (b & 0x7F) << moltiplicatore
        if not b & 0x80:
            break
        moltiplicatore += 7
    return testa >> 4, testa & 0x0F, (_esatti(sock, resto, secondi) if resto else b"")


def _apri_la_pubblicazione(corpo: bytes) -> tuple[str, bytes]:
    """Argomento e carico di un PUBLISH di qualità 0, aperti a mano."""
    n = (corpo[0] << 8) | corpo[1]
    return corpo[2:2 + n].decode("utf-8"), corpo[2 + n:]


def _grezzo(porta: int, nome: str = b"grezzo", testamento=None) -> socket.socket:
    """Si collega e manda un CONNECT composto a mano. Restituisce il socket dopo il CONNACK."""
    bandiere, coda = 0x02, struct.pack("!H", len(nome)) + nome
    if testamento:
        arg, carico = testamento
        bandiere |= 0x04
        coda += struct.pack("!H", len(arg)) + arg + struct.pack("!H", len(carico)) + carico
    corpo = b"\x00\x04MQTT\x04" + bytes([bandiere]) + b"\x00\x3c" + coda
    sock = socket.create_connection(("127.0.0.1", porta), timeout=2.0)
    sock.sendall(b"\x10" + bytes([len(corpo)]) + corpo)
    tipo, _, _ = _leggi_uno(sock)
    assert tipo == 2, f"atteso CONNACK, arrivato {tipo}"
    return sock


def _iscrivi_grezzo(sock, filtro: bytes, identificativo: int = 1) -> None:
    corpo = struct.pack("!H", identificativo) + struct.pack("!H", len(filtro)) + filtro + b"\x00"
    sock.sendall(b"\x82" + bytes([len(corpo)]) + corpo)
    tipo, _, _ = _leggi_uno(sock)
    assert tipo == 9, f"atteso SUBACK, arrivato {tipo}"


def _pubblica_grezzo(sock, argomento: bytes, carico: bytes, ritenuto: bool = False) -> None:
    corpo = struct.pack("!H", len(argomento)) + argomento + carico
    sock.sendall(bytes([0x30 | (0x01 if ritenuto else 0)]) + mqtt.scrivi_lunghezza(len(corpo)) + corpo)


class IlFormatoDeiPacchettiTest(unittest.TestCase):
    """I byte, contro la specifica. Ogni atteso qui è scritto a mano, non prodotto dal nostro codice."""

    def test_la_lunghezza_variabile(self):
        # i quattro scalini della «variable byte integer», MQTT 3.1.1 §2.2.3
        self.assertEqual(mqtt.scrivi_lunghezza(0), b"\x00")
        self.assertEqual(mqtt.scrivi_lunghezza(127), b"\x7f")
        self.assertEqual(mqtt.scrivi_lunghezza(128), b"\x80\x01")
        self.assertEqual(mqtt.scrivi_lunghezza(16383), b"\xff\x7f")
        self.assertEqual(mqtt.scrivi_lunghezza(16384), b"\x80\x80\x01")
        self.assertEqual(mqtt.scrivi_lunghezza(268435455), b"\xff\xff\xff\x7f")

    def test_la_lunghezza_si_rilegge(self):
        for n in (0, 1, 127, 128, 300, 16383, 16384, 2097151, 268435455):
            self.assertEqual(mqtt.leggi_lunghezza(mqtt.scrivi_lunghezza(n), 0)[0], n)

    def test_una_lunghezza_incompleta_non_e_un_errore(self):
        # chi legge dalla rete deve poter distinguere «non è ancora arrivato» da «è rotto»
        with self.assertRaises(ValueError):
            mqtt.leggi_lunghezza(b"\x80", 0)
        self.assertIsNone(mqtt.un_pacchetto(b"\x30\x05\x00\x03a"))

    def test_il_connect(self):
        atteso = (b"\x10\x0d"                    # CONNECT, 13 byte di resto
                  b"\x00\x04MQTT"                # nome del protocollo
                  b"\x04"                        # livello 4 = MQTT 3.1.1
                  b"\x02"                        # bandiere: sessione pulita
                  b"\x00\x3c"                    # keepalive: 60 s
                  b"\x00\x01x")                  # nome del client
        self.assertEqual(mqtt.connetti_pacchetto("x"), atteso)

    def test_il_connect_col_testamento(self):
        atteso = (b"\x10\x18\x00\x04MQTT\x04"
                  b"\x26"                        # pulita + testamento + testamento ritenuto
                  b"\x00\x3c\x00\x01w"
                  b"\x00\x03bye\x00\x04gone")
        self.assertEqual(mqtt.connetti_pacchetto("w", testamento=("bye", b"gone", True)), atteso)

    def test_il_publish(self):
        corpo = mqtt.scrivi_stringa("a/b") + b"ciao"
        self.assertEqual(mqtt.pacchetto(mqtt.PUBLISH, corpo), b"\x30\x09\x00\x03a/bciao")
        # ritenuto (bit 0) e qualità 1 (bit 1) nello stesso primo byte: 0x30 | 0x01 | 0x02
        ritenuto = mqtt.pacchetto(mqtt.PUBLISH, mqtt.scrivi_stringa("t") + b"\x00\x01", bandiere=0x03)
        self.assertEqual(ritenuto, b"\x33\x05\x00\x01t\x00\x01")

    def test_il_subscribe(self):
        corpo = struct.pack("!H", 1) + mqtt.scrivi_stringa("a/#") + b"\x00"
        # il SUBSCRIBE ha le bandiere a 0x02 per obbligo di specifica, non per scelta
        self.assertEqual(mqtt.pacchetto(mqtt.SUBSCRIBE, corpo, bandiere=0x02),
                         b"\x82\x08\x00\x01\x00\x03a/#\x00")

    def test_il_pingreq(self):
        self.assertEqual(mqtt.pacchetto(mqtt.PINGREQ), b"\xc0\x00")

    def test_le_stringhe_sono_utf8_con_due_byte_di_lunghezza(self):
        self.assertEqual(mqtt.scrivi_stringa("così"), b"\x00\x05cos\xc3\xac")
        self.assertEqual(mqtt.leggi_stringa(b"\x00\x05cos\xc3\xac", 0), ("così", 7))


class ICombaciaTest(unittest.TestCase):
    """Le regole dei filtri, comprese le due che si sbagliano sempre."""

    def test_senza_jolly(self):
        self.assertTrue(mqtt.combacia("casa/luce", "casa/luce"))
        self.assertFalse(mqtt.combacia("casa/luce", "casa/presa"))
        self.assertFalse(mqtt.combacia("casa/luce", "casa/luce/stato"))
        self.assertFalse(mqtt.combacia("casa/luce/stato", "casa/luce"))

    def test_il_piu_prende_un_livello_solo(self):
        self.assertTrue(mqtt.combacia("casa/+", "casa/luce"))
        self.assertTrue(mqtt.combacia("casa/+/stato", "casa/luce/stato"))
        self.assertFalse(mqtt.combacia("casa/+", "casa/luce/stato"))
        self.assertFalse(mqtt.combacia("casa/+", "casa"))

    def test_il_cancelletto_prende_anche_il_livello_su_cui_sta(self):
        # la regola che si dimentica: «casa/#» prende anche «casa», non solo quello che c'è sotto
        self.assertTrue(mqtt.combacia("casa/#", "casa"))
        self.assertTrue(mqtt.combacia("casa/#", "casa/luce"))
        self.assertTrue(mqtt.combacia("casa/#", "casa/luce/stato"))
        self.assertTrue(mqtt.combacia("#", "casa/luce"))
        self.assertFalse(mqtt.combacia("casa/#", "cucina/luce"))

    def test_i_jolly_non_prendono_gli_argomenti_del_broker(self):
        # l'altra regola che si dimentica: «$SYS/...» è roba del broker, i jolly la lasciano stare
        self.assertFalse(mqtt.combacia("#", "$SYS/broker/uptime"))
        self.assertFalse(mqtt.combacia("+/broker", "$SYS/broker"))
        self.assertTrue(mqtt.combacia("$SYS/#", "$SYS/broker/uptime"))


class IlBrokerFintoTest(unittest.TestCase):
    """Il broker, interrogato dal client grezzo di questo file."""

    def setUp(self):
        self.broker = Broker(porta=0).avvia()
        self.addCleanup(self.broker.ferma)
        self.porta = self.broker.porta

    def test_un_messaggio_arriva_a_chi_si_e_iscritto(self):
        ascolta = _grezzo(self.porta, b"ascolta")
        self.addCleanup(ascolta.close)
        _iscrivi_grezzo(ascolta, b"casa/+/stato")
        parla = _grezzo(self.porta, b"parla")
        self.addCleanup(parla.close)
        _pubblica_grezzo(parla, b"casa/luce/stato", b"accesa")
        tipo, bandiere, corpo = _leggi_uno(ascolta)
        self.assertEqual(tipo, mqtt.PUBLISH)
        self.assertEqual(_apri_la_pubblicazione(corpo), ("casa/luce/stato", b"accesa"))
        self.assertEqual(bandiere & 0x01, 0, "a chi era già iscritto il ritenuto non si segnala")

    def test_a_chi_non_si_e_iscritto_non_arriva_niente(self):
        zitto = _grezzo(self.porta, b"zitto")
        self.addCleanup(zitto.close)
        _iscrivi_grezzo(zitto, b"cucina/#")
        parla = _grezzo(self.porta, b"parla")
        self.addCleanup(parla.close)
        _pubblica_grezzo(parla, b"garage/luce", b"accesa")
        with self.assertRaises(socket.timeout):
            _leggi_uno(zitto, secondi=0.4)

    def test_il_ritenuto_arriva_a_chi_si_iscrive_dopo(self):
        parla = _grezzo(self.porta, b"parla")
        self.addCleanup(parla.close)
        _pubblica_grezzo(parla, b"casa/luce/stato", b"accesa", ritenuto=True)
        time.sleep(0.1)
        tardi = _grezzo(self.porta, b"tardi")
        self.addCleanup(tardi.close)
        _iscrivi_grezzo(tardi, b"casa/#")
        tipo, bandiere, corpo = _leggi_uno(tardi)
        self.assertEqual(tipo, mqtt.PUBLISH)
        self.assertEqual(_apri_la_pubblicazione(corpo), ("casa/luce/stato", b"accesa"))
        self.assertEqual(bandiere & 0x01, 0x01, "un ritenuto va consegnato segnalato come tale")

    def test_un_ritenuto_vuoto_cancella_quello_di_prima(self):
        parla = _grezzo(self.porta, b"parla")
        self.addCleanup(parla.close)
        _pubblica_grezzo(parla, b"casa/luce/stato", b"accesa", ritenuto=True)
        _pubblica_grezzo(parla, b"casa/luce/stato", b"", ritenuto=True)
        time.sleep(0.1)
        tardi = _grezzo(self.porta, b"tardi")
        self.addCleanup(tardi.close)
        _iscrivi_grezzo(tardi, b"casa/#")
        with self.assertRaises(socket.timeout):
            _leggi_uno(tardi, secondi=0.4)

    def test_il_ritenuto_arriva_dopo_il_suback(self):
        # chi si iscrive deve prima sapere di esserlo: al contrario un client vero butta via il primo
        parla = _grezzo(self.porta, b"parla")
        self.addCleanup(parla.close)
        _pubblica_grezzo(parla, b"casa/luce", b"accesa", ritenuto=True)
        time.sleep(0.1)
        tardi = socket.create_connection(("127.0.0.1", self.porta), timeout=2.0)
        self.addCleanup(tardi.close)
        corpo = b"\x00\x04MQTT\x04\x02\x00\x3c\x00\x05tardi"
        tardi.sendall(b"\x10" + bytes([len(corpo)]) + corpo)
        self.assertEqual(_leggi_uno(tardi)[0], mqtt.CONNACK)
        sub = struct.pack("!H", 7) + struct.pack("!H", 6) + b"casa/#" + b"\x00"
        tardi.sendall(b"\x82" + bytes([len(sub)]) + sub)
        self.assertEqual(_leggi_uno(tardi)[0], mqtt.SUBACK, "prima il SUBACK")
        self.assertEqual(_leggi_uno(tardi)[0], mqtt.PUBLISH, "poi il ritenuto")

    def test_iscriversi_mentre_arriva_un_ritenuto_non_lo_consegna_due_volte(self):
        """La corsa trovata il 14/11, e il motivo per cui la consegna tiene il lucchetto.

        Se un ritenuto viene pubblicato nell'istante in cui qualcuno si iscrive, quel qualcuno può
        riceverlo due volte: una perché è già iscritto, una dal magazzino. A chi legge arriva un
        dispositivo doppio. La barriera fa combaciare i due istanti, e senza il rimedio la doppia
        consegna salta fuori già al primo giro: i giri in più sono solo margine.
        """
        for giro in range(12):
            argomento = f"corsa/{giro}".encode()
            pronti = threading.Barrier(2)
            parla = _grezzo(self.porta, b"parla")
            ascolta = socket.create_connection(("127.0.0.1", self.porta), timeout=2.0)
            corpo = b"\x00\x04MQTT\x04\x02\x00\x3c\x00\x01a"
            ascolta.sendall(b"\x10" + bytes([len(corpo)]) + corpo)
            self.assertEqual(_leggi_uno(ascolta)[0], mqtt.CONNACK)
            sub = struct.pack("!H", 1) + struct.pack("!H", len(argomento)) + argomento + b"\x00"

            def pubblica():
                pronti.wait()
                _pubblica_grezzo(parla, argomento, b"x", ritenuto=True)

            filo = threading.Thread(target=pubblica)
            filo.start()
            pronti.wait()
            ascolta.sendall(b"\x82" + bytes([len(sub)]) + sub)
            filo.join()
            quanti = 0
            try:
                while True:
                    tipo, _, _ = _leggi_uno(ascolta, secondi=0.15)
                    quanti += tipo == mqtt.PUBLISH
            except (socket.timeout, TimeoutError):
                pass
            parla.close()
            ascolta.close()
            self.assertLessEqual(quanti, 1, f"al giro {giro} il ritenuto è arrivato {quanti} volte")

    def test_una_pubblicazione_di_qualita_1_viene_riscontrata(self):
        parla = _grezzo(self.porta, b"parla")
        self.addCleanup(parla.close)
        corpo = struct.pack("!H", 3) + b"a/b" + struct.pack("!H", 1234) + b"x"
        parla.sendall(b"\x32" + bytes([len(corpo)]) + corpo)
        tipo, _, corpo = _leggi_uno(parla)
        self.assertEqual(tipo, mqtt.PUBACK)
        self.assertEqual(struct.unpack("!H", corpo[:2])[0], 1234,
                         "il riscontro deve riportare l'identificativo di chi ha pubblicato")

    def test_il_ping_ha_risposta(self):
        sock = _grezzo(self.porta)
        self.addCleanup(sock.close)
        sock.sendall(b"\xc0\x00")
        self.assertEqual(_leggi_uno(sock)[0], mqtt.PINGRESP)

    def test_dopo_la_disiscrizione_non_arriva_piu_niente(self):
        ascolta = _grezzo(self.porta, b"ascolta")
        self.addCleanup(ascolta.close)
        _iscrivi_grezzo(ascolta, b"casa/#")
        corpo = struct.pack("!H", 2) + struct.pack("!H", 6) + b"casa/#"
        ascolta.sendall(b"\xa2" + bytes([len(corpo)]) + corpo)
        self.assertEqual(_leggi_uno(ascolta)[0], mqtt.UNSUBACK)
        parla = _grezzo(self.porta, b"parla")
        self.addCleanup(parla.close)
        _pubblica_grezzo(parla, b"casa/luce", b"accesa")
        with self.assertRaises(socket.timeout):
            _leggi_uno(ascolta, secondi=0.4)

    def test_un_carico_grosso_arriva_intero(self):
        # oltre 127 byte la lunghezza prende due byte: è lì che il formato si sbaglia
        grosso = bytes(range(256)) * 3
        ascolta = _grezzo(self.porta, b"ascolta")
        self.addCleanup(ascolta.close)
        _iscrivi_grezzo(ascolta, b"grosso")
        parla = _grezzo(self.porta, b"parla")
        self.addCleanup(parla.close)
        _pubblica_grezzo(parla, b"grosso", grosso)
        _, _, corpo = _leggi_uno(ascolta)
        self.assertEqual(_apri_la_pubblicazione(corpo), ("grosso", grosso))

    def test_un_pacchetto_spezzato_in_due_viene_ricomposto(self):
        ascolta = _grezzo(self.porta, b"ascolta")
        self.addCleanup(ascolta.close)
        _iscrivi_grezzo(ascolta, b"pezzi")
        parla = _grezzo(self.porta, b"parla")
        self.addCleanup(parla.close)
        intero = b"\x30\x0b\x00\x05pezziciao"      # 0x0b = 2 + 5 («pezzi») + 4 («ciao»)
        parla.sendall(intero[:4])
        time.sleep(0.05)
        parla.sendall(intero[4:])
        _, _, corpo = _leggi_uno(ascolta)
        self.assertEqual(_apri_la_pubblicazione(corpo), ("pezzi", b"ciao"))

    def test_due_pacchetti_in_un_colpo_vengono_trattati_entrambi(self):
        ascolta = _grezzo(self.porta, b"ascolta")
        self.addCleanup(ascolta.close)
        _iscrivi_grezzo(ascolta, b"doppio/#")
        parla = _grezzo(self.porta, b"parla")
        self.addCleanup(parla.close)
        parla.sendall(b"\x30\x0a\x00\x08doppio/a" + b"\x30\x0a\x00\x08doppio/b")
        primi = {_apri_la_pubblicazione(_leggi_uno(ascolta)[2])[0] for _ in range(2)}
        self.assertEqual(primi, {"doppio/a", "doppio/b"})


class IlTestamentoTest(unittest.TestCase):
    """Il testamento: si esegue se spariamo, non se salutiamo."""

    def setUp(self):
        self.broker = Broker(porta=0).avvia()
        self.addCleanup(self.broker.ferma)
        self.porta = self.broker.porta

    def test_si_esegue_se_il_collegamento_cade_di_brutto(self):
        ascolta = _grezzo(self.porta, b"ascolta")
        self.addCleanup(ascolta.close)
        _iscrivi_grezzo(ascolta, b"mirto/#")
        muore = _grezzo(self.porta, b"muore", testamento=(b"mirto/casa/stato", b"offline"))
        muore.close()
        tipo, _, corpo = _leggi_uno(ascolta)
        self.assertEqual(tipo, mqtt.PUBLISH)
        self.assertEqual(_apri_la_pubblicazione(corpo), ("mirto/casa/stato", b"offline"))

    def test_non_si_esegue_dopo_un_addio_detto(self):
        ascolta = _grezzo(self.porta, b"ascolta")
        self.addCleanup(ascolta.close)
        _iscrivi_grezzo(ascolta, b"mirto/#")
        saluta = _grezzo(self.porta, b"saluta", testamento=(b"mirto/casa/stato", b"offline"))
        saluta.sendall(b"\xe0\x00")              # DISCONNECT
        time.sleep(0.1)
        saluta.close()
        with self.assertRaises(socket.timeout):
            _leggi_uno(ascolta, secondi=0.5)


class IlClienteTest(unittest.TestCase):
    """Il client di casa, contro il broker finto. Qui i due pezzi si incontrano davvero."""

    def setUp(self):
        self.broker = Broker(porta=0).avvia()
        self.addCleanup(self.broker.ferma)
        self.porta = self.broker.porta
        self.arrivati: list[tuple[str, bytes, bool]] = []
        self.suonato = threading.Event()

    def _raccogli(self, argomento, carico, ritenuto):
        self.arrivati.append((argomento, carico, ritenuto))
        self.suonato.set()

    def _cliente(self, **extra) -> mqtt.Cliente:
        c = mqtt.Cliente("127.0.0.1", self.porta, su_messaggio=self._raccogli, **extra)
        self.addCleanup(c.chiudi)
        return c.collega()

    def _aspetta(self, quanti: int = 1, secondi: float = 2.0) -> None:
        fine = time.monotonic() + secondi
        while time.monotonic() < fine and len(self.arrivati) < quanti:
            time.sleep(0.01)
        self.assertGreaterEqual(len(self.arrivati), quanti,
                                f"atteso {quanti}, arrivato {self.arrivati}")

    def test_si_collega_e_lo_sa(self):
        c = self._cliente()
        self.assertTrue(c.collegato())
        self.assertTrue(self.broker.aspetta_collegati(1))

    def test_su_un_broker_che_non_c_e_dice_perche(self):
        morto = socket.socket()
        morto.bind(("127.0.0.1", 0))
        porta = morto.getsockname()[1]
        morto.close()
        with self.assertRaises(mqtt.ErroreMqtt) as caduta:
            mqtt.Cliente("127.0.0.1", porta).collega(secondi=1.0)
        self.assertIn("127.0.0.1", str(caduta.exception))

    def test_pubblica_e_riceve(self):
        c = self._cliente()
        c.iscrivi("casa/+/stato")
        time.sleep(0.1)
        c.pubblica("casa/luce/stato", "accesa")
        self._aspetta()
        self.assertEqual(self.arrivati[0], ("casa/luce/stato", b"accesa", False))

    def test_la_qualita_1_aspetta_il_riscontro(self):
        c = self._cliente()
        c.pubblica("casa/luce/stato", b"accesa", qos=1, secondi=2.0)   # solleva se non arriva

    def test_riceve_i_ritenuti_iscrivendosi(self):
        altro = _grezzo(self.porta, b"altro")
        self.addCleanup(altro.close)
        _pubblica_grezzo(altro, b"casa/luce/stato", b"accesa", ritenuto=True)
        time.sleep(0.1)
        c = self._cliente()
        c.iscrivi("casa/#")
        self._aspetta()
        self.assertEqual(self.arrivati[0], ("casa/luce/stato", b"accesa", True))

    def test_l_iscrizione_chiesta_prima_del_collegamento_viene_mandata_dopo(self):
        c = mqtt.Cliente("127.0.0.1", self.porta, su_messaggio=self._raccogli)
        self.addCleanup(c.chiudi)
        c.iscrivi("casa/#")                     # ancora scollegato: si ricorda e la manda al CONNACK
        c.collega()
        time.sleep(0.1)
        c.pubblica("casa/luce", "accesa")
        self._aspetta()

    def test_il_suo_testamento_arriva_se_il_programma_muore(self):
        ascolta = _grezzo(self.porta, b"ascolta")
        self.addCleanup(ascolta.close)
        _iscrivi_grezzo(ascolta, b"mirto/#")
        c = mqtt.Cliente("127.0.0.1", self.porta, nome="mirto",
                         testamento=("mirto/stato", b"offline", True)).collega()
        c._sock.close()                         # noqa: SLF001 — muore, non saluta
        tipo, _, corpo = _leggi_uno(ascolta)
        self.assertEqual(tipo, mqtt.PUBLISH)
        self.assertEqual(_apri_la_pubblicazione(corpo), ("mirto/stato", b"offline"))

    def test_chiudere_non_esegue_il_testamento(self):
        ascolta = _grezzo(self.porta, b"ascolta")
        self.addCleanup(ascolta.close)
        _iscrivi_grezzo(ascolta, b"mirto/#")
        mqtt.Cliente("127.0.0.1", self.porta, nome="mirto",
                     testamento=("mirto/stato", b"offline", True)).collega().chiudi()
        with self.assertRaises(socket.timeout):
            _leggi_uno(ascolta, secondi=0.5)

    def test_se_il_broker_si_riavvia_torna_e_si_riscrive_alle_iscrizioni(self):
        # il difetto che si scopre dopo settimane: il broker di casa si riavvia e Mirto resta zitto
        c = self._cliente(riprova=True)
        c.iscrivi("casa/#")
        time.sleep(0.1)
        self.broker.ferma()
        nuovo = Broker(porta=self.porta).avvia()
        self.addCleanup(nuovo.ferma)
        self.assertTrue(c.aspetta_collegato(8.0), "non si è ricollegato")
        self.assertTrue(nuovo.aspetta_collegati(1))
        time.sleep(0.2)
        nuovo.consegna("casa/luce", b"accesa")
        self._aspetta(secondi=3.0)
        self.assertEqual(self.arrivati[-1][0], "casa/luce")

    def test_senza_riprova_una_caduta_non_lo_fa_girare_a_vuoto(self):
        c = self._cliente()
        self.broker.ferma()
        fine = time.monotonic() + 3.0
        while time.monotonic() < fine and c.collegato():
            time.sleep(0.05)
        self.assertFalse(c.collegato(), "deve accorgersi che il broker non c'è più")


if __name__ == "__main__":
    unittest.main()
