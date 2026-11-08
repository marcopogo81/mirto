# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
import tempfile
import unittest
from pathlib import Path

from mirto.cameras.manager import CameraManager
from mirto.cameras.network_discovery import (
    SERVICE,
    FoundCamera,
    NetworkCameraBrowser,
    matches,
    parse_service_info,
)
from mirto.cameras import network_discovery
from mirto.config import Config

from tests import senza_rete  # noqa: E402

_vere_rete = None


def setUpModule():
    """La rete vera resta fuori da questo file (vedi tests/senza_rete.py)."""
    global _vere_rete
    _vere_rete = senza_rete.attiva()


def tearDownModule():
    senza_rete.disattiva(_vere_rete)

network_discovery.PROBE_TIMEOUT = 0.05  # niente attese lunghe verso indirizzi finti

PI = "Mirto Cam mirto-ingresso." + SERVICE


class FakeInfo:
    def __init__(self, addrs, port=8554, props=None, server="mirto-ingresso.local."):
        self._addrs = addrs
        self.port = port
        self.properties = props if props is not None else {b"path": b"/cam", b"mirto": b"1"}
        self.server = server

    def parsed_addresses(self):
        return list(self._addrs)


class FakeBrowser(NetworkCameraBrowser):
    """Browser senza rete: gli annunci si simulano con ``announce``."""

    def start(self):
        return True

    def announce(self, service, info):
        self._store(service, info)


class FakeCapture:
    def __init__(self, url):
        self.url = url

    def isOpened(self):
        return False

    def release(self):
        pass


class ParseTest(unittest.TestCase):
    def test_parse_mirto_camera(self):
        cam = parse_service_info(PI, FakeInfo(["192.168.2.20", "fe80::1"]))
        self.assertEqual(cam.name, "Mirto Cam mirto-ingresso")
        self.assertEqual(cam.addresses, ("192.168.2.20",))  # IPv6 scartato
        self.assertEqual(cam.url(), "rtsp://192.168.2.20:8554/cam")
        self.assertTrue(cam.mirto)
        self.assertEqual(cam.suggested_name, "Ingresso")

    def test_parse_generic_camera(self):
        cam = parse_service_info("Camera giardino." + SERVICE,
                                 FakeInfo(["10.0.0.5"], port=554, props={b"path": b"stream1"}, server="cam.local."))
        self.assertEqual(cam.url(), "rtsp://10.0.0.5:554/stream1")
        self.assertFalse(cam.mirto)
        self.assertEqual(cam.suggested_name, "Camera giardino")
        self.assertIsNone(parse_service_info("x." + SERVICE, None))

    def test_matches(self):
        cam = parse_service_info(PI, FakeInfo(["192.168.2.20", "192.168.1.40"]))
        self.assertTrue(matches({"url": "rtsp://192.168.2.20:8554/cam"}, cam))
        self.assertTrue(matches({"url": "rtsp://192.168.1.40:8554/cam"}, cam))
        self.assertTrue(matches({"url": "rtsp://mirto-ingresso.local:8554/cam"}, cam))
        self.assertTrue(matches({"url": "rtsp://u:p@192.168.2.20:8554/cam"}, cam))
        self.assertFalse(matches({"url": "rtsp://192.168.2.20:8554/altro"}, cam))
        self.assertFalse(matches({"url": "rtsp://192.168.2.21:8554/cam"}, cam))
        self.assertFalse(matches({"url": "http://192.168.2.20:8554/cam"}, cam))
        self.assertTrue(matches({"url": "rtsp://1.2.3.4/x", "mdns": PI}, cam))

    def test_resolve_keeps_credentials(self):
        b = FakeBrowser()
        b.announce(PI, FakeInfo(["192.168.1.40"]))
        self.assertEqual(b.resolve_url(PI, "rtsp://utente:segreta@192.168.2.20:8554/cam"),
                         "rtsp://utente:segreta@192.168.1.40:8554/cam")
        self.assertEqual(b.resolve_url("sconosciuta." + SERVICE, "rtsp://x/1"), "rtsp://x/1")


class RenamedServiceTest(unittest.TestCase):
    def test_renamed_announcement_is_same_camera(self):
        renamed = "Mirto Cam mirto-ingresso #2." + SERVICE
        self.assertEqual(network_discovery.base_service(renamed), network_discovery.base_service(PI))
        cam = parse_service_info(renamed, FakeInfo(["192.168.1.77"]))
        self.assertTrue(matches({"url": "rtsp://192.168.2.20:8554/cam", "mdns": PI}, cam))
        b = FakeBrowser()
        b.announce(renamed, FakeInfo(["192.168.1.77"]))
        self.assertEqual(b.get(PI).service, renamed)
        self.assertEqual(b.resolve_url(PI, "rtsp://192.168.2.20:8554/cam"), "rtsp://192.168.1.77:8554/cam")


class ManagerTest(unittest.TestCase):
    def make(self, d, nets=None):
        cfg = Config(Path(d) / "config.yaml")
        cfg.set("cameras.auto_discover", False)
        cfg.set("cameras.network", nets or [])
        browser = FakeBrowser()
        opened = []

        def factory(info):
            opened.append(info.url)
            return FakeCapture(info.url)
        mgr = CameraManager(cfg, discover=lambda: [], capture_factory=factory, network_browser=browser)
        return cfg, mgr, browser, opened

    def test_found_added_and_followed_on_ip_change(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, mgr, browser, opened = self.make(d)
            mgr.start(threaded_discovery=False)
            try:
                self.assertEqual(mgr.discovered_network_cameras(), [])
                browser.announce(PI, FakeInfo(["192.168.2.20"]))
                found = mgr.discovered_network_cameras()
                self.assertEqual(len(found), 1)
                self.assertEqual(found[0]["suggested"], "Ingresso")
                cam_id = mgr.add_discovered(PI, "")
                self.assertEqual(cam_id, "net:ingresso")
                net = cfg.get("cameras.network")[0]
                self.assertEqual(net, {"id": "ingresso", "name": "Ingresso",
                                       "url": "rtsp://192.168.2.20:8554/cam", "mdns": PI})
                self.assertEqual(mgr.discovered_network_cameras(), [])  # già aggiunta
                self.assertEqual(mgr.add_discovered(PI, "Altro"), "net:ingresso")  # niente doppioni
                # il router assegna un altro indirizzo: alla riapertura Mirto lo segue
                browser.announce(PI, FakeInfo(["192.168.1.40"]))
                mgr._open(mgr.sources[cam_id].info)
                self.assertEqual(opened[-1], "rtsp://192.168.1.40:8554/cam")
                self.assertEqual(cfg.get("cameras.network")[0]["url"], "rtsp://192.168.1.40:8554/cam")
            finally:
                mgr.stop()

    def test_hide_and_unhide(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, mgr, browser, opened = self.make(d)
            browser.announce(PI, FakeInfo(["192.168.2.20"]))
            browser.announce("Mirto Cam mirto-ingresso #2." + SERVICE, FakeInfo(["192.168.1.77"]))
            self.assertEqual(len(mgr.discovered_network_cameras()), 1)
            mgr.hide_discovered(PI)
            self.assertEqual(mgr.discovered_network_cameras(), [])  # nascosti anche i doppioni "#2"
            self.assertEqual(mgr.unhide_discovered(), 1)
            self.assertEqual(len(mgr.discovered_network_cameras()), 1)

    def test_manual_camera_is_linked(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, mgr, browser, opened = self.make(
                d, [{"id": "ingresso", "name": "Ingresso", "url": "rtsp://192.168.2.20:8554/cam"}])
            mgr.start(threaded_discovery=False)
            try:
                browser.announce(PI, FakeInfo(["192.168.2.20"]))
                self.assertEqual(mgr.discovered_network_cameras(), [])  # è quella già aggiunta a mano
                self.assertEqual(cfg.get("cameras.network")[0]["mdns"], PI)
                self.assertEqual(mgr.sources["net:ingresso"].info.extra.get("mdns"), PI)
                self.assertEqual(Config(cfg.path).get("cameras.network")[0]["mdns"], PI)  # salvato
            finally:
                mgr.stop()

    def test_without_browser(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Config(Path(d) / "config.yaml")
            cfg.set("cameras.network_discover", False)
            mgr = CameraManager(cfg, discover=lambda: [], capture_factory=FakeCapture)
            mgr.start(threaded_discovery=False)
            try:
                self.assertIsNone(mgr.network_browser)
                self.assertEqual(mgr.discovered_network_cameras(), [])
                self.assertIsNone(mgr.add_discovered(PI))
            finally:
                mgr.stop()


class PickAddressTest(unittest.TestCase):
    def test_skips_unreachable_address(self):
        import socket
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(64)
        port = srv.getsockname()[1]
        try:
            # 192.0.2.1 non risponde (indirizzo di documentazione): si sceglie quello vivo
            self.assertEqual(network_discovery.pick_address(["192.0.2.1", "127.0.0.1"], port), "127.0.0.1")
            # nessuno risponde: ultima risorsa il primo
            self.assertEqual(network_discovery.pick_address(["192.0.2.1"], port + 1 if port < 65535 else 1),
                             "192.0.2.1")
            self.assertIsNone(network_discovery.pick_address([], port))
        finally:
            srv.close()


class RefreshTest(unittest.TestCase):
    def test_live_address_goes_first(self):
        import socket
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(64)
        port = srv.getsockname()[1]
        try:
            b = FakeBrowser()
            # vecchio indirizzo del cavo ancora in memoria + quello vivo
            b.announce(PI, FakeInfo(["192.0.2.1", "127.0.0.1"], port=port))
            self.assertEqual(b.get(PI).addresses[0], "127.0.0.1")
            # solo l'indirizzo vecchio memorizzato, ma il nome in rete risponde
            b._found[PI] = FoundCamera(service=PI, name="x", host="localhost", addresses=("192.0.2.1",),
                                       port=port, path="/cam")
            b.refresh()  # senza zeroconf: riordina con le prove di rete
            self.assertEqual(b.get(PI).url(), f"rtsp://127.0.0.1:{port}/cam")
        finally:
            srv.close()


class TwinsTest(unittest.TestCase):
    def test_live_twin_preferred(self):
        import socket
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(64)
        port = srv.getsockname()[1]
        try:
            b = FakeBrowser()
            twin = "Mirto Cam mirto-ingresso #2." + SERVICE
            b.announce(PI, FakeInfo(["192.0.2.1"], port=port, server="nessuno.invalid."))  # cavo staccato
            b.announce(twin, FakeInfo(["127.0.0.1"], port=port, server="nessuno.invalid."))  # Wi-Fi
            self.assertFalse(b._found[PI].alive)
            self.assertEqual(b.found()[0].service, twin)  # prima quella viva
            self.assertEqual(b.resolve_url(PI, "rtsp://192.0.2.1/cam"), f"rtsp://127.0.0.1:{port}/cam")
            with tempfile.TemporaryDirectory() as d:
                cfg = Config(Path(d) / "config.yaml")
                cfg.set("cameras.auto_discover", False)
                mgr = CameraManager(cfg, discover=lambda: [], capture_factory=FakeCapture, network_browser=b)
                found = mgr.discovered_network_cameras()
                self.assertEqual(len(found), 1)  # un solo dispositivo
                self.assertEqual(found[0]["url"], f"rtsp://127.0.0.1:{port}/cam")
        finally:
            srv.close()


class BothAnswerTest(unittest.TestCase):
    """Due indirizzi che rispondono: il secondo finisce nel dettaglio della camera.

    Qui gli indirizzi non si provano davvero: 127.0.0.2 è un indirizzo locale su
    Linux ma non su macOS, dove non risponderebbe nessuno. Quello che conta è che
    due indirizzi vivi arrivino fino alla scheda della camera; come si scopre che
    rispondono lo provano PickAddressTest e KnownAliveTest.
    """

    def test_both_addresses_reported(self):
        port = 8554
        vivi = ("127.0.0.1", "127.0.0.2")
        b = FakeBrowser()
        b.known_alive = lambda indirizzo, porta: indirizzo in vivi and porta == port
        b.announce(PI, FakeInfo(list(vivi), port=port, server="nessuno.invalid."))
        cam = b.get(PI)
        self.assertEqual(cam.live, vivi)
        with tempfile.TemporaryDirectory() as d:
            cfg = Config(Path(d) / "config.yaml")
            cfg.set("cameras.auto_discover", False)
            mgr = CameraManager(cfg, discover=lambda: [], capture_factory=FakeCapture, network_browser=b)
            self.assertEqual(mgr.discovered_network_cameras()[0]["also"], ["127.0.0.2"])
            cfg.set("cameras.network", [{"id": "ingresso", "name": "Ingresso",
                                         "url": f"rtsp://127.0.0.1:{port}/cam", "mdns": PI}])
            cam = {c["id"]: c for c in mgr.all_cameras()}["net:ingresso"]
            self.assertEqual(cam["kind"], "Di rete")
            self.assertEqual(cam["detail"], "127.0.0.1 · risponde anche su 127.0.0.2")


class SenzaReteTest(unittest.TestCase):
    """La rete finta è davvero attiva: se salta, i test tornano a dipendere dal Wi-Fi."""

    def test_nothing_answers_outside_this_machine(self):
        from mirto.cameras import network_discovery as nd

        import time
        inizio = time.perf_counter()
        self.assertFalse(nd.reachable("192.0.2.1", 8554))      # TEST-NET-1: non esiste per definizione
        self.assertEqual(nd.risolvi("nessuno-qui.local", 8554), [])
        self.assertLess(time.perf_counter() - inizio, 0.2,
                        "ha provato davvero: su macOS questa riga costerebbe cinque secondi")

    def test_the_loopback_still_works(self):
        import socket

        from mirto.cameras import network_discovery as nd

        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        self.addCleanup(srv.close)
        self.assertTrue(nd.reachable("127.0.0.1", srv.getsockname()[1]),
                        "le prove con un ascoltatore vero su 127.0.0.1 devono continuare a funzionare")


class WatchTest(unittest.TestCase):
    def test_fast_mode_and_refresh_now(self):
        import threading
        import time
        b = FakeBrowser()
        th = threading.Thread(target=b._refresh_loop, daemon=True)
        th.start()
        try:
            b.set_interval(100)
            self.assertEqual(b.last_refresh, 0.0)
            b.refresh_now()
            end = time.time() + 3
            while not b.last_refresh and time.time() < end:
                time.sleep(0.01)
            self.assertGreater(b.last_refresh, 0)  # controllo fatto subito, senza aspettare 100 s
            self.assertFalse(b.refreshing)
            with tempfile.TemporaryDirectory() as d:
                cfg = Config(Path(d) / "config.yaml")
                mgr = CameraManager(cfg, discover=lambda: [], capture_factory=FakeCapture, network_browser=b)
                mgr.network_watch(True)
                self.assertEqual(b.interval, network_discovery.FAST_REFRESH_SECONDS)
                mgr.network_watch(False)
                self.assertEqual(b.interval, network_discovery.REFRESH_SECONDS)
                self.assertIsNone(mgr.network_watch_state())  # browser finto: non "running"
        finally:
            b.stop()
            th.join(timeout=2)
        self.assertFalse(th.is_alive())


class KnownAliveTest(unittest.TestCase):
    def test_no_probe_when_already_streaming(self):
        calls = []
        orig = network_discovery.reachable
        network_discovery.reachable = lambda a, p, timeout=None: calls.append(a) or False
        try:
            live = network_discovery.live_addresses(["192.0.2.1", "192.0.2.2"], 8554,
                                                    known_alive=lambda a, p: a == "192.0.2.1")
            self.assertEqual(live, ("192.0.2.1",))
            self.assertEqual(calls, ["192.0.2.2"])  # quello in uso non viene "bussato"
        finally:
            network_discovery.reachable = orig

    def test_manager_streaming_from(self):
        import time as _t
        with tempfile.TemporaryDirectory() as d:
            cfg = Config(Path(d) / "config.yaml")
            cfg.set("cameras.auto_discover", False)
            cfg.set("cameras.network", [{"id": "ingresso", "name": "Ingresso", "url": "rtsp://192.0.2.1:8554/cam"}])
            b = FakeBrowser()
            mgr = CameraManager(cfg, discover=lambda: [], capture_factory=FakeCapture, network_browser=b)
            mgr.start(threaded_discovery=False)
            try:
                self.assertIs(b.known_alive.__func__, CameraManager._streaming_from)
                src = mgr.sources["net:ingresso"]
                self.assertFalse(mgr._streaming_from("192.0.2.1", 8554))  # nessun fotogramma
                src._ts = _t.time()
                self.assertTrue(mgr._streaming_from("192.0.2.1", 8554))
                self.assertFalse(mgr._streaming_from("192.0.2.1", 554))
            finally:
                mgr.stop()


class FoundCameraTest(unittest.TestCase):
    def test_url_without_address_uses_host(self):
        cam = FoundCamera(service=PI, name="x", host="mirto-ingresso.local", addresses=(), port=8554, path="/cam")
        self.assertEqual(cam.url(), "rtsp://mirto-ingresso.local:8554/cam")


if __name__ == "__main__":
    unittest.main()
