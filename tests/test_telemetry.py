# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from mirto import __version__
from mirto import telemetry as tm
from mirto.config import Config


class FakeServer:
    def __init__(self, latest=""):
        self.calls = []
        self.latest = latest
        self.fail = False

    def __call__(self, req):
        if self.fail:
            raise OSError("rete assente")
        body = json.loads(req.data) if req.data else None
        self.calls.append((req.get_method(), req.full_url, body))
        return io.BytesIO(json.dumps({"ok": True, "latest": self.latest}).encode())


class TelemetryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(Path(self.tmp.name) / "c.yaml")
        self._env = os.environ.get("MIRTO_STATS_URL")
        os.environ["MIRTO_STATS_URL"] = "https://stats.example"
        self.now = [1_000_000.0]
        self.server = FakeServer()
        self.updates = []
        self.t = tm.Telemetry(self.cfg, lambda: {"cameras": 3, "nodes": 1, "recording": "events",
                                                 "features": {"telegram": True, "sms": False}},
                              on_update=lambda v: (self.updates.append(v), True)[1], clock=lambda: self.now[0], opener=self.server)

    def tearDown(self):
        self.tmp.cleanup()
        os.environ["MIRTO_STATS_URL"] = self._env or "off"

    def test_not_asked_sends_only_version_question(self):
        self.assertFalse(self.t.asked())
        self.t.tick()
        self.assertEqual(self.server.calls, [("GET", "https://stats.example/v1/latest", None)])

    def test_no_endpoint_sends_nothing(self):
        os.environ["MIRTO_STATS_URL"] = "off"
        self.t.set_consent(True)
        self.t.tick()
        self.assertEqual(self.server.calls, [])

    def test_yes_sends_anonymous_daily_ping(self):
        self.t.set_consent(True)
        install_id = self.cfg.get("telemetry.install_id")
        self.assertEqual(len(install_id), 32)
        self.t.tick()
        method, url, body = self.server.calls[0]
        self.assertEqual((method, url), ("POST", "https://stats.example/v1/ping"))
        self.assertEqual(body["id"], install_id)
        self.assertEqual(body["cameras"], "2-4")
        self.assertEqual(body["nodes"], "1")
        self.assertEqual(body["features"], ["telegram"])
        self.assertEqual(set(body), {"id", "version", "os", "lang", "cameras", "nodes", "recording", "features"})
        self.t.tick()  # stesso giorno: niente
        self.assertEqual(len(self.server.calls), 1)
        self.now[0] += tm.DAY
        self.t.tick()
        self.assertEqual(len(self.server.calls), 2)

    def test_no_sends_single_anonymous_decline(self):
        self.t.set_consent(False)
        self.assertEqual(self.cfg.get("telemetry.install_id"), "")
        self.t.tick()
        self.t.tick()
        self.now[0] += 3 * tm.DAY
        self.t.tick()
        declines = [c for c in self.server.calls if c[1].endswith("/v1/declined")]
        self.assertEqual(declines, [("POST", "https://stats.example/v1/declined", {"version": __version__})])
        self.assertFalse(any(c[1].endswith("/v1/ping") for c in self.server.calls))

    def test_decline_retried_until_delivered(self):
        self.server.fail = True
        self.t.set_consent(False)
        self.t.tick()
        self.assertTrue(self.cfg.get("telemetry.declined_pending"))
        self.server.fail = False
        self.t.tick()
        self.assertFalse(self.cfg.get("telemetry.declined_pending"))

    def test_turning_off_later_is_not_counted_as_decline(self):
        self.t.set_consent(True)
        self.t.set_consent(False)
        self.assertFalse(self.cfg.get("telemetry.declined_pending"))
        self.assertEqual(self.cfg.get("telemetry.install_id"), "")

    def test_withdrawal_and_reset_delete_id_on_server(self):
        self.t.set_consent(True)
        first = self.cfg.get("telemetry.install_id")
        self.t.reset_id()
        second = self.cfg.get("telemetry.install_id")
        self.t.set_consent(False)
        self.t.tick()
        forgets = [c[2]["id"] for c in self.server.calls if c[1].endswith("/v1/forget")]
        self.assertEqual(forgets, [first, second])
        self.assertEqual(self.cfg.get("telemetry.forget_ids"), [])

    def test_yes_after_offline_no_is_not_counted_twice(self):
        self.server.fail = True
        self.t.set_consent(False)
        self.t.tick()
        self.server.fail = False
        self.t.set_consent(True)
        self.t.tick()
        self.assertFalse(any(c[1].endswith("/v1/declined") for c in self.server.calls))

    def test_consent_change_during_request_is_not_overwritten(self):
        def answer_during_request(req):
            self.t.set_consent(True)  # l'utente preme Sì mentre parte la domanda della versione
            return io.BytesIO(b"{}")
        self.t._open = answer_during_request
        self.t.tick()
        self.assertEqual(self.cfg.get("telemetry.last_sent"), 0)  # il primo battito resta da fare subito

    def test_update_not_marked_without_window(self):
        self.t.on_update = lambda v: False
        self.server.latest = "99.0.0"
        self.t.tick()
        self.assertEqual(self.cfg.get("telemetry.notified_version"), "")

    def test_available(self):
        self.assertTrue(self.t.available())
        os.environ["MIRTO_STATS_URL"] = "off"
        self.assertFalse(self.t.available())

    def test_reset_id(self):
        self.t.set_consent(True)
        old = self.cfg.get("telemetry.install_id")
        self.t.reset_id()
        self.assertNotEqual(old, self.cfg.get("telemetry.install_id"))

    def test_update_notified_once(self):
        self.server.latest = "99.0.0"
        self.t.tick()
        self.now[0] += tm.DAY
        self.t.tick()
        self.assertEqual(self.updates, ["99.0.0"])
        self.assertEqual(self.t.latest, "99.0.0")

    def test_update_check_off_and_no_consent_sends_nothing(self):
        self.cfg.set("telemetry.update_check", False)
        self.t.tick()
        self.assertEqual(self.server.calls, [])

    def test_network_error_retries_later(self):
        self.server.fail = True
        self.t.tick()
        self.assertTrue(self.t.last_error)
        self.server.fail = False
        self.t.tick()
        self.assertEqual(len(self.server.calls), 1)

    def test_default_endpoint_and_off(self):
        os.environ["MIRTO_STATS_URL"] = ""
        self.assertEqual(tm.endpoint(self.cfg), tm.ENDPOINT)
        self.cfg.set("telemetry.endpoint", "off")
        self.assertEqual(tm.endpoint(self.cfg), "")

    def test_helpers(self):
        self.assertEqual([tm.bucket(n) for n in (0, 1, 2, 4, 5, 9, 10, 50)],
                         ["0", "1", "2-4", "2-4", "5-9", "5-9", "10+", "10+"])
        self.assertTrue(tm.newer("0.10.0", "0.9.9"))
        self.assertFalse(tm.newer("0.1.0", "0.1.0"))
        self.assertFalse(tm.newer("", "0.1.0"))


class CoreStatsTest(unittest.TestCase):
    def test_core_stats_have_no_names(self):
        import os

        from mirto.core import MirtoCore
        from tests.test_audio import mem_secrets

        with tempfile.TemporaryDirectory() as d, contextlib.ExitStack() as chiudi:
            os.environ["MIRTO_DATA_DIR"] = d
            cfg = Config(Path(d) / "c.yaml")
            cfg.set("actions.nodes", [{"id": "n", "name": "Ingresso", "url": "https://x"}])
            cfg.set("actions.items", [{"name": "Corvi", "node": "n", "action": "d", "kind": "deterrent",
                                       "targets": ["bird"]}])
            cfg.set("recording.cameras", {"cam1": "continuous"})
            core = MirtoCore(cfg, mem_secrets(), detector=object(), discover=list)
            chiudi.callback(core.stop)
            stats = core.telemetry_stats()
            self.assertEqual(stats["nodes"], 1)
            self.assertEqual(stats["recording"], "mixed")
            self.assertTrue(stats["features"]["deterrent"])
            body = json.dumps(tm.payload(cfg, stats))
            self.assertNotIn("Ingresso", body)
            self.assertNotIn("cam1", body)


if __name__ == "__main__":
    unittest.main()
