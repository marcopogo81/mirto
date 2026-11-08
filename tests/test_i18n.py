# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
import json
import string
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import i18n_check  # noqa: E402

from mirto import i18n  # noqa: E402
from mirto.i18n import _, questo_computer  # noqa: E402


class I18nTest(unittest.TestCase):
    def tearDown(self):
        i18n.set_language("it")

    def test_italian_is_source(self):
        i18n.set_language("it")
        self.assertEqual(_("Persona rilevata"), "Persona rilevata")
        self.assertEqual(_("Foto salvata in {f}", f="x.jpg"), "Foto salvata in x.jpg")

    def test_english_catalog(self):
        self.assertEqual(i18n.set_language("en"), "en")
        self.assertEqual(_("Persona rilevata"), "Person detected")
        self.assertEqual(_("testo che non esiste"), "testo che non esiste")

    def test_unknown_language_falls_back(self):
        self.assertEqual(i18n.set_language("xx"), "it")

    def test_every_text_translated_with_same_placeholders(self):
        texts = i18n_check.collect()
        for path in (ROOT / "mirto" / "locales").glob("*.json"):
            cat = json.loads(path.read_text(encoding="utf-8"))
            missing = [t for t in texts if not cat.get(t)]
            self.assertEqual(missing, [], f"{path.name}: testi senza traduzione")
            for t in texts:
                self.assertEqual(i18n_check.fields(t), i18n_check.fields(cat[t]), t)
                # la traduzione deve potersi formattare con gli stessi valori dell'originale
                list(string.Formatter().parse(cat[t]))

    def test_labels_follow_language(self):
        from mirto.detection.labels import headline, short

        i18n.set_language("en")
        self.assertEqual(short("dog"), "Detected: Dog")
        self.assertTrue(headline("person").endswith("Person detected"))
        i18n.set_language("it")
        self.assertEqual(short("dog"), "Rilevato: Cane")


if __name__ == "__main__":
    unittest.main()


class PlaceholderNamesTest(unittest.TestCase):
    def test_placeholder_named_text(self):
        # un segnaposto può chiamarsi come il parametro di _(): {text}
        self.assertEqual(_("<b>{text}</b>", text="ATTIVA"), "<b>ATTIVA</b>")


class ParolaComputerTest(unittest.TestCase):
    """La parola con cui si nomina il computer di chi legge segue il sistema, non quello di chi scrive.

    Trovato al primo avvio su un PC Windows vero (W-01, 19/10): il pannello diceva «gli eventi
    restano solo su questo Mac», e con lui un'altra decina di frasi — il microfono del Mac, la prima
    camera del Mac, «il Mac è collegato al Wi-Fi?». Su Windows il programma parlava all'utente di un
    computer che non ha, nei primi dieci secondi di uso.
    """

    def test_the_word_follows_the_system(self):
        from unittest import mock

        for piattaforma, atteso in (("darwin", "Mac"), ("win32", "PC"), ("linux", "computer")):
            with mock.patch("mirto.i18n.sistema", lambda: piattaforma):
                self.assertEqual(questo_computer(), atteso, piattaforma)

    def test_on_windows_the_message_does_not_mention_a_mac(self):
        from unittest import mock

        with mock.patch("mirto.i18n.sistema", lambda: "win32"):
            testo = _("Nessun avviso. Gli eventi restano solo su questo {computer}.",
                      computer=questo_computer())
        self.assertIn("PC", testo)
        self.assertNotIn("Mac", testo)

    def test_no_text_calls_the_reader_s_computer_a_mac(self):
        """Nessun testo traducibile dice «Mac» tranne la parola stessa: gli altri usano {computer}."""
        sospetti = sorted(t for t in i18n_check.collect() if "Mac" in t and t != "Mac")
        self.assertEqual(sospetti, [],
                         "queste frasi chiamano «Mac» il computer di chi legge: usa {computer}")

