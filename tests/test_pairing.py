# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Abbinamento dei nodi senza chiave copiata (fase 2 del refactor)."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "node"))

from mirto_node.server import Node, NodeServer, PairingWindow, _local_client  # noqa: E402

from mirto.actions import pairing  # noqa: E402
from mirto.actions.nodes import NodeClient, NodeConfig, NodeError, fetch_certificate, pair_node  # noqa: E402
from mirto.actions.signing import generate_key  # noqa: E402
from mirto.config import Config  # noqa: E402
from mirto.credentials import node_key_name  # noqa: E402
from tests.test_actions import mem_secrets  # noqa: E402
from tests.test_node import node_cert  # noqa: E402


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class WindowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.clock = Clock()
        self.flag = self.dir / "boot-flag"

    def tearDown(self):
        self.tmp.cleanup()

    def win(self):
        return PairingWindow(self.dir, clock=self.clock, boot_flag=self.flag, boot_pin=self.dir / "boot-pin.txt")

    def wrong(self, pin):
        return "000000" if pin != "000000" else "111111"

    def test_window_opens_only_when_asked(self):
        w = self.win()
        self.assertEqual(w.remaining(), 0)  # prima: 15 minuti a ogni avvio del servizio
        self.assertEqual(self.win().remaining(), 0, "e nemmeno riavviando il servizio")
        pin = w.open()
        # Nodo mai abbinato: questo PIN è quello stampato sul foglio che viaggia col dispositivo.
        # Se scadesse, chi accende il nodo una settimana dopo avrebbe in mano un numero morto.
        self.assertEqual(w.remaining(), PairingWindow.SENZA_SCADENZA)
        self.clock.t += 7 * 24 * 3600
        self.assertEqual(w.remaining(), PairingWindow.SENZA_SCADENZA, "il PIN stampato non scade da solo")
        self.assertEqual(w.pin(), pin, "ed è sempre lo stesso: quello stampato")
        self.assertTrue(w.claim("una settimana dopo", pin)[0])

    def test_the_window_of_an_already_paired_node_expires(self):
        """Dal primo abbinamento in poi la finestra torna a durare cinque minuti: nessun PIN eterno."""
        self.win().mark_paired("chiave")
        w = self.win()
        pin = w.open()
        self.assertEqual(w.remaining(), 300)
        self.clock.t += 301
        self.assertEqual(w.remaining(), 0)
        self.assertFalse(w.claim("tardi", pin)[0])

    def test_a_pin_lost_to_a_power_cut_is_made_again(self):
        """Il 20/10, staccando la corrente a un nodo nel minuto dopo l'accensione.

        «pin» e «abbina_fino» erano rimasti da **zero byte**: il nome del file registrato, il contenuto
        no. La finestra si presentava aperta (il file c'era) con un PIN che non esisteva, e l'abbinamento
        rispondeva «PIN scaduto»: su un nodo di un tester, senza terminale, quella microSD era da
        riscrivere. Ora un PIN illeggibile non conta come finestra aperta, e un nodo **mai abbinato** se
        ne genera uno nuovo da solo quando riparte.
        """
        w = self.win()
        vecchio = w.open()
        (self.dir / "pin").write_text("")          # come dopo uno spegnimento improvviso
        (self.dir / "abbina_fino").write_text("")
        w2 = self.win()                            # il servizio riparte
        nuovo = w2.pin()
        self.assertTrue(nuovo, "il nodo deve rifarsi un PIN, altrimenti non si abbina più")
        self.assertNotEqual(nuovo, vecchio)
        self.assertEqual(w2.remaining(), PairingWindow.SENZA_SCADENZA)
        self.assertTrue(w2.claim("Mirto", nuovo)[0])

    def test_a_pin_lost_on_a_paired_node_does_not_reopen_anything(self):
        """Ma su un nodo già abbinato no: lì una finestra che si riapre da sola sarebbe una porta."""
        self.win().mark_paired("chiave")
        w = self.win()
        w.open()
        (self.dir / "pin").write_text("")
        w2 = self.win()
        self.assertEqual(w2.pin(), "", "nessun PIN nuovo senza che qualcuno lo chieda")
        self.assertEqual(w2.remaining(), 0, "e la finestra è chiusa")

    def test_claim_needs_the_pin_and_works_once(self):
        w = self.win()
        pin = w.open()
        ok, why = w.claim("Mirto su mac", self.wrong(pin))
        self.assertFalse(ok)
        self.assertIn("PIN", why)
        self.assertTrue(w.claim("Mirto su mac", pin)[0])
        self.assertTrue(w.paired())
        self.assertEqual(w.remaining(), 0)
        self.assertFalse(w.claim("secondo", pin)[0])
        self.assertEqual(w.paired_info()["da"], "Mirto su mac")

    def test_paired_node_stays_closed_after_restart_until_reopened(self):
        self.win().mark_paired("chiave")
        w = self.win()  # riavvio del servizio
        self.assertEqual(w.remaining(), 0)
        pin = w.open()
        self.assertEqual(w.remaining(), 300)
        self.assertTrue(w.claim("secondo mac", pin)[0])
        self.assertEqual(w.remaining(), 0)

    def test_signed_request_does_not_close_explicit_window(self):
        w = self.win()
        w.mark_paired("chiave")  # il Mirto già abbinato continua a parlare col nodo
        w.open()
        w.mark_paired("chiave")
        self.assertEqual(w.remaining(), 300)

    def test_boot_flag_reopens_with_a_new_pin(self):
        self.win().mark_paired("chiave")
        self.flag.write_text("")
        w = self.win()
        self.assertEqual(w.remaining(), 300)
        self.assertRegex(w.pin(), r"^[0-9]{6}$")
        self.assertIn(w.pin(), (self.dir / "boot-pin.txt").read_text())
        self.assertFalse(self.flag.exists())

    def test_local_clients_only(self):
        for ip in ("192.168.1.5", "10.0.0.2", "127.0.0.1", "169.254.3.4", "fe80::1", "::ffff:192.168.1.9"):
            self.assertTrue(_local_client(ip), ip)
        for ip in ("8.8.8.8", "2001:4860::1", "boh"):
            self.assertFalse(_local_client(ip), ip)


class PairingHttpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        d = Path(self.tmp.name)
        os.environ["MIRTO_CONFIG_DIR"] = self.tmp.name
        os.environ["MIRTO_DATA_DIR"] = self.tmp.name
        self.cert, key = node_cert(d / "cert")
        self.key = generate_key()
        self.window = PairingWindow(d, boot_flag=d / "flag", boot_pin=d / "boot-pin.txt")
        self.node = Node("Ingresso", self.key, {}, None, cameras=lambda: [], addresses=lambda: [],
                         hostname=lambda: "mirto-ingresso.local", model=lambda: "Pi",
                         credentials=lambda: ("", ""), pairing=self.window)
        self.pin = self.window.open()  # come  mirto-nodo abbina
        self.srv = NodeServer(self.node, self.cert, key, host="127.0.0.1", port=0).start()
        self.url = f"https://127.0.0.1:{self.srv.port}"
        self.cfg = Config(d / "config.yaml")
        self.secrets = mem_secrets()

    def tearDown(self):
        self.srv.stop()
        self.tmp.cleanup()

    def test_state_and_claim(self):
        st = pairing.pairing_state(self.url)
        self.assertTrue(st["aperto"])
        self.assertEqual(st["nome"], "Ingresso")
        self.assertGreater(st["scade_tra_s"], 280)  # finestra di 5 minuti
        self.assertTrue(st["senza_scadenza"], "nodo mai abbinato: il PIN consegnato non scade")
        _pem, fp = fetch_certificate(self.url)
        self.assertEqual(st["impronta"], fp)

        found = {"nome": st["nome"], "url": "https://mirto-ingresso.local:8443", "contact": self.url,
                 "impronta": st["impronta"]}
        node_id = pairing.pair_found(self.cfg, self.secrets, found, "Ingresso", "Mirto di prova", self.pin)
        self.assertEqual(node_id, "ingresso")
        self.assertEqual(self.cfg.get("actions.nodes")[0]["url"], "https://mirto-ingresso.local:8443")
        self.assertEqual(self.secrets.get(node_key_name("ingresso")), self.key)
        self.assertIn("Mirto di prova", (Path(self.tmp.name) / "abbinato").read_text())
        self.assertFalse(pairing.pairing_state(self.url)["aperto"])
        with self.assertRaises(NodeError):
            pairing.claim(self.url, st["impronta"], pin=self.pin)  # una volta sola
        # la chiave consegnata funziona per le richieste firmate
        client = NodeClient(NodeConfig("ingresso", "Ingresso", self.url), self.cert.read_text(), self.key)
        self.assertEqual(client.status()["abbinamento"]["abbinato"], True)

    def test_without_the_pin_no_key(self):
        with self.assertRaises(NodeError) as ctx:
            pairing.claim(self.url, "", "Mirto")
        self.assertIn("PIN", str(ctx.exception))
        self.assertFalse(self.window.paired())
        pem, key, name = pairing.claim(self.url, "", "Mirto", self.pin)
        self.assertEqual((len(key), name), (64, "Ingresso"))
        self.assertTrue(pem.startswith("-----BEGIN CERTIFICATE"))

    def test_wrong_certificate_sends_nothing(self):
        with self.assertRaises(NodeError):
            pairing.claim(self.url, "AA:" * 31 + "AA", pin=self.pin)
        self.assertNotEqual(self.window.remaining(), 0, "la chiave non è stata consegnata")
        self.assertFalse(self.window.paired())

    def test_signed_request_marks_paired(self):
        client = NodeClient(NodeConfig("ingresso", "Ingresso", self.url), self.cert.read_text(), self.key)
        client.status()
        self.assertTrue(self.window.paired())
        self.assertTrue(self.node.pairing_state()["abbinato"])
        # la finestra aperta a mano resta aperta: l'utente l'ha aperta per abbinare un altro Mac
        self.assertTrue(pairing.pairing_state(self.url)["aperto"])
        self.window.close()
        self.assertFalse(pairing.pairing_state(self.url)["aperto"])

    def test_old_node_is_never_proposed(self):
        self.assertEqual(pairing.pairing_state("https://127.0.0.1:1")["aperto"], False)


class NomeDatoAlNodoTest(unittest.TestCase):
    """Il primo abbinamento battezza il nodo: il nome finisce **sul dispositivo**, non solo in Mirto.

    È il passo che rende possibile lo scenario delle cinque schede: si attacca un nodo al cavo, lo si
    abbina, gli si dà il nome, si stacca il cavo e si passa al successivo. Senza, si finirebbe con
    cinque nodi identici da battezzare dopo, a terminale, indovinando quale sia quale.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.d = d = Path(self.tmp.name)
        os.environ["MIRTO_CONFIG_DIR"] = self.tmp.name
        os.environ["MIRTO_DATA_DIR"] = self.tmp.name
        self.cert, key = node_cert(d / "cert")
        self.key = generate_key()
        self.toml = d / "node.toml"
        self.toml.write_text('nome = "Node 17a9"\n')
        self.window = PairingWindow(d, boot_flag=d / "flag", boot_pin=d / "boot-pin.txt")
        self.node = Node("Node 17a9", self.key, {}, None, cameras=lambda: [], addresses=lambda: [],
                         hostname=lambda: "mirto-node-17a9.local", model=lambda: "Pi",
                         credentials=lambda: ("", ""), pairing=self.window, folder=d, rename=self.scrivi)
        self.pin = self.window.open()
        self.srv = NodeServer(self.node, self.cert, key, host="127.0.0.1", port=0).start()
        self.url = f"https://127.0.0.1:{self.srv.port}"
        self.cfg = Config(d / "config.yaml")
        self.secrets = mem_secrets()

    def tearDown(self):
        self.srv.stop()
        self.tmp.cleanup()

    def scrivi(self, nome: str) -> None:
        self.toml.write_text(f'nome = "{nome}"\n')

    def trovato(self) -> dict:
        st = pairing.pairing_state(self.url)
        return {"nome": st["nome"], "url": "https://mirto-node-17a9.local:8443", "contact": self.url,
                "impronta": st["impronta"], "senza_scadenza": st["senza_scadenza"]}

    def url_salvato(self, node_id: str) -> str:
        return next(n["url"] for n in self.cfg.get("actions.nodes") if str(n["id"]) == node_id)

    def test_the_first_pairing_names_the_device_too(self):
        found = self.trovato()
        self.assertTrue(found["senza_scadenza"], "un nodo mai abbinato lo dice")
        node_id = pairing.pair_found(self.cfg, self.secrets, found, "Garage", "Mirto di prova", self.pin)
        self.assertEqual(self.node.name, "Garage")
        self.assertIn('nome = "Garage"', self.toml.read_text())
        self.assertEqual(self.url_salvato(node_id), "https://mirto-node-17a9.local:8443",
                         "il nome è un'etichetta: l'indirizzo salvato non si muove (deciso il 21/10)")

    def test_a_second_mirto_does_not_rename_a_node_in_service(self):
        found = dict(self.trovato(), senza_scadenza=False)   # nodo già in servizio: il nome è suo
        node_id = pairing.pair_found(self.cfg, self.secrets, found, "Garage", "Mirto di prova", self.pin)
        self.assertEqual(self.node.name, "Node 17a9")
        self.assertEqual(self.url_salvato(node_id), "https://mirto-node-17a9.local:8443")

    def test_a_node_that_cannot_be_named_is_paired_anyway(self):
        """Un nodo con la versione vecchia non conosce /configura/nome: l'abbinamento riesce comunque."""
        def rifiuta(_self, _nome):
            raise NodeError("Nodo: 404 percorso sconosciuto")

        with mock.patch.object(NodeClient, "set_name", rifiuta):
            node_id = pairing.pair_found(self.cfg, self.secrets, self.trovato(), "Garage", "Mirto", self.pin)
        self.assertEqual(self.url_salvato(node_id), "https://mirto-node-17a9.local:8443")
        self.assertTrue(self.window.paired())

    def test_renaming_a_paired_node_fixes_the_name_everywhere(self):
        """Chi scrive «Balocne» invece di «Balcone» non deve finire in un terminale per rimediare.

        Due cose, nell'ordine giusto: il dispositivo (se non accetta, non si cambia niente qui) e il
        nome in Mirto. L'indirizzo salvato **non** si tocca: dal 21/10 il nome è un'etichetta e il
        nome in rete resta quello di fabbrica.
        """
        from mirto.actions.nodes import rinomina_nodo

        node_id = pairing.pair_found(self.cfg, self.secrets, self.trovato(), "Balocne",
                                     "Mirto di prova", self.pin)
        prima = self.url_salvato(node_id)
        client = NodeClient(NodeConfig(node_id, "Balocne", self.url), self.cert.read_text(), self.key)
        esito = rinomina_nodo(self.cfg, node_id, "Balcone", client)
        self.assertEqual(esito["nome"], "Balcone")
        self.assertEqual(self.node.name, "Balcone", "il dispositivo per primo")
        self.assertIn('nome = "Balcone"', self.toml.read_text(), "e resta anche dopo un riavvio del nodo")
        self.assertEqual(self.url_salvato(node_id), prima, "l'indirizzo non si muove: niente da rifare")
        nomi = {str(n["id"]): str(n["name"]) for n in self.cfg.get("actions.nodes")}
        self.assertEqual(nomi[node_id], "Balcone", "e il nome che si legge in Mirto")

    def test_what_had_only_the_node_name_follows_it(self):
        """Trovato il 21/10 rinominando «Studio» in «Ingresso»: la camera restava «Studio».

        La camera di un nodo nasce col nome del nodo, e le azioni proposte nascono «Sirena … – Studio».
        Se il nodo cambia nome, quei nomi non sono più di nessuno: devono seguirlo. Ma solo quelli —
        un nome scelto da una persona resta suo.
        """
        from mirto.actions.nodes import rinomina_nodo

        node_id = pairing.pair_found(self.cfg, self.secrets, self.trovato(), "Studio",
                                     "Mirto di prova", self.pin)
        self.cfg.set("cameras.network", [
            {"id": "studio", "name": "Studio", "node": node_id, "node_cam": "cam", "url": "rtsp://x/cam"},
            {"id": "studio-2", "name": "Studio – retro", "node": node_id, "node_cam": "cam2",
             "url": "rtsp://x/cam2"},
            {"id": "corridoio", "name": "Corridoio", "node": node_id, "node_cam": "cam3",
             "url": "rtsp://x/cam3"},
            {"id": "altra", "name": "Studio", "node": "altro-nodo", "url": "rtsp://y/cam"}])
        self.cfg.set("actions.items", [
            {"name": "Sirena dalla cassa (max 3 minuti) – Studio", "node": node_id, "action": "sirena"},
            {"name": "La mia sirena", "node": node_id, "action": "sirena2"},
            {"name": "Luci – Studio", "node": "altro-nodo", "action": "luci"}])
        client = NodeClient(NodeConfig(node_id, "Studio", self.url), self.cert.read_text(), self.key)
        esito = rinomina_nodo(self.cfg, node_id, "Ingresso", client)

        nomi = {c["id"]: c["name"] for c in self.cfg.get("cameras.network")}
        self.assertEqual(nomi["studio"], "Ingresso", "la camera senza nome suo segue il nodo")
        self.assertEqual(nomi["studio-2"], "Ingresso – retro", "anche quella col suffisso")
        self.assertEqual(nomi["corridoio"], "Corridoio", "un nome scelto da una persona non si tocca")
        self.assertEqual(nomi["altra"], "Studio", "e le camere di un altro nodo nemmeno")
        self.assertEqual({c["id"] for c in self.cfg.get("cameras.network")},
                         {"studio", "studio-2", "corridoio", "altra"},
                         "gli identificativi non cambiano: ci sono appesi portachiavi ed eventi")
        azioni = [a["name"] for a in self.cfg.get("actions.items")]
        self.assertIn("Sirena dalla cassa (max 3 minuti) – Ingresso", azioni)
        self.assertIn("La mia sirena", azioni, "un nome scelto resta")
        self.assertIn("Luci – Studio", azioni, "le azioni di un altro nodo non si toccano")
        self.assertEqual(len(esito["camere"]), 2)
        self.assertEqual(len(esito["azioni"]), 1)

    def test_a_node_renamed_to_the_same_name_changes_nothing(self):
        from mirto.actions.nodes import segui_il_nome

        self.cfg.set("cameras.network", [{"id": "c", "name": "Studio", "node": "n1"}])
        self.assertEqual(segui_il_nome(self.cfg, "n1", "Studio", "Studio"), {"camere": [], "azioni": []})
        self.assertEqual(segui_il_nome(self.cfg, "n1", "", "Ingresso"), {"camere": [], "azioni": []})
        self.assertEqual(self.cfg.get("cameras.network")[0]["name"], "Studio")

    def test_a_node_that_refuses_the_new_name_changes_nothing_here(self):
        from mirto.actions.nodes import rinomina_nodo

        node_id = pairing.pair_found(self.cfg, self.secrets, self.trovato(), "Balcone",
                                     "Mirto di prova", self.pin)
        client = NodeClient(NodeConfig(node_id, "Balcone", self.url), self.cert.read_text(), self.key)
        prima = self.url_salvato(node_id)
        with self.assertRaises(NodeError):
            rinomina_nodo(self.cfg, node_id, "???", client)      # senza lettere il nodo lo rifiuta
        nomi = {str(n["id"]): str(n["name"]) for n in self.cfg.get("actions.nodes")}
        self.assertEqual(nomi[node_id], "Balcone", "il nome di prima resta quello buono")
        self.assertEqual(self.url_salvato(node_id), prima)

    def test_the_address_follows_the_network_name_only_when_it_changes(self):
        from mirto.actions.nodes import url_col_host

        self.assertEqual(url_col_host("https://mirto-node-17a9.local:8443", "mirto-garage"),
                         "https://mirto-garage.local:8443")
        self.assertEqual(url_col_host("https://mirto-garage.local:8443", "mirto-garage"), "",
                         "stesso nome: non si tocca niente")
        self.assertEqual(url_col_host("https://192.168.2.3:8443", "mirto-garage"),
                         "https://mirto-garage.local:8443", "da indirizzo a nome: il nome è più stabile")
        self.assertEqual(url_col_host("https://mirto-garage.local:8443", ""), "",
                         "un nodo vecchio non risponde col nome in rete: si lascia l'indirizzo")

    def test_a_factory_name_is_not_proposed_as_a_name(self):
        for sigla in ("Node 17a9", "node-17a9", "Nodo", "Node", "nodo 3f2a", "mirto-node-17a9"):
            self.assertTrue(pairing.nome_di_fabbrica(sigla), sigla)
        for nome in ("Garage", "Ingresso", "Nodo del garage", "Cucina", "Giardino 2"):
            self.assertFalse(pairing.nome_di_fabbrica(nome), nome)


class MessaggioNodiTrovatiTest(unittest.TestCase):
    """Il messaggio dopo «Cerca nodi in rete» lo legge una persona in un momento di dubbio.

    Prima era una riga sola con dentro tutto: nome, indirizzo, spiegazione e comando, separati da
    parentesi e due punti. Chi lo leggeva non capiva se il nodo c'era o no, né cosa doveva fare.
    """

    def nodo(self, **kw) -> dict:
        base = {"nome": "Mirto Node mirto-ingresso", "url": "https://192.168.2.2:8443", "gia": False}
        base.update(kw)
        return base

    def test_it_says_how_many_and_what_to_do(self):
        from mirto.gui.testi import nodi_trovati

        testo = nodi_trovati([self.nodo()])
        righe = testo.splitlines()
        self.assertIn("un nodo", righe[0], "la prima riga dice quanti ne ha trovati")
        self.assertIn("mirto-ingresso", testo)
        self.assertIn("https://192.168.2.2:8443", testo)
        comando = [r for r in righe if "mirto-nodo abbina" in r]
        self.assertEqual(len(comando), 1)
        self.assertEqual(comando[0].strip(), "mirto-nodo abbina",
                         "il comando sta su una riga sua: si deve poter copiare senza ripulirlo")

    def test_a_frozen_picture_says_when_it_was_taken(self):
        """Trovato provando Q-02 il 19/10: a camera scollegata l'ultima immagine resta nel riquadro.

        Per un programma di sorveglianza è il difetto peggiore: guardi il corridoio vuoto e stai
        guardando com'era dieci minuti fa. L'immagine vecchia è utile — dice cos'era inquadrato —
        ma deve dire di quando è, altrimenti mente.
        """
        from mirto.gui.testi import immagine_ferma

        adesso = 1792400000.0            # un'ora qualunque, a mezzogiorno di qualche giorno
        testo = immagine_ferma(adesso - 5, adesso)
        self.assertIn("ferma", testo.lower(), f"deve dire che non è in diretta: {testo!r}")
        self.assertNotIn("fa", testo.split("·")[-1], "cinque secondi non si raccontano come un'attesa")

        due_minuti = immagine_ferma(adesso - 125, adesso)
        self.assertIn("2 min", due_minuti, due_minuti)
        tre_ore = immagine_ferma(adesso - 3 * 3600 - 60, adesso)
        self.assertIn("3 h", tre_ore, tre_ore)
        un_giorno = immagine_ferma(adesso - 26 * 3600, adesso)
        self.assertIn("1 g", un_giorno, un_giorno)

    def test_without_a_time_it_does_not_invent_one(self):
        from mirto.gui.testi import immagine_ferma

        testo = immagine_ferma(0.0, 1792400000.0)
        self.assertIn("ferma", testo.lower())
        for cifra in ("0:00", "1970", "min fa"):
            self.assertNotIn(cifra, testo, f"senza l'ora non si inventa niente: {testo!r}")

    def test_the_tile_marks_a_picture_that_is_not_live(self):
        """Guardia: il riquadro deve sapere di quando è l'ultimo fotogramma, non solo mostrarlo."""
        sorgente = (Path(__file__).resolve().parents[1] / "mirto" / "gui" / "widgets.py").read_text(encoding="utf-8")
        corpo = sorgente[sorgente.index("class VideoView"):sorgente.index("class CameraTile")]
        self.assertIn("immagine_ferma", corpo, "l'avviso si scrive sull'immagine, non solo nella riga in basso")
        self.assertIn("last_frame_ts", corpo)
        app = (Path(__file__).resolve().parents[1] / "mirto" / "gui" / "app.py").read_text(encoding="utf-8")
        self.assertIn("tile.show_frame(frame, ts)", app,
                      "l'ora del fotogramma arrivava fino al riquadro e veniva buttata via")
        # La scheda di «Tutte le cam» resta com'è di proposito: lì lo stato («In uso · non
        # raggiungibile») è scritto a parole accanto alla miniatura, quindi l'immagine vecchia non
        # finge di essere in diretta. Il riquadro della sorveglianza no: è solo video.

    def test_a_test_that_can_be_heard_is_asked_first(self):
        from mirto.gui.testi import prova_si_sente

        for rumorosa in ("siren", "deterrent"):
            self.assertTrue(prova_si_sente(rumorosa), "cinque secondi di sirena non si danno di nascosto")
        for silenziosa in ("light", "generic", "audio", ""):
            self.assertFalse(prova_si_sente(silenziosa), "una luce si prova e basta: chiedere infastidisce")

    def test_more_than_one_node_is_counted(self):
        from mirto.gui.testi import nodi_trovati

        testo = nodi_trovati([self.nodo(), self.nodo(nome="Mirto Node garage")])
        self.assertIn("2", testo.splitlines()[0])
        self.assertIn("garage", testo)

    def test_an_already_paired_node_asks_for_nothing(self):
        from mirto.gui.testi import nodi_trovati

        testo = nodi_trovati([self.nodo(gia=True)])
        self.assertIn("abbinato", testo)
        self.assertNotIn("mirto-nodo abbina", testo,
                         "non si chiede di lanciare un comando a chi non ha niente da fare")


class WatcherTest(unittest.TestCase):
    class Cfg(dict):
        def get(self, k, d=None):
            return super().get(k, d)

    def test_check_filters_paired_ignored_and_closed(self):
        nodes = [{"nome": "a", "fp": "AA:01", "contact": "https://1:8443", "url": "https://a.local:8443"},
                 {"nome": "b", "fp": "BB:02", "contact": "https://2:8443", "url": "https://b.local:8443"},
                 {"nome": "c", "fp": "CC:03", "contact": "https://3:8443", "url": "https://c.local:8443"},
                 {"nome": "d", "fp": "DD:04", "contact": "https://4:8443", "url": "https://d.local:8443"}]
        states = {"https://1:8443": {"aperto": True, "scade_tra_s": 600, "nome": "Ingresso", "impronta": "AA:01"},
                  "https://2:8443": {"aperto": True, "scade_tra_s": 600, "nome": "B", "impronta": "BB:02"},
                  "https://3:8443": {"aperto": False, "scade_tra_s": 0, "nome": "C", "impronta": "CC:03"},
                  "https://4:8443": {"aperto": True, "scade_tra_s": 600, "nome": "D", "impronta": "EE:99"}}
        asked = []

        def state(url):
            asked.append(url)
            return states[url]

        browser = type("B", (), {"nodes": lambda self: nodes})()
        clock = Clock()
        w = pairing.PairingWatcher(self.Cfg(), browser=browser, state=state,
                                   paired=lambda cfg: {"BB02"}, clock=clock)
        out = w.check()
        self.assertEqual([p["nome"] for p in out], ["Ingresso"])   # b abbinato, c chiuso, d impronta diversa
        self.assertNotIn("https://2:8443", asked)                  # ai nodi abbinati non si chiede nulla
        self.assertEqual(w.current()[0]["scade_tra_s"], 600)
        clock.t += 601
        self.assertEqual(w.current(), [])
        clock.t -= 601
        w.ignore("AA:01")
        self.assertEqual(w.current(), [])
        self.assertEqual(w.check(), [])

    def test_an_announcement_without_a_fingerprint_is_not_proposed(self):
        """S-06: chiunque può annunciare un «_mirto-node._tcp» sulla rete.

        Se l'annuncio non dichiara l'impronta del certificato non c'è niente da confrontare, e l'utente
        confermerebbe un nodo che non è quello che ha in mano — per poi mandargli, col wizard, la
        password del Wi-Fi di casa.
        """
        nodes = [{"nome": "senza", "fp": "", "contact": "https://9:8443", "url": "https://x.local:8443"}]
        chiesto = []

        def state(url):
            chiesto.append(url)
            return {"aperto": True, "scade_tra_s": 300, "nome": "Impostore", "impronta": "AA:01"}

        browser = type("B", (), {"nodes": lambda self: nodes})()
        w = pairing.PairingWatcher(self.Cfg(), browser=browser, state=state,
                                   paired=lambda cfg: set(), clock=Clock())
        self.assertEqual(w.check(), [], "un annuncio senza impronta non si propone")
        self.assertEqual(chiesto, [], "e non gli si chiede nemmeno niente")

    def test_a_never_paired_node_stays_in_the_list(self):
        """Il PIN di un nodo nuovo è stampato e viaggia con lui: l'offerta di abbinarlo non deve sparire.

        Prima il nodo spariva dall'elenco dopo i cinque minuti nominali e tornava solo al giro di
        ricerca dopo: chi aveva il foglio in mano vedeva la proposta andare e venire.
        """
        nodes = [{"nome": "nuovo", "fp": "AA:01", "contact": "https://1:8443", "url": "https://n.local:8443"}]
        state = {"aperto": True, "scade_tra_s": 300, "senza_scadenza": True, "nome": "Nuovo", "impronta": "AA:01"}
        browser = type("B", (), {"nodes": lambda self: nodes})()
        clock = Clock()
        w = pairing.PairingWatcher(self.Cfg(), browser=browser, state=lambda url: state,
                                   paired=lambda cfg: set(), clock=clock)
        w.check()
        self.assertEqual(w.current()[0]["scade_tra_s"], 0, "niente conto alla rovescia: non scade")
        clock.t += 7 * 24 * 3600
        self.assertEqual([p["nome"] for p in w.current()], ["Nuovo"])

    def test_unique_node_id(self):
        cfg = self.Cfg({"actions.nodes": [{"id": "ingresso"}, {"id": "ingresso2"}]})
        self.assertEqual(pairing.unique_node_id(cfg, "Ingresso"), "ingresso3")
        self.assertEqual(pairing.unique_node_id(cfg, "Garage"), "garage")


if __name__ == "__main__":
    unittest.main()


class ForeignPairingTest(unittest.TestCase):
    """Il nodo dice da chi è abbinato: se non è questo Mirto, l'utente riceve un avviso (una volta sola)."""

    def setUp(self):
        import types

        from mirto.actions.engine import ActionEngine
        from tests.test_actions import FakeNotifier

        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        os.environ["MIRTO_CONFIG_DIR"] = self.tmp.name
        os.environ["MIRTO_DATA_DIR"] = self.tmp.name
        self.cfg = Config(Path(self.tmp.name) / "config.yaml")
        self.cfg.set("actions.nodes", [{"id": "ingresso", "name": "Ingresso", "url": "https://nodo:8443"}])
        self.notifier = FakeNotifier()
        core = types.SimpleNamespace(config=self.cfg, secrets=mem_secrets(), notifier=self.notifier)
        self.engine = ActionEngine(core)

    def tearDown(self):
        self.engine.stop()
        self.tmp.cleanup()

    def seen(self, who: str) -> None:
        self.engine._check_node_pairing("ingresso", "Ingresso", {"abbinato": True, "da": who, "quando": 1760000000})

    def test_another_mirto_is_reported_once(self):
        self.seen("Mirto su portatile-sconosciuto · 192.168.1.9")
        self.assertEqual(len(self.notifier.texts), 1)
        self.assertIn("Ingresso", self.notifier.texts[0])
        self.assertIn("portatile-sconosciuto", self.notifier.texts[0])
        self.assertIn("revoca-tutti", self.notifier.texts[0])
        self.seen("Mirto su portatile-sconosciuto · 192.168.1.9")
        self.assertEqual(len(self.notifier.texts), 1, "lo stesso abbinamento non si segnala due volte")
        self.assertEqual(self.cfg.get("actions.nodes")[0]["abbinato_da"],
                         "Mirto su portatile-sconosciuto · 192.168.1.9")

    def test_nothing_to_say_about_ourselves_or_manual_keys(self):
        self.seen(f"{pairing.client_name()} · 192.168.1.5")
        self.seen("chiave · 192.168.1.5")   # abbinamento con la chiave copiata a mano
        self.seen("")
        self.assertEqual(self.notifier.texts, [])

    def test_install_key_left_on_the_node_is_reported_once(self):
        state = {"abbinato": True, "da": "", "chiavi": 1, "chiave_installazione": True,
                 "ritiro_installazione_tra_s": 5 * 86400}
        self.engine._check_node_pairing("ingresso", "Ingresso", state)
        self.assertEqual(len(self.notifier.texts), 1)
        self.assertIn("chiave di installazione", self.notifier.texts[0])
        self.assertIn("revoca installazione", self.notifier.texts[0])
        self.assertIn("5 giorni", self.notifier.texts[0])
        self.engine._check_node_pairing("ingresso", "Ingresso", state)
        self.assertEqual(len(self.notifier.texts), 1, "l'avviso non si ripete a ogni controllo")

    def test_no_install_warning_before_the_first_key_of_its_own(self):
        self.engine._check_node_pairing("ingresso", "Ingresso",
                                        {"abbinato": True, "chiavi": 0, "chiave_installazione": True})
        self.assertEqual(self.notifier.texts, [])


class AnnuncioDelNodoTest(unittest.TestCase):
    """Un nodo che non compare deve almeno dire perché.

    Durante W-01 (19/10) si è visto che su Windows `mirto-ingresso.local` si risolve su un IPv6
    link-local, e che la scoperta scartava in silenzio gli annunci senza IPv4. Il nodo in quel caso
    semplicemente non esisteva, per chi guardava l'elenco.
    """

    class Annuncio:
        port = 8443
        server = "mirto-ingresso.local."
        properties = {b"fp": b"AA:BB:CC"}

        def __init__(self, indirizzi):
            self.indirizzi = indirizzi

        def parsed_addresses(self):
            return self.indirizzi

    NOME = "Ingresso._mirto-node._tcp.local."

    def test_without_ipv4_the_node_is_skipped_but_the_log_says_why(self):
        from mirto.actions import discovery

        with self.assertLogs("mirto.actions.discovery", "WARNING") as registro:
            nodo = discovery.nodo_da_annuncio(self.NOME, self.Annuncio(["fe80::2ac5:9dd8:b985:c31c"]))
        self.assertIsNone(nodo)
        riga = "\n".join(registro.output)
        self.assertIn("Ingresso", riga)
        self.assertIn("IPv4", riga)
        self.assertIn("fe80::", riga, "dire quale indirizzo si è visto fa risparmiare la domanda dopo")

    def test_with_ipv4_it_builds_both_addresses(self):
        from mirto.actions import discovery

        nodo = discovery.nodo_da_annuncio(self.NOME, self.Annuncio(["192.168.1.23", "fe80::1"]))
        self.assertEqual(nodo["name"], "Ingresso")
        self.assertEqual(nodo["url"], "https://192.168.1.23:8443")
        self.assertEqual(nodo["host_url"], "https://mirto-ingresso.local:8443")
        self.assertEqual(nodo["fingerprint"], "AA:BB:CC")

