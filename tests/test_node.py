"""Il servizio del nodo (cartella node/) parla con Mirto come il simulatore."""
import contextlib
import json
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "node"))

from mirto_node import API_VERSION  # noqa: E402
from mirto_node import audio as node_audio  # noqa: E402
from mirto_node import install_tools as it  # noqa: E402
from mirto_node.server import (ActionError, DETERRENT_BUDGET_SECONDS, DETERRENT_MAX_SECONDS,  # noqa: E402
                                 DeterrentDevice, GeneratedAudioDevice, LedDevice, Node, NodeServer,
                                 PRANK_MAX_MINUTI, SirenDevice, TIMEOUT_CONNESSIONE, fra_quanto)

from mirto.actions.nodes import NodeClient, NodeConfig, NodeError  # noqa: E402
from mirto.actions.signing import generate_key  # noqa: E402

# su Windows i permessi dei file non esistono come su Unix: lì queste verifiche non dicono nulla
POSIX = not sys.platform.startswith("win")


class FakePlayer(node_audio.AudioPlayer):
    def __init__(self, ok=True, output="bluetooth", bt_mac="AA:BB:CC:DD:EE:FF", card=""):
        super().__init__(output=output, bt_mac=bt_mac, card=card)
        self.ok = ok
        self.loops, self.stops, self.wavs = [], 0, []

    def _crea_sorella(self, uscita, scheda, mac):
        """Una sorella finta: se no `per()` ne costruirebbe una vera, che cerca una scheda ALSA.

        Vale anche come prova del disegno: la sorella la fa un metodo, quindi chi prova il nodo
        decide di che pasta sono le sue casse.
        """
        sorella = FakePlayer(ok=self.ok, output=uscita, bt_mac=mac, card=scheda)
        sorella.manopole = self.manopole
        return sorella

    def ready(self, chiave=""):
        if not self.ok:
            self.last_error = "cassa Bluetooth spenta o lontana"
        return self.ok

    def loop(self, pcm, seconds, owner="", uscita=""):
        if not self.ready(uscita):
            raise node_audio.AudioError(self.last_error)
        self.loops.append((len(pcm), seconds, uscita))

    def stop(self, owner=None):
        self.stops += 1

    def play_wav(self, data, timeout=120, uscita=""):
        self.wavs.append(len(data))

    def bt_connected(self):
        return self.ok

    def bt_info(self):
        return "\tName: JBL Flip 6\n\tConnected: yes\n" if self.ok else ""


def node_cert(folder: Path):
    from mirto.actions.simulator import make_self_signed
    return make_self_signed("nodo-test", folder)


class NodeServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.cert, key = node_cert(d / "cert")
        led = d / "led"
        led.mkdir()
        (led / "trigger").write_text("none timer [mmc0]")
        self.led = led
        self.key = generate_key()
        self.player = FakePlayer()
        self.node = Node("Ingresso", self.key, {"sirena": SirenDevice(self.player, 80),
                                                "luce_prova": LedDevice(led)}, self.player,
                         cameras=lambda: [{"nome": "cam", "porta": 8554, "percorso": "/cam", "in_linea": True}],
                         addresses=lambda: ["192.168.1.18"], hostname=lambda: "mirto-ingresso.local",
                         model=lambda: "Raspberry Pi 4 Model B Rev 1.2", credentials=lambda: ("mirto", "segreta"))
        self.srv = NodeServer(self.node, self.cert, key, host="127.0.0.1", port=0).start()
        self.client = NodeClient(NodeConfig("ingresso", "Ingresso", f"https://127.0.0.1:{self.srv.port}"),
                                 self.cert.read_text(), self.key)

    def tearDown(self):
        self.srv.stop()
        self.tmp.cleanup()

    def test_info_and_actions(self):
        info = self.client.info()
        self.assertEqual(info["nome"], "Ingresso")
        self.assertEqual(info["versione_api"], API_VERSION)
        kinds = {a["nome"]: a["tipo"] for a in self.client.actions()}
        self.assertEqual(kinds, {"sirena": "siren", "luce_prova": "light"})

    def test_siren_limits(self):
        res = self.client.activate("sirena", 900, "ev1", "Ingresso")
        self.assertEqual(res["durata"], 180)  # mai più di 3 minuti
        self.assertEqual(self.player.loops[-1][1], 180)
        self.assertIn("sirena", self.client.status()["attive"])
        self.assertTrue(self.client.activate("sirena", 60).get("gia_attiva"))
        self.client.deactivate("sirena")
        self.assertNotIn("sirena", self.client.status()["attive"])
        with self.assertRaises(NodeError) as ctx:  # 3 minuti già usati nella mezz'ora
            self.client.activate("sirena", 30)
        self.assertIn("409", str(ctx.exception))
        self.assertEqual(self.client.status()["sirena_disponibile_s"], 0)

    def test_siren_speaker_missing(self):
        self.player.ok = False
        with self.assertRaises(NodeError) as ctx:
            self.client.activate("sirena", 10)
        self.assertIn("503", str(ctx.exception))
        self.assertNotIn("sirena", self.client.status()["attive"])

    def test_led_and_stop_all(self):
        self.client.activate("luce_prova", 30)
        self.assertEqual((self.led / "trigger").read_text(), "timer")  # lampeggia il kernel: vedi LedSenzaPrivilegiTest
        self.client.activate("sirena", 20)
        res = self.client.stop_all()
        self.assertEqual(set(res["spente"]), {"luce_prova", "sirena"})
        self.assertEqual((self.led / "trigger").read_text(), "mmc0")  # ripristinato
        self.assertEqual(self.client.status()["attive"], {})

    def test_auto_off(self):
        self.client.activate("luce_prova", 1)
        time.sleep(1.6)
        self.assertEqual(self.client.status()["attive"], {})
        self.assertEqual((self.led / "trigger").read_text(), "mmc0")

    def test_status_cameras(self):
        st = self.client.status()
        self.assertEqual(st["camere"][0]["percorso"], "/cam")
        self.assertEqual(st["indirizzi"], ["192.168.1.18"])
        self.assertEqual(st["host"], "mirto-ingresso.local")
        info = self.client.info()
        self.assertEqual(info["versione_api"], API_VERSION)
        self.assertEqual(info["camere"], [{"id": "cam", "nome": "cam", "rtsp_porta": 8554, "percorso": "/cam",
                                           "utente": "mirto", "password": "segreta"}])
        self.assertEqual(info["tipo"], "raspberry")
        self.assertEqual({d["id"] if "id" in d else d["nome"] for d in info["dispositivi"]}, {"sirena", "luce_prova"})

    def test_status_speaker(self):
        audio = self.client.status()["audio"]
        self.assertEqual(audio["nome"], "JBL Flip 6")
        self.assertTrue(audio["collegata"])
        self.assertEqual(self.client.status()["sirena_disponibile_s"], 180)

    def test_bad_key_and_audio(self):
        bad = NodeClient(NodeConfig("x", "x", f"https://127.0.0.1:{self.srv.port}"),
                         self.cert.read_text(), generate_key())
        with self.assertRaises(NodeError) as ctx:
            bad.status()
        self.assertIn("401", str(ctx.exception))
        with self.assertRaises(NodeError) as ctx:
            self.client.play_audio(b"OggS....", "audio/ogg")
        self.assertIn("415", str(ctx.exception))
        self.client.play_audio(node_audio.quiet_pcm(0.1), "audio/wav")
        self.assertEqual(len(self.player.wavs), 1)


class _Pipe:
    def __init__(self):
        self.data = bytearray()
        self.closed = False

    def write(self, b):
        self.data += b

    def flush(self):
        pass

    def close(self):
        self.closed = True


class _Proc:
    def __init__(self, args, **kw):
        self.args = args
        self.stdin = _Pipe()
        self.terminated = False

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


class PlayerTest(unittest.TestCase):
    def test_loop_streams_and_stops(self):
        procs = []

        def popen(args, **kw):
            procs.append(_Proc(args))
            return procs[-1]

        class R:
            stdout = "Connected: yes"
        p = node_audio.AudioPlayer("bluetooth", "aa:bb:cc:dd:ee:ff", popen=popen, run=lambda *a, **k: R())
        p.loop(node_audio.quiet_pcm(0.5), 30)
        time.sleep(0.6)
        self.assertTrue(p.playing)
        self.assertIn("bluealsa:DEV=AA:BB:CC:DD:EE:FF,PROFILE=a2dp", procs[0].args)
        p.stop()
        self.assertFalse(p.playing)
        self.assertTrue(procs[0].terminated)
        self.assertGreater(len(procs[0].stdin.data), 0)

    def test_not_configured_still_sounds_from_the_system_output(self):
        """Nessuna cassa configurata non è un buon motivo per restare muti durante un'intrusione.

        Prima alzava AudioError e non suonava niente. Adesso suona dall'uscita di sistema e il
        motivo resta scritto: chi guarda lo stato vede «non configurata», chi è in casa sente.
        """
        procs = []

        def popen(args, **kw):
            procs.append(_Proc(args))
            return procs[-1]

        p = node_audio.AudioPlayer("bluetooth", "", popen=popen, cards=lambda: [])
        p.loop(node_audio.quiet_pcm(0.2), 1)
        time.sleep(0.3)
        self.assertIn("default", procs[0].args, "l'uscita di sistema, non la cassa che non c'è")
        p.stop()
        self.assertIn("non configurata", p.status()["ultimo_errore"])
        self.assertEqual(p.status()["uscita_in_uso"], "predefinito")

    def test_a_player_with_no_output_at_all_is_an_honest_error(self):
        """Se nemmeno il ripiego è disponibile, l'errore arriva a Mirto invece di essere ingoiato."""
        p = node_audio.AudioPlayer("usb", card="Sparita", cards=lambda: [])
        p._ripieghi = lambda: iter(())          # nessuna uscita in tutta la macchina
        with self.assertRaises(node_audio.AudioError):
            p.loop(b"\0\0", 1)
        self.assertIn("non trovata", p.last_error)


class SuonaDaQuestaUscitaTest(unittest.TestCase):
    """API 1.10: «suona da **questa** cassa», e se non risponde non si suona da un'altra.

    Fino alla 1.9 il nodo aveva un'uscita sola, scelta in «Audio del nodo»: tutto quello che suonava
    usciva da lì. Il caso che ha fatto nascere questa tappa e' di Marco, 24/10: *«la camera vede una
    persona al cancello, la cassa emette un suono e in contemporanea si accende la luce»* — e la
    cassa e' una in particolare, non «quella configurata».

    La regola che conta e' il **niente ripieghi**: l'uscita configurata, se non risponde, viene
    sostituita da un'altra — per una sirena «suona da dove puoi» e' quello che si vuole. Ma chiedere
    la cassa del portico e sentirla dalla cucina non e' un ripiego, e' un'altra cosa.
    """

    def test_a_key_says_which_output(self):
        self.assertEqual(node_audio.uscita_da_chiave("usb:UACDemoV10"), ("usb", "UACDemoV10", ""))
        self.assertEqual(node_audio.uscita_da_chiave("bluetooth:90:f2:60:38:e2:3d"),
                         ("bluetooth", "", "90:F2:60:38:E2:3D"))
        for storta in ("", "sciocchezza", "inventata:x"):
            self.assertEqual(node_audio.uscita_da_chiave(storta), ("", "", ""),
                             "una chiave che non si capisce non diventa «l'uscita configurata»")

    def test_the_sound_comes_out_of_the_output_you_asked_for(self):
        procs = []

        def popen(args, **kw):
            procs.append(_Proc(args))
            return procs[-1]

        carte = [{"id": "UACDemoV10", "nome": "Cassa USB", "n": 1, "tipo": "usb"}]
        p = node_audio.AudioPlayer("predefinito", popen=popen, cards=lambda: carte)
        p.loop(node_audio.quiet_pcm(0.2), 1, uscita="usb:UACDemoV10")
        time.sleep(0.3)
        self.assertIn("plughw:CARD=UACDemoV10,DEV=0", procs[0].args,
                      "ha suonato dall'uscita chiesta, non da quella configurata")
        p.stop()

    def test_an_output_you_asked_for_does_not_fall_back(self):
        """Il punto di tutta la tappa: meglio il silenzio che il posto sbagliato."""
        p = node_audio.AudioPlayer("predefinito", cards=lambda: [])
        with self.assertRaises(node_audio.AudioError):
            p.loop(b"\0\0", 1, uscita="usb:Sparita")
        self.assertIn("Sparita", p.last_error)
        self.assertEqual(p.ripiego, "", "nessun ripiego: non e' un guasto da aggirare")

    def test_asking_for_a_speaker_does_not_change_the_configured_one(self):
        """Una richiesta e' per quella volta: il nodo resta configurato come prima."""
        p = node_audio.AudioPlayer("bluetooth", "aa:bb:cc:dd:ee:ff", run=lambda *a, **k: None,
                                   cards=lambda: [])
        p.bt_stato = lambda: {}
        self.assertFalse(p.ready("bluetooth:11:22:33:44:55:66"))
        self.assertEqual(p.bt_mac, "AA:BB:CC:DD:EE:FF",
                         "la cassa del nodo e' ancora quella: gliene abbiamo chiesta un'altra, non cambiata")


class LimitiDelServerTest(unittest.TestCase):
    """S-04: il nodo non si fa tenere occupato da chi non ha la chiave.

    Il server del nodo sta in ascolto sulla rete di casa, e in casa può esserci anche una
    telecamera cinese con un firmware curioso o un ospite col portatile infetto. Prima: il corpo
    della richiesta veniva letto **prima** di controllare la firma, fino a 20 MB, un
    «Content-Length» negativo faceva leggere fino alla chiusura della connessione, e nessun
    timeout chiudeva una connessione lenta. Tre modi per tenere la sirena occupata senza
    conoscere nessuna chiave.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.cert, key = node_cert(d / "cert")
        self.chiave = generate_key()
        self.node = Node("Ingresso", self.chiave, {}, None, cameras=lambda: [], addresses=lambda: [],
                         hostname=lambda: "n.local")
        self.srv = NodeServer(self.node, self.cert, key, host="127.0.0.1", port=0).start()
        self.client = NodeClient(NodeConfig("ingresso", "Ingresso", f"https://127.0.0.1:{self.srv.port}"),
                                 self.cert.read_text(), self.chiave)

    def tearDown(self):
        self.srv.stop()
        self.tmp.cleanup()

    # «Subito» vuol dire: molto prima che la connessione cada da sola. Il confronto che conta è con
    # TIMEOUT_CONNESSIONE (15 s): un nodo che si mette ad aspettare il corpo risponderebbe dopo quello,
    # o non risponderebbe affatto. Una soglia fissa di 2 s invece misurava la fretta del runner: il
    # 21/10 la CI di Windows ha preso 2,125 s su una prova giusta, e il rosso era mio, non del codice.
    SUBITO = TIMEOUT_CONNESSIONE / 3

    def parla(self, richiesta: bytes, attesa: float = 5.0) -> str:
        """Manda byte grezzi al nodo (niente libreria: qui si prova proprio il mal educato)."""
        import socket
        import ssl as _ssl

        ctx = _ssl.create_default_context(cadata=self.cert.read_text())
        ctx.check_hostname = False
        ctx.verify_mode = _ssl.CERT_NONE
        with socket.create_connection(("127.0.0.1", self.srv.port), timeout=attesa) as raw:
            with ctx.wrap_socket(raw) as s:
                s.sendall(richiesta)
                s.settimeout(attesa)
                pezzi = []
                try:   # fino alla chiusura: la risposta può arrivare in più pezzi, e il motivo sta nel corpo
                    while True:
                        dato = s.recv(4096)
                        if not dato:
                            break
                        pezzi.append(dato)
                except (TimeoutError, OSError) as exc:
                    if not pezzi:
                        return f"NESSUNA RISPOSTA: {exc}"
                return b"".join(pezzi).decode("utf-8", "replace")

    def test_a_negative_length_is_refused_at_once(self):
        """«Content-Length: -1» faceva leggere fino a EOF: la connessione restava appesa."""
        inizio = time.monotonic()
        risposta = self.parla(b"POST /abbina HTTP/1.1\r\nHost: x\r\nContent-Length: -1\r\n\r\n",
                              attesa=TIMEOUT_CONNESSIONE)
        self.assertIn("400", risposta.splitlines()[0], risposta[:200])
        self.assertLess(time.monotonic() - inizio, self.SUBITO, "deve rifiutare subito, non aspettare il corpo")

    def test_a_length_that_is_not_a_number_is_refused(self):
        risposta = self.parla(b"POST /abbina HTTP/1.1\r\nHost: x\r\nContent-Length: molti\r\n\r\n")
        self.assertIn("400", risposta.splitlines()[0], risposta[:200])

    def test_pairing_does_not_accept_a_megabyte(self):
        """Per un PIN di sei cifre bastano mille byte: il resto è solo lavoro regalato a un estraneo."""
        corpo = b"x" * (1024 * 1024)
        risposta = self.parla(b"POST /abbina HTTP/1.1\r\nHost: x\r\nContent-Length: "
                              + str(len(corpo)).encode() + b"\r\n\r\n" + corpo[:512])
        self.assertIn("413", risposta.splitlines()[0], risposta[:200])

    def test_a_big_body_without_a_signature_is_never_read(self):
        """Il punto di S-04: senza firma valida il corpo non si legge nemmeno.

        Qui dichiariamo 5 MB e **non li mandiamo**. Se il nodo leggesse il corpo prima di
        controllare gli header, resterebbe in attesa fino al timeout; deve invece rispondere 401
        subito, con il corpo mai letto.
        """
        inizio = time.monotonic()
        risposta = self.parla(b"POST /azioni/sirena/attiva HTTP/1.1\r\nHost: x\r\n"
                              b"Content-Length: 5242880\r\n\r\n", attesa=TIMEOUT_CONNESSIONE)
        self.assertIn("401", risposta.splitlines()[0], risposta[:200])
        self.assertLess(time.monotonic() - inizio, self.SUBITO, "non deve aspettare un corpo che non arriverà")

    def test_an_expired_signature_is_refused_before_the_body_too(self):
        intestazioni = (b"POST /azioni/sirena/attiva HTTP/1.1\r\nHost: x\r\n"
                        b"X-Mirto-Timestamp: 1000000000\r\nX-Mirto-Nonce: abc\r\n"
                        b"X-Mirto-Signature: 00\r\nContent-Length: 1048576\r\n\r\n")
        risposta = self.parla(intestazioni)
        self.assertIn("401", risposta.splitlines()[0], risposta[:200])
        self.assertIn("scaduta", risposta)

    def test_a_signed_request_still_works(self):
        """La prova che i limiti non hanno chiuso la porta a chi ha la chiave."""
        self.assertEqual(self.client.info()["nome"], "Ingresso")

    def test_silent_connections_do_not_block_a_signed_request(self):
        """Venti connessioni aperte e mute non devono fermare chi ha la chiave.

        È il punto di S-04 detto fino in fondo. Attenzione a dove avviene la **stretta di mano
        TLS**: se la fa il ciclo che accetta le connessioni, basta un estraneo che apre la porta e
        non dice niente per fermare *tutti*, compreso l'allarme. Qui si apre solo il TCP, senza
        nemmeno iniziare il TLS: il caso peggiore.
        """
        import socket

        mute = []
        try:
            for _ in range(20):
                s = socket.create_connection(("127.0.0.1", self.srv.port), timeout=5)
                mute.append(s)
            inizio = time.monotonic()
            self.assertEqual(self.client.info()["nome"], "Ingresso",
                             "una richiesta firmata deve passare anche con venti connessioni mute aperte")
            self.assertLess(time.monotonic() - inizio, 5.0, "e deve passare subito, non dopo i timeout")
        finally:
            for s in mute:
                s.close()

    def test_the_socket_has_a_timeout(self):
        """Una connessione aperta e lasciata muta deve cadere da sola, non restare per sempre."""
        from mirto_node.server import make_handler

        handler = make_handler(self.node)
        self.assertIsNotNone(getattr(handler, "timeout", None), "senza timeout una connessione lenta resta")
        self.assertLessEqual(handler.timeout, 30)

    def test_too_many_at_once_get_an_honest_no(self):
        """Oltre il limite di richieste in parallelo si risponde 503, non si rallenta tutto."""
        from mirto_node.server import make_handler

        handler = make_handler(self.node)
        self.assertTrue(hasattr(handler, "semaforo"), "serve un tetto alle richieste in parallelo")
        tenute = []
        try:
            while handler.semaforo.acquire(blocking=False):
                tenute.append(1)
                if len(tenute) > 64:
                    self.fail("il tetto non c'è: il semaforo non si esaurisce mai")
            self.assertGreaterEqual(len(tenute), 4, "un tetto troppo basso taglia fuori l'uso normale")
            self.assertLessEqual(len(tenute), 32, "un tetto troppo alto non protegge da niente")
        finally:
            for _ in tenute:
                handler.semaforo.release()


class FindCamerasTest(unittest.TestCase):
    def test_reads_avahi_files(self):
        from mirto_node.cameras import find_cameras
        with tempfile.TemporaryDirectory() as d:
            Path(d, "mirto-cam.service").write_text(
                "<service-group><service><type>_rtsp._tcp</type><port>8554</port>"
                "<txt-record>path=/cam</txt-record></service></service-group>")
            Path(d, "mirto-node.service").write_text("<service><type>_mirto-node._tcp</type><port>8443</port></service>")
            cams = find_cameras(Path(d), probe=lambda port: port == 8554)
        self.assertEqual(cams, [{"id": "cam", "nome": "cam", "porta": 8554, "percorso": "/cam", "in_linea": True}])


class AvvioSenzaChiaveInstallazioneTest(unittest.TestCase):
    """Ritirare la chiave d'installazione non deve spegnere il nodo al riavvio successivo.

    Trovato sul nodo vero il 18/10: «mirto-nodo abbina --revoca installazione» fa esattamente
    quello che S-01 chiede, cancellando /etc/mirto-node/chiave. Ma il servizio quel file lo
    pretendeva all'avvio. Finché il processo restava in piedi nessuno se ne accorgeva; al primo
    riavvio il nodo moriva e ripartiva ogni cinque secondi, e Mirto lo vedeva come «chiuso».
    """

    NODE = Path(__file__).resolve().parents[1] / "node" / "mirto_node"

    def test_the_service_does_not_demand_the_install_key(self):
        sorgente = (self.NODE / "__main__.py").read_text(encoding="utf-8")
        self.assertNotIn('secret.read_text()', sorgente,
                         "una lettura senza riserve: ritirata la chiave, il servizio non parte più")
        self.assertIn("install_key_hex()", sorgente,
                      "la chiave dell'installazione la sa gestire KeyStore, che sa anche quando non c'è")

    def test_the_key_store_answers_with_nothing_when_it_is_gone(self):
        from mirto_node.keys import KeyStore

        with tempfile.TemporaryDirectory() as d:
            store = KeyStore(Path(d))
            self.assertEqual(store.install_key_hex(), "", "nessun file: nessuna chiave, non un errore")
            store.create("Mirto su mac", "192.0.2.5")
            self.assertEqual(len(store.keys()), 1, "resta quella dell'abbinamento, ed è sufficiente")

    def test_a_node_with_only_its_own_keys_still_works(self):
        from mirto_node.keys import KeyStore
        from mirto_node.server import Node

        with tempfile.TemporaryDirectory() as d:
            store = KeyStore(Path(d))
            chiave = store.create("Mirto su mac", "192.0.2.5")
            nodo = Node("Ingresso", store.install_key_hex(), {}, keys=store)
            self.assertEqual(nodo.all_keys(), [chiave],
                             "senza chiave d'installazione il nodo accetta comunque i suoi abbinamenti")


class ChiaveAManoTest(unittest.TestCase):
    """Una chiave a mano è permanente: chiederla deve costare una risposta consapevole.

    Dalla prova vera del 18/10: rispondendo «s» alla domanda nascevano quattro chiavi in un
    pomeriggio, tutte chiamate «copiata a mano», tutte valide per sempre, nessuna usata.
    """

    def test_the_question_says_it_leaves_a_permanent_key(self):
        from mirto_node import cli

        sorgente = Path(cli.__file__).read_text(encoding="utf-8")
        inizio = sorgente.index("def cmd_abbina")
        corpo = sorgente[inizio:]
        self.assertIn("PERMANENTE", corpo, "la domanda deve dire cosa lascia dietro")
        self.assertIn("--revoca", corpo, "e come toglierla")
        self.assertIn('ask("Per quale Mirto?', corpo,
                      "con un nome, altrimenti nell'elenco sono tutte uguali")

    def test_a_named_key_is_recognisable_in_the_list(self):
        from mirto_node.keys import KeyStore

        with tempfile.TemporaryDirectory() as d:
            store = KeyStore(Path(d))
            store.create("Mirto vecchio del garage", "copiata sul nodo")
            nomi = [p.stem for p in store.files()]
            self.assertEqual(nomi, ["mirto-vecchio-del-garage"],
                             "il nome scelto diventa il nome della chiave nell'elenco")


class ProprietarioDeiFileTest(unittest.TestCase):
    """«mirto-nodo» gira da root, il servizio no: i file di stato devono restare del servizio.

    È il difetto trovato alla prima prova vera sul Raspberry, il 18/10: il PIN scritto dalla riga
    di comando restava di root con permessi 0600, il servizio non riusciva a leggerlo, e Mirto
    si sentiva rispondere «PIN scaduto» su un PIN appena generato.
    """

    def setUp(self):
        from mirto_node import fs

        self.fs = fs
        self.chiamate: list[tuple] = []
        self.vecchio_chown, self.vecchio_root = fs.chown, fs.da_root
        fs.chown = lambda percorso, utente, gruppo: self.chiamate.append((Path(percorso).name, utente))
        fs.da_root = lambda: True          # come quando gira dentro «sudo mirto-nodo»
        self.addCleanup(self.ripristina)

    def ripristina(self):
        self.fs.chown, self.fs.da_root = self.vecchio_chown, self.vecchio_root

    def test_the_pin_written_as_root_is_given_back_to_the_service(self):
        from mirto_node.server import PairingWindow

        with tempfile.TemporaryDirectory() as d:
            conf = Path(d)
            PairingWindow(conf, boot_pin=conf / "mai").open()
            dati = dict(self.chiamate)
            self.assertEqual(dati.get("pin"), "mirto-node",
                             "senza questo il servizio non legge il PIN che ha appena generato la CLI")
            self.assertEqual(dati.get("abbina_fino"), "mirto-node")
            if POSIX:
                self.assertEqual((conf / "pin").stat().st_mode & 0o777, 0o600,
                                 "il PIN resta leggibile solo dal suo proprietario")

    def test_a_key_created_as_root_is_given_back_to_the_service(self):
        from mirto_node.keys import KeyStore

        with tempfile.TemporaryDirectory() as d:
            KeyStore(Path(d)).create("Mirto su mac", "192.0.2.5")
            dati = [nome for nome, _utente in self.chiamate]
            self.assertTrue(any(n.endswith(".key") for n in dati), dati)
            self.assertTrue(any(n.endswith(".json") for n in dati), dati)
            self.assertTrue(all(u == "mirto-node" for _n, u in self.chiamate))


class SegnaliMicroSDTest(unittest.TestCase):
    """S-03: senza privilegi il nodo non scrive sulla microSD, lascia un biglietto a root.

    La partizione di avvio è FAT e appartiene a root. Dare al servizio i permessi su tutta la
    microSD per due file sarebbe il contrario di quello che fa questa azione.
    """

    def finestra(self, conf: Path, microsd: Path):
        from mirto_node.server import PairingWindow

        return PairingWindow(conf, boot_pin=microsd)

    def test_a_pin_it_cannot_write_becomes_a_note_for_root(self):
        with tempfile.TemporaryDirectory() as d:
            conf = Path(d)
            microsd = conf / "boot-non-montata" / "mirto-pin.txt"
            pin = self.finestra(conf, microsd).open(on_boot=True)
            biglietto = conf / "pin-da-mostrare"
            self.assertTrue(biglietto.exists(), "il PIN deve comunque arrivare sulla microSD, tramite root")
            self.assertIn(pin, biglietto.read_text())
            self.assertFalse(microsd.exists())

    @unittest.skipIf(not POSIX or (POSIX and os.geteuid() == 0), "servono permessi che root non sente")
    def test_a_pin_it_cannot_delete_becomes_a_note_for_root(self):
        with tempfile.TemporaryDirectory() as d:
            conf = Path(d) / "conf"
            conf.mkdir()
            boot = Path(d) / "boot"
            boot.mkdir()
            microsd = boot / "mirto-pin.txt"
            microsd.write_text("PIN 123456")
            finestra = self.finestra(conf, microsd)
            with contextlib.ExitStack() as rimetti:
                os.chmod(boot, 0o555)                  # come una FAT di root: dentro non si cancella
                rimetti.callback(os.chmod, boot, 0o755)
                finestra.close()
            self.assertTrue((conf / "pin-da-togliere").exists(),
                            "il PIN resterebbe leggibile sulla microSD a chiunque la monti")
            self.assertTrue(microsd.exists(), "a cancellarlo è il servizio da root, non il nodo")


class RegistrazioniSenzaRootTest(unittest.TestCase):
    """S-03: le registrazioni sono di MediaMTX, ma l'anello lo gira il nodo.

    Il nodo cancella i file più vecchi e prepara la cartella quando si collega un disco. Da root
    funzionava sempre; da utente normale serve che la cartella sia del **gruppo** di MediaMTX,
    con setgid, perché cancellare un file dipende dai permessi della cartella, non del file.
    Se la cartella non si può preparare, il nodo lascia un biglietto al servizio da root.
    """

    def recorder(self, cartella: Path, conf: Path):
        from mirto_node.recording import NodeRecorder

        r = NodeRecorder({"attiva": True, "disco": str(cartella)}, api=object())
        r.segnali = conf
        return r

    def test_the_folder_is_group_writable_with_setgid(self):
        with tempfile.TemporaryDirectory() as d:
            conf = Path(d) / "conf"; conf.mkdir()
            disco = Path(d) / "disco"; disco.mkdir()
            r = self.recorder(disco, conf)
            r.prepare_folder()
            self.assertTrue(r.folder.is_dir())
            if POSIX:
                modo = r.folder.stat().st_mode & 0o7777
                self.assertEqual(modo & 0o2070, 0o2070,
                                 f"serve setgid e scrittura al gruppo (trovato {modo:o}): "
                                 "altrimenti MediaMTX scrive e il nodo non può cancellare")

    def test_a_dead_mount_is_not_a_connected_disk(self):
        """Il difetto peggiore del disco, trovato il 22/10: staccata la chiavetta, restava tutto verde.

        Linux non toglie il punto di mount quando una chiavetta esce: resta li', puntato a un
        /dev/sda che non esiste piu'. ``lsblk`` non lo vedeva piu', ``findmnt`` diceva ancora
        «montato», e il nodo guardava solo quello: nessun errore, riga verde, e intanto la
        registrazione non scriveva piu' niente. Davanti a un errore si fa qualcosa; davanti al verde
        no — per questo e' peggio che fermarsi.
        """
        from unittest import mock

        from mirto_node import recording as R

        with tempfile.TemporaryDirectory() as d:
            disco = Path(d) / "mnt"
            disco.mkdir()
            r = self.recorder(disco, Path(d))
            with mock.patch.object(R.os.path, "ismount", lambda _p: True):
                with mock.patch.object(R, "mount_source", lambda _p: "/dev/sda-che-non-esiste"):
                    self.assertFalse(r.disk_ok(), "il dispositivo non c'e' piu': non e' collegato")
                with mock.patch.object(R, "mount_source", lambda _p: ""):
                    self.assertTrue(r.disk_ok(), "se non si sa che dispositivo sia, si guardano gli altri segni")
                # statvfs non esiste su Windows, dove queste prove girano lo stesso: si prova solo
                # dove c'e', ed e' la stessa ragione per cui il codice la cerca invece di chiamarla.
                if hasattr(R.os, "statvfs"):
                    with mock.patch.object(R, "mount_source", lambda _p: ""), \
                            mock.patch.object(R.os, "statvfs", side_effect=OSError("I/O error")):
                        self.assertFalse(r.disk_ok(), "il dispositivo c'e' ma il filesystem non risponde")

    def test_a_dead_mount_becomes_an_error_and_a_note(self):
        """E quando se ne accorge deve fare le due cose: dirlo, e chiedere di rimetterlo a posto."""
        from unittest import mock

        from mirto_node import recording as R

        with tempfile.TemporaryDirectory() as d:
            conf = Path(d) / "conf"; conf.mkdir()
            disco = Path(d) / "mnt"; disco.mkdir()
            r = self.recorder(disco, conf)
            r.api = self.FintaApi()
            with mock.patch.object(R.os.path, "ismount", lambda _p: True), \
                    mock.patch.object(R, "mount_source", lambda _p: "/dev/sda-che-non-esiste"):
                r.check()
            self.assertIn("non collegato", r.error)
            self.assertTrue((conf / "disco-da-rimontare").exists())

    def test_a_disk_that_came_back_is_asked_to_be_remounted(self):
        """Staccato e riattaccato, il disco deve tornare su da se'. Fino al 22/10 non tornava.

        La riga di /etc/fstab monta all'avvio e basta: a macchina accesa non c'era niente che
        rifacesse il mount. Il nodo diceva «non collegato» e restava li' per sempre, mentre il
        pannello e la diagnosi promettevano che la registrazione sarebbe ripartita da se'. Adesso il
        nodo lascia un biglietto a chi puo' montare, a ogni giro, finche' il disco non c'e'.
        """
        with tempfile.TemporaryDirectory() as d:
            conf = Path(d) / "conf"; conf.mkdir()
            disco = Path(d) / "disco-staccato"      # esiste come cartella, ma non e' un punto di mount
            disco.mkdir()
            r = self.recorder(disco, conf)
            r.api = self.FintaApi()
            r.check()
            self.assertIn("non collegato", r.error)
            biglietto = conf / "disco-da-rimontare"
            self.assertTrue(biglietto.exists(), "senza biglietto nessuno rimonta: resterebbe fermo per sempre")
            self.assertEqual(biglietto.read_text().strip(), str(disco))

    def test_a_node_recording_on_the_microsd_asks_for_nothing(self):
        """Senza disco scelto non c'e' niente da montare: il biglietto non si scrive."""
        with tempfile.TemporaryDirectory() as d:
            conf = Path(d) / "conf"; conf.mkdir()
            from mirto_node.recording import NodeRecorder

            r = NodeRecorder({"attiva": True, "disco": ""}, api=self.FintaApi())
            r.segnali = conf
            r.root = Path(d) / "sd"
            r.folder = r.root / "cam"
            r.check()
            self.assertFalse((conf / "disco-da-rimontare").exists())

    class FintaApi:
        def recording(self):
            return False

        def set_recording(self, on):
            return True

    def test_a_folder_it_cannot_create_becomes_a_note_for_root(self):
        with tempfile.TemporaryDirectory() as d:
            conf = Path(d) / "conf"; conf.mkdir()
            disco = Path(d) / "disco-di-root"
            disco.mkdir()
            r = self.recorder(disco, conf)
            if POSIX and os.geteuid() != 0:
                with contextlib.ExitStack() as rimetti:
                    os.chmod(disco, 0o555)
                    rimetti.callback(os.chmod, disco, 0o755)
                    r.prepare_folder()
                biglietto = conf / "registrazioni-da-preparare"
                self.assertTrue(biglietto.exists(),
                                "senza cartella non si registra: va chiesto a chi può crearla")
                self.assertIn(str(r.folder), biglietto.read_text())


class LedSenzaPrivilegiTest(unittest.TestCase):
    """S-03: il LED lampeggia col trigger del kernel, scrivendo solo un file che esiste sempre.

    Provato sul nodo vero (Pi 4): da root, trigger a «none» e luminosità al massimo, il LED
    verde resta spento — lo stesso LED compare più volte in /sys/class/leds e l'attività della
    microSD sovrascrive quello che scriviamo. Col trigger «timer», invece, lampeggia. Quindi il
    nodo scrive «trigger», che la regola udev rende scrivibile al suo gruppo, e i due file con i
    tempi li prova senza pretenderli: nascono di root insieme al trigger.
    """

    def apparecchia(self, cartella):
        led = Path(cartella) / "led"
        led.mkdir()
        (led / "trigger").write_text("none timer [mmc0]")
        (led / "brightness").write_text("0")
        return led

    def test_it_blinks_with_the_kernel_trigger(self):
        with tempfile.TemporaryDirectory() as d, contextlib.ExitStack() as chiudi:
            led = self.apparecchia(d)
            dev = LedDevice(led)
            dev.start(5)
            chiudi.callback(dev.stop)
            self.assertEqual((led / "trigger").read_text(), "timer",
                             "è il kernel che deve lampeggiare: a mano su questo LED non si vede")
            for tempo in ("delay_on", "delay_off"):
                self.assertEqual((led / tempo).read_text(), str(LedDevice.LAMPEGGIO_MS),
                                 "se i tempi sono scrivibili, il lampeggio è il nostro")

    def test_it_does_not_bother_writing_brightness(self):
        """La luminosità scritta a mano non comanda questo LED: non ci proviamo nemmeno."""
        with tempfile.TemporaryDirectory() as d, contextlib.ExitStack() as chiudi:
            led = self.apparecchia(d)
            dev = LedDevice(led)
            dev.start(5)
            chiudi.callback(dev.stop)
            time.sleep(0.4)   # il tempo che sarebbe bastato a un lampeggio fatto da noi
            self.assertEqual((led / "brightness").read_text(), "0")

    def test_it_blinks_anyway_if_the_times_belong_to_root(self):
        """I due file dei tempi nascono di root: non riuscire a scriverli non è un errore.

        Qui diventano cartelle: scriverci dentro fallisce come fallirebbe per i permessi, con
        qualunque utente stia girando la prova.
        """
        with tempfile.TemporaryDirectory() as d, contextlib.ExitStack() as chiudi:
            led = self.apparecchia(d)
            for tempo in ("delay_on", "delay_off"):
                (led / tempo).mkdir()
            dev = LedDevice(led)
            dev.start(5)              # non deve alzare niente
            chiudi.callback(dev.stop)
            self.assertEqual((led / "trigger").read_text(), "timer",
                             "col solo trigger il kernel lampeggia già, mezzo secondo per volta")

    def test_stop_puts_the_led_back_as_it_was(self):
        with tempfile.TemporaryDirectory() as d:
            led = self.apparecchia(d)
            dev = LedDevice(led)
            dev.start(5)
            dev.stop()
            self.assertEqual((led / "trigger").read_text(), "mmc0", "il trigger di prima va rimesso")

    def test_a_led_that_is_not_there_is_an_honest_error(self):
        dev = LedDevice(None)
        dev.base = None
        with self.assertRaises(ActionError):
            dev.start(5)
        dev.stop()   # spegnere quello che non c'è non deve alzare niente


class NodeCodeTest(unittest.TestCase):
    def test_signing_identical(self):
        def body(p):
            text = (ROOT / p).read_text()
            return re.sub(r'^""".*?"""', "", text.split("\n", 2)[2], count=1, flags=re.S)
        self.assertEqual(body("node/mirto_node/signing.py"), body("mirto/actions/signing.py"),
                         "la firma del nodo deve restare identica a quella di Mirto")

    def test_siren_sound(self):
        pcm = node_audio.siren_pcm(1.0, 80, 8000)
        self.assertEqual(len(pcm), 16000)
        self.assertGreater(max(abs(int.from_bytes(pcm[i:i + 2], "little", signed=True))
                               for i in range(0, len(pcm), 2)), 20000)


if __name__ == "__main__":
    unittest.main()


class NodeRecordingTest(unittest.TestCase):
    """Registrazione sul nodo (API 1.4): anello, disco staccato, MediaMTX acceso/spento, elenco e download."""

    def setUp(self):
        from mirto_node import recording as R

        self.R = R
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    class FakeApi:
        def __init__(self, state=False):
            self.state, self.calls = state, []

        def recording(self):
            return self.state

        def set_recording(self, on):
            self.calls.append(on)
            self.state = on
            return True

    def seg(self, folder: Path, ts: float, size: int = 1000) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        p = folder / time.strftime("%Y%m%d-%H%M%S.mp4", time.gmtime(ts))
        p.write_bytes(b"x" * size)
        import os

        os.utime(p, (ts + 60, ts + 60))
        return p

    def test_ring_deletes_oldest_never_newest(self):
        R = self.R
        api = self.FakeApi()
        rec = R.NodeRecorder({"attiva": True, "disco": "", "massimo_gb": 3500 / 1e9, "minimo_libero_percento": 0},
                             api=api)
        rec.root = self.d / "reg"
        rec.folder = rec.root / "cam"
        t = 1_800_000_000.0
        files = [self.seg(rec.folder, t + 60 * i) for i in range(5)]  # 5000 byte, limite 3500
        rec.check()
        self.assertEqual([f.exists() for f in files], [False, False, True, True, True])
        self.assertEqual(api.calls, [True])  # registrazione accesa
        self.assertTrue(rec.status()["registra"])
        rec.max_bytes = 1  # anche con limite minimo l'ultimo (in scrittura) resta
        rec.check()
        self.assertEqual([f.exists() for f in files], [False, False, False, False, True])

    def test_missing_disk_stops_recording(self):
        R = self.R
        api = self.FakeApi(state=True)
        rec = R.NodeRecorder({"attiva": True, "disco": str(self.d / "ssd-non-montato")}, api=api)
        rec.check()
        self.assertEqual(api.calls, [False])
        self.assertIn("non collegato", rec.status()["errore"])
        self.assertFalse((self.d / "ssd-non-montato").exists())  # niente cartelle sulla microSD al suo posto

    def test_sd_default_and_folder(self):
        R = self.R
        sd = R.NodeRecorder(R.recording_cfg({}), api=self.FakeApi())
        self.assertEqual(sd.max_bytes, int(R.SD_DEFAULT_GB * 1e9))
        self.assertEqual(sd.folder, R.SD_FOLDER / "cam")
        usb = R.NodeRecorder(R.recording_cfg({"registrazione": {"disco": "/mnt/ssd"}}), api=self.FakeApi())
        self.assertEqual(usb.max_bytes, 0)
        self.assertEqual(usb.folder, Path("/mnt/ssd/mirto-reg/cam"))

    def test_file_names_are_validated(self):
        R = self.R
        rec = R.NodeRecorder({"attiva": True}, api=self.FakeApi())
        rec.folder = self.d
        good = self.seg(self.d, 1_800_000_000.0)
        self.assertEqual(rec.file(good.name), good)
        for bad in ("../etc/passwd", "20261017-120000.mp4/../x", "x.mp4", ""):
            self.assertIsNone(rec.file(bad))

    def test_mediamtx_yaml_records(self):
        from mirto_node import install_tools as it

        y = it.mediamtx_yaml(it.CAMERA_DEFAULTS, "u", "p", "", "/mnt/ssd/mirto-reg", ("mirto-api", "segreta"),
                             10800)
        for line in ("api: yes", "apiAddress: 127.0.0.1:9997", "  - user: mirto-api", "    pass: segreta",
                     "record: no", "recordPath: /mnt/ssd/mirto-reg/%path/%Y%m%d-%H%M%S", "recordFormat: fmp4",
                     "recordSegmentDuration: 60s", "recordDeleteAfter: 10800s"):
            self.assertIn(line, y)
        # l'API non è più aperta a «any»: solo con utente e password
        any_block = y.split("  - user: any")[1].split("paths:")[0]
        self.assertNotIn("action: api", any_block)
        plain = it.mediamtx_yaml(it.CAMERA_DEFAULTS, "u", "p")
        self.assertNotIn("recordPath", plain)
        self.assertIn("api: no", plain)

    def test_the_audio_path_exists_only_where_it_was_turned_on(self):
        """Un percorso pubblicabile che nessuno pubblica e' una porta aperta che non serve a niente.

        E chi pubblica ha una password sua, valida **solo** da localhost e **solo** su quel percorso:
        un segreto che apre l'API *e* permette di pubblicare sulla camera e' una cosa sola che fa due
        danni.
        """
        from mirto_node import install_tools as it

        spento = it.mediamtx_for({"registrazione": {"attiva": True}}, "u", "p", "", self.d)
        self.assertNotIn(it.AUDIO_PATH, spento)
        self.assertFalse((self.d / "mtx-pub").exists(), "niente password create per una cosa spenta")

        acceso = it.mediamtx_for({"registrazione": {"attiva": True, "audio": True}}, "u", "p", "", self.d)
        self.assertIn(f"  {it.AUDIO_PATH}:", acceso)
        self.assertIn(f"recordPath", acceso.split(it.AUDIO_PATH + ":")[1],
                      "anche il percorso con l'audio sa registrare, o accendere la registrazione li' "
                      "non scriverebbe niente e nessuno saprebbe perche'")
        pub = acceso.split("  - user: mirto-pub")[1].split("  - user: ")[0]
        self.assertIn("action: publish", pub)
        self.assertIn(f"path: {it.AUDIO_PATH}", pub)
        self.assertIn('ips: ["127.0.0.1", "::1"]', pub)
        self.assertNotIn("action: api", pub, "pubblicare non vuol dire comandare l'API")
        self.assertEqual(it.publish_credentials(self.d / "mtx-pub")[0], "mirto-pub")

    def test_backstop_and_credentials(self):
        from mirto_node import install_tools as it

        # 2 GB a 1,5 Mbit/s ≈ 3 h; ×1,2 di margine
        self.assertEqual(it.delete_after_seconds({"bitrate": 1500}, 2_000_000_000), int(2e9 * 8 / 1.5e6 * 1.2))
        self.assertEqual(it.delete_after_seconds({"bitrate": 1500}, 0), 0)  # disco USB senza massimo
        f = self.d / "mtx-api"
        user, pwd = it.api_credentials(f)
        self.assertEqual((user, len(pwd)), ("mirto-api", 32))
        self.assertEqual(it.api_credentials(f), (user, pwd))  # la seconda volta si rilegge, non si cambia
        if POSIX:
            self.assertEqual(f.stat().st_mode & 0o777, 0o600)
        cfg = {"registrazione": {"attiva": True}}
        y = it.mediamtx_for(cfg, "u", "p", "", self.d)
        self.assertIn("recordPath: /var/lib/mirto-node/registrazioni/%path", y)
        self.assertIn(f"recordDeleteAfter: {it.delete_after_seconds(it.CAMERA_DEFAULTS, 2_000_000_000)}s", y)
        self.assertIn("api: yes", y)

    def test_clean_stop_turns_recording_off(self):
        api = self.FakeApi(state=True)
        rec = self.R.NodeRecorder({"attiva": True}, api=api)
        rec.stop()
        self.assertEqual(api.calls, [False])

    def test_name_regex_is_strict(self):
        R = self.R
        self.assertIsNone(R.parse_start("20261017-120000.mp4\n"))
        self.assertIsNotNone(R.parse_start("20261017-120000.mp4"))

    def test_api_list_and_download(self):
        R = self.R
        cert, key = node_cert(self.d / "cert")
        k = generate_key()
        rec = R.NodeRecorder({"attiva": True}, api=self.FakeApi())
        rec.folder = self.d / "reg" / "cam"
        t = time.time() - 600
        a = self.seg(rec.folder, t, 5000)
        self.seg(rec.folder, t + 60, 3000)
        node = Node("Ingresso", k, {}, None, cameras=lambda: [], addresses=lambda: [], hostname=lambda: "n.local",
                    model=lambda: "Raspberry Pi 4", credentials=lambda: None, recorder=rec)
        srv = NodeServer(node, cert, key, host="127.0.0.1", port=0).start()
        try:
            client = NodeClient(NodeConfig("ingresso", "Ingresso", f"https://127.0.0.1:{srv.port}"), cert.read_text(), k)
            self.assertTrue(client.info()["registrazione"])
            res = client.recordings(t - 10, t + 3600)
            self.assertEqual(len(res["spezzoni"]), 2)
            self.assertEqual(res["spezzoni"][0]["nome"], a.name)
            self.assertIn("registra", res["stato"])
            out = self.d / "scaricato.mp4"
            self.assertEqual(client.download_recording(a.name, out), 5000)
            self.assertEqual(out.read_bytes(), a.read_bytes())
            with self.assertRaises(NodeError):
                client.download_recording("../../x.mp4", self.d / "y.mp4")
            with self.assertRaises(NodeError):
                client.download_recording("20000101-000000.mp4", self.d / "y.mp4")  # non esiste: 404
            self.assertFalse((self.d / "y.mp4").exists())
            self.assertIn("registrazione", client.status())
        finally:
            srv.stop()


class NodeRecordingEndToEndTest(unittest.TestCase):
    """Video veri sul nodo → Mirto li elenca, li scarica e li esporta (come quando il Mac era spento)."""

    def test_export_from_node(self):
        import subprocess
        from types import SimpleNamespace as N

        from mirto.recording import manager as M
        from mirto.recording.ffmpeg import detect
        from mirto_node import recording as R

        ff = detect()
        if not (ff.ok and ff.encoder):
            self.skipTest("ffmpeg non disponibile")
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            rec = R.NodeRecorder({"attiva": True}, api=NodeRecordingTest.FakeApi())
            rec.folder = d / "nodo" / "cam"
            rec.folder.mkdir(parents=True)
            t0 = float(int(time.time()) - 300)
            for i in range(2):  # due spezzoni veri da 3 s registrati "sul nodo"
                name = time.strftime("%Y%m%d-%H%M%S.mp4", time.gmtime(t0 + 3 * i))
                subprocess.run([ff.path, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10",
                                "-t", "3", "-c:v", ff.encoder, "-pix_fmt", "yuv420p", "-g", "10",
                                str(rec.folder / name)], check=True)
            cert, key = node_cert(d / "cert")
            k = generate_key()
            node = Node("Ingresso", k, {}, None, cameras=lambda: [], addresses=lambda: [], hostname=lambda: "n",
                        model=lambda: "Raspberry Pi 4", credentials=lambda: None, recorder=rec)
            srv = NodeServer(node, cert, key, host="127.0.0.1", port=0).start()
            try:
                client = NodeClient(NodeConfig("ingresso", "Ingresso", f"https://127.0.0.1:{srv.port}"),
                                    cert.read_text(), k)

                class Cfg:
                    def get(self, key, default=None):
                        return default

                mgr = M.RecordingManager(Cfg(), N(sources={}), d / "mac")
                mgr.node_client_for = lambda cam_id: client if cam_id == "net:ingresso" else None
                remote = mgr.node_segments("net:ingresso", t0 - 10, t0 + 100)
                self.assertEqual(len(remote), 2)
                self.assertTrue(all(isinstance(r, M.RemoteSegment) and not r.path.exists() for r in remote))
                self.assertEqual(mgr.node_segments("altra", t0, t0 + 10), [])
                path = mgr.fetch("net:ingresso", remote[0])
                self.assertEqual(path.stat().st_size, remote[0].size)
                out = d / "export.mp4"
                self.assertEqual(mgr.export("net:ingresso", t0 + 1, t0 + 5, out, segments=remote), "")
                self.assertGreater(out.stat().st_size, 1000)
                self.assertTrue(remote[1].path.exists())  # scaricato per l'esportazione
            finally:
                srv.stop()


class PairingPinTest(unittest.TestCase):
    """S-01: l'abbinamento chiede il PIN e ogni Mirto riceve una chiave sua."""

    def setUp(self):
        from mirto_node.keys import KeyStore
        from mirto_node.server import PairingWindow

        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.d = Path(self.tmp.name)
        self.cert, cert_key = node_cert(self.d / "cert")
        self.install_key = generate_key()
        (self.d / "chiave").write_text(self.install_key)  # nodo installato prima di questa versione
        self.store = KeyStore(self.d)
        self.window = PairingWindow(self.d, boot_flag=self.d / "mai", boot_pin=self.d / "pin-di-avvio.txt")
        self.node = Node("Ingresso", "", {}, None, cameras=lambda: [], addresses=lambda: [],
                         hostname=lambda: "mirto-ingresso.local", model=lambda: "Raspberry Pi 4",
                         credentials=lambda: None, pairing=self.window, keys=self.store)
        self.srv = NodeServer(self.node, self.cert, cert_key, host="127.0.0.1", port=0).start()
        self.url = f"https://127.0.0.1:{self.srv.port}"

    def tearDown(self):
        self.srv.stop()
        self.tmp.cleanup()

    def post(self, payload: dict) -> tuple[int, dict]:
        from mirto.actions.pairing import unsigned_request

        status, obj, _pem, _fp = unsigned_request(self.url, "POST", "/abbina", payload)
        return status, obj

    def works(self, key: str) -> bool:
        """La chiave è buona per le richieste firmate?"""
        client = NodeClient(NodeConfig("ingresso", "Ingresso", self.url), self.cert.read_text(), key)
        try:
            client.status()
        except NodeError:
            return False
        return True

    def wrong(self, pin: str) -> str:
        return "000000" if pin != "000000" else "111111"

    def test_no_window_until_someone_opens_it(self):
        self.assertEqual(self.window.remaining(), 0)  # prima bastava riavviare il servizio
        status, obj = self.post({"client": "Mirto"})
        self.assertEqual(status, 403)
        self.assertNotIn("chiave", obj)

    def test_claim_without_pin_is_refused(self):
        self.window.open()
        status, obj = self.post({"client": "Mirto"})
        self.assertEqual(status, 403)
        self.assertIn("PIN", obj["errore"])
        self.assertNotIn("chiave", obj)
        self.assertFalse(self.window.paired())
        self.assertNotEqual(self.window.remaining(), 0)  # un tentativo a vuoto non chiude la finestra

    def test_five_wrong_pins_close_the_window(self):
        pin = self.window.open()
        for i in range(4):
            status, obj = self.post({"client": "ladro", "pin": "000000" if pin != "000000" else "111111"})
            self.assertEqual(status, 403, i)
            self.assertIn("restano", obj["errore"])
        status, obj = self.post({"client": "ladro", "pin": "999999" if pin != "999999" else "111111"})
        self.assertEqual(status, 403)
        self.assertEqual(self.window.remaining(), 0, "dopo 5 PIN sbagliati la finestra si chiude")
        self.assertEqual(self.post({"client": "Mirto", "pin": pin})[0], 403)  # nemmeno quello giusto
        self.assertFalse(self.window.paired())

    def test_every_pairing_gets_its_own_key(self):
        pin = self.window.open()
        status, first = self.post({"client": "Mirto su mac", "pin": pin})
        self.assertEqual(status, 200)
        self.assertEqual(len(first["chiave"]), 64)
        self.assertNotEqual(first["chiave"], self.install_key, "la chiave dell'installazione non si consegna più")
        self.assertEqual(self.window.remaining(), 0, "al primo abbinamento la finestra si chiude")

        pin2 = self.window.open()  # secondo Mac: mirto-nodo abbina
        status, second = self.post({"client": "Mirto su portatile", "pin": pin2})
        self.assertEqual(status, 200)
        self.assertNotEqual(second["chiave"], first["chiave"])
        self.assertTrue(self.works(first["chiave"]), "il secondo abbinamento non invalida il primo")
        self.assertTrue(self.works(second["chiave"]))
        self.assertEqual(len(self.store.files()), 2)

    def test_old_install_key_keeps_working_until_revoked(self):
        self.assertTrue(self.works(self.install_key))  # nodo abbinato prima dell'aggiornamento
        pin = self.window.open()
        _status, new = self.post({"client": "Mirto", "pin": pin})
        self.assertEqual(self.node.pairing_state()["chiave_installazione"], True)

        self.assertEqual(self.store.revoke_all(), 2)  # la chiave nuova e quella dell'installazione
        self.assertFalse(self.works(self.install_key))
        self.assertFalse(self.works(new["chiave"]))
        self.assertEqual(self.node.pairing_state()["chiave_installazione"], False)

    def test_pairing_state_says_who_and_when_but_not_to_strangers(self):
        from mirto.actions.pairing import unsigned_request

        pin = self.window.open()
        self.post({"client": "Mirto su mac-di-marco", "pin": pin})
        st = self.node.pairing_state()
        self.assertIn("Mirto su mac-di-marco", st["da"])
        self.assertGreater(st["quando"], 0)
        self.assertEqual(st["chiavi"], 1)
        # GET /abbina non è firmato: a chi passa dalla rete non si dice chi è abbinato
        _status, public, _pem, _fp = unsigned_request(self.url, "GET", "/abbina")
        self.assertNotIn("da", public)
        self.assertNotIn("chiavi", public)

    def test_pin_is_six_digits_and_only_in_protected_files(self):
        pin = self.window.open()
        self.assertRegex(pin, r"^[0-9]{6}$")
        if POSIX:
            self.assertEqual((self.d / "pin").stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.window.pin(), pin)
        self.window.close()
        self.assertEqual(self.window.pin(), "")

    def test_first_boot_writes_the_pin_on_the_boot_partition(self):
        from mirto_node.server import PairingWindow

        (self.d / "apri-abbinamento").write_text("")  # lo lascia install.sh --primo-avvio
        boot_pin = self.d / "pin-di-avvio.txt"
        w = PairingWindow(self.d, boot_flag=self.d / "mai", boot_pin=boot_pin)
        self.assertEqual(w.remaining(), PairingWindow.SENZA_SCADENZA)  # il PIN viaggia stampato col nodo
        self.assertIn(w.pin(), boot_pin.read_text())
        self.assertIn("non scade", boot_pin.read_text(),
                      "il foglio prometteva «vale 5 minuti dall'accensione»: ora non è più vero")
        self.assertFalse((self.d / "apri-abbinamento").exists())
        w.claim("Mirto", w.pin())
        self.assertFalse(boot_pin.exists(), "abbinato: il PIN sparisce dalla microSD")

    def test_revoking_one_client_leaves_the_others_signing(self):
        pin = self.window.open()
        _status, first = self.post({"client": "Mirto su mac-di-marco", "pin": pin})
        pin2 = self.window.open()
        _status, second = self.post({"client": "Mirto su portatile", "pin": pin2})
        self.assertTrue(self.works(first["chiave"]) and self.works(second["chiave"]))

        self.assertEqual(self.store.revoke("Mirto su portatile"), 1)
        self.assertFalse(self.works(second["chiave"]), "il Mirto revocato non firma più")
        self.assertTrue(self.works(first["chiave"]), "gli altri abbinamenti restano validi")
        self.assertEqual([e["cliente"] for e in self.store.entries()], ["Mirto su mac-di-marco"])

    def test_revoked_install_key_is_refused(self):
        self.assertTrue(self.works(self.install_key))
        self.assertEqual(self.store.revoke("installazione"), 1)
        self.assertFalse(self.works(self.install_key), "chiave di installazione ritirata: richiesta firmata = 401")
        self.assertFalse((self.d / "chiave").exists(), "il file viene cancellato, non solo segnato")
        self.assertTrue((self.d / "chiave-revocata").exists(), "e non deve tornare con una reinstallazione")
        self.assertEqual(self.node.pairing_state()["chiave_installazione"], False)

    def test_each_key_says_who_and_from_where(self):
        pin = self.window.open()
        self.post({"client": "Mirto su mac-di-marco", "pin": pin})
        entry = self.store.entries()[0]
        self.assertEqual(entry["cliente"], "Mirto su mac-di-marco")
        self.assertEqual(entry["indirizzo"], "127.0.0.1")
        self.assertGreater(entry["quando"], 0)
        if POSIX:
            self.assertEqual((self.d / "chiavi").stat().st_mode & 0o777, 0o700)
            for name in (entry["nome"] + ".key", entry["nome"] + ".json"):
                self.assertEqual((self.d / "chiavi" / name).stat().st_mode & 0o777, 0o600, name)

    def test_abbina_shows_the_pin_in_progress_instead_of_a_second_one(self):
        pin, fresh = self.window.open_or_current()
        self.assertTrue(fresh)
        again, fresh_again = self.window.open_or_current()
        self.assertEqual((again, fresh_again), (pin, False), "mai due PIN validi insieme")
        self.assertEqual(self.post({"client": "Mirto", "pin": pin})[0], 200)

    def test_wrong_pins_are_counted_across_processes(self):
        from mirto_node.server import PairingWindow

        pin = self.window.open()
        bad = self.wrong(pin)
        for i in range(4):
            self.assertEqual(self.post({"client": "ladro", "pin": bad})[0], 403, i)
        cli = PairingWindow(self.d, boot_flag=self.d / "mai", boot_pin=self.d / "pin-di-avvio.txt")
        self.assertFalse(cli.claim("ladro", bad)[0])  # il quinto tentativo, da mirto-nodo
        self.assertEqual(self.window.remaining(), 0, "i tentativi si contano su file, non in memoria")

    def test_expired_window_takes_the_pin_off_the_microsd(self):
        from mirto_node.server import PairingWindow

        now = [1_800_000_000.0]
        boot_pin = self.d / "pin-scaduto.txt"
        (self.d / "apri-abbinamento").write_text("")
        w = PairingWindow(self.d, clock=lambda: now[0], boot_flag=self.d / "mai", boot_pin=boot_pin)
        self.assertTrue(boot_pin.exists())
        # Finché il nodo non è mai stato abbinato il PIN stampato resta valido. Dal primo abbinamento
        # in poi le finestre scadono di nuovo in cinque minuti: allora il PIN deve sparire dalla microSD.
        w.mark_paired("Mirto")
        w.open(on_boot=True)
        self.assertTrue(boot_pin.exists())
        now[0] += 301
        self.assertEqual(w.remaining(), 0)
        self.assertFalse(boot_pin.exists(), "la microSD è FAT: senza permessi, il PIN non resta lì")
        self.assertFalse((self.d / "pin").exists())
        self.assertEqual(PairingWindow(self.d, clock=lambda: now[0], boot_flag=self.d / "mai",
                                       boot_pin=boot_pin).remaining(), 0, "il file-segnale era già consumato")


class ScrittureCheSopravvivonoTest(unittest.TestCase):
    """I file di stato del nodo devono arrivare sul disco, non restare in memoria.

    Il 20/10, togliendo la corrente a un nodo un minuto dopo l'accensione, «pin» e «abbina_fino» sono
    rimasti da zero byte: il nome del file era registrato, il contenuto no. Da lì il nodo si presentava
    abbinabile con un PIN che non esisteva.
    """

    def test_the_file_is_written_whole_and_nothing_is_left_behind(self):
        from mirto_node import fs

        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "pin"
            fs.scrivi(dest, '{"pin": "123456"}\n')
            self.assertEqual(dest.read_text(), '{"pin": "123456"}\n')
            if not sys.platform.startswith("win"):   # su Windows i permessi di Unix non esistono
                self.assertEqual(dest.stat().st_mode & 0o777, 0o600)
            self.assertEqual([f.name for f in Path(d).iterdir()], ["pin"],
                             "il file di lavoro non deve restare lì")

    def test_a_second_write_replaces_the_first(self):
        from mirto_node import fs

        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "pin"
            fs.scrivi(dest, "vecchio\n")
            fs.scrivi(dest, "nuovo\n")
            self.assertEqual(dest.read_text(), "nuovo\n")
            self.assertEqual(sorted(f.name for f in Path(d).iterdir()), ["pin"])

    def test_it_really_asks_the_disk(self):
        """La rinomina atomica non basta: senza fsync il contenuto può non essere ancora sul disco."""
        import os as _os
        from mirto_node import fs

        chiamate = []
        vero = _os.fsync
        _os.fsync = lambda fd: chiamate.append(fd) or vero(fd)
        try:
            with tempfile.TemporaryDirectory() as d:
                fs.scrivi(Path(d) / "pin", "x\n")
        finally:
            _os.fsync = vero
        atteso = 1 if sys.platform.startswith("win") else 2   # una cartella, su Windows, non si apre
        self.assertGreaterEqual(len(chiamate), atteso, "il file e, dove si può, la cartella")


class AnelloSilenziosoTest(unittest.TestCase):
    """L'anello che gira non deve riempire il registro del nodo.

    A regime cancella uno spezzone al minuto, per sempre. Scritta a INFO, quella riga il 22/10 aveva
    riempito «ultimi errori del servizio» per trenta righe di fila: non c'era modo di sapere se ci
    fossero stati errori veri, proprio mentre se ne stava cercando uno. Un registro che dice sempre la
    stessa cosa non dice piu' niente.
    """

    def righe(self, quanti: int) -> list[str]:
        """Fa cancellare ``quanti`` spezzoni all'anello e restituisce cosa ha scritto nel registro."""
        import logging
        import os

        from mirto_node import recording as R

        cartella = Path(self.enterContext(tempfile.TemporaryDirectory())) / "reg" / "cam"
        cartella.mkdir(parents=True)
        inizio = 1_800_000_000.0
        for i in range(quanti + 1):     # +1: l'ultimo non si cancella mai, lo sta scrivendo MediaMTX
            f = cartella / time.strftime("%Y%m%d-%H%M%S.mp4", time.gmtime(inizio + 60 * i))
            f.write_bytes(b"x" * 1000)
            os.utime(f, (inizio + 60 * i, inizio + 60 * i))
        rec = R.NodeRecorder({"attiva": True, "disco": "", "massimo_gb": 1000 / 1e9,
                              "minimo_libero_percento": 0}, api=None)
        rec.folder = cartella
        with self.assertLogs("mirto_node.recording", level=logging.DEBUG) as registro:
            rec.cleanup()
        return [r for r in registro.output if "anello" in r]

    def test_the_ordinary_trim_does_not_shout(self):
        righe = self.righe(1)
        self.assertTrue(righe, "qualcosa resta scritto: a DEBUG, per chi lo va a cercare")
        self.assertTrue(all(r.startswith("DEBUG") for r in righe),
                        f"uno spezzone al minuto e' la normalita': non va a INFO. {righe}")

    def test_more_than_one_at_a_time_is_worth_saying(self):
        righe = self.righe(3)
        self.assertTrue(any(r.startswith("INFO") for r in righe),
                        f"piu' d'uno in un colpo vuol dire che qualcosa si e' accumulato: va detto. {righe}")


class AiutoDelComandoNomeTest(unittest.TestCase):
    """L'aiuto di ``mirto-nodo`` non promette più di cambiare il nome in rete.

    Dal 21/10 il nome è **solo un'etichetta** e l'hostname non si muove più: il codice lo faceva già,
    ma le due righe di aiuto continuavano a dire il contrario — e quelle le legge chi sta installando un
    nodo, cioè esattamente la persona che non ha modo di verificare. Un aiuto che mente è peggio di un
    aiuto che manca, perché gli si crede.
    """

    def test_the_help_does_not_promise_to_rename_the_machine(self):
        from mirto_node import cli

        testi = [cli.__doc__ or ""] + [descrizione for _f, descrizione in cli.COMMANDS.values()]
        for testo in testi:
            with self.subTest(testo[:40]):
                self.assertNotIn("cambia nome del nodo e nome in rete", testo)
                self.assertNotIn("cambia il nome del nodo e il nome in rete", testo)
        aiuto_nome = cli.COMMANDS["nome"][1]
        self.assertIn("etichetta", aiuto_nome)
        self.assertIn("non cambia", aiuto_nome)


class NomeDalWizardTest(unittest.TestCase):
    """``POST /configura/nome``: il nome lo dà il wizard di Mirto, non più cinque sessioni ssh.

    Il nodo non può cambiare da solo il nome della macchina — gira senza privilegi (S-03) — quindi
    lascia un biglietto al servizio da root e intanto risponde col nome in rete nuovo, che Mirto
    deve salvare al posto del vecchio.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.cert, cert_key = node_cert(self.d / "cert")
        self.key = generate_key()
        self.toml = self.d / "node.toml"
        self.toml.write_text('nome = "Nodo"\nporta = 8443\n')
        self.node = Node("Nodo", self.key, {}, None, cameras=lambda: [], addresses=lambda: [],
                         hostname=lambda: "mirto-node-17a9.local", model=lambda: "Raspberry Pi 4",
                         credentials=lambda: None, folder=self.d, rename=self.scrivi)
        self.srv = NodeServer(self.node, self.cert, cert_key, host="127.0.0.1", port=0).start()
        self.client = NodeClient(NodeConfig("nodo", "Nodo", f"https://127.0.0.1:{self.srv.port}"),
                                 self.cert.read_text(), self.key)

    def tearDown(self):
        self.srv.stop()
        self.tmp.cleanup()

    def scrivi(self, nome: str) -> None:
        from mirto_node.cli import set_toml_values

        self.toml.write_text(set_toml_values(self.toml.read_text(), None, {"nome": nome}))

    def test_the_name_is_a_label_and_the_network_name_does_not_move(self):
        """Deciso il 21/10 provando: il nome che si legge non deve toccare l'identità della macchina."""
        res = self.client.set_name("Garage Nord")
        self.assertEqual(res["nome"], "Garage Nord")
        self.assertEqual(res["host"], "mirto-node-17a9", "il nome in rete è di fabbrica, e resta")
        self.assertEqual(self.node.name, "Garage Nord")          # subito, senza riavviare il servizio
        self.assertIn('nome = "Garage Nord"', self.toml.read_text())   # e anche dopo un riavvio
        self.assertEqual([f.name for f in self.d.iterdir() if f.name.endswith("-da-applicare")], [],
                         "niente da chiedere a root: non si cambia più l'hostname")
        self.assertEqual(self.client.info()["nome"], "Garage Nord")

    def test_a_name_without_letters_is_refused_and_changes_nothing(self):
        for brutto in ("", "   ", "###", "--"):
            with self.assertRaises(NodeError, msg=brutto) as ctx:
                self.client.set_name(brutto)
            self.assertIn("400", str(ctx.exception), brutto)
        self.assertEqual(self.node.name, "Nodo")
        self.assertIn('nome = "Nodo"', self.toml.read_text())

    def test_a_long_name_is_cut_not_refused(self):
        res = self.client.set_name("Garage " * 20)
        self.assertLessEqual(len(res["nome"]), 40)

    def test_without_a_signature_the_name_cannot_be_changed(self):
        import http.client
        import ssl

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        conn = http.client.HTTPSConnection("127.0.0.1", self.srv.port, context=ctx, timeout=5)
        try:
            conn.request("POST", "/configura/nome")   # senza corpo: la firma manca comunque
            self.assertEqual(conn.getresponse().status, 401)
        finally:
            conn.close()
        self.assertEqual(self.node.name, "Nodo")


class WifiDalWizardTest(unittest.TestCase):
    """``/configura/wifi``: le reti che vede **il nodo**, e il collegamento, senza terminale.

    È il pezzo più delicato del wizard, perché qui passa la password del Wi-Fi di casa: non deve finire
    nel registro, né nel biglietto che legge il servizio da root, e il file che la contiene si cancella
    appena è stata usata.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.cert, cert_key = node_cert(self.d / "cert")
        self.key = generate_key()
        self.node = Node("Nodo", self.key, {}, None, cameras=lambda: [],
                         addresses=lambda: ["192.168.2.3"], hostname=lambda: "n.local",
                         model=lambda: "Pi", credentials=lambda: None, folder=self.d)
        self.server = NodeServer(self.node, self.cert, cert_key, host="127.0.0.1", port=0).start()
        self.client = NodeClient(NodeConfig("nodo", "Nodo", f"https://127.0.0.1:{self.server.port}"),
                                 self.cert.read_text(), self.key)

    def tearDown(self):
        self.server.stop()
        self.tmp.cleanup()

    def biglietto(self) -> str:
        f = self.d / "wifi-da-fare"
        return f.read_text().strip() if f.exists() else ""

    def test_the_networks_are_the_ones_the_node_sees(self):
        (self.d / "reti-trovate.json").write_text(json.dumps({"quando": 10, "reti": [
            {"ssid": "CasaMia", "segnale": 72, "sicurezza": "WPA2", "in_uso": True},
            {"ssid": "Vicino", "segnale": 31, "sicurezza": "WPA2", "in_uso": False}]}))
        st = self.client.wifi_state()
        self.assertEqual([r["ssid"] for r in st["reti"]], ["CasaMia", "Vicino"])
        self.assertEqual(st["in_uso"], "CasaMia")
        self.assertEqual(st["indirizzi"], ["192.168.2.3"], "serve a dire «puoi staccare il cavo»")

    def test_looking_for_networks_is_a_job_for_root(self):
        res = self.client.find_networks()
        self.assertEqual(res["esito"], "avviato")
        self.assertGreaterEqual(res["attesa_s"], 20)
        self.assertEqual(self.biglietto(), "cerca")

    def test_the_password_never_goes_through_the_note_for_root(self):
        self.client.join_wifi("CasaMia", "unapasswordlunga")
        self.assertEqual(self.biglietto(), "collega", "il biglietto dice solo cosa fare")
        richiesta = self.d / "wifi-da-collegare.json"
        dati = json.loads(richiesta.read_text())
        self.assertEqual((dati["ssid"], dati["password"]), ("CasaMia", "unapasswordlunga"))
        if POSIX:
            self.assertEqual(richiesta.stat().st_mode & 0o777, 0o600, "la legge solo il nodo")
        self.assertNotIn("unapasswordlunga", self.biglietto())

    def test_an_open_network_needs_no_password(self):
        self.client.join_wifi("BarWifi", "")
        self.assertEqual(json.loads((self.d / "wifi-da-collegare.json").read_text())["password"], "")

    def test_a_password_that_cannot_be_a_wifi_password_is_refused(self):
        for ssid, pwd in (("", "unapasswordlunga"), ("x" * 33, "unapasswordlunga"),
                          ("CasaMia", "corta"), ("CasaMia", "x" * 64)):
            with self.assertRaises(NodeError, msg=f"{ssid!r}/{pwd!r}") as ctx:
                self.client.join_wifi(ssid, pwd)
            self.assertIn("400", str(ctx.exception))
        self.assertFalse((self.d / "wifi-da-collegare.json").exists())
        self.assertEqual(self.biglietto(), "", "niente biglietto da una richiesta storta")

    def test_an_old_node_says_it_cannot(self):
        muto = Node("Nodo", self.key, {}, None, cameras=lambda: [], addresses=lambda: [],
                    hostname=lambda: "n.local", model=lambda: "Pi", credentials=lambda: None)
        with self.assertRaises(ActionError) as ctx:
            muto.set_wifi({"azione": "cerca"})
        self.assertEqual(ctx.exception.code, 501)


class AltriComputerTest(unittest.TestCase):
    """``/configura/abbinamenti``: aggiungere un secondo computer, e staccarne uno, senza terminale.

    Finora, per far entrare il portatile di casa, bisognava andare sul nodo con ``mirto-nodo abbina``
    e leggere il PIN da lì. Qui lo chiede Mirto — che una chiave valida ce l'ha già, quindi il PIN non
    è un segreto in più per lui — e il nodo lo manda **solo** mentre la finestra è aperta.
    """

    def setUp(self):
        from mirto_node.keys import KeyStore
        from mirto_node.server import PairingWindow

        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.d = Path(self.tmp.name)
        self.cert, cert_key = node_cert(self.d / "cert")
        self.install_key = generate_key()
        (self.d / "chiave").write_text(self.install_key)
        self.store = KeyStore(self.d)
        self.window = PairingWindow(self.d, boot_flag=self.d / "mai", boot_pin=self.d / "pin-di-avvio.txt")
        self.node = Node("Ingresso", "", {}, None, cameras=lambda: [], addresses=lambda: [],
                         hostname=lambda: "n.local", model=lambda: "Pi", credentials=lambda: None,
                         pairing=self.window, keys=self.store, folder=self.d)
        self.srv = NodeServer(self.node, self.cert, cert_key, host="127.0.0.1", port=0).start()
        self.url = f"https://127.0.0.1:{self.srv.port}"

    def tearDown(self):
        self.srv.stop()
        self.tmp.cleanup()

    def cliente(self, key: str) -> NodeClient:
        return NodeClient(NodeConfig("ingresso", "Ingresso", self.url), self.cert.read_text(), key)

    def abbina(self, nome: str) -> str:
        """Abbina un Mirto come farebbe lui: finestra aperta, PIN, chiave sua."""
        from mirto.actions.pairing import unsigned_request

        pin = self.window.open_or_current()[0]
        _st, obj, _pem, _fp = unsigned_request(self.url, "POST", "/abbina", {"client": nome, "pin": pin})
        return str((obj or {}).get("chiave") or "")

    def works(self, key: str) -> bool:
        try:
            self.cliente(key).status()
        except NodeError:
            return False
        return True

    def test_the_list_says_who_is_paired_and_from_where(self):
        primo = self.abbina("Mirto su mac-di-marco")
        self.abbina("Mirto su portatile")
        st = self.cliente(primo).pairings()
        self.assertEqual(sorted(k["cliente"] for k in st["chiavi"]),
                         ["Mirto su mac-di-marco", "Mirto su portatile"])
        self.assertEqual({k["indirizzo"] for k in st["chiavi"]}, {"127.0.0.1"})
        self.assertTrue(all(k["quando"] > 0 for k in st["chiavi"]))
        self.assertTrue(st["chiave_installazione"], "c'è ancora quella dell'installazione: va detto")

    def test_the_installation_key_is_retired_from_the_panel(self):
        """La strada che Mirto indica deve esistere davvero (22/10).

        *Sistema* e la diagnosi dicono «toglila in Altri computer…». Fino a oggi, arrivati lì e premuto
        il pulsante, il nodo rispondeva «abbinamento sconosciuto»: quel nome non è fra gli abbinamenti,
        perché non è un computer. Era l'unica strada indicata all'utente, ed era chiusa.
        """
        primo = self.abbina("Mirto su mac-di-marco")
        self.assertTrue(self.works(self.install_key), "prima: la chiave dell'installazione comanda il nodo")
        risposta = self.cliente(primo).revoke_pairing("installazione")
        self.assertEqual(risposta["esito"], "ok")
        self.assertFalse(risposta["chiave_installazione"], "lo stato che torna lo dice gia'")
        self.assertFalse(self.works(self.install_key), "dopo: non comanda piu' niente")
        self.assertTrue(self.works(primo), "e l'unico computer abbinato resta abbinato")

    def test_retiring_it_twice_says_so_instead_of_pretending(self):
        primo = self.abbina("Mirto su mac-di-marco")
        self.cliente(primo).revoke_pairing("installazione")
        with self.assertRaises(NodeError) as caso:
            self.cliente(primo).revoke_pairing("installazione")
        self.assertIn("gia' stata ritirata".replace("gia'", "già"), str(caso.exception))

    def test_the_last_pairing_is_still_protected(self):
        """Togliere la chiave dell'installazione non deve aprire la porta a restare senza nessuno."""
        primo = self.abbina("Mirto su mac-di-marco")
        self.cliente(primo).revoke_pairing("installazione")
        with self.assertRaises(NodeError) as caso:
            self.cliente(primo).revoke_pairing("mirto-su-mac-di-marco")
        self.assertIn("ultimo abbinamento", str(caso.exception))
        self.assertTrue(self.works(primo))

    def test_the_pin_travels_only_while_the_window_is_open(self):
        primo = self.abbina("Mirto su mac-di-marco")
        self.window.close()
        st = self.cliente(primo).pairings()
        self.assertFalse(st["finestra"]["aperto"])
        self.assertNotIn("pin", st["finestra"], "a finestra chiusa il PIN non esce dal nodo")
        aperto = self.cliente(primo).open_pairing()
        self.assertRegex(aperto["pin"], r"^[0-9]{6}$")
        self.assertEqual(aperto["pin"], self.cliente(primo).pairings()["finestra"]["pin"])

    def test_opening_from_mirto_lets_a_second_computer_in(self):
        """È il punto di tutto il pannello: il secondo computer entra senza che nessuno apra un ssh."""
        from mirto.actions.pairing import unsigned_request

        primo = self.abbina("Mirto su mac-di-marco")
        self.window.close()
        pin = self.cliente(primo).open_pairing()["pin"]
        _st, obj, _pem, _fp = unsigned_request(self.url, "POST", "/abbina",
                                               {"client": "Mirto su portatile", "pin": pin})
        secondo = str((obj or {}).get("chiave") or "")
        self.assertTrue(self.works(secondo), "la chiave del secondo computer funziona")
        self.assertTrue(self.works(primo), "e il primo resta abbinato")

    def test_closing_the_window_from_mirto_shuts_the_door(self):
        from mirto.actions.pairing import unsigned_request

        primo = self.abbina("Mirto su mac-di-marco")
        pin = self.cliente(primo).open_pairing()["pin"]
        st = self.cliente(primo).close_pairing()
        self.assertFalse(st["finestra"]["aperto"])
        status, obj, _pem, _fp = unsigned_request(self.url, "POST", "/abbina",
                                                  {"client": "Mirto su ladro", "pin": pin})
        self.assertEqual(status, 403, obj)

    def test_detaching_a_computer_takes_its_key_away(self):
        primo = self.abbina("Mirto su mac-di-marco")
        secondo = self.abbina("Mirto su portatile")
        nome = next(k["nome"] for k in self.cliente(primo).pairings()["chiavi"]
                    if k["cliente"] == "Mirto su portatile")
        st = self.cliente(primo).revoke_pairing(nome)
        self.assertEqual(st["revocate"], 1)
        self.assertFalse(self.works(secondo), "staccato vuol dire che non firma più niente")
        self.assertTrue(self.works(primo))
        self.assertEqual([k["cliente"] for k in st["chiavi"]], ["Mirto su mac-di-marco"])

    def test_the_last_pairing_is_not_detached_from_here(self):
        """Staccare l'ultimo lascerebbe la casa senza nessun Mirto: quella strada resta sul nodo."""
        primo = self.abbina("Mirto su mac-di-marco")
        (self.d / "chiave").unlink()                 # via anche la chiave dell'installazione
        self.node.refresh_keys(force=True)
        nome = self.cliente(primo).pairings()["chiavi"][0]["nome"]
        with self.assertRaises(NodeError) as ctx:
            self.cliente(primo).revoke_pairing(nome)
        self.assertIn("409", str(ctx.exception))
        self.assertTrue(self.works(primo), "e la chiave è ancora lì")

    def test_an_unknown_pairing_is_refused(self):
        """Nomi che non esistono, o che provano a uscire dalla cartella: no.

        Fino al 22/10 in questo elenco c'era anche «installazione», con scritto «nemmeno quella si
        tocca da qui». Rileggendolo non era una decisione, era la conseguenza di com'e' fatto l'elenco:
        la chiave dell'installazione non e' un abbinamento e non compare fra le «entries». Intanto, dal
        21/10, *Sistema* e la diagnosi dicevano all'utente di toglierla **proprio da li'**. Delle due
        una, e quella giusta e' dare la strada che il programma promette: adesso si ritira, e la
        protezione che conta — il nodo non resta senza nessun Mirto — sta nelle prove qui sopra.
        """
        primo = self.abbina("Mirto su mac-di-marco")
        for nome in ("", "non-esiste", "../chiave", "chiave/../../etc"):
            with self.assertRaises(NodeError, msg=nome) as ctx:
                self.cliente(primo).revoke_pairing(nome)
            self.assertIn("400", str(ctx.exception), nome)
        self.assertTrue(self.works(self.install_key), "nessuno di quei nomi l'ha sfiorata")

    def test_an_old_node_says_it_cannot(self):
        muto = Node("Nodo", generate_key(), {}, None, cameras=lambda: [], addresses=lambda: [],
                    hostname=lambda: "n.local", model=lambda: "Pi", credentials=lambda: None)
        with self.assertRaises(ActionError) as ctx:
            muto.set_pairings({"azione": "apri"})
        self.assertEqual(ctx.exception.code, 501)


class DiscoDalWizardTest(unittest.TestCase):
    """``/configura/disco``: il disco delle registrazioni senza aprire un terminale.

    È l'unico pezzo della configurazione che **cancella** dati, quindi queste prove guardano soprattutto
    quello che il nodo deve *rifiutare*: un dispositivo che non viene dalla sua ultima ricerca, una
    formattazione senza conferma, dei limiti impossibili. La ricerca non propone mai il disco da cui
    parte il sistema (``usb_candidates``, provata a parte), così quella strada è chiusa a monte.
    """

    DISCHI = [{"path": "/dev/sda1", "disk": "/dev/sda", "size": 512_000_000_000, "fstype": "ext4",
               "uuid": "1234-5678", "mount": "/mnt/mirto-reg", "model": "Samsung T7",
               "parts": [["/dev/sda1", "/mnt/mirto-reg"]], "is_part": True,
               "libero": 400_000_000_000, "totale": 512_000_000_000},
              {"path": "/dev/sdb1", "disk": "/dev/sdb", "size": 64_000_000_000, "fstype": "ntfs",
               "uuid": "", "mount": "", "model": "Kingston", "parts": [["/dev/sdb1", ""]],
               "is_part": True}]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.cert, cert_key = node_cert(self.d / "cert")
        self.key = generate_key()
        self.scritto = []
        self.rec = {"attiva": True, "disco": "/mnt/mirto-reg", "massimo_gb": 0,
                    "minimo_libero_percento": 10}
        self.node = Node("Nodo", self.key, {}, None, cameras=lambda: [], addresses=lambda: [],
                         hostname=lambda: "n.local", model=lambda: "Pi", credentials=lambda: None,
                         folder=self.d, rec_cfg=lambda: dict(self.rec),
                         set_rec_cfg=lambda v: (self.scritto.append(v), self.rec.update(v)))
        self.server = NodeServer(self.node, self.cert, cert_key, host="127.0.0.1", port=0).start()
        self.client = NodeClient(NodeConfig("nodo", "Nodo", f"https://127.0.0.1:{self.server.port}"),
                                 self.cert.read_text(), self.key)

    def tearDown(self):
        self.server.stop()
        self.tmp.cleanup()

    def biglietto(self) -> str:
        f = self.d / "disco-da-fare"
        return f.read_text().strip() if f.exists() else ""

    def trovati(self, dischi=None) -> None:
        (self.d / "dischi-trovati.json").write_text(json.dumps(
            {"quando": 10, "dischi": self.DISCHI if dischi is None else dischi}))

    def test_the_disks_are_the_ones_the_node_sees(self):
        self.trovati()
        st = self.client.disk_state()
        self.assertEqual([d["path"] for d in st["dischi"]], ["/dev/sda1", "/dev/sdb1"])
        self.assertEqual(st["disco"], "/mnt/mirto-reg", "quello su cui registra adesso")
        self.assertEqual(st["minimo_libero_percento"], 10)
        self.assertIn("ext4", st["formati_buoni"], "la finestra deve sapere cosa va preparato")

    def test_looking_for_disks_is_a_job_for_root(self):
        res = self.client.find_disks()
        self.assertEqual(res["esito"], "avviato")
        self.assertEqual(self.biglietto(), "cerca")

    def test_an_already_good_disk_is_only_mounted(self):
        self.trovati()
        res = self.client.use_disk("/dev/sda1")
        self.assertEqual((res["esito"], res["dispositivo"]), ("avviato", "/dev/sda1"))
        self.assertEqual(self.biglietto(), "usa /dev/sda1")

    def test_a_disk_to_format_is_not_used_as_it_is(self):
        """Il nodo distingue «usa» da «prepara»: su un ntfs non si registra, e non si formatta di nascosto."""
        self.trovati()
        with self.assertRaises(NodeError) as ctx:
            self.client.use_disk("/dev/sdb1")
        self.assertIn("409", str(ctx.exception))
        self.assertEqual(self.biglietto(), "")

    def test_formatting_without_the_confirmation_is_refused(self):
        self.trovati()
        with self.assertRaises(NodeError) as ctx:
            self.client.prepare_disk("/dev/sdb", "")
        self.assertIn("400", str(ctx.exception))
        self.assertEqual(self.biglietto(), "", "niente biglietto: non si formatta per sbaglio")
        res = self.client.prepare_disk("/dev/sdb", "/dev/sdb")
        self.assertEqual(self.biglietto(), "prepara /dev/sdb")
        self.assertGreaterEqual(res["attesa_s"], 60, "formattare un disco grande dura minuti")

    def test_only_a_disk_from_the_last_search_can_be_formatted(self):
        """La ricerca non propone il disco da cui parte il sistema: per questo «prepara» guarda lì."""
        self.trovati()
        for dev in ("/dev/mmcblk0", "/dev/sdz", "/dev/sda1"):   # microSD, mai visto, e una partizione
            with self.assertRaises(NodeError, msg=dev) as ctx:
                self.client.prepare_disk(dev, dev)
            self.assertIn("400", str(ctx.exception), dev)
        self.assertEqual(self.biglietto(), "")

    def test_a_device_name_that_is_not_one_is_refused(self):
        self.trovati()
        for dev in ("", "/dev/../etc/passwd", "/dev/sda1; rm -rf /", "sda1", "/dev/sda1 /dev/sdb",
                    "/dev/" + "a" * 30):
            with self.assertRaises(NodeError, msg=repr(dev)) as ctx:
                self.client.use_disk(dev)
            self.assertIn("400", str(ctx.exception), repr(dev))
        self.assertEqual(self.biglietto(), "")

    def test_the_limits_are_saved_and_applied(self):
        self.trovati()
        st = self.client.set_disk_limits(True, 128, 15)
        self.assertEqual(self.scritto[-1], {"attiva": True, "massimo_gb": 128,
                                            "minimo_libero_percento": 15})
        self.assertEqual(self.biglietto(), "applica")
        self.assertEqual((st["massimo_gb"], st["minimo_libero_percento"]), (128, 15))

    def test_limits_that_would_fill_the_disk_are_refused(self):
        for attiva, massimo, minimo in ((True, -1, 10), (True, 99_999_999, 10), (True, 0, 0),
                                        (True, 0, 80), (True, 0, -5)):
            with self.assertRaises(NodeError, msg=f"{massimo}/{minimo}") as ctx:
                self.client.set_disk_limits(attiva, massimo, minimo)
            self.assertIn("400", str(ctx.exception))
        self.assertEqual(self.scritto, [], "niente di storto arriva in node.toml")
        self.assertEqual(self.biglietto(), "")

    def test_recording_can_be_switched_off_from_mirto(self):
        self.client.set_disk_limits(False, 0, 10)
        self.assertFalse(self.scritto[-1]["attiva"])
        self.assertEqual(self.biglietto(), "applica")

    def test_an_old_node_says_it_cannot(self):
        muto = Node("Nodo", self.key, {}, None, cameras=lambda: [], addresses=lambda: [],
                    hostname=lambda: "n.local", model=lambda: "Pi", credentials=lambda: None)
        with self.assertRaises(ActionError) as ctx:
            muto.set_disk({"azione": "cerca"})
        self.assertEqual(ctx.exception.code, 501)


class AudioDalWizardTest(unittest.TestCase):
    """``/configura/audio``: dove suona la sirena, senza aprire un terminale.

    Chi riceve un nodo e ha una cassa Bluetooth, oggi, dovrebbe fare ssh. La ricerca e l'abbinamento
    delle casse vogliono privilegi e durano secondi, quindi il nodo non li fa lui: lascia il biglietto
    al servizio da root e Mirto ripassa a vedere com'è andata.
    """

    SCHEDE = [{"n": 0, "id": "Headphones", "nome": "bcm2835 Headphones", "tipo": "jack"},
              {"n": 1, "id": "USBAudio", "nome": "USB Audio Device", "tipo": "usb"}]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.cert, cert_key = node_cert(self.d / "cert")
        self.key = generate_key()
        self.audio = {"uscita": "jack", "scheda": "Headphones"}
        self.player = FakePlayer()
        import mirto_node.server as srv

        self.srv_mod = srv
        self.vere_schede = srv.alsa_cards
        srv.alsa_cards = lambda *_a, **_k: self.SCHEDE
        self.addCleanup(lambda: setattr(srv, "alsa_cards", self.vere_schede))
        self.node = Node("Nodo", self.key, {}, self.player, cameras=lambda: [], addresses=lambda: [],
                         hostname=lambda: "n.local", model=lambda: "Pi", credentials=lambda: None,
                         folder=self.d, audio_cfg=lambda: dict(self.audio), set_audio_cfg=self.scrivi)
        self.server = NodeServer(self.node, self.cert, cert_key, host="127.0.0.1", port=0).start()
        self.client = NodeClient(NodeConfig("nodo", "Nodo", f"https://127.0.0.1:{self.server.port}"),
                                 self.cert.read_text(), self.key)

    def tearDown(self):
        self.server.stop()
        self.tmp.cleanup()

    def scrivi(self, valori: dict) -> None:
        self.audio = dict(valori)

    def biglietto(self) -> str:
        f = self.d / "audio-da-fare"
        return f.read_text().strip() if f.exists() else ""

    def test_the_outputs_offered_are_the_ones_the_node_really_has(self):
        st = self.client.audio_state()
        self.assertEqual((st["uscita"], st["scheda"]), ("jack", "Headphones"))
        etichette = " ".join(u["etichetta"] for u in st["uscite"])
        self.assertIn("Bluetooth", etichette)
        self.assertIn("USB Audio Device", etichette, "le schede vere del nodo, non un elenco inventato")
        self.assertEqual(st["casse_trovate"], {}, "nessuna ricerca ancora fatta")

    def test_choosing_an_output_saves_it_and_asks_root_to_apply_it(self):
        res = self.client.set_audio("usb", scheda="USBAudio")
        self.assertEqual(res["uscita"], "usb")
        self.assertEqual(self.audio, {"uscita": "usb", "scheda": "USBAudio"})
        self.assertEqual(self.biglietto(), "applica", "l'amixer e il riavvio li fa root (S-03)")

    def test_a_card_the_node_does_not_have_is_refused(self):
        with self.assertRaises(NodeError) as ctx:
            self.client.set_audio("usb", scheda="SchedaInventata")
        self.assertIn("400", str(ctx.exception))
        self.assertEqual(self.audio, {"uscita": "jack", "scheda": "Headphones"}, "e non cambia niente")

    def test_bluetooth_without_a_speaker_is_refused(self):
        with self.assertRaises(NodeError) as ctx:
            self.client.set_audio("bluetooth")
        self.assertIn("400", str(ctx.exception))
        self.assertFalse((self.d / "audio-da-fare").exists())

    def test_bluetooth_with_a_speaker_is_saved(self):
        self.client.set_audio("bluetooth", bluetooth_mac="aa:bb:cc:dd:ee:ff")
        self.assertEqual(self.audio["bluetooth_mac"], "AA:BB:CC:DD:EE:FF", "maiuscolo, come bluetoothctl")
        self.assertEqual(self.biglietto(), "applica")

    def test_looking_for_speakers_is_a_job_for_root(self):
        res = self.client.find_speakers()
        self.assertEqual(res["esito"], "avviato")
        self.assertGreaterEqual(res["attesa_s"], 15, "Mirto deve sapere quanto aspettare")
        self.assertEqual(self.biglietto(), "cerca")

    def test_the_result_of_the_search_comes_back_in_the_state(self):
        (self.d / "casse-trovate.json").write_text(
            '{"quando": 100, "casse": [{"mac": "AA:BB:CC:DD:EE:FF", "nome": "JBL Flip 6", "abbinata": false}]}')
        st = self.client.audio_state()
        self.assertEqual(st["casse_trovate"]["casse"][0]["nome"], "JBL Flip 6")

    def test_pairing_a_speaker_checks_the_address_first(self):
        for brutto in ("", "ciao", "AA:BB:CC:DD:EE", "../../etc/passwd"):
            with self.assertRaises(NodeError, msg=brutto) as ctx:
                self.client.pair_speaker(brutto)
            self.assertIn("400", str(ctx.exception), brutto)
        self.assertFalse((self.d / "audio-da-fare").exists(), "niente biglietto da una richiesta storta")
        self.client.pair_speaker("aa:bb:cc:dd:ee:ff")
        self.assertEqual(self.biglietto(), "abbina AA:BB:CC:DD:EE:FF")

    def test_the_test_sound_plays_here_and_now(self):
        res = self.client.test_sound()
        self.assertEqual(res["esito"], "ok")
        self.assertTrue(self.player.loops, "il din-don lo suona il nodo, non root")
        self.assertFalse((self.d / "audio-da-fare").exists(), "e non chiede niente a nessuno")

    def test_an_old_node_says_it_cannot(self):
        muto = Node("Nodo", self.key, {}, None, cameras=lambda: [], addresses=lambda: [],
                    hostname=lambda: "n.local", model=lambda: "Pi", credentials=lambda: None)
        with self.assertRaises(ActionError) as ctx:
            muto.set_audio({"azione": "cerca"})
        self.assertEqual(ctx.exception.code, 501)


class CameraDalWizardTest(unittest.TestCase):
    """``/configura/camera``: i nodi avranno camere diverse, e il modello non si può indovinare.

    Il sensore è lo stesso nei due modelli (NoIR e normale), quindi la scelta la fa una persona. Finché
    stava solo in ``mirto-nodo camera``, ogni nodo voleva una sessione ssh: con cinquanta nodi dai
    tester, e camere miste, non è una strada.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.cert, cert_key = node_cert(self.d / "cert")
        self.key = generate_key()
        self.cam = {"attiva": True, "noir": False, "larghezza": 1280, "altezza": 720, "fps": 15}
        self.node = Node("Nodo", self.key, {}, None, cameras=lambda: [], addresses=lambda: [],
                         hostname=lambda: "n.local", model=lambda: "Pi", credentials=lambda: None,
                         folder=self.d, camera_cfg=lambda: dict(self.cam), set_camera_cfg=self.scrivi)
        self.srv = NodeServer(self.node, self.cert, cert_key, host="127.0.0.1", port=0).start()
        self.client = NodeClient(NodeConfig("nodo", "Nodo", f"https://127.0.0.1:{self.srv.port}"),
                                 self.cert.read_text(), self.key)

    def tearDown(self):
        self.srv.stop()
        self.tmp.cleanup()

    def scrivi(self, valori: dict) -> None:
        self.cam = dict(self.cam, **valori) if valori.get("attiva") else {"attiva": False}

    def test_the_choices_offered_are_the_same_as_on_the_node(self):
        st = self.client.camera_state()
        self.assertEqual((st["attiva"], st["larghezza"], st["fps"]), (True, 1280, 15))
        self.assertIn("vista_dal_sistema", st, "Mirto deve poter dire «il nodo non vede la camera»")
        self.assertEqual([(r["larghezza"], r["altezza"], r["fps"]) for r in st["risoluzioni"]],
                         [(w, h, f) for _e, w, h, f in it.RESOLUTIONS])

    def test_a_noir_camera_is_saved_and_root_is_asked_to_apply_it(self):
        res = self.client.set_camera(True, noir=True, larghezza=1920, altezza=1080, fps=15)
        self.assertEqual((res["esito"], res["noir"], res["larghezza"]), ("ok", True, 1920))
        self.assertEqual(self.cam["noir"], True)
        self.assertTrue((self.d / "camera-da-applicare").exists(),
                        "MediaMTX lo riconfigura root: il servizio non ha i privilegi (S-03)")

    def test_turning_the_camera_off_needs_no_resolution(self):
        res = self.client.set_camera(False)
        self.assertEqual((res["esito"], res["attiva"]), ("ok", False))
        self.assertEqual(self.cam, {"attiva": False})

    def test_a_resolution_nobody_offered_is_refused_and_nothing_changes(self):
        for brutta in ((4096, 2160, 15), (1280, 720, 60), ("x", "y", "z")):
            with self.assertRaises(NodeError, msg=str(brutta)) as ctx:
                self.client.set_camera(True, larghezza=brutta[0], altezza=brutta[1], fps=brutta[2])
            self.assertIn("400", str(ctx.exception), str(brutta))
        self.assertEqual(self.cam["larghezza"], 1280)
        self.assertFalse((self.d / "camera-da-applicare").exists())

    def test_an_old_node_that_cannot_configure_the_camera_says_so(self):
        muto = Node("Nodo", self.key, {}, None, cameras=lambda: [], addresses=lambda: [],
                    hostname=lambda: "n.local", model=lambda: "Pi", credentials=lambda: None)
        with self.assertRaises(ActionError) as ctx:
            muto.set_camera({"attiva": True, "larghezza": 1280, "altezza": 720, "fps": 15})
        self.assertEqual(ctx.exception.code, 501)


class InstallKeyRetirementTest(unittest.TestCase):
    """La chiave di installazione è solo un ponte: dal primo abbinamento con chiave propria si ritira."""

    def setUp(self):
        from mirto_node.keys import INSTALL_RETIRE_DAYS, KeyStore

        self.days = INSTALL_RETIRE_DAYS
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.key = generate_key()
        (self.d / "chiave").write_text(self.key)
        self.now = [1_800_000_000.0]
        self.store = KeyStore(self.d, clock=lambda: self.now[0])

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_countdown_before_the_first_key_of_its_own(self):
        self.assertEqual(self.store.install_key_hex(), self.key)
        self.assertEqual(self.store.retire_in(), 0)

    def test_retires_by_itself_after_the_grace_days(self):
        self.store.create("Mirto su mac", "192.168.1.5")
        self.assertEqual(self.store.retire_in(), self.days * 86400)
        self.now[0] += (self.days - 1) * 86400
        self.assertEqual(self.store.install_key_hex(), self.key)
        self.now[0] += 2 * 86400
        self.assertEqual(self.store.install_key_hex(), "")
        self.assertFalse((self.d / "chiave").exists())
        self.assertTrue((self.d / "chiave-revocata").exists())
        self.assertEqual(len(self.store.keys()), 1, "resta solo la chiave del Mac abbinato")

    def test_the_node_notices_the_deadline_without_other_changes(self):
        self.store.create("Mirto su mac", "192.168.1.5")
        before = self.store.stamp()
        self.now[0] += (self.days + 1) * 86400
        self.assertNotEqual(self.store.stamp(), before, "il Verifier deve rileggere le chiavi alla scadenza")

    def test_revoke_by_client_name_or_file_name(self):
        self.store.create("Mirto su mac-di-marco", "192.168.1.5")
        self.now[0] += 60
        self.store.create("Mirto su portatile", "192.168.1.9")
        self.assertEqual(self.store.revoke("Mirto su portatile"), 1)
        self.assertEqual([e["cliente"] for e in self.store.entries()], ["Mirto su mac-di-marco"])
        self.assertEqual(self.store.revoke("mirto-su-mac-di-marco"), 1)
        self.assertEqual(self.store.entries(), [])
        self.assertEqual(self.store.revoke("non-esiste"), 0)
        self.assertEqual(self.store.keys(), [self.key], "la chiave di installazione è un'altra cosa")

    def test_revoke_all_takes_the_install_key_with_it(self):
        self.store.create("Mirto su mac", "192.168.1.5")
        self.assertEqual(self.store.revoke_all(), 2)
        self.assertEqual(self.store.keys(), [])
        self.assertFalse((self.d / "chiave").exists())
        self.assertEqual(self.store.first_claim(), 0)


class ReteDelNodoTest(unittest.TestCase):
    """Cavo o Wi-Fi: la prima domanda quando un nodo non risponde più.

    Chiesta da Marco il 22/10 — in *Sistema* non si vedeva come fosse attaccato il nodo — e serve
    soprattutto **prima** che smetta di rispondere: un Wi-Fi debole si vede, e il rimedio è spostare il
    nodo, non cambiare un'impostazione.

    Si legge dai file di sistema, non chiedendolo a un programma, perché questo finisce dentro lo stato
    del nodo e lo stato lo chiede Mirto a ogni battito: un comando che resta appeso qui farebbe
    sparire il nodo dalla finestra.
    """

    def finto_sys(self, schede: dict) -> Path:
        """``{"eth0": ("up", False), "wlan0": ("down", True)}`` → una finta /sys/class/net."""
        radice = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for nome, (stato, wireless) in schede.items():
            (radice / nome).mkdir()
            (radice / nome / "operstate").write_text(stato + "\n")
            if wireless:
                (radice / nome / "wireless").mkdir()
        return radice

    PROC = (
        "Inter-| sta-|   Quality        |   Discarded packets               | Missed | WE\n"
        " face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22\n"
        " wlan0: 0000   48.  -62.  -256        0      0      0      0      0        0\n"
    )

    def stato(self, schede, proc=None, nome="CasaMia"):
        from mirto_node.rete import stato_rete

        radice = self.finto_sys(schede)
        file_proc = radice / "wireless"
        (file_proc).write_text(self.PROC if proc is None else proc)
        return stato_rete(radice=radice, wireless=file_proc, nome_rete=lambda _scheda: nome)

    def test_the_cable_wins_and_the_wifi_stays_as_the_reserve(self):
        """Col cavo attaccato il traffico passa da lì: è la situazione di chi imposta il Wi-Fi dal pannello."""
        st = self.stato({"eth0": ("up", False), "wlan0": ("up", True)})
        self.assertEqual(st["tipo"], "cavo")
        self.assertEqual(st["scheda"], "eth0")
        self.assertEqual(st["riserva"], "wifi", "il Wi-Fi c'è: va detto, è la rete di riserva")
        self.assertIsNone(st["segnale"])

    def test_a_node_on_wifi_says_its_network_and_how_strong(self):
        st = self.stato({"eth0": ("down", False), "wlan0": ("up", True)})
        self.assertEqual((st["tipo"], st["scheda"], st["rete"]), ("wifi", "wlan0", "CasaMia"))
        self.assertEqual(st["segnale"], 69, "48 su 70 è il 69 %")
        self.assertEqual(st["riserva"], "")

    def test_a_cable_that_is_unplugged_does_not_count(self):
        """La scheda resta lì anche col cavo staccato: conta operstate, non l'esistenza."""
        st = self.stato({"eth0": ("down", False), "wlan0": ("down", True)})
        self.assertEqual(st["tipo"], "", "nessuna scheda su: succede davvero, mentre il Wi-Fi sale")
        self.assertEqual(st["rete"], "")

    def test_the_cards_that_are_not_the_home_network_are_ignored(self):
        """Docker, le VPN e il loopback non sono «come è attaccato il nodo»."""
        st = self.stato({"lo": ("up", False), "docker0": ("up", False), "wg0": ("up", False),
                         "veth9a2": ("up", False), "wlan0": ("up", True)})
        self.assertEqual((st["tipo"], st["scheda"]), ("wifi", "wlan0"))

    def test_the_signal_is_a_percentage_and_the_headers_are_skipped(self):
        from mirto_node.rete import segnale_da_proc

        self.assertEqual(segnale_da_proc(self.PROC), {"wlan0": 69})
        self.assertEqual(segnale_da_proc(""), {})
        self.assertEqual(segnale_da_proc("wlan0: 0000   70.  -40.  -256"), {"wlan0": 100})
        self.assertEqual(segnale_da_proc("wlan0: 0000   0.  -90.  -256"), {"wlan0": 0})
        self.assertEqual(segnale_da_proc("wlan0: 0000   ciao  -40."), {}, "una riga storta non è uno zero")

    def test_a_system_that_says_nothing_is_not_a_crash(self):
        from mirto_node.rete import stato_rete

        vuota = Path(self.enterContext(tempfile.TemporaryDirectory()))
        st = stato_rete(radice=vuota, wireless=vuota / "non-ci-sono", nome_rete=lambda _s: "")
        self.assertEqual(st["tipo"], "")
        st = stato_rete(radice=vuota / "nemmeno-questa", wireless=vuota / "no", nome_rete=lambda _s: "")
        self.assertEqual(st["tipo"], "")

    def test_the_name_of_the_network_is_asked_only_when_there_is_a_wifi_up(self):
        """È l'unico processo che si avvia qui: sul cavo non si deve avviare per niente."""
        from mirto_node.rete import stato_rete

        chiamate = []

        def chiesto(scheda):
            chiamate.append(scheda)
            return "CasaMia"

        radice = self.finto_sys({"eth0": ("up", False), "wlan0": ("up", True)})
        (radice / "wireless").write_text(self.PROC)
        stato_rete(radice=radice, wireless=radice / "wireless", nome_rete=chiesto)
        self.assertEqual(chiamate, [], "sul cavo il nome della rete Wi-Fi non serve a nessuno")

        radice2 = self.finto_sys({"wlan0": ("up", True)})
        (radice2 / "wireless").write_text(self.PROC)
        stato_rete(radice=radice2, wireless=radice2 / "wireless", nome_rete=chiesto)
        self.assertEqual(chiamate, ["wlan0"])

    def test_the_words_a_person_reads_in_the_stato_command(self):
        from mirto_node.rete import in_parole

        self.assertEqual(in_parole({"tipo": "cavo", "scheda": "eth0"}), "cavo (eth0)")
        self.assertEqual(in_parole({"tipo": "cavo", "scheda": "eth0", "riserva": "wifi"}),
                         "cavo (eth0) · Wi-Fi di riserva")
        self.assertEqual(in_parole({"tipo": "wifi", "scheda": "wlan0", "rete": "CasaMia", "segnale": 69}),
                         "Wi-Fi «CasaMia» · 69 % (wlan0)")
        debole = in_parole({"tipo": "wifi", "scheda": "wlan0", "rete": "CasaMia", "segnale": 21})
        self.assertIn("spostalo", debole, "il rimedio è un posto diverso, e va detto dove si legge")
        self.assertIn("nessuna scheda", in_parole({"tipo": ""}))

    def test_the_node_status_tells_mirto_how_it_is_attached(self):
        """Lo stato che Mirto legge deve portare il campo: senza, in Sistema non si può scrivere niente."""
        from unittest import mock

        from mirto_node import server as mod_server

        node = Node("Ingresso", "", {}, None, cameras=lambda: [], addresses=lambda: ["192.168.1.9"],
                    hostname=lambda: "mirto-node-17a9.local")
        finta = {"tipo": "wifi", "scheda": "wlan0", "rete": "CasaMia", "segnale": 69, "riserva": ""}
        with mock.patch.object(mod_server, "stato_rete", lambda *a, **k: finta):
            st = node.status()
        self.assertEqual(st["rete"], finta)


class MicrofonoEManopoleTest(unittest.TestCase):
    """``/configura/audio`` (azione «microfono») e ``/configura/manopole``: API 1.6.

    Nasce il 22/10, col primo microfono USB attaccato a un nodo. Due cose non si potevano fare da
    Mirto: **accendere il microfono** — esisteva, funzionava, e si abilitava solo scrivendo
    ``[microfono] attivo = true`` in ``node.toml`` con un terminale e la password del foglio — e
    **girare le manopole** della scheda. La seconda non è un vezzo: quel microfono nasce col guadagno
    a 0 su 16 e l'automatismo acceso, e in quello stato una voce arriva all'1% del fondo scala.
    """

    # Le schede del nodo il 22/10: un microfono USB che non suona, una cassa USB che non registra.
    SCHEDE = [{"n": 0, "id": "Headphones", "nome": "bcm2835 Headphones", "tipo": "jack",
               "suona": True, "registra": False},
              {"n": 1, "id": "Device", "nome": "USB PnP Sound Device", "tipo": "usb",
               "suona": False, "registra": True},
              {"n": 4, "id": "UACDemoV10", "nome": "UACDemoV1.0", "tipo": "usb",
               "suona": True, "registra": False}]

    MANOPOLE = {
        "Device": """Simple mixer control 'Mic',0
  Capabilities: cvolume cvolume-joined cswitch cswitch-joined
  Capture channels: Mono
  Limits: Capture 0 - 16
  Mono: Capture 0 [0%] [0.00dB] [on]
Simple mixer control 'Auto Gain Control',0
  Capabilities: pswitch pswitch-joined
  Playback channels: Mono
  Mono: Playback [on]
""",
        "UACDemoV10": """Simple mixer control 'PCM',0
  Capabilities: pvolume pswitch pswitch-joined
  Playback channels: Front Left - Front Right
  Limits: Playback 0 - 147
  Mono:
  Front Left: Playback 44 [30%] [-20.16dB] [on]
  Front Right: Playback 44 [30%] [-20.16dB] [on]
""",
    }

    def setUp(self):
        from mirto_node import mixer

        self.mixer = mixer
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.cert, cert_key = node_cert(self.d / "cert")
        self.key = generate_key()
        self.micro = {"attivo": False, "scheda": ""}
        self.girate = []
        import mirto_node.server as srv

        vere = srv.alsa_cards
        srv.alsa_cards = lambda *_a, **_k: self.SCHEDE
        self.addCleanup(lambda: setattr(srv, "alsa_cards", vere))

        class Res:
            def __init__(self, out="", code=0):
                self.stdout, self.stderr, self.returncode = out, "", code

        def run(argv, **_k):
            scheda = argv[2]
            if len(argv) == 3:
                return Res(self.MANOPOLE.get(scheda, ""), 0 if scheda in self.MANOPOLE else 1)
            self.girate.append(argv[4:])
            return Res()

        self.manopole = mixer.Manopole(cartella=self.d, run=run)
        self.node = Node("Nodo", self.key, {}, FakePlayer(), cameras=lambda: [], addresses=lambda: [],
                         hostname=lambda: "n.local", model=lambda: "Pi", credentials=lambda: None,
                         folder=self.d, audio_cfg=lambda: {"uscita": "jack", "scheda": "Headphones"},
                         set_audio_cfg=lambda _v: None,
                         mic_cfg=lambda: dict(self.micro), set_mic_cfg=self.scrivi_micro,
                         manopole=self.manopole)
        self.server = NodeServer(self.node, self.cert, cert_key, host="127.0.0.1", port=0).start()
        self.client = NodeClient(NodeConfig("nodo", "Nodo", f"https://127.0.0.1:{self.server.port}"),
                                 self.cert.read_text(), self.key)

    def tearDown(self):
        self.server.stop()
        self.tmp.cleanup()

    def scrivi_micro(self, valori: dict) -> None:
        self.micro = dict(valori)

    def biglietto(self) -> str:
        f = self.d / "audio-da-fare"
        return f.read_text().strip() if f.exists() else ""

    # --- il microfono ------------------------------------------------------
    def test_the_state_says_which_microphones_are_attached(self):
        st = self.client.audio_state()
        self.assertEqual([m["scheda"] for m in st["microfoni"]], ["Device"],
                         "solo le schede che registrano: una cassa non è un microfono")
        self.assertEqual(st["microfono"], {"attivo": False, "scheda": "", "acceso_ora": False,
                                           "si_puo_scegliere": True})

    def test_a_microphone_can_be_switched_on_from_mirto(self):
        res = self.client.set_microphone(True, "Device")
        self.assertEqual(self.micro, {"attivo": True, "scheda": "Device"})
        self.assertEqual(self.biglietto(), "applica", "l'ascolto parte all'avvio: il riavvio lo fa root")
        self.assertEqual(res["microfono"]["scheda"], "Device")

    def test_a_speaker_is_refused_as_a_microphone(self):
        """Accendere l'ascolto su una cassa vorrebbe dire un microfono che non sentirà mai niente."""
        with self.assertRaises(NodeError) as ctx:
            self.client.set_microphone(True, "UACDemoV10")
        self.assertIn("400", str(ctx.exception))
        self.assertEqual(self.micro, {"attivo": False, "scheda": ""}, "e non cambia niente")
        self.assertEqual(self.biglietto(), "")

    def test_a_microphone_that_is_not_there_is_refused(self):
        with self.assertRaises(NodeError):
            self.client.set_microphone(True, "Inventata")

    def test_switching_the_microphone_off_does_not_need_a_card(self):
        self.client.set_microphone(True, "Device")
        self.client.set_microphone(False)
        self.assertEqual(self.micro["attivo"], False)

    # --- le manopole -------------------------------------------------------
    def test_every_card_brings_its_real_knobs(self):
        schede = self.client.knobs()["schede"]
        self.assertEqual(sorted(schede), ["Device", "UACDemoV10"],
                         "le cuffie non rispondono ad amixer: non si inventa una scheda che non c'è")
        nomi = [m["nome"] for m in schede["Device"]["manopole"]]
        self.assertEqual(nomi, ["Mic", "Auto Gain Control"])
        pcm, = schede["UACDemoV10"]["manopole"]
        self.assertEqual((pcm["percento"], pcm["massimo"], pcm["acceso"]), (30, 147, True))

    def test_the_agc_is_offered_even_though_it_lies_about_its_direction(self):
        """Si dichiara manopola di riproduzione pur essendo l'automatismo del microfono.

        Se il pannello filtrasse per direzione, la manopola che conta non si vedrebbe: è il motivo per
        cui si danno tutte le manopole della scheda.
        """
        agc = self.client.knobs()["schede"]["Device"]["manopole"][1]
        self.assertEqual((agc["nome"], agc["direzione"], agc["volume"]),
                         ("Auto Gain Control", "riproduzione", False))

    def test_turning_a_knob_applies_it_now_and_remembers_it(self):
        res = self.client.set_knob("Device", "Mic,0", "80%")
        self.assertIn(["Mic,0", "80%"], self.girate)
        self.assertEqual(self.manopole.ricordate(), {"Device": {"Mic,0": "80%"}},
                         "ALSA al riavvio dimentica: se lo ricorda il nodo")
        self.assertEqual(res["esito_richiesta"], "ok")
        self.assertEqual(self.biglietto(), "", "niente root e niente riavvio: si applica subito")

    def test_a_knob_that_does_not_exist_is_refused(self):
        for scheda, manopola in (("Device", "Inventata,0"), ("Sparita", "Mic,0")):
            with self.assertRaises(NodeError) as ctx:
                self.client.set_knob(scheda, manopola, "80%")
            self.assertIn("400", str(ctx.exception))
        self.assertEqual(self.girate, [])

    def test_a_nonsense_value_is_refused_before_amixer(self):
        for brutto in ("200%", "boh", "on; rm -rf /", ""):
            with self.assertRaises(NodeError) as ctx:
                self.client.set_knob("Device", "Mic,0", brutto)
            self.assertIn("400", str(ctx.exception))
        self.assertEqual(self.girate, [], "niente arriva ad amixer")


class DispositiviAudioTest(unittest.TestCase):
    """``/configura/dispositivi``: cosa c'è attaccato, non «dove suona la sirena» (API 1.7).

    Il 22/10, guardando il pannello finito, è venuto fuori che il modello era sbagliato: il nodo aveva
    **una** uscita e la sirena suonava lì. Ma la sirena è un'**azione**, e una casa può volerla dalla
    cassa di fuori mentre un dissuasore suona da quella del giardino. Il nodo quindi tiene dei
    dispositivi; chi sceglie è l'azione. Qui c'è la prima metà: i dispositivi.
    """

    SCHEDE = [{"n": 0, "id": "Headphones", "nome": "bcm2835 Headphones", "tipo": "jack",
               "suona": True, "registra": False},
              {"n": 1, "id": "Device", "nome": "USB PnP Sound Device", "tipo": "usb",
               "suona": False, "registra": True},
              {"n": 2, "id": "vc4hdmi0", "nome": "vc4-hdmi-0", "tipo": "hdmi",
               "suona": True, "registra": False},
              {"n": 4, "id": "UACDemoV10", "nome": "UACDemoV1.0", "tipo": "usb",
               "suona": True, "registra": False}]

    def setUp(self):
        from mirto_node import mixer

        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.cert, cert_key = node_cert(self.d / "cert")
        self.key = generate_key()
        self.audio = {"uscita": "usb", "scheda": "UACDemoV10", "jack": True}
        self.schermo = False
        self.casse = [{"mac": "AA:BB:CC:DD:EE:FF", "nome": "JBL Flip 6", "collegata": True},
                      {"mac": "11:22:33:44:55:66", "nome": "Cassa giardino", "collegata": False}]
        import mirto_node.server as srv

        for nome, finta in (("alsa_cards", lambda *_a, **_k: self.SCHEDE),
                            ("casse_abbinate", lambda *_a, **_k: list(self.casse)),
                            ("schermo_attaccato", lambda *_a, **_k: self.schermo)):
            vera = getattr(srv, nome)
            setattr(srv, nome, finta)
            self.addCleanup(lambda n=nome, v=vera: setattr(srv, n, v))

        class Res:
            def __init__(self, out="", code=0):
                self.stdout, self.stderr, self.returncode = out, "", code

        manopole = mixer.Manopole(cartella=self.d, run=lambda argv, **_k: Res(
            "Simple mixer control 'PCM',0\n  Capabilities: pvolume\n  Limits: Playback 0 - 147\n"
            "  Mono: Playback 44 [30%] [-20.16dB] [on]\n" if len(argv) == 3 else "", 0))
        self.node = Node("Nodo", self.key, {}, FakePlayer(), cameras=lambda: [], addresses=lambda: [],
                         hostname=lambda: "n.local", model=lambda: "Pi", credentials=lambda: None,
                         folder=self.d, audio_cfg=lambda: dict(self.audio), set_audio_cfg=self.scrivi,
                         manopole=manopole)
        self.server = NodeServer(self.node, self.cert, cert_key, host="127.0.0.1", port=0).start()
        self.client = NodeClient(NodeConfig("nodo", "Nodo", f"https://127.0.0.1:{self.server.port}"),
                                 self.cert.read_text(), self.key)

    def tearDown(self):
        self.server.stop()
        self.tmp.cleanup()

    def scrivi(self, valori: dict) -> None:
        self.audio.update(valori)

    def biglietto(self) -> str:
        f = self.d / "audio-da-fare"
        return f.read_text().strip() if f.exists() else ""

    def test_the_devices_are_grouped_as_the_panel_shows_them(self):
        st = self.client.devices()
        self.assertEqual([u["scheda"] for u in st["usb"]], ["UACDemoV10"],
                         "il microfono USB non è una cassa: non sta fra i dispositivi che suonano")
        self.assertEqual(st["jack"]["presente"], True)
        self.assertEqual(st["jack"]["scheda"], "Headphones")
        self.assertEqual([c["mac"] for c in st["bluetooth"]["casse"]],
                         ["AA:BB:CC:DD:EE:FF", "11:22:33:44:55:66"], "più di una, e le ricorda BlueZ")
        self.assertTrue(st["usb"][0]["manopole"], "ogni dispositivo porta le sue manopole")

    def test_hdmi_shows_up_only_with_a_screen_attached(self):
        """Un Raspberry dichiara due uscite HDMI sempre: proporle a chi non ha una TV è rumore."""
        self.assertEqual(self.client.devices()["hdmi"], [])
        self.schermo = True
        self.assertEqual([h["scheda"] for h in self.client.devices()["hdmi"]], ["vc4hdmi0"])

    def test_hdmi_does_not_vanish_from_under_whoever_chose_it(self):
        self.audio = {"uscita": "hdmi", "scheda": "vc4hdmi0"}
        self.assertEqual([h["scheda"] for h in self.client.devices()["hdmi"]], ["vc4hdmi0"])

    def test_the_jack_can_be_switched_off(self):
        res = self.client.set_jack(False)
        self.assertEqual(self.audio["jack"], False)
        self.assertEqual(res["jack"]["attivo"], False)
        self.assertEqual(self.biglietto(), "applica")

    def test_forgetting_a_speaker_is_a_job_for_root(self):
        res = self.client.forget_speaker("aa:bb:cc:dd:ee:ff")
        self.assertEqual(res["esito"], "avviato")
        self.assertEqual(self.biglietto(), "dimentica AA:BB:CC:DD:EE:FF")

    def test_a_nonsense_address_never_reaches_root(self):
        for brutto in ("", "AA:BB", "non un mac", "AA:BB:CC:DD:EE:FF; rm -rf /"):
            with self.assertRaises(NodeError):
                self.client.forget_speaker(brutto)
        self.assertEqual(self.biglietto(), "")

    def test_a_card_that_cannot_play_is_refused_as_an_output(self):
        """Il microfono USB era proponibile come uscita della sirena, e il nodo lo accettava."""
        with self.assertRaises(NodeError) as ctx:
            self.client.set_audio("usb", scheda="Device")
        self.assertIn("400", str(ctx.exception))


class UnaCassaEUnaLineaTest(unittest.TestCase):
    """Tappa 4 (API 1.11): piu' casse suonano insieme, la stessa cassa no — e il nodo lo **dice**.

    Fino al 24/10 il nodo aveva un player solo. Due suoni insieme erano una bugia, non un limite:
    `loop()` comincia con `self.stop()`, quindi il secondo ammazzava il primo mentre il nodo
    rispondeva «ok» a tutt'e due e metteva due timer. Con due uscite diverse era peggio ancora —
    l'uscita del player cambiava sotto i piedi del suono che stava andando.

    Adesso: un player per cassa (`AudioPlayer.per`), e chi chiede una cassa occupata si sente dire
    no. Il caso del cancello — suono dalla bluetooth **e** luce — non passa da qui: luce e suono sono
    due dispositivi distinti e andavano insieme anche prima (provato in `SuonoELuceInsiemeTest`).
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.player = FakePlayer()
        suoni = {"sparo": b"\0" * 8000, "corvi": b"\0" * 8000}
        self.node = Node("N", generate_key(),
                         {"sirena": SirenDevice(self.player, 80),
                          "dissuasore": DeterrentDevice(self.player, suoni),
                          "generato": GeneratedAudioDevice(self.player, suoni)}, self.player,
                         cameras=lambda: [], addresses=lambda: [], hostname=lambda: "n")
        self.d = d

    def tearDown(self):
        self.tmp.cleanup()

    def test_two_sounds_on_the_same_speaker_the_second_is_refused(self):
        self.node.activate("dissuasore", {"durata": 10, "suono": "sparo"})
        with self.assertRaises(ActionError) as e:
            self.node.activate("generato", {"durata": 10, "suono": "corvi"})
        self.assertIn("dissuasore", str(e.exception), "chi occupa la cassa si chiama per nome")
        self.assertEqual(len(self.player.loops), 1, "un suono solo e' partito, ed e' il primo")
        self.assertNotIn("generato", self.node.until, "rifiutata vuol dire che non risulta attiva")

    def test_two_different_speakers_play_together(self):
        self.node.activate("dissuasore", {"durata": 10, "suono": "sparo",
                                          "uscita": "bluetooth:AA:BB:CC:DD:EE:FF"})
        self.node.activate("generato", {"durata": 10, "suono": "corvi", "uscita": "usb:UACDemoV10"})
        self.assertEqual(sorted(self.node.until), ["dissuasore", "generato"])
        sorelle = self.player.sorelle()
        self.assertEqual(len(sorelle), 1, "una cassa in piu' chiesta, una sorella in piu'")
        self.assertEqual(len(self.player.loops), 1, "la bluetooth e' quella configurata: suona lei")
        self.assertEqual([u for _n, _s, u in sorelle[0].loops], ["usb:UACDemoV10"],
                         "la USB suona sulla sua linea, non su quella della bluetooth")

    def test_the_same_speaker_said_in_two_ways_is_one_line(self):
        """La cassa configurata e la stessa cassa nominata sono la stessa linea."""
        self.node.activate("dissuasore", {"durata": 10, "suono": "sparo"})   # senza uscita
        with self.assertRaises(ActionError):
            self.node.activate("generato", {"durata": 10, "suono": "corvi",
                                            "uscita": "bluetooth:aa:bb:cc:dd:ee:ff"})

    def test_the_speaker_is_free_again_when_the_action_ends(self):
        self.node.activate("dissuasore", {"durata": 10, "suono": "sparo"})
        self.node.deactivate("dissuasore")
        self.node.activate("generato", {"durata": 10, "suono": "corvi"})
        self.assertIn("generato", self.node.until, "finita quella, la cassa torna libera")

    def test_stop_stops_the_speaker_it_started_on(self):
        """«Ferma» fermava l'uscita configurata, e lasciava suonare quella del portico."""
        self.node.activate("dissuasore", {"durata": 10, "suono": "sparo", "uscita": "usb:UACDemoV10"})
        sorella = self.player.sorelle()[0]
        self.node.deactivate("dissuasore")
        self.assertEqual(sorella.stops, 1, "si ferma la cassa su cui era partita")

    def test_the_version_is_the_only_place_that_says_it(self):
        """Che il nodo sappia suonare da piu' casse insieme lo dice la versione dell'API, e basta lei.

        Un campo in piu' («casse_insieme») l'avevo scritto e poi togliuto: diceva la stessa cosa di
        `versione_api`, e un doppione e' una cosa che prima o poi non si aggiorna — a quel punto i due
        si contraddicono e nessuno sa quale credere. Mirto la versione la guarda gia' di suo, ed e'
        la stessa che si aspetta.
        """
        from mirto.actions.diagnosi import API_ATTESA

        self.assertNotIn("casse_insieme", self.node.info()["audio"])
        self.assertEqual(self.node.info()["versione_api"], API_ATTESA,
                         "il nodo dichiara quello che Mirto si aspetta: si muovono insieme")


class SuonoELuceInsiemeTest(unittest.TestCase):
    """Il caso del cancello su un nodo solo: suono **e** luce, nello stesso istante.

    Questa prova non difende codice nuovo: difende una cosa che funzionava gia' il 24/10 e che ho
    rischiato di riscrivere senza accorgermene. Luce e suono sono due dispositivi distinti, con due
    timer loro, e il lock del nodo si tiene solo per far partire — non per la durata. Senza questa
    prova, la regola nuova della cassa poteva diventare «una operazione per volta» e nessuno se ne
    sarebbe accorto fino a provarlo in giardino.
    """

    def test_a_sound_and_a_light_are_both_on(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        led = Path(tmp.name) / "led"
        led.mkdir()
        (led / "trigger").write_text("none timer [mmc0]")
        p = FakePlayer()
        node = Node("N", generate_key(), {"dissuasore": DeterrentDevice(p, {"sparo": b"\0" * 8000}),
                                          "luce": LedDevice(led)}, p,
                    cameras=lambda: [], addresses=lambda: [], hostname=lambda: "n")
        node.activate("dissuasore", {"durata": 10, "suono": "sparo"})
        node.activate("luce", {"durata": 10})
        self.assertEqual(sorted(node.until), ["dissuasore", "luce"],
                         "il suono e la luce stanno insieme: non e' la cassa a essere contesa")


class UnPlayerPerCassaTest(unittest.TestCase):
    """`AudioPlayer.per()`: la cassa che si chiede decide quale player suona.

    Un player ha una linea sola — `_proc`, `_thread`, `in_uso`, `ripiego` sono di **quel** suono — e
    due casse sono due linee, non una da dividere in due. Per questo la cassa in piu' e' un player in
    piu' e non un pezzo di stato in piu' dentro quello che c'era.
    """

    def uno(self, **kw):
        from mirto_node import audio as a

        return a.AudioPlayer(output=kw.pop("output", "bluetooth"), bt_mac=kw.pop("bt_mac", "AA:BB"),
                             popen=lambda *_a, **_k: None, run=lambda *_a, **_k: None,
                             cards=lambda *_a, **_k: [], **kw)

    def test_the_configured_speaker_is_this_player(self):
        p = self.uno()
        self.assertIs(p.per(), p, "senza chiave vuol dire «quella configurata»")
        self.assertIs(p.per("bluetooth:aa:bb"), p, "la stessa cassa scritta in minuscolo e' la stessa")
        self.assertEqual(p.sorelle(), [], "nessuna cassa in piu' chiesta, nessuna sorella")

    def test_another_speaker_is_a_sister_born_once(self):
        p = self.uno()
        prima = p.per("usb:UACDemoV10")
        self.assertIsNot(prima, p)
        self.assertIs(p.per("usb:UACDemoV10"), prima, "la sorella nasce una volta, poi si ritrova")
        self.assertEqual(prima.output, "usb")
        self.assertEqual(prima.card, "UACDemoV10", "nasce gia' configurata su quella cassa")
        self.assertEqual(prima.keep_alive, 0.0, "la sveglia e' della cassa configurata, non delle altre")

    def test_a_sister_asks_her_mother(self):
        """Una sorella non fa sorelle: se no due strade diverse darebbero due player per una cassa."""
        p = self.uno()
        usb = p.per("usb:X")
        self.assertIs(usb.per("usb:X"), usb)
        self.assertIs(usb.per(), p, "«quella configurata» e' sempre la sua")
        self.assertIs(usb.per("hdmi:Y"), p.per("hdmi:Y"))
        self.assertEqual(len(p.sorelle()), 2)

    def test_a_key_nobody_understands_does_not_make_a_sister(self):
        """Resta a questo player, che poi dira' «uscita non riconosciuta»: un errore solo, dove va."""
        p = self.uno()
        self.assertIs(p.per("pippo:x"), p)
        self.assertEqual(p.sorelle(), [])

    def test_closing_closes_the_other_speakers_too(self):
        p = self.uno()
        usb = p.per("usb:X")
        p.close()
        self.assertTrue(usb._ka_stop.is_set(), "spegnere il nodo spegne tutte le casse")

    def test_the_name_of_a_speaker_is_one_name(self):
        from mirto_node.audio import quale_cassa

        self.assertEqual(quale_cassa("", "bluetooth", "", "aa:bb"), "bluetooth:AA:BB",
                         "vuota vuol dire quella configurata")
        self.assertEqual(quale_cassa("bluetooth:AA:BB"), quale_cassa("bluetooth:aa:bb"))
        self.assertEqual(quale_cassa("usb:X"), "usb:X")
        self.assertNotEqual(quale_cassa("usb:X"), quale_cassa("usb:Y"))

    def test_the_same_name_as_mirto_uses(self):
        """Un posto solo dove si decide come si chiama una cassa: due nomi diventano due bug."""
        from mirto.actions.pannello_audio import chiave_uscita
        from mirto_node.audio import quale_cassa

        for uscita, scheda, mac in (("usb", "UACDemoV10", ""), ("bluetooth", "", "AA:BB:CC:DD:EE:FF"),
                                    ("jack", "Headphones", ""), ("hdmi", "vc4hdmi0", "")):
            with self.subTest(uscita=uscita):
                self.assertEqual(quale_cassa(chiave_uscita(uscita, scheda, mac)),
                                 chiave_uscita(uscita, scheda, mac))


class IlNodoSiAggiornaDaSoloTest(unittest.TestCase):
    """N-02: il codice del nodo arriva da Mirto, e il nodo lo installa senza che nessuno apra un terminale.

    **Perché lo manda Mirto** invece di scaricarlo il nodo: la fiducia fra i due esiste gia' e non e'
    da inventare. L'abbinamento ha lasciato una chiave, ogni richiesta e' firmata, e la firma copre
    l'**impronta del corpo** (`signing._canonical`): questi byte arrivano da chi ha la chiave di questo
    nodo e da nessun altro. Scaricarlo da internet vorrebbe dire una chiave di rilascio da creare e
    proteggere — la stessa decisione sospesa di W-05 — piu' internet sul nodo.

    Qui si prova la meta' senza privilegi: verificare, scompattare dove il servizio puo' scrivere, e
    lasciare il biglietto. A installare ci pensa `boot-segnali.sh`, che e' di root.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.node = Node("N", generate_key(), {}, None, cameras=lambda: [], addresses=lambda: [],
                         hostname=lambda: "n", folder=self.d)

    def tearDown(self):
        self.tmp.cleanup()

    def pacchetto(self, file=None, dentro=True):
        """Un .tar.gz come quello che manda Mirto: il codice del nodo, compresso."""
        import base64
        import hashlib
        import io
        import tarfile

        file = file or {"install.sh": b"#!/bin/sh\nexit 0\n",
                        "mirto_node/__init__.py": b'__version__ = "9.9.9"\n'}
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for nome, dati in file.items():
                info = tarfile.TarInfo(nome)
                info.size = len(dati)
                tar.addfile(info, io.BytesIO(dati))
        crudo = buf.getvalue()
        return crudo if not dentro else {
            "versione": "9.9.9", "sha256": hashlib.sha256(crudo).hexdigest(),
            "pacchetto": base64.b64encode(crudo).decode()}

    def test_the_package_is_unpacked_and_root_is_asked_to_install_it(self):
        esito = self.node.aggiorna(self.pacchetto())
        self.assertTrue(esito["in_corso"])
        self.assertEqual(esito["versione"], "9.9.9")
        self.assertTrue((self.d / "nuovo" / "install.sh").is_file())
        self.assertTrue((self.d / "nuovo" / "mirto_node" / "__init__.py").is_file())
        biglietto = (self.d / "pacchetto-da-installare").read_text(encoding="utf-8")
        self.assertIn("9.9.9", biglietto, "root deve sapere che versione sta installando")

    def test_the_answer_comes_before_the_install(self):
        """`install.sh` riavvia questo servizio: una risposta «ho finito» non arriverebbe mai.

        Quindi la risposta dice «in corso», e com'e' andata si legge dallo stato — dove Mirto
        guarda comunque subito dopo.
        """
        self.assertTrue(self.node.aggiorna(self.pacchetto())["in_corso"])
        self.assertFalse((self.d / "pacchetto-esito").exists(), "l'esito lo scrive root, non noi")

    def test_a_package_that_changed_on_the_way_is_refused(self):
        dati = self.pacchetto()
        dati["sha256"] = "0" * 64
        with self.assertRaises(ActionError) as caso:
            self.node.aggiorna(dati)
        self.assertEqual(caso.exception.code, 400)
        self.assertFalse((self.d / "pacchetto-da-installare").exists(), "e non si chiede niente a root")

    def test_something_that_is_not_a_node_is_refused(self):
        """Un archivio qualsiasi non si installa: prima si guarda se dentro c'e' un nodo."""
        with self.assertRaises(ActionError):
            self.node.aggiorna(self.pacchetto({"foto.jpg": b"\xff\xd8"}))
        self.assertFalse((self.d / "pacchetto-da-installare").exists())

    def test_a_package_that_writes_outside_is_refused_by_name(self):
        """Un archivio e' un elenco di nomi scelti da chi l'ha fatto, e puo' contenere `../`.

        Si rifiuta **per nome**, prima di scrivere un byte: dopo sarebbe tardi.
        """
        with self.assertRaises(ActionError):
            self.node.aggiorna(self.pacchetto({"../../fuori.txt": b"x",
                                               "install.sh": b"#!/bin/sh\n",
                                               "mirto_node/__init__.py": b"x"}))
        self.assertFalse((self.d.parent / "fuori.txt").exists(), "non deve essere uscito niente")
        self.assertFalse((self.d / "pacchetto-da-installare").exists())

    def test_an_empty_package_is_refused(self):
        with self.assertRaises(ActionError):
            self.node.aggiorna({"versione": "9.9.9", "pacchetto": ""})

    def test_the_old_files_do_not_survive_a_new_package(self):
        """Si svuota invece di sovrascrivere: se no un file tolto nella versione nuova resta li',
        e quello che si installa non e' ne' il vecchio ne' il nuovo."""
        self.node.aggiorna(self.pacchetto({"install.sh": b"#!/bin/sh\n",
                                           "mirto_node/__init__.py": b"x",
                                           "vecchio.py": b"x"}))
        self.assertTrue((self.d / "nuovo" / "vecchio.py").exists())
        self.node.aggiorna(self.pacchetto())
        self.assertFalse((self.d / "nuovo" / "vecchio.py").exists())

    def test_how_it_went_is_told_in_the_state(self):
        """Mirto lo stato lo chiede gia': l'esito lo trova dove sta gia' guardando."""
        (self.d / "pacchetto-esito").write_text("ok\n0.12.0\ninstallato\n", encoding="utf-8")
        st = self.node.status()
        self.assertEqual(st["aggiornamento"]["esito"], "ok")
        self.assertEqual(st["aggiornamento"]["versione"], "0.12.0")

    def test_no_update_no_line(self):
        self.assertNotIn("aggiornamento", self.node.status(),
                         "un nodo che non e' mai stato aggiornato non ha niente da dire")


class IlPacchettoVeroArrivaAlNodoTest(unittest.TestCase):
    """Il pacchetto che Mirto costruisce davvero, dato in pasto al nodo vero. Andata e ritorno.

    Le due meta' hanno due prove loro — qui si prova il **contratto**, che e' il posto dove due meta'
    provate separatamente si scoprono incompatibili: un nome di campo diverso, un permesso che si
    perde, una cartella che non c'e'. Costa un secondo e copre la cosa che ci sta piu' a cuore.
    """

    def test_what_mirto_builds_is_what_the_node_accepts(self):
        import base64

        from mirto.actions.aggiorna_nodo import fai_pacchetto, versione_che_abbiamo

        crudo, versione, sha = fai_pacchetto()
        self.assertEqual(versione, versione_che_abbiamo())
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        nodo = Node("N", generate_key(), {}, None, cameras=lambda: [], addresses=lambda: [],
                    hostname=lambda: "n", folder=d)
        esito = nodo.aggiorna({"versione": versione, "sha256": sha,
                               "pacchetto": base64.b64encode(crudo).decode()})
        self.assertTrue(esito["in_corso"])
        self.assertTrue((d / "nuovo" / "install.sh").is_file())
        self.assertTrue((d / "nuovo" / "mirto_node" / "server.py").is_file())
        self.assertTrue((d / "nuovo" / "boot-segnali.sh").is_file())

    def test_the_scripts_keep_the_bit_that_lets_root_run_them(self):
        """`install.sh` lo lancia root: un file arrivato senza il bit di esecuzione e' un
        aggiornamento che fallisce a meta', ed e' il genere di cosa che si scopre sul Raspberry.

        Il bit si guarda **dentro il tar**, perche' quello e' il bit che arriva sul Raspberry e
        l'unico che esista su tutti i sistemi: Windows non ha un bit di esecuzione, quindi un file
        scompattato la' risponde sempre «no» e la prova era rossa per un motivo che non c'entrava
        niente con quello che voleva controllare (8/11, i due lavori Windows della CI). Il controllo
        sul disco resta, dove il disco ha i permessi: e' quello che prova che lo scompattatore del
        nodo non li butti via per strada (il filtro `data` di `tarfile` tiene il bit solo se c'e').
        """
        import base64
        import io
        import os
        import stat
        import tarfile

        from mirto.actions.aggiorna_nodo import fai_pacchetto

        crudo, versione, sha = fai_pacchetto()
        with tarfile.open(fileobj=io.BytesIO(crudo)) as tar:
            modi = {m.name: m.mode for m in tar.getmembers()}
        self.assertTrue(modi["install.sh"] & stat.S_IXUSR,
                        "nel pacchetto install.sh deve essere eseguibile")
        self.assertTrue(modi["boot-segnali.sh"] & stat.S_IXUSR,
                        "nel pacchetto boot-segnali.sh deve essere eseguibile")
        self.assertFalse(modi["README.md"] & stat.S_IXUSR,
                         "un file che nessuno lancia non si manda eseguibile")

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        nodo = Node("N", generate_key(), {}, None, cameras=lambda: [], addresses=lambda: [],
                    hostname=lambda: "n", folder=d)
        nodo.aggiorna({"versione": versione, "sha256": sha,
                       "pacchetto": base64.b64encode(crudo).decode()})
        self.assertTrue((d / "nuovo" / "install.sh").is_file())
        if os.name == "posix":
            modo = os.stat(d / "nuovo" / "install.sh").st_mode
            self.assertTrue(modo & stat.S_IXUSR, "install.sh deve restare eseguibile")

    def test_the_package_carries_only_what_the_node_needs(self):
        """Un pacchetto e' anche quello che qualcuno leggera' per sapere cosa gira sul suo Raspberry."""
        import io
        import tarfile

        from mirto.actions.aggiorna_nodo import fai_pacchetto

        crudo, _v, _s = fai_pacchetto()
        with tarfile.open(fileobj=io.BytesIO(crudo)) as tar:
            nomi = tar.getnames()
        self.assertIn("install.sh", nomi)
        self.assertIn("mirto_node/server.py", nomi)
        self.assertEqual([n for n in nomi if "__pycache__" in n or n.endswith(".pyc")], [],
                         "niente file compilati: pesano e non servono")

    def test_the_same_code_gives_the_same_fingerprint(self):
        """Date e proprietari azzerati: due computer che mandano lo stesso codice fanno la stessa
        impronta, e l'impronta diventa un modo per dire «questi due nodi hanno la stessa roba»."""
        import io
        import tarfile

        from mirto.actions.aggiorna_nodo import fai_pacchetto

        def dentro(crudo):
            with tarfile.open(fileobj=io.BytesIO(crudo)) as tar:
                return {m.name: m.size for m in tar.getmembers()}

        primo, secondo = fai_pacchetto()[0], fai_pacchetto()[0]
        # La data dentro l'intestazione gzip **deve** essere zero. E' la prova vera di questa
        # promessa: finche' il tar si scriveva con `w:gz`, gzip ci metteva l'ora di adesso, e due
        # pacchetti dallo stesso codice fatti in due secondi diversi avevano impronte diverse. Si
        # vedeva solo come capriccio — rossa una volta nella suite, verde da sola — perche' due
        # chiamate di fila cadono quasi sempre nello stesso secondo. Controllare i byte uguali non
        # bastava: diceva «a volte». Questo campo dice sempre.
        self.assertEqual(int.from_bytes(primo[4:8], "little"), 0,
                         "l'intestazione gzip porta l'ora: l'impronta cambierebbe ogni secondo")
        if primo != secondo:
            # Un'uguaglianza che fallisce senza dire *cosa* e' cambiato costringe a indovinare, e il
            # giro dopo e' di nuovo verde: la differenza va stampata adesso, perche' una seconda
            # occasione potrebbe non esserci.
            a, b = dentro(primo), dentro(secondo)
            diverso = [f"{n}: {a.get(n, '—')} → {b.get(n, '—')}"
                       for n in sorted(set(a) | set(b)) if a.get(n) != b.get(n)]
            self.fail("due pacchetti dallo stesso codice sono diversi:\n  "
                      + ("\n  ".join(diverso) or "stessi nomi e stesse dimensioni: byte diversi"))


class BudgetEPrankTest(unittest.TestCase):
    """Il budget dei dissuasori: quando finisce lo dice **e dice quando riprende**; la modalita'
    Prank lo sospende senza togliere le altre garanzie.

    Marco, 25/10: *«i messaggi sui budget di utilizzo dei dissuasori sonori deve essere chiaro»* e
    *«una modalita' Prank che permetta un uso piu' goliardico senza budget dei suoni»*. Le due cose
    stanno in una prova sola perche' sono due facce dello stesso conto: il registro degli scatti
    dell'ultima ora.
    """

    def setUp(self):
        self.now = [1000.0]
        self.player = FakePlayer()
        self.node = Node("N", generate_key(),
                         {"dissuasore": DeterrentDevice(self.player, {"sparo": b"\0" * 8000})},
                         self.player, clock=lambda: self.now[0],
                         cameras=lambda: [], addresses=lambda: [], hostname=lambda: "n")

    def esaurisci(self):
        """Spende tutto il budget dell'ora, trenta secondi per volta."""
        for _ in range(int(DETERRENT_BUDGET_SECONDS // DETERRENT_MAX_SECONDS)):
            self.node.activate("dissuasore", {"durata": DETERRENT_MAX_SECONDS})
            self.now[0] += DETERRENT_MAX_SECONDS + 1
            self.node.deactivate("dissuasore")

    def test_the_refusal_says_when_it_starts_again(self):
        self.esaurisci()
        with self.assertRaises(ActionError) as e:
            self.node.activate("dissuasore", {"durata": 5})
        self.assertIn("riprende", str(e.exception), "non «limite raggiunto»: quando riprende")
        dati = e.exception.dati
        self.assertEqual(dati["motivo"], "budget_dissuasore")
        # Il primo scatto e' uscito dalla finestra un'ora dopo essere partito: da li' si rilibera.
        atteso = 1000.0 + 3600 - self.now[0]
        self.assertAlmostEqual(dati["riprende_fra_s"], round(atteso), delta=2)
        self.assertEqual(dati["budget_s"], DETERRENT_BUDGET_SECONDS)

    def test_the_wait_is_a_number_of_minutes_not_of_seconds(self):
        self.assertEqual(fra_quanto(30), "fra meno di un minuto")
        self.assertEqual(fra_quanto(60), "fra 1 minuto")
        self.assertEqual(fra_quanto(743), "fra 12 minuti")
        self.assertEqual(fra_quanto(7200), "fra 2 ore")

    def test_when_the_window_passes_it_really_starts_again(self):
        self.esaurisci()
        self.now[0] += 3600
        self.node.activate("dissuasore", {"durata": 5})
        self.assertIn("dissuasore", self.node.until, "passata l'ora, suona di nuovo")

    def test_prank_suspends_the_budget_but_not_the_cap(self):
        self.esaurisci()
        self.node.set_prank({"minuti": 10})
        self.node.activate("dissuasore", {"durata": 300})
        self.assertAlmostEqual(self.node.until["dissuasore"] - self.now[0], DETERRENT_MAX_SECONDS,
                               delta=0.5, msg="senza budget restano i 30 secondi per scatto")

    def test_prank_does_not_leave_an_hour_of_silence_behind_it(self):
        """Quello che suona con la modalita' accesa **non si segna**: quando scade il conto riparte
        da zero. Se si segnasse, una modalita' che si spegne da se' lascerebbe un'ora di silenzio
        che nessuno ha chiesto."""
        self.node.set_prank({"minuti": 1})
        for _ in range(8):
            self.node.activate("dissuasore", {"durata": DETERRENT_MAX_SECONDS})
            self.now[0] += 1
            self.node.deactivate("dissuasore")
        self.now[0] += 61        # la modalita' e' scaduta da se'
        self.assertFalse(self.node.prank_resta())
        self.node.activate("dissuasore", {"durata": DETERRENT_MAX_SECONDS})
        self.assertIn("dissuasore", self.node.until, "il budget e' intero")

    def test_prank_expires_by_itself(self):
        self.node.set_prank({"minuti": 5})
        self.assertTrue(self.node.prank_state()["attiva"])
        self.now[0] += 5 * 60 + 1
        self.assertFalse(self.node.prank_state()["attiva"], "scade da se', senza che nessuno la spenga")
        self.esaurisci()
        with self.assertRaises(ActionError):
            self.node.activate("dissuasore", {"durata": 5})

    def test_prank_cannot_be_asked_for_longer_than_the_cap(self):
        self.node.set_prank({"minuti": 10000})
        self.assertLessEqual(self.node.prank_state()["restano_s"], PRANK_MAX_MINUTI * 60)

    def test_stop_all_turns_prank_off(self):
        self.node.set_prank({"minuti": 60})
        esito = self.node.stop_all()
        self.assertTrue(esito["prank_spenta"])
        self.assertFalse(self.node.prank_state()["attiva"],
                         "«Ferma tutto» non puo' lasciare accesa la modalita' che toglie il budget")

    def test_zero_minutes_turns_it_off(self):
        self.node.set_prank({"minuti": 30})
        self.node.set_prank({"minuti": 0})
        self.assertFalse(self.node.prank_state()["attiva"])

    def test_the_status_says_it_even_when_it_is_off(self):
        st = self.node.status()
        self.assertIn("prank", st, "e' cosi' che Mirto sa che questo nodo la sa fare")
        self.assertFalse(st["prank"]["attiva"])
        self.assertEqual(st["dissuasore_riprende_fra_s"], 0, "niente speso, niente da aspettare")
        self.esaurisci()
        self.assertGreater(self.node.status()["dissuasore_riprende_fra_s"], 0)

    def test_a_node_without_deterrents_does_not_offer_the_mode(self):
        p = FakePlayer()
        nudo = Node("N", generate_key(), {"sirena": SirenDevice(p, 80)}, p,
                    cameras=lambda: [], addresses=lambda: [], hostname=lambda: "n")
        self.assertNotIn("prank", nudo.status(), "niente dissuasori, niente modalita' da offrire")


class IlMicrofonoNelloStatoTest(unittest.TestCase):
    """Il microfono nello stato periodico (API 1.13).

    Viveva solo in `GET /configura/audio`, che Mirto chiede **aprendo** il pannello dell'audio: la
    riga del microfono in *Sistema* cercava quel dato nello stato e non lo trovava mai, e la
    condizione sembrava giusta leggendola (25/10). Un dato che tre schermate vogliono va dove si
    guarda sempre.
    """

    def nodo(self, mic_cfg=None, mic=None):
        p = FakePlayer()
        return Node("N", generate_key(), {}, p, mic=mic, mic_cfg=mic_cfg,
                    cameras=lambda: [], addresses=lambda: [], hostname=lambda: "n")

    def test_the_status_says_whether_there_is_a_microphone(self):
        st = self.nodo(mic_cfg=lambda: {"attivo": True, "scheda": "USB"},
                       mic=object()).status()["microfono"]
        self.assertEqual(st, {"attivo": True, "scheda": "USB", "acceso_ora": True})

    def test_configured_but_not_working_is_not_the_same_as_absent(self):
        """Appiattirle vorrebbe dire «nessun microfono» a chi ne ha uno attaccato che non funziona."""
        st = self.nodo(mic_cfg=lambda: {"attivo": True, "scheda": "USB"}).status()["microfono"]
        self.assertTrue(st["attivo"])
        self.assertFalse(st["acceso_ora"])

    def test_an_unreadable_config_does_not_kill_the_status(self):
        def rotto():
            raise OSError("node.toml illeggibile")

        st = self.nodo(mic_cfg=rotto).status()["microfono"]
        self.assertEqual(st["attivo"], False, "non si sa: si dice no, non si muore")

    def test_the_audio_panel_and_the_status_answer_the_same_way(self):
        """Due risposte alla stessa domanda prima o poi si contraddicono: `mic_state()` e' una."""
        n = self.nodo(mic_cfg=lambda: {"attivo": True, "scheda": "USB"}, mic=object())
        dal_pannello = n.audio_state()["microfono"]
        dallo_stato = n.status()["microfono"]
        for chiave in dallo_stato:
            self.assertEqual(dal_pannello[chiave], dallo_stato[chiave], chiave)


class IlVolumeDellaCassaTest(unittest.TestCase):
    """`GET /stato` porta il volume della cassa in uso e `POST /volume` lo cambia (API 1.13).

    Marco, 25/10: *«servirebbe avere sotto la cassa il livello del volume modificabile al volo»*. Per
    cambiarlo al volo bisogna sapere com'e' adesso, e quel dato stava solo in
    `GET /configura/manopole` — una risposta che gira `amixer` su **ogni** scheda del nodo, troppo per
    una cosa chiesta ogni due secondi.
    """

    class FinteManopole:
        def __init__(self, manopole, accetta=True):
            self._manopole, self.accetta, self.girate = manopole, accetta, []

        def leggi(self, scheda):
            return list(self._manopole.get(scheda, []))

        def gira(self, scheda, manopola, valore, ricorda=True):
            self.girate.append((scheda, manopola, valore))
            if self.accetta:
                for m in self._manopole.get(scheda, []):
                    if f"{m['nome']},{m['indice']}" == manopola and m.get("volume"):
                        m["volume"]["percento"] = int(str(valore).rstrip("%"))
            return self.accetta

    def nodo(self, uscita="usb", scheda="UACDemoV10", manopole=None, accetta=True):
        p = FakePlayer(output=uscita, card=scheda)
        m = self.FinteManopole(manopole if manopole is not None else
                               {scheda: [{"nome": "PCM", "indice": 0,
                                          "volume": {"percento": 60, "minimo": 0, "massimo": 100}}]},
                               accetta=accetta)
        self.schede = m
        return Node("N", generate_key(), {}, p, manopole=m,
                    cameras=lambda: [], addresses=lambda: [], hostname=lambda: "n")

    def test_the_status_carries_the_volume_of_the_speaker_in_use(self):
        st = self.nodo().status()["volume"]
        self.assertTrue(st["si_puo"])
        self.assertEqual(st["percento"], 60)
        self.assertEqual(st["manopola"], "PCM,0", "quale manopola sia lo sa il nodo, non chi chiama")

    def test_it_is_not_read_again_at_every_poll(self):
        """`amixer` e' un processo, e lo stato periodico passa ogni due secondi. L'unico che quel
        volume lo cambia e' Mirto, e quando lo cambia il valore nuovo gli torna nella risposta."""
        n = self.nodo()
        for _ in range(5):
            n.status()
        # Una lettura sola: le altre quattro vengono dalla memoria.
        self.assertEqual(n._volume_letto[0]["percento"], 60)
        letture = []
        n.manopole.leggi = lambda s, _l=letture: _l.append(s) or []
        n.status()
        self.assertEqual(letture, [], "entro i dieci secondi non si rilegge")

    def test_a_bluetooth_speaker_says_why_instead_of_a_number(self):
        st = self.nodo(uscita="bluetooth", scheda="").status()["volume"]
        self.assertFalse(st["si_puo"])
        self.assertEqual(st["perche"], "bluetooth")

    def test_a_card_without_a_volume_knob_says_so(self):
        st = self.nodo(manopole={"UACDemoV10": [{"nome": "Auto Gain Control", "indice": 0,
                                                 "interruttore": {"acceso": True}}]}).status()["volume"]
        self.assertEqual(st["perche"], "senza-manopola")

    def test_setting_it_turns_the_right_knob_and_answers_with_the_new_value(self):
        n = self.nodo()
        esito = n.set_volume({"percento": 30})
        self.assertEqual(self.schede.girate, [("UACDemoV10", "PCM,0", "30%")])
        self.assertEqual(esito["volume"]["percento"], 30, "il valore nuovo torna subito")

    def test_out_of_range_and_nonsense_are_refused(self):
        n = self.nodo()
        for sbagliato in ({"percento": 101}, {"percento": -1}, {"percento": "forte"}, {}):
            with self.assertRaises(ActionError, msg=str(sbagliato)):
                n.set_volume(sbagliato)
        self.assertEqual(self.schede.girate, [], "niente girato")

    def test_on_bluetooth_the_refusal_is_readable_by_a_program(self):
        n = self.nodo(uscita="bluetooth", scheda="")
        with self.assertRaises(ActionError) as e:
            n.set_volume({"percento": 50})
        self.assertEqual(e.exception.dati["motivo"], "volume_non_regolabile")
        self.assertEqual(e.exception.dati["perche"], "bluetooth")

    def test_a_card_that_refuses_is_not_reported_as_done(self):
        n = self.nodo(accetta=False)
        with self.assertRaises(ActionError):
            n.set_volume({"percento": 30})


class IlFlussoConAudioTest(unittest.TestCase):
    """Il muxer che pubblica video e microfono insieme (`flusso_audio.py`, API 1.13).

    Marco, 25/10: *«la registrazione del video non contiene l'audio pur avendo il microfono
    agganciato»*. Tre scelte da tenere ferme: il video si **copia** (ricodificarlo su un 3B vuol dire
    un core sempre occupato), l'audio arriva da **stdin** e non da ALSA (il microfono del nodo lo legge
    un `arecord` solo, condiviso: un secondo lettore spegnerebbe il riconoscimento dei suoni), e il
    flusso con l'audio e' un percorso **a parte** (se il muxer muore si perde l'audio, non la camera).
    """

    class FintoProcesso:
        def __init__(self, cmd, **kw):
            self.cmd, self.returncode, self.scritto = list(cmd), None, bytearray()
            self.stdin, self.stderr, self.ucciso = self, self, False
            self.chiuso = False

        # fa anche da stdin e da stderr: la prova guarda cosa ci e' stato scritto
        def write(self, b):
            if self.returncode is not None:
                raise BrokenPipeError("ffmpeg morto")
            self.scritto.extend(b)

        def flush(self):
            pass

        def close(self):
            self.chiuso = True

        def read(self):
            return b"Connection refused\n"

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

        def terminate(self):
            self.ucciso = True
            self.returncode = self.returncode if self.returncode is not None else 0

        def kill(self):
            self.terminate()

    class FintoMic:
        def __init__(self, pezzi=None, errore=""):
            self.pezzi = pezzi if pezzi is not None else [b"\0" * 3200] * 3
            self.errore, self.ascolti = errore, 0

        def stream(self, stop=None):
            from mirto_node.mic import MicError

            self.ascolti += 1
            for pezzo in self.pezzi:
                yield pezzo
            if self.errore:
                raise MicError(self.errore)

    def flusso(self, mic=None, **kw):
        from mirto_node.flusso_audio import FlussoConAudio

        self.processi = []

        def popen(cmd, **kwargs):
            proc = self.FintoProcesso(cmd, **kwargs)
            self.processi.append(proc)
            return proc

        return FlussoConAudio("rtsp://u:p@127.0.0.1:8554/cam", "rtsp://u:p@127.0.0.1:8554/cam-audio",
                              mic if mic is not None else self.FintoMic(), popen=popen, attesa=0.01, **kw)

    def test_the_video_is_copied_and_the_audio_comes_from_stdin(self):
        from mirto_node.flusso_audio import comando

        cmd = comando("ffmpeg", "rtsp://dentro/cam", "rtsp://dentro/cam-audio")
        self.assertIn("copy", cmd, "il video si copia: ricodificarlo su un 3B vuol dire un core occupato")
        self.assertNotIn("libx264", cmd)
        self.assertIn("pipe:0", cmd, "l'audio arriva da stdin, non da ALSA")
        self.assertNotIn("alsa", " ".join(cmd), "un secondo lettore di ALSA spegnerebbe il riconoscimento")
        self.assertIn("-use_wallclock_as_timestamps", cmd,
                      "il PCM non porta un orario: senza, il ritardo cresce per tutta la registrazione")
        self.assertEqual(cmd[-1], "rtsp://dentro/cam-audio", "pubblica sul percorso a parte")
        self.assertIn("rtsp://dentro/cam", cmd, "e legge il video dalla camera")

    def test_it_pumps_the_microphone_into_ffmpeg(self):
        mic = self.FintoMic(pezzi=[b"a" * 3200, b"b" * 3200])
        f = self.flusso(mic=mic)
        f._un_giro()
        self.assertEqual(bytes(self.processi[0].scritto), b"a" * 3200 + b"b" * 3200)
        self.assertEqual(mic.ascolti, 1, "un ascolto solo: e' il terzo di quell'unico arecord")

    def test_a_dead_ffmpeg_says_why_and_is_restarted(self):
        f = self.flusso()
        f._proc = None
        morto = self.FintoProcesso(["ffmpeg"])
        morto.returncode = 1

        def popen(cmd, **kw):
            self.processi.append(morto)
            return morto

        f._popen = popen
        with self.assertRaises(RuntimeError) as e:
            f._un_giro()
        self.assertIn("Connection refused", str(e.exception),
                      "l'ultima riga di ffmpeg e' l'unica cosa che dice cosa e' andato storto")

    def test_a_missing_ffmpeg_is_a_defect_not_a_hiccup(self):
        def popen(cmd, **kw):
            raise OSError("No such file or directory: 'ffmpeg'")

        f = self.flusso()
        f._popen = popen
        with self.assertRaises(RuntimeError) as e:
            f._un_giro()
        self.assertIn("install.sh", str(e.exception), "il rimedio non e' aspettare, ed e' scritto")

    def test_a_microphone_that_goes_away_is_said_as_the_microphone(self):
        f = self.flusso(mic=self.FintoMic(errore="microfono scollegato"))
        with self.assertRaises(RuntimeError) as e:
            f._un_giro()
        self.assertIn("microfono", str(e.exception))

    def test_the_status_tells_running_from_broken(self):
        """«fermo» con un errore e «fermo» senza sono due cose: la prima da sistemare, la seconda una
        che sta partendo."""
        f = self.flusso(clock=lambda: 1000.0)
        self.assertFalse(f.status()["attivo"])
        self.assertNotIn("errore", f.status())
        self.assertEqual(f.status()["percorso"], "cam-audio")
        f.errore = "Connection refused"
        self.assertIn("errore", f.status())

    def test_stopping_closes_the_pipe_before_killing(self):
        f = self.flusso()
        f._proc = proc = self.FintoProcesso(["ffmpeg"])
        f._ammazza()
        self.assertTrue(proc.chiuso, "prima si chiude la pipe: ffmpeg chiude il file come si deve")
        self.assertTrue(proc.ucciso)


class AudioNelleRegistrazioniTest(unittest.TestCase):
    """L'impostazione: spenta di serie, e il nodo dice no **prima** se non puo' farla.

    Scoprirlo dopo vorrebbe dire un'impostazione accesa in Mirto e un silenzio nelle clip che
    nessuno sa spiegare — e su questa funzione e' peggio che su qualunque altra, perche' chi l'accende
    lo fa credendo di aver registrato l'audio di quello che e' successo.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        self.scritto = {}

    def nodo(self, mic=None, audio=False):
        p = FakePlayer()
        self.cfg = {"attiva": True, "disco": "", "massimo_gb": 0, "minimo_libero_percento": 10,
                    "audio": audio}
        return Node("N", generate_key(), {}, p, mic=mic, folder=self.d,
                    rec_cfg=lambda: dict(self.cfg), set_rec_cfg=self.scritto.update,
                    cameras=lambda: [], addresses=lambda: [], hostname=lambda: "n")

    def test_it_is_off_to_begin_with(self):
        from mirto_node.recording import recording_cfg

        self.assertFalse(recording_cfg({})["audio"], "e' un cambio di politica, non una comodita'")
        st = self.nodo().status()["audio_registrazione"]
        self.assertFalse(st["acceso"])
        self.assertFalse(st["attivo"])

    def test_without_a_microphone_it_refuses_instead_of_promising(self):
        with self.assertRaises(ActionError) as e:
            self.nodo().set_disk({"azione": "audio", "attivo": True})
        self.assertEqual(e.exception.dati["motivo"], "audio_reg_senza_microfono")
        self.assertEqual(self.scritto, {}, "niente salvato")

    def test_without_ffmpeg_it_refuses_too(self):
        import shutil

        n = self.nodo(mic=object())
        vero = shutil.which
        shutil.which = lambda x: None if x == "ffmpeg" else vero(x)
        self.addCleanup(lambda: setattr(shutil, "which", vero))
        with self.assertRaises(ActionError) as e:
            n.set_disk({"azione": "audio", "attivo": True})
        self.assertEqual(e.exception.dati["motivo"], "audio_reg_senza_ffmpeg")

    def test_turning_it_off_needs_neither(self):
        """Spegnerla deve funzionare sempre: se servisse il microfono, un microfono staccato
        lascerebbe l'impostazione accesa per sempre."""
        self.nodo(audio=True).set_disk({"azione": "audio", "attivo": False})
        self.assertEqual(self.scritto["audio"], False)

    def test_on_but_with_the_microphone_off_says_why(self):
        st = self.nodo(audio=True).status()["audio_registrazione"]
        self.assertTrue(st["acceso"])
        self.assertFalse(st["attivo"])
        self.assertIn("microfono", st["perche"], "acceso e fermo non e' acceso e funzionante")


class RegistrareIlPercorsoGiustoTest(unittest.TestCase):
    """Accendere l'audio cambia il percorso registrato, e l'archivio non si deve spezzare.

    «Le registrazioni gia' fatte restino leggibili — mute, ma leggibili» era scritto nel documento
    prima del codice, ed e' la cosa che si rompe piu' facilmente: da quel giorno in avanti i file
    nascono in un'altra cartella, e chi riguarda un pomeriggio non deve trovare mezzo archivio
    sparito perche' qualcuno ha spuntato una casella.
    """

    class FakeApi:
        def __init__(self, state=False):
            self.state, self.calls, self.path = state, [], "cam"
            self.last_status = 200

        def recording(self):
            return self.state

        def set_recording(self, on):
            self.state = bool(on)
            self.calls.append((self.path, bool(on)))
            return True

    class FintoFlusso:
        def __init__(self, vivo=False):
            self.vivo = vivo

        def attivo(self):
            return self.vivo

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "reg"
        from mirto_node import recording as R

        self.R = R
        self.api = self.FakeApi()
        self.flusso = self.FintoFlusso()
        self.rec = R.NodeRecorder({"attiva": True, "disco": ""}, api=self.api, flusso=self.flusso)
        self.rec.root = self.root
        self.rec.folder = self.root / R.CAMERA_PATH

    def spezzone(self, percorso: str, nome: str, byte: int = 1000) -> Path:
        d = self.root / percorso
        d.mkdir(parents=True, exist_ok=True)
        f = d / nome
        f.write_bytes(b"\0" * byte)
        return f

    def test_the_path_follows_the_muxer_and_goes_back_when_it_dies(self):
        self.assertEqual(self.rec.percorso, self.R.CAMERA_PATH, "di serie si registra il video")
        self.flusso.vivo = True
        self.rec._segui_il_flusso()
        self.assertEqual(self.rec.percorso, self.R.AUDIO_PATH)
        self.assertEqual(self.rec.folder, self.root / self.R.AUDIO_PATH)
        self.assertEqual(self.api.path, self.R.AUDIO_PATH, "e l'API punta al percorso nuovo")
        self.flusso.vivo = False
        self.rec._segui_il_flusso()
        self.assertEqual(self.rec.percorso, self.R.CAMERA_PATH,
                         "il muxer caduto non lascia registrare un percorso che nessuno pubblica")

    def test_changing_path_turns_the_old_one_off_first(self):
        """MediaMTX continuerebbe a scrivere il flusso di prima, e il disco si riempirebbe del doppio
        senza che nessuno capisse perche'."""
        self.api.state = True
        self.flusso.vivo = True
        self.rec._segui_il_flusso()
        self.assertEqual(self.api.calls[0], ("cam", False), "spento sul percorso vecchio, non sul nuovo")

    def test_the_archive_does_not_break_in_two(self):
        self.spezzone(self.R.CAMERA_PATH, "20261026-100000.mp4")
        self.spezzone(self.R.AUDIO_PATH, "20261026-110000.mp4")
        self.flusso.vivo = True
        self.rec._segui_il_flusso()
        nomi = [s["nome"] for s in self.rec.segments()]
        self.assertEqual(nomi, ["20261026-100000.mp4", "20261026-110000.mp4"],
                         "muti e non muti, in ordine di tempo: chi riguarda non deve accorgersene")
        self.assertIsNotNone(self.rec.file("20261026-100000.mp4"), "il vecchio si apre ancora")
        self.assertIsNotNone(self.rec.file("20261026-110000.mp4"))
        self.assertIsNone(self.rec.file("20261026-120000.mp4"))

    def test_the_ring_counts_both_folders(self):
        """Se l'anello guardasse una cartella sola, non vedrebbe lo spazio occupato dall'altra: il
        disco si riempirebbe e il nodo direbbe di avere posto."""
        for i in range(4):
            self.spezzone(self.R.CAMERA_PATH, f"2026102{i}-100000.mp4", byte=2000)
        self.spezzone(self.R.AUDIO_PATH, "20261026-100000.mp4", byte=2000)
        self.flusso.vivo = True
        self.rec._segui_il_flusso()
        self.rec.max_bytes = 4000
        self.rec.cleanup()
        restati = sorted(s["nome"] for s in self.rec.segments())
        self.assertIn("20261026-100000.mp4", restati, "quello in scrittura non si tocca mai")
        self.assertLess(len(restati), 5, "i piu' vecchi sono stati cancellati, da qualunque cartella")
        self.assertNotIn("20261020-100000.mp4", restati, "e si comincia dal piu' vecchio in assoluto")


class LEsitoAppartieneAlTentativoTest(unittest.TestCase):
    """Accettando un pacchetto nuovo, l'esito di quello di prima si cancella (API 1.13).

    Il 26/10 un «fallito» rimasto dal tentativo del mattino ha fatto dire a Mirto «il nodo non e'
    riuscito a installare» mentre l'installazione nuova stava ancora andando. Un esito senza un
    tentativo a cui appartenere e' peggio di nessun esito, perche' ha l'aria di una risposta.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        p = FakePlayer()
        self.node = Node("N", generate_key(), {}, p, folder=self.d,
                         cameras=lambda: [], addresses=lambda: [], hostname=lambda: "n")

    def pacchetto(self) -> dict:
        import base64
        import hashlib

        from mirto.actions.aggiorna_nodo import fai_pacchetto

        crudo, versione, _sha = fai_pacchetto()
        return {"pacchetto": base64.b64encode(crudo).decode(), "versione": versione,
                "sha256": hashlib.sha256(crudo).hexdigest()}

    def test_accepting_a_new_package_clears_the_old_outcome(self):
        (self.d / "pacchetto-esito").write_text("fallito\n0.12.0\nroba di stamattina\n")
        self.assertEqual(self.node.esito_aggiornamento()["esito"], "fallito")
        self.node.aggiorna(self.pacchetto())
        self.assertEqual(self.node.esito_aggiornamento(), {},
                         "l'esito di prima non deve poter rispondere per questo tentativo")
        self.assertTrue((self.d / "pacchetto-da-installare").is_file(), "e il biglietto c'e'")

    def test_a_refused_package_leaves_the_old_outcome_alone(self):
        """Se il pacchetto non viene nemmeno accettato, non e' cominciato nessun tentativo nuovo:
        cancellare l'esito di prima vorrebbe dire buttare via l'unica cosa che si sa."""
        (self.d / "pacchetto-esito").write_text("fallito\n0.12.0\nroba di stamattina\n")
        with self.assertRaises(ActionError):
            self.node.aggiorna({"pacchetto": "", "versione": "9.9.9"})
        self.assertEqual(self.node.esito_aggiornamento()["esito"], "fallito")
