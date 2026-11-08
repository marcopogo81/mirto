# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Eventi audio: riconoscimento (YAMNet finto), conferma, pause, microfono del nodo, azioni."""
import io
import os
import sys
import tempfile
import threading
import time
import types
import unittest
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "node"))

from mirto_node.mic import Microphone, MicError  # noqa: E402

from mirto.actions.engine import ActionEngine  # noqa: E402
from mirto.actions.nodes import NodeClient, NodeConfig, fetch_certificate  # noqa: E402
from mirto.actions.simulator import SimulatorServer  # noqa: E402
from mirto.audio.classifier import RATE, WINDOW, SoundClassifier, to_mono_16k  # noqa: E402
from mirto.audio.monitor import AudioMonitor, MicSource  # noqa: E402
from mirto.config import Config  # noqa: E402
from tests.test_actions import mem_secrets  # noqa: E402

NAMES = ["Speech", "Dog", "Bark", "Glass", "Shatter", "Smoke detector, smoke alarm", "Fire alarm", "Music"]


class FakeSession:
    """Come onnxruntime: un ingresso, uscite con 521 (qui 8) colonne; i punteggi li decide il test."""

    def __init__(self):
        self.next = np.zeros(len(NAMES), np.float32)
        self.calls = 0

    def get_inputs(self):
        return [types.SimpleNamespace(name="waveform")]

    def get_outputs(self):
        return [types.SimpleNamespace(name="emb", shape=[None, 1024]),
                types.SimpleNamespace(name="scores", shape=[None, len(NAMES)])]

    def run(self, names, feed):
        assert names == ["scores"] and feed["waveform"].shape == (WINDOW,)
        self.calls += 1
        return [self.next[None]]


def set_scores(sess, **kw):
    sess.next = np.zeros(len(NAMES), np.float32)
    for name, v in kw.items():
        sess.next[NAMES.index(name.replace("_", " "))] = v


class ClassifierTest(unittest.TestCase):
    def test_groups_and_output_choice(self):
        sess = FakeSession()
        clf = SoundClassifier(None, NAMES, session=sess)
        self.assertEqual(clf.output_name, "scores", "sceglie l'uscita con una colonna per classe")
        self.assertIn("glass", clf.groups)
        self.assertNotIn("scream", clf.groups, "nessuna classe di 'urla' nel modello finto")
        set_scores(sess, Bark=0.7, Dog=0.2)
        k = clf.kinds(np.zeros(100, np.float32))  # finestra corta: completata con zeri
        self.assertAlmostEqual(k["dog"], 0.7, places=5)
        self.assertEqual(clf.top(np.zeros(WINDOW))[0][0], "Bark")

    def test_resampling(self):
        stereo48 = np.zeros(48000 * 2, np.int16)
        self.assertEqual(len(to_mono_16k(stereo48, 48000, 2)), 16000)
        self.assertEqual(len(to_mono_16k(np.zeros(44100, np.float32), 44100)), 16000)
        x = to_mono_16k(np.full(16000, 16384, np.int16), 16000)
        self.assertAlmostEqual(float(x.mean()), 0.5, places=3)


class MonitorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(Path(self.tmp.name) / "c.yaml")
        self.sess = FakeSession()
        self.events = []
        self.now = [1000.0]
        self.mon = AudioMonitor(self.cfg, lambda: SoundClassifier(None, NAMES, session=self.sess), self.events.append,
                                snapshot=lambda cam: np.full((4, 4, 3), 7, np.uint8), clock=lambda: self.now[0])
        self.src = MicSource("mac", "Microfono del Mac", camera_id="mac:cam")
        self.hist = {}

    def tearDown(self):
        self.mon.stop()
        self.tmp.cleanup()

    def window(self, loud=True):
        return np.full(WINDOW, 0.2 if loud else 0.0, np.float32)

    def test_confirmation_and_cooldown(self):
        self.sess.next[NAMES.index("Smoke detector, smoke alarm")] = 0.8
        self.assertEqual(self.mon.analyze(self.src, self.window(), self.hist), [], "serve una seconda finestra")
        ev = self.mon.analyze(self.src, self.window(), self.hist)[0]
        self.assertEqual(ev.label, "sound:smoke_alarm")
        self.assertEqual(ev.camera_id, "mac:cam")
        self.assertEqual(ev.image[0, 0, 0], 7, "foto dalla camera associata al microfono")
        self.assertEqual(self.mon.analyze(self.src, self.window(), self.hist), [])
        self.assertEqual(self.mon.analyze(self.src, self.window(), self.hist), [], "pausa anti-raffica")
        self.now[0] += 121
        self.assertEqual(len(self.mon.analyze(self.src, self.window(), self.hist)), 1,
                         "il suono continua: nuovo avviso appena finita la pausa")

    def test_glass_needs_one_window_and_silence_skips_model(self):
        self.sess.next[NAMES.index("Shatter")] = 0.5
        self.assertEqual(len(self.mon.analyze(self.src, self.window(), self.hist)), 1)
        calls = self.sess.calls
        self.mon.analyze(self.src, self.window(loud=False), self.hist)
        self.assertEqual(self.sess.calls, calls, "silenzio: il modello non lavora")

    def test_the_peak_is_kept_so_the_gain_can_be_judged(self):
        """`level` e' l'RMS dell'ultima finestra e fra una parola e l'altra va a zero: su quello non si
        puo' dire se il guadagno va bene. Il picco tenuto si', ed e' quello che fa dire alla riga di
        *Sistema* perche' la barretta e' vuota (26/10)."""
        from mirto.actions.pannello_audio import PICCO_TENUTO, giudizio_livello

        self.mon.analyze(self.src, self.window(), self.hist)
        self.assertGreaterEqual(self.src.picco, self.src.level, "il picco non sta sotto l'RMS")
        self.assertGreater(self.src.picco, 0.0, "con un suono dentro, il picco c'e'")
        picco = self.src.picco
        self.mon.analyze(self.src, self.window(loud=False), self.hist)
        self.assertEqual(self.src.picco, picco, "silenzio subito dopo: il picco si tiene")
        self.assertLess(self.src.level, picco, "ma il livello e' quello di adesso")
        # Passata la tenuta, il picco scende: un giudizio non resta appeso a un rumore di dieci minuti fa.
        self.now[0] += PICCO_TENUTO + 1
        self.mon.analyze(self.src, self.window(loud=False), self.hist)
        self.assertLess(self.src.picco, picco)
        self.assertEqual(giudizio_livello(self.src.picco)[0], "silenzio")
        # E Mirto lo deve poter leggere: la sorgente si registra (il thread che consuma la coda
        # vuota non fa niente) e si guarda cosa esce da `status()`.
        self.mon.add_source(self.src)
        self.addCleanup(self.mon.remove_source, self.src.id)
        letto = self.mon.status()[0]
        self.assertIn("picco", letto)
        self.assertEqual(letto["picco"], self.src.picco)

    def test_only_watched_or_targeted_kinds(self):
        self.sess.next[NAMES.index("Bark")] = 0.9
        for _ in range(3):
            self.mon.analyze(self.src, self.window(), self.hist)
        self.assertEqual(self.events, [], "il cane non è tra i suoni sorvegliati")
        self.cfg.set("actions.items", [{"name": "D", "node": "n", "action": "dissuasore", "kind": "deterrent",
                                        "targets": ["sound:dog"]}])
        self.assertIn("dog", self.mon.watched())
        for _ in range(2):
            self.mon.analyze(self.src, self.window(), self.hist)
        self.assertEqual([e.label for e in self.events], ["sound:dog"])

    def test_threaded_feed(self):
        self.sess.next[NAMES.index("Glass")] = 0.9
        self.mon.add_source(MicSource("mac", "Mac"))
        for _ in range(4):
            self.mon.feed("mac", np.full(RATE // 2, 0.1, np.float32))
        for _ in range(50):
            if self.events:
                break
            threading.Event().wait(0.05)
        self.assertEqual(self.events[0].label, "sound:glass")
        self.assertTrue(self.mon.status()[0]["live"])


class FakeProc:
    def __init__(self, data: bytes):
        self.stdout = io.BytesIO(data)
        self.stderr = io.BytesIO(b"")
        self.terminated = False

    def terminate(self):
        self.terminated = True


class NodeMicTest(unittest.TestCase):
    def test_shared_recording(self):
        procs = []

        def popen(cmd, **kw):
            self.assertIn("plughw:CARD=Device", cmd)
            procs.append(FakeProc(b"\x01\x00" * 16000 * 3))
            return procs[-1]

        mic = Microphone("Device", popen=popen)
        wav = mic.record(1.0)
        with wave.open(io.BytesIO(wav)) as w:
            self.assertEqual((w.getframerate(), w.getnchannels(), w.getnframes()), (16000, 1, 16000))
        self.assertEqual(mic.listeners(), 0, "a fine registrazione nessuno ascolta più")
        self.assertTrue(procs[0].terminated or procs[0].stdout.tell() > 0)

    def test_stream_ends_with_error_when_unplugged(self):
        mic = Microphone("", popen=lambda cmd, **kw: FakeProc(b"\x00\x00" * 100))
        got = b""
        with self.assertRaises(MicError):
            for chunk in mic.stream():
                got += chunk
        self.assertEqual(len(got), 200)


class NodeStreamTest(unittest.TestCase):
    def test_stream_from_simulator(self):
        with tempfile.TemporaryDirectory() as d:
            srv = SimulatorServer("orto", cert_dir=Path(d)).start()
            try:
                pem, _fp = fetch_certificate(srv.url)
                c = NodeClient(NodeConfig("orto", "Orto", srv.url), pem, srv.key_hex)
                data = b"".join(c.stream_audio())
                self.assertEqual(len(data), 64000, "2 s di audio a 16 kHz, 16 bit")
            finally:
                srv.stop()


class FakeNotifier:
    def __init__(self):
        self.events, self.texts = [], []

    def notify_event(self, ev, extra=None, force=False):
        self.events.append((ev.label, force, [b[0] for b in (extra or [])]))

    def notify_text(self, text):
        self.texts.append(text)


class CoreSoundTest(unittest.TestCase):
    def test_handle_sound_event(self):
        from mirto.core import MirtoCore
        from mirto.events import Event

        with tempfile.TemporaryDirectory() as d:
            os.environ["MIRTO_DATA_DIR"] = d
            cfg = Config(Path(d) / "c.yaml")
            cfg.set("actions.items", [
                {"name": "Sirena", "node": "n", "action": "sirena", "kind": "siren", "mode": "confirm",
                 "targets": ["person", "sound:glass"]},
                {"name": "Corvi", "node": "n", "action": "dissuasore", "kind": "deterrent", "targets": ["sound:bird"]},
            ])
            core = MirtoCore(cfg, mem_secrets(), detector=object(), discover=list)
            core.notifier = FakeNotifier()
            calls = []
            client = types.SimpleNamespace(activate=lambda *a, **k: calls.append(a[0]))
            core.actions = ActionEngine(core, client_factory=lambda nd, s: client)
            core.actions.clients = {"n": client}
            core.actions._pool = types.SimpleNamespace(submit=lambda fn, *a: fn(*a), shutdown=lambda **k: None)
            img = np.zeros((4, 4, 3), np.uint8)
            try:
                core.handle_sound_event(Event("mic:mac", "Mac", 0.9, img, label="sound:smoke_alarm"))
                self.assertEqual(core.notifier.events[-1][:2], ("sound:smoke_alarm", True),
                                 "allarme fumo: avviso anche a protezione disattivata")
                core.actions.set_armed(True)
                core.handle_sound_event(Event("mic:mac", "Mac", 0.9, img, label="sound:glass"))
                self.assertEqual(core.notifier.events[-1], ("sound:glass", False, ["Attiva Sirena"]),
                                 "vetro rotto: avviso con il pulsante della sirena")
                core.handle_sound_event(Event("mic:mac", "Mac", 0.9, img, label="sound:bird"))
                self.assertEqual(calls, ["dissuasore"], "versi di uccelli: solo il dissuasore, nessun avviso")
                self.assertNotIn("sound:bird", [e[0] for e in core.notifier.events])
            finally:
                core.actions.stop()
                core.store.close()


if __name__ == "__main__":
    unittest.main()


class ReviewFixesAudioTest(unittest.TestCase):
    def test_always_notify_is_listened_even_if_not_watched(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Config(Path(d) / "c.yaml")
            cfg.set("audio.watch", [])
            mon = AudioMonitor(cfg, lambda: None, lambda e: None)
            self.assertIn("smoke_alarm", mon.watched())

    def test_classifier_failure_backs_off(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Config(Path(d) / "c.yaml")
            calls, now = [], [0.0]

            def factory():
                calls.append(1)
                raise RuntimeError("niente rete")
            mon = AudioMonitor(cfg, factory, lambda e: None, clock=lambda: now[0])
            src, hist = MicSource("mac", "Mac"), {}
            for _ in range(5):
                mon.analyze(src, np.full(WINDOW, 0.2, np.float32), hist)
            self.assertEqual(len(calls), 1, "un solo tentativo, poi si aspetta")
            now[0] += 1801
            mon.analyze(src, np.full(WINDOW, 0.2, np.float32), hist)
            self.assertEqual(len(calls), 2)

    def test_mic_end_never_blocks_on_full_queue(self):
        import queue as q
        mic = Microphone("", popen=lambda cmd, **kw: FakeProc(b""))
        lst = mic.subscribe()
        for _ in range(lst.maxsize - lst.qsize()):
            lst.put_nowait(b"x")
        done = threading.Event()
        threading.Thread(target=lambda: (mic._pump(FakeProc(b"")), done.set()), daemon=True).start()
        self.assertTrue(done.wait(2), "il microfono non si blocca su una coda piena")
        self.assertIsInstance(lst, q.Queue)

    def test_sound_ignored_while_node_sounds_and_when_muted(self):
        from mirto.core import MirtoCore
        from mirto.events import Event

        with tempfile.TemporaryDirectory() as d:
            os.environ["MIRTO_DATA_DIR"] = d
            cfg = Config(Path(d) / "c.yaml")
            core = MirtoCore(cfg, mem_secrets(), detector=object(), discover=list)
            core.notifier = FakeNotifier()
            core.actions = types.SimpleNamespace(recently_active=lambda: True, specs=[],
                                                 confirmation_buttons=lambda e: [], on_detection=lambda e: [])
            img = np.zeros((4, 4, 3), np.uint8)
            try:
                core.handle_sound_event(Event("c", "Mac", 0.9, img, label="sound:glass"))
                self.assertEqual(core.notifier.events, [], "un nodo sta suonando: il suono è suo")
                core.handle_sound_event(Event("c", "Mac", 0.9, img, label="sound:smoke_alarm"))
                self.assertEqual(len(core.notifier.events), 1, "l'allarme fumo passa sempre")
                core.actions.recently_active = lambda: False
                core.camera_paused["c"] = time.time() + 60
                core.handle_sound_event(Event("c", "Mac", 0.9, img, label="sound:glass"))
                self.assertEqual(len(core.notifier.events), 1, "camera silenziata: nessun avviso")
            finally:
                core.store.close()
