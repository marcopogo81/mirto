# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Si accende la luce delle scale e parte la musica: il cambio di stato come trigger.

Passo 3 del piano domotica, e il **terzo mattone che ascolta** — fino a ieri erano due, la camera e
il microfono. È anche il verso che nessun concorrente ha: Frigate e compagnia comandano sé stessi,
nessuno dei quattro *reagisce* alla casa, perché nessuno ha un modello di azioni.

**Le due cose che questo file esiste per non far succedere**, e che non si vedono leggendo il codice:

1. **La prima lettura non è un cambio.** Quando Mirto si collega, il broker gli riconsegna in un
   colpo lo stato ritenuto di tutti i dispositivi. Senza memoria del prima, accendere Mirto la sera
   con la luce delle scale accesa farebbe partire la musica di benvenuto — e chi lo vede non pensa
   «sarà il messaggio ritenuto», pensa che Mirto sia impazzito.
2. **Un cambio che ha fatto Mirto non richiama Mirto.** Un'azione accende una luce, la luce annuncia
   il suo stato nuovo, un trigger «quando si accende» riparte: nel caso migliore una sorpresa, nel
   peggiore un giro che si morde la coda. Due azioni incrociate e la casa lampeggia tutta la notte.

Nessuna delle due si vede provando a mano una volta: la prima si vede solo riavviando con una luce
già accesa, la seconda solo quando due azioni si incrociano.
"""
import os
import tempfile
import time
import types
import unittest
from pathlib import Path

import numpy as np

from mirto.actions.blocchi import (
    ACCESO,
    CAMERA,
    DOMOTICA,
    SPENTO,
    Blocco,
    blocco_scatta,
    e_un_cambio_di_stato,
)
from mirto.actions.engine import ECO_NOSTRO, ActionEngine
from mirto.actions.smarthome import CambioStato, Dispositivo, chiave_di
from mirto.config import Config
from mirto.credentials import SecretStore
from mirto.events import Event
# Il finto sta in un posto solo: il passo 2 l'ha scritto, il passo 3 lo usa. Due copie
# divergono, e una delle due resta indietro sul contratto — è già successo.
from tests.test_smarthome_azioni import DomoticaFinta

ATTESA = 3.0


def mem_secrets():
    s = SecretStore()
    s._kr = None        # noqa: SLF001
    return s


def aspetta(cosa, timeout: float = 5.0) -> bool:
    fine = time.monotonic() + timeout
    while time.monotonic() < fine:
        if cosa():
            return True
        time.sleep(0.02)
    return bool(cosa())


class IlMattoneCheAscoltaTest(unittest.TestCase):
    """Il blocco riconosce il cambio giusto, e **solo** quello."""

    def test_riconosce_il_verso_che_gli_e_stato_chiesto(self):
        b = Blocco(tipo=DOMOTICA, soggetti=[ACCESO], dispositivo="smart:luce_scale")
        self.assertTrue(blocco_scatta(b, CambioStato("smart:luce_scale", "Luce", True)))
        self.assertFalse(blocco_scatta(b, CambioStato("smart:luce_scale", "Luce", False)))

    def test_un_blocco_senza_dispositivo_ascolta_tutti(self):
        """Come `fonti` vuoto vuol dire «qualunque camera»: chi ha una lampadina sola non scelga."""
        b = Blocco(tipo=DOMOTICA, soggetti=[ACCESO, SPENTO])
        self.assertTrue(blocco_scatta(b, CambioStato("smart:a", "A", True)))
        self.assertTrue(blocco_scatta(b, CambioStato("smart:b", "B", False)))

    def test_il_dispositivo_sbagliato_non_lo_accende(self):
        b = Blocco(tipo=DOMOTICA, soggetti=[ACCESO], dispositivo="smart:luce_scale")
        self.assertFalse(blocco_scatta(b, CambioStato("smart:presa_tv", "Presa", True)))

    def test_una_camera_non_si_accende_su_un_cambio_di_casa(self):
        """E al contrario. Il controllo è esplicito nei due versi, non affidato a una coincidenza.

        Se dipendesse dal fatto che «acceso» non è anche un obiettivo riconoscibile da una camera,
        basterebbe che un giorno qualcuno aggiunga un obiettivo con quel nome.
        """
        cam = Blocco(tipo=CAMERA, soggetti=["person", "acceso"], confidenza=0.1)
        self.assertFalse(blocco_scatta(cam, CambioStato("smart:a", "A", True)))
        dom = Blocco(tipo=DOMOTICA, soggetti=[ACCESO, "person"])
        self.assertFalse(blocco_scatta(dom, Event("c", "Cam", 0.9, np.zeros((4, 4, 3), np.uint8))))

    def test_si_riconosce_un_cambio_per_quello_che_e_non_annusando_i_campi(self):
        self.assertTrue(e_un_cambio_di_stato(CambioStato("smart:a", "A", True)))
        self.assertFalse(e_un_cambio_di_stato(Event("c", "Cam", 0.9, np.zeros((4, 4, 3), np.uint8))))
        finto = types.SimpleNamespace(dispositivo="smart:a", acceso=True)
        self.assertFalse(e_un_cambio_di_stato(finto), "basta somigliare per essere creduto")

    def test_un_cambio_e_una_notizia_certa(self):
        """Non è un riconoscimento: non c'è una confidenza da superare."""
        ev = CambioStato("smart:a", "A", True)
        self.assertEqual(ev.score, 1.0)
        self.assertEqual(ev.targets(), {"acceso"})
        self.assertEqual(CambioStato("smart:a", "A", False).targets(), {"spento"})


class IlMotoreCheReagisceAllaCasaTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        os.environ["MIRTO_CONFIG_DIR"] = self.tmp.name
        os.environ["MIRTO_DATA_DIR"] = self.tmp.name
        self.cfg = Config(Path(self.tmp.name) / "config.yaml")
        self.casa = DomoticaFinta()
        self.avvisi: list = []
        self.partite: list = []
        self.core = types.SimpleNamespace(
            config=self.cfg, secrets=mem_secrets(), domotica=self.casa,
            notifier=types.SimpleNamespace(notify_text=self.avvisi.append))
        self.engine = ActionEngine(self.core)
        self.addCleanup(self.engine.stop)

    def _azione(self, soggetti, dispositivo="smart:luce_scale", quando="sempre", fa="smart:cassa"):
        """Il trigger nei «blocchi», il corpo nei «passi»: è così che è scritta un'azione oggi.

        Scrivendo queste prove avevo messo anche il corpo nei blocchi, e il motore le rifiutava tutte
        con «serve il nodo, oppure un pezzo che dica dove si fa». Aveva ragione lui: `_passi()` legge
        `passi`, i blocchi li legge solo il trigger — la migrazione dei blocchi al corpo è una cosa
        a metà, dichiarata a metà nel codice («quando l'interfaccia li scriverà»), e il passo 3 non è
        il posto dove finirla di nascosto.
        """
        self.cfg.set("actions.items", [
            {"name": "Benvenuto", "kind": "generic", "mode": "auto", "action": "accendi",
             "quando": quando, "duration": 2,
             "passi": [{"comando": "accendi", "dispositivo": fa}],
             "blocchi": [{"tipo": DOMOTICA, "soggetti": soggetti, "dispositivo": dispositivo}]},
        ])
        self.engine.reload()
        self.engine.start()
        self.addCleanup(self.engine.stop)

    def _accensioni(self) -> int:
        """Quante volte l'azione ha **acceso** qualcosa.

        Non «quanti comandi»: un'azione che accende per due secondi manda due comandi, il secondo è
        lo spegnimento automatico, e contandoli tutti una partenza sola sembra due. È il capitolo 28
        del metodo in un vestito nuovo — si aspetta il fatto, non il numero di messaggi — e queste
        due prove ci sono cascate.
        """
        return len([c for c in self.casa.comandi if c[1]])

    def test_il_motore_si_mette_in_ascolto_della_casa(self):
        self._azione([ACCESO])
        self.assertTrue(self.casa.ascoltatori, "nessuno ascolta la domotica")

    def test_e_smette_quando_si_ferma(self):
        self._azione([ACCESO])
        self.engine.stop()
        self.assertEqual(self.casa.ascoltatori, [], "resta attaccato dopo lo stop")

    def test_fermarsi_non_muore_se_la_domotica_e_strana(self):
        """Uno `stop()` che solleva lascia i timer vivi, e pezzi di catena partono a Mirto chiuso.

        È il difetto che ho fatto io scrivendo il passo 3: il motore ha cominciato a chiamare
        `non_ascoltare` allo stop, e tutto quello che gli si passava come domotica senza quel metodo
        faceva morire `stop()` **a metà** — prima di cancellare i timer. Quattordici prove sono
        cadute lì, e mi hanno fatto vedere la cosa vera: non è un problema dei finti, è che lo
        spegnimento non deve dipendere dai modi di un collaboratore.
        """
        self._azione([ACCESO])
        self.core.domotica = types.SimpleNamespace()  # una domotica che non sa fare niente
        self.engine._schedule(600, lambda: None)      # noqa: SLF001 - un timer da cancellare
        self.engine.stop()                            # non deve sollevare
        self.assertFalse(self.engine.has_activity(), "i timer sono rimasti vivi dopo lo stop")

    def test_la_luce_si_accende_e_l_azione_parte(self):
        """La frase del piano: *si accende la luce delle scale, parte la musica di benvenuto*."""
        self._azione([ACCESO])
        self.casa.dillo("luce_scale", "Luce delle scale", False)    # prima: la sappiamo spenta
        self.casa.dillo("luce_scale", "Luce delle scale", True)     # e adesso cambia
        self.assertTrue(aspetta(lambda: self.casa.comandi), f"non è partita: {self.casa.comandi}")
        self.assertEqual(self.casa.comandi[0][0], "cassa")

    def test_la_prima_lettura_non_fa_partire_niente(self):
        """**Trappola 1.** Mirto si collega, il broker gli riconsegna «la luce è accesa».

        Non è successo niente: è il messaggio ritenuto. Chi accende Mirto la sera con una luce
        accesa non deve sentirsi partire la musica di benvenuto, e non c'è modo di spiegarglielo.
        """
        self._azione([ACCESO])
        self.casa.dillo("luce_scale", "Luce delle scale", True)
        time.sleep(ATTESA)
        self.assertEqual(self.casa.comandi, [], "è partita sulla prima lettura")

    def test_lo_stesso_stato_ripetuto_non_e_un_cambio(self):
        """Un dispositivo che ripete «sono accesa» non si è acceso di nuovo.

        Questa prova l'ho scritta finta **due volte**, e tutt'e due le volte l'ha detto la mutazione.
        La prima la fermava «un'azione in corso non si riattiva»; sistemata quella, la fermava la
        **pausa fra due scatti** delle azioni «sempre». Due garanzie diverse che davano lo stesso
        verde, nessuna delle due quella in prova.

        La terza stesura usa `quando="protetta"` con la casa inserita, che è la strada del cancello
        senza pausa e senza tetto giornaliero, e aspetta che l'azione sia finita. Così l'unica cosa
        che può fermare il secondo scatto è il controllo che si vuole provare. La morale non è su
        questo controllo: **una prova verde non dice quale garanzia l'ha fatta passare**, e l'unico
        modo di saperlo è rompere quella che si crede di stare provando.
        """
        self._azione([ACCESO], quando="protetta")
        self.engine.set_armed(True)
        self.casa.dillo("luce_scale", "Luce delle scale", False)
        self.casa.dillo("luce_scale", "Luce delle scale", True)
        self.assertTrue(aspetta(lambda: self._accensioni()))
        self.assertTrue(aspetta(lambda: not self.engine.has_activity(), timeout=8),
                        "l'azione non è mai finita: la prova proverebbe un'altra cosa")
        quante = self._accensioni()
        for _ in range(3):
            self.casa.dillo("luce_scale", "Luce delle scale", True)
        time.sleep(ATTESA)
        self.assertEqual(self._accensioni(), quante, "ha ripartito su uno stato ripetuto")

    def test_non_lo_so_non_e_uno_stato(self):
        """``acceso`` a ``None`` vuol dire «non lo so»: **diverso da spento**, e non è un cambio.

        Anche questa l'ho scritta finta. Mandavo due «non lo so» di fila su un dispositivo di cui non
        si sapeva niente, e non partiva nulla nemmeno togliendo il controllo — perché la prima
        lettura non è un cambio e la seconda ripeteva lo stesso stato. Provava le altre due
        garanzie. Il caso vero è questo: si **sa** che è accesa, e poi arriva un «non lo so». Chi
        confonde «non lo so» con «spenta» fa partire l'azione dello spegnimento su un dispositivo
        che magari è ancora accesissimo.
        """
        self._azione([SPENTO])
        self.casa.dillo("luce_scale", "Luce delle scale", False)
        self.casa.dillo("luce_scale", "Luce delle scale", True)      # adesso la sappiamo accesa
        time.sleep(0.3)
        self.assertEqual(self._accensioni(), 0, "un blocco «spento» è partito su un'accensione")
        self.casa.dillo("luce_scale", "Luce delle scale", None)      # «non lo so» ≠ «spenta»
        time.sleep(ATTESA)
        self.assertEqual(self.casa.comandi, [], "ha creduto che «non lo so» volesse dire «spenta»")

    def test_un_cambio_fatto_da_mirto_non_richiama_mirto(self):
        """**Trappola 2.** L'azione accende la cassa, la cassa annuncia, e il trigger ripartirebbe.

        Qui l'azione ascolta e comanda **lo stesso dispositivo**: è il giro che si morde la coda
        nella sua forma più corta. Senza il timbro «questo l'ho fatto io», la casa lampeggia.

        **Con l'orologio in mano, e non per gusto.** La prima stesura aspettava col tempo vero e
        passava anche togliendo il timbro, perché l'eco arrivava mentre l'azione era ancora in
        corso — e un'azione in corso non si riattiva, per un'altra ragione. Fra «l'azione è finita»
        (un secondo) e «l'eco non vale più» (cinque) c'è una finestra di quattro secondi in cui il
        timbro è l'unica cosa che decide: con l'orologio finto ci si mette dentro in modo esatto,
        invece di sperare che la macchina di CI sia veloce.
        """
        ora = [1000.0]
        eng = self._engine_con_orologio(ora, dispositivo="smart:cassa", fa="smart:cassa")
        with eng._lock:                                      # noqa: SLF001
            eng._stato_smart["smart:cassa"] = False           # noqa: SLF001
        self.casa.dillo("cassa", "Cassa", True)
        self.assertTrue(aspetta(lambda: self._accensioni()), "non è partita nemmeno una volta")
        quante = self._accensioni()
        with eng._lock:                                      # noqa: SLF001
            timbro = eng._smart_nostri.get("smart:cassa")     # noqa: SLF001
        self.assertEqual(timbro, 1000.0, "il comando non ha lasciato il timbro «l'ho fatto io»")
        ora[0] += 2.0           # l'azione è finita (dura un secondo), l'eco vale ancora (cinque)
        self.assertTrue(aspetta(lambda: not eng.has_activity(), timeout=8),
                        "l'azione non è finita: la prova proverebbe un'altra cosa")
        with eng._lock:                                      # noqa: SLF001
            eng._stato_smart["smart:cassa"] = False           # noqa: SLF001
        self.casa.dillo("cassa", "Cassa", True)              # l'eco del nostro comando
        time.sleep(ATTESA)
        self.assertEqual(self._accensioni(), quante,
                         f"il giro si è morso la coda: {self.casa.comandi}")

    def test_nemmeno_lo_spegnimento_automatico_richiama_mirto(self):
        """L'altra metà della trappola 2, e scoperta solo guastando il codice.

        Mirto accende una luce per un po' e poi **la spegne lui**. Un'azione che ascolta «quando si
        spegne» partirebbe a ogni fine di ogni azione che accende quella luce: non è un caso
        inventato, è la coppia naturale di *«accendi la luce di cortesia»* più *«quando si spegne,
        rimetti la musica piano»*.

        Qui serve un po' di orologeria, e il motivo è istruttivo: se l'eco dell'**accensione** fosse
        ancora valida, sarebbe lei a fermare tutto e questa prova passerebbe anche senza il timbro
        dello spegnimento. Quindi si manda avanti l'orologio oltre i cinque secondi dell'eco
        *prima* che lo spegnimento automatico arrivi, così l'unica cosa che può fermare il secondo
        scatto è il timbro che si vuole provare.
        """
        ora = [1000.0]
        eng = self._engine_con_orologio(ora, dispositivo="smart:cassa", fa="smart:cassa",
                                        soggetti=[SPENTO])
        with eng._lock:                                      # noqa: SLF001
            eng._stato_smart["smart:cassa"] = True            # noqa: SLF001
        self.casa.dillo("cassa", "Cassa", False)             # si spegne: l'azione parte
        self.assertTrue(aspetta(lambda: self._accensioni()), "non è partita nemmeno una volta")
        quante = self._accensioni()
        ora[0] += ECO_NOSTRO + 5        # l'eco dell'accensione è scaduta, e l'azione è finita
        self.assertTrue(aspetta(lambda: any(not acceso for _d, acceso in self.casa.comandi), timeout=8),
                        f"lo spegnimento automatico non è arrivato: {self.casa.comandi}")
        with eng._lock:                                      # noqa: SLF001
            self.assertEqual(eng._smart_nostri.get("smart:cassa"), ora[0],   # noqa: SLF001
                             "lo spegnimento non ha lasciato il timbro «l'ho fatto io»")
            eng._stato_smart["smart:cassa"] = True            # noqa: SLF001
        self.casa.dillo("cassa", "Cassa", False)             # l'eco dello spegnimento
        time.sleep(ATTESA)
        self.assertEqual(self._accensioni(), quante,
                         f"è ripartita sul proprio spegnimento: {self.casa.comandi}")

    def _engine_con_orologio(self, ora, dispositivo="smart:luce_scale", fa="smart:cassa",
                             soggetti=None):
        """Un motore con l'orologio in mano, su un'azione «protetta» a casa inserita.

        «Protetta» e non «sempre» di proposito: la strada delle azioni «sempre» ha la pausa fra due
        scatti e il tetto giornaliero, e quelle due fermerebbero il secondo scatto da sé — dando
        verde a una prova che non sta provando niente. È già successo due volte in questo file.
        """
        core = types.SimpleNamespace(
            config=self.cfg, secrets=mem_secrets(), domotica=self.casa,
            notifier=types.SimpleNamespace(notify_text=self.avvisi.append))
        eng = ActionEngine(core, clock=lambda: ora[0])
        self.addCleanup(eng.stop)
        self.cfg.set("actions.items", [
            {"name": "Benvenuto", "kind": "generic", "mode": "auto", "action": "accendi",
             "quando": "protetta", "duration": 1,
             "passi": [{"comando": "accendi", "dispositivo": fa}],
             "blocchi": [{"tipo": DOMOTICA, "soggetti": soggetti or [ACCESO],
                          "dispositivo": dispositivo}]},
        ])
        eng.reload()
        eng.start()
        eng.set_armed(True)
        return eng

    def test_passato_il_tempo_dell_eco_un_cambio_vero_si_sente(self):
        """Il timbro non è una pensione: chi tocca l'interruttore dopo deve essere sentito.

        Se il timbro non scadesse, un dispositivo comandato una volta da Mirto non farebbe mai più
        scattare niente — che è un difetto peggiore di quello che il timbro risolve.
        """
        ora = [1000.0]
        core = types.SimpleNamespace(
            config=self.cfg, secrets=mem_secrets(), domotica=self.casa,
            notifier=types.SimpleNamespace(notify_text=self.avvisi.append))
        eng = ActionEngine(core, clock=lambda: ora[0])
        self.addCleanup(eng.stop)
        self.cfg.set("actions.items", [
            {"name": "Benvenuto", "kind": "generic", "mode": "auto", "action": "accendi",
             "quando": "sempre", "duration": 2,
             "passi": [{"comando": "accendi", "dispositivo": "smart:cassa"}],
             "blocchi": [{"tipo": DOMOTICA, "soggetti": [ACCESO], "dispositivo": "smart:luce_scale"}]},
        ])
        eng.reload()
        eng.start()
        with eng._lock:                                     # noqa: SLF001
            eng._stato_smart["smart:luce_scale"] = False     # noqa: SLF001
            eng._smart_nostri["smart:luce_scale"] = ora[0]   # noqa: SLF001
        self.casa.dillo("luce_scale", "Luce delle scale", True)
        time.sleep(0.3)
        self.assertEqual(self.casa.comandi, [], "ha risposto all'eco")
        with eng._lock:                                     # noqa: SLF001
            eng._stato_smart["smart:luce_scale"] = False     # noqa: SLF001
        ora[0] += ECO_NOSTRO + 1
        self.casa.dillo("luce_scale", "Luce delle scale", True)
        self.assertTrue(aspetta(lambda: self.casa.comandi, timeout=4),
                        "passato il tempo dell'eco, un cambio vero non si sente più")

    def test_l_avviso_non_grida_all_intrusione_per_una_lampadina(self):
        """Difetto trovato scrivendo il passo 3: l'avviso era uno solo, e diceva «Intrusione».

        Con la frase di prima, accendere la luce delle scale mandava sul telefono «🚨 Intrusione
        confermata (Luce delle scale)». Un allarme per una lampadina è il modo più veloce di
        insegnare a qualcuno che gli avvisi di Mirto non vanno letti — e il giorno che ce n'è uno
        vero, non lo legge.
        """
        self._azione([ACCESO], quando="protetta")
        self.engine.set_armed(True)
        self.casa.dillo("luce_scale", "Luce delle scale", False)
        self.casa.dillo("luce_scale", "Luce delle scale", True)
        self.assertTrue(aspetta(lambda: self.avvisi), f"non ha avvisato: {self.avvisi}")
        detto = " ".join(self.avvisi)
        self.assertNotIn("ntrusione", detto, f"grida all'intrusione per una luce: {detto}")
        self.assertIn("Luce delle scale", detto, "e non dice nemmeno cosa è cambiato")
        self.assertIn("Benvenuto", detto, "né quale azione è partita")

    def test_il_tempo_dell_eco_e_corto(self):
        """La prova di sopra non può cadere se il tempo diventa enorme: usa la costante stessa.

        Me l'ha detto la mutazione — portando `ECO_NOSTRO` a mille miliardi di secondi, quella prova
        passava tranquilla, perché sposta l'orologio di `ECO_NOSTRO + 1`. Quindi il numero va
        guardato per quello che è: un timbro che non scade quasi mai vuol dire che un dispositivo
        comandato una volta da Mirto non fa più scattare niente, per sempre, e nessuno capirebbe
        perché. Un annuncio MQTT torna in meno di un decimo di secondo.
        """
        self.assertGreater(ECO_NOSTRO, 0.5, "troppo corto: l'eco del nostro comando passerebbe")
        self.assertLess(ECO_NOSTRO, 30.0,
                        "troppo lungo: un dispositivo comandato da Mirto resterebbe sordo")


class ILimitiValgonoAncheQuiTest(unittest.TestCase):
    """Un'azione che scatta da una lampadina **non è più libera** di una che scatta da una camera.

    È la ragione per cui il cambio di stato entra dal cancello che c'era già invece di averne uno
    suo: gli orari, la pausa fra due scatti, il tetto giornaliero e la protezione sono scritti una
    volta e valgono per tutti. Un secondo cancello sarebbe un posto dove quelle promesse non
    arrivano, e nessuno se ne accorgerebbe fino al giorno che serve.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        os.environ["MIRTO_CONFIG_DIR"] = self.tmp.name
        os.environ["MIRTO_DATA_DIR"] = self.tmp.name
        self.cfg = Config(Path(self.tmp.name) / "config.yaml")
        self.casa = DomoticaFinta()
        self.core = types.SimpleNamespace(
            config=self.cfg, secrets=mem_secrets(), domotica=self.casa,
            notifier=types.SimpleNamespace(notify_text=lambda *_a: None))
        self.engine = ActionEngine(self.core)
        self.addCleanup(self.engine.stop)

    def _azione(self, quando: str) -> None:
        self.cfg.set("actions.items", [
            {"name": "Benvenuto", "kind": "generic", "mode": "auto", "action": "accendi",
             "quando": quando, "duration": 2,
             "passi": [{"comando": "accendi", "dispositivo": "smart:cassa"}],
             "blocchi": [{"tipo": DOMOTICA, "soggetti": [ACCESO], "dispositivo": "smart:luce_scale"}]},
        ])
        self.engine.reload()
        self.engine.start()

    def _accendi_la_luce(self) -> None:
        self.casa.dillo("luce_scale", "Luce delle scale", False)
        self.casa.dillo("luce_scale", "Luce delle scale", True)

    def test_un_azione_protetta_non_scatta_a_casa_disinserita(self):
        self._azione("protetta")
        self.engine.set_armed(False)
        self._accendi_la_luce()
        time.sleep(ATTESA)
        self.assertEqual(self.casa.comandi, [], "ha scattato con la protezione spenta")

    def test_e_scatta_a_casa_inserita(self):
        self._azione("protetta")
        self.engine.set_armed(True)
        self._accendi_la_luce()
        self.assertTrue(aspetta(lambda: self.casa.comandi), "non scatta nemmeno inserita")

    def test_un_azione_sempre_scatta_anche_disinserita(self):
        """Ed è il caso normale: la musica di benvenuto non è un antifurto."""
        self._azione("sempre")
        self.engine.set_armed(False)
        self._accendi_la_luce()
        self.assertTrue(aspetta(lambda: self.casa.comandi))

    def test_ferma_tutto_zittisce_anche_la_casa(self):
        """«Ferma tutto» mette in pausa un'ora: deve valere anche per chi scatta da una lampadina."""
        self._azione("sempre")
        self.engine.stop_all()
        self._accendi_la_luce()
        time.sleep(ATTESA)
        self.assertEqual([c for c in self.casa.comandi if c[0] == "cassa"], [],
                         "ha scattato dopo «Ferma tutto»")


if __name__ == "__main__":
    unittest.main()
