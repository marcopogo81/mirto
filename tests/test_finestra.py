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

    def test_la_striscia_degli_eventi_dice_che_si_apre_col_doppio_clic(self):
        """Un gesto che non si vede non esiste.

        Sopra la griglia il suggerimento del doppio clic c'era, sotto gli eventi no, e Marco il
        14/11: *«gli eventi partono ma bisogna fare doppio click e manca l'indicazione, pensavo non
        funzionasse»*. Chi non trova il gesto non conclude «si fa in un altro modo»: conclude che è
        rotto. E il suggerimento dice **cosa** si apre, perché cambia con la vista scelta.
        """
        win = self._finestra()
        self.assertIn("oppio clic", win.events_hint.text())
        win._set_events_mode("videos")       # noqa: SLF001
        self.assertIn("video", win.events_hint.text().lower())
        win._set_events_mode("photos")       # noqa: SLF001
        self.assertIn("foto", win.events_hint.text().lower())

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

    def test_eliminare_una_camera_senza_registrazioni_la_toglie_davvero(self):
        """Il difetto del 15/11, visto da Marco: «dice che non c'è niente da cancellare in bagno e
        lascia tutto lì com'è senza fare niente».

        Una camera che ha smesso di registrare da un po' è nell'elenco per via del suo cartellino,
        non dei video — che intanto sono stati cancellati da sé. Contare i file e fermarsi a zero
        voleva dire rifiutarsi di fare l'unica cosa che serviva. Qui si preme il pulsante per
        davvero, con la risposta «sì» già data, e si pretende che la voce sparisca.
        """
        from PySide6.QtWidgets import QMessageBox

        vista = self._vista()
        store = vista.core.recording.store
        store.cam_dir("bagno", "Bagno")          # solo il cartellino, nessun video
        vista._fill_cameras("bagno")             # noqa: SLF001
        self.assertIn("bagno", store.cameras())
        self.assertEqual(vista.cam_id, "bagno", "la camera di prova deve essere quella scelta")

        chiesto = []
        originale = QMessageBox.question
        QMessageBox.question = lambda *a, **k: (chiesto.append(a[2] if len(a) > 2 else ""),
                                                QMessageBox.StandardButton.Yes)[1]
        try:
            vista._svuota_camera()               # noqa: SLF001
        finally:
            QMessageBox.question = originale
        self.assertTrue(chiesto, "deve chiedere prima di cancellare, non fare in silenzio")
        self.assertNotIn("bagno", store.cameras(), "e dopo il sì la voce deve sparire")

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
