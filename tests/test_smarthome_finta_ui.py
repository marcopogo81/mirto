# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""La finestra della casa finta: i due versi si vedono davvero, non solo nei registri.

Questa finestra esiste per una ragione sola — guardare i due movimenti nello stesso istante — e sono
quelli che si provano qui: il bottone che gira l'interruttore si deve vedere **fuori**, sul broker; e
un comando che arriva da fuori si deve vedere **dentro**, nella riga e nel diario. Se una di queste
due si rompe, la finestra non serve più a niente e tanto valeva leggere i messaggi a mano.
"""
import json
import os
import time
import unittest

from mirto.actions import mqtt
from mirto.actions.smarthome_finta import (
    ESPHOME,
    LUCE,
    MALEDUCATO,
    PREFISSO_ZIGBEE,
    SENSORE,
    ZIGBEE,
    CasaFinta,
    DispositivoFinto,
)


class LaFinestraTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            from PySide6.QtWidgets import QApplication
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"senza PySide6 non c'è finestra da provare ({exc})") from exc
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from mirto.gui.smarthome_finta_ui import FinestraCasaFinta

        self.casa = CasaFinta([
            DispositivoFinto("luce_scale", "Luce delle scale", LUCE, ESPHOME),
            DispositivoFinto("luce_cucina", "Luce della cucina", LUCE, ZIGBEE),
            DispositivoFinto("movimento", "Movimento in corridoio", SENSORE, ZIGBEE),
        ]).avvia()
        self.addCleanup(self.casa.ferma)
        self.finestra = FinestraCasaFinta(self.casa)
        self.addCleanup(self.finestra.deleteLater)
        self.visto: dict[str, bytes] = {}
        self.spia = mqtt.Cliente("127.0.0.1", self.casa.porta, nome="spia",
                                 su_messaggio=lambda a, c, _r: self.visto.__setitem__(a, c)).collega()
        self.addCleanup(self.spia.chiudi)
        self.spia.iscrivi("#")
        time.sleep(0.2)

    def _macina(self, secondi: float = 1.5, finche=lambda: True) -> None:
        """Fa girare Qt e il tempo insieme: i segnali arrivano dal filo del broker."""
        fine = time.monotonic() + secondi
        while time.monotonic() < fine and finche():
            self.app.processEvents()
            time.sleep(0.02)
        self.app.processEvents()

    def _diario(self) -> str:
        return self.finestra.diario.toPlainText()

    def _bottone(self, identificativo: str):
        return self.finestra._righe[identificativo][1]      # noqa: SLF001

    def _stato(self, identificativo: str) -> str:
        return self.finestra._righe[identificativo][0].text()   # noqa: SLF001

    # --- la finestra mostra la casa -------------------------------------------
    def test_mostra_una_riga_per_dispositivo(self):
        self.assertEqual(set(self.finestra._righe), {"luce_scale", "luce_cucina", "movimento"})  # noqa: SLF001

    def test_dice_la_porta_vera_del_broker(self):
        # è la sola cosa da copiare in Mirto: se qui comparisse una porta sbagliata, la finestra
        # manderebbe a sbattere chi la usa
        from PySide6.QtWidgets import QLabel  # noqa: PLC0415

        testi = [e.text() for e in self.finestra.findChildren(QLabel)]
        self.assertTrue(any(f"127.0.0.1:{self.casa.porta}" in t for t in testi),
                        f"la porta {self.casa.porta} non compare in nessuna scritta della finestra")

    def test_un_sensore_non_si_accende_si_fa_passare_qualcuno(self):
        # un sensore non lo si comanda: chiamare quel bottone «Accendi» sarebbe una bugia
        self.assertIn("passa", self._bottone("movimento").text().lower())
        self.assertNotIn("accendi", self._bottone("movimento").text().lower())

    # --- il verso che va fuori -------------------------------------------------
    def test_il_bottone_si_vede_sul_broker(self):
        self._bottone("luce_scale").click()
        self._macina(finche=lambda: self.visto.get("luce_scale/light/luce_scale/state") != b"ON")
        self.assertEqual(self.visto.get("luce_scale/light/luce_scale/state"), b"ON")
        self.assertEqual(self._stato("luce_scale"), "acceso")
        self.assertIn("Mirto lo deve vedere cambiare", self._diario())

    def test_il_bottone_cambia_nome_e_rigira(self):
        self._bottone("luce_scale").click()
        self._macina(0.3)
        self.assertEqual(self._bottone("luce_scale").text(), "Spegni")
        self._bottone("luce_scale").click()
        self._macina(finche=lambda: self.visto.get("luce_scale/light/luce_scale/state") != b"OFF")
        self.assertEqual(self._stato("luce_scale"), "spento")

    def test_il_sensore_che_rileva_si_vede_sul_broker(self):
        # è il trigger del piano: passa qualcuno in corridoio e Mirto deve poterlo sapere
        self._bottone("movimento").click()
        argomento = f"{PREFISSO_ZIGBEE}/Movimento in corridoio"
        self._macina(finche=lambda: json.loads(self.visto.get(argomento, b"{}")) != {"state": "ON"})
        self.assertEqual(json.loads(self.visto[argomento]), {"state": "ON"})
        self.assertEqual(self._stato("movimento"), "rileva")

    # --- il verso che viene dentro --------------------------------------------
    def test_un_comando_da_fuori_si_vede_nella_riga_e_nel_diario(self):
        self.spia.pubblica("luce_scale/light/luce_scale/command", "ON")
        self._macina(finche=lambda: self._stato("luce_scale") != "acceso")
        self.assertEqual(self._stato("luce_scale"), "acceso")
        self.assertIn("Mirto ha comandato", self._diario())

    def test_la_finestra_distingue_chi_ha_girato_l_interruttore(self):
        # se le due righe si somigliassero, guardare la finestra non direbbe se Mirto sta funzionando:
        # «→» è una mano qui, «←» è un comando arrivato da Mirto
        self._bottone("luce_cucina").click()
        self._macina(finche=lambda: "→" not in self._diario())
        self.assertIn("→", self._diario())
        self.spia.pubblica(f"{PREFISSO_ZIGBEE}/Luce della cucina/set", json.dumps({"state": "OFF"}))
        self._macina(finche=lambda: "←" not in self._diario())
        diario = self._diario()
        self.assertIn("←", diario)
        self.assertLess(diario.index("→"), diario.index("←"), "il diario va in ordine di tempo")

    # --- aggiungere e togliere ------------------------------------------------
    def test_aggiungere_un_dispositivo_lo_mette_in_casa_e_sul_broker(self):
        self.finestra.nome.setText("Luce del portico")
        self.finestra.marca.setCurrentIndex([self.finestra.marca.itemData(i)
                                             for i in range(self.finestra.marca.count())].index(ESPHOME))
        self.finestra._aggiungi()       # noqa: SLF001
        self._macina(finche=lambda: "homeassistant/light/luce_del_portico/config" not in self.visto)
        dentro = json.loads(self.visto["homeassistant/light/luce_del_portico/config"])
        self.assertEqual(dentro["name"], "Luce del portico")
        self.assertIn("luce_del_portico", self.finestra._righe)      # noqa: SLF001
        self.assertEqual(self.finestra.nome.text(), "", "il campo si deve svuotare")

    def test_due_dispositivi_con_lo_stesso_nome_non_si_sovrascrivono(self):
        for _ in range(2):
            self.finestra.nome.setText("Luce doppia")
            self.finestra._aggiungi()   # noqa: SLF001
        self._macina(0.3)
        self.assertEqual(sorted(i for i in self.finestra._righe if "doppia" in i),  # noqa: SLF001
                         ["luce_doppia", "luce_doppia_2"])

    def test_un_nome_vuoto_non_aggiunge_niente(self):
        quanti = len(self.finestra._righe)      # noqa: SLF001
        self.finestra.nome.setText("   ")
        self.finestra._aggiungi()               # noqa: SLF001
        self.assertEqual(len(self.finestra._righe), quanti)     # noqa: SLF001

    def test_togliere_un_dispositivo_lo_leva_dalla_finestra_e_dall_elenco(self):
        self.finestra._togli("luce_cucina")     # noqa: SLF001
        self._macina(0.4)
        self.assertNotIn("luce_cucina", self.finestra._righe)    # noqa: SLF001
        elenco = json.loads(self.visto.get(f"{PREFISSO_ZIGBEE}/bridge/devices", b"[]"))
        self.assertEqual([v["friendly_name"] for v in elenco], ["Movimento in corridoio"])
        self.assertIn("deve sparire anche da lì", self._diario())

    def test_un_maleducato_aggiunto_dalla_finestra_lo_dice_in_chiaro(self):
        # chi prova e non lo sa resta a chiedersi perché Mirto non lo trova: va detto lì, non in guida
        self.finestra.nome.setText("Luce strana")
        self.finestra.marca.setCurrentIndex([self.finestra.marca.itemData(i)
                                             for i in range(self.finestra.marca.count())].index(MALEDUCATO))
        self.finestra._aggiungi()       # noqa: SLF001
        self.assertIn("ascolto grezzo", self._diario())

    # --- chiudere --------------------------------------------------------------
    def test_chiudere_la_finestra_spegne_la_casa(self):
        # il broker sta dentro la finestra: se restasse in piedi, la porta resterebbe occupata
        porta = self.casa.porta
        self.finestra.close()
        self._macina(0.3)
        with self.assertRaises(mqtt.ErroreMqtt):
            mqtt.Cliente("127.0.0.1", porta).collega(secondi=1.0)


if __name__ == "__main__":
    unittest.main()
