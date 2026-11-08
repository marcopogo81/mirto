"""Camere dei nodi: create con il nodo, adottate se già presenti, tolte con il nodo."""
import tempfile
import types
import unittest
from pathlib import Path

from mirto.actions.node_cameras import (
    camera_credentials,
    camera_url,
    node_camera_url,
    remove_node_cameras,
    sync_node_cameras,
)
from mirto.credentials import rtsp_name

from tests import senza_rete  # noqa: E402

_vere_rete = None


def setUpModule():
    """La rete vera resta fuori da questo file (vedi tests/senza_rete.py)."""
    global _vere_rete
    _vere_rete = senza_rete.attiva()


def tearDownModule():
    senza_rete.disattiva(_vere_rete)


class MemSecrets:
    """Portachiavi finto (come quello dei test delle azioni)."""

    def __init__(self):
        self.data = {}

    def get(self, name):
        return self.data.get(name)

    def set(self, name, value):
        self.data[name] = value

    def delete(self, name):
        self.data.pop(name, None)

NODE = {"id": "ingressotricks", "name": "Ingresso Tricks", "url": "https://mirto-ingresso.local:8443"}
INFO = {"versione_api": "1.1", "host": "mirto-ingresso.local", "indirizzi": ["192.168.1.18"],
        "camere": [{"id": "cam", "nome": "cam", "rtsp_porta": 8554, "percorso": "/cam",
                    "utente": "mirto", "password": "p@ss"}]}


class SyncTest(unittest.TestCase):
    def test_new_node_creates_camera_without_credentials_in_the_url(self):
        secrets = MemSecrets()
        nets, changed = sync_node_cameras([], NODE, INFO, secrets)
        self.assertTrue(changed)
        self.assertEqual(nets, [{"id": "ingresso-tricks", "name": "Ingresso Tricks",
                                 "url": "rtsp://mirto-ingresso.local:8554/cam",
                                 "node": "ingressotricks", "node_cam": "cam"}])
        self.assertEqual(secrets.data, {rtsp_name("ingresso-tricks"): "mirto:p@ss"})
        again, changed = sync_node_cameras(nets, NODE, INFO, secrets)
        self.assertFalse(changed)  # nessun doppione e niente da riscrivere
        self.assertEqual(again, nets)

    def test_existing_camera_is_adopted(self):
        old = [{"id": "ingresso", "name": "Ingresso", "url": "rtsp://192.168.1.18:8554/cam",
                "mdns": "Mirto Cam mirto-ingresso._rtsp._tcp.local."},
               {"id": "giardino", "name": "Giardino", "url": "rtsp://10.0.0.5:554/s1"}]
        nets, changed = sync_node_cameras(old, NODE, INFO)
        self.assertTrue(changed)
        ing = nets[0]
        self.assertEqual((ing["id"], ing["name"], ing["node"], ing["node_cam"]),
                         ("ingresso", "Ingresso", "ingressotricks", "cam"))
        self.assertNotIn("mdns", ing)
        self.assertEqual(len(nets), 2)
        self.assertNotIn("node", nets[1])  # la camera di altri resta com'è

    def test_credentials_update_but_not_runtime_host(self):
        secrets = MemSecrets()
        nets, _ = sync_node_cameras([], NODE, INFO, secrets)
        nets[0]["url"] = nets[0]["url"].replace("mirto-ingresso.local", "192.168.1.18")  # scelto a runtime
        _, changed = sync_node_cameras(nets, NODE, INFO, secrets)
        self.assertFalse(changed)
        info2 = dict(INFO, camere=[dict(INFO["camere"][0], password="nuova")])
        nets2, changed = sync_node_cameras(nets, NODE, info2, secrets)
        self.assertTrue(changed, "password cambiata sul nodo: la camera va riaperta")
        self.assertNotIn("nuova", nets2[0]["url"])
        self.assertEqual(secrets.data[rtsp_name("ingresso-tricks")], "mirto:nuova")

    def test_old_api_untouched(self):
        nets, changed = sync_node_cameras([], NODE, {"versione_api": "1.0", "camere": ["/cam"]})
        self.assertFalse(changed)
        self.assertEqual(nets, [])

    def test_remove_and_url(self):
        nets, _ = sync_node_cameras([{"id": "x", "url": "rtsp://a/1"}], NODE, INFO)
        keep, gone = remove_node_cameras(nets, "ingressotricks")
        self.assertEqual(gone, ["net:ingresso-tricks"])
        self.assertEqual(keep, [{"id": "x", "url": "rtsp://a/1"}])
        url = node_camera_url(nets[1], NODE, {"indirizzi": ["192.0.2.1", "192.168.1.18"]},
                              pick=lambda addrs, port, host: "192.168.1.18")
        self.assertEqual(url, "rtsp://192.168.1.18:8554/cam")
        self.assertEqual(camera_url("h", {"porta": 554, "percorso": "x"}), "rtsp://h:554/x")
        self.assertEqual(camera_credentials(INFO["camere"][0]), "mirto:p@ss")
        self.assertEqual(camera_credentials({"utente": "solo"}), "")


class EngineSyncTest(unittest.TestCase):
    def test_pairing_brings_cameras(self):
        import os

        from mirto.actions.engine import ActionEngine
        from mirto.actions.nodes import fetch_certificate, pair_node
        from mirto.actions.simulator import SimulatorServer
        from mirto.config import Config
        from mirto.credentials import SecretStore

        with tempfile.TemporaryDirectory() as d:
            os.environ["MIRTO_CONFIG_DIR"] = d
            os.environ["MIRTO_DATA_DIR"] = d
            srv = SimulatorServer("ingresso", cert_dir=Path(d, "c"), cameras=[
                {"id": "cam", "nome": "cam", "rtsp_porta": 8554, "percorso": "/cam"}]).start()
            try:
                cfg = Config(Path(d) / "config.yaml")
                secrets = SecretStore(); secrets._kr = None
                pem, _ = fetch_certificate(srv.url)
                pair_node(cfg, secrets, "ingresso", "Ingresso", srv.url, pem, srv.key_hex)
                reloads = []
                core = types.SimpleNamespace(config=cfg, secrets=secrets, notifier=None,
                                             cameras=types.SimpleNamespace(reload=lambda: reloads.append(1)))
                eng = ActionEngine(core)
                eng.check_nodes()
                nets = cfg.get("cameras.network")
                self.assertEqual([(n["node"], n["node_cam"]) for n in nets], [("ingresso", "cam")])
                self.assertEqual(reloads, [1])
                self.assertEqual(eng.node_meta["ingresso"]["tipo"], "simulato")
                self.assertEqual(eng.node_camera_url("net:" + nets[0]["id"]), "rtsp://127.0.0.1:8554/cam")
                self.assertIn("127.0.0.1", eng.node_hosts())
            finally:
                srv.stop()


if __name__ == "__main__":
    unittest.main()
