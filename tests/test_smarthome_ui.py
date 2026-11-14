# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""La scheda «SmartHome» delle impostazioni, contro la casa finta.

Qui si prova la promessa della scheda, che è una sola: **l'utente non configura, riconosce.** Si
scrive un indirizzo, si preme un pulsante, e quello che c'è in casa compare con il suo nome e il suo
stato. Se questa catena si rompe in un punto qualunque, la scheda non serve a niente — e le prove
qui sotto rompono quel punto una volta per uno.

Anche quelle sul «non lo so»: mostrare *spento* un dispositivo di cui non si sa niente è una bugia
piccola a cui qualcuno crede, e poi si chiede perché l'azione non è partita.
"""
import json
import os
import time
import unittest

from mirto.actions import mqtt
from mirto.actions.smarthome_finta import (
    ESPHOME as F_ESPHOME,
    LUCE,
    MALEDUCATO,
    SENSORE,
    ZIGBEE as F_ZIGBEE,
    CasaFinta,
    DispositivoFinto,
)


class ConfigFinta:
    """La configurazione, quanto basta: ``get`` a chiavi puntate e ``set``."""

    def __init__(self, dentro=None):
        self.dentro = dentro or {}

    def get(self, chiave, ripiego=None):
        posto = self.dentro
        for pezzo in chiave.split("."):
            if not isinstance(posto, dict) or pezzo not in posto:
                return ripiego
            posto = posto[pezzo]
        return posto

    def set(self, chiave, valore):
        posto = self.dentro
        pezzi = chiave.split(".")
        for pezzo in pezzi[:-1]:
            posto = posto.setdefault(pezzo, {})
        posto[pezzi[-1]] = valore

    def save(self):
        self.salvata = getattr(self, "salvata", 0) + 1


class SegretiFinti:
    def __init__(self, dentro=None):
        self.dentro = dentro or {}

    def get(self, nome):
        return self.dentro.get(nome)

    def set(self, nome, valore):
        self.dentro[nome] = valore

    def delete(self, nome):
        self.dentro.pop(nome, None)


class LaSchedaTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            from PySide6.QtWidgets import QApplication
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"senza PySide6 non c'è scheda da provare ({exc})") from exc
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from PySide6.QtWidgets import QLineEdit

        from mirto.gui.smarthome_ui import SchedaSmartHome

        self.finta = CasaFinta([
            DispositivoFinto("luce_scale", "Luce delle scale", LUCE, F_ESPHOME),
            DispositivoFinto("luce_cucina", "Luce della cucina", LUCE, F_ZIGBEE),
            DispositivoFinto("movimento", "Movimento in corridoio", SENSORE, F_ZIGBEE),
            DispositivoFinto("luce_garage", "Luce del garage", LUCE, MALEDUCATO),
        ]).avvia()
        self.addCleanup(self.finta.ferma)
        self.cfg = ConfigFinta({"smarthome": {"enabled": True, "host": "", "port": 1883}})
        self.segreti = SegretiFinti()
        self.scheda = SchedaSmartHome(self.cfg, self.segreti, QLineEdit())
        self.addCleanup(self.scheda.stacca)
        self.addCleanup(lambda: self.scheda.domotica.stop())
        self.addCleanup(self.scheda.deleteLater)
        self.scheda.host.setText("127.0.0.1")
        self.scheda.porta.setValue(self.finta.porta)

    def _macina(self, secondi: float = 3.0, finche=lambda: True) -> None:
        fine = time.monotonic() + secondi
        while time.monotonic() < fine and finche():
            self.app.processEvents()
            time.sleep(0.02)
        self.app.processEvents()

    def _righe(self) -> list[str]:
        from PySide6.QtWidgets import QLabel

        fuori = []
        for r in range(self.scheda.griglia.rowCount()):
            voce = self.scheda.griglia.itemAtPosition(r, 0)
            if voce is not None and isinstance(voce.widget(), QLabel):
                fuori.append(voce.widget().text())
        return fuori

    def _aspetta_righe(self, quante: int, secondi: float = 4.0) -> list[str]:
        self._macina(secondi, lambda: len([r for r in self._righe() if "Luce" in r or "Movimento" in r]) < quante)
        righe = [r for r in self._righe() if "Luce" in r or "Movimento" in r]
        self.assertGreaterEqual(len(righe), quante, f"viste solo: {righe}")
        return righe

    def _bottone_di(self, pezzo: str):
        from PySide6.QtWidgets import QLabel, QPushButton

        for r in range(self.scheda.griglia.rowCount()):
            voce = self.scheda.griglia.itemAtPosition(r, 0)
            if voce is None or not isinstance(voce.widget(), QLabel) or pezzo not in voce.widget().text():
                continue
            comando = self.scheda.griglia.itemAtPosition(r, 3)
            return comando.widget() if comando and isinstance(comando.widget(), QPushButton) else None
        raise AssertionError(f"«{pezzo}» non è nell'elenco: {self._righe()}")

    def _stato_di(self, pezzo: str) -> str:
        from PySide6.QtWidgets import QLabel

        for r in range(self.scheda.griglia.rowCount()):
            voce = self.scheda.griglia.itemAtPosition(r, 0)
            if voce is None or not isinstance(voce.widget(), QLabel) or pezzo not in voce.widget().text():
                continue
            return self.scheda.griglia.itemAtPosition(r, 2).widget().text()
        raise AssertionError(f"«{pezzo}» non è nell'elenco: {self._righe()}")

    # --- la promessa della scheda ---------------------------------------------
    def test_prima_di_collegarsi_lo_dice_invece_di_mostrare_un_vuoto(self):
        # un elenco vuoto senza spiegazione fa pensare «non ho niente in casa» invece di «non sei
        # ancora collegato»: sono due cose diverse e portano a due gesti diversi
        self.assertIn("Collegati", "".join(self._righe()) + self.scheda.stato.text())

    def test_un_pulsante_e_i_dispositivi_compaiono(self):
        self.assertTrue(self.scheda.collega())
        righe = self._aspetta_righe(3)
        self.assertTrue(any("Luce delle scale" in r for r in righe))
        self.assertTrue(any("Luce della cucina" in r for r in righe), "lo Zigbee non si annuncia: "
                        "se manca, la via della marca non sta funzionando")
        self.assertTrue(any("Movimento" in r for r in righe))

    def test_il_maleducato_non_compare_finche_non_si_ascolta(self):
        self.scheda.collega()
        self._aspetta_righe(3)
        self.assertFalse(any("garage" in r for r in self._righe()))

    def test_senza_indirizzo_lo_dice_e_non_fa_finta_di_niente(self):
        self.scheda.host.setText("  ")
        self.assertFalse(self.scheda.collega())
        self.assertIn("indirizzo", self.scheda.stato.text().lower())

    def test_se_il_broker_non_c_e_lo_dice(self):
        self.scheda.porta.setValue(1)
        self.assertFalse(self.scheda.collega())
        self.assertTrue(self.scheda.stato.text(), "un fallimento muto è peggio di un fallimento")

    # --- lo stato che si muove ------------------------------------------------
    def test_lo_stato_si_muove_sotto_gli_occhi(self):
        self.scheda.collega()
        self._aspetta_righe(3)
        self.assertEqual(self._stato_di("Luce delle scale"), "spento")
        self.finta.cambia("luce_scale", True)
        self._macina(3.0, lambda: self._stato_di("Luce delle scale") != "acceso")
        self.assertEqual(self._stato_di("Luce delle scale"), "acceso")

    def test_il_pulsante_accende_davvero(self):
        self.scheda.collega()
        self._aspetta_righe(3)
        self._bottone_di("Luce delle scale").click()
        self._macina(3.0, lambda: not self.finta.dispositivi["luce_scale"].acceso)
        self.assertTrue(self.finta.dispositivi["luce_scale"].acceso)

    def test_uno_zigbee_si_comanda_nella_sua_lingua(self):
        self.scheda.collega()
        self._aspetta_righe(3)
        self._bottone_di("Luce della cucina").click()
        self._macina(3.0, lambda: not self.finta.dispositivi["luce_cucina"].acceso)
        self.assertTrue(self.finta.dispositivi["luce_cucina"].acceso)

    def test_un_sensore_non_ha_un_interruttore(self):
        # un pulsante che non fa niente è peggio di nessun pulsante
        self.scheda.collega()
        self._aspetta_righe(3)
        self.assertIsNone(self._bottone_di("Movimento"))

    # --- la riga «sei pronto» -------------------------------------------------
    def test_la_riga_sei_pronto_compare_solo_quando_c_e_qualcosa(self):
        self.assertFalse(self.scheda.scatola_pronto.isVisibleTo(self.scheda))
        self.scheda.collega()
        self._aspetta_righe(3)
        self.assertTrue(self.scheda.scatola_pronto.isVisibleTo(self.scheda))

    def test_la_riga_sei_pronto_fa_due_esempi_veri(self):
        # un rimando alla guida qui non serve: chi è arrivato fin qui vuole sapere adesso cosa farci
        self.scheda.collega()
        self._aspetta_righe(3)
        testo = self.scheda.riga_pronto.text()
        self.assertIn("Azioni", testo)
        self.assertIn("Telegram", testo)
        self.assertGreaterEqual(testo.count("«") + testo.count("“"), 2, "servono due esempi, non uno")

    # --- la terza via ----------------------------------------------------------
    def test_l_ascolto_grezzo_vuole_prima_il_collegamento(self):
        self.scheda.ascolta_btn.setChecked(True)
        self.scheda._ascolta()      # noqa: SLF001
        self.assertFalse(self.scheda.ascolta_btn.isChecked())
        self.assertIn("broker", self.scheda.stato.text().lower())

    def test_col_maleducato_adottato_compare_come_gli_altri(self):
        self.scheda.collega()
        self._aspetta_righe(3)
        self.scheda.ascolta_btn.setChecked(True)
        self.scheda._ascolta()      # noqa: SLF001
        self.finta.cambia("luce_garage", True)
        self._macina(3.0, lambda: not any("garage" in a for a, _ in self.scheda.casa.chi_parla()))
        self.scheda.casa.adotta("tuya/luce_garage/dps", "Luce del garage")
        self.scheda._ridisegna()    # noqa: SLF001
        self.assertTrue(any("Luce del garage" in r for r in self._righe()))
        self._bottone_di("Luce del garage").click()
        self._macina(3.0, lambda: self.finta.dispositivi["luce_garage"].acceso)
        self.assertFalse(self.finta.dispositivi["luce_garage"].acceso)

    # --- quello che si salva ---------------------------------------------------
    def test_i_valori_da_salvare_sono_quelli_scritti(self):
        self.scheda.host.setText("192.168.1.10")
        self.scheda.porta.setValue(8883)
        self.scheda.utente.setText("casa")
        valori = self.scheda.valori()
        self.assertEqual(valori["host"], "192.168.1.10")
        self.assertEqual(valori["port"], 8883)
        self.assertEqual(valori["username"], "casa")
        self.assertTrue(valori["enabled"])

    def test_i_dispositivi_adottati_si_ricordano_e_gli_altri_no(self):
        """Gli adottati si salvano perché nessuno li ritrova da solo. Gli altri no, apposta.

        Un dispositivo che si annuncia si rifà vedere a ogni avvio: salvarlo vorrebbe dire tenersi
        in configurazione un elenco che invecchia, e mostrare a vita una lampadina venduta un anno fa.
        """
        self.scheda.collega()
        self._aspetta_righe(3)
        self.assertEqual(self.scheda.valori()["adopted"], [])
        self.scheda.ascolta_btn.setChecked(True)
        self.scheda._ascolta()      # noqa: SLF001
        self.finta.cambia("luce_garage", True)
        self._macina(3.0, lambda: not any("garage" in a for a, _ in self.scheda.casa.chi_parla()))
        self.scheda._adottati.append({"topic": "tuya/luce_garage/dps", "name": "Luce del garage",
                                      "command_topic": "tuya/luce_garage/dps/set"})
        salvati = self.scheda.valori()["adopted"]
        self.assertEqual([v["topic"] for v in salvati], ["tuya/luce_garage/dps"])

    def test_riaprendo_le_impostazioni_gli_adottati_tornano(self):
        from PySide6.QtWidgets import QLineEdit

        from mirto.gui.smarthome_ui import SchedaSmartHome

        cfg = ConfigFinta({"smarthome": {
            "enabled": True, "host": "127.0.0.1", "port": self.finta.porta,
            "adopted": [{"topic": "tuya/luce_garage/dps", "name": "Luce del garage",
                         "command_topic": "tuya/luce_garage/dps/set"}]}})
        scheda = SchedaSmartHome(cfg, self.segreti, QLineEdit())
        self.addCleanup(scheda.stacca)
        self.addCleanup(scheda.deleteLater)
        scheda.host.setText("127.0.0.1")
        scheda.porta.setValue(self.finta.porta)
        self.assertTrue(scheda.collega())
        self.scheda = scheda
        righe = self._aspetta_righe(4)
        self.assertTrue(any("Luce del garage" in r for r in righe),
                        "chi è stato preso a orecchio non si deve riprendere a ogni avvio")

    # --- la password -----------------------------------------------------------
    def test_la_password_salvata_si_riusa_senza_mostrarla(self):
        """Il campo mostra i pallini, non la password: collegandosi si prende quella del portachiavi."""
        from PySide6.QtWidgets import QLineEdit

        from mirto.credentials import KEEP, SMARTHOME_PASSWORD
        from mirto.gui.smarthome_ui import SchedaSmartHome

        self.segreti.set(SMARTHOME_PASSWORD, "quella-vera")
        scheda = SchedaSmartHome(self.cfg, self.segreti, QLineEdit(KEEP))
        self.addCleanup(scheda.stacca)
        self.addCleanup(scheda.domotica.stop)
        self.addCleanup(scheda.deleteLater)
        scheda.host.setText("127.0.0.1")
        scheda.porta.setValue(self.finta.porta)
        self.assertTrue(scheda.collega())
        self.assertEqual(self.segreti.get(SMARTHOME_PASSWORD), "quella-vera",
                         "il segnaposto non deve sovrascrivere la password vera")
        self.assertEqual(scheda.casa.password, "quella-vera")

    def test_collegarsi_salva_subito(self):
        """Il difetto del 14/11, visto da Marco alla prima apertura.

        Il pulsante «Collegati e cerca» fa una cosa vera: compaiono le lampadine, e «Salva» sembra
        superfluo. Chi chiude la finestra se l'è persa tutta. È lo stesso difetto corretto sul nodo
        — *niente più «Applica»* — quindi collegarsi **è** salvare.
        """
        self.assertTrue(self.scheda.collega())
        self._aspetta_righe(3)
        self.assertEqual(self.cfg.get("smarthome.host"), "127.0.0.1")
        self.assertEqual(self.cfg.get("smarthome.port"), self.finta.porta)
        self.assertTrue(self.cfg.get("smarthome.enabled"),
                        "collegarsi è dire che la vuoi usare")
        self.assertGreaterEqual(getattr(self.cfg, "salvata", 0), 1, "e il file va scritto")

    def test_chiudere_la_scheda_non_stacca_la_casa(self):
        # la scheda guarda, non possiede: chiudendo le impostazioni la domotica resta viva, se no
        # il pannello «Sistema» la mostrerebbe spenta e le Azioni non potrebbero comandare niente
        self.assertTrue(self.scheda.collega())
        self._aspetta_righe(3)
        domotica = self.scheda.domotica
        self.addCleanup(domotica.stop)
        self.scheda.stacca()
        self.assertTrue(domotica.stato()["collegata"])
        self.assertGreaterEqual(len(domotica.dispositivi()), 3)


if __name__ == "__main__":
    unittest.main()
