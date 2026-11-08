# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
import tempfile
import time
import unittest
from pathlib import Path

import cv2
import numpy as np

from mirto.cameras.discovery import CameraInfo
from mirto.cameras.manager import CameraManager
from mirto.cameras.source import STATUS_ONLINE, CameraSource
from mirto.config import Config


class FakeCapture:
    def __init__(self, frames=50, fail_open=False):
        self.left = frames
        self.opened = not fail_open

    def isOpened(self):
        return self.opened

    def read(self):
        time.sleep(0.005)
        if self.left <= 0:
            return False, None
        self.left -= 1
        return True, np.full((48, 64, 3), 100, np.uint8)

    def release(self):
        self.opened = False


def wait_until(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class SourceTest(unittest.TestCase):
    def test_reads_and_reconnects(self):
        opens = []

        def factory(info):
            opens.append(info.index)
            return FakeCapture(frames=20)

        src = CameraSource(CameraInfo("a", "A", index=0), capture_factory=factory, read_timeout=0.2, max_backoff=0.1)
        src.start()
        try:
            self.assertTrue(wait_until(lambda: src.latest()[2] > 5))
            self.assertTrue(wait_until(lambda: len(opens) >= 2), "deve riaprire dopo la perdita del segnale")
        finally:
            src.stop()

    def test_update_info_reopens_with_new_index(self):
        opens = []
        src = CameraSource(
            CameraInfo("a", "A", index=0),
            capture_factory=lambda i: (opens.append(i.index), FakeCapture(frames=10_000))[1],
        )
        src.start()
        try:
            self.assertTrue(wait_until(lambda: src.status == STATUS_ONLINE))
            src.update_info(CameraInfo("a", "A", index=3))
            self.assertTrue(wait_until(lambda: 3 in opens))
        finally:
            src.stop()

    def test_network_source_from_video_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "clip.avi")
            vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"MJPG"), 10, (64, 48))
            for i in range(30):
                vw.write(np.full((48, 64, 3), i * 5, np.uint8))
            vw.release()
            src = CameraSource(CameraInfo("net:x", "File", kind="network", url=path))
            src.start()
            try:
                self.assertTrue(wait_until(lambda: src.latest()[2] > 3))
            finally:
                src.stop()


class ManagerTest(unittest.TestCase):
    def test_hotplug_and_overrides(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Config(Path(d) / "c.yaml")
            cfg.set("cameras.overrides", {"b": {"enabled": False}, "a": {"name": "Ingresso"}})
            lists = [
                [CameraInfo("a", "Cam A", index=0), CameraInfo("b", "Cam B", index=1)],
                [CameraInfo("b", "Cam B", index=0)],
                [CameraInfo("b", "Cam B", index=0)],
            ]
            mgr = CameraManager(cfg, discover=lambda: lists.pop(0), capture_factory=lambda i: FakeCapture(10_000))
            added, removed = [], []
            mgr.on_source_added.append(lambda s: added.append((s.info.id, s.info.name)))
            mgr.on_source_removed.append(removed.append)
            mgr.start(threaded_discovery=False)
            self.assertEqual(added, [("a", "Ingresso")])  # b è disattivata
            mgr.scan()
            self.assertEqual(removed, [], "serve che manchi in due scansioni di fila (W-03)")
            mgr.scan()
            self.assertEqual(removed, ["a"])
            mgr.stop()

    def test_all_cameras_enable_disable_rename(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Config(Path(d) / "c.yaml")
            cfg.set("cameras.network", [{"id": "giardino", "name": "Giardino", "url": "rtsp://x/1"}])
            present = [CameraInfo("a", "FaceTime HD Camera", index=0), CameraInfo("b", "Camera di iPhone", index=1)]
            mgr = CameraManager(cfg, discover=lambda: list(present), capture_factory=lambda i: FakeCapture(10_000))
            added, removed = [], []
            mgr.on_source_added.append(lambda s: added.append(s.info.id))
            mgr.on_source_removed.append(removed.append)
            mgr.start(threaded_discovery=False)
            try:
                cams = {c["id"]: c for c in mgr.all_cameras()}
                self.assertEqual(set(cams), {"a", "b", "net:giardino"})
                self.assertEqual(cams["a"]["kind"], "Integrata")
                self.assertEqual(cams["b"]["kind"], "iPhone")
                self.assertEqual(cams["net:giardino"]["kind"], "Di rete")
                kl = CameraManager.kind_label
                self.assertEqual(kl(CameraInfo("x", "Fotocamera di MacBook Air")), "Integrata")
                self.assertEqual(kl(CameraInfo("x", "Logitech C920")), "USB / esterna")
                self.assertEqual(kl(CameraInfo("x", "C920", extra={"device_type": "AVCaptureDeviceTypeExternal"})),
                                 "USB / esterna")
                self.assertEqual(kl(CameraInfo("x", "Cam", extra={"device_type": "AVCaptureDeviceTypeContinuityCamera"})),
                                 "iPhone")
                self.assertTrue(all(c["enabled"] and c["connected"] for c in cams.values()))

                # spegni l'iPhone: la sorgente si chiude e la scelta resta salvata
                mgr.set_enabled("b", False)
                self.assertIn("b", removed)
                self.assertNotIn("b", mgr.sources)
                self.assertFalse(Config(Path(d) / "c.yaml").get("cameras.overrides")["b"]["enabled"])
                self.assertFalse({c["id"]: c for c in mgr.all_cameras()}["b"]["enabled"])

                # scollegata: resta nell'elenco come "non collegata" (dopo due scansioni, W-03)
                present.pop()
                mgr.scan()
                mgr.scan()
                b = {c["id"]: c for c in mgr.all_cameras()}["b"]
                self.assertFalse(b["connected"])
                self.assertEqual(b["name"], "Camera di iPhone")

                # ricollegata e riaccesa: torna attiva
                present.append(CameraInfo("b", "Camera di iPhone", index=1))
                mgr.scan()
                self.assertNotIn("b", mgr.sources)
                mgr.set_enabled("b", True)
                self.assertIn("b", mgr.sources)

                # rinomina senza riaprire la camera
                mgr.rename("a", "Soggiorno")
                self.assertEqual(mgr.sources["a"].info.name, "Soggiorno")
                self.assertEqual({c["id"]: c for c in mgr.all_cameras()}["a"]["name"], "Soggiorno")
                mgr.rename("a", "")
                self.assertEqual(mgr.sources["a"].info.name, "FaceTime HD Camera")
            finally:
                mgr.stop()


if __name__ == "__main__":
    unittest.main()


class NonBlockingStopTest(unittest.TestCase):
    """Q-02: fermare una camera non deve congelare la finestra (una RTSP irraggiungibile ci mette secondi)."""

    class SlowSource:
        """Camera che ci mette un secondo a staccarsi, come una RTSP sparita dalla rete."""

        def __init__(self, cam_id="a"):
            import threading

            self.info = CameraInfo(cam_id, "Cam lenta", index=0)
            self.asked = threading.Event()
            self.joined = threading.Event()

        def request_stop(self):
            self.asked.set()

        def join(self, timeout=5.0):
            time.sleep(1.0)
            self.joined.set()

        def stop(self):
            self.request_stop()
            self.join()

    def test_disabling_a_stuck_camera_returns_at_once(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Config(Path(d) / "c.yaml")
            mgr = CameraManager(cfg, discover=lambda: [], capture_factory=lambda i: FakeCapture(10_000))
            src = self.SlowSource()
            mgr.sources["a"] = src
            tolte = []
            mgr.on_source_removed.append(tolte.append)

            t0 = time.monotonic()
            mgr.set_enabled("a", False)
            passati = time.monotonic() - t0

            self.assertLess(passati, 0.1, "l'interfaccia non aspetta il thread della camera")
            self.assertTrue(src.asked.is_set(), "lo stop è stato chiesto subito")
            self.assertEqual(tolte, ["a"], "chi guarda sa già che la camera non c'è più")
            self.assertNotIn("a", mgr.sources)
            self.assertTrue(src.joined.wait(5), "lo spazzino aspetta davvero che il thread finisca")
            mgr.stop()

    def test_the_reaper_works_before_and_after_the_app_closes(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Config(Path(d) / "c.yaml")
            mgr = CameraManager(cfg, discover=lambda: [], capture_factory=lambda i: FakeCapture(10))
            atteso = []
            mgr.reap(lambda: atteso.append(time.monotonic()))
            self.assertTrue(wait_until(lambda: atteso), "lo spazzino esegue le attese che gli passano")
            mgr.stop()

            # a finestra chiusa lo spazzino è spento: l'attesa si fa comunque, qui
            fatto = []
            mgr.reap(lambda: fatto.append(True))
            self.assertTrue(wait_until(lambda: fatto))

    def test_closing_the_app_really_ends_the_camera_threads(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Config(Path(d) / "c.yaml")
            mgr = CameraManager(cfg, discover=lambda: [CameraInfo("a", "Cam A", index=0)],
                                capture_factory=lambda i: FakeCapture(10_000))
            mgr.start(threaded_discovery=False)
            src = mgr.sources["a"]
            self.assertTrue(wait_until(lambda: src.status == STATUS_ONLINE))
            mgr.stop()
            self.assertFalse(src._thread and src._thread.is_alive(), "alla chiusura il thread è davvero finito")
