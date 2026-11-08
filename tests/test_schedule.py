# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
import contextlib
import os
import tempfile
import types
import unittest
from datetime import date, datetime
from pathlib import Path

from mirto.config import Config
from mirto.schedule import ScheduleRule, Scheduler, load_rules, next_event, occurrences


def rule(**kw):
    base = {"nome": "R", "azione": "inserisci", "ora": "22:00"}
    base.update(kw)
    return ScheduleRule.from_dict(base)


class RuleTest(unittest.TestCase):
    def test_validation(self):
        for bad in ({"azione": "accendi"}, {"ora": "25:00"}, {"ora": "aa"}, {"giorni": ["lunedi", "xyz"]},
                    {"dal": "2026-13-01"}, {"dal": "2026-12-31", "al": "2026-01-01"}):
            with self.assertRaises(ValueError, msg=bad):
                rule(**bad)
        rules, errors = load_rules([{"nome": "ok", "azione": "inserisci", "ora": "07:00"}, {"azione": "x"}])
        self.assertEqual(len(rules), 1)
        self.assertEqual(len(errors), 1)

    def test_days_and_period(self):
        r = rule(giorni=["lun", "ven"], dal="2026-10-01", al="2026-10-31")
        self.assertTrue(r.applies_on(date(2026, 10, 16)))      # venerdì
        self.assertFalse(r.applies_on(date(2026, 10, 17)))     # sabato
        self.assertFalse(r.applies_on(date(2026, 11, 6)))      # venerdì ma fuori periodo
        self.assertTrue(rule().applies_on(date(2026, 10, 17)))  # tutti i giorni
        self.assertFalse(rule(attiva=False).applies_on(date(2026, 10, 17)))

    def test_occurrences_across_midnight(self):
        rules = [rule(nome="Notte", ora="22:00"), rule(nome="Mattina", azione="disinserisci", ora="07:00")]
        occ = occurrences(rules, datetime(2026, 10, 16, 21, 0), datetime(2026, 10, 17, 8, 0))
        self.assertEqual([r.nome for _t, r in occ], ["Notte", "Mattina"])

    def test_next_event(self):
        rules = [rule(nome="Weekend", ora="09:00", giorni=["sab"])]
        t, r = next_event(rules, datetime(2026, 10, 16, 20, 0))  # venerdì sera
        self.assertEqual(t, datetime(2026, 10, 17, 9, 0))
        self.assertIsNone(next_event([rule(al="2026-01-01")], datetime(2026, 10, 16, 20, 0)))


class SchedulerTest(unittest.TestCase):
    def make(self, rules, armed=False, enabled=True):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = Config(Path(tmp.name) / "c.yaml")
        cfg.set("schedule.rules", rules)
        cfg.set("schedule.enabled", enabled)
        cfg.set("actions.armed", armed)
        calls = []

        def set_armed(value, source=""):
            calls.append((value, source))
            cfg.set("actions.armed", value)
            return "ok"

        core = types.SimpleNamespace(config=cfg, set_armed=set_armed)
        t = [datetime(2026, 10, 16, 21, 59, 50).timestamp()]
        sch = Scheduler(core, clock=lambda: t[0])
        return sch, calls, t

    def test_arms_at_time_and_respects_manual_override(self):
        sch, calls, t = self.make([{"nome": "Notte", "azione": "inserisci", "ora": "22:00"}])
        t[0] += 5
        self.assertIsNone(sch.tick())
        t[0] += 20  # 22:00:15
        self.assertEqual(sch.tick().nome, "Notte")
        self.assertEqual(calls, [(True, "programmazione «Notte»")])
        sch.core.config.set("actions.armed", False)  # disinserito a mano
        t[0] += 20
        sch.tick()
        self.assertEqual(len(calls), 1, "il cambio manuale resta fino al prossimo evento")

    def test_sleep_gap_applies_only_last(self):
        sch, calls, t = self.make([
            {"nome": "Notte", "azione": "inserisci", "ora": "22:00"},
            {"nome": "Mattina", "azione": "disinserisci", "ora": "07:00"},
        ], armed=False)
        t[0] = datetime(2026, 10, 17, 8, 0).timestamp()  # Mac in stop tutta la notte
        self.assertEqual(sch.tick().nome, "Mattina")
        self.assertEqual(calls, [], "già disinserito: nessun cambio")

    def test_disabled(self):
        sch, calls, t = self.make([{"nome": "Notte", "azione": "inserisci", "ora": "22:00"}], enabled=False)
        t[0] += 30
        self.assertIsNone(sch.tick())
        self.assertIsNone(sch.next())
        self.assertIn("SOSPESA", sch.status_text())


class CoreScheduleTest(unittest.TestCase):
    def test_core_set_armed_and_lines(self):
        from mirto.core import MirtoCore
        from mirto.credentials import SecretStore
        from tests.test_core import FakeDetector
        with tempfile.TemporaryDirectory() as d, contextlib.ExitStack() as chiudi:
            os.environ["MIRTO_DATA_DIR"] = d
            cfg = Config(Path(d) / "c.yaml")
            store = SecretStore(); store._kr = None
            core = MirtoCore(cfg, store, detector=FakeDetector(), discover=lambda: [])
            chiudi.callback(core.stop)
            self.assertEqual(core.schedule_line(), "⏰ Nessuna programmazione")
            cfg.set("schedule.rules", [{"nome": "Notte", "azione": "inserisci", "ora": "22:00"}])
            self.assertIn("Prossimo", core.schedule_line())
            self.assertIn("SOSPESA", core.handlers.schedule("sospendi"))
            self.assertEqual(core.schedule_line(), "⏰ Programmazione sospesa")
            core.handlers.schedule("riattiva")
            core.set_armed(True)
            self.assertTrue(cfg.get("actions.armed"))
            self.assertIn("Notte", core.handlers.schedule(""))


if __name__ == "__main__":
    unittest.main()
