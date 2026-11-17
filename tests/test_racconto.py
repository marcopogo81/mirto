# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Mirto si racconta sul broker: quali argomenti, quali carichi, ritenuto o no.

Passo 5 del piano domotica, e chiude **C-04** — l'unica priorità 1 del cancello del lancio mai fatta.

Qui non c'è né rete né broker: il `Racconto` riceve una funzione che pubblica, e queste prove le
passano un elenco in memoria. È voluto. La parte che si può sbagliare e che fa danno è **cosa si
dice e come**, e quella si prova meglio contro un elenco che contro un broker, dove un difetto di
protocollo e un difetto di contenuto si confondono. Il protocollo ha le sue prove, in
`test_mqtt.py`, contro byte scritti a mano dalla specifica.

**Le tre cose che questo file esiste per non far succedere**, tutte e tre sul *non* dire:

1. **Niente si pubblica finché non lo accendi.** `PRIVACY.md`, il 17/11, in due lingue: *«Mirto
   publishes nothing of its own on the broker today… if a future version does, this page will say so
   before it ships, and it will be off until you turn it on»*. È un impegno scritto davanti a chi
   legge, non una preferenza.
2. **Le istantanee sono un interruttore a parte, e anche quello spento.** Un JPEG su una bacheca
   condivisa lo vede qualunque cosa sia attaccata al broker.
3. **Un evento non è uno stato.** Ritenere un evento vorrebbe dire riconsegnarlo a chi si collega
   mezz'ora dopo, cioè far suonare un allarme per una cosa finita — lo stesso difetto che al passo 3
   faceva partire la musica di benvenuto all'avvio di Mirto.
"""
import json
import types
import unittest

from mirto.actions.racconto import (
    OFFLINE,
    ONLINE,
    PREFISSO,
    Racconto,
    argomenti_camera,
    argomenti_che_si_pestano,
    argomento_protezione,
    argomento_stato,
    carico_evento,
    pulisci_nome,
    testamento as il_testamento,   # il nome «test…» pytest lo raccoglierebbe come prova
)


def aspetta(cosa, timeout: float = 5.0) -> bool:
    import time

    fine = time.monotonic() + timeout
    while time.monotonic() < fine:
        if cosa():
            return True
        time.sleep(0.02)
    return bool(cosa())


def evento(**extra):
    base = {"id": "ev1", "camera_name": "Ingresso", "label": "person", "score": 0.912,
            "ts": 1700000000.0, "is_test": False, "detail": "", "species": "", "jpeg": None}
    base.update(extra)
    return types.SimpleNamespace(**base)


class Bacheca:
    """Un broker finto ridotto all'osso: tiene quello che gli si pubblica, nell'ordine."""

    def __init__(self, rompe: bool = False):
        self.rompe = rompe
        self.detti: list[tuple[str, bytes, bool]] = []

    def pubblica(self, argomento: str, carico: bytes, ritenuto: bool) -> bool:
        if self.rompe:
            raise OSError("il broker è caduto")
        self.detti.append((argomento, carico, ritenuto))
        return True

    def ultimo(self, argomento: str):
        for a, c, r in reversed(self.detti):
            if a == argomento:
                return c, r
        return None, None

    def argomenti(self) -> list[str]:
        return [a for a, _c, _r in self.detti]


class QualiArgomentiTest(unittest.TestCase):
    """Gli argomenti sono un contratto: chi scrive un'automazione li scrive a mano una volta."""

    def test_stanno_tutti_sotto_un_ramo_solo(self):
        """Così si disinstalla con una cancellazione e non si pesta niente di nessuno."""
        tutti = [argomento_stato(), argomento_protezione(),
                 *argomenti_camera("Ingresso").values()]
        for a in tutti:
            self.assertTrue(a.startswith(PREFISSO + "/"), f"{a} esce dal ramo di Mirto")

    def test_il_prefisso_si_puo_cambiare(self):
        """Su un broker di casa può esserci già qualcosa che si chiama «mirto»."""
        self.assertEqual(argomento_stato("casa/vigile"), "casa/vigile/status")
        self.assertEqual(argomenti_camera("Ingresso", "casa/vigile")["stato"],
                         "casa/vigile/ingresso/state")

    def test_un_nome_di_camera_non_rompe_l_argomento(self):
        """``/``, ``+`` e ``#`` su MQTT non sono lettere: uno spezza l'argomento, l'altro è un jolly."""
        for nome in ("Garage / retro", "Cam #2", "a+b", "Ingresso"):
            pulito = pulisci_nome(nome)
            for segno in "/+#":
                self.assertNotIn(segno, pulito, f"«{nome}» lascia passare {segno}")
            self.assertTrue(pulito, f"«{nome}» non dà nessun nome")

    def test_un_nome_fatto_solo_di_segni_da_comunque_un_argomento(self):
        # Meglio una parola brutta che un argomento con un livello vuoto.
        self.assertEqual(pulisci_nome("###"), "camera")
        self.assertEqual(pulisci_nome("   "), "camera")

    def test_lo_stesso_nome_da_sempre_lo_stesso_argomento(self):
        """Un argomento che cambia rompe le automazioni già scritte: deve essere una funzione."""
        self.assertEqual(pulisci_nome("Garage / retro"), pulisci_nome("Garage / retro"))
        self.assertEqual(pulisci_nome("  Ingresso  "), "ingresso")

    def test_due_camere_che_si_pestano_i_piedi_si_vedono(self):
        """Il difetto che nessuno capirebbe: due camere, un argomento, una delle due mente.

        Qualunque ripulitura butta via qualcosa, quindi *«Cam#2»* e *«Cam 2»* finiscono insieme e non
        c'è modo di evitarlo. Non si risolve di nascosto aggiungendo un numero — un argomento che
        cambia perché ne è comparso un altro rompe le automazioni scritte — si fa vedere a chi
        configura, che cambiare un nome lo sa fare in dieci secondi.
        """
        pestati = argomenti_che_si_pestano(["Cam#2", "Cam 2", "Ingresso"])
        self.assertEqual(list(pestati), ["cam_2"])
        self.assertEqual(sorted(pestati["cam_2"]), ["Cam 2", "Cam#2"])

    def test_e_quando_non_si_pestano_non_si_dice_niente(self):
        self.assertEqual(argomenti_che_si_pestano(["Ingresso", "Garage", "Cucina"]), {})

    def test_un_elenco_vuoto_non_fa_esplodere_niente(self):
        self.assertEqual(argomenti_che_si_pestano([]), {})
        self.assertEqual(argomenti_che_si_pestano(None), {})


class CosaEStatoECosaENotiziaTest(unittest.TestCase):
    """La distinzione che su MQTT conta più di tutte: ritenuto o no."""

    def setUp(self):
        self.b = Bacheca()
        self.r = Racconto(self.b.pubblica)

    def test_sono_vivo_resta_sul_broker(self):
        """Ritenuto, se no Home Assistant mostra «sconosciuto» ogni volta che si riavvia."""
        self.r.sono_qui()
        carico, ritenuto = self.b.ultimo(argomento_stato())
        self.assertEqual(carico, ONLINE)
        self.assertTrue(ritenuto, "lo stato non ritenuto si perde per chi arriva dopo")

    def test_la_protezione_resta_sul_broker(self):
        self.r.protezione(True)
        self.assertEqual(self.b.ultimo(argomento_protezione()), (b"ON", True))
        self.r.protezione(False)
        self.assertEqual(self.b.ultimo(argomento_protezione()), (b"OFF", True))

    def test_un_evento_non_resta_sul_broker(self):
        """**Il difetto peggiore di questo file, se mancasse.**

        Un evento ritenuto verrebbe riconsegnato a chi si collega mezz'ora dopo, e la sua automazione
        partirebbe per una cosa finita: una sirena alle tre di notte per un gatto passato a cena. È
        lo stesso difetto che al passo 3 faceva partire la musica di benvenuto all'avvio, visto dal
        verso di chi pubblica invece di quello di chi ascolta.
        """
        self.r.evento(evento())
        for argomento, _carico, ritenuto in self.b.detti:
            self.assertFalse(ritenuto, f"{argomento}: un evento ritenuto risuona a chi arriva dopo")

    def test_quando_si_chiude_lo_dice(self):
        self.r.me_ne_vado()
        self.assertEqual(self.b.ultimo(argomento_stato()), (OFFLINE, True))

    def test_il_testamento_dice_la_stessa_cosa(self):
        """Lo manda il broker se Mirto muore male: un morto non manda messaggi.

        Se il testamento dicesse un'altra cosa, o stesse su un altro argomento, dopo un crash Mirto
        resterebbe «online» per sempre e nessuno saprebbe che non c'è più.
        """
        arg, carico, ritenuto = il_testamento()
        self.assertEqual(arg, argomento_stato())
        self.assertEqual(carico, OFFLINE)
        self.assertTrue(ritenuto)

    def test_lo_stato_di_una_camera_resta(self):
        self.r.camera("Ingresso", True)
        self.assertEqual(self.b.ultimo(argomenti_camera("Ingresso")["stato"]), (b"ON", True))

    def test_una_camera_senza_nome_non_inventa_un_argomento(self):
        self.r.camera("", True)
        self.assertEqual(self.b.detti, [])


class UnaCameraNonRestaAccesaPerSempreTest(unittest.TestCase):
    """Il difetto che rende inutile un'integrazione: non sbaglia una volta, **mente sempre**.

    Uno stato ritenuto a ``ON`` resta ``ON``. Un evento è un istante, ma su MQTT non esiste un
    istante: esiste quello che hai detto per ultimo, e quello resta sul broker. Senza uno
    spegnimento programmato, Home Assistant mostrerebbe *«Ingresso: sta vedendo qualcosa»* da
    stasera fino al prossimo riavvio di Mirto.

    L'ho visto scrivendo il nucleo, non provandolo: pubblicavo ``ON`` all'evento e non c'era una riga
    da nessuna parte che lo rimettesse a ``OFF``. Qui il tempo è in mano alla prova — un
    programmatore finto — così non si aspetta nessun secondo vero e il margine è esatto.
    """

    def setUp(self):
        self.b = Bacheca()
        self.attesi: list = []
        self.r = Racconto(self.b.pubblica, programma=self._programma, quanto_dura=30)

    def _programma(self, secondi, cosa):
        t = types.SimpleNamespace(secondi=secondi, cosa=cosa, annullato=False)
        t.cancel = lambda: setattr(t, "annullato", True)
        self.attesi.append(t)
        return t

    def _scade(self, t) -> None:
        if not t.annullato:
            t.cosa()

    def test_accendendola_si_programma_lo_spegnimento(self):
        self.r.camera("Ingresso", True)
        self.assertEqual(len(self.attesi), 1, "nessuno spegnerà quella camera")
        self.assertEqual(self.attesi[0].secondi, 30)

    def test_e_quando_scade_si_spegne(self):
        self.r.camera("Ingresso", True)
        self._scade(self.attesi[0])
        self.assertEqual(self.b.ultimo(argomenti_camera("Ingresso")["stato"]), (b"OFF", True))

    def test_un_evento_nuovo_rimanda_lo_spegnimento_invece_di_aggiungerne_un_altro(self):
        """Come la luce di cortesia di un portone: non si accumulano i timer.

        Se si accumulassero, il primo scaduto spegnerebbe la camera mentre ci sta ancora passando
        qualcuno — e il secondo la riaccenderebbe dopo, dando a chi guarda un lampeggio che non
        corrisponde a niente.
        """
        self.r.camera("Ingresso", True)
        primo = self.attesi[0]
        self.r.camera("Ingresso", True)
        self.assertTrue(primo.annullato, "il primo timer è rimasto vivo")
        self.assertEqual(len(self.attesi), 2)
        self._scade(primo)
        self.assertEqual(self.b.ultimo(argomenti_camera("Ingresso")["stato"])[0], b"ON",
                         "un timer vecchio ha spento una camera che sta ancora vedendo qualcosa")

    def test_spegnerla_a_mano_non_programma_niente(self):
        self.r.camera("Ingresso", False)
        self.assertEqual(self.attesi, [], "ha programmato di spegnere una camera già spenta")

    def test_due_camere_hanno_due_timer_loro(self):
        self.r.camera("Ingresso", True)
        self.r.camera("Garage", True)
        self.assertEqual(len(self.attesi), 2)
        self.assertFalse(self.attesi[0].annullato, "una camera ha cancellato il timer dell'altra")

    def test_chiudendo_si_buttano_i_timer(self):
        """Un timer vivo tiene in piedi il programma che sta cercando di chiudersi."""
        self.r.camera("Ingresso", True)
        self.r.camera("Garage", True)
        self.r.basta()
        self.assertTrue(all(t.annullato for t in self.attesi), "sono rimasti timer vivi")

    def test_a_durata_zero_non_si_programma_niente(self):
        """Chi vuole lo stato «grezzo» lo può avere, e deve essere una scelta e non un difetto."""
        r = Racconto(self.b.pubblica, programma=self._programma, quanto_dura=0)
        r.camera("Ingresso", True)
        self.assertEqual(self.attesi, [])


class CosaDiceUnEventoTest(unittest.TestCase):

    def test_dice_cosa_dove_quando_e_quanto_sicuro(self):
        d = json.loads(carico_evento(evento()))
        self.assertEqual(d["id"], "ev1")
        self.assertEqual(d["camera"], "Ingresso")
        self.assertEqual(d["label"], "person")
        self.assertEqual(d["score"], 0.912)
        self.assertEqual(d["at"], 1700000000)

    def test_non_porta_il_fotogramma(self):
        """Il JPEG è un messaggio a parte, sotto un interruttore a parte.

        Chi accende «raccontami» vuole far accendere una luce quando passa qualcuno; non ha chiesto
        che le immagini di casa finiscano su una bacheca condivisa, e infilarle qui vorrebbe dire
        fargli dire sì a una cosa chiedendogliene un'altra.
        """
        grosso = b"\xff\xd8" + b"x" * 5000
        carico = carico_evento(evento(jpeg=grosso))
        self.assertLess(len(carico), 400, "il carico dell'evento è diventato grande come una foto")
        self.assertNotIn(b"\xff\xd8", carico)

    def test_e_json_valido_anche_con_un_nome_con_gli_accenti(self):
        d = json.loads(carico_evento(evento(camera_name="Cortile è buio", detail="vetro")))
        self.assertEqual(d["camera"], "Cortile è buio")
        self.assertEqual(d["detail"], "vetro")

    def test_i_campi_vuoti_non_si_scrivono(self):
        """Un campo vuoto in un'automazione è una condizione in più da scrivere per niente."""
        d = json.loads(carico_evento(evento()))
        self.assertNotIn("detail", d)
        self.assertNotIn("species", d)

    def test_i_nomi_dei_campi_sono_quelli_che_un_automazione_legge(self):
        """E non si traducono: una traduzione cambia il nome di un campo a chi ha già scritto l'automazione."""
        d = json.loads(carico_evento(evento()))
        self.assertEqual(sorted(d), ["at", "camera", "id", "label", "score", "test"])

    def test_un_evento_a_pezzi_non_fa_esplodere_niente(self):
        """Un evento di prova, o uno costruito a mano in una prova, non deve rompere il racconto."""
        d = json.loads(carico_evento(types.SimpleNamespace()))
        self.assertEqual(d["camera"], "")
        self.assertGreater(d["at"], 0, "senza data mette adesso, non zero")


class LeIstantaneeSonoUnaSceltaAParteTest(unittest.TestCase):
    """La seconda delle tre cose: un fotogramma su una bacheca condivisa non parte per sbaglio."""

    def test_di_serie_non_partono(self):
        b = Bacheca()
        r = Racconto(b.pubblica)
        self.assertFalse(r.istantanee, "le istantanee sono accese di serie")
        self.assertFalse(r.istantanea(evento(jpeg=b"\xff\xd8dati")))
        self.assertEqual(b.detti, [], "ha pubblicato un fotogramma senza che nessuno lo chiedesse")

    def test_accese_partono(self):
        b = Bacheca()
        r = Racconto(b.pubblica, istantanee=True)
        self.assertTrue(r.istantanea(evento(jpeg=b"\xff\xd8dati")))
        carico, ritenuto = b.ultimo(argomenti_camera("Ingresso")["istantanea"])
        self.assertEqual(carico, b"\xff\xd8dati")
        self.assertTrue(ritenuto, "l'ultima foto di una camera è uno stato, non una notizia")

    def test_il_controllo_sta_nel_racconto_non_in_chi_chiama(self):
        """Se l'interruttore lo guardasse chi chiama, basterebbe un posto nuovo che chiama.

        Questa prova non guarda un comportamento: guarda **dove** sta il controllo, perché è quello
        che decide se un domani le immagini possono scappare. Un posto nuovo che chiama
        «istantanea()» non deve poter pubblicare niente senza che l'impostazione sia accesa.
        """
        b = Bacheca()
        r = Racconto(b.pubblica)
        for _ in range(3):
            r.istantanea(evento(jpeg=b"\xff\xd8dati"))
        self.assertEqual(b.detti, [])

    def test_senza_camera_non_inventa_un_argomento(self):
        """Un evento senza nome di camera finirebbe su ``mirto/camera/snapshot``, cioè in un posto
        che non è di nessuno — e le foto di due camere senza nome si sovrascriverebbero a vicenda.

        Succede: un evento di prova, o uno costruito a mano, non ha un nome di camera. Il buco l'ha
        trovato la mutazione: la prova che c'era guardava solo `camera()`, non `istantanea()`.
        """
        b = Bacheca()
        r = Racconto(b.pubblica, istantanee=True)
        self.assertFalse(r.istantanea(evento(camera_name="", jpeg=b"\xff\xd8x")))
        self.assertEqual(b.detti, [])

    def test_senza_foto_non_pubblica_un_messaggio_vuoto(self):
        b = Bacheca()
        r = Racconto(b.pubblica, istantanee=True)
        self.assertFalse(r.istantanea(evento(jpeg=None)))
        self.assertFalse(r.istantanea(evento(jpeg=b"")))
        self.assertEqual(b.detti, [])

    def test_prende_la_foto_anche_se_e_una_funzione(self):
        """Un evento vero la fa su richiesta: non si tiene un JPEG in memoria per ogni evento."""
        b = Bacheca()
        r = Racconto(b.pubblica, istantanee=True)
        self.assertTrue(r.istantanea(evento(jpeg=lambda: b"\xff\xd8pigro")))
        self.assertEqual(b.ultimo(argomenti_camera("Ingresso")["istantanea"])[0], b"\xff\xd8pigro")


class UnBrokerCheCadeNonPortaViaUnAvvisoTest(unittest.TestCase):
    """La foto su Telegram parte comunque: il broker è un ascoltatore in più, non il destinatario."""

    def test_non_solleva_mai(self):
        r = Racconto(Bacheca(rompe=True).pubblica, istantanee=True)
        r.sono_qui()
        r.protezione(True)
        r.evento(evento())
        r.camera("Ingresso", False)
        self.assertFalse(r.istantanea(evento(jpeg=b"\xff\xd8x")))
        r.me_ne_vado()

    def test_e_lo_scrive_nel_registro(self):
        """Ingoiare un errore in silenzio vorrebbe dire un'integrazione che non funziona e non lo dice."""
        r = Racconto(Bacheca(rompe=True).pubblica)
        with self.assertLogs("mirto.actions.racconto", level="WARNING") as registro:
            r.protezione(True)
        self.assertTrue(any("pubblicare" in riga for riga in registro.output))


if __name__ == "__main__":
    unittest.main()


class BancoDelBrokerVero:
    """Il banco di prova, **senza prove**: broker vero, `Domotica` vero, un client spia.

    Sta per conto suo e non è un `TestCase` di proposito. Ereditando da un `TestCase` per
    riusarne il `setUp` si ereditano anche le sue prove, e si rifanno tutte una seconda volta
    sotto un altro nome: il 17/11 le sette prove della scoperta ne mostravano sedici, e nove
    erano le stesse di sopra rifatte. Costa tempo in CI e, peggio, una prova che cade compare
    con un nome che non è il suo.
    """

    @classmethod
    def setUpClass(cls):
        from mirto.actions.smarthome_finta import CasaFinta

        cls.CasaFinta = CasaFinta

    def setUp(self):
        import os
        import tempfile
        from pathlib import Path

        from mirto.actions.smarthome import Domotica
        from mirto.credentials import SecretStore

        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        os.environ["MIRTO_CONFIG_DIR"] = self.tmp.name
        os.environ["MIRTO_DATA_DIR"] = self.tmp.name
        from mirto.config import Config

        self.finta = self.CasaFinta([]).avvia()
        self.addCleanup(self.finta.ferma)
        self.cfg = Config(Path(self.tmp.name) / "config.yaml")
        segreti = SecretStore()
        segreti._kr = None                                  # noqa: SLF001
        self.dom = Domotica(self.cfg, segreti)
        self.addCleanup(self.dom.stop)

    def _accendi(self, **extra):
        dati = {"enabled": True, "host": "127.0.0.1", "port": self.finta.porta}
        dati.update(extra)
        self.cfg.set("smarthome", dati)
        return self.dom.riapri()

    def _spia(self, *argomenti):
        """Un client a parte, iscritto a quello che Mirto dice. Torna la lista dei messaggi."""
        from mirto.actions import mqtt

        arrivati: list = []
        c = mqtt.Cliente("127.0.0.1", self.finta.porta, nome="spia",
                         su_messaggio=lambda a, carico, rit: arrivati.append((a, carico, rit)))
        c.collega()
        self.addCleanup(c.chiudi)
        c.iscrivi(*argomenti)
        # **Si aspetta che l'iscrizione esista**, non si spera. Fra `iscrivi()` e il momento in cui
        # il broker l'ha registrata passa un pezzo di rete: pubblicando subito dopo, il messaggio si
        # perde una volta su tante, e quella volta in CI si liquida come «sarà stata la rete». È il
        # capitolo 28 del metodo in un vestito nuovo — qui il fatto da aspettare non è un valore, è
        # che il broker sappia a chi consegnare.
        broker = self.finta._mio_broker                      # noqa: SLF001
        for argomento in argomenti:
            atteso = argomento.replace("#", "x").replace("+", "x")
            self.assertTrue(aspetta(lambda a=atteso: broker.quanti_iscritti(a) > 0),
                            f"il broker non ha registrato l'iscrizione a {argomento}")
        return arrivati


class ContoIlBrokerVeroTest(BancoDelBrokerVero, unittest.TestCase):
    """E ora tutto insieme: `Domotica`, il client MQTT, un broker vero (finto di casa, vero di
    protocollo) e un altro client che ascolta quello che Mirto dice.

    Le prove di sopra provano **cosa** si dice, contro un elenco in memoria, e sono quelle che
    trovano i difetti di contenuto. Questa prova una cosa diversa e non meno importante: che i pezzi
    **combacino** — che il racconto nasca quando l'impostazione è accesa, che non nasca quando è
    spenta, e che un messaggio parta per davvero e arrivi a chi si è iscritto. Se qualcuno cambia la
    forma di un argomento o dimentica di passare il testamento, è questa che cade.
    """

    def test_spento_non_nasce_nessun_racconto(self):
        """La promessa scritta in PRIVACY, provata dal verso che conta: configurare un broker non
        basta a far parlare Mirto."""
        self.assertTrue(self._accendi(), "non si è collegata alla casa finta")
        self.assertIsNone(self.dom.racconto, "il racconto è nato senza che nessuno lo accendesse")

    def test_accesso_dice_subito_che_c_e_e_com_e_la_protezione(self):
        spia = self._spia("mirto/#")
        self.cfg.set("actions.armed", True)
        self.assertTrue(self._accendi(publish=True))
        self.assertIsNotNone(self.dom.racconto)
        self.assertTrue(aspetta(lambda: {a for a, _c, _r in spia} >= {"mirto/status", "mirto/armed"}),
                        f"non si è presentato: {spia}")
        detti = {a: c for a, c, _r in spia}
        self.assertEqual(detti["mirto/status"], b"online")
        self.assertEqual(detti["mirto/armed"], b"ON", "non dice com'è la protezione adesso")

    def test_chi_si_collega_dopo_trova_lo_stato_sul_broker(self):
        """È il punto dei messaggi ritenuti, e il motivo per cui Home Assistant non mostra
        «sconosciuto» appena si riavvia."""
        self.assertTrue(self._accendi(publish=True))
        # Si aspetta che lo stato sia **già sul broker**, non che l'oggetto che lo pubblica esista:
        # fra le due cose c'è una corsa, e il tardivo che si iscrive nel mezzo non trova il ritenuto
        # perché non è ancora stato scritto. Cadeva 1 volta su 30 circa, sempre in compagnia di altri
        # file di prova — cioè solo quando la macchina è carica, che è quando si guarda meno.
        presto = self._spia("mirto/status")
        self.assertTrue(aspetta(lambda: any(c == b"online" for _a, c, _r in presto)),
                        "lo stato non è mai arrivato sul broker")
        tardi = self._spia("mirto/status")
        self.assertTrue(aspetta(lambda: any(a == "mirto/status" for a, _c, _r in tardi)),
                        "chi arriva dopo non trova niente")
        self.assertEqual(tardi[0][1], b"online")
        self.assertTrue(tardi[0][2], "non è arrivato come ritenuto")

    def test_chiudendosi_lo_dice(self):
        self.assertTrue(self._accendi(publish=True))
        spia = self._spia("mirto/status")
        self.assertTrue(aspetta(lambda: spia))
        spia.clear()
        self.dom.stop()
        self.assertTrue(aspetta(lambda: any(c == b"offline" for _a, c, _r in spia)),
                        f"se ne è andato in silenzio: {spia}")

    def test_un_evento_arriva_a_chi_ascolta(self):
        self.assertTrue(self._accendi(publish=True))
        self.assertTrue(aspetta(lambda: self.dom.racconto is not None))
        spia = self._spia("mirto/event", "mirto/ingresso/event")
        self.dom.racconto.evento(evento())
        self.assertTrue(aspetta(lambda: len(spia) >= 2), f"l'evento non è arrivato: {spia}")
        for _a, carico, ritenuto in spia:
            self.assertEqual(json.loads(carico)["id"], "ev1")
            self.assertFalse(ritenuto)

    def test_il_testamento_e_dichiarato_al_broker(self):
        """La prova che non si può fare con un elenco in memoria: il testamento vive nel broker.

        Si guarda che il broker l'abbia **ricevuto** al collegamento, perché è lui che lo manderà, e
        un testamento dimenticato vuol dire un Mirto che resta «online» per sempre dopo un crash.
        """
        self.assertTrue(self._accendi(publish=True))
        self.assertTrue(aspetta(lambda: self.dom.racconto is not None))
        self.assertIn(("mirto/status", b"offline", True), self._testamenti_di_mirto(),
                      f"il broker non ha ricevuto nessun testamento da Mirto: {self._tutti()}")

    def test_spento_il_broker_non_riceve_nemmeno_il_testamento(self):
        self.assertTrue(self._accendi())
        self.assertEqual(self._testamenti_di_mirto(), [],
                         "a racconto spento Mirto lascia comunque un testamento sul broker")

    def _tutti(self):
        return self.finta._mio_broker.testamenti()        # noqa: SLF001

    def _testamenti_di_mirto(self):
        """Solo i testamenti di Mirto.

        **La casa finta ne lascia uno suo** — `zigbee2mqtt/bridge/state` a `offline`, perché imita
        un vero Zigbee2MQTT anche in questo — quindi chiedere al broker «non c'è nessun testamento»
        sarebbe una prova che non può passare. L'ho scritta così la prima volta.
        """
        return [x for x in self._tutti() if x[0].startswith("mirto/")]

    def test_pubblicare_senza_collegamento_dice_no_invece_di_esplodere(self):
        """Il buco che ha trovato la mutazione: la riga che controlla il collegamento non era provata.

        Non cambia cosa si vede da fuori — il racconto ingoia tutto — ed è proprio per questo che
        serve una prova sua: senza, una sollevata per ogni messaggio riempirebbe il registro di
        tracce di stack ogni volta che il broker di casa si riavvia, e un registro illeggibile è il
        modo migliore di non accorgersi del difetto vero quando arriva.
        """
        from mirto.actions.smarthome import CasaSmart

        muta = CasaSmart("127.0.0.1", self.finta.porta)     # mai collegata
        self.assertFalse(muta.pubblica("mirto/status", b"online", True))

    def test_e_dopo_essersi_chiusa_dice_no_allo_stesso_modo(self):
        from mirto.actions.smarthome import CasaSmart

        casa = CasaSmart("127.0.0.1", self.finta.porta).collega()
        self.assertTrue(casa.pubblica("mirto/prova", b"x", False))
        casa.chiudi()
        self.assertFalse(casa.pubblica("mirto/prova", b"x", False))


class HomeAssistantTrovaMirtoDaSoloTest(BancoDelBrokerVero, unittest.TestCase):
    """Passo 6, sul banco vero: le configurazioni partono davvero e arrivano a chi ascolta.

Usa lo stesso banco della classe di sopra — broker vero, `Domotica` vero, un client a parte che fa
    la parte di Home Assistant — senza esserne erede, perché erederne vorrebbe dire rifarne anche le
    prove. Le prove di
    ``test_scoperta.py`` dicono **cosa** c'è dentro i messaggi; queste dicono che partono, quando
    partono, e che quando si spegne tutto qualcuno si prende la briga di cancellarle.
    """

    PROTEZIONE = "homeassistant/binary_sensor/mirto/armed/config"
    MOVIMENTO = "homeassistant/binary_sensor/mirto/cam1_motion/config"
    RECINTO = "homeassistant/zzz-recinto/config"

    def _recinto(self, ha) -> None:
        """Aspetta che **non sia arrivato niente**, che è la cosa più difficile da aspettare.

        Una prova che vuole dire «non ha pubblicato» e guarda subito una lista vuota non prova
        niente: la lista è vuota anche un istante prima che arrivi il messaggio che non dovrebbe
        esserci. Passerebbe sempre, anche sul codice rotto, ed è il genere di prova verde che fa più
        danno di una rossa.

        Allora si pubblica un messaggio nostro, sullo stesso collegamento, e si aspetta **quello**:
        MQTT consegna in ordine su una connessione, quindi quando arriva il recinto tutto ciò che
        fosse partito prima è già arrivato. Dopo, una lista vuota vuol dire qualcosa.
        """
        casa = self.dom.casa
        self.assertIsNotNone(casa, "non c'è collegamento: il recinto non si può piantare")
        self.assertTrue(casa.pubblica(self.RECINTO, b"x", False))
        self.assertTrue(aspetta(lambda: any(a == self.RECINTO for a, _c, _r in list(ha))),
                        "il recinto non è arrivato: il broker non ha consegnato niente")

    def _detti(self, ha) -> list[str]:
        return [a for a, _c, _r in list(ha) if a != self.RECINTO]

    def _niente_piu_in_magazzino(self) -> None:
        """Aspetta che il broker abbia **davvero** svuotato il magazzino dei ritenuti.

        `pubblica()` torna quando ha scritto sul socket, non quando il broker ha finito di
        obbedire. Fra le due cose una spia che si iscrive riceve ancora le configurazioni di
        prima — e una prova che le conta giudica su quelle. Cadeva una volta su sei, con un
        messaggio che accusava il programma di non aver cancellato quando invece aveva cancellato.
        """
        broker = self.finta._mio_broker                      # noqa: SLF001
        self.assertTrue(aspetta(lambda: not broker.ritenuti_per("homeassistant/#")),
                        f"restano ritenuti: {broker.ritenuti_per('homeassistant/#')}")

    def test_chi_ascolta_la_scoperta_trova_mirto(self):
        ha = self._spia("homeassistant/#")
        self.assertTrue(self._accendi(publish=True))
        self.dom.scopri([("cam1", "Ingresso")])
        self.assertTrue(aspetta(lambda: any(a == self.MOVIMENTO for a, _c, _r in ha)),
                        f"la camera non si è fatta trovare: {[a for a, _c, _r in ha]}")
        self.assertTrue(any(a == self.PROTEZIONE for a, _c, _r in ha))

    def test_col_racconto_spento_non_si_pubblica_niente(self):
        """La riga che conta per la riservatezza: chi vuole solo accendere le luci non si ritrova
        l'antifurto su una bacheca condivisa, e **nemmeno il fatto di averne uno**.

        Una configurazione di scoperta è già un'informazione: dice che in quella casa c'è un Mirto,
        quante camere ha e come si chiamano. Pubblicarla «tanto è solo la configurazione» sarebbe
        esattamente il genere di scorciatoia che PRIVACY.md promette di non prendere.
        """
        ha = self._spia("homeassistant/#")
        self.assertTrue(self._accendi(publish=False))
        self.assertEqual(self.dom.scopri([("cam1", "Ingresso")]), 0)
        self._recinto(ha)
        self.assertEqual(self._detti(ha), [], "ha parlato col racconto spento")

    def test_si_fa_trovare_da_se_appena_si_collega(self):
        """Senza, bisognerebbe aggiungere una camera per comparire — e chi ne ha già due non lo fa."""
        self.dom.scopri([("cam1", "Ingresso")])        # prima del collegamento: non parte niente
        ha = self._spia("homeassistant/#")
        self.assertTrue(self._accendi(publish=True))   # riapre: adesso sì, con le camere di prima
        self.assertTrue(aspetta(lambda: any(a == self.MOVIMENTO for a, _c, _r in ha)),
                        f"riaprendo non si è fatto trovare: {[a for a, _c, _r in ha]}")

    def test_spegnendo_il_racconto_si_cancella_quello_che_si_era_detto(self):
        """La parte che quasi nessuno fa, provata dove si vede: sul broker.

        E provata **dopo un riavvio finto**, che è il caso vero: chi spegne il racconto lo fa un
        altro giorno, con l'elenco delle camere in memoria vuoto. Se la cancellazione guardasse
        quell'elenco invece di quello scritto in configurazione, qui resterebbe orfano il sensore
        della camera e si cancellerebbe solo quello della protezione.
        """
        self.assertTrue(self._accendi(publish=True))
        self.dom.scopri([("cam1", "Ingresso")])
        self.assertTrue(aspetta(lambda: self.cfg.get("homeassistant.published") is True))
        self.dom.stop()
        self.dom._camere = []                                           # noqa: SLF001 - «è un altro giorno»

        ha = self._spia("homeassistant/#")
        self.assertTrue(self._accendi(publish=False))
        # Si aspettano **i vuoti**, non «due messaggi». Chi si iscrive ora riceve per prima cosa i
        # ritenuti di prima — le configurazioni che questa prova stessa ha appena pubblicato — e
        # contandoli la prova si fermava a quelli e giudicava su quelli: cadeva una volta su sei,
        # e cadeva dicendo il vero («nessun vuoto») con un messaggio che sembrava un difetto del
        # programma. Il fatto da aspettare non è quanti messaggi sono arrivati: è che le
        # cancellazioni siano arrivate.
        def vuoti():
            return {a for a, c, _r in list(ha) if c == b""}

        self.assertTrue(aspetta(lambda: {self.PROTEZIONE, self.MOVIMENTO} <= vuoti()),
                        f"non ha cancellato tutto: vuoti={sorted(vuoti())}, "
                        f"arrivati={[a for a, _c, _r in list(ha)]}")
        self.assertFalse(self.cfg.get("homeassistant.published"))

    def test_non_si_cancella_due_volte(self):
        """Riaprendo due volte col racconto spento, la seconda non deve scrivere niente.

        Scrivere messaggi vuoti sul broker di chi non ha mai acceso niente non rompe nulla, ma è
        traffico a casa d'altri senza averlo chiesto, e su un broker condiviso si nota.
        """
        self.assertTrue(self._accendi(publish=True))
        self.dom.scopri([("cam1", "Ingresso")])
        self.assertTrue(aspetta(lambda: self.cfg.get("homeassistant.published") is True))
        self.assertTrue(self._accendi(publish=False))
        self.assertTrue(aspetta(lambda: self.cfg.get("homeassistant.published") is False))
        self._niente_piu_in_magazzino()

        ha = self._spia("homeassistant/#")
        self.assertTrue(self._accendi(publish=False))
        self._recinto(ha)
        self.assertEqual(self._detti(ha), [], "ha cancellato di nuovo a vuoto")

    def test_chi_non_ha_mai_acceso_niente_non_riceve_niente(self):
        ha = self._spia("homeassistant/#")
        self.assertTrue(self._accendi(publish=False))
        self._recinto(ha)
        self.assertEqual(self._detti(ha), [])

    def test_le_camere_di_adesso_si_ricordano_per_la_prossima_volta(self):
        self.assertTrue(self._accendi(publish=True))
        self.dom.scopri([("cam1", "Ingresso"), ("cam2", "Garage")])
        self.assertTrue(aspetta(lambda: self.cfg.get("homeassistant.cameras")))
        self.assertEqual([list(c) for c in self.cfg.get("homeassistant.cameras")],
                         [["cam1", "Ingresso"], ["cam2", "Garage"]])

    def test_spegnendo_tutto_il_collegamento_la_memoria_si_tiene(self):
        """Il caso in cui **non** si può cancellare subito, e la decisione che ne segue.

        Chi spegne tutta la domotica, non la sola spunta, non ha più un broker a cui dire
        «dimenticami». Si potrebbe provare a cancellare prima di chiudere, e ci ho provato: a QoS 0
        pubblicare e chiudere il socket un istante dopo **perde i messaggi** — misurato, sei volte su
        otto — e intanto la memoria era già stata azzerata, quindi quel tentativo rendeva impossibile
        anche il recupero. Un rimedio che fallisce in silenzio e brucia il piano B è peggio del
        problema.

        Allora la memoria **si tiene**, e la cancellazione avviene il giorno che Mirto si ricollega a
        quel broker — con il racconto spento, cioè nella strada già provata qui sopra. L'unico caso
        che resta scoperto è «spengo e non ricollego mai più», e in quel caso Mirto è uscito dalla
        vita di chi lo usava. Sta scritto nella guida, non nascosto.
        """
        self.assertTrue(self._accendi(publish=True))
        self.dom.scopri([("cam1", "Ingresso")])
        self.assertTrue(aspetta(lambda: self.cfg.get("homeassistant.published") is True))

        self.cfg.set("smarthome", {"enabled": False, "host": "127.0.0.1", "port": self.finta.porta})
        self.assertFalse(self.dom.riapri())
        self.assertTrue(self.cfg.get("homeassistant.published"),
                        "ha dimenticato cosa aveva pubblicato senza averlo cancellato")
        self.assertEqual([list(c) for c in self.cfg.get("homeassistant.cameras")],
                         [["cam1", "Ingresso"]])

        # e al ricollegamento, col racconto spento, si cancella per davvero
        ha = self._spia("homeassistant/#")
        self.assertTrue(self._accendi(publish=False))
        self.assertTrue(aspetta(lambda: {self.PROTEZIONE, self.MOVIMENTO}
                                <= {a for a, c, _r in list(ha) if c == b""}),
                        f"al ritorno non ha cancellato: {[a for a, _c, _r in list(ha)]}")
        self.assertFalse(self.cfg.get("homeassistant.published"))
