# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Test di movimento e riconoscimento persone.

I test con il modello reale richiedono:
  MIRTO_MODELS_DIR  cartella con yolox_nano.onnx
  MIRTO_TEST_IMAGES cartella con un'immagine con persone (sunjian.png) e una senza (dog.jpg),
                      ad es. gli esempi del repository YOLOX (cartella assets).
Altrimenti vengono saltati.
"""

import os
import tempfile
import time
import unittest
from pathlib import Path

import cv2
import numpy as np

from mirto.cameras.discovery import CameraInfo
from mirto.config import Config
from mirto.detection.motion import MotionDetector, brightness
from mirto.events import Event, EventStore

MODELS = os.environ.get("MIRTO_MODELS_DIR", "")
IMAGES = os.environ.get("MIRTO_TEST_IMAGES", "")
HAVE_MODEL = bool(MODELS) and Path(MODELS, "yolox_nano.onnx").exists()
HAVE_IMAGES = bool(IMAGES) and Path(IMAGES, "sunjian.png").exists() and Path(IMAGES, "dog.jpg").exists()


def background():
    rng = np.random.default_rng(0)
    bg = rng.integers(60, 120, (480, 640, 3), dtype=np.uint8)
    return cv2.GaussianBlur(bg, (21, 21), 0)


def paste(bg, img):
    out = bg.copy()
    small = cv2.resize(img, (300, 300))
    out[90:390, 170:470] = small
    return out


class MotionTest(unittest.TestCase):
    def test_static_then_change(self):
        md = MotionDetector(min_area=0.01)
        bg = background()
        results = [md.update(bg) for _ in range(30)]
        self.assertFalse(any(results))
        changed = bg.copy()
        cv2.rectangle(changed, (200, 100), (400, 400), (255, 255, 255), -1)
        self.assertTrue(md.update(changed))

    def test_ignore_zone(self):
        md = MotionDetector(min_area=0.01, ignore_zones=[[0.0, 0.0, 1.0, 1.0]])
        bg = background()
        for _ in range(30):
            md.update(bg)
        changed = bg.copy()
        cv2.rectangle(changed, (200, 100), (400, 400), (255, 255, 255), -1)
        self.assertFalse(md.update(changed))

    def test_brightness(self):
        self.assertLess(brightness(np.zeros((10, 10, 3), np.uint8)), 1)


class EventStoreTest(unittest.TestCase):
    def test_save_purge(self):
        with tempfile.TemporaryDirectory() as d:
            store = EventStore(Path(d), retention_days=1)
            old = Event("c", "Cam", 0.8, np.zeros((20, 20, 3), np.uint8), ts=time.time() - 3 * 86400)
            new = Event("c", "Cam", 0.7, np.zeros((20, 20, 3), np.uint8))
            store.save(old); store.save(new)
            store.update(new.id, telegram_link="https://t.me/c/1/2")
            self.assertEqual(store.get(new.id)["telegram_link"], "https://t.me/c/1/2")
            self.assertEqual(store.purge(), 1)
            self.assertFalse(Path(old.image_path).exists())
            self.assertEqual([e["id"] for e in store.recent()], [new.id])
            store.close()

    def test_delete(self):
        with tempfile.TemporaryDirectory() as d:
            store = EventStore(Path(d))
            evs = [store.save(Event("c", "Cam", 0.5, np.zeros((20, 20, 3), np.uint8))) for _ in range(3)]
            self.assertEqual(store.delete([evs[0].id]), 1)
            self.assertFalse(Path(evs[0].image_path).exists())
            self.assertEqual(len(store.recent()), 2)
            self.assertEqual(store.delete_all(), 2)
            self.assertEqual(store.recent(), [])
            store.close()

    def test_the_last_time_each_camera_saw_something(self):
        """Serve alla vista di flotta per dire da quanto un nodo non vede niente.

        Una domanda sola per tutte le camere, non una per camera: con otto nodi sarebbero otto
        letture a ogni aggiornamento della schermata, ogni due secondi.
        """
        with tempfile.TemporaryDirectory() as d:
            store = EventStore(Path(d))
            vuoto = np.zeros((20, 20, 3), np.uint8)
            store.save(Event("c1", "Ingresso", 0.5, vuoto, ts=1000.0))
            store.save(Event("c1", "Ingresso", 0.5, vuoto, ts=3000.0))
            store.save(Event("c2", "Garage", 0.5, vuoto, ts=2000.0))
            ultimi = store.ultimo_per_camera()
            self.assertEqual(ultimi["c1"], 3000.0, "di ogni camera conta l'ultima volta, non la prima")
            self.assertEqual(ultimi["c2"], 2000.0)
            self.assertNotIn("c3", ultimi, "una camera che non ha mai visto niente non compare")
            store.close()

    def test_label_and_old_archive_migration(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as d:
            db = sqlite3.connect(str(Path(d) / "events.sqlite"))  # archivio della versione precedente
            db.execute("""CREATE TABLE events (id TEXT PRIMARY KEY, ts REAL, camera_id TEXT, camera_name TEXT,
                score REAL, action_level INTEGER, image_path TEXT, telegram_link TEXT, false_alarm INTEGER DEFAULT 0)""")
            db.execute("INSERT INTO events VALUES ('old', 1, 'c', 'Cam', 0.9, 0, NULL, NULL, 0)")
            db.commit(); db.close()
            store = EventStore(Path(d))
            store.save(Event("c", "Cam", 0.6, np.zeros((20, 20, 3), np.uint8), label="car"))
            labels = {e["id"]: e["label"] for e in store.recent()}
            self.assertEqual(labels["old"], "person")
            self.assertIn("car", labels.values())
            store.close()

    def test_jpeg_size(self):
        img = np.random.default_rng(1).integers(0, 255, (2000, 3000, 3), dtype=np.uint8)
        data = Event("c", "Cam", 0.5, img).jpeg(max_bytes=500_000)
        dec = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        self.assertLessEqual(max(dec.shape[:2]), 1920)


@unittest.skipUnless(HAVE_MODEL and HAVE_IMAGES, "modello o immagini di test non disponibili")
class PersonPipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from mirto.detection.person import PersonDetector
        cls.det = PersonDetector(Path(MODELS, "yolox_nano.onnx"), 416)
        cls.person = cv2.imread(str(Path(IMAGES, "sunjian.png")))
        cls.dog = cv2.imread(str(Path(IMAGES, "dog.jpg")))

    def test_detector(self):
        self.assertTrue(self.det.detect(self.person, threshold=0.4))
        self.assertFalse(self.det.detect(self.dog, threshold=0.4))

    def _run(self, intruder, frames=12, watch=None):
        from mirto.detection.pipeline import CameraPipeline

        with tempfile.TemporaryDirectory() as d:
            cfg = Config(Path(d) / "c.yaml")
            if watch:
                cfg.set("detection.watch", watch)
            src = type("S", (), {"info": CameraInfo("cam1", "Ingresso", index=0)})()
            events, actions = [], []
            t = [1000.0]
            pipe = CameraPipeline(src, self.det, cfg, events.append, actions.append, clock=lambda: t[0])
            bg = background()
            for _ in range(20):
                pipe.process(bg); t[0] += 0.2
            scene = paste(bg, intruder)
            for _ in range(frames):
                pipe.process(scene); t[0] += 0.2
            return events, actions

    def test_person_triggers_once_with_cooldown(self):
        events, actions = self._run(self.person)
        self.assertEqual(len(events), 1, "un solo evento per pausa anti-spam")
        self.assertEqual(events[0].camera_name, "Ingresso")
        self.assertGreaterEqual(events[0].score, 0.4)
        self.assertEqual(len(actions), 1)

    def test_no_person_no_event(self):
        events, actions = self._run(self.dog)
        self.assertEqual(events, [])
        self.assertEqual(actions, [])

    def test_detector_other_categories(self):
        from mirto.detection.labels import INDEX
        dets = self.det.detect(self.dog, threshold=0.4, classes=[INDEX["dog"], INDEX["bicycle"], INDEX["truck"]])
        labels = {d.label for d in dets}
        self.assertIn("dog", labels)
        self.assertTrue(labels & {"bicycle", "truck"})
        self.assertFalse(self.det.detect(self.dog, threshold=0.4, classes=[INDEX["cat"]]))

    def test_watched_animal_creates_labelled_event_without_actions(self):
        events, actions = self._run(self.dog, watch=["person", "dog"])
        self.assertEqual([e.label for e in events], ["dog"])
        self.assertEqual(events[0].headline(), "🐾 Rilevato: Cane")
        self.assertIn("rilevato: cane", events[0].title())
        self.assertEqual(actions, [], "le azioni (trappole) partono solo per le persone")

    def test_person_not_watched_no_event(self):
        events, actions = self._run(self.person, watch=["dog"])
        self.assertEqual(events, [])
        self.assertEqual(actions, [])


if __name__ == "__main__":
    unittest.main()


class ProviderPromiseTest(unittest.TestCase):
    """W-04: quello che il README promette e quello che si installa devono coincidere."""

    ROOT = Path(__file__).resolve().parents[1]

    def test_windows_installs_the_package_that_has_directml(self):
        # il provider DmlExecutionProvider esiste solo in onnxruntime-directml, non in onnxruntime
        for nome in ("requirements.txt", "pyproject.toml"):
            testo = (self.ROOT / nome).read_text(encoding="utf-8")
            righe = [r for r in testo.splitlines() if "onnxruntime" in r and not r.strip().startswith("#")]
            self.assertTrue(any("onnxruntime-directml" in r and "win32" in r for r in righe),
                            f"{nome}: manca onnxruntime-directml per Windows")
            self.assertTrue(any(r for r in righe
                                if "onnxruntime-directml" not in r and "onnxruntime" in r and "win32" in r),
                            f"{nome}: onnxruntime senza marcatore di piattaforma si scontra con directml")

    def test_preferred_providers_put_the_accelerators_first(self):
        from mirto.detection.person import _providers

        class FakeOrt:
            @staticmethod
            def get_available_providers():
                return ["CPUExecutionProvider", "DmlExecutionProvider"]

        import sys as _sys

        vecchio = _sys.modules.get("onnxruntime")
        _sys.modules["onnxruntime"] = FakeOrt
        try:
            self.assertEqual(_providers(), ["DmlExecutionProvider", "CPUExecutionProvider"])
        finally:
            if vecchio is not None:
                _sys.modules["onnxruntime"] = vecchio
            else:
                _sys.modules.pop("onnxruntime", None)


class InferenzaInParalleloTest(unittest.TestCase):
    """Q-03: con più camere le inferenze non si mettono in fila una dietro l'altra.

    Prima un `Lock` sul rilevatore ne lasciava passare **una sola**: con quattro camere la quarta
    aspettava le altre tre, e la cadenza promessa («due fotogrammi su cinque») per lei diventava
    secondi. Qui il modello è finto e lento apposta: quello che si misura è quante inferenze
    riescono a stare dentro insieme.
    """

    def rilevatore(self, run):
        """Un PersonDetector senza onnxruntime: sessione finta, il resto costruito a mano."""
        import threading

        from mirto.detection import person as P

        d = P.PersonDetector.__new__(P.PersonDetector)
        d.session = type("Sessione", (), {"run": staticmethod(run)})()
        d.input_name = "images"
        d.size = (64, 64)
        d._grid, d._strides = P.PersonDetector._make_grid(d)
        d._posti = threading.BoundedSemaphore(P.INFERENZE_IN_PARALLELO)
        return d

    def test_more_than_one_camera_at_a_time_gets_through(self):
        import threading

        from mirto.detection import person as P

        dentro = 0
        massimo = 0
        guardia = threading.Lock()

        def run(_out, _feed):
            nonlocal dentro, massimo
            with guardia:
                dentro += 1
                massimo = max(massimo, dentro)
            time.sleep(0.15)
            with guardia:
                dentro -= 1
            punti = sum((64 // s) ** 2 for s in (8, 16, 32))
            return [np.zeros((1, punti, 85), np.float32)]

        d = self.rilevatore(run)
        fotogramma = np.zeros((64, 64, 3), np.uint8)
        thread = [threading.Thread(target=d.detect, args=(fotogramma,)) for _ in range(4)]
        for th in thread:
            th.start()
        for th in thread:
            th.join(10)

        self.assertGreaterEqual(massimo, 2, "le inferenze passano ancora una alla volta")
        self.assertLessEqual(massimo, P.INFERENZE_IN_PARALLELO,
                             "il tetto serve a non prendersi tutta la macchina: va rispettato")

    def test_the_ceiling_is_at_least_two_even_on_a_small_machine(self):
        from mirto.detection.person import _inferenze_in_parallelo

        self.assertGreaterEqual(_inferenze_in_parallelo(), 2)

