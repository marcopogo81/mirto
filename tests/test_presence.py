# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
import socket
import tempfile
import unittest
from pathlib import Path

from mirto.config import Config
from mirto.presence import PresenceMonitor, is_alive, is_private_mac, normalize_mac, parse_arp

MAC_OS_ARP = """\
? (192.168.1.1) at 4:5:6:a:b:c on en0 ifscope [ethernet]
? (192.168.1.23) at 3a:7f:1:2:3:4 on en0 ifscope [ethernet]
? (192.168.1.40) at (incomplete) on en0 ifscope [ethernet]
? (192.168.1.255) at ff:ff:ff:ff:ff:ff on en0 ifscope [ethernet]
? (224.0.0.251) at 1:0:5e:0:0:fb on en0 ifscope permanent [ethernet]
"""
WINDOWS_ARP = """\
Interfaccia: 192.168.1.10 --- 0x5
  Indirizzo Internet    Indirizzo fisico      Tipo
  192.168.1.1           04-05-06-0a-0b-0c     dinamico
  192.168.1.23          3a-7f-01-02-03-04     dinamico
"""


class ParseTest(unittest.TestCase):
    def test_parse_macos_and_windows(self):
        expected = {"04:05:06:0a:0b:0c": "192.168.1.1", "3a:7f:01:02:03:04": "192.168.1.23"}
        self.assertEqual(parse_arp(MAC_OS_ARP), expected)
        self.assertEqual(parse_arp(WINDOWS_ARP), expected)

    def test_mac_helpers(self):
        self.assertEqual(normalize_mac("A:B:C:1:2:3"), "0a:0b:0c:01:02:03")
        self.assertTrue(is_private_mac("3a:7f:01:02:03:04"))   # indirizzo Wi-Fi privato dell'iPhone
        self.assertFalse(is_private_mac("04:05:06:0a:0b:0c"))

    def test_is_alive_refused_means_present(self):
        """Un rifiuto vuol dire "il dispositivo c'è", un silenzio vuol dire "non c'è".

        Con un connettore finto invece della rete vera: i codici di errore e i tempi del firewall
        cambiano da un sistema all'altro (su Windows il rifiuto ha un numero tutto suo).
        """
        import errno as E

        def rifiuto_unix(_indirizzo, timeout=None):
            raise ConnectionRefusedError(E.ECONNREFUSED, "rifiutata")

        def rifiuto_windows(_indirizzo, timeout=None):
            raise OSError(getattr(E, "WSAECONNREFUSED", E.ECONNREFUSED), "rifiutata")

        def silenzio(_indirizzo, timeout=None):
            raise TimeoutError

        def irraggiungibile(_indirizzo, timeout=None):
            raise OSError(E.EHOSTUNREACH, "nessuna risposta")

        self.assertTrue(is_alive("192.0.2.1", timeout=1, connect=rifiuto_unix))
        self.assertTrue(is_alive("192.0.2.1", timeout=1, connect=rifiuto_windows))
        self.assertFalse(is_alive("192.0.2.1", timeout=1, connect=silenzio))
        self.assertFalse(is_alive("192.0.2.1", timeout=1, connect=irraggiungibile))


class MonitorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(Path(self.tmp.name) / "c.yaml")
        self.cfg.set("presence", {"enabled": True, "away_minutes": 10, "arm_when_all_away": True,
                                  "disarm_on_arrival": True,
                                  "devices": [{"name": "Marco", "mac": "3A:7F:1:2:3:4"},
                                              {"name": "Luca", "mac": "aa:bb:cc:dd:ee:01"}]})
        self.t = [1000.0]
        self.armed = [False]
        self.calls = []
        self.present = {"3a:7f:01:02:03:04": "192.168.1.23"}  # mac → ip dei telefoni in casa

        def set_armed(on, source):
            self.calls.append((on, source))
            self.armed[0] = on

        self.mon = PresenceMonitor(
            self.cfg, set_armed, lambda: self.armed[0], clock=lambda: self.t[0],
            sweep_fn=lambda: None, arp_fn=lambda: dict(self.present),
            alive_fn=lambda ip: ip in self.present.values())

    def tearDown(self):
        self.tmp.cleanup()

    def step(self, minutes):
        self.t[0] += minutes * 60
        self.mon.check()

    def test_everyone_leaves_then_someone_returns(self):
        self.mon.check()
        self.assertTrue(self.mon.home)
        self.assertEqual(self.calls, [], "all'avvio non si cambia nulla")
        self.assertEqual(self.mon.summary(), "👥 In casa: Marco · fuori: Luca")

        self.present.clear()                      # Marco esce (resta nella cache ARP? no: non risponde)
        self.step(5)
        self.assertEqual(self.calls, [], "fuori da poco: si aspetta")
        self.step(6)
        self.assertEqual(self.calls, [(True, "presenza: sono usciti tutti")])

        self.present["aa:bb:cc:dd:ee:01"] = "192.168.1.50"   # rientra Luca
        self.step(1)
        self.assertEqual(self.calls[-1], (False, "presenza: è rientrato Luca"))
        self.assertFalse(self.armed[0])

    def test_stale_arp_entry_is_not_presence(self):
        self.mon.check()
        alive = {"192.168.1.23": True}
        self.mon._alive = lambda ip: alive.get(ip, False)
        alive["192.168.1.23"] = False             # ancora nella tabella ARP ma non risponde
        self.step(11)
        self.assertEqual(self.calls, [(True, "presenza: sono usciti tutti")])

    def test_start_with_nobody_home_arms_after_delay(self):
        self.present.clear()
        self.mon.check()
        self.assertEqual(self.calls, [])
        self.step(11)
        self.assertEqual(self.calls, [(True, "presenza: sono usciti tutti")])

    def test_manual_choice_respected_and_options(self):
        self.mon.check()
        self.armed[0] = True                      # attivata a mano con tutti in casa: resta attiva
        self.step(1)
        self.assertEqual(self.calls, [])
        self.cfg.set("presence.disarm_on_arrival", False)
        self.present.clear()
        self.step(11)
        self.assertEqual(self.calls, [], "era già attiva: niente da fare")
        self.present["3a:7f:01:02:03:04"] = "192.168.1.23"
        self.step(1)
        self.assertEqual(self.calls, [], "disattivazione al rientro spenta")

    def test_ip_taken_by_other_device_is_not_presence(self):
        self.mon.check()                          # Marco in casa con 192.168.1.23
        self.present.clear()
        self.present["99:99:99:99:99:99"] = "192.168.1.23"   # il router dà quell'indirizzo a un altro
        self.step(11)
        self.assertEqual(self.calls, [(True, "presenza: sono usciti tutti")])

    def test_enabling_later_restarts_grace_period(self):
        self.cfg.set("presence.enabled", False)
        self.mon.check()
        self.t[0] += 3600                         # un'ora dopo l'avvio l'utente accende la presenza
        self.cfg.set("presence.enabled", True)
        self.present.clear()                      # primo controllo: il telefono dorme
        self.mon.check()
        self.assertEqual(self.calls, [], "appena accesa: si aspetta prima di dire «usciti tutti»")
        self.present["3a:7f:01:02:03:04"] = "192.168.1.23"
        self.step(2)
        self.assertTrue(self.mon.home)
        self.assertEqual(self.calls, [])

    def test_disabled_does_nothing(self):
        self.cfg.set("presence.enabled", False)
        self.present.clear()
        self.mon.check()
        self.step(30)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.mon.summary(), "")


if __name__ == "__main__":
    unittest.main()
