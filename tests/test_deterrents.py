# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Dissuasori per uccelli e animali, specie degli uccelli, suoni del nodo."""
import array
import io
import math
import os
import sys
import tempfile
import types
import unittest
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "node"))

from mirto_node import audio as node_audio  # noqa: E402
from mirto_node.server import ActionError, DeterrentDevice, Node, SirenDevice  # noqa: E402

from mirto.actions.engine import ActionEngine, ActionSpec  # noqa: E402
from mirto.actions.signing import generate_key  # noqa: E402
from mirto.cameras.discovery import CameraInfo  # noqa: E402
from mirto.config import Config  # noqa: E402
from mirto.detection.labels import INDEX  # noqa: E402
from mirto.detection.person import Detection  # noqa: E402
from mirto.detection.pipeline import CameraPipeline, deterrent_indices, needs_species  # noqa: E402
from mirto.detection.species import SpeciesClassifier, genus_group, parse_labels  # noqa: E402
from mirto.events import Event  # noqa: E402
from tests.test_actions import mem_secrets  # noqa: E402


def ev(label="bird", group="", score=0.8, cam="cam1"):
    e = Event(cam, "Orto", score, np.zeros((10, 10, 3), np.uint8), label=label)
    e.species_group = group
    return e


DETERRENT = {"name": "Via i corvi", "node": "orto", "action": "dissuasore", "kind": "deterrent",
             "targets": ["bird:corvid"], "sounds": ["rapace", "sparo", "falco"], "cooldown": 120,
             "max_per_day": 3, "duration": 8}


class SpecTest(unittest.TestCase):
    def test_a_deterrent_that_also_wants_people_is_simply_not_a_deterrent(self):
        """Il 23/10: un'azione che scatta anche per le persone non e\' un dissuasore, e non e\' un errore.

        Prima qui si rifiutava l'azione intera con un ValueError — e un'azione rifiutata non scatta.
        E\' il motivo per cui il suono non partiva quando Marco ha messo «persona e cane» su un
        dissuasore: non era il nodo, non era il suono, era questa riga.
        """
        mista = ActionSpec.from_dict({**DETERRENT, "targets": ["bird:corvid", "person"]})
        self.assertNotEqual(mista.kind, "deterrent")
        self.assertEqual(sorted(mista.targets), ["bird:corvid", "person"], "nessuna scelta buttata via")

    def test_deterrent_needs_targets(self):
        with self.assertRaises(ValueError):
            ActionSpec.from_dict({**DETERRENT, "targets": []})
        spec = ActionSpec.from_dict({**DETERRENT, "mode": "confirm", "cooldown": 5, "duration": 999})
        self.assertEqual(spec.mode, "auto", "un dissuasore non aspetta conferme")
        self.assertGreaterEqual(spec.cooldown, 60, "pausa minima tra due scatti")
        self.assertEqual(spec.safe_duration(), 30, "al massimo 30 secondi per volta")

    def test_targets(self):
        spec = ActionSpec.from_dict(DETERRENT)
        self.assertTrue(spec.matches(ev("bird", "corvid")))
        self.assertFalse(spec.matches(ev("bird", "pigeon")))
        self.assertFalse(spec.matches(ev("bird", "")), "uccello senza gruppo: non è un corvide")
        self.assertFalse(spec.matches(ev("person")))
        anybird = ActionSpec.from_dict({**DETERRENT, "targets": ["bird"]})
        self.assertTrue(anybird.matches(ev("bird", "pigeon")))
        self.assertTrue(anybird.matches(ev("bird", "")))

    def test_an_action_keeps_every_subject_it_was_given(self):
        """Fino al 23/10 gli animali venivano tolti in silenzio da un'azione antintrusione.

        Aveva senso finché il tipo si sceglieva a mano e diceva «che specie di azione è questa».
        Adesso si sceglie cosa deve riconoscere la camera, e buttare via una scelta senza dirlo
        sarebbe il modo peggiore di non permetterla.
        """
        siren = ActionSpec.from_dict({"name": "S", "node": "n", "action": "sirena", "kind": "siren",
                                      "targets": ["person", "dog", "sound:glass"]})
        self.assertEqual(siren.targets, ["person", "dog", "sound:glass"])
        self.assertTrue(siren.matches(ev("person")))
        self.assertTrue(siren.matches(ev("sound:glass")))
        self.assertTrue(siren.matches(ev("dog")), "se l'hai chiesto, scatta")
        old = ActionSpec.from_dict({"name": "L", "node": "n", "action": "luci", "kind": "light"})
        self.assertEqual(old.targets, ["person"], "azioni già salvate: solo persone come prima")

    def test_hours(self):
        import time
        spec = ActionSpec.from_dict({**DETERRENT, "hours": "06:00-20:00"})
        noon = time.mktime((2026, 10, 17, 12, 0, 0, 0, 0, -1))
        night = time.mktime((2026, 10, 17, 23, 0, 0, 0, 0, -1))
        self.assertTrue(spec.in_hours(noon))
        self.assertFalse(spec.in_hours(night))
        wrap = ActionSpec.from_dict({**DETERRENT, "hours": "22:00-06:00"})
        self.assertTrue(wrap.in_hours(night))
        with self.assertRaises(ValueError):
            ActionSpec.from_dict({**DETERRENT, "hours": "25:00-26:00"})


class FakeClient:
    def __init__(self):
        self.calls = []

    def activate(self, action, duration, event_id="", camera="", extra=None):
        self.calls.append((action, duration, (extra or {}).get("suono", "")))
        return {"esito": "ok"}

    def stop_all(self):
        return {"esito": "ok"}

    def prank(self, minuti):
        self.calls.append(("prank", minuti, ""))
        return {"esito": "ok", "prank": {"attiva": bool(minuti), "restano_s": int(minuti * 60)}}

    def volume(self, percento):
        self.calls.append(("volume", percento, ""))
        return {"esito": "ok", "volume": {"si_puo": True, "percento": int(percento)}}


class FakeNotifier:
    def __init__(self):
        self.texts = []

    def notify_text(self, text):
        self.texts.append(text)


class EngineDeterrentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(Path(self.tmp.name) / "c.yaml")
        self.cfg.set("actions.items", [DETERRENT])
        self.notifier = FakeNotifier()
        self.client = FakeClient()
        core = types.SimpleNamespace(config=self.cfg, secrets=mem_secrets(), notifier=self.notifier)
        self.now = [1_800_000_000.0]
        self.engine = ActionEngine(core, client_factory=lambda nd, s: self.client, clock=lambda: self.now[0])
        self.engine.clients = {"orto": self.client}
        self.engine._pool = types.SimpleNamespace(submit=lambda fn, *a: fn(*a), shutdown=lambda **k: None)

    def tearDown(self):
        self.engine.stop()
        self.tmp.cleanup()

    def test_fires_without_protection_with_cooldown_and_daily_limit(self):
        self.assertFalse(self.engine.armed)
        self.assertEqual(self.engine.on_detection(ev("bird", "corvid")), ["Via i corvi"])
        self.assertEqual(self.engine.on_detection(ev("bird", "corvid")), [], "pausa tra due scatti")
        sounds = [self.client.calls[0][2]]
        for _ in range(4):
            self.now[0] += 121
            self.engine.on_detection(ev("bird", "corvid"))
            if len(self.client.calls) > len(sounds):
                sounds.append(self.client.calls[-1][2])
        self.assertEqual(len(self.client.calls), 3, "massimo 3 scatti al giorno")
        self.assertTrue(all(a != b for a, b in zip(sounds, sounds[1:])), "mai lo stesso suono due volte di fila")
        self.assertTrue(all(s in DETERRENT["sounds"] for s in sounds))
        self.assertTrue(any("riprende domani" in t for t in self.notifier.texts))
        self.now[0] += 86400
        self.assertEqual(self.engine.on_detection(ev("bird", "corvid")), ["Via i corvi"], "nuovo giorno")

    def test_a_refusal_says_why(self):
        """Il nodo rifiuta in italiano chiaro: quella frase deve arrivare a chi legge l'avviso.

        Marco, 25/10, con il dissuasore a budget esaurito: su Telegram leggeva «Azione Halloween non
        eseguita» e basta, mentre nel registro c'era *«limite del dissuasore raggiunto: al massimo 2
        minuti ogni ora»*. Il motivo c'era e moriva lì — e un avviso che dice che qualcosa non è
        andato senza dire perché è lo stesso difetto di un numero che non porta da nessuna parte.
        """
        def rifiuta(*_a, **_k):
            raise RuntimeError("Nodo orto: 409 limite del dissuasore raggiunto: al massimo 2 minuti ogni ora")

        self.client.activate = rifiuta
        self.engine.on_detection(ev("bird", "corvid"))
        avvisi = [t for t in self.notifier.texts if "non eseguita" in t]
        self.assertTrue(avvisi, "l'avviso parte lo stesso")
        self.assertIn("limite del dissuasore", avvisi[-1], "e dice perché")
        self.assertNotIn("409", avvisi[-1], "senza il numero del protocollo, che è roba nostra")

    def test_a_refusal_for_budget_is_said_in_our_own_words(self):
        """Dall'API 1.13 il nodo manda anche `motivo` e i numeri: la frase la scriviamo noi.

        Il nodo parla italiano sempre — e' la sua lingua, come il registro — quindi finche' l'unica
        cosa che aveva da dare era una frase, quella frase finiva dentro un avviso tradotto: un
        utente inglese leggeva *«Action not run: i dissuasori hanno gia' suonato…»*. Con il motivo
        leggibile accanto, lo stesso rifiuto si dice in qualunque lingua senza che il nodo ne sappia
        nessuna, e l'avviso dice **quando riprende**, che e' l'unica cosa che chi legge puo' usare.
        """
        from mirto.actions.nodes import NodeError

        def rifiuta(*_a, **_k):
            raise NodeError("Nodo orto: 409 i dissuasori hanno gia' suonato i suoi 2 minuti di "
                            "questi 60: riprende fra 12 minuti",
                            {"motivo": "budget_dissuasore", "riprende_fra_s": 743,
                             "budget_s": 120, "finestra_s": 3600})

        self.client.activate = rifiuta
        self.engine.on_detection(ev("bird", "corvid"))
        avvisi = [t for t in self.notifier.texts if "non eseguita" in t]
        self.assertTrue(avvisi, "l'avviso parte lo stesso")
        ultimo = avvisi[-1]
        self.assertIn("2 minuti", ultimo, "quanto puo' suonare")
        self.assertIn("12 minuti", ultimo, "e quando riprende")
        self.assertNotIn("409", ultimo)
        self.assertNotIn("questi 60", ultimo, "la frase e' nostra, non quella del nodo inoltrata")

    def test_a_reason_we_do_not_know_is_passed_on_as_it_came(self):
        """Un motivo sconosciuto e' un nodo piu' nuovo di questo programma, non un errore: la sua
        frase arriva comunque, che e' meglio di «non eseguita» e basta."""
        from mirto.actions.nodes import NodeError

        def rifiuta(*_a, **_k):
            raise NodeError("Nodo orto: 409 la cassa si e' staccata", {"motivo": "cosa_nuova"})

        self.client.activate = rifiuta
        self.engine.on_detection(ev("bird", "corvid"))
        avvisi = [t for t in self.notifier.texts if "non eseguita" in t]
        self.assertIn("la cassa si e' staccata", avvisi[-1])

    def test_a_refusal_without_a_reason_does_not_invent_one(self):
        def rifiuta(*_a, **_k):
            raise RuntimeError("")

        self.client.activate = rifiuta
        self.engine.on_detection(ev("bird", "corvid"))
        avvisi = [t for t in self.notifier.texts if "non eseguita" in t]
        self.assertTrue(avvisi)
        self.assertTrue(avvisi[-1].rstrip().endswith("non eseguita"), "niente due punti e il vuoto")

    def test_prank_is_asked_of_the_node_and_said_in_words(self):
        """La modalita' vive nel nodo, come il budget che sospende: da qui si chiede e basta."""
        msg = self.engine.prank("orto", 30)
        self.assertIn(("prank", 30, ""), self.client.calls)
        self.assertIn("30 minuti", msg)
        self.assertIn("30 secondi per scatto", msg, "si dice cosa resta, non solo cosa cade")
        self.assertIn("spenta", self.engine.prank("orto", 0).lower())

    def test_the_new_volume_is_shown_at_once_not_at_the_next_poll(self):
        """Fra due letture dello stato periodico passano secondi: se si aspettasse quella, i
        quadratini tornerebbero indietro sotto il dito di chi li ha cliccati."""
        self.engine.node_info["orto"] = {"volume": {"si_puo": True, "percento": 60}}
        msg = self.engine.volume("orto", 30)
        self.assertIn(("volume", 30, ""), self.client.calls)
        self.assertIn("30", msg)
        self.assertEqual(self.engine.node_info["orto"]["volume"]["percento"], 30)

    def test_the_audio_url_is_the_camera_url_with_the_last_piece_swapped(self):
        """Utente, password, porta e l'indirizzo scelto fra quelli del nodo li ha gia' decisi
        `node_camera_url`: rifarne il conto qui vorrebbe dire due risposte alla stessa domanda."""
        self.cfg.set("cameras.network", [{"id": "c1", "node": "orto", "url": "rtsp://u:p@orto.local:8554/cam"}])
        self.cfg.set("actions.nodes", [{"id": "orto", "name": "Orto", "url": "https://orto.local:8443"}])
        self.engine.node_info["orto"] = {"indirizzi": [], "audio_registrazione": {
            "acceso": True, "attivo": True, "percorso": "cam-audio"}}
        self.assertTrue(self.engine.node_audio_url("net:c1").endswith("/cam-audio"))
        base = self.engine.node_camera_url("net:c1")
        self.assertEqual(self.engine.node_audio_url("net:c1"), base.rsplit("/", 1)[0] + "/cam-audio")

    def test_no_audio_url_when_the_node_is_not_publishing_it(self):
        """E' spento di serie, si accende per nodo, e anche acceso puo' non essere in piedi adesso."""
        self.cfg.set("cameras.network", [{"id": "c1", "node": "orto", "url": "rtsp://u:p@orto.local:8554/cam"}])
        self.cfg.set("actions.nodes", [{"id": "orto", "name": "Orto", "url": "https://orto.local:8443"}])
        for stato in ({}, {"acceso": True, "attivo": False, "percorso": "cam-audio"},
                      {"acceso": True, "attivo": True, "percorso": ""}):
            self.engine.node_info["orto"] = {"audio_registrazione": stato}
            self.assertEqual(self.engine.node_audio_url("net:c1"), "", str(stato))
        self.assertEqual(self.engine.node_audio_url("net:sconosciuta"), "")

    def test_prank_on_an_unreachable_node_says_so(self):
        self.assertIn("non raggiungibile", self.engine.prank("cucina", 30))

    def test_every_alert_says_the_mode_is_on(self):
        """*«si vede ovunque»*: una modalita' che cambia le regole di sicurezza non puo' essere
        invisibile proprio nei messaggi che raccontano cosa ha fatto la casa."""
        self.engine.on_detection(ev("bird", "corvid"))
        self.assertTrue(self.notifier.texts)
        self.assertNotIn("Prank", self.notifier.texts[-1], "spenta non si nomina")
        self.engine.node_info["orto"] = {"prank": {"attiva": True, "restano_s": 600}}
        self.now[0] += 1000          # passata la pausa fra due scatti
        self.engine.on_detection(ev("bird", "corvid"))
        self.assertIn("Prank", self.notifier.texts[-1])

    def test_person_and_other_birds_ignored(self):
        self.assertEqual(self.engine.on_detection(ev("person")), [])
        self.assertEqual(self.engine.on_detection(ev("bird", "pigeon")), [])
        self.assertEqual(self.client.calls, [])

    def test_intrusion_never_starts_deterrent(self):
        self.engine.set_armed(True)
        self.engine.on_intrusion(ev("person"))
        self.assertEqual(self.client.calls, [])

    def test_sound_from_node_when_none_chosen(self):
        self.cfg.set("actions.items", [{**DETERRENT, "sounds": []}])
        self.engine.reload()
        self.engine.clients = {"orto": self.client}
        self.engine.node_meta["orto"] = {"dispositivi": [{"nome": "dissuasore", "tipo": "deterrent",
                                                         "suoni": ["cannoncino"]}]}
        self.engine.on_detection(ev("bird", "corvid"))
        self.assertEqual(self.client.calls[0][2], "cannoncino")


class FakePlayer(node_audio.AudioPlayer):
    def __init__(self, output="predefinito", card="", bt_mac=""):
        super().__init__(output=output, card=card, bt_mac=bt_mac)
        self.loops, self.stops, self.ripetizioni = [], [], []

    def _crea_sorella(self, uscita, scheda, mac):
        """Una cassa in piu' e' un player in piu' (tappa 4): qui finto, se no cerca una scheda ALSA."""
        return FakePlayer(output=uscita, card=scheda, bt_mac=mac)

    def ready(self, chiave=""):
        return True

    def loop(self, pcm, seconds, owner="", uscita=""):
        self.owner = owner
        self.loops.append((owner, len(pcm), seconds, uscita))

    def ripeti(self, pcm, volte, owner="", uscita=""):
        self.owner = owner
        self.ripetizioni.append((owner, len(pcm), volte, uscita))
        return len(pcm) / (2 * 8000)

    def stop(self, owner=None):
        self.stops.append(owner)


class NodeDeterrentTest(unittest.TestCase):
    def setUp(self):
        self.player = FakePlayer()
        self.det = DeterrentDevice(self.player, {"rapace": b"\0\0" * 100, "sparo": b"\0\0" * 50})
        self.now = [1000.0]
        self.node = Node("Orto", generate_key(), {"sirena": SirenDevice(self.player, 80), "dissuasore": self.det},
                         self.player, clock=lambda: self.now[0], cameras=lambda: [], addresses=lambda: [],
                         hostname=lambda: "orto.local", model=lambda: "Raspberry Pi 4", credentials=lambda: None)

    def tearDown(self):
        self.node.stop_all()

    def test_the_request_can_say_which_speaker(self):
        """API 1.10: «suona da questa cassa», e la cassa viaggia fino al giocatore.

        Dalla tappa 4 il giocatore e' quello **di quella cassa**: una cassa, una linea. La chiave
        gli arriva comunque, e non per abitudine — e' cosi' che resta in piedi la regola del 1.10,
        che un'uscita chiesta non ripiega da nessun'altra parte.
        """
        self.node.activate("dissuasore", {"durata": 5, "suono": "sparo", "volte": 1,
                                     "uscita": "usb:UACDemoV10"})
        self.assertEqual(self.player.ripetizioni, [], "la cassa configurata non c'entra niente")
        usb = self.player.per("usb:UACDemoV10")
        self.assertEqual(usb.ripetizioni[-1][3], "usb:UACDemoV10")
        self.assertEqual(usb.card, "UACDemoV10")

    def test_without_an_output_nothing_changes(self):
        """Una richiesta senza «uscita» e' esattamente quella di prima: le azioni gia' salvate
        continuano a suonare da dove il nodo e' configurato."""
        self.node.activate("dissuasore", {"durata": 5, "suono": "sparo", "volte": 1})
        self.assertEqual(self.player.ripetizioni[-1][3], "")

    def test_you_cannot_ask_a_light_which_speaker_to_use(self):
        """Una domanda senza senso si rifiuta subito, invece di essere ignorata in silenzio."""
        from mirto_node.server import ActionError, LedDevice

        self.node.devices["luci"] = LedDevice(base=None)
        with self.assertRaises(ActionError) as caso:
            self.node.activate("luci", {"durata": 5, "uscita": "usb:UACDemoV10"})
        self.assertIn("uscita", str(caso.exception))

    def test_the_node_counts_the_repetitions_not_the_seconds(self):
        """«Per quanti secondi» non sa dire «una volta» (23/10).

        Marco ha chiesto uno sparo e ne ha sentiti due: il nodo riempiva i secondi chiesti scrivendo
        l'audio piu' in fretta del tempo reale, e quello che aveva in pancia si sentiva comunque. Con
        «volte» si contano le ripetizioni, e la durata la calcola il nodo — che e' l'unico a sapere
        quanto dura il file.
        """
        item = next(a for a in self.node.actions_list() if a["nome"] == "dissuasore")
        self.assertIn("durate", item, "il nodo dichiara quanto dura ogni suono (API 1.8)")
        lungo = item["durate"]["sparo"]
        res = self.node.activate("dissuasore", {"durata": 99, "suono": "sparo", "volte": 1})
        self.assertEqual(self.player.ripetizioni[-1][2], 1, "una volta vuol dire una")
        self.assertAlmostEqual(res["durata"], lungo + 0.2, places=2)
        self.assertEqual(self.player.loops, [], "non si riempiono piu' i secondi")

        self.node.deactivate("dissuasore")
        self.node.activate("dissuasore", {"durata": 99, "suono": "sparo", "volte": 3})
        self.assertEqual(self.player.ripetizioni[-1][2], 3)

    def test_too_many_repetitions_are_cut_to_what_fits(self):
        """Meglio due spari interi che tre con l'ultimo tagliato a meta'."""
        res = self.node.activate("dissuasore", {"durata": 99, "suono": "sparo", "volte": 100000})
        volte = self.player.ripetizioni[-1][2]
        self.assertLess(volte, 100000, "non si sfora il limite del dissuasore")
        self.assertGreaterEqual(volte, 1)
        self.assertLessEqual(res["durata"], 30, "al massimo 30 secondi per volta")

    def test_sound_list_and_choice(self):
        item = next(a for a in self.node.actions_list() if a["nome"] == "dissuasore")
        self.assertEqual(item["tipo"], "deterrent")
        self.assertEqual(item["suoni"], ["rapace", "sparo"])
        res = self.node.activate("dissuasore", {"durata": 999, "suono": "sparo"})
        self.assertEqual(res["suono"], "sparo")
        self.assertEqual(res["durata"], 30, "il nodo limita il dissuasore a 30 s")
        self.assertEqual(self.player.loops[-1][0], "dissuasore")
        with self.assertRaises(ActionError):
            self.node.deactivate("dissuasore")
            self.node.activate("dissuasore", {"durata": 5, "suono": "inesistente"})

    def test_siren_has_priority(self):
        self.node.activate("sirena", {"durata": 60})
        with self.assertRaises(ActionError) as cm:
            self.node.activate("dissuasore", {"durata": 5})
        self.assertIn("sirena", str(cm.exception))
        self.det.stop()
        self.assertEqual(self.player.stops[-1], "dissuasore", "il dissuasore spegne solo sé stesso")

    def test_hourly_budget(self):
        for _ in range(4):
            self.node.activate("dissuasore", {"durata": 30})
            self.node.deactivate("dissuasore")
            self.now[0] += 31
        with self.assertRaises(ActionError) as cm:
            self.node.activate("dissuasore", {"durata": 30})
        # Il messaggio dice il limite **e quando riprende** (25/10): «limite raggiunto» diceva che
        # era inutile insistere, non fino a quando.
        self.assertIn("2 minuti nell'ultima ora", str(cm.exception))
        self.assertIn("riprende", str(cm.exception))
        self.now[0] += 3600
        self.assertEqual(self.node.activate("dissuasore", {"durata": 10})["esito"], "ok")

    def test_owner_aware_stop(self):
        p = node_audio.AudioPlayer(output="predefinito")
        p.owner = "sirena"
        p._thread = types.SimpleNamespace(is_alive=lambda: True, join=lambda timeout=None: None)
        p.stop(owner="dissuasore")
        self.assertFalse(p._stop.is_set(), "un dissuasore non ferma la sirena")


class AudioGeneratoTest(unittest.TestCase):
    """L'audio generato: suoni del nodo che non sono allarmi (23/10 sera, idea di Marco).

    Sta accanto alla sirena e non dentro — strada A — perche' la sirena porta un limite di legge e un
    jingle da due secondi non deve mangiarsi il budget di un allarme. Due comandi, due limiti, e un
    elenco solo davanti a chi configura.
    """

    def setUp(self):
        from mirto_node.server import GeneratedAudioDevice

        self.player = FakePlayer()
        # Alla frequenza del player, non a una scelta qui: ``length()`` divide per quella del player,
        # e generare a 8 kHz un suono misurato a 22 kHz dava lunghezze sbagliate di tre volte. Nel
        # programma vero combaciano (``load_generated(..., player.rate)``), ma il primo giro di questo
        # test mi ha fatto scrivere 0,61 s dove ce n'erano 1,68: la stessa famiglia di difetto di oggi.
        self.player.rate = 8000
        self.audio = GeneratedAudioDevice(self.player,
                                          node_audio.load_generated(volume=50, rate=self.player.rate))
        self.now = [1000.0]
        self.node = Node("Orto", generate_key(),
                         {"sirena": SirenDevice(self.player, 80),
                          "dissuasore": DeterrentDevice(self.player, {"sparo": b"\0\0" * 50}),
                          "audio_generato": self.audio},
                         self.player, clock=lambda: self.now[0], cameras=lambda: [], addresses=lambda: [],
                         hostname=lambda: "orto.local", model=lambda: "Raspberry Pi 4",
                         credentials=lambda: None)

    def tearDown(self):
        self.node.stop_all()

    def test_the_node_offers_them_as_sounds_with_a_length(self):
        """Senza la lunghezza dichiarata l'interfaccia torna a chiedere i secondi: e' il difetto di oggi."""
        item = next(a for a in self.node.actions_list() if a["nome"] == "audio_generato")
        self.assertEqual(sorted(item["suoni"]), ["jingle", "matrix", "ufo"])
        for nome, secondi in item["durate"].items():
            self.assertGreater(secondi, 0, f"«{nome}» senza lunghezza non si puo' contare")
            self.assertAlmostEqual(secondi, len(self.audio.sounds[nome]) / (2 * 8000), places=1,
                                   msg="la lunghezza dichiarata e' quella vera, non una costante scritta a mano")

    def test_the_siren_stays_a_command_of_its_own(self):
        """La regressione da cui e' nato tutto: la sirena non deve diventare un suono fra gli altri.

        E' l'unica senza suoni — un ululato generato al volo — ed e' l'unica a cui si chiedono i
        secondi; se finisse qui dentro, perderebbe il suo limite di legge.
        """
        sirena = next(a for a in self.node.actions_list() if a["nome"] == "sirena")
        self.assertEqual(sirena["tipo"], "siren")
        self.assertNotIn("suoni", sirena)
        self.assertNotIn("durate", sirena)

    def test_it_counts_the_repetitions_like_any_other_sound(self):
        res = self.node.activate("audio_generato", {"durata": 99, "suono": "jingle", "volte": 2})
        self.assertEqual(self.player.ripetizioni[-1][2], 2)
        self.assertEqual(self.player.ripetizioni[-1][0], "audio")
        atteso = 2 * len(self.audio.sounds["jingle"]) / (2 * 8000) + 0.2
        self.assertAlmostEqual(res["durata"], atteso, places=1)

    def test_it_keeps_the_deterrent_limits(self):
        res = self.node.activate("audio_generato", {"durata": 999, "suono": "matrix"})
        self.assertLessEqual(res["durata"], 30, "al massimo 30 secondi per volta, come un dissuasore")

    def test_stopping_one_sound_does_not_cut_the_other(self):
        """Due comandi sulla stessa cassa: fermare il jingle non deve spegnere un dissuasore a meta'."""
        self.audio.stop()
        self.assertEqual(self.player.stops[-1], "audio")
        self.assertNotEqual(self.player.stops[-1], "dissuasore")

    def test_the_siren_still_wins(self):
        self.node.activate("sirena", {"durata": 60})
        with self.assertRaises(ActionError) as cm:
            self.node.activate("audio_generato", {"durata": 5, "suono": "ufo"})
        self.assertIn("sirena", str(cm.exception))


class SoundsTest(unittest.TestCase):
    def test_generated_sounds_are_real_audio(self):
        """Tre suoni nuovi: che esistano, che non siano silenzio e che non durino un'era."""
        for name, fn in node_audio.GENERATED_SOUNDS.items():
            pcm = fn(volume=60, rate=8000)
            secondi = len(pcm) / (2 * 8000)
            self.assertGreater(secondi, 0.5, f"«{name}» troppo corto per sentirsi")
            self.assertLess(secondi, 12, f"«{name}» e' un suono, non un disco")
            self.assertTrue(any(pcm[i:i + 2] != b"\0\0" for i in range(0, len(pcm), 2)),
                            f"«{name}» e' silenzio")

    def test_the_generated_sounds_are_equally_loud(self):
        """Tre suoni nello stesso elenco devono uscire allo stesso volume (23/10).

        Marco: *«matrix è di volume molto più basso delle 3»*. Era vero, ed era colpa della
        normalizzazione sul **picco**: il Matrix è fatto di colpi di basso, stesso picco dell'UFO e
        metà dell'energia — nove decibel di differenza, cioè «più basso» a orecchio. Adesso si
        pareggia il livello percepito (RMS) con un limitatore morbido; questo test tiene il patto.
        """
        livelli = {}
        for name, fn in node_audio.GENERATED_SOUNDS.items():
            campioni = array.array("h")
            campioni.frombytes(fn(volume=90, rate=8000))
            livelli[name] = math.sqrt(sum(float(v) * v for v in campioni) / len(campioni))
        self.assertLess(max(livelli.values()) / min(livelli.values()), 1.6,
                        f"livelli troppo diversi fra loro: {livelli}")
        self.assertGreater(min(livelli.values()), 0.30 * 32767,
                           "un suono di dissuasione o di festa si deve sentire: non e' musica di sottofondo")

    def test_builtin_sounds(self):
        for name, fn in node_audio.BUILTIN_SOUNDS.items():
            pcm = fn(volume=50, rate=8000)
            secs = len(pcm) / 2 / 8000
            self.assertTrue(1.5 <= secs <= 10, f"{name}: {secs:.1f} s")
            vals = np.frombuffer(pcm, np.int16)
            self.assertGreater(np.abs(vals).max(), 10000, f"{name}: troppo debole")

    def test_custom_wav(self):
        with tempfile.TemporaryDirectory() as d:
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setnchannels(2); w.setsampwidth(2); w.setframerate(44100)
                w.writeframes(np.zeros(44100 * 2, np.int16).tobytes())
            Path(d, "Poiana Vera.wav").write_bytes(buf.getvalue())
            Path(d, "rotto.wav").write_bytes(b"non sono un wav")
            sounds = node_audio.load_sounds(Path(d), rate=22050)
            self.assertIn("poiana_vera", sounds)
            self.assertNotIn("rotto", sounds)
            self.assertEqual(len(sounds["poiana_vera"]), 22050 * 2, "1 s mono a 22050 Hz")
            self.assertIn("rapace", sounds)


LABELS = "0  Corvus brachyrhynchos (American Crow)\n1  Columba livia (Rock Pigeon)\n2  Corvus corax (Common Raven)\n" \
         "3  Turdus migratorius (American Robin)\n4  background\n"


class FakeBackend:
    kind = "finto"
    size = (224, 224)

    def __init__(self, probs):
        self.probs = np.asarray(probs, np.float32)
        self.seen = None

    def run(self, rgb):
        self.seen = rgb.shape
        return self.probs


class SpeciesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.labels = Path(self.tmp.name, "labels.txt")
        self.labels.write_text(LABELS)

    def tearDown(self):
        self.tmp.cleanup()

    def test_labels_and_groups(self):
        lab = parse_labels(LABELS)
        self.assertEqual(lab[1], ("Columba livia", "Rock Pigeon"))
        self.assertEqual(genus_group("Corvus corone"), "corvid")
        self.assertEqual(genus_group("Columba palumbus"), "pigeon")
        self.assertEqual(genus_group("Turdus merula"), "blackbird")
        self.assertEqual(genus_group("Haemorhous mexicanus"), "")

    def test_group_wins_over_single_species(self):
        # nessuna specie di corvo supera il piccione da sola, ma insieme sì: il gruppo è "corvid"
        clf = SpeciesClassifier(None, self.labels, backend=FakeBackend([0.3, 0.35, 0.3, 0.05, 0.0]))
        res = clf.classify(np.zeros((480, 640, 3), np.uint8), (100, 100, 200, 180))
        self.assertEqual(res.scientific, "Columba livia")
        self.assertEqual(res.group, "corvid")
        self.assertAlmostEqual(res.group_score, 0.6, places=5)
        self.assertEqual(clf.backend.seen, (224, 224, 3))

    def test_needs_species(self):
        cfg = Config(Path(self.tmp.name, "c.yaml"))
        self.assertFalse(needs_species(cfg))
        cfg.set("actions.items", [DETERRENT])
        self.assertTrue(needs_species(cfg))
        self.assertEqual(deterrent_indices(cfg), [INDEX["bird"]])


class FakeDetector:
    def __init__(self, dets):
        self.dets = dets

    def detect(self, frame, threshold=0.3, classes=None):
        return [d for d in self.dets if d.cls in (classes or [0]) and d.score >= threshold]


class PipelineDeterrentTest(unittest.TestCase):
    def test_bird_not_watched_reaches_deterrent_with_species(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Config(Path(d) / "c.yaml")
            cfg.set("actions.items", [DETERRENT])  # "bird" non è tra le categorie degli avvisi
            labels = Path(d, "l.txt")
            labels.write_text(LABELS)
            clf = SpeciesClassifier(None, labels, backend=FakeBackend([0.7, 0.1, 0.1, 0.1, 0.0]))
            bird = Detection((100, 100, 160, 150), 0.9, INDEX["bird"])
            src = types.SimpleNamespace(info=CameraInfo("cam1", "Orto", index=0))
            events, detected = [], []
            t = [1000.0]
            pipe = CameraPipeline(src, FakeDetector([bird]), cfg, events.append, None, clock=lambda: t[0],
                                  on_detect=detected.append, species=lambda: clf)
            pipe.motion.update = lambda frame: True
            frame = np.full((240, 320, 3), 128, np.uint8)
            for _ in range(5):
                pipe.process(frame)
                t[0] += 0.2
            self.assertEqual(events, [], "niente avviso: 'bird' non è sorvegliato")
            self.assertEqual(len(detected), 1)
            self.assertEqual(detected[0].species_group, "corvid")
            self.assertIn("bird:corvid", detected[0].targets())
            self.assertIn("American Crow", detected[0].species)


if __name__ == "__main__":
    unittest.main()


class ReviewFixesTest(unittest.TestCase):
    """Correzioni dopo la revisione del codice."""

    def test_nothing_is_stripped_from_the_targets_any_more(self):
        """La lista bianca e\' caduta: quello che chi configura ha chiesto, resta.

        Al suo posto c'e\' un avviso visibile (vedi «avvisi.py»): una scelta discutibile si segnala a
        chi la fa, non si corregge alle sue spalle.
        """
        spec = ActionSpec.from_dict({**DETERRENT, "targets": ["bird:corvid", "sound:speech", "car"]})
        self.assertEqual(spec.targets, ["bird:corvid", "sound:speech", "car"])
        ok = ActionSpec.from_dict({**DETERRENT, "targets": ["cat", "bird", "sound:dog"]})
        self.assertEqual(ok.targets, ["cat", "bird", "sound:dog"])

    def test_person_in_frame_blocks_deterrent(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Config(Path(d) / "c.yaml")
            cfg.set("actions.items", [{**DETERRENT, "targets": ["bird"]}])
            cfg.set("detection.watch", ["dog"])  # persone non negli avvisi: vanno cercate lo stesso
            dets = [Detection((100, 100, 160, 150), 0.9, INDEX["bird"]), Detection((10, 10, 90, 200), 0.8, 0)]
            src = types.SimpleNamespace(info=CameraInfo("cam1", "Orto", index=0))
            events, detected, actions = [], [], []
            t = [1000.0]
            pipe = CameraPipeline(src, FakeDetector(dets), cfg, events.append, actions.append, clock=lambda: t[0],
                                  on_detect=detected.append)
            pipe.motion.update = lambda frame: True
            for _ in range(5):
                pipe.process(np.zeros((240, 320, 3), np.uint8))
                t[0] += 0.2
            self.assertEqual(detected, [], "c'è una persona: nessun dissuasore")
            self.assertEqual(events, [], "persone non scelte negli avvisi: nessun avviso")
            self.assertEqual(actions, [], "e nessuna azione antintrusione")

    def test_sound_event_never_strong_confirmation(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Config(Path(d) / "c.yaml")
            core = types.SimpleNamespace(config=cfg, secrets=mem_secrets(), notifier=FakeNotifier())
            eng = ActionEngine(core, client_factory=lambda nd, s: FakeClient())
            try:
                eng.on_intrusion(ev("sound:dog", cam="cam1"))
                self.assertNotIn("cam1", eng._strong)
                eng.on_intrusion(ev("person", cam="cam1"))
                self.assertIn("cam1", eng._strong)
            finally:
                eng.stop()

    def test_recently_active_and_stop_all_pause(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Config(Path(d) / "c.yaml")
            cfg.set("actions.items", [DETERRENT])
            now = [5000.0]
            client = FakeClient()
            core = types.SimpleNamespace(config=cfg, secrets=mem_secrets(), notifier=FakeNotifier())
            eng = ActionEngine(core, client_factory=lambda nd, s: client, clock=lambda: now[0])
            eng.clients = {"orto": client}
            eng._pool = types.SimpleNamespace(submit=lambda fn, *a: fn(*a), shutdown=lambda **k: None)
            try:
                self.assertFalse(eng.recently_active())
                eng.on_detection(ev("bird", "corvid"))
                self.assertTrue(eng.recently_active(), "il nodo sta suonando: i suoni sentiti ora sono suoi")
                now[0] += 8 + 10 + 1
                self.assertFalse(eng.recently_active())
                eng.stop_all()
                now[0] += 600
                self.assertEqual(eng.on_detection(ev("bird", "corvid")), [], "Ferma tutto: dissuasori fermi un'ora")
                now[0] += 3600
                self.assertEqual(eng.on_detection(ev("bird", "corvid")), ["Via i corvi"])
            finally:
                eng.stop()

    def test_windows_language_names(self):
        from unittest import mock

        from mirto import i18n
        with mock.patch.dict(os.environ, {"LANG": "English_United States.1252", "LC_ALL": "", "LC_MESSAGES": ""}), \
                mock.patch.object(i18n, "sistema", lambda: "linux"), \
                mock.patch.object(i18n.locale, "getlocale", lambda: (None, None)):
            self.assertEqual(i18n.system_language(), "en")


def wav_finto(secondi: float = 1.0, rate: int = 22050, canali: int = 1) -> bytes:
    """Un WAV vero e silenzioso, della durata chiesta: serve a provare il caricamento."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(canali); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(np.zeros(int(rate * secondi) * canali, np.int16).tobytes())
    return buf.getvalue()


class CaricareUnSuonoSulNodoTest(unittest.TestCase):
    """I suoni propri si caricano dalle impostazioni (chiesto da Marco il 23/10).

    I nostri sono generati e non hanno licenze addosso; quelli di chi usa il programma arrivano da un
    file suo. Il nodo resta un posto che sa suonare **solo WAV** — la conversione da mp3 o m4a la fa
    il computer, che ha gia' ffmpeg — cosi' su un dispositivo in giardino non c'e' un decoder in piu'
    da aggiornare.

    I limiti sono **dichiarati**, non scoperti: il nodo dice quanto puo' durare e quanto puo' pesare
    un suono, cosi' il programma lo scrive prima di far scegliere un file invece che dopo averlo
    caricato per un minuto.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cartella = Path(self.tmp.name, "suoni")
        self.player = FakePlayer()
        self.player.rate = 22050
        self.det = DeterrentDevice(self.player, node_audio.load_sounds(None, rate=self.player.rate))
        self.node = Node("Orto", generate_key(), {"dissuasore": self.det}, self.player,
                         cameras=lambda: [], addresses=lambda: [], hostname=lambda: "orto.local",
                         model=lambda: "Raspberry Pi 4", credentials=lambda: None,
                         folder=Path(self.tmp.name), cartella_suoni=self.cartella)

    def tearDown(self):
        self.node.stop_all(); self.tmp.cleanup()

    def test_the_node_says_its_limits_before_you_choose_a_file(self):
        stato = self.node.sounds_state()
        self.assertGreater(stato["massimo_s"], 0)
        self.assertGreater(stato["massimo_byte"], 0)
        nostri = [s for s in stato["suoni"] if s["nostro"]]
        self.assertTrue(nostri, "i suoni generati dal nodo si vedono nell'elenco")
        self.assertTrue(all(s["secondi"] > 0 for s in nostri), "con la loro durata")

    def test_an_uploaded_sound_works_right_away_and_survives_a_restart(self):
        import base64

        esito = self.node.add_sound({"nome": "Voce Mia.m4a", "wav": base64.b64encode(wav_finto(1.5)).decode()})
        self.assertEqual(esito["nome"], "voce_mia", "il nome si ripulisce, come per i file nella cartella")
        # subito disponibile, senza riavviare il servizio
        item = next(a for a in self.node.actions_list() if a["nome"] == "dissuasore")
        self.assertIn("voce_mia", item["suoni"])
        self.assertAlmostEqual(item["durate"]["voce_mia"], 1.5, places=1)
        # e domani, quando il nodo riparte, lo ritrova: e' un file, non una cosa in memoria
        self.assertTrue((self.cartella / "voce_mia.wav").exists())
        dopo_riavvio = node_audio.load_sounds(self.cartella, rate=self.player.rate)
        self.assertIn("voce_mia", dopo_riavvio)

    def test_a_name_of_ours_is_not_overwritten(self):
        import base64

        with self.assertRaises(ActionError) as cm:
            self.node.add_sound({"nome": "sparo", "wav": base64.b64encode(wav_finto(0.5)).decode()})
        self.assertIn("sparo", str(cm.exception))
        self.assertEqual(node_audio.load_sounds(self.cartella, rate=self.player.rate).get("sparo"),
                         self.det.sounds["sparo"], "il nostro e' rimasto quello di prima")

    def test_a_sound_too_long_is_refused_not_silently_cut(self):
        """Tagliarlo di nascosto vorrebbe dire far sentire meta' frase e non dirlo a nessuno."""
        import base64

        lungo = wav_finto(self.node.sounds_state()["massimo_s"] + 5)
        with self.assertRaises(ActionError) as cm:
            self.node.add_sound({"nome": "lungo", "wav": base64.b64encode(lungo).decode()})
        self.assertIn("second", str(cm.exception).lower())
        self.assertFalse((self.cartella / "lungo.wav").exists())

    def test_rubbish_is_refused_with_a_reason(self):
        import base64

        with self.assertRaises(ActionError):
            self.node.add_sound({"nome": "finto", "wav": base64.b64encode(b"non sono un wav").decode()})
        with self.assertRaises(ActionError):
            self.node.add_sound({"nome": "", "wav": base64.b64encode(wav_finto(0.5)).decode()})
        with self.assertRaises(ActionError):
            # «....» diventerebbe «___»: e' il rumore della sostituzione, non un nome
            self.node.add_sound({"nome": "....", "wav": base64.b64encode(wav_finto(0.5)).decode()})

    def test_removing_takes_away_the_file_and_the_sound(self):
        import base64

        self.node.add_sound({"nome": "mio", "wav": base64.b64encode(wav_finto(1.0)).decode()})
        self.node.remove_sound({"nome": "mio"})
        self.assertFalse((self.cartella / "mio.wav").exists())
        item = next(a for a in self.node.actions_list() if a["nome"] == "dissuasore")
        self.assertNotIn("mio", item["suoni"])

    def test_ours_cannot_be_removed(self):
        """Non sono file: sono qualche riga di matematica. Toglierli non vuol dire niente."""
        with self.assertRaises(ActionError):
            self.node.remove_sound({"nome": "sparo"})
        self.assertIn("sparo", self.det.sounds)


class UnPezzoRifiutatoNonPortaViaLaCatenaTest(unittest.TestCase):
    """Tappa 4: se la cassa e' occupata, non parte **quel** suono — non muore la catena.

    La regola di fermarsi dopo un errore nasce dalla fila, e li' e' giusta: accendere la luce di una
    sequenza il cui suono non e' partito lascia la casa a metà di qualcosa. Ma un pezzo che parte
    **insieme** a un altro ha il pezzo di prima gia' partito: fermarsi non riporta niente indietro,
    e spegne anche i pezzi dopo, che non c'entrano niente.

    Il caso che succede davvero e' la cassa contesa: il nodo dall'API 1.11 dice no al secondo suono
    invece di risponder «ok» e ammazzare il primo, e l'unica cosa che non deve avvenire e' quel suono.
    """

    def motore(self, catena, rifiuta):
        chiamate = []

        class Cliente:
            def activate(self, azione, durata, event_id="", camera="", extra=None):
                chiamate.append(azione)
                if azione == rifiuta:
                    raise RuntimeError("«dissuasore» sta gia' suonando da quella cassa: aspetta")
                return {"esito": "ok"}

            def stop_all(self):
                return {"esito": "ok"}

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = Config(Path(tmp.name) / "c.yaml")
        azione = {"name": "Cancello", "node": "orto", "action": "dissuasore", "kind": "generic",
                  "targets": ["person"], "duration": 60, "passi": catena}
        cfg.set("actions.items", [azione])
        cliente = Cliente()
        core = types.SimpleNamespace(config=cfg, secrets=mem_secrets(), notifier=FakeNotifier())
        eng = ActionEngine(core, client_factory=lambda nd, s: cliente, clock=lambda: 1_800_000_000.0)
        self.addCleanup(eng.stop)
        eng.clients = {"orto": cliente}
        eng._pool = types.SimpleNamespace(submit=lambda fn, *a: fn(*a), shutdown=lambda **k: None)
        # Niente attese vere: si esegue subito quello che il motore programma, cosi' la prova guarda
        # **quali** chiamate partono e non quando.
        eng._schedule = lambda _fra, fn, *a: fn(*a)
        return eng, chiamate

    CATENA = [{"comando": "dissuasore", "suono": "sparo", "durata": 2},
              {"comando": "audio_generato", "suono": "ufo", "durata": 2, "nel_gruppo": True},
              {"comando": "luci", "durata": 30}]

    def test_the_rest_of_the_chain_goes_on(self):
        eng, chiamate = self.motore(self.CATENA, rifiuta="audio_generato")
        eng.set_armed(True)
        eng.on_intrusion(ev("person"))
        self.assertEqual(chiamate, ["dissuasore", "audio_generato", "luci"],
                         "il suono rifiutato non c'e' stato; la luce dopo invece si")

    def test_a_piece_in_a_row_still_stops_the_chain(self):
        """La regola di prima resta dov'era: in fila, un errore ferma quello che viene dopo."""
        catena = [{"comando": "dissuasore", "suono": "sparo", "durata": 2},
                  {"comando": "audio_generato", "suono": "ufo", "durata": 2},
                  {"comando": "luci", "durata": 30}]
        eng, chiamate = self.motore(catena, rifiuta="audio_generato")
        eng.set_armed(True)
        eng.on_intrusion(ev("person"))
        self.assertEqual(chiamate, ["dissuasore", "audio_generato"],
                         "la luce non si accende: la sequenza e' rotta a metà")


class IlGiroAvanzaSoloSeLoScattoAvvieneTest(unittest.TestCase):
    """Tappa 5 nel motore: a ogni scatto tocca al prossimo, e uno scatto mancato non consuma un turno.

    E' la parte che si puo' sbagliare in silenzio. Contare il giro **prima** di provarci sarebbe
    stato piu' comodo — una riga sola in cima — e avrebbe fatto saltare un'operazione ogni volta che
    l'azione era ancora in corso, o che il nodo non rispondeva: un suono che non si sente e che a
    nessuno risulta mai mancato, perche' nel registro c'e' scritto che quello scatto c'e' stato.
    """

    CATENA = [{"comando": "dissuasore", "suono": "sparo", "durata": 2, "a_turno": True},
              {"comando": "dissuasore", "suono": "corvi", "durata": 2, "nel_gruppo": True},
              {"comando": "dissuasore", "suono": "falco", "durata": 2, "nel_gruppo": True}]

    def motore(self, rompe=False):
        suonati = []
        ora = [1_800_000_000.0]

        class Cliente:
            def activate(self, azione, durata, event_id="", camera="", extra=None):
                if rompe:
                    raise RuntimeError("il nodo non risponde")
                suonati.append((extra or {}).get("suono", ""))
                return {"esito": "ok"}

            def stop_all(self):
                return {"esito": "ok"}

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = Config(Path(tmp.name) / "c.yaml")
        cfg.set("actions.items", [{"name": "Orto", "node": "orto", "action": "dissuasore",
                                   "kind": "generic", "targets": ["person"], "duration": 10,
                                   "passi": self.CATENA}])
        cliente = Cliente()
        core = types.SimpleNamespace(config=cfg, secrets=mem_secrets(), notifier=FakeNotifier())
        eng = ActionEngine(core, client_factory=lambda nd, s: cliente, clock=lambda: ora[0])
        self.addCleanup(eng.stop)
        eng.clients = {"orto": cliente}
        eng._pool = types.SimpleNamespace(submit=lambda fn, *a: fn(*a), shutdown=lambda **k: None)
        eng._schedule = lambda _fra, fn, *a: fn(*a)
        return eng, suonati, ora

    def scatta(self, eng, ora):
        ora[0] += 600          # lontano dallo scatto prima: niente pause, niente «gia' in corso»
        eng.set_armed(True)
        eng.on_intrusion(ev("person"))

    def test_three_firings_three_different_sounds(self):
        eng, suonati, ora = self.motore()
        for _ in range(3):
            self.scatta(eng, ora)
        self.assertEqual(suonati, ["sparo", "corvi", "falco"],
                         "a ogni scatto il prossimo, in ordine")

    def test_a_firing_that_did_not_happen_does_not_use_up_a_turn(self):
        """Il nodo non risponde: nessuno ha sentito niente, quindi il turno e' ancora di «sparo»."""
        eng, suonati, ora = self.motore(rompe=True)
        self.scatta(eng, ora)
        self.assertEqual(suonati, [])
        self.assertEqual(eng._giri.get("Orto", 0), 0, "non si conta uno scatto che non c'e' stato")

    def test_the_duration_is_computed_on_the_piece_that_will_start(self):
        """Il conto del tempo e la scelta guardano lo **stesso** giro: una domanda, una risposta.

        Se il conto usasse un giro e l'esecuzione un altro, l'azione risulterebbe «in corso» per la
        durata di un'operazione diversa da quella che sta suonando — e quando le durate sono diverse
        il prossimo scatto arriva troppo presto o troppo tardi, senza che nulla lo spieghi.
        """
        eng, _suonati, _ora = self.motore()
        spec = next(s for s in eng.specs if s.name == "Orto")
        for giro in range(3):
            eng._giri["Orto"] = giro
            catena = spec.catena(giro=eng._giri["Orto"])
            self.assertEqual(len(catena), 1, "di un gruppo a turno parte un pezzo solo")
            self.assertEqual(catena[0]["suono"], ["sparo", "corvi", "falco"][giro])


class AzioneDiTipoPrankTest(unittest.TestCase):
    """Il terzo valore di «Quando e' attiva»: `prank` (26/10).

    Marco: *«se l'azione e' di tipo prank bypassa come ci siamo gia' detti alcune regole»*, e *«la
    scelta la metterei nel primo step»*. Le regole che salta sono le **nostre** — pausa fra due
    scatti, tetto giornaliero, fascia oraria — piu' il divieto di avere «persona» fra gli obiettivi,
    che per ogni altro dissuasore e' assoluto.

    In cambio di quell'eccezione, un'azione prank scatta **solo mentre la modalita' Prank del nodo e'
    accesa**: una finestra che apre una persona, con una durata scelta, che scade da se' e che «Ferma
    tutto» chiude. Chiesto a Marco il 26/10 fra tre possibilita', e questa e' la sua scelta — l'unica
    in cui l'eccezione si puo' concedere, perche' non e' il programma a dichiarare che quello e' uno
    scherzo: e' qualcuno che ha aperto la finestra sul nodo cinque minuti prima.
    """

    PRANK = {"name": "Scherzo", "node": "orto", "action": "dissuasore", "kind": "deterrent",
             "targets": ["person"], "sounds": ["sparo"], "duration": 5, "quando": "prank"}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(Path(self.tmp.name) / "c.yaml")
        self.cfg.set("actions.items", [self.PRANK])
        self.notifier = FakeNotifier()
        self.client = FakeClient()
        core = types.SimpleNamespace(config=self.cfg, secrets=mem_secrets(), notifier=self.notifier)
        self.now = [1_800_000_000.0]
        self.engine = ActionEngine(core, client_factory=lambda nd, s: self.client, clock=lambda: self.now[0])
        self.engine.clients = {"orto": self.client}
        self.engine._pool = types.SimpleNamespace(submit=lambda fn, *a: fn(*a), shutdown=lambda **k: None)

    def tearDown(self):
        self.engine.stop()
        self.tmp.cleanup()

    def accendi(self, minuti=10):
        self.engine.node_info["orto"] = {"prank": {"attiva": True, "restano_s": minuti * 60}}

    def persona(self):
        return self.engine.on_intrusion(ev("person")) or self.client.calls

    def test_a_prank_stays_a_deterrent_with_people_among_its_targets(self):
        """Per ogni altro dissuasore «persona» fra gli obiettivi lo declassa a generico: e' la regola
        che impedisce di puntare un suono su qualcuno. Qui no, e **solo** qui."""
        spec = ActionSpec.from_dict(dict(self.PRANK))
        self.assertEqual(spec.kind, "deterrent", "resta un dissuasore")
        self.assertIn("person", spec.targets)
        self.assertEqual(spec.quando, "prank")
        # Senza «quando: prank» la stessa azione viene declassata, come e' sempre stato.
        normale = ActionSpec.from_dict({k: v for k, v in self.PRANK.items() if k != "quando"})
        self.assertNotEqual(normale.kind, "deterrent")

    def test_it_does_not_fire_outside_the_window(self):
        """Fuori dalla finestra un'azione prank non parte mai: e' la meta' dello scambio."""
        self.engine.on_intrusion(ev("person"))
        self.assertEqual(self.client.calls, [], "modalita' spenta: niente")

    def test_it_fires_while_the_mode_is_on(self):
        self.accendi()
        self.engine.on_intrusion(ev("person"))
        self.assertTrue(self.client.calls, "dentro la finestra scatta")

    def test_inside_the_window_it_has_no_pause_between_shots(self):
        """La pausa minima di un dissuasore e' un minuto: su uno scherzo renderebbe inutile proprio la
        cosa per cui esiste. Il freno e' la finestra, non il cronometro."""
        spec = ActionSpec.from_dict(dict(self.PRANK, cooldown=0))
        self.assertEqual(spec.cooldown, 0, "il minimo di un minuto non si applica a un prank")
        self.accendi()
        for _ in range(4):
            self.engine.on_intrusion(ev("person"))
        self.assertEqual(len(self.client.calls), 4, "quattro volte di fila, senza aspettare")

    def test_inside_the_window_the_daily_cap_does_not_stop_it(self):
        self.cfg.set("actions.items", [dict(self.PRANK, max_per_day=1)])
        self.engine.reload()
        self.engine.clients = {"orto": self.client}
        self.accendi()
        for _ in range(3):
            self.engine.on_intrusion(ev("person"))
        self.assertEqual(len(self.client.calls), 3)

    def test_stop_everything_closes_the_window_for_it_too(self):
        """«Ferma tutto» spegne la modalita' sul nodo **e** sospende i dissuasori per un'ora qui: un
        prank non deve poter ripartire un minuto dopo che qualcuno ha detto basta."""
        self.accendi()
        self.engine.deterrents_paused_until = self.now[0] + 3600
        self.engine.on_intrusion(ev("person"))
        self.assertEqual(self.client.calls, [])

    def test_an_expired_window_closes_it_by_itself(self):
        self.accendi()
        self.engine.on_intrusion(ev("person"))
        self.assertTrue(self.client.calls)
        # Lo stato periodico dice che la modalita' e' scaduta: l'azione torna a non scattare, senza
        # che nessuno la spenga.
        self.engine.node_info["orto"] = {"prank": {"attiva": False, "restano_s": 0}}
        quante = len(self.client.calls)
        self.engine.on_intrusion(ev("person"))
        self.assertEqual(len(self.client.calls), quante)

    def test_an_uncertain_person_is_not_enough_even_for_a_prank(self):
        """Una persona **vista** non basta: serve la conferma forte. Vale anche qui, perche' quella
        regola non riguarda i limiti d'uso ma quanto si crede al riconoscimento."""
        self.accendi()
        self.assertEqual(self.engine.on_detection(ev("person")), [])
        self.assertEqual(self.client.calls, [])

    # --- il secondo cancello: la casa inserita (26/10) ----------------------

    def test_an_armed_house_silences_it(self):
        """Marco, 26/10: *«se uno ha piu' azioni ed attiva la protezione voglio che i prank non siano
        attivi durante il lavoro serio»*.

        E' il difetto che aveva visto: la finestra sul nodo era l'unico cancello, quindi uno scherzo
        partiva anche a antifurto inserito -- cioe' mandava sul telefono di chi non e' in casa un
        avviso identico a quello di un'intrusione. Adesso i cancelli sono due.
        """
        self.accendi()
        self.engine.config.set("actions.armed", True)
        self.engine.on_intrusion(ev("person"))
        self.assertEqual(self.client.calls, [], "casa protetta: lo scherzo non parte")

    def test_the_window_survives_the_armed_house(self):
        """Scelta di Marco fra le due: la finestra **resta aperta** e muta, non si chiude.

        Se inserisci la protezione per dieci minuti e poi disinserisci, i minuti che restano valgono
        ancora: era l'alternativa a chiuderla, e la differenza si vede solo qui.
        """
        self.accendi(minuti=10)
        self.engine.config.set("actions.armed", True)
        self.engine.on_intrusion(ev("person"))
        self.assertEqual(self.client.calls, [])
        self.engine.config.set("actions.armed", False)
        self.engine.on_intrusion(ev("person"))
        self.assertTrue(self.client.calls, "disinserita prima della scadenza: riprende da dove era")

    def test_an_armed_house_does_not_consume_the_prank(self):
        """Zittito non vuol dire scattato: il tetto e la pausa non devono segnare niente mentre tace.

        Un prank che a casa inserita «consuma» i suoi scatti sarebbe muto due volte -- una per la
        protezione e una per un contatore che nessuno ha fatto girare.
        """
        self.accendi()
        self.engine.config.set("actions.armed", True)
        for _i in range(3):
            self.engine.on_intrusion(ev("person"))
        self.assertNotIn("Scherzo", self.engine._deterrent_last,
                         "non deve aver segnato nessuno scatto")
        self.engine.config.set("actions.armed", False)
        self.engine.on_intrusion(ev("person"))
        self.assertTrue(self.client.calls)
