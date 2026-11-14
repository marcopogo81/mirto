# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
import http.client
import os
import ssl
import tempfile
import time
import types
import unittest
from pathlib import Path

import numpy as np

from mirto.actions.diagnosi import API_ATTESA  # noqa: E402
from mirto.actions.engine import ActionEngine, SIREN_MAX_SECONDS
from mirto.actions.nodes import NodeClient, NodeConfig, NodeError, fetch_certificate, pair_node
from mirto.actions.signing import Verifier, generate_key, sign
from mirto.actions.simulator import SimulatorServer
from mirto.config import Config
from mirto.credentials import SecretStore
from mirto.events import Event
from tests.test_sources import wait_until


def mem_secrets():
    s = SecretStore(); s._kr = None
    return s


class SigningTest(unittest.TestCase):
    def test_sign_verify_replay_and_expiry(self):
        key = generate_key()
        v = Verifier(key)
        h = sign(key, "POST", "/azioni/x/attiva", b"{}", now=1000)
        self.assertEqual(v.verify("POST", "/azioni/x/attiva", h, b"{}", now=1005), (True, ""))
        self.assertFalse(v.verify("POST", "/azioni/x/attiva", h, b"{}", now=1005)[0], "ripetizione")
        h2 = sign(key, "POST", "/azioni/x/attiva", b"{}", now=1000)
        self.assertFalse(v.verify("POST", "/azioni/x/attiva", h2, b"{}", now=1100)[0], "scaduta")
        h3 = sign(key, "POST", "/azioni/x/attiva", b"{}", now=1000)
        self.assertFalse(v.verify("POST", "/azioni/y/attiva", h3, b"{}", now=1000)[0], "percorso diverso")
        h4 = sign(generate_key(), "GET", "/stato", b"", now=1000)
        self.assertFalse(v.verify("GET", "/stato", h4, b"", now=1000)[0], "chiave diversa")


class CassaSceltaTest(unittest.TestCase):
    """La regola che tiene selezionata la cassa giusta quando l'elenco si rifà (hardware, 21/10)."""

    def test_a_speaker_seen_passing_is_kept_chosen_but_never_picked_for_you(self):
        """Stessa lezione del 21/10, sull'elenco che dall'8/11 si rifà dopo ogni tentativo.

        Senza il ripiego sulla prima riga, però: lì accenderebbe «Abbina» su una cassa che nessuno
        ha scelto, in un elenco che Marco vuole nasca vuoto.
        """
        from mirto.actions.pannello_audio import quale_trovata_tenere

        elenco = ["FF:02:3C:D5:9A:7E", "AA:BB:CC:DD:EE:FF"]
        self.assertEqual(quale_trovata_tenere("AA:BB:CC:DD:EE:FF", "FF:02:3C:D5:9A:7E", elenco),
                         "AA:BB:CC:DD:EE:FF", "quella del tentativo in corso viene prima")
        self.assertEqual(quale_trovata_tenere("", "AA:BB:CC:DD:EE:FF", elenco), "AA:BB:CC:DD:EE:FF")
        self.assertEqual(quale_trovata_tenere("", "11:22:33:44:55:66", elenco), "",
                         "sparita dall'elenco: nessuna, non la prima che capita")
        self.assertEqual(quale_trovata_tenere("", "", elenco), "",
                         "nessuno ha scelto: «Abbina» resta spento")

    def test_the_speaker_being_paired_wins(self):
        from mirto.actions.pannello_audio import quale_cassa_tenere

        elenco = ["FF:02:3C:D5:9A:7E", "AA:BB:CC:DD:EE:FF"]
        self.assertEqual(quale_cassa_tenere("AA:BB:CC:DD:EE:FF", "", "", elenco), "AA:BB:CC:DD:EE:FF",
                         "l'abbinamento in corso comanda: l'errore deve parlare di lei")
        self.assertEqual(quale_cassa_tenere("", "AA:BB:CC:DD:EE:FF", "FF:02:3C:D5:9A:7E", elenco),
                         "AA:BB:CC:DD:EE:FF", "poi la scelta di chi guarda, non quella del nodo")
        self.assertEqual(quale_cassa_tenere("", "", "FF:02:3C:D5:9A:7E", elenco), "FF:02:3C:D5:9A:7E",
                         "all'apertura: quella configurata sul nodo")

    def test_a_speaker_that_is_no_longer_in_the_list_does_not_decide(self):
        from mirto.actions.pannello_audio import quale_cassa_tenere

        elenco = ["AA:BB:CC:DD:EE:FF"]
        self.assertEqual(quale_cassa_tenere("11:11:11:11:11:11", "", "", elenco), "AA:BB:CC:DD:EE:FF")
        self.assertEqual(quale_cassa_tenere("", "", "", []), "", "senza casse non si sceglie niente")

    def test_the_answer_of_the_attempt_before_is_not_an_answer(self):
        """Il nodo lascia l'esito in un file: dentro c'è ancora quello del tentativo di prima."""
        from mirto.actions.pannello_audio import esito_arrivato, firma_esito

        vecchio = {"azione": "cerca", "quando": 100, "esito": "ok", "messaggio": "3 casse"}
        prima = firma_esito(vecchio)
        self.assertFalse(esito_arrivato(vecchio, prima, "cerca"), "quello di prima non conta")
        self.assertFalse(esito_arrivato({"azione": "abbina", "quando": 140}, prima, "cerca"),
                         "e nemmeno la risposta a un'altra domanda")
        nuovo = {"azione": "cerca", "quando": 140, "esito": "ok", "messaggio": "1 casse"}
        self.assertTrue(esito_arrivato(nuovo, prima, "cerca"))

    def test_an_endless_wait_is_a_frozen_window(self):
        from mirto.actions.pannello_audio import attesa_scaduta

        self.assertFalse(attesa_scaduta(0.0, 60, 80.0), "mentre il nodo lavora si aspetta")
        self.assertFalse(attesa_scaduta(0.0, 60, 90.0), "e c'è mezzo minuto di grazia")
        self.assertTrue(attesa_scaduta(0.0, 60, 91.0), "poi si smette, e si dice che non ha risposto")
        self.assertTrue(attesa_scaduta(0.0, 0, 51.0), "senza tempo dichiarato: cinquanta secondi")


class NodesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["MIRTO_CONFIG_DIR"] = cls.tmp.name
        os.environ["MIRTO_DATA_DIR"] = cls.tmp.name
        cls.a = SimulatorServer("ingresso", cert_dir=Path(cls.tmp.name, "a")).start()
        cls.b = SimulatorServer("garage", cert_dir=Path(cls.tmp.name, "b")).start()

    @classmethod
    def tearDownClass(cls):
        cls.a.stop(); cls.b.stop(); cls.tmp.cleanup()

    def client(self, srv, key=None, pem_from=None):
        pem, _fp = fetch_certificate((pem_from or srv).url)
        return NodeClient(NodeConfig("n", srv.node.name, srv.url), pem, key or srv.key_hex)

    def test_contract(self):
        c = self.client(self.a)
        self.assertEqual(c.info()["nome"], "ingresso")
        self.assertIn("sirena", [x["nome"] for x in c.actions()])
        res = c.activate("sirena", 999, "ev1", "Ingresso")
        self.assertEqual(res["durata"], SIREN_MAX_SECONDS, "il nodo limita la sirena a 3 minuti")
        self.assertIn("sirena", c.status()["attive"])
        c.stop_all()
        self.assertEqual(c.status()["attive"], {})
        data, mime = c.record_audio(3)
        self.assertTrue(data.startswith(b"RIFF"))
        c.play_audio(b"OggS....", "audio/ogg")
        self.assertEqual(self.a.node.played[-1], ("audio/ogg", 8))

    def test_wrong_key_rejected(self):
        with self.assertRaises(NodeError) as cm:
            self.client(self.a, key=generate_key()).status()
        self.assertIn("401", str(cm.exception))

    def test_pinning_blocks_other_certificate(self):
        c = self.client(self.b, pem_from=self.a)  # certificato di A, ma ci si collega a B
        with self.assertRaises(NodeError) as cm:
            c.status()
        self.assertIn("certificato diverso", str(cm.exception))

    def test_unsigned_request_rejected(self):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
        conn = http.client.HTTPSConnection("127.0.0.1", self.a.port, context=ctx, timeout=5)
        conn.request("POST", "/azioni/ferma-tutto", body=b"{}")
        self.assertEqual(conn.getresponse().status, 401)
        conn.close()


class FakeNotifier:
    def __init__(self):
        self.texts = []

    def notify_text(self, t):
        self.texts.append(t)


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        os.environ["MIRTO_CONFIG_DIR"] = self.tmp.name
        os.environ["MIRTO_DATA_DIR"] = self.tmp.name
        self.a = SimulatorServer("ingresso", cert_dir=Path(self.tmp.name, "a")).start()
        self.b = SimulatorServer("garage", cert_dir=Path(self.tmp.name, "b")).start()
        self.cfg = Config(Path(self.tmp.name) / "config.yaml")
        self.secrets = mem_secrets()
        for nid, srv in (("ingresso", self.a), ("garage", self.b)):
            pem, _ = fetch_certificate(srv.url)
            pair_node(self.cfg, self.secrets, nid, srv.node.name, srv.url, pem, srv.key_hex)
        self.cfg.set("actions.items", [
            {"name": "Luci", "node": "ingresso", "action": "luci", "kind": "light", "mode": "auto", "duration": 30},
            {"name": "Sirena", "node": "ingresso", "action": "sirena", "kind": "siren", "mode": "auto",
             "delay": 0.3, "duration": 900},
            {"name": "Uscita 1", "node": "garage", "action": "uscita_1", "kind": "generic", "mode": "confirm",
             "confirm_timeout": 60},
            {"name": "Audio", "node": "ingresso", "action": "audio", "kind": "audio"},
        ])
        self.notifier = FakeNotifier()
        self.core = types.SimpleNamespace(config=self.cfg, secrets=self.secrets, notifier=self.notifier)
        self.engine = ActionEngine(self.core)
        self.ev = Event("cam1", "Ingresso", 0.8, np.zeros((10, 10, 3), np.uint8))

    def tearDown(self):
        self.engine.stop(); self.a.stop(); self.b.stop(); self.tmp.cleanup()

    def activated(self, srv):
        return [p for (m, path, p) in srv.node.calls if path.endswith("/attiva")]

    def comandi_attivati(self, srv):
        """I comandi accesi, in ordine: il nome sta nel percorso (``/azioni/<nome>/attiva``)."""
        return [path.split("/")[2] for (m, path, p) in srv.node.calls if path.endswith("/attiva")]

    def test_a_chain_runs_in_order_on_the_node(self):
        """La prova vera della catena: il campanello di Marco, eseguito sul nodo finto.

        Luce per un attimo, un'attesa, poi il suono due volte. Il secondo passo deve partire **dopo**
        il primo (durata piu\' attesa), non insieme, e il nodo deve riceverli nell'ordine giusto.
        """
        self.cfg.set("actions.items", [
            {"name": "Campanello", "node": "ingresso", "kind": "generic", "mode": "auto",
             "action": "luci", "duration": 30,
             "passi": [{"comando": "luci", "durata": 1, "pausa": 0.2},
                       {"comando": "messaggio", "durata": 1, "ripeti": 2}]},
        ])
        self.engine.reload()
        self.engine.set_armed(True)
        spec = next(s for s in self.engine.specs if s.name == "Campanello")
        self.assertEqual([c["comando"] for c in spec.catena()], ["luci", "messaggio", "messaggio"])

        self.engine.on_intrusion(self.ev)
        self.assertTrue(wait_until(lambda: len(self.activated(self.a)) >= 3, timeout=15),
                        f"la catena non e\' arrivata in fondo: {self.activated(self.a)}")
        comandi = self.comandi_attivati(self.a)
        self.assertEqual(comandi[:3], ["luci", "messaggio", "messaggio"], f"ordine sbagliato: {comandi}")
        # E il secondo «messaggio» deve aver fatto qualcosa davvero: se fosse arrivato mentre il primo
        # era ancora in corso, il nodo avrebbe risposto «già attiva» senza suonare (vedi MINIMO_NODO).
        self.assertEqual(self.a.node.saltate, [], "una ripetizione e\' caduta: il nodo la aveva in corso")

    def test_a_chain_can_cross_two_nodes(self):
        """Tappa 2: chi esegue obbedisce al dispositivo **del pezzo**, non a quello dell'azione.

        E' la prova che il modello nuovo serve a qualcosa: la luce si accende all'ingresso e il suono
        esce dal garage, dentro **una** azione. Finche' il nodo stava sull'azione, una cosa cosi' si
        poteva solo scrivere in due azioni che non sanno l'una dell'altra.
        """
        self.cfg.set("actions.items", [
            {"name": "Due stanze", "node": "ingresso", "kind": "generic", "mode": "auto",
             "action": "luci", "duration": 30,
             "passi": [{"comando": "luci", "durata": 1, "pausa": 0.2},
                       {"comando": "messaggio", "durata": 1, "dispositivo": "nodo:garage"}]},
        ])
        self.engine.reload()
        self.engine.set_armed(True)
        spec = next(s for s in self.engine.specs if s.name == "Due stanze")
        self.assertEqual([c["dispositivo"] for c in spec.catena()], ["nodo:ingresso", "nodo:garage"])

        self.engine.on_intrusion(self.ev)
        self.assertTrue(wait_until(lambda: self.comandi_attivati(self.b), timeout=15),
                        "il secondo pezzo non e\' mai arrivato al garage")
        self.assertEqual(self.comandi_attivati(self.a), ["luci"], "all'ingresso va solo la luce")
        self.assertEqual(self.comandi_attivati(self.b), ["messaggio"], "il suono esce dal garage")

    def test_a_piece_pointed_at_a_node_that_is_not_there_stops_the_chain(self):
        """Un dispositivo che non esiste non deve far finire il pezzo sul nodo sbagliato."""
        self.cfg.set("actions.items", [
            {"name": "Sbagliata", "node": "ingresso", "kind": "generic", "mode": "auto",
             "action": "luci", "duration": 30,
             "passi": [{"comando": "messaggio", "durata": 1, "dispositivo": "nodo:cantina"},
                       {"comando": "luci", "durata": 1}]},
        ])
        self.engine.reload()
        self.engine.set_armed(True)
        self.engine.on_intrusion(self.ev)
        self.assertFalse(wait_until(lambda: self.comandi_attivati(self.a), timeout=3),
                         "il pezzo del nodo che non c'e\' e\' finito su un altro nodo")

    def test_the_limits_come_from_the_node_that_runs_the_piece(self):
        """I tetti li dichiara il nodo: in una catena su due nodi non puo' deciderli uno solo."""
        chiesti = []

        def tetti_per(dove):
            chiesti.append(dove)
            return {"sirena": 180.0} if dove == "nodo:ingresso" else {}

        from mirto.actions.passi import Passo, srotola

        catena = srotola([Passo(comando="sirena", durata=900),
                          Passo(comando="sirena", durata=900, dispositivo="nodo:garage")],
                         0, 1, dispositivo="nodo:ingresso", tetti_per=tetti_per)
        self.assertEqual(catena[0]["durata"], 180.0, "all'ingresso la sirena ha il suo tetto")
        self.assertEqual(catena[1]["durata"], 900.0, "il garage non ne dichiara: non si inventa")
        self.assertEqual(chiesti, ["nodo:ingresso", "nodo:garage"])

    def test_stopping_a_trial_also_throws_away_the_queued_pieces(self):
        """Spegnere l'audio non basta: il pezzo dopo partirebbe lo stesso (24/10).

        Marco, provando un brano di mezzo minuto dentro il wizard: «da qui non e' possibile bloccare
        l'audio una volta premuto play». Fermare una prova vuol dire spegnere **e** disfare la coda.
        """
        passi = [{"comando": "luci", "durata": 1, "pausa": 1.5},
                 {"comando": "messaggio", "durata": 1}]
        self.engine.prova_catena("ingresso", passi)
        self.assertTrue(wait_until(lambda: self.comandi_attivati(self.a), timeout=10))
        self.assertIn("Fermato", self.engine.ferma_prova("ingresso"))
        quanti = len(self.comandi_attivati(self.a))
        self.assertFalse(wait_until(lambda: len(self.comandi_attivati(self.a)) > quanti, timeout=4),
                         "il secondo pezzo e' partito lo stesso: la coda non e' stata disfatta")
        self.assertFalse(self.engine.has_activity(), "e la prova non risulta piu' in corso")

    def test_stopping_everything_breaks_a_chain_in_the_middle(self):
        """«Ferma tutto» deve interrompere una catena a meta\', come interrompe tutto il resto."""
        self.cfg.set("actions.items", [
            {"name": "Lunga", "node": "ingresso", "kind": "generic", "mode": "auto", "action": "luci",
             "duration": 30, "passi": [{"comando": "luci", "durata": 0.1, "pausa": 5},
                                       {"comando": "audio", "durata": 0.1}]},
        ])
        self.engine.reload()
        self.engine.set_armed(True)
        self.engine.on_intrusion(self.ev)
        self.assertTrue(wait_until(lambda: len(self.activated(self.a)) >= 1, timeout=10))
        self.engine.stop_all()
        quanti = len(self.activated(self.a))
        time.sleep(0.5)
        self.assertEqual(len(self.activated(self.a)), quanti, "il passo dopo non deve piu\' partire")

    def test_the_siren_budget_is_three_minutes_in_half_an_hour(self):
        """Il limite esisteva dal primo giorno e non aveva una prova: eccola, prima di spostarlo."""
        spec = next(s for s in self.engine.specs if s.name == "Sirena")
        self.assertEqual(self.engine._allowed_duration(spec), SIREN_MAX_SECONDS)
        self.engine._active_until.clear()  # non «già attiva»: deve essere il budget a dire no
        self.assertEqual(self.engine._allowed_duration(spec), 0.0)

    def test_the_node_says_what_each_command_is(self):
        """I limiti di una catena vengono da qui: dal nodo, non dall'etichetta scelta in Mirto."""
        self.engine.check_nodes()
        self.assertEqual(self.engine.tipi_comandi("ingresso").get("sirena"), "siren")
        self.assertEqual(self.engine.tipi_comandi("ingresso").get("luci"), "light")
        self.assertEqual(self.engine.tetti_comandi("ingresso").get("sirena"), SIREN_MAX_SECONDS)
        self.assertNotIn("luci", self.engine.tetti_comandi("ingresso"),
                         "una luce non ha un limite suo: risponde alla durata dell'azione")
        self.assertEqual(self.engine.tipi_comandi("mai visto"), {}, "un nodo mai letto non inventa tipi")

    def test_a_siren_step_counts_on_the_budget_even_in_a_generic_action(self):
        """Il buco che si apriva con le catene: una sirena dentro un'azione «generica».

        Il budget guardava l'etichetta dell'azione (``kind == "siren"``), e un passo «sirena» in
        un'azione generica avrebbe suonato senza consumare niente — tre minuti ogni mezz'ora diventano
        tre minuti ogni volta che qualcuno cambia la tendina del tipo.
        """
        self.cfg.set("actions.items", [
            {"name": "Tutto", "node": "ingresso", "kind": "generic", "mode": "auto",
             "action": "luci", "duration": 300,
             "passi": [{"comando": "luci", "durata": 10}, {"comando": "sirena", "durata": 300}]},
        ])
        self.engine.reload()
        self.engine.check_nodes()
        spec = next(s for s in self.engine.specs if s.name == "Tutto")
        catena = spec.catena(self.engine.tetti_comandi("ingresso"))
        self.assertEqual([c["durata"] for c in catena], [10, SIREN_MAX_SECONDS],
                         "la sirena nella catena resta tre minuti anche se l'azione ne concede cinque")
        self.assertGreater(self.engine._allowed_duration(spec), 0)
        self.engine._active_until.clear()
        self.assertEqual(self.engine._allowed_duration(spec), 0.0,
                         "quella sirena ha consumato il budget come qualunque altra sirena")

    def test_a_warning_sound_can_be_aimed_at_a_person(self):
        """L'opposto della prova di ieri, e la ragione sta nell'esempio di Marco.

        Il 22/10 avevo scritto che un suono di dissuasione non scatta mai per una persona. Il 23/10
        e' arrivato il caso vero: uno scavalca la recinzione, una voce gli dice di allontanarsi, e se
        dopo dieci secondi e' ancora li' parte la sirena. Avvisare prima di urlare e' la cosa piu'
        civile che questo programma possa fare, e quella regola la vietava.
        """
        self.cfg.set("actions.items", [
            {"name": "Recinto", "node": "ingresso", "kind": "generic", "mode": "auto",
             "action": "dissuasore", "duration": 8, "targets": ["person"]},
        ])
        self.engine.reload()
        self.engine.check_nodes()
        self.engine.set_armed(True)
        self.engine.on_intrusion(self.ev)
        self.assertTrue(wait_until(lambda: len(self.activated(self.a)) >= 1, timeout=10),
                        "la voce deve partire: e' il caso del recinto")

    def test_an_animal_deterrent_still_ignores_people(self):
        """Quello che resta della vecchia regola, detto senza nominare i tipi: se in inquadratura c'e'
        una persona e il blocco le persone non le ha chieste, non scatta."""
        from mirto.actions.engine import ActionSpec

        gatti = ActionSpec.from_dict({"name": "Gatti", "node": "ingresso", "action": "dissuasore",
                                      "kind": "deterrent", "targets": ["cat"]})

        class E:
            camera_id, camera_name, score = "cam1", "Ingresso", 0.9

            def __init__(self, *t):
                self._t = set(t)

            def targets(self):
                return self._t

        self.assertTrue(gatti.matches(E("cat")))
        self.assertFalse(gatti.matches(E("cat", "person")), "col padrone in giardino, no")

    def test_a_person_does_not_fire_anything_before_the_strong_confirmation(self):
        """La garanzia che tengo d'occhio mentre unifico le due strade del motore (23/10).

        «on_detection» e' la porta delle cose riconosciute; «on_intrusion» e' quella delle conferme
        forti. Un'azione che scatta per le persone deve passare dalla seconda: far suonare una sirena
        su un riconoscimento incerto e' il modo piu' veloce di far spegnere Mirto a chi ce l'ha.
        """
        self.cfg.set("actions.items", [
            {"name": "Sirena", "node": "ingresso", "action": "sirena", "kind": "siren",
             "mode": "auto", "duration": 30, "targets": ["person"]},
        ])
        self.engine.reload()
        self.engine.set_armed(True)
        self.engine.on_detection(self.ev)       # riconoscimento, non conferma
        time.sleep(0.4)
        self.assertEqual(self.activated(self.a), [], "una persona vista non basta: serve la conferma")
        self.engine.on_intrusion(self.ev)
        self.assertTrue(wait_until(lambda: len(self.activated(self.a)) >= 1, timeout=10),
                        "con la conferma forte invece parte")

    def test_a_sound_does_not_start_the_same_action_twice(self):
        """Con una strada sola, chiamare due porte per lo stesso suono farebbe due avvisi."""
        self.cfg.set("actions.items", [
            {"name": "Vetri", "node": "ingresso", "action": "sirena", "kind": "siren", "mode": "auto",
             "duration": 30, "targets": ["sound:glass"]},
        ])
        self.engine.reload()
        self.engine.set_armed(True)

        class Suono:
            camera_id, camera_name, score, label = "mic:mac", "Microfono del Mac", 0.9, "sound:glass"

            def targets(self):
                return {"sound:glass"}

        self.assertEqual(self.engine.on_detection(Suono()), ["Vetri"])
        self.assertTrue(wait_until(lambda: len(self.activated(self.a)) >= 1, timeout=10))
        quanti = len(self.notifier.texts)
        self.engine.on_detection(Suono())        # lo stesso suono, subito dopo
        time.sleep(0.3)
        self.assertEqual(len(self.activated(self.a)), 1, "il nodo non si accende due volte")
        self.assertGreaterEqual(len(self.notifier.texts), quanti)

    def test_node_overview(self):
        self.engine.check_nodes()
        ov = {n["id"]: n for n in self.engine.node_overview()}
        self.assertTrue(ov["ingresso"]["online"])
        self.assertEqual(ov["ingresso"]["active"], {})
        self.assertIn("Sirena", [a["name"] for a in ov["ingresso"]["actions"]])
        self.engine.set_armed(True)
        self.engine.on_intrusion(self.ev)  # le luci partono subito
        time.sleep(0.4)
        self.engine.check_nodes()
        ov = {n["id"]: n for n in self.engine.node_overview()}
        self.assertIn("luci", ov["ingresso"]["active"])
        self.engine.stop_all()

    def test_has_activity(self):
        self.assertFalse(self.engine.has_activity())  # niente in corso
        self.engine.set_armed(True)
        self.engine.on_intrusion(self.ev)  # luci subito, sirena dopo 0,3 s
        end = time.time() + 3
        while not self.engine.has_activity() and time.time() < end:
            time.sleep(0.02)
        self.assertTrue(self.engine.has_activity())
        self.engine.stop_all()
        self.assertFalse(self.engine.has_activity())

    def test_disarmed_does_nothing(self):
        self.engine.on_intrusion(self.ev)
        self.assertEqual(self.engine.confirmation_buttons(self.ev), [])
        time.sleep(0.6)
        self.assertEqual(self.activated(self.a), [])

    def test_armed_sequence_confirm_and_stop(self):
        self.engine.set_armed(True)
        buttons = self.engine.confirmation_buttons(self.ev)
        self.assertEqual([b[0] for b in buttons], ["Attiva Uscita 1"])
        self.engine.on_intrusion(self.ev)
        self.assertTrue(wait_until(lambda: "luci" in self.a.node.active))
        self.assertNotIn("sirena", self.a.node.active, "la sirena aspetta il ritardo")
        self.assertTrue(wait_until(lambda: "sirena" in self.a.node.active))
        siren = [p for p in self.activated(self.a) if "durata" in p and p.get("durata") == SIREN_MAX_SECONDS]
        self.assertTrue(siren, "durata sirena limitata a 180 s anche da Mirto")
        self.assertNotIn("uscita_1", self.b.node.active, "l'uscita con conferma aspetta il pulsante")
        token = buttons[0][1].split(":", 1)[1]
        self.assertIn("confermata", self.engine.confirm(token, "Marco"))
        self.assertTrue(wait_until(lambda: "uscita_1" in self.b.node.active))
        self.assertIn("già usato", self.engine.confirm(token, "Marco"))
        self.assertTrue(self.engine.stop_all().startswith("⛔ Tutto fermo."))
        self.assertFalse(self.engine.armed, "Ferma tutto disinserisce il sistema")
        self.assertEqual(self.a.node.active, {})
        self.assertEqual(self.b.node.active, {})

    def test_non_person_events_never_trigger_actions(self):
        self.engine.set_armed(True)
        dog = Event("cam1", "Ingresso", 0.95, np.zeros((10, 10, 3), np.uint8), label="dog")
        self.assertEqual(self.engine.confirmation_buttons(dog), [])
        self.engine.on_intrusion(dog)
        time.sleep(0.6)
        self.assertEqual(self.activated(self.a), [])
        self.assertEqual(self.activated(self.b), [])

    def test_stop_all_with_node_down_and_heartbeat(self):
        self.engine.check_nodes()
        self.b.stop()
        msg = self.engine.stop_all()
        self.assertIn("garage", msg)
        self.engine.check_nodes()
        self.assertTrue(any("garage" in t for t in self.notifier.texts))
        self.assertFalse(self.engine.node_online["garage"])
        self.assertTrue(self.engine.node_online["ingresso"])

    def test_audio_manual(self):
        self.assertIn("riprodotto", self.engine.play_audio(b"OggS", "audio/ogg"))
        data, mime = self.engine.listen(3)
        self.assertEqual(mime, "audio/wav")

    def test_disarm_cancels_pending_confirmations(self):
        self.engine.set_armed(True)
        token = self.engine.confirmation_buttons(self.ev)[0][1].split(":", 1)[1]
        self.engine.set_armed(False)
        self.assertIn("scaduto", self.engine.confirm(token, "Marco"))

    def test_no_retrigger_and_siren_budget(self):
        from mirto.actions.engine import ActionSpec
        self.engine.set_armed(True)
        t = [1000.0]
        self.engine.clock = lambda: t[0]
        siren = ActionSpec.from_dict({"name": "S", "node": "ingresso", "action": "sirena", "kind": "Siren",
                                      "duration": 120})
        self.assertEqual(self.engine._allowed_duration(siren), 120)
        self.assertEqual(self.engine._allowed_duration(siren), 0, "non si riattiva mentre suona")
        t[0] += 130
        self.assertEqual(self.engine._allowed_duration(siren), 60, "al massimo 180 s in 30 minuti")
        t[0] += 70
        self.assertEqual(self.engine._allowed_duration(siren), 0)
        t[0] += 1800
        self.assertEqual(self.engine._allowed_duration(siren), 120)

    def test_invalid_kind_rejected(self):
        from mirto.actions.engine import ActionSpec
        with self.assertRaises(ValueError):
            ActionSpec.from_dict({"name": "x", "node": "n", "action": "a", "kind": "serratura"})

    def test_legacy_kinds_become_generic_with_confirmation(self):
        from mirto.actions.engine import ActionSpec
        for old in ("fog", "lock"):
            spec = ActionSpec.from_dict({"name": "x", "node": "n", "action": "a", "kind": old, "mode": "auto",
                                         "close_action": "chiudi"})
            self.assertEqual((spec.kind, spec.mode), ("generic", "confirm"))

    def test_confirm_expires(self):
        self.engine.set_armed(True)
        t = [1000.0]
        self.engine.clock = lambda: t[0]
        token = self.engine.confirmation_buttons(self.ev)[0][1].split(":", 1)[1]
        t[0] += 61
        self.assertIn("scaduto", self.engine.confirm(token, "Marco"))

    def test_dry_run_says_out_loud_that_nothing_was_switched_on(self):
        """Il difetto di partenza: «prova riuscita» e niente acceso, con l'utente convinto del contrario."""
        esito = self.engine.test_action("Luci")        # a vuoto: è il valore predefinito
        self.assertEqual(self.activated(self.a), [], "la prova a vuoto non deve accendere niente")
        self.assertIn("non", esito.lower(), f"deve dire che non ha acceso niente: {esito!r}")
        self.assertIn("non offre", self.engine.test_action("Audio"))

    def test_a_real_test_switches_it_on_for_a_few_seconds(self):
        esito = self.engine.test_action("Luci", dry_run=False)
        acceso = self.activated(self.a)
        self.assertEqual(len(acceso), 1, "la prova vera deve arrivare al nodo")
        self.assertLessEqual(acceso[0]["durata"], 5, "una prova non dura come un'azione vera")
        self.assertIn("Luci", esito, "chi legge deve sapere cosa si è acceso, e per quanto")
        self.assertIn("5", esito)

    def test_the_test_button_runs_the_whole_chain(self):
        """Marco, 24/10, provando dalla scheda Azioni un'azione di tre pezzi su due casse:
        *«si sente solo la sirena, e non per un secondo ma di piu', e non si sentono altri suoni»*.

        Era vero. Il pulsante chiamava `client.activate(spec.action, 5)`: **un comando solo**, per
        cinque secondi, sul primo dispositivo, senza suono e senza uscita. Faceva un'altra cosa
        rispetto al «Prova» del wizard — cioe' provava qualcosa che non esiste.
        """
        import time

        self.cfg.set("actions.items", (self.cfg.get("actions.items") or []) + [
            {"name": "Cancello", "node": "ingresso", "action": "sirena", "kind": "generic",
             "mode": "auto", "duration": 30,
             "passi": [{"comando": "sirena", "durata": 1, "uscita": "bluetooth:AA:BB"},
                       {"comando": "dissuasore", "suono": "allarme_corvi", "durata": 1,
                        "uscita": "bluetooth:AA:BB"},
                       {"comando": "dissuasore", "suono": "falco", "durata": 1,
                        "uscita": "usb:UACDemoV10"}]}])
        self.engine.reload()
        esito = self.engine.test_action("Cancello", dry_run=False)
        scadenza = time.monotonic() + 8
        while len(self.activated(self.a)) < 3 and time.monotonic() < scadenza:
            time.sleep(0.05)
        acceso = self.activated(self.a)
        self.assertEqual([p.get("suono", "") for p in acceso], ["", "allarme_corvi", "falco"],
                         f"i tre pezzi, nell'ordine, con i loro suoni: {acceso}")
        self.assertEqual([p.get("uscita", "") for p in acceso],
                         ["bluetooth:AA:BB", "bluetooth:AA:BB", "usb:UACDemoV10"],
                         "e ognuno dalla cassa che si e' scelta")
        self.assertIn("3", esito, f"e chi legge sa quanti pezzi sono partiti: {esito!r}")

    def test_the_test_shortens_a_long_piece_but_not_a_short_one(self):
        """Il tetto della prova e' quello del wizard: si sente **cosa** fa e **in che ordine**,
        senza restare tre minuti con una sirena accesa. Un pezzo da un secondo resta un secondo."""
        self.cfg.set("actions.items", (self.cfg.get("actions.items") or []) + [
            {"name": "Lunga", "node": "ingresso", "action": "sirena", "kind": "generic",
             "mode": "auto", "duration": 120,
             "passi": [{"comando": "sirena", "durata": 90}, {"comando": "luci", "durata": 1}]}])
        self.engine.reload()
        self.engine.test_action("Lunga", dry_run=False)
        acceso = self.activated(self.a)
        self.assertLessEqual(acceso[0]["durata"], 5, "novanta secondi dentro una prova sono troppi")
        self.assertGreater(acceso[0]["durata"], 0)

    def test_two_devices_fire_in_the_same_instant(self):
        """Il caso del cancello: il suono dalla cassa e la luce **nello stesso istante** (tappa 3).

        Si misura col cronometro, non guardando il codice: il primo pezzo dura tre secondi, quindi
        in fila il secondo arriverebbe dopo tre secondi. Se arriva subito, sono paralleli.
        """
        import time

        self.cfg.set("actions.items", (self.cfg.get("actions.items") or []) + [
            {"name": "Cancello", "node": "ingresso", "action": "sirena", "kind": "generic",
             "mode": "auto", "duration": 30,
             "passi": [{"comando": "sirena", "durata": 3},
                       {"comando": "luci", "durata": 3, "dispositivo": "nodo:garage",
                        "insieme": True}]}])
        self.engine.reload()
        partenza = time.monotonic()
        self.engine.test_action("Cancello", dry_run=False)
        scadenza = partenza + 2.5
        while not self.activated(self.b) and time.monotonic() < scadenza:
            time.sleep(0.02)
        passato = time.monotonic() - partenza
        self.assertTrue(self.activated(self.b),
                        "la luce dell'altro nodo non e' mai partita: il parallelo non c'e'")
        self.assertLess(passato, 1.5,
                        f"e' partita dopo {passato:.1f} s: sta aspettando il pezzo prima, non e' insieme")
        self.assertTrue(self.activated(self.a), "e il suono e' partito comunque")

    def test_without_together_the_second_waits_its_turn(self):
        """La stessa catena senza «insieme»: la luce **non** deve partire subito."""
        import time

        self.cfg.set("actions.items", (self.cfg.get("actions.items") or []) + [
            {"name": "InFila", "node": "ingresso", "action": "sirena", "kind": "generic",
             "mode": "auto", "duration": 30,
             "passi": [{"comando": "sirena", "durata": 3},
                       {"comando": "luci", "durata": 3, "dispositivo": "nodo:garage"}]}])
        self.engine.reload()
        self.engine.test_action("InFila", dry_run=False)
        time.sleep(0.6)
        self.assertFalse(self.activated(self.b),
                         "senza «insieme» il pezzo dopo aspetta che questo finisca")

    def test_a_real_test_on_an_unknown_action_does_not_lie(self):
        self.assertIn("non offre", self.engine.test_action("Audio", dry_run=False))
        self.assertEqual(self.activated(self.a), [])


class ProvaDallInterfacciaTest(unittest.TestCase):
    """Il pulsante «Prova» di Impostazioni → Nodi deve accendere davvero.

    Guardia sul codice, non sull'interfaccia: qui non serve Qt per vedere il difetto che ci è
    costato mezza sera di diagnosi sul Raspberry. Il pulsante chiamava `test_action` con
    `dry_run=True`, quindi rispondeva «riuscita» senza eseguire la riga che accende.
    """

    SORGENTE = Path(__file__).resolve().parents[1] / "mirto" / "gui" / "settings.py"

    def corpo_del_pulsante(self) -> str:
        sorgente = self.SORGENTE.read_text(encoding="utf-8")
        inizio = sorgente.index("def _test_azione")
        return sorgente[inizio:sorgente.index("\n    def ", inizio + 1)]

    def test_the_button_really_switches_the_action_on(self):
        corpo = self.corpo_del_pulsante()
        self.assertIn("dry_run=False", corpo,
                      "un pulsante che si chiama «Prova» deve provare, non controllare la rete")
        self.assertNotIn("dry_run=True", corpo)

    def test_the_loud_ones_ask_first(self):
        corpo = self.corpo_del_pulsante()
        self.assertIn("prova_si_sente", corpo, "sirena e dissuasore si chiedono prima di farli suonare")
        self.assertIn("question", corpo, "e si chiedono con una domanda, non con un avviso")

    def test_nobody_else_is_left_doing_a_dry_run_by_default(self):
        """Se un altro pulsante chiamasse test_action senza dirlo, tornerebbe il difetto di prima."""
        sorgente = self.SORGENTE.read_text(encoding="utf-8")
        chiamate = [r for r in sorgente.splitlines() if "test_action(" in r]
        self.assertTrue(chiamate)
        for riga in chiamate:
            self.assertIn("dry_run=", riga, f"di' sempre quale delle due prove vuoi: {riga.strip()!r}")


if __name__ == "__main__":
    unittest.main()


class AltriComputerTest(unittest.TestCase):
    """«Altri computer…»: chi comanda il nodo, e cosa si può togliere.

    Nasce da un difetto trovato usando il programma il 22/10: *Sistema* e la diagnosi dicevano «togli
    la chiave dell'installazione in Altri computer…», e arrivati lì non c'era niente da selezionare —
    l'elenco mostrava solo i computer abbinati. L'unico pulsante utile, «Stacca…», lavorava sulla riga
    scelta, e la sola riga era *questo computer*. Chi ha provato si è fermato un attimo prima di
    staccarsi il proprio Mac. In fondo, per giunta, c'era scritto di usare «--revoca-tutti», che
    stacca tutto: l'esatto contrario.

    Ogni riga di quell'elenco è qualcuno che può accendere una sirena in casa d'altri: le regole
    stanno fuori dalla finestra apposta, per poterle provare qui.
    """

    STATO = {"chiavi": [{"nome": "mirto-su-mac", "cliente": "Mirto su Mac",
                         "quando": 1792586820, "indirizzo": "192.168.2.1"}],
             "chiave_installazione": True, "ritiro_installazione_tra_s": 6 * 86400 + 4 * 3600,
             "finestra": {"aperto": False}}
    IO = "Mirto su Mac"

    def righe(self, stato=None, io=None):
        from mirto.actions.pannello_abbinamenti import righe

        return righe(self.STATO if stato is None else stato, self.IO if io is None else io)

    def test_the_installation_key_is_a_row_you_can_select(self):
        righe = self.righe()
        self.assertEqual(len(righe), 2, "il computer abbinato e la chiave: due righe")
        chiave = righe[-1]
        self.assertTrue(chiave["installazione"], "sta in fondo: non è un computer")
        self.assertEqual(chiave["nome"], "installazione", "è il nome con cui il nodo la conosce")
        self.assertIn("comanda il nodo", chiave["testo"])
        self.assertIn("6 giorni", chiave["testo"], "quanto manca al ritiro da sola si legge nella riga")

    def test_this_computer_is_marked_so_you_do_not_detach_yourself(self):
        mio = self.righe()[0]
        self.assertTrue(mio["questo"])
        self.assertIn("questo computer", mio["testo"])
        self.assertIn("192.168.2.1", mio["testo"])
        altro = self.righe(io="Mirto su PC")[0]
        self.assertFalse(altro["questo"])

    def test_the_installation_key_can_always_be_detached_the_last_pairing_cannot(self):
        from mirto.actions.pannello_abbinamenti import si_puo_staccare

        righe = self.righe()
        self.assertTrue(si_puo_staccare(righe, "installazione"),
                        "non è un computer: toglierla non lascia il nodo senza nessuno")
        self.assertFalse(si_puo_staccare(righe, "mirto-su-mac"),
                         "è l'unico Mirto abbinato: staccandolo servirebbe il terminale per rientrare")
        self.assertFalse(si_puo_staccare(righe, ""), "senza selezione non si stacca niente")
        in_due = dict(self.STATO, chiavi=list(self.STATO["chiavi"]) + [
            {"nome": "mirto-su-pc", "cliente": "Mirto su PC", "quando": 1792586820}])
        righe = self.righe(in_due)
        self.assertTrue(si_puo_staccare(righe, "mirto-su-mac"), "con due, uno si può staccare")

    def test_each_question_says_what_really_happens(self):
        from mirto.actions.pannello_abbinamenti import domanda

        righe = self.righe()
        chiave = domanda(righe[-1])
        self.assertIn("nemmeno dopo una reinstallazione", chiave, "è definitivo, e va detto")
        self.assertIn("non si toccano", chiave, "e va detto anche cosa NON succede")
        mio = domanda(righe[0])
        self.assertIn("questo</b> computer", mio)
        self.assertIn("PIN nuovo", mio)

    def test_the_note_points_at_the_button_instead_of_a_dangerous_command(self):
        from mirto.actions.pannello_abbinamenti import nota

        testo = nota(self.STATO, self.righe())
        self.assertIn("Stacca", testo, "la strada è il pulsante che sta lì, non un comando")
        self.assertIn("--revoca installazione", testo)
        self.assertNotIn("--revoca-tutti", testo,
                         "quello stacca TUTTO, compreso questo computer: non si suggerisce a chi "
                         "voleva togliere solo la chiave dell'installazione")

    def test_a_node_without_the_installation_key_says_the_other_thing(self):
        from mirto.actions.pannello_abbinamenti import nota

        pulito = dict(self.STATO, chiave_installazione=False)
        testo = nota(pulito, self.righe(pulito))
        self.assertIn("Un solo computer", testo)
        self.assertEqual(len(self.righe(pulito)), 1, "ritirata, la riga sparisce")


class DiagnosiNodoTest(unittest.TestCase):
    """Da venti campi di `/stato` a un elenco di problemi col rimedio, per chi non ha un terminale.

    È la parte del pannello di stato che si può provare davvero: la finestra qui non gira, questa sì.
    """

    # L'API la si prende da Mirto, non si scrive a mano: un nodo «in ordine» è un nodo aggiornato,
    # e il 22/10 alzare l'API a 1.6 ha fatto diventare rosse nove prove che parlavano di tutt'altro.
    INFO = {"nome": "Balcone", "modello": "Raspberry Pi 4 Model B", "versione": "0.7.1",
            "versione_api": API_ATTESA, "camere": [], "azioni": []}
    STATO = {"nome": "Balcone", "versione": "0.7.1", "attivo_da_s": 93600, "temperatura_c": 46.2,
             "host": "mirto-balcone", "indirizzi": ["192.168.2.21"],
             "camere": [{"nome": "cam", "in_linea": True}],
             "registrazione": {"attiva": True, "registra": True, "errore": "", "disco": "/mnt/mirto-reg",
                               "usato": 120_000_000_000, "libero": 390_000_000_000,
                               "totale": 512_000_000_000},
             "audio": {"uscita": "bluetooth", "uscita_in_uso": "bluetooth", "collegata": True,
                       "abbinata": True, "nome": "JBL Flip 6"},
             "abbinamento": {"aperto": False, "abbinato": True, "chiavi": 2,
                             "chiave_installazione": False, "da": "Mirto su mac-di-marco"}}
    CAMERA = {"attiva": True, "vista_dal_sistema": True, "sensore": "imx219"}

    def guai(self, stato=None, camera=None, info=None):
        from mirto.actions.diagnosi import diagnosi

        return diagnosi(info or self.INFO, stato or self.STATO, self.CAMERA if camera is None else camera)

    def test_a_node_in_order_says_nothing(self):
        self.assertEqual(self.guai(), [], "nessun problema vuol dire elenco vuoto, non un messaggio finto")

    def test_the_cable_that_came_loose_is_named_with_its_remedy(self):
        """Il difetto vero del 20/10: camera accesa in configurazione, sensore non visto dal sistema."""
        guai = self.guai(camera={"attiva": True, "vista_dal_sistema": False, "sensore": ""})
        self.assertEqual(guai[0]["livello"], "errore")
        self.assertIn("non la vede", guai[0]["testo"])
        self.assertIn("cavo piatto", guai[0]["rimedio"], "il rimedio è quello che ha funzionato davvero")

    def test_a_disk_that_was_unplugged_is_the_first_thing_said(self):
        stato = dict(self.STATO, registrazione=dict(self.STATO["registrazione"],
                                                    errore="disco non collegato", registra=False))
        guai = self.guai(stato)
        self.assertEqual(guai[0]["livello"], "errore")
        self.assertIn("disco non collegato", guai[0]["testo"])
        self.assertIn("ricollegalo", guai[0]["rimedio"])

    def test_recording_on_the_microsd_is_worth_saying(self):
        stato = dict(self.STATO, registrazione=dict(self.STATO["registrazione"], disco=""))
        livelli = [(g["livello"], g["testo"]) for g in self.guai(stato)]
        self.assertEqual(len(livelli), 1)
        self.assertEqual(livelli[0][0], "avviso", "funziona, ma tiene tre ore invece di giorni")
        self.assertIn("microSD", livelli[0][1])

    def test_a_node_with_no_speaker_yet_is_told_what_to_choose(self):
        """Lo stato di un nodo appena nato dall'immagine: uscita Bluetooth e nessuna cassa.

        Visto il 21/10 sul primo nodo fatto con la candidata. Non è rotto niente: manca una scelta, e
        il rimedio non è «accendi la cassa» ma «scegli l'uscita».
        """
        stato = dict(self.STATO, audio={"uscita": "bluetooth", "uscita_in_uso": "bluetooth",
                                        "cassa": "non configurata", "configurata": False,
                                        "collegata": False})
        guai = self.guai(stato)
        self.assertEqual([g["livello"] for g in guai], ["avviso"], "non è un guasto, è una cosa da fare")
        self.assertIn("nessuna cassa", guai[0]["testo"])
        self.assertIn("Configurazione → Audio", guai[0]["rimedio"])
        self.assertIn("jack", guai[0]["rimedio"], "e la via d'uscita più semplice è un'altra uscita")

    def test_an_old_node_that_does_not_say_it_still_gets_an_answer(self):
        """Un nodo prima di questa versione non manda «configurata»: resta il messaggio generico."""
        stato = dict(self.STATO, audio={"uscita": "bluetooth", "cassa": "non configurata",
                                        "collegata": False})
        guai = self.guai(stato)
        self.assertEqual([g["livello"] for g in guai], ["errore"])
        self.assertIn("non è collegata", guai[0]["testo"])

    def test_a_speaker_that_lost_its_bond_is_an_error_not_a_warning(self):
        stato = dict(self.STATO, audio=dict(self.STATO["audio"], abbinata=False, collegata=False))
        guai = self.guai(stato)
        self.assertEqual(guai[0]["livello"], "errore")
        self.assertIn("bluetoothctl remove", guai[0]["rimedio"], "il rimedio che serve davvero")
        self.assertEqual(len(guai), 1, "una cosa sola: l'abbinamento perso spiega anche il non collegata")

    def test_the_installation_key_and_an_open_window_are_warnings(self):
        stato = dict(self.STATO, abbinamento=dict(self.STATO["abbinamento"], chiave_installazione=True,
                                                  ritiro_installazione_tra_s=172800, aperto=True))
        testi = [g["testo"] for g in self.guai(stato)]
        self.assertTrue(all(g["livello"] == "avviso" for g in self.guai(stato)))
        self.assertTrue(any("chiave dell'installazione" in t for t in testi), testi)
        self.assertTrue(any("aperto" in t for t in testi), testi)
        self.assertIn("2 giorni", " ".join(g["rimedio"] for g in self.guai(stato)))

    def test_heat_is_a_warning_then_an_error(self):
        caldo = self.guai(dict(self.STATO, temperatura_c=72))
        bollente = self.guai(dict(self.STATO, temperatura_c=84))
        self.assertEqual([g["livello"] for g in caldo], ["avviso"])
        self.assertEqual([g["livello"] for g in bollente], ["errore"])
        self.assertIn("84", bollente[0]["testo"])

    def test_how_the_node_is_attached_is_said_in_words(self):
        """Cavo o Wi-Fi, chiesto da Marco il 22/10: in Sistema non si vedeva."""
        from mirto.actions.diagnosi import rete_in_parole

        self.assertEqual(rete_in_parole({"tipo": "cavo"}), "cavo")
        self.assertEqual(rete_in_parole({"tipo": "wifi", "rete": "CasaMia", "segnale": 68}),
                         "Wi-Fi «CasaMia» · segnale 68 %")
        self.assertEqual(rete_in_parole({"tipo": "wifi", "rete": "CasaMia", "segnale": None}),
                         "Wi-Fi «CasaMia»", "senza segnale si dice comunque dove sta attaccato")
        self.assertEqual(rete_in_parole({"tipo": "wifi"}), "Wi-Fi")
        for niente in ({}, None, {"tipo": ""}, {"tipo": "sconosciuto"}):
            self.assertEqual(rete_in_parole(niente), "",
                             "un nodo che non lo dice (fino alla 0.7.2) non deve sembrare guasto")

    def test_a_node_on_the_cable_is_asked_about_wifi_while_it_can_still_be_reached(self):
        """Il 14/11 un nodo senza Wi-Fi è sparito, e per riprenderlo è servito il cavo e un terminale.

        La finestra del Wi-Fi c'era già ed era perfino scritta per questo caso, ma viveva dopo
        l'abbinamento e si apriva solo su un nodo che risponde: il momento in cui serve era l'unico in
        cui non si poteva più fare. Ora la domanda si fa alla fine dell'abbinamento, finché il cavo
        c'è ancora.
        """
        from mirto.actions.diagnosi import serve_mettere_il_wifi

        self.assertTrue(serve_mettere_il_wifi({"tipo": "cavo"}),
                        "sul cavo si chiede: staccarlo farebbe sparire il nodo")
        self.assertTrue(serve_mettere_il_wifi({"tipo": "cavo", "riserva": "wifi"}),
                        "una scheda Wi-Fi accesa non vuol dire attaccata a una rete: si chiede lo stesso")
        self.assertFalse(serve_mettere_il_wifi({"tipo": "wifi", "rete": "CasaMia", "segnale": 68}),
                         "chi è già in Wi-Fi non deve vedere niente di nuovo")
        for incerto in ({}, None, {"tipo": ""}, {"tipo": "sconosciuto"}, {"riserva": "wifi"}):
            self.assertFalse(serve_mettere_il_wifi(incerto),
                             "su un dato che non abbiamo non si assilla nessuno")

    def test_a_weak_wifi_is_a_warning_and_the_remedy_is_a_different_place(self):
        guai = self.guai(dict(self.STATO, rete={"tipo": "wifi", "rete": "CasaMia", "segnale": 22}))
        self.assertEqual([g["livello"] for g in guai], ["avviso"])
        self.assertIn("22", guai[0]["testo"])
        self.assertIn("segnale debole", guai[0]["testo"])
        self.assertIn("Spostalo", guai[0]["rimedio"], "il rimedio è spostare il nodo, non un'impostazione")

    def test_a_good_signal_a_cable_and_a_silent_node_are_not_problems(self):
        for rete in ({"tipo": "wifi", "rete": "CasaMia", "segnale": 74},
                     {"tipo": "wifi", "rete": "CasaMia", "segnale": None},
                     {"tipo": "cavo", "riserva": "wifi"},
                     {"tipo": ""}, {}):
            self.assertEqual(self.guai(dict(self.STATO, rete=rete)), [], rete)

    def test_the_status_rows_say_how_it_is_attached_without_an_empty_tail(self):
        from mirto.actions.diagnosi import righe_stato

        rete = {"tipo": "wifi", "rete": "CasaMia", "segnale": 68}
        righe = dict(righe_stato(self.INFO, dict(self.STATO, rete=rete)))
        self.assertIn("192.168.2.21", righe["In rete"])
        self.assertIn("Wi-Fi «CasaMia»", righe["In rete"])
        vecchio = dict(righe_stato(self.INFO, self.STATO))
        self.assertTrue(vecchio["In rete"].endswith("192.168.2.21"),
                        "un nodo che non lo dice non deve lasciare un separatore a vuoto")

    def test_an_old_node_is_told_how_to_update(self):
        """Il rimedio e' il pulsante, non il terminale.

        Fino al 25/10 diceva «sudo ./node/install.sh»: era vero finche' il programma del nodo si
        copiava a mano. Da N-02 lo manda Mirto, e lasciare li' il comando vecchio manderebbe ad
        aprire una sessione ssh per una cosa che si fa in un clic.
        """
        guai = self.guai(info=dict(self.INFO, versione_api=1.3))
        self.assertEqual(guai[0]["livello"], "avviso")
        self.assertIn("Aggiorna il nodo", guai[0]["rimedio"])
        self.assertNotIn("install.sh", guai[0]["rimedio"])
        self.assertNotIn("sudo", guai[0]["rimedio"])

    def test_a_node_that_answers_almost_nothing_does_not_invent_problems(self):
        """Un nodo vecchio risponde con metà dei campi: non si deve diagnosticare il vuoto."""
        from mirto.actions.diagnosi import diagnosi, righe_stato

        self.assertEqual(diagnosi({}, {}), [])
        self.assertEqual(diagnosi({}, {"nome": "Nodo"}, None), [])
        righe = dict(righe_stato({}, {"nome": "Nodo"}))
        self.assertEqual(righe["Nome"], "Nodo")

    def test_the_report_can_be_pasted_in_a_message_and_has_no_secrets(self):
        from mirto.actions.diagnosi import rapporto

        # al nodo si chiede /stato e /info, che portano anche roba che non deve essere incollata in
        # un messaggio: qui se ne mette dentro un po' apposta, e si pretende che non esca
        stato = dict(self.STATO, registrazione=dict(self.STATO["registrazione"], disco=""))
        stato["abbinamento"] = dict(stato["abbinamento"], aperto=True, pin="481516")
        stato["camere"] = [{"nome": "cam", "in_linea": True, "utente": "mirto",
                            "password": "segretissima"}]
        info = dict(self.INFO, camere=[{"nome": "cam", "password": "segretissima"}])
        testo = rapporto(info, stato, self.CAMERA, quando=1792568000)
        self.assertIn("Balcone", testo)
        self.assertIn("Raspberry Pi 4", testo)
        self.assertIn("nodo 0.7.1", testo)
        self.assertIn("192.168.2.21", testo)
        self.assertIn("21/10/2026", testo, "la data: serve a capire quando è stato preso")
        self.assertIn("microSD", testo)
        self.assertNotIn("segretissima", testo, "la password della camera non si incolla da nessuna parte")
        self.assertNotIn("481516", testo, "e nemmeno il PIN dell'abbinamento in corso")
        self.assertNotRegex(testo, r"\b[0-9]{6}\b", "niente che somigli a un PIN")
        self.assertNotRegex(testo, r"\b[0-9a-f]{32,}\b", "niente che somigli a una chiave")

    def test_the_report_says_when_there_is_nothing_wrong(self):
        from mirto.actions.diagnosi import rapporto

        testo = rapporto(self.INFO, self.STATO, self.CAMERA)
        self.assertIn("Nessun problema", testo)
        self.assertNotIn("[errore]", testo)

    def test_how_long_it_has_been_on_is_said_like_a_person_would(self):
        from mirto.actions.diagnosi import da_quanto

        self.assertEqual(da_quanto(45), "45 secondi")
        self.assertEqual(da_quanto(600), "10 minuti")
        self.assertEqual(da_quanto(3600), "un'ora")
        self.assertEqual(da_quanto(25000), "6 ore")
        self.assertEqual(da_quanto(93600), "un giorno", "26 ore si dicono «un giorno», non «26 ore»")
        self.assertEqual(da_quanto(260000), "3 giorni")
        self.assertEqual(da_quanto(None), "0 secondi")


class ManopoleDelPannelloTest(unittest.TestCase):
    """Quali manopole mostrare, e come chiamarle (22/10, prima cassa USB e primo microfono USB)."""

    SCHEDE = {
        "schede": {
            "Device": {"nome": "USB PnP Sound Device", "suona": False, "registra": True,
                       "manopole": [{"nome": "Mic", "indice": 0, "volume": True, "interruttore": True,
                                     "percento": 100, "direzione": "cattura"},
                                    {"nome": "Auto Gain Control", "indice": 0, "volume": False,
                                     "interruttore": True, "acceso": False, "direzione": "riproduzione"}]},
            "UACDemoV10": {"nome": "UACDemoV1.0", "suona": True, "registra": False,
                           "manopole": [{"nome": "PCM", "indice": 0, "volume": True,
                                         "interruttore": True, "percento": 90, "direzione": "riproduzione"}]},
            "vc4hdmi0": {"nome": "vc4-hdmi-0", "suona": True, "registra": False,
                         "manopole": [{"nome": "PCM", "indice": 0, "volume": True, "percento": 50}]},
        }
    }

    def stato(self, **extra) -> dict:
        base = {"uscita": "usb", "scheda": "UACDemoV10",
                "microfono": {"attivo": True, "scheda": "Device"}}
        base.update(extra)
        return base

    def test_only_the_two_cards_in_use_are_shown(self):
        from mirto.actions.pannello_audio import manopole_da_mostrare

        gruppi = manopole_da_mostrare(self.SCHEDE, self.stato())
        self.assertEqual([g["scheda"] for g in gruppi], ["UACDemoV10", "Device"])
        self.assertNotIn("vc4hdmi0", [g["scheda"] for g in gruppi],
                         "due HDMI e un jack che nessuno usa sarebbero cursori per niente")

    def test_bluetooth_has_no_knobs_here(self):
        """Il volume di una cassa Bluetooth sta sulla cassa: bluealsa non espone niente da girare."""
        from mirto.actions.pannello_audio import manopole_da_mostrare

        gruppi = manopole_da_mostrare(self.SCHEDE, self.stato(uscita="bluetooth", scheda=""))
        self.assertEqual([g["scheda"] for g in gruppi], ["Device"])

    def test_a_microphone_switched_off_brings_no_knobs(self):
        from mirto.actions.pannello_audio import manopole_da_mostrare

        gruppi = manopole_da_mostrare(self.SCHEDE, self.stato(microfono={"attivo": False, "scheda": "Device"}))
        self.assertEqual([g["scheda"] for g in gruppi], ["UACDemoV10"])

    def test_one_card_doing_both_jobs_is_one_group(self):
        from mirto.actions.pannello_audio import manopole_da_mostrare

        gruppi = manopole_da_mostrare(self.SCHEDE, self.stato(scheda="Device"))
        self.assertEqual(len(gruppi), 1)
        self.assertIn("e", gruppi[0]["titolo"], "il titolo dice che fa entrambi i mestieri")

    def test_a_card_that_does_not_answer_amixer_is_not_invented(self):
        from mirto.actions.pannello_audio import manopole_da_mostrare

        self.assertEqual(manopole_da_mostrare({"schede": {}}, self.stato()), [])
        self.assertEqual(manopole_da_mostrare(None, self.stato()), [])

    def test_the_alsa_name_is_never_lost(self):
        """«PCM» da solo non dice niente; ma è il nome che si trova nelle guide e in amixer."""
        from mirto.actions.pannello_audio import etichetta_manopola

        self.assertIn("PCM", etichetta_manopola({"nome": "PCM"}))
        self.assertIn("Auto Gain Control", etichetta_manopola({"nome": "Auto Gain Control"}))
        self.assertEqual(etichetta_manopola({"nome": "Qualcosa Di Ignoto"}), "Qualcosa Di Ignoto")

    def test_the_key_matches_the_one_the_node_uses(self):
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "node"))
        from mirto_node import mixer

        from mirto.actions.pannello_audio import chiave_manopola

        m = {"nome": "Auto Gain Control", "indice": 0}
        self.assertEqual(chiave_manopola(m), mixer.chiave(m),
                         "due nomi diversi per la stessa manopola e il nodo risponderebbe «non la ho»")

    def test_the_microphone_is_only_sent_when_it_changed(self):
        """Accenderlo riavvia il servizio del nodo: mezzo minuto senza camera, per niente."""
        from mirto.actions.pannello_audio import microfono_da_mandare

        prima = {"attivo": True, "scheda": "Device"}
        self.assertIsNone(microfono_da_mandare(prima, True, "Device"))
        self.assertEqual(microfono_da_mandare(prima, False, "Device"), {"attivo": False, "scheda": "Device"})
        self.assertEqual(microfono_da_mandare(prima, True, "Altra"), {"attivo": True, "scheda": "Altra"})
        self.assertEqual(microfono_da_mandare(None, True, "Device"), {"attivo": True, "scheda": "Device"})


class TabDeiDispositiviTest(unittest.TestCase):
    """Cosa elencano le tab Jack / Bluetooth / USB, e chi è «quello in uso»."""

    DISP = {
        "predefinito": {"uscita": "usb", "scheda": "UACDemoV10", "bluetooth_mac": ""},
        "jack": {"presente": True, "attivo": True, "scheda": "Headphones", "nome": "bcm2835 Headphones"},
        "usb": [{"scheda": "UACDemoV10", "nome": "UACDemoV1.0"}],
        "hdmi": [],
        "bluetooth": {"casse": [{"mac": "AA:BB:CC:DD:EE:FF", "nome": "JBL Flip 6", "collegata": True}],
                      "trovate": {"casse": [{"mac": "AA:BB:CC:DD:EE:FF", "nome": "JBL Flip 6", "abbinata": True},
                                            {"mac": "11:22:33:44:55:66", "nome": "Cassa nuova", "abbinata": False}]}},
    }

    def test_the_box_at_the_bottom_talks_about_the_tab_you_are_looking_at(self):
        """La scheda aperta decide il dispositivo, non una tendina che elenca tutto sotto a tutto.

        Marco, 7/11: *«è fuorviante vedere 3 scelte sui 3 tab in ogni scheda dei tab»*; l'8/11,
        provando: *«vorrei fosse statica»*. Qui c'era `si_puo_provare`, la regola che diceva quando
        il din-don aveva senso — serviva perché il nodo suonava solo dall'uscita sua. Dall'API 1.14
        la prova dice da dove, e la regola non ha più niente da regolare.
        """
        from mirto.actions.pannello_audio import uscita_della_scheda

        self.assertEqual(uscita_della_scheda("jack", self.DISP)["chiave"], "jack:Headphones")
        self.assertEqual(uscita_della_scheda("bluetooth", self.DISP, mac="AA:BB:CC:DD:EE:FF")["chiave"],
                         "bluetooth:AA:BB:CC:DD:EE:FF")
        self.assertEqual(uscita_della_scheda("usb", self.DISP, card="UACDemoV10")["chiave"],
                         "usb:UACDemoV10")
        self.assertEqual(uscita_della_scheda("bluetooth", self.DISP), {},
                         "nessuna cassa scelta: non si inventa quella che capita")
        self.assertEqual(uscita_della_scheda("microfono", self.DISP), {},
                         "il microfono non è un'uscita: il riquadro sparisce invece di parlare d'altro")
        self.assertEqual(uscita_della_scheda("bluetooth", self.DISP, mac="00:00:00:00:00:00"), {},
                         "una cassa che il nodo non ricorda più non è un dispositivo da provare")

    def test_the_two_lists_are_two_lists(self):
        """Dall'8/11 le ricordate e le trovate non stanno piu' insieme: due elenchi, due azioni."""
        from mirto.actions.pannello_audio import casse_ricordate, casse_trovate

        bt = dict(self.DISP["bluetooth"])
        bt["trovate"] = {"quando": 1000, **bt["trovate"]}
        ricordate = casse_ricordate(bt)
        self.assertEqual([c["mac"] for c in ricordate], ["AA:BB:CC:DD:EE:FF"])
        self.assertTrue(ricordate[0]["collegata"])
        trovate = casse_trovate(bt, da_quando=999)
        self.assertEqual([c["mac"] for c in trovate], ["11:22:33:44:55:66"],
                         "una cassa gia' ricordata non si ripropone da abbinare")

    def test_a_search_older_than_the_window_is_not_shown(self):
        """L'elenco nasce vuoto: il file del nodo tiene l'ultima ricerca, anche di giorni fa.

        E' il difetto dell'8/11: aprendo il programma si vedeva la propria cassa «da abbinare»
        mentre non era in abbinamento, e premendo «Cerca» per qualche secondo non cambiava niente —
        si stava guardando il file vecchio.
        """
        from mirto.actions.pannello_audio import casse_trovate

        bt = dict(self.DISP["bluetooth"])
        bt["trovate"] = {"quando": 1000, **bt["trovate"]}
        self.assertEqual(casse_trovate(bt, da_quando=1001), [], "ricerca piu' vecchia della finestra")
        self.assertEqual(casse_trovate(bt, da_quando=1000), [{"mac": "11:22:33:44:55:66",
                                                              "nome": "Cassa nuova",
                                                              "abbinata": False, "collegata": False}])
        self.assertEqual(casse_trovate({"casse": []}, da_quando=0), [], "nessuna ricerca: niente")

    def test_the_wire_comes_before_the_radio(self):
        """Marco, 8/11: «la cassa principale deve prediligere connessioni sicure come jack/usb».

        Non e' un gusto: una cassa Bluetooth si scollega da sola, perde l'abbinamento quando qualcuno
        la rimette in abbinamento, e il 21/10 ha tenuto fermo un nodo per cinque ore. L'uscita di una
        sirena e' l'ultimo posto dove mettere la cosa che cade piu' spesso.
        """
        from mirto.actions.pannello_audio import uscite_per_le_azioni

        chiavi = [u["chiave"] for u in uscite_per_le_azioni(self.DISP)]
        self.assertEqual(chiavi, ["jack:Headphones", "usb:UACDemoV10", "bluetooth:AA:BB:CC:DD:EE:FF"])
        self.assertLess(chiavi.index("usb:UACDemoV10"), chiavi.index("bluetooth:AA:BB:CC:DD:EE:FF"))

    def test_a_speaker_not_yet_paired_is_not_an_output(self):
        from mirto.actions.pannello_audio import uscite_per_le_azioni

        chiavi = [u["chiave"] for u in uscite_per_le_azioni(self.DISP)]
        self.assertNotIn("bluetooth:11:22:33:44:55:66", chiavi, "prima si abbina, poi ci si suona")

    def test_a_jack_switched_off_is_not_offered(self):
        """Spegnerlo vuol dire «lì non c'è attaccato niente»: proporlo sarebbe offrire un posto muto."""
        from mirto.actions.pannello_audio import uscite_per_le_azioni

        spento = dict(self.DISP, jack=dict(self.DISP["jack"], attivo=False))
        self.assertNotIn("jack:Headphones", [u["chiave"] for u in uscite_per_le_azioni(spento)])

    def test_which_one_the_node_is_using(self):
        from mirto.actions.pannello_audio import uscita_in_uso, uscite_per_le_azioni

        self.assertEqual(uscita_in_uso(self.DISP), "usb:UACDemoV10")
        self.assertIn(uscita_in_uso(self.DISP), [u["chiave"] for u in uscite_per_le_azioni(self.DISP)],
                      "quello in uso dev'essere uno di quelli elencati, o nessun pulsante risulterebbe scelto")
        bt = dict(self.DISP, predefinito={"uscita": "bluetooth", "scheda": "", "bluetooth_mac": "AA:BB:CC:DD:EE:FF"})
        self.assertEqual(uscita_in_uso(bt), "bluetooth:AA:BB:CC:DD:EE:FF")
        self.assertEqual(uscita_in_uso({}), "")


class LivelloDelMicrofonoTest(unittest.TestCase):
    """Il misuratore del microfono: leggere il livello e dire se il guadagno va bene.

    Le soglie vengono dalle misure sul Raspberry del 22/10, non da un'idea: col guadagno di fabbrica
    una voce stava all'1 % del fondo scala, col guadagno al massimo e l'automatismo spento al 38 %, e
    un colpo di mani a due metri al 50 %.
    """

    @staticmethod
    def pcm(valori) -> bytes:
        import array

        return array.array("h", valori).tobytes()

    def test_peak_and_mean_are_read_from_the_audio(self):
        from mirto.actions.pannello_audio import livello_da_pcm

        media, picco = livello_da_pcm(self.pcm([0, 16384, 0, -16384] * 100))
        self.assertAlmostEqual(picco, 0.5, places=2)
        self.assertAlmostEqual(media, 0.3536, places=2, msg="RMS di un'onda quadra a metà scala")
        self.assertEqual(livello_da_pcm(b""), (0.0, 0.0))
        self.assertEqual(livello_da_pcm(b"\x00"), (0.0, 0.0), "un byte spaiato non deve far saltare niente")

    def test_a_clap_moves_the_peak_and_almost_not_the_mean(self):
        """È il motivo per cui si guardano tutti e due: la media da sola non vede un colpo secco."""
        from mirto.actions.pannello_audio import livello_da_pcm

        media, picco = livello_da_pcm(self.pcm([0] * 1590 + [30000] * 10))
        self.assertGreater(picco, 0.9)
        self.assertLess(media, 0.1)

    def test_what_the_meter_says(self):
        from mirto.actions.pannello_audio import giudizio_livello

        self.assertEqual(giudizio_livello(0.001)[0], "silenzio")
        self.assertEqual(giudizio_livello(0.01)[0], "piano", "il guadagno di fabbrica di quel microfono")
        self.assertEqual(giudizio_livello(0.38)[0], "bene", "la voce con l'automatismo spento")
        self.assertEqual(giudizio_livello(0.50)[0], "bene", "il colpo di mani a due metri")
        self.assertEqual(giudizio_livello(0.90)[0], "forte")
        self.assertEqual(giudizio_livello(0.99)[0], "saturo")

    def test_silence_never_sends_you_to_raise_the_gain(self):
        """Fra una parola e l'altra il livello è zero: un misuratore che dicesse «alza» sbaglierebbe."""
        from mirto.actions.pannello_audio import giudizio_livello

        esito, consiglio = giudizio_livello(0.0)
        self.assertEqual(esito, "silenzio")
        self.assertNotIn("alza", consiglio.lower())

    def test_the_peak_rises_at_once_and_falls_only_later(self):
        """Il picco tenuto: una funzione sola per il pannello che regola il guadagno e per la riga di
        *Sistema* che lo giudica. Due copie darebbero due giudizi dello stesso microfono."""
        from mirto.actions.pannello_audio import PICCO_TENUTO, picco_tenuto

        tenuto, quando = picco_tenuto(0.38, 0.0, 0.0, 100.0)
        self.assertEqual((tenuto, quando), (0.38, 100.0), "sale subito")
        # Subito dopo, silenzio fra due parole: il picco non si muove.
        self.assertEqual(picco_tenuto(0.0, tenuto, quando, 101.0), (0.38, 100.0))
        # Passata la tenuta, scende a quello che c'e' adesso.
        self.assertEqual(picco_tenuto(0.02, tenuto, quando, 100.0 + PICCO_TENUTO + 0.1),
                         (0.02, 100.0 + PICCO_TENUTO + 0.1))


class TestoIncolonnatoTest(unittest.TestCase):
    def test_the_values_all_start_at_the_same_column(self):
        from mirto.actions.diagnosi import testo_console

        testo = testo_console([("Nome", "Ingresso"), ("Modello", "Raspberry Pi 4"), ("In rete", "cavo")])
        colonne = {riga.index(riga.split("   ")[-1]) for riga in testo.splitlines()}
        self.assertEqual(len(colonne), 1)
        self.assertIn("Nome      Ingresso", testo)

    def test_nothing_to_show_is_not_a_crash(self):
        from mirto.actions.diagnosi import testo_console

        self.assertEqual(testo_console([]), "")


class ColonneDellaGrigliaTest(unittest.TestCase):
    """Quante colonne di box ci stanno: fra N box ci sono N-1 spazi, non N."""

    def test_how_many_boxes_fit(self):
        from mirto.gui.sistema import colonne_griglia

        self.assertEqual(colonne_griglia(624, 148, 8), 4, "quattro da 148 con tre spazi da 8: 616")
        self.assertEqual(colonne_griglia(615, 148, 8), 3, "un pixel in meno e la quarta non ci sta")
        self.assertEqual(colonne_griglia(300, 148, 8), 1, "due da 148 vogliono 304")
        self.assertEqual(colonne_griglia(10_000, 148, 8), 4, "quattro è il massimo chiesto")
        self.assertEqual(colonne_griglia(0, 148, 8), 1, "prima che la finestra abbia una larghezza")
        self.assertEqual(colonne_griglia(600, 0, 8), 1, "box senza larghezza: non si divide per zero")


class PannelloAzioneTest(unittest.TestCase):
    """Le regole della finestra «Azione del nodo»: sorgente, obiettivi, camere, aiuti."""

    OPZIONI = [("person", "Persona"), ("cat", "Gatto"), ("sound:glass", "Vetro rotto"),
               ("sound:dog", "Cane che abbaia")]

    def test_where_a_trigger_comes_from(self):
        from mirto.actions.pannello_azione import AUDIO, ENTRAMBI, VIDEO, sorgente_da_obiettivi, sorgente_di

        self.assertEqual(sorgente_di("cat"), VIDEO)
        self.assertEqual(sorgente_di("sound:glass"), AUDIO)
        self.assertEqual(sorgente_da_obiettivi(["person", "cat"]), VIDEO)
        self.assertEqual(sorgente_da_obiettivi(["sound:glass"]), AUDIO)
        self.assertEqual(sorgente_da_obiettivi(["person", "sound:glass"]), ENTRAMBI)

    def test_a_new_action_starts_from_the_camera(self):
        """Senza obiettivi non vuol dire «nessuna sorgente», vuol dire «non ancora scelto»."""
        from mirto.actions.pannello_azione import VIDEO, sorgente_da_obiettivi

        self.assertEqual(sorgente_da_obiettivi([]), VIDEO)
        self.assertEqual(sorgente_da_obiettivi(None), VIDEO)

    def test_the_source_decides_what_is_shown(self):
        from mirto.actions.pannello_azione import AUDIO, ENTRAMBI, VIDEO, obiettivi_per_sorgente

        self.assertEqual([k for k, _t in obiettivi_per_sorgente(self.OPZIONI, VIDEO)], ["person", "cat"])
        self.assertEqual([k for k, _t in obiettivi_per_sorgente(self.OPZIONI, AUDIO)],
                         ["sound:glass", "sound:dog"])
        self.assertEqual(len(obiettivi_per_sorgente(self.OPZIONI, ENTRAMBI)), 4)

    def test_changing_source_drops_what_that_source_cannot_trigger(self):
        """Un obiettivo che la finestra non mostra più ma che fa ancora scattare l'azione è il peggio
        dei due mondi: invisibile e vivo."""
        from mirto.actions.pannello_azione import AUDIO, VIDEO, obiettivi_da_tenere

        scelti = {"cat", "sound:glass"}
        self.assertEqual(obiettivi_da_tenere(scelti, VIDEO), {"cat"})
        self.assertEqual(obiettivi_da_tenere(scelti, AUDIO), {"sound:glass"})

    def test_cameras_make_no_sense_for_a_sound_only_action(self):
        from mirto.actions.pannello_azione import AUDIO, ENTRAMBI, VIDEO, serve_scegliere_le_camere

        self.assertTrue(serve_scegliere_le_camere(VIDEO))
        self.assertTrue(serve_scegliere_le_camere(ENTRAMBI))
        self.assertFalse(serve_scegliere_le_camere(AUDIO))

    def test_only_cameras_that_are_switched_on(self):
        """Offrire una camera spenta vuol dire far configurare un'azione che non scatterà mai."""
        from mirto.actions.pannello_azione import camere_da_mostrare

        camere = [{"name": "Ingresso", "enabled": True}, {"name": "iPhone di Marco", "enabled": False}]
        self.assertEqual([c["nome"] for c in camere_da_mostrare(camere, [])], ["Ingresso"])

    def test_a_camera_already_chosen_is_not_dropped_in_silence(self):
        from mirto.actions.pannello_azione import camere_da_mostrare

        camere = [{"name": "Ingresso", "enabled": True}, {"name": "Garage", "enabled": False}]
        mostrate = camere_da_mostrare(camere, ["Garage"])
        self.assertEqual([(c["nome"], c["accesa"]) for c in mostrate], [("Ingresso", True), ("Garage", False)])
        sparita = camere_da_mostrare(camere, ["Cancello"])
        self.assertIn(("Cancello", False), [(c["nome"], c["accesa"]) for c in sparita],
                      "scelta e poi sparita dal computer: si vede lo stesso, per poterla togliere")

    def test_cameras_and_microphones_are_two_lists_and_one_field(self):
        """Un evento audio porta il nome del microfono dove un evento video porta quello della camera:
        il filtro guarda il nome, quindi non serve un campo nuovo — serve la lista giusta."""
        from mirto.actions.pannello_azione import fonti_da_mostrare

        camere = [{"name": "Ingresso", "enabled": True}]
        microfoni = [{"id": "mac", "name": "Microfono del Mac"}, {"id": "n1", "name": "Ingresso · mic"}]
        viste, sentite = fonti_da_mostrare(camere, microfoni, ["Ingresso", "Microfono del Mac"])
        self.assertEqual([c["nome"] for c in viste], ["Ingresso"])
        self.assertEqual([m["nome"] for m in sentite], ["Microfono del Mac", "Ingresso · mic"])

    def test_a_vanished_choice_shows_up_once_not_twice(self):
        """Comparire in tutte e due le liste vorrebbe dire salvarla due volte."""
        from mirto.actions.pannello_azione import fonti_da_mostrare

        viste, sentite = fonti_da_mostrare([], [{"id": "mac", "name": "Microfono del Mac"}], ["Sparita"])
        nomi = [c["nome"] for c in viste] + [m["nome"] for m in sentite]
        self.assertEqual(nomi.count("Sparita"), 1)
        self.assertIn("Sparita", [c["nome"] for c in viste], "le sconosciute restano dove stavano prima")

    def test_every_field_of_the_window_has_a_help_text(self):
        from mirto.actions.pannello_azione import aiuto, chiavi_aiuto

        attesi = {"nome", "tipo", "comando", "quando_parte", "ritardo", "attesa_conferma",
                  "se_nessuno_conferma", "obiettivi_camera", "obiettivi_microfono",
                  "suoni_emessi", "pausa", "massimi", "cosa_fa", "quando_attiva",
                  "fascia", "avvisa", "camere", "microfoni", "attiva"}
        self.assertEqual(set(chiavi_aiuto()), attesi)
        for chiave in attesi:
            self.assertGreater(len(aiuto(chiave)), 40, f"l'aiuto di «{chiave}» non spiega niente")
        self.assertEqual(aiuto("inventata"), "", "un campo senza aiuto non fa esplodere la finestra")


class CatenaDiPassiTest(unittest.TestCase):
    """Un'azione è una catena, non un comando solo (22/10).

    Tutto qui dentro gira senza thread, senza rete e senza orologio: «srotola» dice esattamente quali
    chiamate si faranno e in che ordine, quindi la parte in cui si sbaglia — ordine, ripetizioni,
    attese — si prova in un millesimo di secondo.
    """

    def test_an_old_action_is_a_chain_of_one(self):
        """Niente da convertire sul disco: una configurazione vecchia resta valida per sempre."""
        from mirto.actions.passi import passi_di

        passi = passi_di({"action": "sirena", "duration": 30})
        self.assertEqual(len(passi), 1)
        self.assertEqual((passi[0].comando, passi[0].durata, passi[0].ripeti), ("sirena", 30, 1))
        self.assertEqual(passi_di({}), [], "un'azione senza comando non è una catena da un passo vuoto")

    def test_a_deterrent_with_one_sound_keeps_it(self):
        from mirto.actions.passi import passi_di

        passi = passi_di({"action": "dissuasore", "duration": 8, "sounds": ["rapace"]})
        self.assertEqual(passi[0].suono, "rapace")
        a_rotazione = passi_di({"action": "dissuasore", "sounds": ["rapace", "sparo"]})
        self.assertEqual(a_rotazione[0].suono, "", "due suoni vogliono dire «a rotazione»: non se ne sceglie uno")

    def test_the_doorbell_unrolls_in_the_right_order(self):
        """L'esempio di Marco: luce 10 s → aspetta 5 → campanello tre volte."""
        from mirto.actions.passi import Passo, srotola

        catena = srotola([Passo(comando="luce", durata=10, pausa=5),
                          Passo(comando="audio", suono="campanello", durata=2, ripeti=3)])
        from mirto.actions.passi import RESPIRO

        self.assertEqual([(c["comando"], c["durata"], c["attesa_dopo"]) for c in catena],
                         [("luce", 10, 5), ("audio", 2, RESPIRO), ("audio", 2, RESPIRO), ("audio", 2, 0)],
                         "fra due chiamate dello stesso comando ci vuole un respiro, o la seconda "
                         "arriva mentre la prima e\' ancora in corso e il nodo la rifiuta")

    def test_the_two_repetitions_are_two_different_things(self):
        from mirto.actions.passi import Passo, srotola

        dentro = srotola([Passo(comando="audio", ripeti=3)])
        attorno = srotola([Passo(comando="audio")], ripeti_tutto=3)
        self.assertEqual(len(dentro), 3)
        self.assertEqual(len(attorno), 3)
        misto = srotola([Passo(comando="luce"), Passo(comando="audio", ripeti=2)], ripeti_tutto=2)
        self.assertEqual([c["comando"] for c in misto],
                         ["luce", "audio", "audio", "luce", "audio", "audio"],
                         "la ripetizione del passo sta dentro quella della catena, non viceversa")

    def test_the_last_wait_is_dropped(self):
        """Aspettare dopo l'ultima cosa tiene occupata un'azione per niente."""
        from mirto.actions.passi import Passo, quanto_dura, srotola

        catena = srotola([Passo(comando="audio", durata=2, pausa=60)])
        self.assertEqual(catena[-1]["attesa_dopo"], 0)
        self.assertEqual(quanto_dura(catena), 2)

    def test_the_action_duration_fills_the_gaps(self):
        from mirto.actions.passi import Passo, srotola

        catena = srotola([Passo(comando="sirena"), Passo(comando="luce", durata=3)], durata_azione=30)
        self.assertEqual([c["durata"] for c in catena], [30, 3])

    def test_a_step_is_never_shorter_than_the_node_can_do(self):
        """Il nodo tiene accesa una cosa per almeno un secondo: contare meno è contare una bugia.

        Scoperto il 22/10 provando la catena sul nodo finto: ``activate`` fa ``max(1.0, durata)`` sia
        sul nodo vero che sul simulatore. Se qui srotolassimo 0,1 s, la ripetizione successiva
        arriverebbe mentre quel comando è ancora in corso — e il nodo in quel caso **non protesta**:
        risponde «già attiva» e non fa niente. Il campanello suonerebbe una volta invece di tre e
        nessuno vedrebbe un errore.
        """
        from mirto.actions.passi import MINIMO_NODO, Passo, srotola

        catena = srotola([Passo(comando="audio", durata=0.1, ripeti=2)])
        self.assertEqual([c["durata"] for c in catena], [MINIMO_NODO, MINIMO_NODO])
        self.assertGreater(catena[0]["durata"] + catena[0]["attesa_dopo"], MINIMO_NODO,
                           "fra due chiamate dello stesso comando il nodo deve aver fatto in tempo "
                           "a spegnere la prima, o la seconda cade nel vuoto")

    def test_the_action_ceiling_wins_over_the_node_floor(self):
        """Il pavimento non scavalca il tetto: i limiti del tipo di azione restano l'ultima parola."""
        from mirto.actions.passi import Passo, srotola

        catena = srotola([Passo(comando="sirena", durata=300)], durata_azione=30)
        self.assertEqual(catena[0]["durata"], 30, "un passo scritto a mano non scavalca i limiti")
        corta = srotola([Passo(comando="luce", durata=5)], durata_azione=0.5)
        self.assertLessEqual(corta[0]["durata"], 0.5, "e nemmeno il minimo del nodo li scavalca")

    def test_a_chain_cannot_grow_without_end(self):
        """Dodici passi per venti giri sarebbero duecentoquaranta chiamate a un nodo."""
        from mirto.actions.passi import MAX_CHIAMATE, Passo, srotola

        catena = srotola([Passo(comando="audio", ripeti=20)] * 12, ripeti_tutto=20)
        self.assertEqual(len(catena), MAX_CHIAMATE)
        self.assertEqual(catena[-1]["attesa_dopo"], 0, "tagliata, ma non lascia un'attesa appesa")

    def test_how_long_the_siren_sounds_in_a_chain(self):
        """Serve ai limiti: tre minuti ogni quarto d'ora valgono sulla catena, non sul singolo passo."""
        from mirto.actions.passi import Passo, quanto_suona, srotola

        tipi = {"sirena": "siren", "luce": "light", "audio": "deterrent"}
        catena = srotola([Passo(comando="sirena", durata=20, ripeti=3), Passo(comando="luce", durata=60),
                          Passo(comando="audio", durata=5)])
        self.assertEqual(quanto_suona(catena, tipi), 60, "solo la sirena, e tutte e tre le volte")
        self.assertEqual(quanto_suona(catena, {}), 0, "un comando che il nodo non dichiara non è una sirena")

    def test_what_a_chain_cannot_be(self):
        from mirto.actions.passi import MAX_RIPETI, Passo, problemi

        self.assertTrue(problemi([]))
        self.assertFalse(problemi([Passo(comando="sirena", durata=30)]))
        self.assertTrue(problemi([Passo(comando="")]))
        self.assertTrue(problemi([Passo(comando="audio")], ripeti_tutto=MAX_RIPETI + 1))

    def test_what_gets_written_down_is_only_what_was_chosen(self):
        """Un passo semplice resta una riga corta: la configurazione si legge ancora a occhio."""
        from mirto.actions.passi import Passo

        self.assertEqual(Passo(comando="sirena").come_dict(), {"comando": "sirena"})
        self.assertEqual(Passo(comando="audio", suono="campanello", ripeti=3).come_dict(),
                         {"comando": "audio", "suono": "campanello", "ripeti": 3})

    def test_a_chain_knows_how_long_it_lasts(self):
        from mirto.actions.passi import Catena

        catena = Catena.da_azione({"passi": [{"comando": "luce", "durata": 10, "pausa": 5},
                                             {"comando": "audio", "durata": 2, "ripeti": 3}]})
        self.assertEqual(len(catena.chiamate), 4)
        from mirto.actions.passi import RESPIRO

        self.assertEqual(catena.durata, 10 + 5 + 2 + 2 + 2 + 2 * RESPIRO)
        vecchia = Catena.da_azione({"action": "sirena"}, durata_azione=30)
        self.assertEqual(vecchia.durata, 30)


class TettoDeiPassiTest(unittest.TestCase):
    def test_a_step_can_never_outlast_what_the_action_is_allowed(self):
        """Difetto trovato scrivendo il motore: un passo con «durata: 300» chiedeva al nodo trecento
        secondi di sirena, scavalcando il tetto di tre minuti applicato all'azione."""
        from mirto.actions.passi import Passo, srotola

        catena = srotola([Passo(comando="sirena", durata=300)], durata_azione=180)
        self.assertEqual(catena[0]["durata"], 180)

    def test_a_shorter_step_stays_shorter(self):
        from mirto.actions.passi import Passo, srotola

        catena = srotola([Passo(comando="luce", durata=5)], durata_azione=180)
        self.assertEqual(catena[0]["durata"], 5, "il tetto è un massimo, non un valore da imporre")

    def test_each_command_answers_to_its_own_limit(self):
        """In una catena i comandi si mescolano: il tetto segue il comando, non l'azione.

        Finché un'azione faceva una cosa sola bastava il limite del tipo scelto in Mirto. Una luce e
        una sirena nello stesso elenco no: la sirena non può prendersi il tetto della luce, e la luce
        non deve prendersi quello della sirena.
        """
        from mirto.actions.passi import Passo, srotola

        tetti = {"sirena": 180.0, "dissuasore": 30.0}
        catena = srotola([Passo(comando="luce", durata=600), Passo(comando="sirena", durata=600),
                          Passo(comando="dissuasore", durata=600)], durata_azione=900, tetti=tetti)
        self.assertEqual([c["durata"] for c in catena], [600, 180, 30])

    def test_a_tight_command_does_not_shrink_the_steps_after_it(self):
        """Il tetto di un passo è suo: un dissuasore in mezzo non accorcia quello che viene dopo."""
        from mirto.actions.passi import Passo, srotola

        catena = srotola([Passo(comando="dissuasore", durata=600), Passo(comando="luce", durata=600)],
                         durata_azione=900, tetti={"dissuasore": 30.0})
        self.assertEqual([c["durata"] for c in catena], [30, 600])

    def test_the_chain_of_an_old_siren_is_capped(self):
        from mirto.actions.engine import SIREN_MAX_SECONDS, ActionSpec

        spec = ActionSpec.from_dict({"name": "x", "node": "n", "action": "sirena", "kind": "siren",
                                     "duration": 300})
        self.assertEqual(spec.durata_catena(), SIREN_MAX_SECONDS)
        self.assertEqual(spec.catena()[0]["durata"], SIREN_MAX_SECONDS)


class StrisciaDeiPassiTest(unittest.TestCase):
    """La striscia: cosa propone il «+», cosa si legge nei riquadri, cosa manca per provare.

    Il principio da far valere è uno: **i pezzi suggeriscono altri pezzi**. Il «+» non è un menù di
    tutto, è l'elenco corto di quello che ha senso lì, su questo nodo, con quello che a questo nodo è
    attaccato davvero — e quando una cosa non si può fare resta visibile e dice cosa manca.
    """

    COMANDI = [{"nome": "sirena", "tipo": "siren", "descrizione": "Sirena"},
               {"nome": "luci", "tipo": "light", "descrizione": "Luci"},
               {"nome": "uscita_1", "tipo": "generic", "descrizione": "Relè 1"},
               {"nome": "dissuasore", "tipo": "deterrent", "descrizione": "Suoni",
                "suoni": ["falco", "sparo"]}]

    def test_the_plus_only_offers_what_this_node_has(self):
        from mirto.actions.pannello_catena import proposte

        voci = proposte([], [{"nome": "luci", "tipo": "light"}], casse=["jack"])
        self.assertEqual([v.chiave for v in voci], ["comando:luci"],
                         "un nodo con una luce sola non propone sirene che non ha")

    def test_a_node_without_a_speaker_says_so_instead_of_hiding_the_sound(self):
        """Nascondere non insegna niente, e offrire per poi fallire è peggio."""
        from mirto.actions.pannello_catena import proposte

        voci = {v.chiave: v for v in proposte([], self.COMANDI, casse=[])}
        self.assertFalse(voci["suono:sirena:"].si_puo)
        self.assertTrue(voci["suono:sirena:"].manca, "deve dire cosa manca")
        self.assertEqual(voci["suono:sirena:"].scorciatoia, "audio_del_nodo",
                         "e dove si aggiusta")
        self.assertTrue(voci["comando:luci"].si_puo, "una luce va a un morsetto, non a una cassa")
        self.assertTrue(voci["comando:uscita_1"].si_puo)

    def test_a_sound_is_a_sound_not_a_device(self):
        """Marco, due volte: «sirena e dissuasore sono la stessa cosa, un suono».

        Sul nodo sono due dispositivi perche' hanno due limiti diversi, e quei limiti restano. Ma a
        chi configura non si chiede di scegliere un dispositivo: si chiede che suono vuole far
        sentire.
        """
        from mirto.actions.pannello_catena import proposte, suoni_del_nodo

        suoni = suoni_del_nodo(self.COMANDI)
        self.assertEqual([v["nome"] for v in suoni], ["Sirena", "falco", "sparo"],
                         "la sirena e' un suono come gli altri, e i suoi suoni sono i suoi")
        nomi = [v.testo for v in proposte([], self.COMANDI, casse=["jack"])]
        self.assertIn("falco", nomi)
        self.assertNotIn("dissuasore", nomi, "il dispositivo non si vede piu'")
        self.assertIn("luci", nomi, "una luce invece resta un comando: non e' un suono")

    def test_choosing_a_sound_knows_which_command_plays_it(self):
        from mirto.actions.pannello_catena import passo_proposto

        p = passo_proposto("suono:dissuasore:falco", self.COMANDI)
        self.assertEqual((p.comando, p.suono), ("dissuasore", "falco"))
        sirena = passo_proposto("suono:sirena:", self.COMANDI)
        self.assertEqual((sirena.comando, sirena.suono), ("sirena", ""))

    def test_a_sound_box_is_called_by_its_sound(self):
        from mirto.actions.pannello_catena import Passo, riquadri

        box = riquadri([Passo(comando="dissuasore", suono="falco", durata=3)], self.COMANDI)
        self.assertEqual(box[0].titolo, "FALCO", "«FALCO», non «DISSUASORE»")
        luce = riquadri([Passo(comando="luci", durata=3)], self.COMANDI)
        self.assertEqual(luce[0].titolo, "LUCI")

    def test_after_a_light_the_sound_comes_first(self):
        """L'ordine è metà del valore: luce e poi suono è la sequenza che chiede chiunque."""
        from mirto.actions.pannello_catena import ATTESA, Passo, proposte

        voci = proposte([Passo(comando="luci", durata=10)], self.COMANDI, casse=["jack"])
        self.assertEqual(voci[0].chiave, ATTESA, "dopo qualcosa, l'attesa è sempre la prima voce")
        suonanti = [v.chiave for v in voci[1:]][:3]
        self.assertTrue(all(c.startswith("suono:") for c in suonanti),
                        f"dopo una luce i suoni stanno in cima: {[v.chiave for v in voci]}")

    def test_an_empty_chain_has_nothing_to_wait_for(self):
        from mirto.actions.pannello_catena import ATTESA, proposte

        self.assertNotIn(ATTESA, [v.chiave for v in proposte([], self.COMANDI, casse=["jack"])],
                         "aspettare prima del primo pezzo non vuol dire niente")

    def test_the_offered_piece_arrives_already_working(self):
        """Un pezzo vuoto fa cominciare da un modulo; un pezzo con una durata sensata, da una cosa
        che già funziona."""
        from mirto.actions.pannello_catena import passo_proposto

        luce = passo_proposto("comando:luci", self.COMANDI)
        self.assertEqual(luce.comando, "luci")
        self.assertGreater(luce.durata, 0)
        diss = passo_proposto("comando:dissuasore", self.COMANDI)
        self.assertEqual(diss.suono, "", "due suoni vogliono dire «a rotazione»: non se ne sceglie uno")
        self.assertEqual(passo_proposto("attesa", self.COMANDI), None)
        uno = passo_proposto("comando:x", [{"nome": "x", "tipo": "deterrent", "suoni": ["falco"]}])
        self.assertEqual(uno.suono, "falco", "un suono solo si può scegliere da sé")

    def test_the_boxes_read_like_the_drawing(self):
        from mirto.actions.pannello_catena import Passo, riquadri

        box = riquadri([Passo(comando="luci", durata=10, pausa=5),
                        Passo(comando="dissuasore", durata=2, suono="falco", ripeti=3)], self.COMANDI)
        self.assertEqual(box[0].titolo, "LUCI")
        self.assertIn("10", box[0].righe[0])
        self.assertIn("5", box[0].freccia, "l'attesa si legge sulla freccia, non dentro il riquadro")
        self.assertEqual(box[1].titolo, "FALCO", "un suono si chiama col suo nome")
        self.assertTrue(any("3" in r for r in box[1].righe), "il «× N» del passo sta dentro il riquadro")
        self.assertEqual(box[1].freccia, "", "dopo l'ultimo passo non si aspetta niente")
        self.assertTrue(box[1].avviso, "un dissuasore dice che non scatta per le persone")

    def test_seconds_are_written_like_a_person_writes_them(self):
        from mirto.actions.pannello_catena import Passo, riquadri

        self.assertIn("10", riquadri([Passo(comando="luci", durata=10)], self.COMANDI)[0].righe[0])
        self.assertNotIn("10.0", riquadri([Passo(comando="luci", durata=10)], self.COMANDI)[0].righe[0])

    def test_dragging_reorders_and_never_loses_a_step(self):
        from mirto.actions.pannello_catena import Passo, riordina

        passi = [Passo(comando="a"), Passo(comando="b"), Passo(comando="c")]
        self.assertEqual([p.comando for p in riordina(passi, 2, 0)], ["c", "a", "b"])
        self.assertEqual([p.comando for p in riordina(passi, 0, 9)], ["b", "c", "a"],
                         "trascinato oltre la fine finisce in fondo, non nel vuoto")
        self.assertEqual(len(riordina(passi, 1, 1)), 3)
        self.assertEqual([p.comando for p in riordina(passi, 7, 0)], ["a", "b", "c"])

    def test_the_summary_says_what_will_happen(self):
        from mirto.actions.pannello_catena import Passo, riassunto

        testo = riassunto([Passo(comando="luci", durata=10), Passo(comando="sirena", durata=20)],
                          durata_azione=60, ripeti_tutto=3)
        self.assertIn("6", testo, f"sei passaggi (due passi per tre giri): {testo}")
        self.assertTrue(riassunto([], 60))

    def test_the_suggestions_are_words_not_ticked_boxes(self):
        from mirto.actions.pannello_catena import Passo, suggerimenti

        solo_luce = suggerimenti([Passo(comando="luci", durata=10)], self.COMANDI, casse=["jack"])
        self.assertTrue(any("suono" in s.lower() for s in solo_luce))
        sirena = suggerimenti([Passo(comando="sirena", durata=20)], self.COMANDI, avvisa=False,
                              casse=["jack"])
        self.assertTrue(any("Telegram" in s for s in sirena))
        self.assertEqual(suggerimenti([Passo(comando="sirena", durata=20)], self.COMANDI, avvisa=True,
                                      casse=["jack"]),
                         [s for s in sirena if "Telegram" not in s],
                         "chi avvisa già non si sente dire di avvisare")
        self.assertEqual(suggerimenti([Passo(comando="luci")], self.COMANDI, casse=[]), [],
                         "senza cassa non si suggerisce un suono che non si potrebbe aggiungere")

    def test_a_disabled_test_button_always_says_why(self):
        from mirto.actions.pannello_catena import Passo, cosa_manca_per_provare

        self.assertTrue(cosa_manca_per_provare([], True))
        self.assertTrue(cosa_manca_per_provare([Passo(comando="luci")], False))
        self.assertTrue(cosa_manca_per_provare([Passo(comando="luci")], None))
        self.assertEqual(cosa_manca_per_provare([Passo(comando="luci")], True), "")

    def test_the_kind_is_deduced_from_what_the_action_does(self):
        """Il tipo non si sceglie piu': lo portano i comandi, e vince la regola piu' stretta."""
        from mirto.actions.pannello_catena import Passo, tipo_azione_da

        c = self.COMANDI
        self.assertEqual(tipo_azione_da([Passo(comando="luci")], c), "light")
        self.assertEqual(tipo_azione_da([Passo(comando="sirena")], c), "siren")
        self.assertEqual(tipo_azione_da([Passo(comando="luci"), Passo(comando="sirena")], c), "siren",
                         "una luce accanto a una sirena non la rende una luce")
        self.assertEqual(tipo_azione_da([Passo(comando="sirena"), Passo(comando="dissuasore")], c),
                         "siren",
                         "un comando di dissuasione non fa piu\' di per se\' un\'azione «dissuasore»: "
                         "quello lo decide chi la fa scattare (23/10)")
        self.assertEqual(tipo_azione_da([Passo(comando="dissuasore")], c, soggetti=["cat", "dog"]),
                         "deterrent", "dissuasore = scatta per gli animali")
        self.assertEqual(tipo_azione_da([Passo(comando="dissuasore")], c, soggetti=["person"]),
                         "generic", "la stessa cosa puntata su una persona non e\' un dissuasore")
        self.assertEqual(tipo_azione_da([Passo(comando="boh")], c), "generic",
                         "un comando che il nodo non dichiara non e' niente di speciale")
        self.assertEqual(tipo_azione_da([], c, se_vuota="siren"), "siren",
                         "a catena vuota resta quello che era: non si cambia tipo da soli")

    def test_the_action_duration_never_clips_a_step(self):
        """«duration» è anche il tetto dei passi: salvarla più corta di un passo lo renderebbe finto."""
        from mirto.actions.pannello_catena import Passo, durata_azione_da

        self.assertEqual(durata_azione_da([Passo(comando="a", durata=60)], scelta=30), 60)
        self.assertEqual(durata_azione_da([Passo(comando="a", durata=10)], scelta=120), 120)
        self.assertEqual(durata_azione_da([], scelta=0), 1.0, "mai zero: il nodo non saprebbe che fare")


class ProvaDellaCatenaTest(unittest.TestCase):
    """Il pulsante «Prova»: esegue una catena non ancora salvata, sul nodo, adesso."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        os.environ["MIRTO_CONFIG_DIR"] = self.tmp.name
        os.environ["MIRTO_DATA_DIR"] = self.tmp.name
        self.a = SimulatorServer("ingresso", cert_dir=Path(self.tmp.name, "a")).start()
        self.cfg = Config(Path(self.tmp.name) / "config.yaml")
        self.secrets = mem_secrets()
        pem, _k = fetch_certificate(self.a.url)
        pair_node(self.cfg, self.secrets, "ingresso", self.a.node.name, self.a.url, pem, self.a.key_hex)
        self.core = types.SimpleNamespace(config=self.cfg, secrets=self.secrets, notifier=FakeNotifier())
        self.engine = ActionEngine(self.core)
        self.engine.check_nodes()

    def tearDown(self):
        self.engine.stop(); self.a.stop(); self.tmp.cleanup()

    def attivate(self):
        return [p for (m, path, p) in self.a.node.calls if path.endswith("/attiva")]

    def test_a_chain_can_be_tried_before_being_saved(self):
        testo = self.engine.prova_catena("ingresso", [{"comando": "luci", "durata": 2},
                                                      {"comando": "messaggio", "durata": 2}])
        self.assertTrue(wait_until(lambda: len(self.attivate()) >= 2, timeout=10), testo)
        self.assertTrue(testo)

    def test_how_many_times_reaches_the_node(self):
        """La prova che chiude il giro: «× 1» arriva al nodo come una ripetizione, non come secondi."""
        testo = self.engine.prova_catena("ingresso", [{"comando": "dissuasore", "suono": "falco",
                                                       "durata": 2.2, "volte": 1}])
        self.assertTrue(wait_until(lambda: self.a.node.ripetizioni, timeout=10), testo)
        self.assertEqual(self.a.node.ripetizioni[-1], ("dissuasore", "falco", 1))
        self.assertEqual(self.attivate()[-1].get("volte"), 1, "«volte» viaggia nella chiamata")

    def test_nothing_to_try_says_so(self):
        self.assertTrue(self.engine.prova_catena("ingresso", []))
        self.assertEqual(self.attivate(), [])
        self.assertTrue(self.engine.prova_catena("fantasma", [{"comando": "luci"}]))

    def test_the_test_shortens_every_piece(self):
        """Chi prova vuole sentire cosa fa e in che ordine, non restare tre minuti con la sirena."""
        from mirto.actions.engine import PROVA_MAX_SECONDI

        self.engine.prova_catena("ingresso", [{"comando": "sirena", "durata": 900}])
        self.assertTrue(wait_until(lambda: len(self.attivate()) >= 1, timeout=10))
        self.assertLessEqual(float(self.attivate()[0]["durata"]), PROVA_MAX_SECONDI)

    def test_a_tried_siren_still_eats_its_budget(self):
        """Una sirena che suona è una sirena che suona, anche se sta in una prova."""
        self.engine.prova_catena("ingresso", [{"comando": "sirena", "durata": 5}])
        self.assertTrue(wait_until(lambda: len(self.attivate()) >= 1, timeout=10))
        self.assertTrue(self.engine._siren_log, "la prova deve aver consumato dal budget")


class BlocchiTest(unittest.TestCase):
    """I blocchi (23/10): un mattone solo, e la posizione gli dà il significato.

    In testa un blocco di ascolto è il trigger, in mezzo è un controllo. Le azioni di prima si
    convertono **leggendo**, e devono comportarsi **identiche**: questa classe serve soprattutto a
    questo, perché il punto che si sta spostando — se un evento accende un'azione — e' uno dei due o
    tre piu' delicati del programma.
    """

    def ev(self, *obiettivi, camera="Ingresso", score=0.9):
        class E:
            camera_id = "cam1"
            camera_name = camera

            def targets(self):
                return set(obiettivi)
        e = E()
        e.score = score
        return e

    def test_an_old_action_becomes_a_listening_block_plus_a_command(self):
        from mirto.actions.blocchi import CAMERA, COMANDO, blocchi_di

        b = blocchi_di({"action": "sirena", "duration": 60, "targets": ["person"],
                        "cameras": ["Ingresso"], "min_confidence": 0.7})
        self.assertEqual([x.tipo for x in b], [CAMERA, COMANDO])
        self.assertEqual(b[0].soggetti, ["person"])
        self.assertEqual(b[0].fonti, ["Ingresso"])
        self.assertEqual(b[0].confidenza, 0.7)
        self.assertEqual((b[1].comando, b[1].durata), ("sirena", 60))

    def test_both_sources_become_two_blocks_in_or(self):
        """«Sorgente: tutti e due» era un campo; adesso sono due blocchi, e scatta se uno riconosce."""
        from mirto.actions.blocchi import CAMERA, MICROFONO, blocchi_di, scatta, teste

        b = blocchi_di({"action": "sirena", "targets": ["person", "sound:glass"],
                        "cameras": ["Ingresso", "Microfono del Mac"]})
        self.assertEqual([x.tipo for x in teste(b)], [CAMERA, MICROFONO])
        self.assertEqual(teste(b)[1].soggetti, ["sound:glass"])
        self.assertTrue(scatta(b, self.ev("person")))
        self.assertTrue(scatta(b, self.ev("sound:glass", camera="Microfono del Mac")))
        self.assertFalse(scatta(b, self.ev("dog")))

    def test_a_chain_keeps_all_its_commands(self):
        from mirto.actions.blocchi import COMANDO, blocchi_di, corpo

        b = blocchi_di({"action": "luci", "targets": ["person"],
                        "passi": [{"comando": "luci", "durata": 10, "pausa": 5},
                                  {"comando": "sirena", "durata": 20, "ripeti": 2}]})
        self.assertEqual([x.tipo for x in corpo(b)], [COMANDO, COMANDO])
        self.assertEqual([(x.comando, x.ripeti) for x in corpo(b)], [("luci", 1), ("sirena", 2)])

    def test_a_person_in_the_frame_stops_a_block_that_did_not_ask_for_people(self):
        """La regola che resta, detta senza nominare i «tipi»: il dissuasore per i gatti non parte
        in faccia a chi e' in giardino col gatto."""
        from mirto.actions.blocchi import blocchi_di, scatta

        gatti = blocchi_di({"action": "dissuasore", "targets": ["cat"]})
        self.assertTrue(scatta(gatti, self.ev("cat")))
        self.assertFalse(scatta(gatti, self.ev("cat", "person")))
        persone = blocchi_di({"action": "voce", "targets": ["person"]})
        self.assertTrue(scatta(persone, self.ev("person")),
                        "un suono puntato sulle persone adesso si puo' fare: era il punto")
        self.assertTrue(scatta(persone, self.ev("person", "cat")))

    def test_the_sources_filter_works_on_name_or_id(self):
        from mirto.actions.blocchi import blocchi_di, scatta

        b = blocchi_di({"action": "sirena", "targets": ["person"], "cameras": ["Garage"]})
        self.assertFalse(scatta(b, self.ev("person", camera="Ingresso")))
        self.assertTrue(scatta(b, self.ev("person", camera="Garage")))
        ovunque = blocchi_di({"action": "sirena", "targets": ["person"]})
        self.assertTrue(scatta(ovunque, self.ev("person", camera="Qualunque")),
                        "nessuna fonte scelta vuol dire tutte")

    def test_the_confidence_is_a_floor(self):
        from mirto.actions.blocchi import blocchi_di, scatta

        b = blocchi_di({"action": "sirena", "targets": ["person"], "min_confidence": 0.8})
        self.assertFalse(scatta(b, self.ev("person", score=0.5)))
        self.assertTrue(scatta(b, self.ev("person", score=0.9)))

    def test_the_head_stops_at_the_first_thing_that_does(self):
        """Un blocco di ascolto **in mezzo** e' un controllo, non un altro trigger."""
        from mirto.actions.blocchi import Blocco, CAMERA, COMANDO, corpo, teste

        b = [Blocco(tipo=CAMERA, soggetti=["person"]),
             Blocco(tipo=COMANDO, comando="voce"),
             Blocco(tipo=CAMERA, soggetti=["person"], guarda=20)]
        self.assertEqual(len(teste(b)), 1, "il secondo blocco camera non fa partire niente")
        self.assertEqual(len(corpo(b)), 2)

    def test_what_gets_written_down_is_only_what_was_chosen(self):
        from mirto.actions.blocchi import Blocco, COMANDO

        self.assertEqual(Blocco(tipo=COMANDO, comando="sirena").come_dict(),
                         {"tipo": "comando", "comando": "sirena"})

    def test_an_action_written_with_blocks_is_read_as_blocks(self):
        """Il giorno che l'interfaccia li scrive, si rileggono senza conversione."""
        from mirto.actions.blocchi import blocchi_di

        b = blocchi_di({"blocchi": [{"tipo": "camera", "soggetti": ["person"]},
                                    {"tipo": "comando", "comando": "sirena", "durata": 30}],
                        "action": "ignorato", "targets": ["dog"]})
        self.assertEqual([x.tipo for x in b], ["camera", "comando"])
        self.assertEqual(b[0].soggetti, ["person"], "i blocchi scritti vincono sulla conversione")

    def test_what_an_action_cannot_be(self):
        from mirto.actions.blocchi import Blocco, CAMERA, COMANDO, problemi

        self.assertTrue(problemi([]))
        self.assertTrue(problemi([Blocco(tipo=CAMERA, soggetti=["person"])]),
                        "riconosce e non fa niente")
        self.assertTrue(problemi([Blocco(tipo=COMANDO, comando="sirena")]), "fa senza che niente lo accenda")
        self.assertFalse(problemi([Blocco(tipo=CAMERA, soggetti=["person"]),
                                   Blocco(tipo=COMANDO, comando="sirena")]))


class AvvisiTest(unittest.TestCase):
    """Il simbolino accanto al nome: quello che resta dopo aver tolto i divieti (23/10).

    Non blocca niente: segnala. E il criterio non e\' un elenco di suoni «brutti» — l'ha inquadrato
    Marco: «non ci basiamo sul cannoncino, uno potrebbe usare il suono di una frenata e sarebbe forse
    peggio». Decide la **categoria dichiarata**, e quello che non e\' dichiarato e\' sconosciuto.
    """

    CATEGORIE = {"voce_allontanarsi": "voce", "cannoncino": "animali", "sirena_lunga": "sirena"}

    def test_a_sound_aimed_at_people_is_flagged(self):
        from mirto.actions.avvisi import avvisi

        self.assertTrue(avvisi(["person"], ["cannoncino"], self.CATEGORIE))
        self.assertTrue(avvisi(["person"], ["frenata_caricata_da_marco"], self.CATEGORIE),
                        "un suono caricato non ha categoria: conta come sconosciuto")

    def test_a_spoken_warning_is_not_flagged(self):
        """E\' fatta apposta per essere rivolta a una persona: segnalarla sarebbe rumore."""
        from mirto.actions.avvisi import avvisi

        self.assertEqual(avvisi(["person"], ["voce_allontanarsi"], self.CATEGORIE), [])

    def test_an_animal_deterrent_is_not_flagged(self):
        from mirto.actions.avvisi import avvisi

        self.assertEqual(avvisi(["cat", "bird"], ["cannoncino"], self.CATEGORIE), [])

    def test_a_human_voice_counts_as_a_person(self):
        """«sound:speech» e\' una persona quanto «person»: chi se lo sente addosso stava parlando."""
        from mirto.actions.avvisi import avvisi

        self.assertTrue(avvisi(["sound:scream"], ["cannoncino"], self.CATEGORIE))
        self.assertTrue(avvisi(["sound:crying"], ["cannoncino"], self.CATEGORIE))

    def test_the_night_is_flagged_too(self):
        from mirto.actions.avvisi import avvisi, tocca_la_notte

        self.assertTrue(tocca_la_notte(""), "senza fascia oraria l'azione vale anche di notte")
        self.assertTrue(tocca_la_notte("20:00-06:00"), "una fascia che scavalca la mezzanotte")
        self.assertFalse(tocca_la_notte("08:00-20:00"))
        self.assertTrue(avvisi(["cat"], ["cannoncino"], self.CATEGORIE, fascia_notturna=True))

    def test_without_sounds_there_is_nothing_to_flag(self):
        from mirto.actions.avvisi import avvisi

        self.assertEqual(avvisi(["person"], [], self.CATEGORIE), [])

    def test_a_command_that_plays_the_node_s_own_sound_counts_as_unknown(self):
        """Prudente nel verso giusto: un suono senza nome e' un suono senza categoria."""
        from mirto.actions.avvisi import avvisi, suoni_di

        tipi = {"dissuasore": "deterrent", "luci": "light"}
        self.assertEqual(suoni_di({"action": "dissuasore"}, tipi), [""])
        self.assertEqual(suoni_di({"action": "luci"}, tipi), [], "una luce non suona")
        self.assertTrue(avvisi(["person"], suoni_di({"action": "dissuasore"}, tipi), self.CATEGORIE))

    def test_our_own_sounds_know_what_they_are(self):
        from mirto.actions.avvisi import CATEGORIE_NOSTRE, avvisi

        self.assertEqual(avvisi(["cat"], ["cannoncino"], CATEGORIE_NOSTRE), [],
                         "il cannoncino sui gatti e' il suo mestiere")
        self.assertTrue(avvisi(["person"], ["cannoncino"], CATEGORIE_NOSTRE))

    def test_the_long_text_promises_nothing_it_cannot_prove(self):
        """Le parole evitate di proposito: dicono una cosa che nessuno ha verificato."""
        from mirto.actions.avvisi import testo_lungo

        testo = testo_lungo().lower()
        for parola in ("conforme", "a norma di legge", "certificat", "garantis"):
            self.assertNotIn(parola, testo, f"«{parola}» promette troppo")
        self.assertIn("responsabilità", testo)


class QuanteVolteTest(unittest.TestCase):
    """«Per quanto» era la domanda sbagliata per un suono (23/10).

    Il nodo ripete il suono finche\' il tempo scade: chiedendo «sparo per 8 secondi» se ne sentono
    tre o quattro. Chi configura voleva **uno** sparo.
    """

    COMANDI = [{"nome": "dissuasore", "tipo": "deterrent", "suoni": ["sparo", "falco"],
                "durate": {"sparo": 2.0, "falco": 1.5}},
               {"nome": "sirena", "tipo": "siren"}]

    def test_the_proposed_name_is_one_you_can_actually_use(self):
        """Proporre un nome gia' preso e' peggio che non proporne nessuno: lo scopri al salvataggio."""
        from mirto.actions.pannello_catena import nome_libero

        self.assertEqual(nome_libero("Sirena", set()), "Sirena")
        self.assertEqual(nome_libero("Sirena", {"Sirena"}), "Sirena 2")
        self.assertEqual(nome_libero("Sirena", {"Sirena", "Sirena 2"}), "Sirena 3")
        self.assertEqual(nome_libero("", {"Sirena"}), "")

    def test_a_rotating_sound_still_knows_how_to_count(self):
        """Il pezzo di Marco non aveva **un** suono scelto: «quello del nodo, a rotazione».

        Cercavo la durata di un suono senza nome, non la trovavo, e ripiegavo sui secondi — cioe'
        sulla domanda sbagliata, e il cannoncino sparava due volte. Per chiedere «tre volte» non
        serve sapere quale suono partira': basta che il nodo sappia contare, e lui lo sa.
        """
        from mirto.actions.pannello_catena import lunghezza_per

        self.assertEqual(lunghezza_per("dissuasore", "sparo", self.COMANDI), 2.0)
        self.assertEqual(lunghezza_per("dissuasore", "", self.COMANDI), 2.0,
                         "senza suono scelto si prende il piu' lungo: il conto resta prudente")
        self.assertEqual(lunghezza_per("sirena", "", self.COMANDI), 0.0,
                         "la sirena la genera il nodo: li' i secondi sono la domanda giusta")

    def test_a_box_that_counts_says_times_not_seconds(self):
        from mirto.actions.pannello_catena import Passo, riquadri

        box = riquadri([Passo(comando="dissuasore", suono="sparo", durata=6.2, volte=3)], self.COMANDI)
        self.assertIn("3", " ".join(box[0].righe))
        self.assertNotIn("6", " ".join(box[0].righe), "i secondi sono una cosa nostra")

    def test_the_node_says_how_long_each_sound_is(self):
        from mirto.actions.pannello_catena import durata_di

        self.assertEqual(durata_di("sparo", self.COMANDI), 2.0)
        self.assertEqual(durata_di("mai_sentito", self.COMANDI), 0.0)
        self.assertEqual(durata_di("sparo", [{"nome": "x", "tipo": "deterrent", "suoni": ["sparo"]}]), 0.0,
                         "un nodo vecchio non le dichiara: zero, e si torna ai secondi")

    def test_the_seconds_are_only_bookkeeping_now(self):
        """Il 23/10, seconda correzione: i secondi non decidono piu' quello che si sente.

        Marco: «se metti x1 su un suono che emetti non decidi te quanto dura». Chiedere secondi al
        nodo non potra' mai voler dire «una volta» — a dirlo e' «volte», che il nodo esegue contando
        le ripetizioni. Questi secondi servono solo a sapere per quanto l'azione risulta in corso, e
        portano un filo di margine perche' il nodo non tagli la coda dell'ultima ripetizione.
        """
        from mirto.actions.pannello_catena import MARGINE, quante_volte, secondi_per

        self.assertEqual(secondi_per(1, 2.0), 2.0 + MARGINE)
        self.assertEqual(secondi_per(3, 2.0), 6.0 + MARGINE)
        self.assertEqual(quante_volte(secondi_per(3, 2.0), 2.0), 3, "andata e ritorno")
        self.assertEqual(quante_volte(secondi_per(1, 2.0), 2.0), 1)

    def test_an_old_node_keeps_the_seconds(self):
        from mirto.actions.pannello_catena import quante_volte, secondi_per

        self.assertEqual(secondi_per(3, 0), 0.0, "senza la lunghezza non si calcola niente")
        self.assertEqual(quante_volte(8, 0), 1)


class LaDomandaSegueIlSuonoTest(unittest.TestCase):
    """Cambiando il suono dentro un pezzo deve cambiare **la domanda** (23/10, sera).

    Marco apre un pezzo «sirena» — che e\' un ululato generato, e li\' i secondi sono giusti — e
    dentro sceglie «sparo». La finestra continuava a chiedere «per quanto: 60 s», perche\' la domanda
    veniva decisa **all\'apertura** e non si rifaceva piu\': un colpo da 0,9 s configurato per un
    minuto. Vale anche al contrario.

    La decisione se ne va dalla finestra e viene qui, dove si prova senza aprire niente.
    """

    COMANDI = [{"nome": "sirena", "tipo": "siren", "descrizione": "Sirena dalla cassa (max 3 minuti)"},
               {"nome": "dissuasore", "tipo": "deterrent", "suoni": ["sparo", "falco"],
                "durate": {"sparo": 0.9, "falco": 1.5}},
               {"nome": "luce", "tipo": "light"}]

    def test_the_question_follows_the_chosen_sound(self):
        from mirto.actions.pannello_catena import a_volte

        self.assertFalse(a_volte("sirena", "", self.COMANDI),
                         "la sirena la genera il nodo: quanto dura lo decidi tu")
        self.assertTrue(a_volte("dissuasore", "sparo", self.COMANDI),
                        "uno sparo dura quanto dura: si conta, non si cronometra")
        self.assertTrue(a_volte("dissuasore", "", self.COMANDI),
                        "anche «a rotazione»: il nodo sa contare lo stesso")
        self.assertFalse(a_volte("luce", "", self.COMANDI))

    def test_counting_and_repeating_are_never_both_on(self):
        """«volte» e «ripeti» insieme fanno 3 x 3 = nove spari: l\'errore che gia\' ci e\' costato due giri."""
        from mirto.actions.pannello_catena import MARGINE, passo_da_scelte

        p = passo_da_scelte("suono:dissuasore:sparo", volte=3, durata=60.0, ripeti=3,
                            pausa=0.0, comandi=self.COMANDI)
        self.assertEqual(p.volte, 3)
        self.assertEqual(p.ripeti, 1, "la ripetizione la porta «volte», una volta sola")
        self.assertAlmostEqual(p.durata, 3 * 0.9 + MARGINE, places=3,
                               msg="i secondi restano, ma ricalcolati sul suono scelto")

    def test_the_sixty_seconds_of_the_siren_do_not_follow_the_shot(self):
        """Il difetto di Marco, in una riga: 60 s addosso a un colpo da 0,9."""
        from mirto.actions.pannello_catena import passo_da_scelte

        p = passo_da_scelte("suono:dissuasore:sparo", volte=1, durata=60.0, ripeti=1,
                            pausa=0.0, comandi=self.COMANDI)
        self.assertLess(p.durata, 2.0)

    def test_choosing_the_siren_goes_back_to_seconds(self):
        from mirto.actions.pannello_catena import passo_da_scelte

        p = passo_da_scelte("suono:sirena:", volte=3, durata=60.0, ripeti=2,
                            pausa=1.0, comandi=self.COMANDI)
        self.assertEqual(p.comando, "sirena")
        self.assertEqual(p.volte, 0, "una sirena non si conta: si tiene accesa")
        self.assertEqual(p.durata, 60.0)
        self.assertEqual(p.ripeti, 2)
        self.assertEqual(p.pausa, 1.0)

    def test_a_command_without_sounds_keeps_its_seconds(self):
        from mirto.actions.pannello_catena import passo_da_scelte

        p = passo_da_scelte("suono:luce::", volte=4, durata=30.0, ripeti=1, pausa=0.0,
                            comandi=self.COMANDI)
        self.assertEqual(p.comando, "luce")
        self.assertEqual(p.volte, 0)
        self.assertEqual(p.durata, 30.0)

    def test_nothing_chosen_builds_nothing(self):
        from mirto.actions.pannello_catena import passo_da_scelte

        self.assertIsNone(passo_da_scelte("", 1, 5.0, 1, 0.0, self.COMANDI))
        self.assertIsNone(passo_da_scelte("suono::", 1, 5.0, 1, 0.0, self.COMANDI))

    def test_an_old_node_without_lengths_asks_for_seconds(self):
        """Nodo prima dell\'API 1.8: nessuna lunghezza dichiarata, e la domanda torna a essere i secondi."""
        from mirto.actions.pannello_catena import a_volte, passo_da_scelte

        vecchi = [{"nome": "dissuasore", "tipo": "deterrent", "suoni": ["sparo"]}]
        self.assertFalse(a_volte("dissuasore", "sparo", vecchi))
        p = passo_da_scelte("suono:dissuasore:sparo", volte=3, durata=8.0, ripeti=2,
                            pausa=0.0, comandi=vecchi)
        self.assertEqual(p.volte, 0)
        self.assertEqual(p.durata, 8.0)
        self.assertEqual(p.ripeti, 2)


class IlDispositivoStaSulBloccoTest(unittest.TestCase):
    """Tappa 1 del passaggio «dall'azione al blocco» (23/10 sera).

    Un'azione porta ancora **un** campo `node`, erede di quando un'azione era «una cosa che fa un
    nodo». Col modello a blocchi puo' ascoltare la camera del Mac, aspettare, far suonare la sirena di
    un nodo e mandare un'email: non ha piu' un nodo solo, e finche' quel campo resta sull'azione le
    azioni devono vivere dentro la pagina di un nodo.

    Qui si fa solo la prima tappa: **il campo esiste e viaggia** — sul blocco, sul passo e dentro la
    lista srotolata, che e' l'unica cosa che chi esegue si legge. Il comportamento non cambia di
    niente, e soprattutto **una configurazione vecchia non guadagna una riga**: vuoto vuol dire
    «quello dell'azione», e il ripiego lo fa chi esegue (tappa 2).
    """

    def test_how_a_device_is_written_down(self):
        """Scelta B, presa da Marco: col prefisso, cosi' domani una cam esterna o una presa non
        obbligano a riconvertire tutte le configurazioni."""
        from mirto.actions.blocchi import COMPUTER, NODO, QUESTO_COMPUTER, dispositivo_di

        self.assertEqual(dispositivo_di("nodo:ingresso"), (NODO, "ingresso"))
        self.assertEqual(dispositivo_di(QUESTO_COMPUTER), (COMPUTER, "questo"))
        self.assertEqual(dispositivo_di(""), ("", ""))
        self.assertEqual(dispositivo_di("ingresso"), (NODO, "ingresso"),
                         "senza prefisso e' un nodo: le configurazioni di oggi si leggono per sempre")
        self.assertEqual(dispositivo_di("marziano:x"), ("", ""), "un prefisso che non conosciamo non si inventa")

    def test_an_old_action_lends_its_node_to_every_block(self):
        from mirto.actions.blocchi import CAMERA, COMANDO, blocchi_di

        vecchia = {"name": "Sirena", "node": "ingresso", "action": "sirena", "kind": "siren",
                   "targets": ["person"], "duration": 60}
        blocchi = blocchi_di(vecchia)
        self.assertEqual([b.tipo for b in blocchi], [CAMERA, COMANDO])
        for b in blocchi:
            self.assertEqual(b.dispositivo, "nodo:ingresso", "leggendo, il nodo dell'azione diventa quello dei blocchi")

    def test_the_unrolled_chain_says_where_every_call_goes(self):
        """Il punto che decide se il resto funziona: chi esegue legge **solo** questa lista."""
        from mirto.actions.passi import Passo, srotola

        passi = [Passo(comando="luci", durata=5),
                 Passo(comando="sirena", durata=10, dispositivo="nodo:garage")]
        catena = srotola(passi, 60, 1, dispositivo="nodo:ingresso")
        self.assertEqual([c["dispositivo"] for c in catena], ["nodo:ingresso", "nodo:garage"],
                         "il pezzo che non dice niente eredita, quello che lo dice comanda")

    def test_a_chain_without_a_device_stays_empty_handed(self):
        from mirto.actions.passi import Passo, srotola

        catena = srotola([Passo(comando="luci", durata=5)], 60, 1)
        self.assertEqual(catena[0]["dispositivo"], "", "senza un valore non si inventa un nodo")

    def test_the_action_chain_already_knows_where_it_goes(self):
        """Il ponte fra i due modelli: l'azione presta il suo nodo ai pezzi che non dicono dove."""
        from mirto.actions.engine import ActionSpec

        spec = ActionSpec.from_dict({"name": "Sirena", "node": "ingresso", "action": "sirena",
                                     "kind": "siren", "targets": ["person"], "duration": 30})
        catena = spec.catena({})
        self.assertTrue(catena)
        self.assertEqual({c["dispositivo"] for c in catena}, {"nodo:ingresso"})

    def test_an_old_configuration_does_not_gain_a_line(self):
        """La promessa fatta due volte, e verificata a mano da Marco (prova 19): una configurazione
        che non usa la cosa nuova non deve cambiare di una riga."""
        from mirto.actions.passi import Passo, passi_di

        vecchia = {"name": "Sirena", "node": "ingresso", "action": "sirena", "duration": 60}
        passi = passi_di(vecchia)
        self.assertEqual(passi[0].dispositivo, "", "leggendo non si appiccica niente al passo")
        self.assertNotIn("dispositivo", passi[0].come_dict())
        self.assertIn("dispositivo", Passo(comando="x", dispositivo="nodo:a").come_dict(),
                      "quando invece e' stato scelto, si scrive")


class DoveSiFaQuestoPezzoTest(unittest.TestCase):
    """Tappa 3: i comandi che si vedono sono quelli **del dispositivo scelto**.

    Senza questo filtro l'interfaccia lascerebbe puntare un pezzo al garage e poi scegliergli un
    comando che ce l'ha solo l'ingresso: un'azione che si rompe al primo scatto, e chi l'ha costruita
    non ha nessun modo di accorgersene guardando. Il dispositivo viaggia **dentro** l'elenco dei
    comandi — una chiave in piu' su ogni voce — cosi' le funzioni che gia' c'erano continuano a
    ricevere una lista piatta e imparano solo a filtrarla.
    """

    COMANDI = [{"nome": "luci", "tipo": "light", "descrizione": "Luci", "dispositivo": "nodo:ingresso"},
               {"nome": "sirena", "tipo": "siren", "descrizione": "Sirena", "dispositivo": "nodo:ingresso"},
               {"nome": "sirena", "tipo": "deterrent", "descrizione": "Suoni", "dispositivo": "nodo:garage",
                "suoni": ["sparo"], "durate": {"sparo": 0.9}}]

    def test_the_same_name_on_two_nodes_is_not_the_same_command(self):
        """Il caso che rende il filtro obbligatorio invece che carino: due nodi, stesso nome."""
        from mirto.actions.pannello_catena import _tipo_di

        self.assertEqual(_tipo_di("sirena", self.COMANDI, "nodo:ingresso"), "siren")
        self.assertEqual(_tipo_di("sirena", self.COMANDI, "nodo:garage"), "deterrent")

    def test_without_a_device_nothing_changes(self):
        """Le chiamate di prima non passano il dispositivo e devono continuare a funzionare."""
        from mirto.actions.pannello_catena import _tipo_di, lunghezza_per

        self.assertEqual(_tipo_di("luci", self.COMANDI), "light")
        self.assertEqual(lunghezza_per("sirena", "sparo", self.COMANDI), 0.9)

    def test_the_sounds_follow_the_device(self):
        from mirto.actions.pannello_catena import a_volte, lunghezza_per

        self.assertEqual(lunghezza_per("sirena", "", self.COMANDI, "nodo:ingresso"), 0.0,
                         "la sirena dell'ingresso la genera il nodo: niente lunghezza")
        self.assertEqual(lunghezza_per("sirena", "sparo", self.COMANDI, "nodo:garage"), 0.9)
        self.assertFalse(a_volte("sirena", "", self.COMANDI, "nodo:ingresso"))
        self.assertTrue(a_volte("sirena", "sparo", self.COMANDI, "nodo:garage"))

    def test_only_the_commands_of_that_device(self):
        from mirto.actions.pannello_catena import comandi_di

        self.assertEqual([c["nome"] for c in comandi_di(self.COMANDI, "nodo:ingresso")], ["luci", "sirena"])
        self.assertEqual([c["nome"] for c in comandi_di(self.COMANDI, "nodo:garage")], ["sirena"])
        self.assertEqual(len(comandi_di(self.COMANDI, "")), 3, "senza dispositivo si vede tutto")
        self.assertEqual(comandi_di(self.COMANDI, "nodo:cantina"), [],
                         "un dispositivo che non ha comandi non ne presta da altri")

    def test_a_box_says_where_it_happens_when_it_is_not_the_usual_place(self):
        """Nella striscia, un pezzo che sta altrove deve dirlo: altrimenti si legge come gli altri."""
        from mirto.actions.pannello_catena import Passo, riquadri

        passi = [Passo(comando="luci", durata=5),
                 Passo(comando="sirena", durata=5, dispositivo="nodo:garage")]
        box = riquadri(passi, self.COMANDI, dispositivo="nodo:ingresso", nomi={"nodo:garage": "Garage"})
        self.assertNotIn("Garage", " ".join(box[0].righe), "il pezzo che sta nel posto solito non lo ripete")
        self.assertIn("Garage", " ".join(box[1].righe))

    def test_the_usual_place_is_not_written_down(self):
        """Salvare un pezzo che sta dov'e' sempre stato non deve aggiungere una riga alla configurazione."""
        from mirto.actions.pannello_catena import passo_da_scelte

        p = passo_da_scelte("suono:luci:", 1, 5.0, 1, 0.0, self.COMANDI,
                            dispositivo="nodo:ingresso", predefinito="nodo:ingresso")
        self.assertEqual(p.dispositivo, "")
        self.assertNotIn("dispositivo", p.come_dict())
        altrove = passo_da_scelte("suono:sirena:sparo", 2, 5.0, 1, 0.0, self.COMANDI,
                                  dispositivo="nodo:garage", predefinito="nodo:ingresso")
        self.assertEqual(altrove.dispositivo, "nodo:garage")
        self.assertEqual(altrove.volte, 2, "e i secondi li calcola sul suono **di quel** nodo")


class ilNodoDellAzioneNonComandaPiuTest(unittest.TestCase):
    """Tappa 4: il `node` dell'azione diventa un residuo, scritto per compatibilita' e non piu' letto.

    E' il pezzo che sblocca la scheda Azioni: finche' l'azione **deve** avere un nodo, deve anche
    vivere dentro la pagina di quel nodo. Da qui in poi l'azione ha **i dispositivi dei suoi pezzi**, e
    il campo vecchio serve solo a leggere le configurazioni scritte prima.
    """

    def test_an_action_can_say_where_it_goes_without_having_a_node(self):
        from mirto.actions.engine import ActionSpec

        spec = ActionSpec.from_dict({"name": "Sparsa", "kind": "generic", "mode": "auto",
                                     "action": "luci", "targets": ["person"],
                                     "passi": [{"comando": "luci", "durata": 5, "dispositivo": "nodo:ingresso"},
                                               {"comando": "messaggio", "durata": 2, "dispositivo": "nodo:garage"}]})
        self.assertEqual(spec.node, "", "non serve piu' averlo")
        self.assertEqual([c["dispositivo"] for c in spec.catena()], ["nodo:ingresso", "nodo:garage"])

    def test_an_action_with_nowhere_to_run_is_still_refused(self):
        """Togliere l'obbligo del nodo non vuol dire accettare un'azione che non sa dove farsi."""
        from mirto.actions.engine import ActionSpec

        with self.assertRaises(ValueError):
            ActionSpec.from_dict({"name": "Nessunluogo", "kind": "generic", "mode": "auto",
                                  "action": "luci", "targets": ["person"]})

    def test_which_devices_an_action_touches(self):
        from mirto.actions.blocchi import dispositivi_di

        vecchia = {"name": "Sirena", "node": "ingresso", "action": "sirena", "duration": 60}
        self.assertEqual(dispositivi_di(vecchia), ["nodo:ingresso"])
        sparsa = {"name": "Due", "node": "ingresso", "action": "luci",
                  "passi": [{"comando": "luci"}, {"comando": "messaggio", "dispositivo": "nodo:garage"}]}
        self.assertEqual(dispositivi_di(sparsa), ["nodo:ingresso", "nodo:garage"],
                         "l'ordine e' quello della catena: il primo e' il posto solito")
        self.assertEqual(dispositivi_di({"name": "x", "action": "y"}), [])

    def test_the_siren_budget_asks_the_node_that_runs_the_piece(self):
        """Lo stesso nome puo' essere una sirena su un nodo e un dissuasore su un altro.

        Se i tipi venissero da un nodo solo, un pezzo «sirena» eseguito altrove consumerebbe (o non
        consumerebbe) il budget dei tre minuti in base a quello che dichiara il nodo sbagliato.
        """
        from mirto.actions.passi import quanto_suona

        catena = [{"comando": "sirena", "durata": 60, "attesa_dopo": 0, "dispositivo": "nodo:a",
                   "tipo": "siren"},
                  {"comando": "sirena", "durata": 30, "attesa_dopo": 0, "dispositivo": "nodo:b",
                   "tipo": "deterrent"}]
        self.assertEqual(quanto_suona(catena, {}), 60.0,
                         "conta solo il pezzo che su **quel** nodo e' davvero una sirena")


class SuoniPropriTest(unittest.TestCase):
    """Scegliere un file, dire subito se va bene, convertirlo per il nodo (24/10).

    Tutta la parte in cui si sbaglia — le estensioni, i limiti, i nomi, i messaggi — si prova qui,
    senza un nodo acceso e senza aprire una finestra. La conversione la fa il computer: il nodo resta
    un posto che sa suonare solo WAV, e un decoder in meno in giardino e' un decoder in meno da
    aggiornare quando ci trovano dentro una falla.
    """

    def test_the_name_it_will_have_is_shown_before_uploading(self):
        from mirto.actions.suoni import nome_pulito

        self.assertEqual(nome_pulito("Voce Mia.m4a"), "voce_mia")
        self.assertEqual(nome_pulito("corvo-2.WAV"), "corvo-2")
        self.assertEqual(nome_pulito("...."), "")
        self.assertEqual(nome_pulito("a" * 80), "a" * 40, "il nodo tiene quaranta caratteri")

    def test_why_a_file_cannot_be_used_is_said_before_the_wait(self):
        import tempfile
        from pathlib import Path

        from mirto.actions.suoni import perche_no

        with tempfile.TemporaryDirectory() as d:
            buono = Path(d, "Voce Mia.m4a"); buono.write_bytes(b"x" * 10)
            brutto = Path(d, "foto.png"); brutto.write_bytes(b"x" * 10)
            pesante = Path(d, "enorme.wav"); pesante.write_bytes(b"x" * 100)
            self.assertEqual(perche_no(buono), "")
            self.assertIn("Formato", perche_no(brutto))
            self.assertIn("pesa troppo", perche_no(pesante, massimo_sorgente=50))
            self.assertIn("c'è già", perche_no(buono, nomi_presi=["voce_mia"]))
            self.assertIn("non c'è più", perche_no(Path(d, "mai_esistito.wav")))

    def test_the_conversion_asks_for_exactly_what_the_node_plays(self):
        from mirto.actions.suoni import comando_conversione

        cmd = comando_conversione("/usr/bin/ffmpeg", "/tmp/a.mp3", "/tmp/b.wav", rate=22050, massimo_s=30)
        self.assertEqual(cmd[0], "/usr/bin/ffmpeg")
        for atteso in ("-ac", "1", "-ar", "22050", "-c:a", "pcm_s16le"):
            self.assertIn(atteso, cmd)
        self.assertEqual(cmd[cmd.index("-t") + 1], "30", "si taglia al limite dichiarato dal nodo")
        self.assertEqual(cmd[-1], "/tmp/b.wav")

    def test_the_limits_are_written_where_you_choose(self):
        from mirto.actions.suoni import riassunto

        testo = riassunto({"massimo_s": 30, "massimo_byte": 2 * 1024 * 1024})
        self.assertIn("30", testo)
        self.assertIn("2048", testo)
        self.assertIn("mp3", testo)
        self.assertIn("vecchia", riassunto({}), "un nodo che non dichiara i limiti lo dice")

    def test_a_sound_in_use_is_not_silently_deleted(self):
        from mirto.actions.suoni import in_uso

        azioni = [{"name": "Corvi", "sounds": ["allarme_corvi"]},
                  {"name": "Recinto", "passi": [{"comando": "dissuasore", "suono": "voce_mia"}]},
                  {"name": "Luci", "passi": [{"comando": "luci"}]}]
        self.assertEqual(in_uso("voce_mia", azioni), ["Recinto"])
        self.assertEqual(in_uso("allarme_corvi", azioni), ["Corvi"])
        self.assertEqual(in_uso("mai_usato", azioni), [])

    def test_the_whole_round_trip_against_a_node(self):
        """Carica, compare fra i suoni, si toglie: il giro intero contro un nodo (finto, ma in rete).

        Qui si vede la cosa che conta per chi usa il programma: un suono caricato **compare subito**
        fra quelli scegliibili in un'azione, senza riavviare niente.
        """
        import tempfile
        from pathlib import Path

        from mirto.actions.nodes import NodeClient, NodeConfig, fetch_certificate

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            srv = SimulatorServer("suoni", cert_dir=Path(d, "c")).start()
            try:
                pem, _fp = fetch_certificate(srv.url)
                client = NodeClient(NodeConfig("n1", srv.node.name, srv.url), pem, srv.key_hex)
                stato = client.sounds()
                self.assertGreater(stato["massimo_s"], 0, "i limiti si dicono prima")
                quanti = len(stato["suoni"])

                client.add_sound("voce_mia", b"\0\0" * 22050)
                self.assertIn("voce_mia", {s["nome"] for s in client.sounds()["suoni"]})
                suoni_azione = next(a for a in client.actions() if a["nome"] == "dissuasore")["suoni"]
                self.assertIn("voce_mia", suoni_azione, "scegliibile subito in un'azione")

                client.remove_sound("voce_mia")
                self.assertEqual(len(client.sounds()["suoni"]), quanti)
            finally:
                srv.stop()

    def test_the_button_says_stop_while_it_is_playing(self):
        """«Preme ascolta, il brano parte; se preme stop si ferma» (Marco, provando A-02).

        Un brano caricato puo' durare mezzo minuto: restare a sentirlo fino in fondo perche' il
        pulsante dice solo «Ascolta» e' una piccola prigione.
        """
        from mirto.actions.suoni import righe_suoni

        stato = {"suoni": [{"nome": "sparo", "secondi": 0.9, "nostro": True},
                           {"nome": "carillon", "secondi": 28.0, "nostro": False}]}
        fermo = righe_suoni(stato)
        self.assertEqual([r["azione"] for r in fermo], ["ascolta", "ascolta"])
        suona = righe_suoni(stato, in_corso="carillon")
        self.assertEqual([r["azione"] for r in suona], ["ascolta", "ferma"],
                         "solo quello che sta suonando si puo' fermare")
        self.assertEqual(righe_suoni({}), [], "un nodo che non dichiara niente non inventa righe")
        self.assertEqual([r["azione"] for r in righe_suoni(stato, in_corso="")], ["ascolta", "ascolta"],
                         "«niente in corso» non deve far comparire «ferma» sul primo della lista")


class LaSchedaDelleAzioniTest(unittest.TestCase):
    """L'elenco di tutte le azioni e la libreria aggregata (24/10).

    Dopo il passaggio dall'azione al blocco, «di che nodo e' questa azione?» non ha piu' una risposta:
    un'azione puo' accendere una luce all'ingresso e far suonare il garage. Da qui una scheda loro, e
    una libreria che e' **delle azioni**, non di un nodo.
    """

    NODI = [{"id": "ingresso", "name": "Ingresso"}, {"id": "garage", "name": "Garage"}]

    def test_a_row_says_where_the_action_happens(self):
        from mirto.actions.pannello_azioni import righe_azioni

        azioni = [{"name": "Sirena", "node": "ingresso", "action": "sirena", "kind": "siren",
                   "targets": ["person"], "duration": 60},
                  {"name": "Due stanze", "node": "ingresso", "action": "luci", "kind": "generic",
                   "targets": ["person"],
                   "passi": [{"comando": "luci"}, {"comando": "messaggio", "dispositivo": "nodo:garage"}]}]
        righe = righe_azioni(azioni, self.NODI, salvate=["Sirena"])
        self.assertEqual(righe[0]["dove"], "Ingresso")
        self.assertEqual(righe[1]["dove"], "Ingresso, Garage", "la colonna che prima non poteva esistere")
        self.assertFalse(righe[0]["da_salvare"])
        self.assertTrue(righe[1]["da_salvare"])
        self.assertTrue(all(r["nome"] for r in righe))

    def test_the_same_ready_action_on_two_nodes_is_shown_once(self):
        from mirto.actions.pannello_azioni import libreria_aggregata, quante_volte_presente
        from mirto.actions.pannello_azioni import nomi_dispositivi

        per_nodo = {
            "ingresso": [{"name": "Sirena – Ingresso", "node": "ingresso", "action": "sirena", "kind": "siren"},
                         {"name": "Luci – Ingresso", "node": "ingresso", "action": "luci", "kind": "light"}],
            "garage": [{"name": "Sirena – Garage", "node": "garage", "action": "sirena", "kind": "siren"}],
        }
        voci = libreria_aggregata(per_nodo)
        self.assertEqual([v["titolo"] for v in voci], ["Luci", "Sirena"])
        sirena = next(v for v in voci if v["titolo"] == "Sirena")
        self.assertEqual(sorted(sirena["nodi"]), ["garage", "ingresso"])
        self.assertEqual(sirena["per_nodo"]["garage"]["name"], "Sirena – Garage",
                         "importarla su un nodo o sull'altro vuol dire prendere due azioni diverse")
        nomi = nomi_dispositivi(self.NODI)
        self.assertEqual(quante_volte_presente(sirena, nomi), "presente su 2 nodi")
        luci = next(v for v in voci if v["titolo"] == "Luci")
        self.assertEqual(quante_volte_presente(luci, nomi), "Ingresso", "con un nodo solo si dice quale")

    def test_an_empty_library_is_not_an_error(self):
        from mirto.actions.pannello_azioni import libreria_aggregata

        self.assertEqual(libreria_aggregata({}), [])
        self.assertEqual(libreria_aggregata({"ingresso": []}), [])


class ChiAscoltaIlMicrofonoDelNodoTest(unittest.TestCase):
    """Due interruttori diversi: il nodo accende il microfono, Mirto decide se ascoltarlo (24/10).

    Il secondo stava in «Suoni» ed era **uno per tutti i nodi**: sbagliato due volte. Marco aveva il
    microfono del nodo acceso e Mirto che non lo ascoltava, e in elenco non compariva niente senza
    che nessuna delle due finestre lo dicesse.
    """

    def test_a_configuration_written_before_keeps_meaning_what_it_meant(self):
        from mirto.actions.pannello_audio import ascolta_microfono

        self.assertTrue(ascolta_microfono(True, "ingresso"))
        self.assertFalse(ascolta_microfono(False, "ingresso"), "un «no» per tutti resta un no")
        self.assertTrue(ascolta_microfono(None, "ingresso"), "chi non ha mai scelto ascolta")

    def test_each_node_has_its_own_answer(self):
        from mirto.actions.pannello_audio import ascolta_microfono, con_ascolto

        valore = con_ascolto(True, "garage", False)
        self.assertFalse(ascolta_microfono(valore, "garage"))
        self.assertTrue(ascolta_microfono(valore, "ingresso"), "gli altri non si toccano")
        valore = con_ascolto(valore, "ingresso", False)
        self.assertFalse(ascolta_microfono(valore, "ingresso"))
        self.assertFalse(ascolta_microfono(valore, "garage"), "e il primo resta com'era")


class UnaSogliaPerOgniRiconoscimentoTest(unittest.TestCase):
    """Marco, 24/10: «dato che i modelli AI si aggiornano deve essere specifica per tipologia».

    Tre reti diverse giudicano le tre famiglie di cose che sappiamo riconoscere, e 0,60 non vuol dire
    la stessa cosa per tutte e tre. Quando una si aggiorna, i suoi numeri si spostano e quelli delle
    altre no: una soglia sola per tutta l'azione li legava insieme senza motivo.
    """

    CFG = {"actions.person_threshold": 0.60, "detection.object_threshold": 0.50,
           "detection.species_threshold": 0.35, "audio.thresholds": {"dog": 0.4},
           "detection.model": "yolox_s"}

    def leggi(self, chiave, se_manca=None):
        return self.CFG.get(chiave, se_manca)

    def test_three_networks_three_scales(self):
        from mirto.actions.confidenze import SPECIE, YAMNET, YOLOX, modelli_di, modello_di

        self.assertEqual(modello_di("person"), YOLOX)
        self.assertEqual(modello_di("cat"), YOLOX)
        self.assertEqual(modello_di("bird:corvid"), SPECIE)
        self.assertEqual(modello_di("sound:glass"), YAMNET)
        self.assertEqual(modelli_di(["person", "cat", "sound:glass"]), [YOLOX, YAMNET],
                         "una riga per rete, nell'ordine in cui si incontrano, senza ripetizioni")

    def test_the_standard_is_not_copied_here(self):
        """«Standard» e' un rimando, non un numero: se cambia in Generale, cambia anche qui."""
        from mirto.actions.confidenze import soglia_standard

        self.assertAlmostEqual(soglia_standard("person", self.leggi), 0.60)
        self.assertAlmostEqual(soglia_standard("cat", self.leggi), 0.50)
        self.assertAlmostEqual(soglia_standard("bird:corvid", self.leggi), 0.35)
        self.assertAlmostEqual(soglia_standard("sound:dog", self.leggi), 0.4,
                               msg="il suono ha gia' una sua soglia per tipo, in «audio.thresholds»")
        altro = dict(self.CFG, **{"actions.person_threshold": 0.8})
        self.assertAlmostEqual(soglia_standard("person", lambda k, d=None: altro.get(k, d)), 0.8)

    def test_a_threshold_you_wrote_wins_and_says_so(self):
        from mirto.actions.confidenze import soglia_in_uso

        self.assertEqual(soglia_in_uso("person", {"person": 0.9}, self.leggi), (0.9, True))
        self.assertEqual(soglia_in_uso("person", {}, self.leggi), (0.60, False))
        self.assertEqual(soglia_in_uso("cat", {}, self.leggi, vecchia=0.7), (0.7, True),
                         "la soglia unica di prima vale ancora per chi non ne ha una sua")

    def test_standard_is_never_written_down(self):
        """Scrivere il numero di oggi congelerebbe l'azione sul modello di oggi: il contrario."""
        from mirto.actions.confidenze import pulisci

        self.assertEqual(pulisci({"person": 0.8, "cat": 0, "bird": 1.2}, ["person", "cat", "bird"]),
                         {"person": 0.8})
        self.assertEqual(pulisci({"person": 0.8}, ["cat"]), {},
                         "una soglia su un obiettivo tolto non vale niente")

    def test_two_thresholds_are_two_blocks_and_the_engine_does_not_change(self):
        from mirto.actions.blocchi import blocchi_di, scatta

        base = {"action": "sirena", "node": "n1", "targets": ["person", "cat"],
                "cameras": ["Ingresso"]}
        uno = [(b.soggetti, b.confidenza) for b in blocchi_di(base) if b.ascolta]
        self.assertEqual(uno, [(["person", "cat"], 0.0)], "senza soglie scritte, un blocco come prima")

        due = [(b.soggetti, b.confidenza) for b in blocchi_di(dict(base, confidenze={"cat": 0.9}))
               if b.ascolta]
        self.assertEqual(due, [(["person"], 0.0), (["cat"], 0.9)],
                         "soglie diverse, blocchi diversi: in testa basta che scatti uno")

        class Ev:
            def __init__(self, cosa, quanto):
                self._cosa, self.score = cosa, quanto
                self.camera_name, self.camera_id = "Ingresso", "Ingresso"

            def targets(self):
                return [self._cosa]

        blocchi = blocchi_di(dict(base, confidenze={"cat": 0.9}))
        self.assertTrue(scatta(blocchi, Ev("cat", 0.95)), "il gatto sicuro passa")
        self.assertFalse(scatta(blocchi, Ev("cat", 0.7)), "il gatto incerto no: 0,9 e' suo")
        self.assertTrue(scatta(blocchi, Ev("person", 0.7)), "la persona ha la sua, piu' bassa")

    def test_an_old_action_keeps_working_and_grows_up_when_saved(self):
        """``min_confidence`` non si rifiuta e non si riscrive da solo: vale finche' nessuno salva."""
        from mirto.actions.blocchi import blocchi_di
        from mirto.actions.engine import ActionSpec

        vecchia = {"name": "x", "action": "sirena", "node": "n1", "kind": "generic",
                   "targets": ["person", "cat"], "min_confidence": 0.7}
        teste = [(b.soggetti, b.confidenza) for b in blocchi_di(vecchia) if b.ascolta]
        self.assertEqual(teste, [(["person", "cat"], 0.7)])
        spec = ActionSpec.from_dict(dict(vecchia, confidenze={"cat": 0.9, "sparito": 0.5}))
        self.assertEqual(spec.confidenze, {"cat": 0.9}, "le soglie orfane si buttano in lettura")

    def test_the_line_says_who_judges_and_where_the_number_comes_from(self):
        from mirto.actions.confidenze import YAMNET, YOLOX, riga_modello

        riga = riga_modello(YOLOX, self.leggi, mie=2)
        self.assertIn("YOLOX-S", riga, "il modello scelto, non quello di serie")
        self.assertIn("60%", riga)
        self.assertIn("2", riga, "quante soglie sono tue")
        sola = riga_modello(YOLOX, self.leggi, mie=0)
        self.assertIn("standard", sola)
        self.assertIn("YAMNet", riga_modello(YAMNET, self.leggi))


class OgniInputDeveAvereIlSuoRiconoscimentoTest(unittest.TestCase):
    """Una regola per input, non una in tutto (24/10)."""

    def test_nothing_ticked_asks_for_a_source_first(self):
        from mirto.actions.pannello_azione import manca_negli_input

        self.assertIn("camera", manca_negli_input([], [], [], []))
        self.assertIn("camera", manca_negli_input([], [], ["person"], []),
                      "un'azione nuova nasce con «persona» spuntata: non e' una scelta fatta")
        self.assertEqual(manca_negli_input([], [], ["person"], [], nuova=False), "",
                         "un'azione gia' salvata senza fonti spuntate vuol dire «tutte»")
        self.assertEqual(manca_negli_input([], [], [], [], scegliibili=False), "",
                         "senza camere e senza microfoni non si puo' chiedere di sceglierne uno")

    def test_a_ticked_microphone_that_listens_to_nothing_is_not_allowed(self):
        from mirto.actions.pannello_azione import manca_negli_input

        self.assertIn("suono", manca_negli_input(["Ingresso"], ["Mic"], ["person"], []))
        self.assertIn("riconoscere", manca_negli_input(["Ingresso"], [], [], []))
        self.assertEqual(manca_negli_input(["Ingresso"], ["Mic"], ["person"], ["sound:glass"]), "")

    def test_a_grid_shows_only_for_an_input_you_chose(self):
        from mirto.actions.pannello_azione import si_vede_la_griglia

        self.assertFalse(si_vede_la_griglia([], []))
        self.assertTrue(si_vede_la_griglia(["Ingresso"], []))
        self.assertTrue(si_vede_la_griglia([], ["person"]), "un'azione gia' salvata resta modificabile")
        self.assertFalse(si_vede_la_griglia([], ["person"], nuova=True),
                         "in una nuova, «persona» spuntata di nascita non fa comparire la griglia")


class LaGrigliaNonFaUnRotoloTest(unittest.TestCase):
    """Venti riquadri su quattro colonne fanno cinque righe, e la finestra diventa piu' alta dello
    schermo. Marco, 24/10: «la finestra e' troppo alta e i box troppo grossi»."""

    def test_six_boxes_fit_in_a_row_of_the_wider_window(self):
        from mirto.gui.sistema import colonne_griglia

        utile = 1000 - 36 - 200          # finestra meno margini meno la colonna delle etichette
        self.assertEqual(colonne_griglia(utile, 118, 8, 6), 6)
        self.assertEqual(-(-18 // 6), 3, "diciotto riquadri in tre righe, non cinque")


class UnSuonoTuoNonHaLaStessaIconaTest(unittest.TestCase):
    """Marco, 24/10: *«i suoni, identificati dalla stessa icona, cosa che vorrei fosse diversa se
    audio veri o generati»*.

    Un verso di falco che il nodo si costruisce da solo e un brano che hai caricato tu non sono la
    stessa cosa: il primo esce da una formula, il secondo e' un file che occupa posto e che puoi
    togliere. Il nodo la distinzione ce l'ha gia' — nel suo elenco ogni suono dice se e' «nostro» —,
    mancava solo farla arrivare fin dentro la finestra.
    """

    COMANDI = [{"nome": "dissuasore", "tipo": "deterrent", "suoni": ["falco", "scary_music_box"]},
               {"nome": "sirena", "tipo": "siren"}]

    def test_the_speaker_is_the_node_and_the_note_is_yours(self):
        from mirto.actions.pannello_catena import SUONO_GENERATO, SUONO_TUO, icona_suono

        self.assertEqual(icona_suono("falco", ["falco", "ufo"]), SUONO_GENERATO)
        self.assertEqual(icona_suono("scary_music_box", ["falco", "ufo"]), SUONO_TUO)

    def test_without_knowing_nobody_is_called_yours(self):
        """Nodo spento, o versione vecchia che non dichiara i suoi suoni: si tace, non si indovina."""
        from mirto.actions.pannello_catena import SUONO_GENERATO, icona_suono, suoni_del_nodo

        self.assertEqual(icona_suono("qualunque"), SUONO_GENERATO)
        self.assertTrue(all(v["icona"] == SUONO_GENERATO for v in suoni_del_nodo(self.COMANDI)),
                        "dire «e' tuo» di un suono che non lo e' e' peggio che non dire niente")

    def test_the_plus_says_where_the_sound_comes_from(self):
        from mirto.actions.pannello_catena import SUONO_TUO, proposte

        voci = {p.testo: p for p in proposte([], self.COMANDI, casse=["cassa"], nostri=["falco"])}
        self.assertEqual(voci["scary_music_box"].icona, SUONO_TUO)
        self.assertIn("caricato", voci["scary_music_box"].spiega)
        self.assertIn("nodo", voci["falco"].spiega)

    def test_a_command_with_no_sounds_of_its_own_is_always_the_node(self):
        """La sirena ha un verso solo e non lo dichiara: non puo' essere un file tuo."""
        from mirto.actions.pannello_catena import SUONO_GENERATO, suoni_del_nodo

        sua = next(v for v in suoni_del_nodo(self.COMANDI, nostri=[]) if v["comando"] == "sirena")
        self.assertEqual(sua["icona"], SUONO_GENERATO)


class IlDecimoNumeroNonEUnDecimoTest(unittest.TestCase):
    """Passando il nodo da API 1.9 a 1.10, un confronto fra numeri si rompeva in silenzio.

    `float("1.10")` fa **1,1**, cioe' meno di `float("1.9")`. L'avviso «il nodo ha una versione
    vecchia» sarebbe sparito per ogni nodo di questo mondo, e nessuna prova se ne sarebbe accorta:
    non e' un errore, e' un numero che vuol dire un'altra cosa.
    """

    def test_ten_comes_after_nine(self):
        from mirto.actions.diagnosi import versione

        self.assertGreater(versione("1.10"), versione("1.9"))
        self.assertGreater(versione("2.0"), versione("1.99"))
        self.assertEqual(versione("1.10"), (1, 10))

    def test_a_version_that_is_not_one_is_empty_not_zero(self):
        """Vuoto vuol dire «non lo so», e chi legge non avvisa: un nodo muto non e' un nodo vecchio."""
        from mirto.actions.diagnosi import versione

        for storta in ("", None, "boh", "?"):
            self.assertEqual(versione(storta), ())
        self.assertEqual(versione("1.x"), (1,), "quello che si capisce si tiene")

    def test_an_old_node_is_still_called_old(self):
        from mirto.actions.diagnosi import API_ATTESA, diagnosi, versione

        self.assertGreater(versione(API_ATTESA), versione("1.9"))
        detti = diagnosi({"versione_api": "1.5"}, {})
        self.assertTrue(any("versione vecchia" in str(g) for g in detti),
                        f"un nodo ad API 1.5 deve essere segnalato: {detti}")
        zitti = diagnosi({"versione_api": API_ATTESA}, {})
        self.assertFalse(any("versione vecchia" in str(g) for g in zitti))


class IlPiuChiedePrimaDoveTest(unittest.TestCase):
    """Marco, 24/10: *«quando faccio aggiungi pezzo mi mostra le possibilita' di suono/luci ma non i
    dispositivi; devo scegliere prima il dispositivo/nodo e poi da li' vedo cosa c'e' di collegato»*.

    Prima il menu era una lista piatta di cose da fare, e **dove** si facessero era una domanda
    nascosta dentro il pezzo. Adesso l'ordine e' quello del ragionamento: dove, con cosa, cosa.
    """

    COMANDI = [{"nome": "dissuasore", "tipo": "deterrent", "suoni": ["falco", "brano_tuo"]},
               {"nome": "sirena", "tipo": "siren"},
               {"nome": "luci", "tipo": "light", "descrizione": "LED verde del Raspberry"}]
    USCITE = [{"chiave": "usb:UACDemoV10", "etichetta": "Cassa USB"},
              {"chiave": "bluetooth:AA:BB", "etichetta": "Cassa del portico"}]

    def test_the_components_are_what_the_device_has_attached(self):
        from mirto.actions.pannello_catena import GENERE_LUCE, GENERE_SUONO, componenti_di

        pezzi = componenti_di(self.COMANDI, self.USCITE)
        self.assertEqual([(c.genere, c.chiave) for c in pezzi],
                         [(GENERE_SUONO, "usb:UACDemoV10"), (GENERE_SUONO, "bluetooth:AA:BB"),
                          (GENERE_LUCE, "luci")],
                         "una voce per cassa, e le luci sono componenti loro stesse")

    def test_an_old_node_still_has_a_speaker(self):
        """Un nodo prima dell'API 1.10 non dichiara le uscite: non e' un nodo senza casse."""
        from mirto.actions.pannello_catena import GENERE_SUONO, componenti_di

        pezzi = componenti_di(self.COMANDI)
        casse = [c for c in pezzi if c.genere == GENERE_SUONO]
        self.assertEqual(len(casse), 1)
        self.assertEqual(casse[0].chiave, "", "senza chiave: il nodo suona da dove e' configurato")

    def test_a_device_with_no_speakers_has_no_sound_components(self):
        from mirto.actions.pannello_catena import GENERE_SUONO, componenti_di

        solo_luci = [c for c in self.COMANDI if c["nome"] == "luci"]
        pezzi = componenti_di(solo_luci, self.USCITE)
        self.assertFalse([c for c in pezzi if c.genere == GENERE_SUONO],
                         "le casse si offrono se c'e' qualcosa che suona")

    def test_what_a_speaker_can_do_is_the_sounds(self):
        from mirto.actions.pannello_catena import SUONO_TUO, componenti_di, cose_da_fare

        cassa = componenti_di(self.COMANDI, self.USCITE)[0]
        cose = cose_da_fare(cassa, self.COMANDI, nostri=["falco"], dispositivo="nodo:n1")
        self.assertEqual([p.testo for p in cose], ["falco", "brano_tuo", "sirena"])
        self.assertEqual([p.icona for p in cose][1], SUONO_TUO, "l'icona arriva fin qui")
        for p in cose:
            self.assertTrue(p.chiave.startswith("pezzo:nodo:n1|usb:UACDemoV10|"),
                            f"la voce porta dove e da quale cassa: {p.chiave}")

    def test_a_light_does_one_thing_and_says_so(self):
        from mirto.actions.pannello_catena import componenti_di, cose_da_fare

        luce = componenti_di(self.COMANDI, self.USCITE)[-1]
        cose = cose_da_fare(luce, self.COMANDI, dispositivo="nodo:n1")
        self.assertEqual(len(cose), 1, "per una luce il componente e la cosa da fare coincidono")
        self.assertEqual(cose[0].chiave, "pezzo:nodo:n1||luci|", "nessuna cassa: non suona")

    def test_without_a_speaker_the_voice_stays_and_says_what_is_missing(self):
        """E lo dice **sul componente**, non sui suoi suoni: altrimenti finisce dentro un sotto-menu.

        Il 24/10 l'avevo messo sui suoni, e una prova di finestra l'ha preso: nel menu si vedeva
        «La cassa del nodo» e basta, e il «ti manca una cassa» stava un livello sotto — cioe'
        nascosto, che e' l'opposto di quello che deve fare.
        """
        from mirto.actions.pannello_catena import componenti_di

        cassa = componenti_di(self.COMANDI, None, casse=[])[0]
        self.assertIn("cassa", cassa.manca)
        self.assertEqual(cassa.scorciatoia, "audio_del_nodo",
                         "non «non puoi»: «ti serve una cassa, vieni che te la configuro»")
        zitta = componenti_di(self.COMANDI, None, casse=None)[0]
        self.assertEqual(zitta.manca, "", "«non lo so» non e' «non ne ha»")

    def test_the_piece_that_is_born_knows_where_and_from_which_speaker(self):
        from mirto.actions.pannello_catena import passo_da_pezzo

        passo = passo_da_pezzo("pezzo:nodo:n1|usb:UACDemoV10|dissuasore|falco", self.COMANDI)
        self.assertEqual((passo.comando, passo.suono), ("dissuasore", "falco"))
        self.assertEqual(passo.dispositivo, "nodo:n1")
        self.assertEqual(passo.uscita, "usb:UACDemoV10")
        self.assertGreater(passo.durata, 0, "nasce con una durata sensata, non da riempire")
        self.assertIsNone(passo_da_pezzo("suono:dissuasore:falco", self.COMANDI),
                          "una voce del menu vecchio non e' una voce di questo")

    def test_the_speaker_travels_all_the_way_to_the_node(self):
        """Dal pezzo alla richiesta: se si perde per strada, la cassa scelta non serve a niente."""
        from mirto.actions.passi import Passo, srotola

        passo = Passo(comando="dissuasore", suono="falco", durata=3, uscita="usb:UACDemoV10")
        self.assertEqual(srotola([passo], 0, 0)[0]["uscita"], "usb:UACDemoV10")
        self.assertEqual(passo.come_dict()["uscita"], "usb:UACDemoV10")
        self.assertNotIn("uscita", Passo(comando="luci").come_dict(),
                         "un pezzo che non la sceglie non guadagna una riga")


class DuePezziNelloStessoIstanteTest(unittest.TestCase):
    """Tappa 3: il pezzo che fa piu' cose **insieme**.

    Il caso che l'ha chiesta, da Marco il 24/10: *«la camera avvisa che c'e' una persona fuori dal
    cancello emettendo un suono sulla cassa bluetooth ed in contemporanea accende la striscia di led
    USB, per veder meglio lei e per far luce al visitatore»*.

    Qui si prova il **dato** e il conto del tempo; che la chiamata dopo parta senza aspettare lo
    prova `test_the_engine_does_not_wait_for_a_piece_that_starts_together`, qui sotto, guardando con
    che attesa il motore la programma.
    """

    def test_together_does_not_add_up(self):
        """Un suono da un secondo e una luce da trenta, insieme, sono trenta — non trentuno."""
        from mirto.actions.passi import Passo, quanto_dura, srotola

        catena = srotola([Passo(comando="dissuasore", suono="falco", durata=1),
                          Passo(comando="luci", durata=30, nel_gruppo=True),
                          Passo(comando="sirena", durata=2)], 60, 1)
        self.assertEqual([c["nel_gruppo"] for c in catena], [False, True, False])
        self.assertEqual(quanto_dura(catena), 32.0,
                         "il gruppo dura quanto la sua piu' lunga, poi si va avanti")

    def test_together_on_the_first_piece_means_nothing(self):
        """Non c'e' un «prima» con cui stare insieme: si ignora, non si rifiuta."""
        from mirto.actions.passi import Passo, srotola

        catena = srotola([Passo(comando="luci", durata=5, nel_gruppo=True)], 60, 1)
        self.assertEqual([c["nel_gruppo"] for c in catena], [False])

    def test_repetitions_of_one_piece_stay_in_a_row(self):
        """«Tre volte insieme» non vuol dire niente: insieme vale per la prima, le altre seguono."""
        from mirto.actions.passi import Passo, srotola

        catena = srotola([Passo(comando="sirena", durata=1),
                          Passo(comando="luci", durata=1, ripeti=3, nel_gruppo=True)], 60, 1)
        self.assertEqual([c["nel_gruppo"] for c in catena], [False, True, False, False])

    def test_a_piece_that_is_not_in_a_group_writes_nothing(self):
        from mirto.actions.passi import Passo

        scritto = Passo(comando="luci").come_dict()
        self.assertNotIn("nel_gruppo", scritto)
        self.assertNotIn("a_turno", scritto)

    def test_the_old_name_is_read_for_ever(self):
        """Le azioni salvate il 24/10 dicono `insieme`, e devono continuare a funzionare.

        Il campo si chiamava cosi' per un giorno, poi quel nome e' diventato falso: era vero per il
        pezzo di un gruppo che non suona insieme a nessuno (tappa 5). Il nome e' cambiato nel codice,
        **non sul disco di chi l'aveva gia' salvato**: si legge il vecchio e si scrive il nuovo, e
        non c'e' niente da convertire.
        """
        from mirto.actions.passi import Passo

        vecchio = Passo.da_dict({"comando": "luci", "insieme": True})
        self.assertTrue(vecchio.nel_gruppo)
        self.assertFalse(vecchio.a_turno, "«insieme» era l'unico modo che c'era: resta quello")
        self.assertEqual(vecchio.come_dict().get("nel_gruppo"), True, "riscritta col nome nuovo")
        self.assertTrue(Passo.da_dict({"comando": "luci", "nel_gruppo": True}).nel_gruppo)

    def tempi_di(self, catena):
        """Con che attesa il motore programma ogni chiamata dopo la prima.

        Si chiama `_programma_dopo` per quello che e', con un `_schedule` che scrive invece di
        aspettare. Fino al 24/10 questa prova leggeva il **sorgente** del motore e cercava una riga
        dentro: ha fatto il suo mestiere per un giorno, poi quella riga si e' spostata in un metodo
        suo e la prova si e' rotta senza che il comportamento fosse cambiato di niente. Una prova che
        guarda com'e' scritto il codice invece di cosa fa si rompe quando il codice migliora.
        """
        from mirto.actions.engine import ActionEngine

        class Finta:
            def __init__(self):
                self.tempi = []

            def _schedule(self, fra, _fn, *_a):
                self.tempi.append(round(fra, 3))

            def _esegui_passo(self, *_a):
                return True

        fuori = []
        for i in range(len(catena)):
            finta = Finta()
            ActionEngine._programma_dopo(finta, None, None, catena, i)
            fuori += finta.tempi
        return fuori

    def test_the_engine_does_not_wait_for_a_piece_that_starts_together(self):
        """Chi parte insieme si programma a **zero**: e' tutto il parallelo fra due dispositivi."""
        from mirto.actions.passi import Passo, srotola

        catena = srotola([Passo(comando="dissuasore", suono="sparo", durata=2),
                          Passo(comando="luci", durata=30, nel_gruppo=True)], 60, 1)
        self.assertEqual(self.tempi_di(catena), [0.0],
                         "la luce non aspetta il suono: parte nello stesso istante")

    def test_what_comes_after_a_group_waits_for_the_longest_of_it(self):
        """Il difetto che la tappa 4 ha trovato nella tappa 3: si aspettava **l'ultima**, non la piu' lunga.

        Suono da un secondo e luce da trenta, insieme, poi una sirena. La sirena partiva dopo un
        secondo — la durata dell'ultima chiamata del gruppo — mentre il conto dell'azione diceva
        trenta. Due verita' sullo stesso tempo, e quella che si sente e' sempre l'altra.
        """
        from mirto.actions.passi import Passo, quanto_dura, srotola

        catena = srotola([Passo(comando="luci", durata=30),
                          Passo(comando="dissuasore", suono="sparo", durata=1, nel_gruppo=True),
                          Passo(comando="sirena", durata=5)], 60, 1)
        self.assertEqual(self.tempi_di(catena), [0.0, 30.0],
                         "la sirena aspetta la luce, che sta ancora accesa")
        self.assertEqual(quanto_dura(catena), 35.0,
                         "ed e' lo stesso conto che fa quanto_dura: una verita' sola sul tempo")

    def test_a_single_piece_waits_exactly_as_before(self):
        """Niente cambia per le azioni gia' salvate: un gruppo di uno dura quanto quello."""
        from mirto.actions.passi import Passo, srotola

        catena = srotola([Passo(comando="sirena", durata=4, pausa=2),
                          Passo(comando="luci", durata=9)], 60, 1)
        self.assertEqual(self.tempi_di(catena), [6.0], "durata piu' attesa, come sempre")


class DueSuoniSullaStessaCassaTest(unittest.TestCase):
    """Tappa 4: due operazioni «insieme» che finiscono sulla stessa cassa non suonano in due.

    Il nodo dall'API 1.11 lo dice — rifiuta la seconda invece di risponder «ok» e ammazzare la prima
    — ma scoprirlo quando l'azione scatta e' tardi: qui si dice **mentre si costruisce**, che e'
    l'unico momento in cui cambiare cassa non costa niente.

    La regola che conta e' la seconda meta': **si accusa solo quando si e' certi.** Un pezzo che
    nomina la cassa USB e un pezzo senza uscita, su un nodo di cui non sappiamo com'e' configurato,
    potrebbero essere la stessa linea o due diverse. Dire «e' un conflitto» quando non si sa e' il
    modo di farsi ignorare la volta che conta davvero.
    """

    COMANDI = [{"nome": "dissuasore", "tipo": "deterrent", "suoni": ["sparo", "corvi"]},
               {"nome": "sirena", "tipo": "siren"},
               {"nome": "luci", "tipo": "light"}]

    def contese(self, passi, **kw):
        from mirto.actions.pannello_catena import casse_contese

        return casse_contese(passi, self.COMANDI, **kw)

    def test_two_sounds_together_without_a_speaker_are_the_same_speaker(self):
        """Nessuno dei due nomina la cassa: vogliono dire «quella configurata», cioe' la stessa."""
        from mirto.actions.passi import Passo

        contese = self.contese([Passo(comando="dissuasore", suono="sparo", durata=2),
                                Passo(comando="sirena", durata=5, nel_gruppo=True)])
        self.assertEqual(list(contese), [1], "il secondo e' quello che non suonerebbe")
        self.assertIn("SPARO", contese[1], "si dice con chi litiga, non solo che litiga")

    def test_two_sounds_on_two_speakers_are_fine(self):
        from mirto.actions.passi import Passo

        self.assertEqual(self.contese([
            Passo(comando="dissuasore", suono="sparo", durata=2, uscita="bluetooth:AA:BB"),
            Passo(comando="sirena", durata=5, nel_gruppo=True, uscita="usb:UACDemoV10")]), {},
            "due casse sono due linee: e' la capacita' nuova, non un conflitto")

    def test_the_same_named_speaker_twice_is_a_conflict(self):
        from mirto.actions.passi import Passo

        self.assertEqual(list(self.contese([
            Passo(comando="dissuasore", suono="sparo", durata=2, uscita="usb:X"),
            Passo(comando="sirena", durata=5, nel_gruppo=True, uscita="usb:X")])), [1])

    def test_named_and_unnamed_are_a_conflict_only_when_we_know_the_node(self):
        from mirto.actions.passi import Passo

        passi = [Passo(comando="dissuasore", suono="sparo", durata=2, uscita="usb:X"),
                 Passo(comando="sirena", durata=5, nel_gruppo=True)]
        self.assertEqual(self.contese(passi, dispositivo="nodo:n1"), {},
                         "non sappiamo com'e' configurato: non si accusa")
        self.assertEqual(list(self.contese(passi, dispositivo="nodo:n1",
                                           in_uso={"nodo:n1": "usb:X"})), [1],
                         "appena si sa che la configurata e' quella, sono la stessa linea")

    def test_one_after_the_other_is_never_a_conflict(self):
        """In fila la cassa e' libera quando tocca al secondo: e' proprio il rimedio che suggeriamo."""
        from mirto.actions.passi import Passo

        self.assertEqual(self.contese([Passo(comando="dissuasore", suono="sparo", durata=2),
                                       Passo(comando="sirena", durata=5)]), {})

    def test_a_light_does_not_take_a_speaker(self):
        """Il caso del cancello: suono e luce insieme. Niente da dire — non si contendono niente."""
        from mirto.actions.passi import Passo

        self.assertEqual(self.contese([Passo(comando="dissuasore", suono="sparo", durata=2),
                                       Passo(comando="luci", durata=30, nel_gruppo=True)]), {})

    def test_three_together_two_on_one_speaker(self):
        """Tre insieme, due sulla stessa: si accusa solo il terzo, e nomina il primo dei due."""
        from mirto.actions.passi import Passo

        contese = self.contese([Passo(comando="dissuasore", suono="sparo", durata=2, uscita="usb:X"),
                                Passo(comando="luci", durata=9, nel_gruppo=True),
                                Passo(comando="sirena", durata=5, nel_gruppo=True, uscita="usb:X")])
        self.assertEqual(list(contese), [2])
        self.assertIn("SPARO", contese[2])

    def test_the_box_shows_it_instead_of_the_usual_hint(self):
        """Il riquadro porta **questo** avviso, non quello che spiega com'e' fatto un dissuasore.

        Un avviso solo per riquadro: quello del tipo spiega una cosa che si impara una volta, questo
        dice «questo pezzo non suonera'». Se vincesse l'altro, l'unico che conta non si vedrebbe.
        """
        from mirto.actions.pannello_catena import riquadri
        from mirto.actions.passi import Passo

        caselle = riquadri([Passo(comando="dissuasore", suono="sparo", durata=2),
                            Passo(comando="dissuasore", suono="corvi", durata=5, nel_gruppo=True)],
                           self.COMANDI)
        self.assertIn("SPARO", caselle[1].avviso)
        self.assertTrue(caselle[0].unito, "la freccia fra i due dice che partono insieme")

    def test_the_groups_are_read_as_the_chain_is_written(self):
        from mirto.actions.pannello_catena import gruppi_di_passi
        from mirto.actions.passi import Passo

        passi = [Passo(comando="a"), Passo(comando="b", nel_gruppo=True), Passo(comando="c"),
                 Passo(comando="d", nel_gruppo=True), Passo(comando="e", nel_gruppo=True)]
        self.assertEqual(gruppi_di_passi(passi), [[0, 1], [2, 3, 4]])

    def test_together_on_the_first_step_is_its_own_group(self):
        """Non c'e' un «prima» con cui stare insieme: resta un gruppo suo, come fa `srotola`."""
        from mirto.actions.pannello_catena import gruppi_di_passi
        from mirto.actions.passi import Passo

        self.assertEqual(gruppi_di_passi([Passo(comando="a", nel_gruppo=True), Passo(comando="b")]),
                         [[0], [1]])


class UnoPerVoltaATurnoTest(unittest.TestCase):
    """Tappa 5: di un gruppo «a turno» a ogni scatto ne parte **uno solo**, e la volta dopo il prossimo.

    Marco il 24/10, scegliendo fra i due significati possibili: *«a ogni scatto ne parte una sola, e
    allo scatto dopo tocca a un'altra»*. E' quello che il dissuasore fa gia' con i suoi suoni, per
    una ragione che vale anche qui: gli animali si abituano in fretta a un suono solo, e il gatto non
    impara che il rumore viene sempre da quella parte.

    L'altro significato possibile — una dopo l'altra dentro lo stesso scatto — l'abbiamo scartato
    perche' e' **gia'** quello che fa la catena mettendo due pezzi in fila: due modi per dire la
    stessa cosa sono il doppione che poi si paga.
    """

    def gruppo(self, **capo):
        from mirto.actions.passi import Passo

        return [Passo(comando="dissuasore", suono="sparo", durata=2, a_turno=True, **capo),
                Passo(comando="dissuasore", suono="corvi", durata=2, nel_gruppo=True),
                Passo(comando="dissuasore", suono="falco", durata=2, nel_gruppo=True)]

    def test_each_firing_starts_one_of_them_in_turn(self):
        from mirto.actions.passi import scegli_a_turno

        passi = self.gruppo()
        giri = [[p.suono for p in scegli_a_turno(passi, g)] for g in range(5)]
        self.assertEqual(giri, [["sparo"], ["corvi"], ["falco"], ["sparo"], ["corvi"]],
                         "un giro in ordine, non a caso: dopo tre scatti sai cos'ha suonato")

    def test_the_chosen_one_leads_its_own_group(self):
        """Chi parte non ha piu' nessuno a cui attaccarsi, e la scelta non si rifa' piu' a valle."""
        from mirto.actions.passi import scegli_a_turno

        scelto = scegli_a_turno(self.gruppo(), 1)[0]
        self.assertEqual(scelto.suono, "corvi")
        self.assertFalse(scelto.nel_gruppo, "e' lui il capogruppo di questo scatto")
        self.assertFalse(scelto.a_turno, "la scelta e' fatta: chi esegue non scelga una seconda volta")

    def test_the_pieces_around_it_are_untouched(self):
        from mirto.actions.passi import Passo, scegli_a_turno

        passi = [Passo(comando="luci", durata=9), *self.gruppo(), Passo(comando="sirena", durata=4)]
        for giro in range(3):
            with self.subTest(giro=giro):
                fuori = [p.comando for p in scegli_a_turno(passi, giro)]
                self.assertEqual(fuori, ["luci", "dissuasore", "sirena"])

    def test_a_group_of_one_is_not_a_rotation(self):
        """«A turno» da solo non vuol dire niente: non si rifiuta, si ignora."""
        from mirto.actions.passi import Passo, scegli_a_turno

        solo = [Passo(comando="luci", durata=5, a_turno=True)]
        self.assertEqual([p.comando for p in scegli_a_turno(solo, 7)], ["luci"])

    def test_a_group_that_starts_together_is_not_touched(self):
        from mirto.actions.passi import scegli_a_turno

        insieme = self.gruppo()
        insieme[0].a_turno = False
        self.assertEqual(len(scegli_a_turno(insieme, 1)), 3, "insieme vuol dire tutti, a ogni scatto")

    def test_the_mode_is_written_in_one_place_only(self):
        """Il modo sta sul **capogruppo**: un gruppo «uno insieme e uno a turno» non si puo' scrivere.

        Non e' una cosa che l'interfaccia evita: e' una cosa che non esiste nel dato. Se il modo
        stesse su ogni pezzo, quel gruppo si potrebbe scrivere — e nessuno saprebbe cosa vuol dire.
        """
        from mirto.actions.passi import A_TURNO, INSIEME, gruppi_di_passi, modo_del_gruppo

        passi = self.gruppo()
        gruppi = gruppi_di_passi(passi)
        self.assertEqual(gruppi, [[0, 1, 2]])
        self.assertEqual(modo_del_gruppo(passi, gruppi[0]), A_TURNO)
        passi[0].a_turno = False
        self.assertEqual(modo_del_gruppo(passi, gruppi[0]), INSIEME)
        self.assertEqual(modo_del_gruppo(passi, [0]), "", "un pezzo solo non parte ne' insieme ne' a turno")

    def test_the_head_of_my_group_is_where_the_mode_lives(self):
        from mirto.actions.passi import Passo, capogruppo

        passi = [Passo(comando="a"), *self.gruppo()]
        self.assertEqual([capogruppo(passi, i) for i in range(4)], [0, 1, 1, 1])
        self.assertIsNone(capogruppo(passi, 9))

    def test_the_arrow_says_which_of_the_two(self):
        """Si legge a occhio quale dei due: sono due cose diverse, non due parole per la stessa."""
        from mirto.actions.pannello_catena import riquadri

        comandi = [{"nome": "dissuasore", "tipo": "deterrent", "suoni": ["sparo", "corvi", "falco"]}]
        a_turno = riquadri(self.gruppo(), comandi)
        self.assertEqual(a_turno[0].freccia, "a turno")
        insieme = self.gruppo()
        insieme[0].a_turno = False
        self.assertEqual(riquadri(insieme, comandi)[0].freccia, "insieme")

    def test_a_rotation_never_fights_over_a_speaker(self):
        """Ne parte uno solo: due cose che non suonano mai insieme non si rubano la linea.

        E' anche il rimedio che si suggerisce per una cassa contesa — accusarlo vorrebbe dire
        accusare la propria risposta.
        """
        from mirto.actions.pannello_catena import casse_contese

        comandi = [{"nome": "dissuasore", "tipo": "deterrent", "suoni": ["sparo", "corvi", "falco"]}]
        self.assertEqual(casse_contese(self.gruppo(), comandi), {},
                         "tre suoni sulla stessa cassa, ma uno per scatto")
        insieme = self.gruppo()
        insieme[0].a_turno = False
        self.assertEqual(sorted(casse_contese(insieme, comandi)), [1, 2],
                         "insieme invece e' il conflitto vero, e si dice")


class IlRiquadroDiceAncheDaDoveTest(unittest.TestCase):
    """Marco, 25/10: *«nel box si deve vedere anche il dispositivo utilizzato: UFO 3x USB»*.

    Da quando un'azione puo' suonare da casse diverse, un riquadro che dice solo «× 3» non dice dove
    si sentira' — e due riquadri identici possono essere due stanze diverse.
    """

    COMANDI = [{"nome": "dissuasore", "tipo": "deterrent", "suoni": ["ufo"], "durate": {"ufo": 2.0}},
               {"nome": "luci", "tipo": "light"}]

    def righe(self, passo, **kw):
        from mirto.actions.pannello_catena import riquadri

        return riquadri([passo], self.COMANDI, **kw)[0].righe

    def test_the_named_speaker_is_written_in_the_box(self):
        from mirto.actions.passi import Passo

        righe = self.righe(Passo(comando="dissuasore", suono="ufo", volte=3, uscita="usb:UACDemoV10"))
        self.assertEqual(righe[0], "3 volte · USB")

    def test_the_configured_speaker_is_named_when_we_know_it(self):
        from mirto.actions.passi import Passo

        righe = self.righe(Passo(comando="dissuasore", suono="ufo", volte=1), dispositivo="nodo:n1",
                           in_uso={"nodo:n1": "bluetooth:AA:BB:CC:DD:EE:FF"})
        self.assertEqual(righe[0], "una volta · Bluetooth",
                         "il nome della cassa no: quello e' una marca, e una marca non ci va")

    def test_nothing_is_written_when_we_do_not_know(self):
        """Un nome inventato e' peggio di nessun nome: chi legge crederebbe di sapere da dove suona."""
        from mirto.actions.passi import Passo

        self.assertEqual(self.righe(Passo(comando="dissuasore", suono="ufo", volte=1)), ["una volta"])

    def test_a_light_has_no_speaker(self):
        from mirto.actions.passi import Passo

        righe = self.righe(Passo(comando="luci", durata=30), dispositivo="nodo:n1",
                           in_uso={"nodo:n1": "usb:X"})
        self.assertEqual(righe[0], "accendi 30 s", "una luce non esce da una cassa")


class UnPezzoNasceConLaDomandaGiustaTest(unittest.TestCase):
    """Un suono appena aggiunto non puo' dire «suona 8 s» e poi farsi chiedere «quante volte».

    Marco, 25/10, aprendo col cog uno sparo appena aggiunto: *«arriva impostato con 8 secondi, ma se
    premo modifica mi dice quante volte»*. Due domande diverse sulla stessa cosa: il riquadro
    cronometrava, la finestra contava. La regola di quale delle due sia giusta e' una sola e sta in
    `passo_da_scelte` — il «+» adesso la chiede a lei invece di ricopiarla.
    """

    COMANDI = [{"nome": "dissuasore", "tipo": "deterrent", "suoni": ["sparo"], "durate": {"sparo": 0.9}},
               {"nome": "sirena", "tipo": "siren"},
               {"nome": "luci", "tipo": "light"}]

    def nato(self, uscita, comando, suono=""):
        from mirto.actions.pannello_catena import chiave_pezzo, passo_da_pezzo

        return passo_da_pezzo(chiave_pezzo("nodo:n1", uscita, comando, suono), self.COMANDI)

    def test_a_sound_the_node_can_measure_is_born_counted(self):
        from mirto.actions.pannello_catena import secondi_per

        p = self.nato("usb:X", "dissuasore", "sparo")
        self.assertEqual(p.volte, 1, "un colpo si conta, non si cronometra")
        self.assertEqual(p.durata, secondi_per(1, 0.9), "i secondi sono il conto di quel colpo")
        self.assertEqual(p.ripeti, 1, "«volte» e «ripeti» non vanno mai accese insieme")
        self.assertEqual(p.uscita, "usb:X", "e la cassa scelta resta sua")

    def test_a_siren_is_still_measured_in_seconds(self):
        """La sirena la genera il nodo apposta per tenerla accesa: li' i secondi sono la domanda giusta."""
        p = self.nato("", "sirena")
        self.assertEqual(p.volte, 0)
        self.assertEqual(p.durata, 5.0, "cinque secondi, non venti e non il minuto dell'azione")

    def test_a_light_is_not_a_sound(self):
        p = self.nato("", "luci")
        self.assertEqual((p.volte, p.durata), (0, 30.0))


class IlPacchettoDelNodoTest(unittest.TestCase):
    """N-02: quello che Mirto manda a un nodo per aggiornarlo.

    Il pacchetto e' **deterministico** — date e proprietari azzerati — per una ragione che si vede
    solo con piu' nodi: due computer che mandano lo stesso codice producono la stessa impronta, e
    l'impronta diventa un modo per dire «questi due nodi hanno la stessa roba dentro».
    """

    def test_the_version_is_read_from_the_file_not_imported(self):
        """Importare `mirto_node` dentro Mirto vorrebbe dire due programmi quasi omonimi in
        memoria, e prima o poi uno prende il posto dell'altro.

        La prova lo mostra con un nodo **finto**: se la versione la leggesse importando, da qui
        uscirebbe quella vera (o un errore), non la 7.7.7 che c'e' scritta in questo file. La prima
        stesura guardava invece `sys.modules`, ed era una prova di stato globale: passava da sola e
        falliva dentro la suite, perche' `mirto_node` lo importano le prove del nodo.
        """
        from mirto.actions.aggiorna_nodo import versione_che_abbiamo

        self.assertTrue(versione_che_abbiamo(), "questa copia porta il codice del nodo")
        finto = Path(tempfile.mkdtemp()) / "node"
        (finto / "mirto_node").mkdir(parents=True)
        (finto / "mirto_node" / "__init__.py").write_text('__version__ = "7.7.7"\n', encoding="utf-8")
        self.assertEqual(versione_che_abbiamo(finto), "7.7.7")

    def test_a_package_without_the_node_code_says_so(self):
        """In un pacchetto dell'app il codice del nodo potrebbe non esserci: va **detto**."""
        from pathlib import Path

        from mirto.actions.aggiorna_nodo import fai_pacchetto

        with self.assertRaises(FileNotFoundError):
            fai_pacchetto(Path(tempfile.mkdtemp()))

    def test_older_is_decided_number_by_number(self):
        """`float("0.10")` fa 0,1, cioe' meno di `float("0.9")`: e' lo scivolone del 24/10."""
        from mirto.actions.aggiorna_nodo import piu_vecchio

        self.assertTrue(piu_vecchio("0.9.0", "0.10.0"))
        self.assertTrue(piu_vecchio("0.11.0", "0.12.0"))
        self.assertFalse(piu_vecchio("0.12.0", "0.12.0"))
        self.assertFalse(piu_vecchio("0.13.0", "0.12.0"))

    def test_one_question_one_answer_for_all_three_places(self):
        """La riga del nodo in Sistema, la scheda in Impostazioni e il pulsante in «Stato e
        diagnosi» chiedono tutt'e tre la stessa cosa. Finche' se la rispondevano per conto loro,
        bastava che uno confrontasse le versioni in un altro modo per dire il contrario degli altri.
        """
        from mirto.actions.aggiorna_nodo import aggiornamento_del_nodo, versione_che_abbiamo

        nostra = versione_che_abbiamo()
        self.assertTrue(nostra, "questa copia porta il codice del nodo")
        self.assertEqual(aggiornamento_del_nodo({"versione": "0.1.0"}), ("0.1.0", nostra))
        self.assertIsNone(aggiornamento_del_nodo({"versione": nostra}),
                          "un nodo pari non ha niente da fare")
        self.assertIsNone(aggiornamento_del_nodo({}), "un nodo che non dice la versione non si tocca")
        self.assertIsNone(aggiornamento_del_nodo(None), "e nemmeno un nodo che non ha risposto")

    def test_an_unknown_version_is_not_called_old(self):
        """Un nodo che non dice la sua versione non si aggiorna d'ufficio: non si sa cos'ha."""
        from mirto.actions.aggiorna_nodo import piu_vecchio

        self.assertFalse(piu_vecchio("", "0.12.0"))
        self.assertFalse(piu_vecchio("0.11.0", ""))

    def test_the_client_sends_it_where_the_node_listens(self):
        """Il metodo si prova per quello che fa, senza costruire un client vero: un NodeClient vuole
        un certificato valido, e qui non si sta provando il certificato."""
        import base64
        import types

        from mirto.actions.nodes import NodeClient

        visti = []

        finto = types.SimpleNamespace(
            request=lambda method, path, payload=None, timeout=None: (
                visti.append((method, path, payload)) or {"esito": "ok"}))
        NodeClient.aggiorna(finto, b"ciao", "0.12.0", "abc")
        metodo, percorso, corpo = visti[0]
        self.assertEqual((metodo, percorso), ("POST", "/aggiorna"))
        self.assertEqual(corpo["versione"], "0.12.0")
        self.assertEqual(base64.b64decode(corpo["pacchetto"]), b"ciao",
                         "il pacchetto arriva intero, non descritto")
