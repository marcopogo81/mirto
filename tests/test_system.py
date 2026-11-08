# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
import os
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mirto.system import autostart


class AutostartMacTest(unittest.TestCase):
    def test_launch_agent(self):
        """Il LaunchAgent che scriviamo su un Mac, provato anche dove un Mac non c'è.

        ``MIRTO_DATA_DIR`` non è un dettaglio da prova: senza, ``set_enabled`` crea la cartella dei
        registri **vera**, dentro la casa di chi sta provando, e su Windows ci arriva passando da
        ``platformdirs``, che lì legge il registro di sistema.
        """
        with tempfile.TemporaryDirectory() as home, \
                mock.patch.dict(os.environ, {"MIRTO_DATA_DIR": str(Path(home) / "dati")}), \
                mock.patch.object(autostart, "sistema", lambda: "darwin"), \
                mock.patch.object(autostart.Path, "home", return_value=Path(home)), \
                mock.patch.object(autostart.subprocess, "run") as run:
            self.assertFalse(autostart.is_enabled())
            autostart.set_enabled(True)
            plist = plistlib.loads(autostart._plist_path().read_bytes())
            self.assertEqual(plist["Label"], "it.mirto.app")
            self.assertTrue(plist["RunAtLoad"])
            self.assertEqual(plist["KeepAlive"], {"SuccessfulExit": False})
            self.assertEqual(plist["ProgramArguments"][-2:], ["-m", "mirto"])
            self.assertTrue(autostart.is_enabled())
            autostart.set_enabled(False)
            self.assertFalse(autostart.is_enabled())
            self.assertTrue(run.called)


if __name__ == "__main__":
    unittest.main()


class PortachiaviWifiTest(unittest.TestCase):
    """La password del Wi-Fi presa dal computer: i lettori di testo, e cosa chiede al sistema.

    Il consenso lo chiede il sistema operativo (il portachiavi su macOS, i privilegi su Windows): qui si
    prova che il comando giusto parta col nome giusto, che quello che torna venga ripulito, e che **la
    password non finisca nel registro**. Il resto si prova a mano, perché una finestra del portachiavi
    non si simula.
    """

    PROFILI_WIN = """
Interfacce sul sistema:

    Profilo tutti gli utenti     : CasaMia
    Profilo tutti gli utenti     : CasaMia_Ospiti
    Profilo tutti gli utenti     : Hotel Wifi
"""
    CHIAVE_WIN = """
Impostazioni di sicurezza
-------------------------
    Autenticazione         : WPA2-Personal
    Crittografia           : CCMP
    Chiave di sicurezza    : Presente
    Contenuto chiave       : unapasswordlunga
"""
    CHIAVE_WIN_EN = """
Security settings
-----------------
    Authentication         : WPA2-Personal
    Cipher                 : CCMP
    Security key           : Present
    Key Content            : unapasswordlunga
"""
    RETI_MAC = """Preferred networks on en0:
	CasaMia
	CasaMia_Ospiti
	iPhone di Marco
"""

    def test_the_windows_profiles_are_read_in_any_language(self):
        from mirto.system import portachiavi

        self.assertEqual(portachiavi.reti_da_netsh(self.PROFILI_WIN),
                         ["CasaMia", "CasaMia_Ospiti", "Hotel Wifi"])
        self.assertEqual(portachiavi.chiave_da_netsh(self.CHIAVE_WIN), "unapasswordlunga")
        self.assertEqual(portachiavi.chiave_da_netsh(self.CHIAVE_WIN_EN), "unapasswordlunga",
                         "un Windows in inglese scrive «Key Content»")

    def test_a_profile_without_the_key_gives_nothing(self):
        """Senza privilegi Windows stampa tutto il resto e non la chiave: non si inventa una password."""
        from mirto.system import portachiavi

        senza = self.CHIAVE_WIN.replace("    Contenuto chiave       : unapasswordlunga", "")
        self.assertEqual(portachiavi.chiave_da_netsh(senza), "")
        self.assertEqual(portachiavi.chiave_da_netsh(""), "")

    def test_the_mac_networks_are_read_without_the_title(self):
        from mirto.system import portachiavi

        self.assertEqual(portachiavi.reti_da_networksetup(self.RETI_MAC),
                         ["CasaMia", "CasaMia_Ospiti", "iPhone di Marco"])
        self.assertEqual(portachiavi.reti_da_networksetup(""), [])

    def test_the_linux_wifi_connections_are_told_apart(self):
        from mirto.system import portachiavi

        testo = "CasaMia:802-11-wireless\nCavo:802-3-ethernet\nCasaMia_Ospiti:802-11-wireless\n"
        self.assertEqual(portachiavi.reti_da_nmcli(testo), ["CasaMia", "CasaMia_Ospiti"])

    def test_the_password_is_asked_with_the_right_command_and_never_logged(self):
        import logging
        from unittest import mock

        from mirto.system import portachiavi

        chiamate = []

        def finto(cmd, timeout=30):
            chiamate.append(list(cmd))
            return 0, "unapasswordlunga\n"

        with mock.patch.object(portachiavi, "_esegui", finto), \
                mock.patch.object(portachiavi, "sistema", lambda: "darwin"), \
                self.assertLogs("mirto.system.portachiavi", level=logging.INFO) as registro:
            self.assertEqual(portachiavi.password_di("CasaMia"), "unapasswordlunga",
                             "il capoverso finale va via: finirebbe dentro la password")
        self.assertIn("find-generic-password", chiamate[0][1])
        self.assertIn("CasaMia", chiamate[0])
        scritto = "\n".join(registro.output)
        self.assertIn("CasaMia", scritto, "nel registro il nome della rete ci può stare")
        self.assertNotIn("unapasswordlunga", scritto, "la password nel registro non ci va mai")

    def test_an_ssid_that_is_empty_asks_nothing(self):
        from unittest import mock

        from mirto.system import portachiavi

        chiamate = []
        with mock.patch.object(portachiavi, "_esegui", lambda *a, **k: chiamate.append(a) or (0, "x")):
            self.assertEqual(portachiavi.password_di("  "), "")
        self.assertEqual(chiamate, [])

    def test_a_command_that_is_not_there_is_not_a_crash(self):
        from unittest import mock

        from mirto.system import portachiavi

        def esplode(*_a, **_k):
            raise FileNotFoundError("netsh")

        with mock.patch.object(portachiavi.subprocess, "run", esplode):
            self.assertEqual(portachiavi._esegui(["netsh"]), (1, ""))
            self.assertEqual(portachiavi.reti_conosciute(), [])

    def test_why_not_says_something_useful_on_every_system(self):
        from unittest import mock

        from mirto.system import portachiavi

        for nome in ("darwin", "win32", "linux"):
            with mock.patch.object(portachiavi, "sistema", lambda n=nome: n):
                self.assertGreater(len(portachiavi.perche_no()), 20, nome)
                self.assertTrue(portachiavi.supportato(), nome)
