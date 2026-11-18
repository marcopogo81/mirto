# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""«Accendo la luce dal telefono»: le uscite comandate da Telegram.

Passo 4 del piano domotica. Vale per le luci di casa **e** per i relè dei nodi, che fino a oggi da
Telegram non si comandavano affatto: si poteva accendere la protezione, fermare tutto, chiedere una
foto, ma non accendere una luce.

**Non si apre nessuna porta nuova, e questo è il punto che vale rileggere.** La serratura di Telegram
è la nostra: ``authorized(user_id)`` controlla a ogni messaggio che chi scrive sia in un elenco
esplicito di ID. Le uscite si comandavano già — dentro un'azione — quindi qui non nasce un potere,
nasce una scorciatoia. E proprio perché è una scorciatoia, **i limiti che valgono per un'azione
devono valere anche qui**: una sirena accesa a mano consuma gli stessi tre minuti ogni mezz'ora, che
è una promessa scritta in `GOVERNANCE.md` fra le cose che non cambiano. Se il telefono non li
consumasse, i tre minuti non sarebbero più tre e nessuno se ne accorgerebbe.

Le due cose che un comando che **accende** deve fare diversamente da uno che guarda:

1. **non indovinare il bersaglio.** Cercare una camera per fare una foto può ripiegare sulla prima
   che c'è: se sbaglia, viene la foto della stanza sbagliata. Un'uscita no — se sbaglia, suona la
   sirena invece di accendersi la luce del portico, alle tre di notte;
2. **non dire «spento» quando non si sa.** Chi legge «spenta» ci crede e va a dormire.
"""
import os
import tempfile
import types
import unittest
from pathlib import Path

from mirto.actions.engine import (
    DURATA_A_MANO,
    SIREN_MAX_SECONDS,
    ActionEngine,
)
from mirto.actions.smarthome import Dispositivo
from mirto.config import Config
from mirto.credentials import SecretStore
from mirto.notify.telegram import ALIASES, BotHandlers, TelegramBot
from tests.test_smarthome_azioni import DomoticaFinta


def mem_secrets():
    s = SecretStore()
    s._kr = None        # noqa: SLF001
    return s


class NodoFinto:
    """Un nodo che segna cosa gli si chiede. Niente rete, niente firme: quelle hanno le loro prove."""

    def __init__(self, fallisce: bool = False):
        self.fallisce = fallisce
        self.accesi: list = []
        self.fermato = 0

    def activate(self, comando, durata, *_a, **_k):
        if self.fallisce:
            raise RuntimeError("il nodo non risponde")
        self.accesi.append((comando, durata))
        return {}

    def stop_all(self):
        self.fermato += 1
        return {}


class ConUnNodoEUnaCasa(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        os.environ["MIRTO_CONFIG_DIR"] = self.tmp.name
        os.environ["MIRTO_DATA_DIR"] = self.tmp.name
        self.cfg = Config(Path(self.tmp.name) / "config.yaml")
        self.cfg.set("actions.nodes", [{"id": "n1", "name": "Ingresso", "url": "https://x"}])
        self.casa = DomoticaFinta(dispositivi=[
            Dispositivo("luce_portico", "Luce del portico", tipo="luce",
                        argomento_stato="s/p", argomento_comando="c/p"),
            Dispositivo("movimento", "Sensore del corridoio", tipo="sensore", argomento_stato="s/m"),
        ])
        self.nodo = NodoFinto()
        self.ora = [1000.0]
        core = types.SimpleNamespace(
            config=self.cfg, secrets=mem_secrets(), domotica=self.casa,
            notifier=types.SimpleNamespace(notify_text=lambda *_a: None))
        self.engine = ActionEngine(core, client_factory=lambda nd, s: self.nodo,
                                   clock=lambda: self.ora[0])
        self.addCleanup(self.engine.stop)
        self.engine.reload()
        # Quello che il nodo dichiara di avere: è da qui che nascono i tipi, e con i tipi i limiti.
        self.engine.node_commands["n1"] = {"luci": "light", "sirena": "siren"}
        self.engine.clients["n1"] = self.nodo


class LElencoDelleUsciteTest(ConUnNodoEUnaCasa):

    def test_ci_sono_i_rele_del_nodo_e_le_luci_di_casa(self):
        nomi = [v["nome"] for v in self.engine.uscite()]
        self.assertIn("luci", nomi, "manca un relè del nodo")
        self.assertIn("Luce del portico", nomi, "manca una luce di casa")

    def test_un_sensore_non_e_un_uscita(self):
        """Un sensore di movimento non si accende: offrirlo è promettere una cosa che non si fa."""
        self.assertNotIn("Sensore del corridoio", [v["nome"] for v in self.engine.uscite()])

    def test_l_elenco_dice_dove_sta_ogni_cosa(self):
        dove = {v["nome"]: v["dove"] for v in self.engine.uscite()}
        self.assertEqual(dove["luci"], "Ingresso")
        self.assertEqual(dove["Luce del portico"], "casa")

    def test_non_dice_spento_quando_non_lo_sa(self):
        """La seconda regola del file. ``acceso`` a ``None`` resta ``None`` fino in fondo.

        Il nodo non ha ancora detto cos'ha attivo, e la luce di casa non ha ancora parlato: in
        entrambi i casi non si sa. Scrivere «spenta» è una bugia a cui qualcuno crede.
        """
        for v in self.engine.uscite():
            self.assertIsNone(v["acceso"], f"{v['nome']}: dice di sapere una cosa che non sa")
        testo = self.engine.uscite_testo()
        self.assertIn("non lo so", testo)
        self.assertNotIn("spenta", testo)

    def test_quando_lo_sa_lo_dice(self):
        self.casa.dispositivi()[0].acceso = True
        self.engine.node_info["n1"] = {"attive": {"luci": 12.0}, "_at": self.ora[0]}
        testo = self.engine.uscite_testo()
        self.assertIn("accesa", testo)
        self.assertIn("12 s", testo, "non dice quanto resta accesa")

    def test_senza_niente_lo_dice_invece_di_dare_un_elenco_vuoto(self):
        self.cfg.set("actions.nodes", [])
        self.engine.node_commands.clear()
        vuota = ActionEngine(types.SimpleNamespace(
            config=self.cfg, secrets=mem_secrets(),
            notifier=types.SimpleNamespace(notify_text=lambda *_a: None)))
        self.addCleanup(vuota.stop)
        self.assertIn("niente da accendere", vuota.uscite_testo())


class CercareUnUscitaTest(ConUnNodoEUnaCasa):
    """La prima regola del file: **un comando che accende non indovina il bersaglio.**"""

    def test_il_nome_intero_la_trova(self):
        quale, perche = self.engine.cerca_uscita("Luce del portico")
        self.assertIsNotNone(quale, perche)
        self.assertEqual(quale["nome"], "Luce del portico")

    def test_non_importa_come_lo_scrivi(self):
        quale, _p = self.engine.cerca_uscita("  LUCE del   Portico ")
        self.assertIsNotNone(quale)

    def test_un_pezzo_di_nome_basta_se_e_di_una_sola(self):
        quale, _p = self.engine.cerca_uscita("portico")
        self.assertEqual(quale["nome"], "Luce del portico")

    def test_un_nome_che_non_c_e_non_accende_niente_e_mostra_l_elenco(self):
        quale, perche = self.engine.cerca_uscita("tapparella")
        self.assertIsNone(quale)
        self.assertIn("Non trovo", perche)
        self.assertIn("Luce del portico", perche, "e deve dire quali ci sono")

    def test_se_puo_essere_due_cose_non_ne_sceglie_una(self):
        """Il caso che fa male: «luce» è sia il relè del nodo sia la luce del portico.

        Scegliere la prima vorrebbe dire che un giorno, aggiungendo un dispositivo, lo stesso
        comando comincia ad accendere un'altra cosa senza che nessuno abbia cambiato niente.
        """
        quale, perche = self.engine.cerca_uscita("luc")
        self.assertIsNone(quale, "ha scelto da sé fra due uscite")
        self.assertIn("più di una cosa", perche)
        self.assertIn("luci", perche)
        self.assertIn("Luce del portico", perche)

    def test_il_nome_esatto_vince_su_quello_che_lo_contiene(self):
        """Se una si chiama «luci» e un'altra «luci del giardino», «luci» è senza dubbio la prima."""
        self.engine.node_commands["n1"] = {"luci": "light", "luci del giardino": "light"}
        quale, perche = self.engine.cerca_uscita("luci")
        self.assertIsNotNone(quale, perche)
        self.assertEqual(quale["nome"], "luci")


class AccendereESpegnereTest(ConUnNodoEUnaCasa):

    def test_una_luce_di_casa_si_accende_e_resta_accesa(self):
        """Dal telefono «accendi la luce del portico» vuol dire quello che vuol dire un interruttore.

        Un relè di un nodo non può: il nodo conta i suoi secondi e si spegne. Una luce smart sì, e
        fingere il contrario vorrebbe dire spegnerla addosso a chi l'ha accesa.
        """
        detto = self.engine.comanda_uscita("portico", True)
        self.assertEqual(self.casa.comandi, [("luce_portico", True)])
        self.assertIn("accesa", detto)
        self.assertIn("/spegni", detto, "non dice come si spegne")

    def test_e_si_spegne(self):
        self.engine.comanda_uscita("portico", True)
        detto = self.engine.comanda_uscita("portico", False)
        self.assertEqual(self.casa.comandi[-1], ("luce_portico", False))
        self.assertIn("spenta", detto)

    def test_con_i_secondi_la_luce_di_casa_si_spegne_da_se(self):
        detto = self.engine.comanda_uscita("portico", True, secondi=120)
        self.assertIn("120", detto)
        self.assertTrue(any(t.is_alive() for t in self.engine._timers),  # noqa: SLF001
                        "nessuno spegnerà quella luce")

    def test_un_rele_del_nodo_ha_sempre_una_durata(self):
        self.engine.comanda_uscita("luci", True)
        self.assertEqual(self.nodo.accesi, [("luci", DURATA_A_MANO)])

    def test_e_la_durata_si_puo_dire(self):
        self.engine.comanda_uscita("luci", True, secondi=10)
        self.assertEqual(self.nodo.accesi, [("luci", 10.0)])

    def test_spegnere_un_rele_ferma_il_nodo(self):
        self.engine.comanda_uscita("luci", False)
        self.assertEqual(self.nodo.fermato, 1)

    def test_se_il_nodo_non_risponde_lo_dice_invece_di_tacere(self):
        self.nodo.fallisce = True
        detto = self.engine.comanda_uscita("luci", True)
        self.assertIn("Non ho potuto", detto)
        self.assertIn("luci", detto)

    def test_se_la_domotica_non_risponde_lo_dice(self):
        self.casa.risponde = False
        detto = self.engine.comanda_uscita("portico", True)
        self.assertIn("Non ho potuto", detto)

    def test_accendere_a_mano_non_fa_ripartire_un_trigger(self):
        """La luce accesa da Telegram l'ha accesa **Mirto**, quindi non richiama Mirto.

        Senza il timbro, un'azione che ascolta «quando si accende quella luce» partirebbe perché
        l'hai accesa tu dal telefono — e non è quello che vuol dire «quando si accende».
        """
        self.engine.comanda_uscita("portico", True)
        with self.engine._lock:                                  # noqa: SLF001
            self.assertEqual(self.engine._smart_nostri.get("smart:luce_portico"),  # noqa: SLF001
                             self.ora[0], "il comando a mano non ha lasciato il timbro")


class ILimitiValgonoAncheDalTelefonoTest(ConUnNodoEUnaCasa):
    """La promessa di `GOVERNANCE.md`: tre minuti di sirena ogni mezz'ora, e non c'è scorciatoia.

    Un'azione che suona consuma il budget. Se il telefono non lo consumasse, chiunque potesse
    scrivere al bot avrebbe una sirena senza limiti — e il limite non è una preferenza, è il motivo
    per cui questo programma si può tenere acceso in un condominio.
    """

    def test_la_sirena_a_mano_e_tagliata_al_tetto_del_tipo(self):
        """Attenzione a cosa prova questa: **la taglia il budget, non il tetto.**

        Togliendo il `min(..., tetto)` questa prova passava comunque, perché per una sirena il
        budget dei tre minuti arriva allo stesso numero. Il verde veniva dall'altra garanzia — di
        nuovo il capitolo 31 del metodo. Il tetto del tipo serve davvero al **dissuasore**, che
        budget non ha: la prova che lo tiene è quella sotto, e senza di lei il `min` non era
        guardato da nessuno.
        """
        self.engine.comanda_uscita("sirena", True, secondi=9999)
        self.assertEqual(self.nodo.accesi[-1][1], float(SIREN_MAX_SECONDS))

    def test_il_dissuasore_a_mano_e_tagliato_ai_suoi_trenta_secondi(self):
        """Trenta secondi per volta è il limite del dissuasore, e dal telefono vale uguale.

        Il dissuasore non ha un budget a mezz'ora come la sirena: ha un tetto per singolo scatto, e
        quel tetto è l'unica cosa che lo tiene. Un dissuasore che urla cinque minuti di fila perché
        qualcuno ha scritto «/accendi dissuasore 300» è esattamente il genere di cosa che fa
        disinstallare Mirto a chi ha i vicini.
        """
        from mirto.actions.engine import DETERRENT_MAX_SECONDS

        self.engine.node_commands["n1"] = {"dissuasore": "deterrent"}
        self.engine.comanda_uscita("dissuasore", True, secondi=300)
        self.assertEqual(self.nodo.accesi[-1][1], float(DETERRENT_MAX_SECONDS))

    def test_e_consuma_il_budget_delle_azioni(self):
        self.engine.comanda_uscita("sirena", True, secondi=SIREN_MAX_SECONDS)
        detto = self.engine.comanda_uscita("sirena", True, secondi=30)
        self.assertIn("esauriti", detto, f"la seconda sirena è partita comunque: {self.nodo.accesi}")
        self.assertEqual(len(self.nodo.accesi), 1, "ha suonato due volte dentro la mezz'ora")

    def test_passata_la_mezz_ora_si_ricomincia(self):
        self.engine.comanda_uscita("sirena", True, secondi=SIREN_MAX_SECONDS)
        self.ora[0] += 1801
        self.engine.comanda_uscita("sirena", True, secondi=30)
        self.assertEqual(len(self.nodo.accesi), 2, "il budget non si è mai rinnovato")

    def test_una_luce_non_consuma_il_budget_della_sirena(self):
        for _ in range(5):
            self.engine.comanda_uscita("luci", True, secondi=100)
        detto = self.engine.comanda_uscita("sirena", True, secondi=30)
        self.assertNotIn("esauriti", detto, "le luci hanno mangiato il budget della sirena")


class IlBotCapisceIComandiTest(unittest.TestCase):
    """Il pezzo di Telegram: quali parole arrivano al nucleo, e cosa succede senza nome."""

    class Finti(BotHandlers):
        def __init__(self):
            self.log: list = []

        def uscite(self) -> str:
            self.log.append("elenco")
            return "ecco l'elenco"

        def comanda_uscita(self, nome, acceso, secondi=0) -> str:
            self.log.append((nome, acceso, secondi))
            return "fatto"

    class API:
        def __init__(self):
            self.calls: list = []

        def call(self, method, params=None, files=None, timeout=None):
            self.calls.append((method, params, files))
            return {"message_id": 1}

        def download_file(self, file_id):
            return b""

    def _bot(self):
        self.h = self.Finti()
        self.api = self.API()
        return TelegramBot(self.api, "-100", [111], self.h)

    def _manda(self, bot, testo: str) -> None:
        bot.handle_update({"message": {"chat": {"id": 5}, "from": {"id": 111}, "text": testo}})

    def test_accendi_con_un_nome(self):
        bot = self._bot()
        self._manda(bot, "/accendi luce del portico")
        self.assertEqual(self.h.log, [("luce del portico", True, 0)])

    def test_spegni_con_un_nome(self):
        bot = self._bot()
        self._manda(bot, "/spegni luci")
        self.assertEqual(self.h.log, [("luci", False, 0)])

    def test_i_secondi_in_fondo_sono_secondi_non_parte_del_nome(self):
        bot = self._bot()
        self._manda(bot, "/accendi sirena 20")
        self.assertEqual(self.h.log, [("sirena", True, 20.0)])

    def test_senza_nome_mostra_l_elenco_invece_di_accendere_qualcosa(self):
        """La prima regola del file, dal lato del bot: nessun bersaglio predefinito.

        E l'elenco compare proprio quando serve, senza un secondo comando da imparare.
        """
        bot = self._bot()
        self._manda(bot, "/accendi")
        self.assertEqual(self.h.log, ["elenco"])

    def test_anche_in_inglese(self):
        bot = self._bot()
        self._manda(bot, "/on porch light")
        self._manda(bot, "/off porch light")
        self.assertEqual(self.h.log, [("porch light", True, 0), ("porch light", False, 0)])
        self.assertEqual(ALIASES["/on"], "/accendi")

    def test_un_estraneo_non_accende_niente(self):
        """La serratura è quella di Telegram, e questo comando non la scavalca."""
        bot = self._bot()
        bot.handle_update({"message": {"chat": {"id": 5}, "from": {"id": 999},
                                       "text": "/accendi sirena"}})
        self.assertEqual(self.h.log, [], "un utente non autorizzato ha comandato un'uscita")

class OgniComandoEScrittoDoveSiCercaTest(unittest.TestCase):
    """Un comando che non è scritto da nessuna parte non esiste per chi usa Mirto.

    E i posti dove si cerca sono **quattro**, non uno: la risposta di `/aiuto` del bot, il menu dei
    comandi che il bot si imposta da sé (in italiano e in inglese), la finestra *Aiuto → Comandi
    Telegram* dentro l'app, e l'appendice della guida nelle due lingue.

    Questo guardiano è nato da una dimenticanza vera: scrivendo il passo 4 avevo messo `/accendi` in
    tre di quei posti e scordato il quarto, la finestra dentro l'app, perché l'elenco è scritto in un
    **altro file**. Sono due testi diversi per due momenti diversi — uno lo leggi su Telegram,
    l'altro davanti al computer — quindi tenerne due è giusto; dimenticarne uno no. Invece di
    ricordarmelo, lo chiede una prova: i comandi li legge **dal codice che li esegue**.
    """

    @staticmethod
    def comandi_eseguiti() -> set:
        """I comandi che il bot sa davvero eseguire, letti dal suo smistatore.

        Si leggono dal codice e non da un elenco scritto a mano: un elenco a mano è il quinto posto
        da ricordarsi di aggiornare, cioè lo stesso difetto in una prova.
        """
        import ast  # noqa: PLC0415
        import pathlib  # noqa: PLC0415

        from mirto.notify.telegram import ALIASES  # noqa: PLC0415

        sorgente = pathlib.Path("mirto/notify/telegram.py").read_text(encoding="utf-8")
        albero = ast.parse(sorgente)
        fuori: set = set()
        for n in ast.walk(albero):
            if not (isinstance(n, ast.FunctionDef) and n.name == "_handle_message"):
                continue
            for dentro in ast.walk(n):
                # «cmd == "/x"» e «cmd in ("/x", "/y")»
                if not isinstance(dentro, ast.Compare) or ast.unparse(dentro.left) != "cmd":
                    continue
                for c in dentro.comparators:
                    pezzi = c.elts if isinstance(c, (ast.Tuple, ast.List, ast.Set)) else [c]
                    fuori |= {x.value for x in pezzi
                              if isinstance(x, ast.Constant) and str(x.value).startswith("/")}
        # Gli alias sono gli stessi comandi in inglese, e si cercano col nome inglese.
        return fuori | {a for a, vero in ALIASES.items() if vero in fuori}

    def test_il_guardiano_li_vede(self):
        comandi = self.comandi_eseguiti()
        self.assertGreater(len(comandi), 10, f"guardiano cieco: {comandi}")
        for atteso in ("/stato", "/ferma", "/accendi", "/spegni", "/on", "/off"):
            self.assertIn(atteso, comandi, "lo smistatore non si legge più come prima")

    # Comandi di servizio: non sono cose che si fanno, e non vanno in tutti gli elenchi. «/start» lo
    # manda Telegram da sé aprendo il bot; «/aiuto» e «/help» sono l'elenco stesso; «/inserisci» e
    # «/disinserisci» sono i secondi nomi di «/attiva» e «/disattiva», scritti fra parentesi lì dove
    # servono; «/snapshot» e «/outputs» sono alias comodi di cui non si fa pubblicità.
    DI_SERVIZIO = {"/start", "/help", "/aiuto", "/inserisci", "/disinserisci", "/snapshot",
                   "/outputs", "/id"}

    def _da_dire(self) -> tuple[set, set]:
        """I comandi da scrivere, divisi per lingua: ``(italiani, inglesi)``.

        La divisione non è pignoleria. `HELP` e la finestra dentro l'app sono **testi italiani** —
        l'inglese arriva dal catalogo delle traduzioni — quindi pretendere `/stop` dentro un testo
        italiano è sbagliato, e la prima stesura di questo guardiano lo pretendeva. Lo ha detto lui
        stesso fallendo su tredici comandi: dodici erano colpa sua, uno era un buco vero.
        """
        from mirto.notify.telegram import ALIASES  # noqa: PLC0415

        tutti = self.comandi_eseguiti() - self.DI_SERVIZIO
        inglesi = {c for c in tutti if c in ALIASES}
        return tutti - inglesi, inglesi

    def test_gli_italiani_stanno_nei_tre_posti_italiani(self):
        import pathlib  # noqa: PLC0415

        from mirto.gui.sistema import TELEGRAM_HELP  # noqa: PLC0415
        from mirto.notify.telegram import COMMANDS_IT, HELP  # noqa: PLC0415

        italiani, _en = self._da_dire()
        self.assertGreater(len(italiani), 8, "il guardiano non vede più i comandi")
        menu = {"/" + c for c, _d in COMMANDS_IT}
        guida = pathlib.Path("GUIDA_UTENTE.md").read_text(encoding="utf-8")
        for cmd in sorted(italiani):
            with self.subTest(comando=cmd):
                self.assertIn(cmd, HELP, "manca nella risposta di /aiuto del bot")
                self.assertIn(cmd, menu, "manca nel menu dei comandi del bot")
                self.assertIn(cmd, TELEGRAM_HELP, "manca in Aiuto → Comandi Telegram")
                self.assertTrue(self._nella_tabella(cmd, guida),
                                "manca dalla tabella dell'appendice della guida")

    def test_gli_inglesi_stanno_nei_due_posti_inglesi(self):
        import pathlib  # noqa: PLC0415

        from mirto.notify.telegram import COMMANDS_EN  # noqa: PLC0415

        _it, inglesi = self._da_dire()
        self.assertGreater(len(inglesi), 5, "il guardiano non vede più gli alias inglesi")
        menu = {"/" + c for c, _d in COMMANDS_EN}
        guida = pathlib.Path("USER_GUIDE.md").read_text(encoding="utf-8")
        for cmd in sorted(inglesi):
            with self.subTest(comando=cmd):
                self.assertIn(cmd, menu, "manca nel menu inglese dei comandi del bot")
                self.assertTrue(self._nella_tabella(cmd, guida),
                                "manca dalla tabella dell'appendice della guida inglese")

    @staticmethod
    def _nella_tabella(cmd: str, guida: str) -> bool:
        """Il comando sta nella **tabella dell'appendice dei comandi**, non altrove nella guida.

        Due giri di mutazione per arrivare qui, e ciascuno ha tolto un modo di passare per sbaglio.
        Il primo: cercare il comando in tutta la guida lo trovava **nella prosa** — un esempio
        scritto a mano due righe sotto la tabella — quindi togliendo la riga della tabella il
        guardiano taceva. Il secondo: cercarlo in qualunque riga di tabella lo trovava in **un'altra
        tabella** della guida, in un altro capitolo. Chi cerca un comando va all'appendice: è lì che
        deve esserci, e solo quello conta.
        """
        righe, dentro = guida.splitlines(), False
        for r in righe:
            if r.startswith("## "):
                # L'appendice dei comandi, nelle due lingue: da qui al capitolo dopo.
                dentro = "Comandi Telegram" in r or "Telegram commands" in r
                continue
            if dentro and r.lstrip().startswith("|") and ("`" + cmd) in r:
                return True
        return False


if __name__ == "__main__":
    unittest.main()
