# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""La camera vede il cane e la luce delle scale si accende: i dispositivi di casa come uscite.

Passo 2 del piano domotica. Quello che l'utente riesce a fare quando questo file è verde: mettere
una lampadina smart in una catena di azioni, accanto al relè di un nodo, **senza sapere cos'è MQTT**.

La cosa che queste prove tengono, e che non si vede guardando il codice: **una lampadina smart non
si spegne da sé.** A un nodo si dice «accendi per trenta secondi» e il conto lo tiene lui, nel suo
firmware, anche se Mirto muore. A una lampadina si dice «accendi», e resta accesa. Quindi il tempo lo
tiene Mirto, e con quello la responsabilità di spegnerla — al timer e a «Ferma tutto». Il difetto che
ci sarebbe senza queste prove è il peggiore del suo genere: «Ferma tutto» cancella i timer, quindi
cancellerebbe anche lo spegnimento, e lascerebbe la casa illuminata facendo esattamente il contrario
di quello che dice il suo nome. Non si vedrebbe mai in sviluppo e si vedrebbe una notte sola.
"""
import os
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path

import numpy as np

from mirto.actions.blocchi import NODO, PREFISSI, SMART, dispositivo_di
from mirto.actions.engine import ActionEngine
from mirto.actions.smarthome import (
    ACCENDI,
    SPEGNI,
    Dispositivo,
    chiave_di,
    comandi_di,
    dispositivi_per_le_azioni,
)
from mirto.config import Config
from mirto.credentials import SecretStore
from mirto.events import Event


def mem_secrets():
    s = SecretStore()
    s._kr = None        # noqa: SLF001 - niente portachiavi vero nelle prove
    return s


# Quanto si aspetta per poter dire «il pezzo dopo non è partito». Un pezzo della catena dura **almeno
# un secondo** per costruzione, qualunque numero gli si scriva, e il pezzo dopo parte quando quello
# finisce: aspettare meno di così vuol dire scrivere una prova che passa anche a codice guasto. Due
# secondi sono il secondo più il margine per una macchina di CI carica.
ATTESA_SICURA = 2.0


def aspetta(cosa, timeout: float = 5.0) -> bool:
    fine = time.monotonic() + timeout
    while time.monotonic() < fine:
        if cosa():
            return True
        time.sleep(0.02)
    return bool(cosa())


class DomoticaFinta:
    """Tiene il conto di chi è stato acceso e spento, e in che ordine. Niente broker, niente rete.

    Serve a provare **l'engine**, non il protocollo: il protocollo ha le sue prove, contro la casa
    finta vera, e mescolare le due cose vorrebbe dire che un difetto dell'uno si nasconde nell'altro.
    """

    def __init__(self, dispositivi=None, risponde: bool = True):
        self.risponde = risponde
        self.esplode = False
        self.comandi: list[tuple[str, bool]] = []
        self.stato: dict[str, bool] = {}
        self._lucchetto = threading.Lock()
        self._dispositivi = list(dispositivi or [])

    def dispositivi(self):
        return list(self._dispositivi)

    def comanda(self, identificativo: str, acceso: bool) -> bool:
        if self.esplode:
            raise RuntimeError("il broker di casa è caduto")
        with self._lucchetto:
            self.comandi.append((identificativo, acceso))
            if self.risponde:
                self.stato[identificativo] = acceso
        return self.risponde

    def accesi(self) -> set:
        with self._lucchetto:
            return {k for k, v in self.stato.items() if v}


class IlPrefissoTest(unittest.TestCase):
    """``smart:`` sta accanto a ``nodo:`` e ``computer:``, e non è un tipo di blocco nuovo."""

    def test_il_prefisso_esiste_e_si_rilegge(self):
        self.assertIn(SMART, PREFISSI)
        self.assertEqual(dispositivo_di("smart:luce_scale"), (SMART, "luce_scale"))

    def test_si_scrive_in_un_modo_solo(self):
        self.assertEqual(chiave_di("luce_scale"), "smart:luce_scale")
        self.assertEqual(dispositivo_di(chiave_di("luce_scale")), (SMART, "luce_scale"))

    def test_le_configurazioni_di_prima_si_leggono_ancora(self):
        """La promessa del 23/10: un prefisso nuovo non è una migrazione."""
        self.assertEqual(dispositivo_di("ingresso"), (NODO, "ingresso"))
        self.assertEqual(dispositivo_di("nodo:ingresso"), (NODO, "ingresso"))

    def test_un_prefisso_che_non_conosciamo_resta_sconosciuto(self):
        # Meglio «non so dove» che mandare un comando al posto sbagliato.
        self.assertEqual(dispositivo_di("lampadina:salotto"), ("", ""))


class QuelloCheIlWizardVedeTest(unittest.TestCase):
    """I dispositivi di casa nella forma che il wizard già conosce, senza imparare niente."""

    def setUp(self):
        self.luce = Dispositivo("luce_scale", "Luce delle scale", tipo="luce",
                                argomento_stato="s/luce", argomento_comando="c/luce")
        self.presa = Dispositivo("presa_tv", "Presa della TV", tipo="presa",
                                 argomento_stato="s/presa", argomento_comando="c/presa")
        self.sensore = Dispositivo("movimento", "Sensore del corridoio", tipo="sensore",
                                   argomento_stato="s/mov")

    def test_un_sensore_non_si_offre_fra_le_uscite(self):
        """Un sensore di movimento non si accende: offrirlo è far costruire un pezzo morto.

        È la stessa regola per cui «questo computer» non è nell'elenco delle uscite — sa ascoltare e
        avvisare, non ha morsetti (23/10).
        """
        elenco = dispositivi_per_le_azioni([self.luce, self.presa, self.sensore])
        self.assertEqual([v["chiave"] for v in elenco], ["smart:luce_scale", "smart:presa_tv"])
        self.assertEqual(comandi_di(self.sensore), [])

    def test_ogni_dispositivo_sa_fare_due_cose(self):
        nomi = [c["nome"] for c in comandi_di(self.luce)]
        self.assertEqual(nomi, [ACCENDI, SPEGNI])

    def test_il_tipo_e_quello_che_la_striscia_parla_gia(self):
        """``light`` e ``generic``, non ``luce`` e ``presa``: il vocabolario dei nodi."""
        self.assertEqual({c["tipo"] for c in comandi_di(self.luce)}, {"light"})
        self.assertEqual({c["tipo"] for c in comandi_di(self.presa)}, {"generic"})

    def test_il_nome_del_comando_e_un_identificativo_non_una_scritta(self):
        """Finisce nella configurazione: deve restare uguale cambiando lingua.

        Quello che si legge sullo schermo è la «descrizione», che porta il nome del dispositivo e si
        traduce dove si mostra. Se un giorno il comando diventasse «Accendi la luce delle scale», le
        configurazioni scritte prima non si eseguirebbero più.
        """
        for c in comandi_di(self.luce):
            self.assertEqual(c["nome"], c["nome"].lower())
            self.assertNotIn(" ", c["nome"])
            self.assertIn("Luce delle scale", c["descrizione"])

    def test_ogni_comando_dice_a_che_dispositivo_appartiene(self):
        # Due dispositivi hanno comandi con lo stesso nome: senza questo, «accendi» sarebbe ambiguo.
        for c in comandi_di(self.presa):
            self.assertEqual(c["dispositivo"], "smart:presa_tv")


class UnaCatenaCheAccendeUnaLuceDiCasaTest(unittest.TestCase):
    """L'engine comanda un dispositivo di casa come comanda un relè, e la catena non cambia."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        os.environ["MIRTO_CONFIG_DIR"] = self.tmp.name
        os.environ["MIRTO_DATA_DIR"] = self.tmp.name
        self.cfg = Config(Path(self.tmp.name) / "config.yaml")
        self.casa = DomoticaFinta()
        self.avvisi: list[str] = []
        self.core = types.SimpleNamespace(
            config=self.cfg, secrets=mem_secrets(), domotica=self.casa,
            notifier=types.SimpleNamespace(notify_text=self.avvisi.append))
        self.engine = ActionEngine(self.core)
        self.addCleanup(self.engine.stop)
        self.ev = Event("cam1", "Ingresso", 0.8, np.zeros((10, 10, 3), np.uint8))

    def _azione(self, passi: list[dict], nome: str = "Cane") -> None:
        self.cfg.set("actions.items", [
            {"name": nome, "kind": "generic", "mode": "auto", "action": "accendi",
             "duration": 30, "passi": passi},
        ])
        self.engine.reload()
        self.engine.set_armed(True)

    def test_la_camera_vede_il_cane_e_la_luce_si_accende(self):
        """La frase del piano, eseguita: nessun nodo in giro, solo una lampadina."""
        self._azione([{"comando": ACCENDI, "dispositivo": "smart:luce_scale"}])
        self.engine.on_intrusion(self.ev)
        self.assertTrue(aspetta(lambda: self.casa.comandi), f"nessun comando: {self.casa.comandi}")
        self.assertEqual(self.casa.comandi[0], ("luce_scale", True))

    def test_spegni_spegne(self):
        self._azione([{"comando": SPEGNI, "dispositivo": "smart:luce_scale"}])
        self.engine.on_intrusion(self.ev)
        self.assertTrue(aspetta(lambda: self.casa.comandi))
        self.assertEqual(self.casa.comandi[0], ("luce_scale", False))

    def test_due_dispositivi_di_casa_in_fila_nell_ordine_scritto(self):
        """L'ordine è quello scritto, come con due nodi.

        Il secondo pezzo parte **dopo** il primo, e «dopo» vuol dire dopo la sua durata: ogni pezzo
        della catena dura quanto gli spetta, e un pezzo senza durata propria dura quanto l'azione.
        Qui si danno a entrambi quattro decimi, se no questa prova starebbe trenta secondi ad
        aspettare per provare una cosa che non è il tempo.
        """
        self._azione([{"comando": ACCENDI, "dispositivo": "smart:luce_scale", "durata": 0.4},
                      {"comando": ACCENDI, "dispositivo": "smart:presa_tv", "durata": 0.4}])
        self.engine.on_intrusion(self.ev)
        self.assertTrue(aspetta(lambda: len([c for c in self.casa.comandi if c[1]]) >= 2, timeout=8),
                        f"la catena non è arrivata in fondo: {self.casa.comandi}")
        accesi = [c[0] for c in self.casa.comandi if c[1]]
        self.assertEqual(accesi[:2], ["luce_scale", "presa_tv"])

    def test_con_la_durata_la_luce_si_spegne_da_se(self):
        """Il conto lo tiene Mirto, perché la lampadina non lo tiene."""
        self._azione([{"comando": ACCENDI, "dispositivo": "smart:luce_scale", "durata": 0.4}])
        self.engine.on_intrusion(self.ev)
        self.assertTrue(aspetta(lambda: self.casa.comandi))
        self.assertEqual(self.casa.accesi(), {"luce_scale"}, "doveva accendersi subito")
        self.assertTrue(aspetta(lambda: not self.casa.accesi(), timeout=4),
                        f"la luce è rimasta accesa: {self.casa.comandi}")
        self.assertEqual(self.casa.comandi[-1], ("luce_scale", False))

    def test_una_luce_accesa_da_un_pezzo_si_spegne_sempre(self):
        """**Non esiste un pezzo che accende e lascia accesa**, e questa prova è quella che lo tiene.

        Scrivendo il passo 2 credevo che una durata a zero volesse dire «lascia accesa», e ho
        scritto la prova che se lo aspettava. Sbagliata: nel modello della catena un pezzo ha
        **sempre** una durata — se non gliela dai tu è quella dell'azione, e se l'azione non ne ha è
        un secondo. Quindi la lampadina si spegne comunque, sempre, e va bene così: è il
        comportamento di una luce di cortesia, che è quello che si chiede. La cosa da non lasciar
        succedere mai è l'altra — una luce accesa e dimenticata.
        """
        self._azione([{"comando": ACCENDI, "dispositivo": "smart:luce_scale"}])
        spec = next(s for s in self.engine.specs if s.name == "Cane")
        for c in spec.catena():
            self.assertGreater(float(c["durata"]), 0,
                               "un pezzo senza durata lascerebbe una luce accesa per sempre")

    def test_ferma_tutto_spegne_le_luci_che_mirto_ha_accese(self):
        """Il difetto che non si vedrebbe mai in sviluppo, e una notte sola a casa di qualcuno.

        «Ferma tutto» cancella i timer. Fra i timer c'era lo spegnimento della luce. Se si fermasse
        lì, «Ferma tutto» lascerebbe la casa illuminata — il contrario del suo nome.
        """
        self._azione([{"comando": ACCENDI, "dispositivo": "smart:luce_scale", "durata": 600}])
        self.engine.on_intrusion(self.ev)
        self.assertTrue(aspetta(lambda: self.casa.accesi() == {"luce_scale"}))
        self.engine.stop_all()
        self.assertEqual(self.casa.accesi(), set(), f"luce rimasta accesa: {self.casa.comandi}")

    def test_ferma_tutto_lo_dice_anche_a_chi_non_ha_nodi(self):
        """Chi ha solo lampadine non deve leggere «nessun nodo configurato» dopo che le ha spente."""
        self._azione([{"comando": ACCENDI, "dispositivo": "smart:luce_scale", "durata": 600}])
        self.engine.on_intrusion(self.ev)
        self.assertTrue(aspetta(lambda: self.casa.accesi()))
        self.assertNotIn("nodo", self.engine.stop_all().lower())

    def test_ferma_tutto_non_spegne_quello_che_non_ha_acceso_lui(self):
        """Mirto spegne le sue, non fa il buio in casa: una luce accesa a mano non la ha accesa lui."""
        self.casa.stato["lampada_salotto"] = True
        self._azione([{"comando": ACCENDI, "dispositivo": "smart:luce_scale", "durata": 600}])
        self.engine.on_intrusion(self.ev)
        self.assertTrue(aspetta(lambda: "luce_scale" in self.casa.accesi()))
        self.engine.stop_all()
        self.assertEqual(self.casa.accesi(), {"lampada_salotto"})


class QuandoLaCasaNonRispondeTest(unittest.TestCase):
    """Un broker spento non deve far andare avanti la catena: mezza catena è peggio di niente."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        os.environ["MIRTO_CONFIG_DIR"] = self.tmp.name
        os.environ["MIRTO_DATA_DIR"] = self.tmp.name
        self.cfg = Config(Path(self.tmp.name) / "config.yaml")
        self.avvisi: list[str] = []
        self.ev = Event("cam1", "Ingresso", 0.8, np.zeros((10, 10, 3), np.uint8))

    def _engine(self, casa):
        core = types.SimpleNamespace(
            config=self.cfg, secrets=mem_secrets(), domotica=casa,
            notifier=types.SimpleNamespace(notify_text=self.avvisi.append))
        eng = ActionEngine(core)
        self.addCleanup(eng.stop)
        # **Un pezzo dura almeno un secondo**, qualunque numero gli si scriva: lo impone la catena,
        # e per due volte di fila mi ha fatto scrivere una prova finta. Prima con la durata presa
        # dall'azione (trenta secondi: il pezzo dopo partiva fuori dalla prova), poi con due decimi
        # scritti a mano, che diventano un secondo — e la prova aspettava un secondo esatto. Entrambe
        # le volte passava guastando il codice. Quindi: `ATTESA_SICURA` sta **sopra** quel secondo,
        # ed è il tempo oltre il quale, se la catena fosse andata avanti, si vedrebbe.
        self.cfg.set("actions.items", [
            {"name": "Cane", "kind": "generic", "mode": "auto", "action": "accendi", "duration": 1,
             "passi": [{"comando": ACCENDI, "dispositivo": "smart:luce_scale", "pausa": 0.1},
                       {"comando": ACCENDI, "dispositivo": "smart:presa_tv"}]},
        ])
        eng.reload()
        eng.set_armed(True)
        return eng

    def test_se_il_primo_passo_non_parte_il_secondo_non_si_fa(self):
        casa = DomoticaFinta(risponde=False)
        eng = self._engine(casa)
        eng.on_intrusion(self.ev)
        self.assertTrue(aspetta(lambda: casa.comandi))
        time.sleep(ATTESA_SICURA)
        self.assertEqual([c[0] for c in casa.comandi], ["luce_scale"],
                         "la catena è andata avanti dopo un passo non eseguito")

    def test_un_broker_che_esplode_non_porta_via_mirto(self):
        casa = DomoticaFinta()
        casa.esplode = True
        eng = self._engine(casa)
        eng.on_intrusion(self.ev)
        time.sleep(ATTESA_SICURA)
        self.assertEqual(casa.stato, {})

    def test_e_lo_dice_a_chi_sta_in_casa(self):
        """Il motivo si guarda **nell'avviso**, che è dove lo legge chi usa Mirto.

        Scrivendo questa prova l'avevo puntata su ``_perche_no``, e falliva: quel posto non è un
        archivio, è una buca delle lettere — ``_non_eseguita`` lo svuota mettendo il motivo nel
        messaggio. Guardare lì voleva dire provare un dettaglio interno invece della cosa vera, che è
        se Marco viene a sapere perché la luce non si è accesa.
        """
        casa = DomoticaFinta(risponde=False)
        eng = self._engine(casa)
        eng.on_intrusion(self.ev)
        # Si aspetta **il motivo**, non un messaggio: il primo che arriva è «avvio Cane», e contare
        # i messaggi qui vorrebbe dire guardare quello sbagliato (capitolo 28 del metodo).
        self.assertTrue(aspetta(lambda: any("non eseguita" in a for a in self.avvisi)),
                        f"non ha detto che l'azione non è partita: {self.avvisi}")
        detto = next(a for a in self.avvisi if "non eseguita" in a)
        self.assertIn("casa", detto.lower(),
                      f"l'avviso non dice che è un dispositivo di casa: {detto}")

    def test_senza_domotica_configurata_il_passo_non_parte(self):
        """Chi non ha un impianto smart e si ritrova un passo smart in configurazione: non esplode."""
        core = types.SimpleNamespace(config=self.cfg, secrets=mem_secrets(),
                                     notifier=types.SimpleNamespace(notify_text=self.avvisi.append))
        eng = ActionEngine(core)
        self.addCleanup(eng.stop)
        self.cfg.set("actions.items", [
            {"name": "Cane", "kind": "generic", "mode": "auto", "action": "accendi", "duration": 30,
             "passi": [{"comando": ACCENDI, "dispositivo": "smart:luce_scale"}]},
        ])
        eng.reload()
        eng.set_armed(True)
        eng.on_intrusion(self.ev)
        self.assertTrue(aspetta(lambda: self.avvisi), "non ha avvisato nessuno")


class ControLaCasaFintaVeraTest(unittest.TestCase):
    """E ora tutto insieme: l'engine, il client MQTT, il broker e una lampadina che si accende.

    Le prove di sopra usano una domotica finta, che è giusto — provano l'engine. Questa non prova
    l'engine: prova che i pezzi **combaciano**. Una catena di azioni scritta come la scriverebbe
    l'utente arriva fino a un messaggio MQTT su un broker vero (finto, ma vero di protocollo) e una
    lampadina cambia stato. Se qualcuno cambia la forma di un argomento, è questa che cade.
    """

    def setUp(self):
        from mirto.actions.smarthome import CasaSmart
        from mirto.actions.smarthome_finta import ESPHOME, LUCE, CasaFinta, DispositivoFinto

        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        os.environ["MIRTO_CONFIG_DIR"] = self.tmp.name
        os.environ["MIRTO_DATA_DIR"] = self.tmp.name
        self.finta = CasaFinta([
            DispositivoFinto("luce_scale", "Luce delle scale", LUCE, ESPHOME),
        ]).avvia()
        self.addCleanup(self.finta.ferma)
        self.cfg = Config(Path(self.tmp.name) / "config.yaml")
        self.vera = CasaSmart("127.0.0.1", self.finta.porta).collega()
        self.addCleanup(self.vera.chiudi)
        self.assertTrue(aspetta(lambda: self.vera.dispositivi()), "l'annuncio non è arrivato")
        self.core = types.SimpleNamespace(
            config=self.cfg, secrets=mem_secrets(),
            domotica=types.SimpleNamespace(comanda=self.vera.comanda,
                                           dispositivi=self.vera.dispositivi),
            notifier=types.SimpleNamespace(notify_text=lambda *_a: None))
        self.engine = ActionEngine(self.core)
        self.addCleanup(self.engine.stop)

    def test_dall_azione_alla_lampadina(self):
        d = self.vera.dispositivi()[0]
        self.assertTrue(d.comandabile, "l'annuncio non ha portato l'argomento dei comandi")
        self.cfg.set("actions.items", [
            {"name": "Cane", "kind": "generic", "mode": "auto", "action": "accendi", "duration": 30,
             "passi": [{"comando": ACCENDI, "dispositivo": chiave_di(d.identificativo)}]},
        ])
        self.engine.reload()
        self.engine.set_armed(True)
        self.engine.on_intrusion(Event("cam1", "Ingresso", 0.8, np.zeros((10, 10, 3), np.uint8)))
        finta = next(x for x in self.finta.elenco() if x.identificativo == "luce_scale")
        self.assertTrue(aspetta(lambda: finta.acceso, timeout=8),
                        "la lampadina della casa finta non si è accesa")

    def test_l_identificativo_vero_non_e_un_nome_pulito_e_va_bene(self):
        """Quello che un dispositivo annunciato si porta dietro, e che credevo fosse un nome corto.

        L'identificativo di un dispositivo che si annuncia **è il suo annuncio**
        (``annuncio:homeassistant/light/luce_scale/config``): brutto, pieno di due punti e di barre,
        e stabile — che è l'unica cosa che conta per una chiave scritta in configurazione. La prova
        che tiene il prefisso sta qui: ``smart:`` si stacca al **primo** due punti, quindi tutto il
        resto torna intero, barre comprese. Se un giorno si spezzasse all'ultimo, ogni dispositivo
        annunciato diventerebbe incomandabile senza che niente dica perché.
        """
        d = self.vera.dispositivi()[0]
        self.assertIn(":", d.identificativo, "cambiata la forma dell'identificativo: rileggi questa prova")
        self.assertEqual(dispositivo_di(chiave_di(d.identificativo)), (SMART, d.identificativo))

    def test_e_il_wizard_la_vedrebbe(self):
        """La stessa lampadina, nell'elenco che il wizard mostra: una cosa sola, non due."""
        elenco = dispositivi_per_le_azioni(self.vera.dispositivi())
        self.assertEqual(len(elenco), 1, f"il wizard ne vedrebbe {len(elenco)}: {elenco}")
        self.assertEqual(elenco[0]["nome"], "Luce delle scale")
        self.assertEqual(dispositivo_di(elenco[0]["chiave"])[0], SMART)


if __name__ == "__main__":
    unittest.main()
