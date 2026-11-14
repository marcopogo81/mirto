# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""La finestra principale si apre, e continua a disegnarsi.

**Perché questo file esiste.** Il 14/11 Mirto non si è aperto affatto: una riga del riquadro
«Sistema» chiamava ``StatusRow.set`` con una lista dove ci va un testo, Qt ha sollevato, e la
finestra è morta dentro il suo stesso costruttore. Nessuna prova se n'era accorta — **nessuna prova
costruiva la finestra principale**, in tutto il progetto. L'unico modo di scoprirlo era aprire
Mirto, e lo ha fatto Marco.

Il riquadro «Sistema» si ridisegna **ogni secondo**, e ogni riga che ci si aggiunge è un pezzo di
codice che gira sempre e che nessuno prova. Qui si costruisce la finestra per davvero e la si fa
ridisegnare in tutti gli stati che contano: protetta e no, con e senza domotica, con la domotica
collegata e con il broker spento.

Senza PySide6 queste prove si saltano. In CI girano, perché PySide6 sta in ``requirements.txt``.
"""
import os
import tempfile
import unittest
from pathlib import Path

from mirto.config import Config
from mirto.credentials import SecretStore


class LaFinestraSiApreTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            from PySide6.QtWidgets import QApplication
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"senza PySide6 non c'è finestra da aprire ({exc})") from exc
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from mirto.core import MirtoCore

        self.cartella = tempfile.TemporaryDirectory()
        self.addCleanup(self.cartella.cleanup)
        os.environ["MIRTO_DATA_DIR"] = self.cartella.name
        self.cfg = Config(Path(self.cartella.name) / "c.yaml")
        segreti = SecretStore()
        segreti._kr = None          # noqa: SLF001 - niente portachiavi vero nelle prove
        self.core = MirtoCore(self.cfg, segreti, discover=lambda: [])
        self.addCleanup(self.core.stop)

    def _finestra(self):
        from mirto.gui.app import MainWindow

        win = MainWindow(self.core)
        self.addCleanup(win.deleteLater)
        return win

    def test_si_costruisce(self):
        """La prova che mancava: se il costruttore solleva, Mirto non si apre e basta."""
        win = self._finestra()
        self.assertTrue(win.windowTitle())

    def test_il_riquadro_sistema_si_ridisegna_senza_esplodere(self):
        # gira ogni secondo: un errore qui dentro non è un difetto grafico, è il programma che muore
        win = self._finestra()
        for _ in range(3):
            win._update_status()        # noqa: SLF001
        self.assertTrue(True)

    def test_si_ridisegna_anche_a_protezione_attiva(self):
        win = self._finestra()
        self.core.set_armed(True)
        win._update_status()            # noqa: SLF001
        self.core.set_armed(False)
        win._update_status()            # noqa: SLF001

    def test_senza_domotica_la_riga_non_c_e(self):
        # chi non ha un impianto smart non deve vedersi una riga «non configurata» per sempre
        win = self._finestra()
        win._update_status()            # noqa: SLF001
        self.assertFalse(win.row_smart.isVisibleTo(win))

    def test_con_la_domotica_configurata_ma_spenta_la_riga_lo_dice(self):
        """Il caso che ha fatto esplodere Mirto: configurata, e il broker non risponde.

        È anche il caso normale di chi riaccende il computer prima del broker, quindi non è un caso
        limite: è il primo che si incontra.
        """
        self.cfg.set("smarthome", {"enabled": True, "host": "127.0.0.1", "port": 1})
        self.core.domotica.riapri()
        win = self._finestra()
        win._update_status()            # noqa: SLF001
        self.assertTrue(win.row_smart.isVisibleTo(win))
        self.assertIn("SmartHome", win.row_smart.text.text())

    def test_con_la_domotica_collegata_la_riga_conta_i_dispositivi(self):
        import time

        from mirto.actions.smarthome_finta import (
            ESPHOME as F_ESPHOME,
            LUCE,
            CasaFinta,
            DispositivoFinto,
        )

        finta = CasaFinta([DispositivoFinto("luce_scale", "Luce delle scale", LUCE, F_ESPHOME)]).avvia()
        self.addCleanup(finta.ferma)
        self.cfg.set("smarthome", {"enabled": True, "host": "127.0.0.1", "port": finta.porta})
        self.core.domotica.riapri()
        win = self._finestra()
        fine = time.monotonic() + 4.0
        while time.monotonic() < fine and not self.core.domotica.dispositivi():
            time.sleep(0.02)
            self.app.processEvents()
        win._update_status()            # noqa: SLF001
        self.assertTrue(win.row_smart.isVisibleTo(win))
        self.assertIn("1", win.row_smart.text.text())


class LaPaginaRivediTest(unittest.TestCase):
    """La riga della ricerca sta dentro i comandi, non sopra il video.

    Marco, 14/11: *«il filtro in alto della data, del device, la cartella — voglio che tutta quella
    riga che fa parte della ricerca stia sotto dentro i controlli, in quanto fa parte dei controlli,
    inutile averla sopra»*. Scegliere la camera e il giorno è lo stesso gesto di scegliere il
    minuto: i gesti che fanno la stessa cosa stanno insieme, e sopra il video resta il video.
    """

    @classmethod
    def setUpClass(cls):
        try:
            from PySide6.QtWidgets import QApplication
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"senza PySide6 non c'è finestra da aprire ({exc})") from exc
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication([])

    def _vista(self):
        from mirto.config import Config as _C
        from mirto.core import MirtoCore
        from mirto.credentials import SecretStore as _S
        from mirto.gui.review import ReviewView

        cartella = tempfile.TemporaryDirectory()
        self.addCleanup(cartella.cleanup)
        os.environ["MIRTO_DATA_DIR"] = cartella.name
        segreti = _S()
        segreti._kr = None          # noqa: SLF001
        core = MirtoCore(_C(Path(cartella.name) / "c.yaml"), segreti, discover=lambda: [])
        self.addCleanup(core.stop)
        vista = ReviewView(core)
        self.addCleanup(vista.deleteLater)
        return vista

    def test_il_riquadro_dei_comandi_contiene_la_ricerca(self):
        from PySide6.QtWidgets import QFrame

        vista = self._vista()
        riquadro = vista.findChild(QFrame, "reviewPanel")
        self.assertIsNotNone(riquadro, "il riquadro dei comandi non si trova")
        for arnese, nome in ((vista.cam_combo, "la camera"), (vista.date_edit, "il giorno"),
                             (vista.folder_button, "la cartella"), (vista.info, "la riga di stato")):
            self.assertTrue(self._dentro(arnese, riquadro),
                            f"{nome} sta ancora fuori dal riquadro dei comandi")

    @staticmethod
    def _dentro(arnese, contenitore) -> bool:
        genitore = arnese.parent()
        while genitore is not None:
            if genitore is contenitore:
                return True
            genitore = genitore.parent()
        return False


if __name__ == "__main__":
    unittest.main()
