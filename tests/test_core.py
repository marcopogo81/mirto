# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
import ast
import contextlib
import os
import re
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from mirto.actions.diagnosi import API_ATTESA  # noqa: E402
from mirto.cameras.discovery import CameraInfo
from mirto.config import Config
from mirto.credentials import SecretStore
from mirto.detection.person import Detection
from tests.test_sources import FakeCapture, wait_until


class FakeDetector:
    def __init__(self):
        self.person = False

    def detect(self, frame, threshold=0.3, classes=None):
        return [Detection((10, 10, 50, 90), 0.9)] if frame.mean() > 150 else []


class ChangingCapture(FakeCapture):
    """Sfondo scuro per un po', poi una 'persona' (immagine chiara)."""
    def __init__(self):
        super().__init__(frames=10_000)
        self.n = 0

    def read(self):
        time.sleep(0.01)
        self.n += 1
        val = 60 if self.n < 60 else 200
        return True, np.full((120, 160, 3), val, np.uint8)


class CoreTest(unittest.TestCase):
    def test_event_flow(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["MIRTO_DATA_DIR"] = d
            cfg = Config(Path(d) / "c.yaml")
            cfg.set("detection.analysis_fps", 30)
            store = SecretStore(); store._kr = None
            from mirto.core import MirtoCore
            core = MirtoCore(cfg, store, detector=FakeDetector(),
                               discover=lambda: [CameraInfo("cam1", "Ingresso", index=0)],
                               capture_factory=lambda i: ChangingCapture())
            got = []
            core.on_event.append(got.append)
            core.start(threaded_discovery=False)
            try:
                self.assertTrue(wait_until(lambda: got, timeout=30), "deve generare un evento")
                self.assertTrue(Path(got[0].image_path).exists())
                self.assertEqual(core.store.recent()[0]["camera_name"], "Ingresso")
            finally:
                core.stop()


class CoreNotifyTest(unittest.TestCase):
    def test_event_reaches_telegram_and_handlers(self):
        from mirto.core import MirtoCore
        from mirto.credentials import TELEGRAM_TOKEN
        from tests.test_notify import FakeAPI
        with tempfile.TemporaryDirectory() as d:
            os.environ["MIRTO_DATA_DIR"] = d
            cfg = Config(Path(d) / "c.yaml")
            cfg.set("detection.analysis_fps", 30)
            cfg.set("general.prevent_sleep", False)
            cfg.set("notifications.telegram", {"enabled": True, "chat_id": "-1005", "allowed_user_ids": [1]})
            cfg.set("actions.armed", True)
            store = SecretStore(); store._kr = None; store.set(TELEGRAM_TOKEN, "T")
            api = FakeAPI()
            core = MirtoCore(cfg, store, detector=FakeDetector(),
                               dispatcher_kwargs={"telegram_api_factory": lambda t: api},
                               discover=lambda: [CameraInfo("cam1", "Ingresso", index=0)],
                               capture_factory=lambda i: ChangingCapture())
            core.start(threaded_discovery=False)
            core.notifier.bot.stop()
            try:
                # I messaggi di questi due attese non sono decorazione. Questa prova e' caduta tre
                # volte in CI quando la suite girava lenta (147 s invece di 117) e ogni volta ha detto
                # soltanto «False is not true», che non distingue le due meta' della catena: la camera
                # non ha prodotto l'evento, oppure l'evento c'era e la notifica non e' partita. Il
                # 23/10 ho provato a farla cadere a comando — da sola e in tre suite intere sotto
                # carico quadruplo, piu' un conteggio dei thread (picco 3, nessuno lasciato acceso) —
                # e non si e' riprodotta. Finche' non si riproduce non si corregge: si fa in modo che
                # la prossima volta dica da sola cos'e' andato storto.
                def dove_siamo() -> str:
                    pipeline = core.pipelines.get("cam1")
                    return (f"chiamate a Telegram: {[c[0] for c in api.calls]} · "
                            f"eventi salvati: {len(core.store.recent())} · "
                            f"errori del notificatore: {dict(core.notifier.last_error)} · "
                            f"pipeline: {'presente' if pipeline else 'mancante'}")

                self.assertTrue(wait_until(lambda: any(c[0] == "sendPhoto" for c in api.calls), timeout=30),
                                f"nessuna foto su Telegram in 30 s — {dove_siamo()}")
                ev = core.store.recent()[0]
                self.assertTrue(wait_until(lambda: core.store.get(ev["id"])["telegram_link"], timeout=30),
                                f"la foto e' partita ma il link non e' tornato sull'evento — {dove_siamo()}")
                self.assertIn("Ingresso", core.handlers.status_text())
                self.assertIsNotNone(core.handlers.snapshot("ingr"))
                self.assertIn("silenziata", core.handlers.mute_event_camera(ev["id"], 3600))
                self.assertGreater(core.pipelines["cam1"].paused_until, time.time())
                self.assertEqual(core.handlers.stop_all(), "Azioni non configurate.")
            finally:
                core.stop()


class DisarmedTest(unittest.TestCase):
    def test_disarmed_saves_but_does_not_notify(self):
        from mirto.core import MirtoCore
        from mirto.credentials import TELEGRAM_TOKEN
        from tests.test_notify import FakeAPI
        with tempfile.TemporaryDirectory() as d:
            os.environ["MIRTO_DATA_DIR"] = d
            cfg = Config(Path(d) / "c.yaml")
            cfg.set("detection.analysis_fps", 30)
            cfg.set("general.prevent_sleep", False)
            cfg.set("notifications.telegram", {"enabled": True, "chat_id": "-1005", "allowed_user_ids": [1]})
            cfg.set("actions.armed", False)
            store = SecretStore(); store._kr = None; store.set(TELEGRAM_TOKEN, "T")
            api = FakeAPI()
            core = MirtoCore(cfg, store, detector=FakeDetector(),
                               dispatcher_kwargs={"telegram_api_factory": lambda t: api},
                               discover=lambda: [CameraInfo("cam1", "Ingresso", index=0)],
                               capture_factory=lambda i: ChangingCapture())
            core.start(threaded_discovery=False)
            core.notifier.bot.stop()
            try:
                self.assertTrue(wait_until(lambda: core.store.recent(), timeout=30), "l'evento viene archiviato")
                time.sleep(1)
                self.assertFalse(any(c[0] in ("sendPhoto", "sendMessage") for c in api.calls),
                                 "disinserito: nessun messaggio Telegram")
                core.send_test_alert()
                self.assertTrue(wait_until(lambda: any(c[0] == "sendPhoto" for c in api.calls), timeout=30),
                                "l'avviso di prova parte anche da disinserito")
                photo = next(c for c in api.calls if c[0] == "sendPhoto")
                self.assertIn("PROVA", photo[1]["caption"])
                shots = core.snapshot_all(Path(d) / "prove")
                self.assertEqual(len(shots), 1)
                self.assertTrue(shots[0].exists())
            finally:
                core.stop()


class PauseStateTest(unittest.TestCase):
    def test_pause_survives_restart_and_state(self):
        from mirto.core import MirtoCore
        with tempfile.TemporaryDirectory() as d, contextlib.ExitStack() as chiudi:
            os.environ["MIRTO_DATA_DIR"] = d
            path = Path(d) / "c.yaml"
            cfg = Config(path)
            store = SecretStore(); store._kr = None
            core = MirtoCore(cfg, store, detector=FakeDetector(), discover=lambda: [])
            chiudi.callback(core.stop)
            self.assertEqual(core.alert_state()["mode"], "disarmed")
            cfg.set("actions.armed", True)
            self.assertEqual(core.alert_state()["mode"], "active")
            core.pause(3600)
            core.pause(1800, "cam1")
            self.assertEqual(core.alert_state()["mode"], "paused")
            again = MirtoCore(Config(path), store, detector=FakeDetector(), discover=lambda: [])
            chiudi.callback(again.stop)
            st = again.alert_state()
            self.assertEqual(st["mode"], "paused", "la pausa resta dopo il riavvio")
            self.assertIn("cam1", st["camera_paused"])
            again.pause(0)
            self.assertEqual(again.alert_state()["mode"], "active")
            self.assertEqual(again.alert_state()["camera_paused"], {})
            self.assertIn("Protezione ATTIVA", again.handlers.status_text())

    def test_startup_state(self):
        from mirto.core import MirtoCore
        with tempfile.TemporaryDirectory() as d, contextlib.ExitStack() as chiudi:
            os.environ["MIRTO_DATA_DIR"] = d
            store = SecretStore(); store._kr = None
            core = MirtoCore(Config(Path(d) / "c.yaml"), store, detector=None, discover=lambda: [])
            chiudi.callback(core.stop)
            boot = core.startup_state()
            self.assertTrue(boot["core"] and boot["telegram"] and boot["cameras"])

    def test_the_dark_warning_is_said_once_and_says_what_to_do(self):
        """Marco, 25/10: *«la camera della Raspberry 3B non e\' NoIR, quindi non ha visione
        notturna»*. La frase era vera e tornava **ogni notte**, per sempre, su una camera che non
        puo\' risolverla: un avviso che non si puo\' chiudere e che si ripete insegna a ignorare gli
        avvisi, e il prossimo che conta passa inosservato con gli altri."""
        from mirto.core import MirtoCore
        with tempfile.TemporaryDirectory() as d, contextlib.ExitStack() as chiudi:
            os.environ["MIRTO_DATA_DIR"] = d
            path = Path(d) / "c.yaml"
            cfg = Config(path)
            store = SecretStore(); store._kr = None
            core = MirtoCore(cfg, store, detector=FakeDetector(), discover=lambda: [])
            chiudi.callback(core.stop)
            detti = []
            core.on_message.append(detti.append)
            core._camera_state("cam1", {"dark": True})
            self.assertEqual(len(detti), 1)
            self.assertIn("infrarossi", detti[0], "il rimedio vero: una luce o una camera a infrarossi")
            self.assertIn("una volta sola", detti[0], "e si dice perche\' non tornera\'")
            core._camera_state("cam1", {"dark": False})
            core._camera_state("cam1", {"dark": True})
            self.assertEqual(len(detti), 1, "tornare chiaro e poi scuro e\' la notte dopo, non una novita\'")
            core._camera_state("cam2", {"dark": True})
            self.assertEqual(len(detti), 2, "un'altra camera e\' un'altra cosa da sapere")
            # Dopo un riavvio non si ricomincia: il rimedio e\' fisico, non uno stato da sorvegliare.
            ancora = MirtoCore(Config(path), store, detector=FakeDetector(), discover=lambda: [])
            chiudi.callback(ancora.stop)
            ancora.on_message.append(detti.append)
            ancora._camera_state("cam1", {"dark": True})
            self.assertEqual(len(detti), 2)

    def test_an_action_switches_from_wherever_asks(self):
        """Accendere o spegnere un'azione è una decisione sul programma, non una cosa dell'interfaccia:
        sta nel core, così può chiederla la colonna Sistema oggi e un comando di Telegram domani."""
        import types
        from mirto.core import MirtoCore
        with tempfile.TemporaryDirectory() as d, contextlib.ExitStack() as chiudi:
            os.environ["MIRTO_DATA_DIR"] = d
            cfg = Config(Path(d) / "c.yaml")
            store = SecretStore(); store._kr = None
            core = MirtoCore(cfg, store, detector=FakeDetector(), discover=lambda: [])
            chiudi.callback(core.stop)
            cfg.set("actions.items", [{"name": "Luci", "node": "n1", "action": "luci",
                                       "kind": "light", "enabled": True}])
            riletture = []
            core.actions = types.SimpleNamespace(has_activity=lambda: False,
                                                 reload=lambda: riletture.append(1))
            self.assertEqual(core.accendi_o_spegni_azione("Luci"), "spenta")
            self.assertFalse(cfg.get("actions.items")[0]["enabled"])
            self.assertEqual(core.accendi_o_spegni_azione("Luci"), "accesa")
            self.assertTrue(cfg.get("actions.items")[0]["enabled"])
            self.assertEqual(riletture, [1, 1], "il motore rilegge: se no cambia il file e non il comportamento")
            self.assertEqual(core.accendi_o_spegni_azione("Non esiste"), "",
                             "un nome che non c'è non inventa un'azione")

    def test_stop_all_is_there_whenever_there_is_something_to_stop(self):
        """Il pulsante segue **una** cosa: che ci sia qualcosa in corso.

        Chiedeva anche la protezione attiva, e veniva da quando un'azione partiva solo da inserita.
        Ma i dissuasori partono sempre, per scelta, e l'allarme fumo pure: poteva suonare qualcosa
        in giardino e il comando per fermarlo non esserci. Un comando d'emergenza che sparisce
        proprio quando serve e' peggio che non averlo (Marco, 25/10, provando con due nodi).
        """
        import types
        from mirto.core import MirtoCore
        with tempfile.TemporaryDirectory() as d, contextlib.ExitStack() as chiudi:
            os.environ["MIRTO_DATA_DIR"] = d
            cfg = Config(Path(d) / "c.yaml")
            store = SecretStore(); store._kr = None
            core = MirtoCore(cfg, store, detector=FakeDetector(), discover=lambda: [])
            chiudi.callback(core.stop)
            core.actions = types.SimpleNamespace(has_activity=lambda: True)
            self.assertTrue(core.stop_all_visible(),
                            "un dissuasore suona a protezione disinserita: il pulsante deve esserci")
            cfg.set("actions.armed", True)
            self.assertTrue(core.stop_all_visible())
            core.actions = types.SimpleNamespace(has_activity=lambda: False)
            self.assertFalse(core.stop_all_visible(), "niente in corso, niente pulsante")
            cfg.set("actions.armed", False)
            self.assertFalse(core.stop_all_visible())
            core.actions = None
            self.assertFalse(core.stop_all_visible(), "senza il motore delle azioni non c'e' nulla da fermare")


if __name__ == "__main__":
    unittest.main()


class RobustezzaDiAvvioTest(unittest.TestCase):
    """Q-06: i primi dieci minuti e la settimana dopo.

    Quattro cose che non si vedono in una demo riuscita: una configurazione rovinata che fa «non si
    apre» senza dire niente, un errore in un thread che muore in silenzio (su Windows non c'è
    nemmeno una console a raccoglierlo), un timer della finestra Impostazioni che continua a battere
    dopo la chiusura, e una foto ricompressa da capo per ogni canale di avviso.
    """

    def test_a_broken_configuration_does_not_stop_the_program(self):
        """Prima: `yaml.safe_load` alzava e l'app non si apriva, senza un messaggio.

        La configurazione rovinata non si cancella: si mette da parte con `.rotto`, così chi l'ha
        scritta a mano può recuperarne i pezzi, e si riparte dai valori predefiniti **dicendolo**.
        """
        with tempfile.TemporaryDirectory() as d:
            percorso = Path(d) / "config.yaml"
            percorso.write_text("general:\n  language: it\n  questa riga: [non chiusa\n", encoding="utf-8")
            cfg = Config(percorso)
            self.assertEqual(cfg.get("general.language"), "auto", "si riparte dai predefiniti")
            self.assertTrue(cfg.load_warning, "e lo si dice: senza avviso l'utente non sa perché")
            messo_da_parte = percorso.with_suffix(".yaml.rotto")
            self.assertTrue(messo_da_parte.exists(), "il file rovinato si tiene, non si butta")
            self.assertIn("non chiusa", messo_da_parte.read_text(encoding="utf-8"))
            cfg.set("general.language", "it")
            cfg.save()
            self.assertEqual(Config(percorso).get("general.language"), "it", "e da lì si riparte normalmente")

    def test_a_configuration_that_is_not_a_mapping_is_treated_the_same(self):
        with tempfile.TemporaryDirectory() as d:
            percorso = Path(d) / "config.yaml"
            percorso.write_text("- una lista, non una mappa\n", encoding="utf-8")
            cfg = Config(percorso)
            self.assertTrue(cfg.load_warning)
            self.assertEqual(cfg.get("general.language"), "auto")

    def test_an_unreadable_file_does_not_lose_it(self):
        """Illeggibile per i permessi non è «rovinata»: non si mette da parte niente."""
        if os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0):
            self.skipTest("servono permessi che root non sente e che Windows non applica così")
        with tempfile.TemporaryDirectory() as d:
            percorso = Path(d) / "config.yaml"
            percorso.write_text("general: {}\n", encoding="utf-8")
            percorso.chmod(0o000)
            try:
                cfg = Config(percorso)
                self.assertTrue(cfg.load_warning)
                self.assertFalse(percorso.with_suffix(".yaml.rotto").exists(),
                                 "il file c'è ed è valido: spostarlo sarebbe un danno")
            finally:
                percorso.chmod(0o600)

    def test_an_error_in_a_thread_is_written_down(self):
        """Senza gancio, un'eccezione in un thread spariva: su Windows non c'è console che la raccolga."""
        import logging
        import threading

        from mirto.logging_setup import installa_ganci_errori

        installa_ganci_errori()
        self.assertIsNot(threading.excepthook, threading.__excepthook__, "il gancio dei thread va installato")
        with self.assertLogs("mirto", level=logging.ERROR) as registro:
            def scoppia():
                raise RuntimeError("prova di scoppio")

            t = threading.Thread(target=scoppia, name="prova")
            t.start()
            t.join()
        unito = "\n".join(registro.output)
        self.assertIn("prova di scoppio", unito)
        self.assertIn("Traceback", unito, "serve il traceback, non solo il messaggio")

    def test_the_main_thread_hook_is_installed_too(self):
        import sys

        from mirto.logging_setup import installa_ganci_errori

        installa_ganci_errori()
        self.assertIsNot(sys.excepthook, sys.__excepthook__)

    def test_the_photo_is_compressed_once_for_all_the_channels(self):
        """`jpeg()` leggeva `self._jpeg` ma non lo scriveva mai: ricompressione a ogni canale.

        Un avviso va su Telegram, per email, nella cartella degli eventi: la stessa foto veniva
        ricompressa ogni volta, fino a sei tentativi di qualità per volta.
        """
        import mirto.events as E   # la spia va sul cv2 che usa questo modulo, non su un altro
        from mirto.events import Event

        ev = Event("cam1", "Ingresso", 0.9, np.full((400, 600, 3), 200, np.uint8))
        conta = [0]
        vero = E.cv2.imencode

        def spia(*a, **k):
            conta[0] += 1
            return vero(*a, **k)

        E.cv2.imencode = spia
        try:
            primo = ev.jpeg()
            dopo_il_primo = conta[0]
            secondo = ev.jpeg()
        finally:
            E.cv2.imencode = vero
        self.assertEqual(primo, secondo)
        self.assertGreater(dopo_il_primo, 0)
        self.assertEqual(conta[0], dopo_il_primo, "la seconda volta non si ricomprime niente")


class TimerDellaFinestraTest(unittest.TestCase):
    """Ogni timer della finestra Impostazioni si ferma quando la finestra si chiude.

    La finestra ha il parente (la finestra principale), quindi non muore da sola: senza fermare i
    timer, aprire e chiudere Impostazioni dieci volte lascia dieci timer che continuano a chiedere
    lo stato della registrazione ogni due secondi, per sempre.
    """

    SORGENTE = Path(__file__).resolve().parents[1] / "mirto" / "gui" / "settings.py"

    def test_every_timer_is_stopped_when_the_window_closes(self):
        sorgente = self.SORGENTE.read_text(encoding="utf-8")
        timer = sorted(set(re.findall(r"self\.(_\w*timer)\s*=\s*QTimer", sorgente)))
        self.assertTrue(timer, "nessun timer trovato: il controllo non sta guardando la cosa giusta")
        corpo = sorgente[sorgente.index("    def done(self, result)"):]
        corpo = corpo[:corpo.index("\n    def ", 1)]
        self.assertIn(".stop()", corpo, "done() deve fermare i timer, non solo nominarli")
        for nome in timer:
            # Basta che il nome compaia: done() li ferma con un ciclo su un elenco di nomi, che è
            # meglio di una riga per timer. Quello che questa guardia difende è che aggiungere un
            # timer in __init__ e dimenticarlo in done() faccia fallire la prova.
            self.assertIn(nome, corpo, f"{nome} continua a battere a finestra chiusa")

    def test_the_window_is_destroyed_on_close(self):
        sorgente = self.SORGENTE.read_text(encoding="utf-8")
        self.assertIn("WA_DeleteOnClose", sorgente,
                      "con il parente la finestra sopravvive alla chiusura: va distrutta")


class DiagnosticaTest(unittest.TestCase):
    """C-03: un rapporto da incollare in una segnalazione, incapace di contenere un segreto.

    Chi apre una issue non sa quali dati siano delicati: il modello di issue dice «non incollare
    token, indirizzi IP, ID delle chat», ma poi serve sapere com'è fatta l'installazione. Questo
    comando raccoglie quello che serve e **toglie** quello che non deve uscire — non per buona
    volontà di chi lo usa, ma perché il rapporto passa da un filtro e un test lo verifica.
    """

    # I finti segreti qui sotto devono avere la **forma** di un segreto, perché è su quella che il
    # filtro lavora — ma non devono *sembrare* un segreto a chi li legge da fuori. L'8/11 il
    # controllo di GitHub ha segnalato il token di prova come «Telegram Bot Token — Public leak»:
    # era inventato, ma era inventato *bene*, con l'aria di avere dell'entropia dentro. Un dato di
    # prova fatto così fa suonare ogni scanner, a noi e a chiunque faccia un fork, per sempre.
    # Quindi: la forma la rispettano (`\d{6,}:[\w-]{20,}`), ma lo dicono da sé di non essere veri.
    # È la stessa regola del 7/11 con gitleaks: un allarme si spegne togliendone il motivo.
    SEGRETI = {
        "token Telegram": "8123456789:QUESTO-NON-E-UN-TOKEN-VERO-SOLO-UNA-PROVA",
        "password nell'indirizzo": "rtsp://mirto:4ab8c848a260a128ac0dc80f@192.168.2.2:8554/cam",
        "chiave esadecimale": "e0d1d36ecbb563d0fb977b55e0d1d36ecbb563d0fb977b55",
        "indirizzo di casa": "192.168.2.2",
        "posta": "marco.esempio@gmail.com",
        "id della chat": "-1001234567890",
    }

    def test_nothing_secret_survives_the_filter(self):
        from mirto.diagnostics import pulisci

        for nome, segreto in self.SEGRETI.items():
            ripulito = pulisci(f"qualcosa prima {segreto} qualcosa dopo")
            self.assertNotIn(segreto, ripulito, f"{nome} è passato: {ripulito}")

    def test_the_filter_keeps_what_makes_a_report_useful(self):
        from mirto.diagnostics import pulisci

        riga = "2026-10-19 11:00:00 ERROR mirto.cameras.manager: Camera Ingresso: timeout dopo 10 s"
        self.assertEqual(pulisci(riga), riga, "togliere troppo rende il rapporto inutile")
        self.assertIn("rtsp://", pulisci("apro rtsp://utente:segreta@casa.local/cam"),
                      "lo schema resta: dice che tipo di camera è")
        self.assertNotIn("segreta", pulisci("apro rtsp://utente:segreta@casa.local/cam"))

    def test_the_report_says_what_support_needs(self):
        from mirto.diagnostics import rapporto

        with tempfile.TemporaryDirectory() as d:
            cfg = Config(Path(d) / "config.yaml")
            cfg.set("cameras.network", [{"id": "n1", "name": "Ingresso", "url": "rtsp://192.168.2.2:8554/cam"}])
            cfg.set("recording.mode", "events")
            cfg.save()
            testo = rapporto(cfg, data_dir=Path(d))
        minuscolo = testo.lower()
        for atteso in ("mirto", "python", "ffmpeg", "camere", "registrazione", "sistema"):
            self.assertIn(atteso, minuscolo, f"manca «{atteso}»: {testo}")
        self.assertIn("1 di rete", testo, "il numero di camere serve a chi deve capire il problema")
        self.assertIn("events", testo, "e come registra")

    def test_the_report_does_not_leak_the_configuration(self):
        """La configurazione contiene indirizzi, nomi scelti dall'utente, destinatari: non si stampa."""
        from mirto.diagnostics import rapporto

        with tempfile.TemporaryDirectory() as d:
            cfg = Config(Path(d) / "config.yaml")
            cfg.set("cameras.network", [{"id": "n1", "name": "Camera di Giulia",
                                         "url": "rtsp://mirto:4ab8c848a260a128ac0dc80f@192.168.2.2:8554/cam"}])
            cfg.set("notify.email.to", ["marco.esempio@gmail.com"])
            cfg.set("notify.telegram.chat_id", "-1001234567890")
            cfg.save()
            registro = Path(d) / "logs"
            registro.mkdir()
            (registro / "mirto.log").write_text(
                "2026-10-19 10:00:00 ERROR mirto.core: apro "
                "rtsp://mirto:4ab8c848a260a128ac0dc80f@192.168.2.2:8554/cam e fallisce\n",
                encoding="utf-8")
            testo = rapporto(cfg, data_dir=Path(d))
        for segreto in ("4ab8c848a260a128ac0dc80f", "192.168.2.2", "marco.esempio@gmail.com",
                        "-1001234567890", "Camera di Giulia"):
            self.assertNotIn(segreto, testo, f"il rapporto contiene {segreto}:\n{testo}")

    def test_the_last_errors_are_there_but_cleaned(self):
        from mirto.diagnostics import rapporto

        with tempfile.TemporaryDirectory() as d:
            cfg = Config(Path(d) / "config.yaml")
            registro = Path(d) / "logs"
            registro.mkdir()
            righe = [f"2026-10-19 10:00:0{i} ERROR mirto.core: cosa rotta numero {i}\n" for i in range(3)]
            righe.append("2026-10-19 10:00:09 INFO mirto.core: tutto bene\n")
            (registro / "mirto.log").write_text("".join(righe), encoding="utf-8")
            testo = rapporto(cfg, data_dir=Path(d))
        self.assertIn("cosa rotta numero 2", testo, "gli errori recenti sono il motivo del rapporto")
        self.assertNotIn("tutto bene", testo, "le righe normali non servono a nessuno")

    def test_it_works_on_a_fresh_install_with_no_log(self):
        from mirto.diagnostics import rapporto

        with tempfile.TemporaryDirectory() as d:
            testo = rapporto(Config(Path(d) / "config.yaml"), data_dir=Path(d) / "mai-esistita")
        self.assertIn("Mirto", testo)
        self.assertIn("nessun errore", testo.lower())


class VersionTest(unittest.TestCase):
    """W-07: la versione sta scritta in un posto solo e gli altri file la seguono."""

    ROOT = Path(__file__).resolve().parents[1]

    def leggi(self, nome: str) -> str:
        return (self.ROOT / nome).read_text(encoding="utf-8")

    def test_every_file_follows_mirto_version(self):
        import re

        from mirto import __version__

        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")

        pyproject = self.leggi("pyproject.toml")
        self.assertIn('dynamic = ["version"]', pyproject)
        self.assertNotRegex(pyproject, r'(?m)^version = "', "la versione non va ripetuta in pyproject")
        self.assertIn('version = {attr = "mirto.__version__"}', pyproject)

        spec = self.leggi("packaging/mirto.spec")
        self.assertIn('"CFBundleShortVersionString": MIRTO_VERSION', spec)
        self.assertNotRegex(spec, r'CFBundleShortVersionString":\s*"\d', "versione scritta a mano nello spec")

        wrangler = self.leggi("tools/stats-worker/wrangler.toml")
        trovata = re.search(r'LATEST_VERSION\s*=\s*"([^"]+)"', wrangler)
        self.assertIsNotNone(trovata, "manca LATEST_VERSION nel Worker delle statistiche")
        self.assertEqual(trovata.group(1), __version__,
                         "il server direbbe agli utenti che esiste una versione diversa da questa")

    def test_changelog_has_a_section_for_this_version(self):
        from mirto import __version__

        self.assertIn(f"## [{__version__}]", self.leggi("CHANGELOG.md"))


class CleanupOrderTest(unittest.TestCase):
    """Chi apre qualcosa dentro una cartella temporanea la chiude lì dentro.

    ``addCleanup`` corre quando il metodo è già finito, cioè dopo che la cartella
    temporanea è stata cancellata. Su Linux e su macOS non si nota, perché un file
    aperto si cancella lo stesso; su Windows no, e il test va in errore senza che
    ci sia niente di rotto. Dentro un ``with`` si usa ``contextlib.ExitStack``.
    """

    ROOT = Path(__file__).resolve().parents[1]

    @staticmethod
    def _cartella_temporanea(nodo) -> bool:
        return any(isinstance(voce.context_expr, ast.Call)
                   and ast.unparse(voce.context_expr.func).endswith("TemporaryDirectory")
                   for voce in nodo.items)

    @staticmethod
    def _chiusura_rimandata(nodo) -> bool:
        """Qualunque addCleanup: dentro un «with» di cartella temporanea arriva comunque tardi."""
        return isinstance(nodo, ast.Call) and ast.unparse(nodo.func).endswith("addCleanup")

    def test_nothing_is_closed_after_its_temporary_folder(self):
        colpevoli = []
        for file in sorted((self.ROOT / "tests").glob("test_*.py")):
            albero = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
            for blocco in ast.walk(albero):
                if not isinstance(blocco, ast.With) or not self._cartella_temporanea(blocco):
                    continue
                for dentro in ast.walk(blocco):
                    if self._chiusura_rimandata(dentro):
                        colpevoli.append(f"{file.name}:{dentro.lineno}")
        self.assertEqual(colpevoli, [],
                         "registrato dentro un «with» di cartella temporanea, questo addCleanup corre "
                         "quando la cartella è già sparita: usa contextlib.ExitStack dentro il with")


class ImportiCheEsistonoDavveroTest(unittest.TestCase):
    """Ogni «from .qualcosa import nome» punta a un nome che in quel file c'è davvero.

    Il 22/10 lo spostamento delle righe di *Sistema* in `gui/sistema` si è portato dietro `MODE_NAMES`,
    e `gui/settings` continuava a chiederlo a `gui/app` — ma **dentro una funzione**, quindi il file si
    importava benissimo, le prove passavano, la CI era verde, e il difetto si vedeva solo aprendo le
    Impostazioni: «ImportError: cannot import name MODE_NAMES». Un import rimandato non lo prova
    nessuno finché qualcuno non ci passa sopra, e chi ci passa sopra è chi usa il programma.

    Qui si leggono tutti gli import relativi del pacchetto, rimandati o no, e si pretende che il nome
    dall'altra parte esista. È un controllo statico: gira ovunque, in un decimo di secondo, e avrebbe
    fermato questo difetto prima che uscisse di qui.
    """

    RADICE = Path(__file__).resolve().parents[1] / "mirto"

    @staticmethod
    def _nomi(albero) -> set[str]:
        """Tutto quello che un modulo mette a disposizione, con larghezza: meglio perdonare che accusare."""
        fuori = set()
        for n in ast.walk(albero):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                fuori.add(n.name)
            elif isinstance(n, ast.Assign):
                for bersaglio in n.targets:      # anche «A, B = 1, 2»: sono nomi come gli altri
                    fuori |= {x.id for x in ast.walk(bersaglio) if isinstance(x, ast.Name)}
            elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
                fuori.add(n.target.id)
            elif isinstance(n, ast.Import):
                fuori |= {a.asname or a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                fuori |= {a.asname or a.name for a in n.names}
        return fuori

    def test_every_relative_import_finds_what_it_asks_for(self):
        alberi = {f: ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
                  for f in sorted(self.RADICE.rglob("*.py"))}
        nomi = {f: self._nomi(a) for f, a in alberi.items()}
        rotti = []
        for file, albero in alberi.items():
            pacchetto = file.parent if file.name != "__init__.py" else file.parent.parent
            for n in ast.walk(albero):
                if not isinstance(n, ast.ImportFrom) or not n.level:
                    continue
                base = file.parent
                for _ in range(n.level - 1):
                    base = base.parent
                dove = base.joinpath(*(n.module or "").split(".")) if n.module else base
                bersaglio = dove.with_suffix(".py") if dove.with_suffix(".py").exists() else dove / "__init__.py"
                if bersaglio not in alberi:
                    continue                      # modulo fuori dal pacchetto: non è roba nostra
                for voce in n.names:
                    if voce.name == "*":
                        continue
                    if voce.name in nomi[bersaglio]:
                        continue
                    if (dove / f"{voce.name}.py").exists() or (dove / voce.name / "__init__.py").exists():
                        continue                  # «from . import theme»: è un modulo, non un nome
                    rotti.append(f"{file.relative_to(self.RADICE.parent)}:{n.lineno} chiede "
                                 f"«{voce.name}» a {bersaglio.relative_to(self.RADICE.parent)}")
        self.assertEqual(rotti, [], "questi import non troverebbero niente: se il nome ha cambiato casa, "
                                    "va cambiato anche chi lo chiede — anche quando l'import è dentro "
                                    "una funzione e quindi nessuna prova ci passa sopra")
        self.assertGreater(len(alberi), 40, "se i file letti sono pochi, questo controllo non sta guardando niente")


class BugieSulSistemaTest(unittest.TestCase):
    """Nessuna prova sostituisce ``sys.platform``: quella bugia la sentirebbe tutto il programma.

    ``sys`` è un modulo solo, per tutti. Una prova che ci scrive dentro «darwin» non lo dice soltanto al
    pezzo che sta provando: lo dice anche alle librerie. Su Windows ``platformdirs`` costruisce i percorsi
    delle cartelle leggendo il registro di sistema e, se ``sys.platform`` non dice «win32», si ferma con un
    errore che non c'entra niente con la prova in corso — e si porta via il processo, e con lui la suite
    intera, senza lasciare detto quale prova fosse. Il 21/10 la CI su Windows è stata rossa mezza giornata
    così: nessun nome, nessuna riga, niente.

    Il modo giusto c'è e costa una riga: ogni modulo chiede il sistema a ``mirto.piattaforma.sistema``, e
    una prova sostituisce **quella** dentro **quel** modulo. La bugia resta dove serve.
    """

    ROOT = Path(__file__).resolve().parents[1]

    @staticmethod
    def _bugia(nodo) -> bool:
        if not isinstance(nodo, ast.Call):
            return False
        chi = ast.unparse(nodo.func)
        pezzi = [ast.unparse(a) for a in nodo.args[:2]]
        if chi.endswith("patch.object"):          # patch.object(qualcosa.sys, "platform", …)
            return (len(pezzi) >= 2 and pezzi[0].split(".")[-1] == "sys"
                    and pezzi[1].strip("'\"") == "platform")
        if chi.endswith("patch"):                 # patch("pacchetto.modulo.sys.platform", …)
            return bool(pezzi) and "sys.platform" in pezzi[0]
        return False

    def test_no_test_lies_to_the_whole_program_about_the_system(self):
        colpevoli = []
        for file in sorted((self.ROOT / "tests").glob("test_*.py")):
            albero = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
            colpevoli += [f"{file.name}:{n.lineno}" for n in ast.walk(albero) if self._bugia(n)]
        self.assertEqual(colpevoli, [],
                         "questa prova scrive dentro sys.platform, cioè lo dice a tutto il programma e a "
                         "tutte le librerie: su Windows platformdirs si ferma e la suite muore senza dire "
                         "quale prova era. Fai chiedere il sistema a mirto.piattaforma.sistema e "
                         "sostituisci «sistema» dentro il modulo in prova")


class SistemaChiestoAUnPostoSoloTest(unittest.TestCase):
    """I moduli che una prova deve poter «spostare» su un altro sistema non leggono ``sys.platform``.

    L'altra metà della regola di sopra: se il modulo legge ``sys.platform`` da sé, la prova non ha altro
    modo che mentire a tutti. Questi quattro sono quelli che le prove spostano davvero.
    """

    ROOT = Path(__file__).resolve().parents[1]
    MODULI = ("mirto/i18n.py", "mirto/cameras/permissions.py",
              "mirto/system/autostart.py", "mirto/system/portachiavi.py")

    def test_they_ask_the_one_function(self):
        for nome in self.MODULI:
            testo = (self.ROOT / nome).read_text(encoding="utf-8")
            with self.subTest(nome):
                self.assertNotIn("sys.platform", testo,
                                 "una prova dovrà spostare questo modulo su un altro sistema: fallo "
                                 "chiedere a mirto.piattaforma.sistema")
                self.assertIn("import sistema", testo)


class RigaRegistrazioneTest(unittest.TestCase):
    """Una camera sola non si annuncia come «1 camere».

    Visto nella riga *Registrazione* durante W-01 (19/10). È un dettaglio, ma è il genere di dettaglio
    che un lettore attento usa per decidere quanta cura c'è nel resto: sta nella colonna Sistema, cioè
    nella prima schermata. Dal 22/10 queste righe stanno in `gui/sistema`, che è testo e basta: la prova
    gira ovunque, non solo dove è installato PySide6.
    """

    def riga(self, quante: int) -> str:
        from mirto.gui.sistema import recording_summary

        stato = {"enabled": True, "ffmpeg": "/usr/bin/ffmpeg",
                 "recorders": [{"id": f"c{i}", "name": f"Camera {i}", "state": "recording", "mode": "copy"}
                               for i in range(quante)]}
        return recording_summary(stato, {})[1]

    def test_one_camera_is_not_one_cameras(self):
        self.assertEqual(self.riga(1), "Registrazione · 1 camera")
        self.assertEqual(self.riga(3), "Registrazione · 3 camere")


class RigaReteDelNodoTest(unittest.TestCase):
    """In *Sistema* si deve vedere **come** è attaccato il nodo: cavo o Wi-Fi.

    Chiesta da Marco il 22/10, dopo la mattina in cui il Raspberry 4 è ripartito da sé in Wi-Fi senza
    cavo: funzionava, ma dalla finestra non si capiva da dove arrivasse. È la riga che serve quando un
    nodo fa i capricci — e un Wi-Fi debole si fa notare qui, prima che il nodo sparisca.
    """

    def righe(self, rete) -> str:
        from mirto.gui.sistema import nodes_detail

        info = {"nome": "Balcone", "versione": "0.7.3", "attivo_da_s": 3600}
        if rete is not None:
            info["rete"] = rete
        return nodes_detail([{"id": "n1", "name": "Balcone", "online": True, "info": info,
                              "actions": [], "active": {}}], aperti={"n1"})

    def test_the_cable_and_the_wifi_are_both_named(self):
        self.assertIn("cavo", self.righe({"tipo": "cavo", "scheda": "eth0"}))
        testo = self.righe({"tipo": "wifi", "rete": "CasaMia", "segnale": 68})
        self.assertIn("Wi-Fi", testo)
        self.assertIn("CasaMia", testo)
        self.assertIn("68", testo)

    def test_a_weak_signal_is_marked_and_says_what_to_do(self):
        testo = self.righe({"tipo": "wifi", "rete": "CasaMia", "segnale": 18})
        self.assertIn("debole", testo)
        self.assertIn("cavo", testo, "il rimedio sta nella riga stessa: spostalo o mettilo sul cavo")

    def test_a_node_that_does_not_say_it_leaves_no_empty_row(self):
        """Fino alla 0.7.2 il nodo non lo sapeva: meglio nessuna riga che una riga vuota."""
        for niente in (None, {}, {"tipo": ""}):
            self.assertNotIn("Rete", self.righe(niente), niente)


class SistemaSiApreESiChiudeTest(unittest.TestCase):
    """*Sistema* dice molte cose: chiuse stanno dietro un più, aperte tornano tutte.

    Chiesto da Marco il 22/10 guardando la finestra vera: sotto «Nodi in linea» c'era un muro di righe
    per un nodo solo, e con cinque nodi sarebbe diventato illeggibile. La regola è la stessa dappertutto:
    chiusa, una riga dice il titolo e il riassunto; aperta, rimette quello che c'era prima.

    Queste prove girano ovunque perché il testo di *Sistema* non ha dentro niente di Qt (``gui/sistema``):
    è il motivo per cui è stato spostato lì.
    """

    NODO = {"id": "n1", "name": "Bagno", "online": True, "actions": [], "active": {},
            "info": {"versione": "0.7.3", "attivo_da_s": 3600, "temperatura_c": 44.0,
                     "rete": {"tipo": "wifi", "rete": "CasaMia", "segnale": 68},
                     "audio": {"uscita": "bluetooth", "collegata": True, "nome": "JBL Flip 6"},
                     "sirena_disponibile_s": 180,
                     "registrazione": {"attiva": True, "registra": True, "disco": "/mnt/mirto-reg",
                                       "usato": 1_000_000_000, "libero": 2_500_000_000,
                                       "totale": 4_100_000_000}}}

    def nodi(self, aperti=(), nodi=None, da_aggiornare=None, livelli=None, armata=False):
        from mirto.gui.sistema import nodes_detail

        return nodes_detail(nodi or [self.NODO], aperti=aperti, da_aggiornare=da_aggiornare,
                            livelli=livelli, armata=armata)

    # --- l'aggiornamento si vede dalla riga chiusa (25/10) --------------------
    #
    # Marco, guardando la finestra: *«se c'e' un aggiornamento disponibile per il nodo devo saperlo
    # sia da NODI che da Sistema e raggiungere velocemente la funzionalita'»*. In Sistema la riga
    # chiusa e' tutto quello che si vede senza fare niente: se la cosa da fare sta dentro, non si sa.

    def test_an_update_shows_on_the_closed_line(self):
        testo = self.nodi(da_aggiornare={"n1": "0.12.0"})
        self.assertEqual(testo.count("<br>"), 0, "resta una riga sola")
        self.assertIn("0.12.0", testo, "dice a quale versione si va")
        self.assertIn("sistema:aggiorna:n1", testo, "ed e' un collegamento che porta dove si fa")

    def test_without_an_update_nothing_is_said(self):
        """Una riga che dice sempre qualcosa sull'aggiornamento e' rumore: parla solo se c'e'."""
        testo = self.nodi()
        self.assertNotIn("aggiorna", testo.lower())

    def test_the_update_link_is_not_the_plus(self):
        """Due collegamenti nella stessa riga che fanno cose diverse: il piu' apre, questo porta
        altrove. Se avessero la stessa forma, uno dei due sorprenderebbe."""
        testo = self.nodi(da_aggiornare={"n1": "0.12.0"})
        self.assertIn("sistema:nodo:n1", testo)
        self.assertIn("sistema:aggiorna:n1", testo)

    # --- la vista di flotta: una riga per nodo (N-03, 25/10) -----------------
    #
    # Con un nodo basta la riga di Sistema; con otto le domande diventano **confronti**. Queste prove
    # girano qui perche' quello che si legge lo decide `righe_flotta`, che e' testo e basta: la
    # tabella Qt e' il vestito e non sa niente.

    def _flotta(self, nodi=None, **kw):
        from mirto.gui.sistema import righe_flotta

        return righe_flotta(nodi or [self.NODO], adesso=10_000_000.0, **kw)

    def test_every_node_is_one_row_with_every_column(self):
        from mirto.gui.sistema import COLONNE_FLOTTA

        righe = self._flotta()
        self.assertEqual(len(righe), 1)
        for chiave, _titolo in COLONNE_FLOTTA:
            self.assertIn(chiave, righe[0], f"manca la colonna «{chiave}»: le righe non si incolonnano")

    def test_a_node_that_does_not_answer_is_broken_not_empty(self):
        """Un nodo spento non e' «tutto a posto, campi vuoti»: e' la riga che si deve leggere per prima."""
        spento = dict(self.NODO, online=False, info={}, meta={})
        riga = self._flotta([spento])[0]
        self.assertEqual(riga["stato"][1], "rotto")
        self.assertEqual(riga["avvisi"][1], "assente", "di un nodo spento non si diagnostica il vuoto")

    def test_the_state_column_does_not_say_everything_is_off(self):
        """Stessa parola, stesso equivoco: *«nel nodo Ingresso c'e' scritto tutto spento, ma e'
        fuorviante»* era della riga di Sistema, corretta il 25/10 — e questa colonna continuava a
        dirlo con le stesse parole."""
        riga = self._flotta()[0]
        self.assertEqual(riga["stato"][0], "a riposo")

    def test_the_state_column_says_what_changes_how_the_node_behaves(self):
        """La modalita' Prank e il dissuasore muto cambiano **cosa fara'** quel nodo adesso: in una
        tabella che si guarda per confrontare otto nodi, e' la cosa che non si puo' non vedere."""
        prank = dict(self.NODO, info=dict(self.NODO["info"], prank={"attiva": True, "restano_s": 600}))
        riga = self._flotta([prank])[0]
        self.assertIn("Prank", riga["stato"][0])
        self.assertEqual(riga["stato"][1], "attenzione", "non e' un nodo come gli altri")
        # Con la casa inserita la stessa modalita' non fara' partire niente: in una tabella di otto
        # nodi, «sta per succedere» e «non succedera'» non possono leggersi uguali.
        self.assertIn("muto", self._flotta([prank], armata=True)[0]["stato"][0])
        muto = dict(self.NODO, info=dict(self.NODO["info"], dissuasore_disponibile_s=0,
                                         dissuasore_riprende_fra_s=600))
        self.assertIn("muto", self._flotta([muto])[0]["stato"][0])
        pieno = dict(self.NODO, info=dict(self.NODO["info"], dissuasore_disponibile_s=120))
        self.assertEqual(self._flotta([pieno])[0]["stato"][0], "a riposo",
                         "col budget intero non c'e' niente da dire")

    def test_prank_hides_the_muted_deterrent_because_it_is_not_true(self):
        """Con la modalita' accesa il budget non si guarda: dire «dissuasore muto» sarebbe falso."""
        nodo = dict(self.NODO, info=dict(self.NODO["info"], dissuasore_disponibile_s=0,
                                         prank={"attiva": True, "restano_s": 600}))
        self.assertNotIn("muto", self._flotta([nodo])[0]["stato"][0])

    def test_a_disk_nearly_full_is_said_before_it_is_full(self):
        """Il disco pieno si scopre quando manca la registrazione: qui si vede prima."""
        def con(libero):
            nodo = dict(self.NODO, info=dict(self.NODO["info"],
                                             registrazione={"attiva": True, "registra": True,
                                                            "libero": libero, "totale": 100_000_000_000}))
            return self._flotta([nodo])[0]["spazio"]

        self.assertEqual(con(50_000_000_000)[1], "ok")
        self.assertEqual(con(10_000_000_000)[1], "attenzione")
        self.assertEqual(con(2_000_000_000)[1], "rotto")

    def test_the_column_that_justifies_the_screen(self):
        """Un nodo che non vede niente da giorni **non fa rumore**: e' in linea, verde, e non succede
        nulla. Le altre colonne dicono se il nodo sta bene; questa se sta servendo a qualcosa."""
        adesso = 10_000_000.0
        visto = self._flotta(node_cams={"n1": [{"cam_id": "c1"}]}, ultimi={"c1": adesso - 300})[0]
        self.assertEqual(visto["visto"][1], "ok")
        vecchio = self._flotta(node_cams={"n1": [{"cam_id": "c1"}]}, ultimi={"c1": adesso - 3 * 86400})[0]
        self.assertEqual(vecchio["visto"][1], "attenzione")
        self.assertIn("3", vecchio["visto"][0], "si dice da quanto, non solo che e' tanto")
        mai = self._flotta(node_cams={"n1": [{"cam_id": "c1"}]}, ultimi={})[0]
        self.assertEqual(mai["visto"][0], "mai")
        senza = self._flotta(node_cams={})[0]
        self.assertEqual(senza["visto"][1], "assente", "senza camere non c'e' niente da aspettarsi")

    def test_a_warning_says_what_it_is_and_how_to_fix_it(self):
        """Marco, guardando la tabella per la prima volta: *«mostra avvisi che poi non so cosa siano
        e come risolvere»*. La diagnosi ha per ogni problema il testo **e** il rimedio: ridurli a un
        numero vuol dire dire che c'e' qualcosa che non va e lasciare soli."""
        from mirto.actions.diagnosi import API_ATTESA, versione

        # Un nodo con l'API indietro di proposito: la versione si ricava da `API_ATTESA`, non si
        # scrive a mano in una fixture — il guardiano del 22/10 vieta la seconda cosa, e ha ragione.
        # «Una meno» si conta sui **pezzi**, non sul numero: `float("1.13") - 0.1` fa
        # 1.0300000000000002, che letto come versione diventa (1, 300000000000002) — piu' nuovo di
        # quello che aspettiamo. E' esattamente lo scivolone contro cui esiste `versione()`, e il
        # 25/10, passando l'API dalla 1.12 alla 1.13, questa riga l'ha rifatto.
        maggiore, minore = versione(API_ATTESA)[:2]
        vecchia = f"{maggiore}.{minore - 1}"
        vecchio = dict(self.NODO, meta=dict(versione="0.7.3", versione_api=vecchia))
        riga = self._flotta([vecchio])[0]
        self.assertIn("da guardare", riga["avvisi"][0])
        dettaglio = riga["avvisi_dettaglio"]
        self.assertEqual(len(dettaglio), int(riga["avvisi"][0].split()[0]),
                         "tanti quanti ne conta la cella: il numero e l'elenco non possono smentirsi")
        self.assertTrue(all("—" in d for d in dettaglio), "ogni avviso porta con se' il suo rimedio")

    def test_a_node_without_problems_has_nothing_to_explain(self):
        riga = self._flotta()[0]
        self.assertEqual(riga["avvisi_dettaglio"], [])

    def test_the_update_is_a_cell_and_a_button(self):
        riga = self._flotta(da_aggiornare={"n1": "0.13.0"})[0]
        self.assertTrue(riga["aggiornabile"], "il pulsante sta nella riga del nodo che riguarda")
        self.assertIn("0.13.0", riga["versione"][0])
        self.assertEqual(riga["versione"][1], "attenzione")
        self.assertFalse(self._flotta()[0]["aggiornabile"])

    def test_the_link_to_the_fleet_only_exists_with_nodes(self):
        from mirto.gui.sistema import riga_vista_flotta

        self.assertEqual(riga_vista_flotta(0), "", "senza nodi non si offre una tabella vuota")
        self.assertIn("sistema:flotta", riga_vista_flotta(3))
        self.assertIn("3", riga_vista_flotta(3))

    # --- Sistema non è solo da guardare: si tocca (25/10) --------------------

    def test_an_action_is_a_switch(self):
        """Marco: *«servirebbe poter cliccare tipo su disattivata per attivarla»*. Per riaccendere
        un'azione servivano sei gesti — apri Impostazioni, trova la scheda, trova la riga — per una
        cosa che si decide guardandola."""
        nodo = dict(self.NODO, actions=[{"name": "Luci dell'orto", "action": "luci", "kind": "light",
                                         "mode": "auto", "enabled": False}])
        testo = self.nodi(aperti={"n1"}, nodi=[nodo])
        self.assertIn("sistema:azione:Luci%20dell%27orto", testo,
                      "il nome viaggia nel collegamento, scritto in modo che un apostrofo non lo rompa")
        self.assertIn("disattivata", testo)
        acceso = dict(nodo, actions=[dict(nodo["actions"][0], enabled=True)])
        self.assertIn("sistema:azione:", self.nodi(aperti={"n1"}, nodi=[acceso]),
                      "si deve poter spegnere anche quella accesa, non solo accendere")

    def test_nothing_running_does_not_mean_nothing_enabled(self):
        """*«Nel nodo Ingresso c'è scritto tutto spento, ma è fuorviante: avevo un'azione attiva»*.
        Quella riga parla di cosa sta suonando **adesso**, non di cosa è abilitato."""
        testo = self.nodi()
        self.assertIn("tutto a riposo", testo)
        self.assertNotIn("tutto spento", testo)

    # --- i limiti, scritti dove si creano le azioni (26/10) -------------------

    def test_the_limits_are_written_where_the_actions_are_made(self):
        """Marco, 26/10: *«e' indubbiamente un casino far capire all'utente questa cosa dei budget
        senza che lui ne sia a conoscenza»*. Stavano solo nelle (i) accanto ai campi, cioe' dove si
        guarda **dopo** aver deciso — e il paragrafo che stava in cima diceva una cosa diventata
        falsa: *«partono solo con la protezione attiva, solo per le persone»*, vero finche' un'azione
        era solo un antifurto e smentito dal primo dissuasore."""
        from mirto.gui.testi import riassunto_limiti_azioni

        from mirto.gui.testi import tipologie_azioni

        quattro = "".join(r for _k, r in tipologie_azioni(0.6))
        tutto = riassunto_limiti_azioni(0.6, {"siren", "deterrent"})
        self.assertIn("60%", quattro, "la soglia e' quella vera, non un numero scritto a mano")
        self.assertIn("Nessun limite d'uso", quattro, "l'antifurto non si limita: deve suonare")
        self.assertIn("3 ogni mezz'ora", tutto)
        self.assertIn("2 minuti ogni ora", tutto)
        self.assertIn("nessun programma aggira", tutto, "e si dice chi li impone")
        self.assertIn("vicini", tutto, "e perche' esistono: un numero senza scopo si cerca di aggirare")
        # Del budget della sirena non si parla a chi non ne ha.
        self.assertNotIn("sirena", riassunto_limiti_azioni(0.6, {"deterrent"}))
        self.assertNotIn("dissuasore 30 secondi", riassunto_limiti_azioni(0.6, {"siren"}))

    def test_the_band_opens_with_the_four_kinds(self):
        """Marco, 27/10: *«voglio che la descrizione parta elencando le tipologie, che espliciti per
        ogni tipologia le sue caratteristiche»*.

        Prima apriva con «antifurto» e «sempre attive», che sono i valori di *quando è attiva*, non le
        cose che uno vuole fare: il campanello e il dissuasore erano esempi dentro una parentesi e lo
        scherzo stava in fondo. Chi cercava «come faccio un campanello» doveva dedurlo.
        """
        from mirto.gui.testi import riassunto_limiti_azioni

        from mirto.gui.testi import tipologie_azioni

        quattro = tipologie_azioni(0.6)
        self.assertEqual([k for k, _r in quattro],
                         ["antifurto", "campanello", "dissuasore", "prank"])
        from mirto.gui.testi import riquadro_tipologia

        for chiave, riga in quattro:
            self.assertIn(riquadro_tipologia(chiave), riga,
                          f"la riga di {chiave} non porta il suo riquadro")

    def test_every_kind_is_in_a_box_of_its_own_colour(self):
        """*«Dai enfasi alle tipologie elencate anche in riga con dei box e le icone.»*

        Il riquadro si fa col fondo, non col bordo: il testo ricco di Qt disegna «background-color» su
        uno span e ignora «border». Se un giorno qualcuno lo riscrive con un bordo, qui si vede.
        """
        from mirto.gui import colori
        from mirto.gui.testi import riassunto_limiti_azioni

        from mirto.gui.testi import tipologie_azioni

        testo = "".join(r for _k, r in tipologie_azioni(0.6))
        self.assertEqual(testo.count("background-color:"), 4, "un riquadro per tipologia")
        for colore in (colori.ALARM, colori.INFO, colori.OBJECT, colori.PAUSED):
            self.assertIn(f"color:{colore}", testo, "ogni tipologia col suo colore")
        for icona in ("🚨", "🔔", "🦅", "🎭"):
            self.assertIn(icona, testo)

    def test_a_box_never_breaks_in_half(self):
        """Marco, 27/10, guardando l'elenco: «Halloween 🔔» e sotto «Campanello» — il riquadro tagliato
        a meta' da un a capo. Lo spazio fra l'icona e la parola era spezzabile, e in una riga stretta
        Qt ci manda a capo."""
        from mirto.gui.testi import riquadro_tipologia

        for chiave in ("antifurto", "campanello", "dissuasore", "prank"):
            dentro = riquadro_tipologia(chiave).split("<b>")[1].split("</b>")[0]
            self.assertNotIn(" ", dentro, f"il riquadro di {chiave} si puo' spezzare")

    def test_the_tint_is_the_colour_over_the_panel(self):
        """Il fondo è una tinta, non il colore pieno: quattro riquadri saturi in fila urlano, e quello
        che deve risaltare è la parola, non la vernice."""
        from mirto.gui import colori
        from mirto.gui.testi import _tinta

        self.assertEqual(_tinta(colori.ALARM, quota=0.0), colori.PANEL.lower(), "zero = solo il fondo")
        self.assertEqual(_tinta(colori.ALARM, quota=1.0), colori.ALARM.lower(), "uno = solo il colore")
        tinta = _tinta(colori.ALARM)
        self.assertNotIn(tinta, (colori.ALARM.lower(), colori.PANEL.lower()))
        self.assertTrue(tinta.startswith("#") and len(tinta) == 7, tinta)

    def test_the_four_kinds_are_there_even_without_nodes(self):
        """Le tipologie sono un vocabolario: si leggono anche prima di avere un nodo. Quello che
        sparisce quando non serve sono i **limiti del nodo**, non le tipologie."""
        from mirto.gui.testi import riassunto_limiti_azioni

        from mirto.gui.testi import tipologie_azioni

        spoglio = "".join(r for _k, r in tipologie_azioni(0.6))
        for parola in ("Antifurto", "Campanello", "Dissuasore", "Prank"):
            self.assertIn(parola, spoglio)
        self.assertNotIn("3 ogni mezz'ora", riassunto_limiti_azioni(0.6),
                         "della sirena non si parla a chi non ne ha")

    def test_the_last_page_says_which_kind_it_will_be(self):
        """Marco, 27/10: *«anche nell'ultima pagina dove si sceglie di attivare o no la regola conviene
        dire all'utente quale tipologia gli verra' associata e quali sono i motivi / budget»*.

        E' il momento in cui si sta per accendere una cosa, e fino a un istante prima si sono
        guardati campi singoli. Il tipo non l'ha scelto lui: questo e' l'unico punto in cui puo'
        scoprirlo prima di scoprirlo dal comportamento.
        """
        from mirto.gui.testi import spiega_tipologia

        prank = spiega_tipologia("prank")
        self.assertIn("Prank", prank)
        self.assertIn("30 secondi", prank, "il limite che il nodo impone comunque")
        self.assertIn("protezione", prank, "e che tace a casa protetta")
        antifurto = spiega_tipologia("antifurto", 0.75)
        self.assertIn("75%", antifurto, "la soglia vera")
        self.assertIn("Nessun limite", antifurto)

    def test_the_last_page_says_the_limits_you_just_set(self):
        """I numeri **tuoi**, non quelli di serie: un budget raccontato in generale e' esattamente il
        problema da cui tutta questa storia e' partita."""
        from mirto.gui.testi import spiega_tipologia

        testo = spiega_tipologia("campanello", 0.6,
                                 {"cooldown": 180, "max_per_day": 12, "hours": "07:00-22:00"})
        self.assertIn("3 minuti", testo)
        self.assertIn("12 volte", testo)
        self.assertIn("07:00-22:00", testo)

    def test_a_row_in_the_list_says_its_kind(self):
        """*«Nella lista non c'e' evidenza della tipologia assegnata e quindi non si riesce a percepire
        come funzionera'.»* Ed e' la cosa che decide tutto il resto della riga."""
        from mirto.actions.pannello_azioni import righe_azioni
        from mirto.actions.presets import azione_per_scopo

        for chiave in ("antifurto", "campanello", "dissuasore", "prank"):
            a = azione_per_scopo(chiave, "n1", "x", "Prova")
            riga = righe_azioni([a], [{"id": "n1", "name": "Ingresso"}])[0]
            self.assertEqual(riga["tipologia"], chiave)

    def test_the_row_does_not_say_the_same_thing_twice(self):
        """Col riquadro accanto al nome, ripetere «🎭 solo a modalita' Prank accesa» nel sottotitolo e'
        la stessa frase due volte su una riga che deve stare corta."""
        from mirto.actions.pannello_azioni import righe_azioni
        from mirto.actions.presets import azione_per_scopo, describe_action

        a = azione_per_scopo("prank", "n1", "x", "Halloween")
        riga = righe_azioni([a], [{"id": "n1", "name": "Ingresso"}])[0]
        self.assertNotIn("modalità Prank accesa", riga["cosa"])
        self.assertIn("modalità Prank accesa", describe_action(a),
                      "ma in libreria, dove il riquadro non c'e', si dice ancora")

    def test_the_band_says_when_prank_is_on_right_now(self):
        from mirto.gui.testi import riassunto_limiti_azioni

        spento = riassunto_limiti_azioni(0.6, {"deterrent"})
        self.assertNotIn("accesa adesso", spento)
        acceso = riassunto_limiti_azioni(0.6, {"deterrent"}, ["Ingresso"])
        self.assertIn("accesa adesso su Ingresso", acceso,
                      "una modalita' che cambia le regole di cui parla questa fascia va detta qui")
        self.assertNotIn("disinserisci", acceso, "a casa aperta lo scherzo parte: niente da avvertire")
        armata = riassunto_limiti_azioni(0.6, {"deterrent"}, ["Ingresso"], armata=True)
        self.assertIn("la casa è protetta", armata, "accanto alla riga che la annuncia, non altrove")
        self.assertIn("disinserisci", armata, "e dice che riprendono, non che sono finiti")

    def test_the_band_says_a_prank_needs_the_house_open(self):
        """Il secondo cancello va scritto dove si leggono le regole, non solo dove si accende.

        La fascia prima diceva che a un prank serve **la finestra sul nodo**; da sola quella frase
        faceva credere che bastasse."""
        from mirto.gui.testi import riassunto_limiti_azioni

        from mirto.gui.testi import tipologie_azioni

        testo = dict(tipologie_azioni(0.6))["prank"]
        self.assertIn("casa non", testo)
        self.assertIn("due", testo, "due cose insieme, non una")

    # --- l'audio del microfono nelle registrazioni (26/10) --------------------

    def test_the_recording_audio_note_is_read_before_it_is_done(self):
        """Una spunta nuda qui sarebbe la cosa sbagliata: fino al 26/10 Mirto prometteva in tre
        punti dell'interfaccia e in tutt'e due le guide che l'audio non viene mai registrato. Non e'
        una comodita' in piu', e' un cambio di politica, e va letto prima di essere fatto."""
        from mirto.gui.testi import testo_audio_registrazione as testo

        vecchio = testo(None)
        self.assertIn("0.12.1", vecchio)
        self.assertNotIn("Garante", vecchio, "a un nodo che non la sa fare non si fa la lezione")
        spento = testo({"acceso": False, "attivo": False})
        self.assertIn("Garante", spento, "cosa comporta si legge prima di accenderlo")
        self.assertIn("cartello", spento)
        self.assertIn("spento", spento)
        acceso = testo({"acceso": True, "attivo": True})
        self.assertIn("muti", acceso, "e che le registrazioni di prima restano")
        fermo = testo({"acceso": True, "attivo": False, "perche": "il microfono del nodo e' spento"})
        self.assertIn("fermo", fermo)
        self.assertIn("microfono del nodo", fermo, "«acceso e fermo» dice anche perche'")

    # --- modalita' Prank e budget dei dissuasori (25/10) ----------------------
    #
    # Marco: *«una modalita' Prank che permetta un uso piu' goliardico senza budget dei suoni»* e
    # *«i messaggi sui budget devono essere chiari»*. La modalita' sospende una regola di sicurezza:
    # per questo si vede a riga **chiusa**, e il budget si mostra solo quando sta per finire.

    def test_the_prank_window_says_what_falls_and_what_stays(self):
        """La finestra che accende la modalita' ha un compito oltre all'interruttore: dire **cosa
        cade e cosa resta**. Un interruttore senza quelle righe nasconde la cosa piu' importante che
        fa — togliere una regola di sicurezza."""
        from mirto.gui.prank_ui import testo_prank

        spenta = testo_prank({"attiva": False, "restano_s": 0}, "Ingresso")
        self.assertIn("Spenta", spenta)
        self.assertIn("non hanno budget", spenta, "cosa cade")
        self.assertIn("30 secondi per scatto", spenta, "cosa resta")
        self.assertIn("si spegne da s", spenta)
        self.assertIn("Ferma tutto", spenta)
        accesa = testo_prank({"attiva": True, "restano_s": 1380}, "Ingresso")
        self.assertIn("Accesa su Ingresso", accesa)
        self.assertIn("23 minuti", accesa)

    def test_an_old_node_is_told_so_instead_of_being_offered_buttons(self):
        """Un nodo prima della 0.12.1 non ha `prank` nello stato: la finestra lo dice invece di
        offrire pulsanti che quel nodo rifiuterebbe."""
        from mirto.gui.prank_ui import testo_prank

        testo = testo_prank(None, "Bagno")
        self.assertIn("0.12.1", testo)
        self.assertIn("Stato e diagnosi", testo, "e dice dove si aggiorna")
        self.assertNotIn("Accendila", testo)

    def test_prank_is_visible_without_opening_the_node(self):
        acceso = dict(self.NODO, info=dict(self.NODO["info"],
                                           prank={"attiva": True, "restano_s": 1380}))
        testo = self.nodi(nodi=[acceso])
        self.assertIn("Prank", testo, "una modalita' che cambia le regole non sta dietro un «+»")
        self.assertIn("23 minuti", testo, "e dice quanto le resta")
        self.assertIn("sistema:prank:n1", testo, "da dove si legge che e' accesa, si spegne")

    def test_an_armed_house_says_the_prank_is_mute(self):
        """A casa inserita gli scherzi tacciono (26/10), e la finestra resta aperta.

        Senza questa parola si legge «🎭 ancora 23 minuti» accanto a un antifurto attivo e si aspetta
        uno scherzo che non arrivera' mai: il difetto non sarebbe nel comportamento ma nel fatto che
        la riga dice il contrario di quello che fara'.
        """
        acceso = dict(self.NODO, info=dict(self.NODO["info"],
                                           prank={"attiva": True, "restano_s": 1380}))
        testo = self.nodi(nodi=[acceso], armata=True)
        self.assertIn("muta", testo)
        self.assertIn("casa protetta", testo, "e dice **perche'** e' muta")
        self.assertIn("23 minuti", testo, "la finestra e' ancora aperta: i minuti restano veri")
        self.assertNotIn("muta", self.nodi(nodi=[acceso]), "a casa aperta non c'e' niente da dire")

    def test_prank_off_does_not_add_a_line(self):
        spento = dict(self.NODO, info=dict(self.NODO["info"],
                                           prank={"attiva": False, "restano_s": 0}))
        self.assertNotIn("Prank", self.nodi(nodi=[spento]),
                         "una riga per nodo che dice sempre «spenta» e' rumore")

    def test_the_deterrent_budget_shows_only_when_it_is_nearly_over(self):
        """Un contatore sempre pieno e' una riga che non dice mai niente — la lezione della sirena,
        tolta il 24/10. Gli ultimi trenta secondi invece sono la cosa da sapere prima di chiedersi
        perche' un'azione non e' partita."""
        pieno = dict(self.NODO, info=dict(self.NODO["info"], dissuasore_disponibile_s=120,
                                          dissuasore_riprende_fra_s=0))
        self.assertNotIn("Dissuasore", self.nodi(aperti={"n1"}, nodi=[pieno]))
        finito = dict(self.NODO, info=dict(self.NODO["info"], dissuasore_disponibile_s=0,
                                           dissuasore_riprende_fra_s=743))
        testo = self.nodi(aperti={"n1"}, nodi=[finito])
        self.assertIn("Dissuasore", testo)
        self.assertIn("12 minuti", testo, "non «limite raggiunto»: quando riprende")

    # --- il volume della cassa, cliccabile (25/10) ---------------------------
    #
    # Marco: *«nella finestra di sistema servirebbe avere sotto la cassa il livello del volume
    # modificabile al volo, sempre usando lo stile della finestra di sistema, tipo quadratini
    # cliccabili»*.

    def test_the_volume_is_ten_clickable_squares(self):
        from mirto.gui.sistema import riga_volume

        riga = riga_volume("n1", {"si_puo": True, "percento": 60, "scheda": "UACDemoV10",
                                  "manopola": "PCM,0"})
        self.assertEqual(riga.count("<a href='sistema:volume:n1:"), 10, "dieci passi, dieci bersagli")
        self.assertIn("sistema:volume:n1:10", riga, "il primo porta al 10 %")
        self.assertIn("sistema:volume:n1:100", riga, "l'ultimo al 100 %")
        self.assertEqual(riga.count("▮"), 6, "60 % sono sei quadratini pieni")
        self.assertEqual(riga.count("▯"), 4)
        self.assertIn("60 %", riga, "e il numero si legge, non si conta")

    def test_a_bluetooth_speaker_says_so_instead_of_faking_a_control(self):
        """Il nodo manda l'audio al profilo A2DP: la manopola sta sulla cassa. Un controllo che si
        muove senza che il suono cambi insegna che la finestra racconta storie."""
        from mirto.gui.sistema import riga_volume

        riga = riga_volume("n1", {"si_puo": False, "perche": "bluetooth"})
        self.assertIn("sulla cassa", riga)
        self.assertNotIn("sistema:volume:", riga, "nessun quadratino da cliccare")
        senza = riga_volume("n1", {"si_puo": False, "perche": "senza-manopola", "scheda": "x"})
        self.assertIn("non ha un volume", senza)
        self.assertEqual(riga_volume("n1", None), "", "un nodo prima della 0.12.1 non lo dice")
        self.assertEqual(riga_volume("n1", {"si_puo": False, "perche": "niente-cassa"}), "",
                         "nessuna cassa, nessuna riga")

    def test_the_volume_line_is_under_the_speaker(self):
        """È di quella cassa che si parla, e alzarlo è la cosa che si vuole fare subito dopo aver
        letto da dove suona."""
        nodo = dict(self.NODO, info=dict(self.NODO["info"],
                                         volume={"si_puo": True, "percento": 40, "scheda": "USB",
                                                 "manopola": "PCM,0"}))
        testo = self.nodi(aperti={"n1"}, nodi=[nodo])
        self.assertIn("Volume", testo)
        self.assertLess(testo.index("Cassa"), testo.index("Volume"), "sotto la cassa, non sopra")

    def test_an_empty_bar_says_why_it_is_empty(self):
        """Marco, 26/10: la barretta stava ferma e vuota, e il microfono funzionava — era il
        **guadagno** al minimo, che su un microfono USB e' il valore di fabbrica (misurato il 22/10:
        una voce all'1 % del fondo scala, cioe' sotto il primo quadratino). Una barra vuota senza una
        ragione accanto si legge come una funzione rotta: e' lo stesso difetto del budget dei
        dissuasori, un numero che non porta da nessuna parte."""
        def con(picco, livello=0.01):
            return self.nodi(aperti={"n1"}, livelli={"n1": {"livello": livello, "picco": picco}})

        piano = con(0.01)        # la voce col guadagno di fabbrica
        self.assertIn("troppo piano", piano)
        self.assertIn("Configurazione → Audio", piano, "e dove si alza")
        self.assertNotIn("troppo piano", con(0.38), "col guadagno giusto non si dice niente")
        self.assertIn("satura", con(0.99), "e anche il contrario si dice")
        # Il silenzio **non** si commenta: in una casa tranquilla e' lo stato normale, e «fai un
        # rumore» in una riga di riassunto e' un consiglio fuori posto.
        muto = con(0.001, livello=0.0)
        self.assertNotIn("guadagno", muto)
        self.assertIn("Microfono", muto, "la riga c'e' comunque, con la barretta vuota")

    def test_the_verdict_does_not_change_between_two_words(self):
        """Il giudizio sta sul **picco tenuto**, non sull'ultimo istante: fra una parola e l'altra il
        livello va a zero, e un consiglio che cambiasse li' manderebbe ad alzare un guadagno giusto."""
        fra_due_parole = self.nodi(aperti={"n1"},
                                   livelli={"n1": {"livello": 0.0, "picco": 0.38}})
        self.assertNotIn("guadagno", fra_due_parole)

    def test_a_microphone_mirto_is_not_listening_to_is_still_said(self):
        """Due fonti, due domande: **se c'e'** lo dice il nodo (API 1.13), **quanto sente** lo sa
        Mirto. Finche' la riga esisteva solo quando Mirto ascoltava, un microfono attaccato e
        funzionante che Mirto ignora non si nominava — e non sapere di avere un microfono e' il
        modo piu' rapido per non usarlo mai."""
        def con(mic, livelli=None):
            nodo = dict(self.NODO, info=dict(self.NODO["info"], microfono=mic))
            return self.nodi(aperti={"n1"}, nodi=[nodo], livelli=livelli)

        acceso = con({"attivo": True, "acceso_ora": True, "scheda": "USB"})
        self.assertIn("Microfono", acceso)
        self.assertIn("non lo ascolta", acceso, "e dice dove si accende")
        rotto = con({"attivo": True, "acceso_ora": False, "scheda": "USB"})
        self.assertIn("scheda non risponde", rotto,
                      "configurato ma la scheda non c'e' piu' non e' «nessun microfono»")
        spento = con({"attivo": False, "acceso_ora": False, "scheda": ""})
        self.assertIn("spento sul nodo", spento)
        # Quando Mirto ascolta, vince il livello: e' la cosa piu' utile delle quattro.
        self.assertIn("▮", con({"attivo": True, "acceso_ora": True}, livelli={"n1": {"livello": 0.05, "picco": 0.38}}))
        self.assertNotIn("Microfono", self.nodi(aperti={"n1"}),
                         "un nodo prima della 0.12.1 non lo dice: meglio tacere che inventare")

    def test_the_microphone_shows_what_it_hears(self):
        """La riga c'è se **Mirto ascolta** quel microfono, e il livello lo misura lui.

        Prima la condizione guardava lo stato periodico del nodo, dove il microfono non c'è: quel
        dato vive solo nella risposta che si chiede aprendo il pannello dell'audio. La riga non
        compariva mai, e la condizione sembrava giusta leggendola (25/10).
        """
        vivo = self.nodi(aperti={"n1"}, livelli={"n1": {"livello": 0.05, "picco": 0.38}})
        self.assertIn("Microfono", vivo)
        self.assertIn("▮", vivo, "si vede quanto sente")
        muto = self.nodi(aperti={"n1"}, livelli={"n1": None})
        self.assertIn("il flusso non arriva", muto,
                      "«lo ascolto e non sento niente» non è «non lo ascolto»: mandano a cercare altrove")

    def test_a_microphone_nobody_listens_to_is_not_invented(self):
        """Senza una sorgente, Mirto non sa se quel nodo abbia un microfono acceso: tace."""
        self.assertNotIn("Microfono", self.nodi(aperti={"n1"}))

    def test_a_closed_node_is_one_line_with_its_plus_and_its_five_signs(self):
        from mirto.actions.diagnosi import ICONE

        testo = self.nodi()
        self.assertEqual(testo.count("<br>"), 0, "chiuso, un nodo è una riga sola")
        self.assertIn("sistema:nodo:n1", testo, "il più porta la chiave del nodo, non un numero di riga")
        self.assertIn(">+<", testo)
        self.assertIn("Bagno", testo)
        for _cosa, segno in ICONE:
            self.assertIn(segno, testo, "i cinque segni ci sono sempre, anche quando una parte manca")
        for parola in ("Sirena", "Registra", "Rete", "JBL"):
            self.assertNotIn(parola, testo, f"«{parola}» è dentro, e chiuso non si deve vedere")

    def test_the_speaker_row_says_which_speaker(self):
        """Marco, 24/10: *«ho sia la cassa USB che la bluetooth collegate, ma dice uscita del
        Raspberry»*. Il nome vero lo sapevamo — il nodo manda uscita, scheda e nome — e non lo
        scrivevamo: una riga che non distingue due casse attaccate non serve a niente.
        """
        con_usb = dict(self.NODO, info=dict(self.NODO["info"],
                                            audio={"uscita": "usb", "scheda": "UACDemoV10",
                                                   "nome": "Cassa USB", "collegata": True}))
        testo = self.nodi(aperti={"n1"}, nodi=[con_usb])
        self.assertIn("Scheda USB", testo, f"si deve leggere quale uscita usa: {testo}")
        self.assertIn("Cassa USB", testo, "e come si chiama la scheda")
        self.assertNotIn("uscita del Raspberry", testo)

    def test_the_siren_counter_is_not_a_row_of_the_panel(self):
        """Tolta il 24/10: in una colonna che dice **com'e' messo il nodo**, un contatore sempre
        pieno e' una riga che non dice mai niente. Il limite resta, e si scopre dove conta."""
        testo = self.nodi(aperti={"n1"}, nodi=[self.NODO])
        self.assertNotIn("disponibili", testo)
        self.assertNotIn("max 3 min/30 min", testo)

    def test_an_open_node_puts_everything_back(self):
        con_azioni = dict(self.NODO, actions=[
            {"action": "sirena", "name": "Sirena dalla cassa (max 3 minuti) – Bagno", "kind": "audio",
             "mode": "confirm", "enabled": True},
            {"action": "suoni", "name": "Suoni contro uccelli e animali", "kind": "audio",
             "mode": "auto", "enabled": False}])
        testo = self.nodi(aperti={"n1"}, nodi=[con_azioni])
        self.assertIn(">−<", testo, "aperto il più diventa un meno")
        for parola in ("Sirena", "Registra", "Rete", "Wi-Fi «CasaMia»", "JBL Flip 6", "44 °C"):
            self.assertIn(parola, testo, parola)
        self.assertIn("Sirena dalla cassa", testo, "il nome dell'azione perde solo la parte fra parentesi")
        self.assertNotIn("max 3 minuti", testo)
        self.assertIn("disattivata", testo)

    def test_five_nodes_are_five_lines_and_the_signs_line_up(self):
        """Il motivo della richiesta: una casa con cinque nodi deve restare leggibile."""
        nodi = [dict(self.NODO, id=f"n{i}", name=f"Nodo {i}") for i in range(5)]
        testo = self.nodi(nodi=nodi)
        self.assertEqual(testo.count("<br>"), 4, "cinque nodi, cinque righe")
        for i in range(5):
            self.assertIn(f"sistema:nodo:n{i}", testo)
        aperto_uno = self.nodi(aperti={"n2"}, nodi=nodi)
        self.assertGreater(aperto_uno.count("<br>"), 4, "aprire un nodo apre solo quello")
        self.assertEqual(aperto_uno.count(">−<"), 1)
        self.assertEqual(aperto_uno.count(">+<"), 4)

    def test_the_signs_carry_the_colour_of_what_is_wrong(self):
        from mirto.gui import colori

        guasto = dict(self.NODO)
        guasto["info"] = dict(self.NODO["info"],
                              rete={"tipo": "wifi", "rete": "CasaMia", "segnale": 12},
                              audio={"uscita": "bluetooth", "collegata": False, "abbinata": False})
        testo = self.nodi(nodi=[guasto])
        self.assertIn(f"color:{colori.ALARM}'>♪", testo, "la sirena non può suonare: rosso")
        self.assertIn(f"color:{colori.PAUSED}'>⌁", testo, "Wi-Fi debole: ambra")
        sano = self.nodi()
        self.assertIn(f"color:{colori.ARMED}'>⌁", sano)
        self.assertIn(f"color:{colori.FAINT}'>▣", sano, "nessuna camera su questo nodo: grigio, non rosso")

    def test_a_node_that_does_not_answer_says_so_without_hiding_it_behind_a_plus(self):
        """Un nodo che non risponde è il caso in cui non si vuole dover cliccare niente."""
        testo = self.nodi(nodi=[{"id": "n9", "name": "Garage", "online": False, "error": "timeout dopo 5 s"}])
        self.assertIn("non risponde", testo)
        self.assertIn("timeout dopo 5 s", testo)
        self.assertNotIn("sistema:nodo:n9", testo)

    def test_the_cameras_that_are_off_are_a_number_until_you_open_them(self):
        from mirto.gui.sistema import cameras_detail

        cams = [{"id": "a", "name": "Bagno", "enabled": True, "connected": True, "status": "online", "kind": ""},
                {"id": "b", "name": "Fotocamera di iPhone 12 di Marco", "enabled": False, "connected": True, "kind": ""},
                {"id": "c", "name": "Fotocamera di MacBook Air", "enabled": False, "connected": True, "kind": ""}]
        chiusa = cameras_detail(cams)
        self.assertIn("Bagno", chiusa, "quella che sta guardando si vede sempre")
        self.assertIn("Spente: 2", chiusa)
        self.assertNotIn("iPhone", chiusa, "i nomi delle spente stanno in mezzo e non servono")
        aperta = cameras_detail(cams, aperto=True)
        self.assertIn("iPhone", aperta)
        self.assertIn("MacBook Air", aperta)

    def test_the_notification_channels_stay_and_their_details_do_not(self):
        from mirto.gui.sistema import notifications_summary

        canali = [("ok", "Telegram collegato", "Gruppo: MirtoAdmin<br>Bot: @MirtoBot"),
                  ("off", "Email off", "")]
        _l, _t, chiusa, espandibile = notifications_summary(canali, True)
        self.assertTrue(espandibile, "c'è qualcosa da aprire: il più deve comparire")
        self.assertIn("Telegram collegato", chiusa)
        self.assertIn("Email off", chiusa)
        self.assertNotIn("MirtoAdmin", chiusa)
        _l, _t, aperta, _e = notifications_summary(canali, True, aperto=True)
        self.assertIn("MirtoAdmin", aperta)
        self.assertIn("@MirtoBot", aperta)

    def test_the_system_lines_stay_out_of_qt(self):
        """Se qui rientra Qt, queste prove tornano a girare solo in CI: è la garanzia dello spostamento."""
        radice = Path(__file__).resolve().parents[1]
        for nome in ("mirto/gui/sistema.py", "mirto/gui/colori.py"):
            testo = (radice / nome).read_text(encoding="utf-8")
            with self.subTest(nome):
                self.assertIsNone(re.search(r"^\s*(from|import)\s+PySide6", testo, re.M),
                                  f"{nome} importa Qt: queste prove tornerebbero a girare solo in CI")

    def test_nothing_to_open_means_no_plus(self):
        """Senza dettagli il più non serve: un più che non apre niente è un invito a sbagliare."""
        from mirto.gui.sistema import notifications_summary

        _l, _t, _d, espandibile = notifications_summary([("off", "Email off", "")], False)
        self.assertFalse(espandibile)


class IconaNellaBarraTest(unittest.TestCase):
    """Fuori da macOS il glifo nell'area di notifica è pieno, non di contorno.

    Nella barra di Windows le icone vicine sono quadrati pieni e saturi: la sagoma disegnata con un
    filo grigio da 2 px spariva (W-01, 19/10 — «l'icona grigia non si nota»). Su macOS il contorno
    monocromo resta, perché è quello che la barra dei menu si aspetta.
    """

    @classmethod
    def setUpClass(cls):
        try:
            from PySide6.QtWidgets import QApplication
        except ImportError:
            raise unittest.SkipTest("PySide6 non installato")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def dentro_la_chioma(pm):
        """Il colore in un punto che sta **dentro la chioma**, lontano dalla lente e dai tratti.

        Il disegno vive in un quadrato di 22 unità: il punto (7, 13) è nella massa a sinistra, a
        quattro unità dal centro della lente (raggio 3) e a più di quattro dal bordo della sagoma,
        quindi fuori dal tratto del contorno (penna da 2). Lì l'arbusto pieno ha il colore dello
        stato e quello di contorno non ha niente: è la differenza che si vede nella barra.

        Il punto era (7, 17) finché il glifo era una casetta: con l'arbusto, il 7/11, quell'altezza
        è finita addosso al tratto della base, che passa a 17,4. Un punto va sempre rimisurato sul
        disegno che c'è, non lasciato dov'era.

        Contare i pixel di tutta l'immagine non funzionava — un contorno da 2 px con il pallino copre
        già più di metà di quanto copra la sagoma piena, e la soglia «il doppio» che avevo scelto al
        buio è stata bocciata dalla CI (854 contro 571). Un punto preciso non ha soglie da indovinare.
        """
        img = pm.toImage()
        fattore = img.width() / 22
        return img.pixelColor(int(7 * fattore), int(13 * fattore))

    def test_inside_the_canopy_only_the_filled_glyph_is_painted(self):
        from mirto.gui.app import MainWindow

        piena = self.dentro_la_chioma(MainWindow.tray_pixmap("#2FB383", 22, pieno=True))
        contorno = self.dentro_la_chioma(MainWindow.tray_pixmap("#2FB383", 22, pieno=False))
        self.assertGreater(piena.alpha(), 200, "dentro la chioma l'arbusto pieno deve essere dipinto")
        self.assertLess(contorno.alpha(), 50, "quello di contorno, nello stesso punto, è vuoto")
        self.assertEqual((piena.red(), piena.green(), piena.blue()), (0x2F, 0xB3, 0x83),
                         "il colore dentro è quello dello stato, che è ciò che si legge da lontano")


class FinestreDiDialogoTest(unittest.TestCase):
    """Le finestre che si aprono di rado devono almeno **importarsi**.

    Scrivo l'interfaccia senza poterla eseguire — PySide6 non si installa nell'ambiente dove lavoro —
    e il 19/10 questo è già costato una CI rossa. Queste due finestre si aprono solo abbinando un nodo
    o toccando la sua camera: un errore di battitura in un nome di classe si scoprirebbe in casa di un
    tester. Qui si costruiscono davvero, con uno stato finto, e questo la CI lo esegue.
    """

    @classmethod
    def setUpClass(cls):
        try:
            from PySide6.QtWidgets import QApplication
        except ImportError:
            raise unittest.SkipTest("PySide6 non installato")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication([])

    def test_the_first_step_offers_the_three_kinds_of_action(self):
        """Marco, 26/10: *«la scelta la metterei nel primo step»*. Le tre scelte cambiano le regole, e
        la terza — Prank — e' l'unica che puo' suonare verso una persona: va letta prima, non
        scoperta dopo."""
        from mirto.gui.settings import ActionDialog

        dlg = ActionDialog({"name": "x", "node": "n1", "action": "sirena", "kind": "siren"},
                           "n1", [])
        valori = [dlg.quando.itemData(i) for i in range(dlg.quando.count())]
        self.assertEqual(sorted(valori), ["prank", "protetta", "sempre"])
        prank = dlg.quando.itemText(valori.index("prank"))
        self.assertIn("Prank", prank)
        self.assertIn("nodo", prank, "e dice subito che dipende dalla modalita' sul nodo")

    # --- modalita' Prank: la finestra che la accende (25/10) ----------------

    def test_the_prank_window_has_a_button_for_every_length(self):
        from mirto.gui.prank_ui import DURATE, costruisci_prank
        from PySide6.QtWidgets import QPushButton

        chiesti = []
        dlg = costruisci_prank(None, "Ingresso", {"attiva": False, "restano_s": 0},
                               lambda m: chiesti.append(m) or f"accesa per {m}", lambda: "spenta")
        testi = [b.text() for b in dlg.findChildren(QPushButton)]
        for minuti in DURATE:
            self.assertTrue(any(str(minuti) in x for x in testi), f"manca il pulsante da {minuti} min")
        self.assertFalse(any("Spegni" in x for x in testi), "spenta non si spegne")
        primo = next(b for b in dlg.findChildren(QPushButton) if str(DURATE[0]) in b.text())
        primo.click()
        self.assertEqual(chiesti, [DURATE[0]], "il pulsante chiede **quella** durata")
        # `isVisible()` guarda anche i genitori, e questa finestra non e' mai stata mostrata: sarebbe
        # falso comunque, e la prova direbbe «rotto» per il motivo sbagliato. `isHidden()` guarda lo
        # stato di **questo** riquadro, che e' quello che il codice decide.
        self.assertFalse(dlg.esito.isHidden(), "e la risposta del nodo si legge")
        self.assertIn(str(DURATE[0]), dlg.esito.text(), "ed e' quella che il nodo ha risposto")

    def test_the_prank_window_can_switch_it_off(self):
        from mirto.gui.prank_ui import costruisci_prank
        from PySide6.QtWidgets import QPushButton

        spenti = []
        dlg = costruisci_prank(None, "Ingresso", {"attiva": True, "restano_s": 600},
                               lambda m: "", lambda: spenti.append(True) or "spenta")
        off = next(b for b in dlg.findChildren(QPushButton) if "Spegni" in b.text())
        off.click()
        self.assertEqual(spenti, [True])

    def test_an_old_node_gets_no_buttons_at_all(self):
        from mirto.gui.prank_ui import costruisci_prank
        from PySide6.QtWidgets import QPushButton

        dlg = costruisci_prank(None, "Bagno", None, lambda m: "", lambda: "")
        testi = [b.text() for b in dlg.findChildren(QPushButton)]
        self.assertEqual([x for x in testi if "min" in x], [],
                         "un nodo che non la sa fare non si prende pulsanti che rifiuterebbe")

    # --- di chi e' questa immagine (25/10) ---------------------------------
    #
    # Marco: *«quella e' la cam del MacBook Air e non dell'iPhone»*. Nome e ID li da' il sistema, la
    # camera la apre OpenCV **per posizione**, e i due ordini non coincidono. Quell'ordine non si
    # legge da fuori — la sonda ha escluso tutt'e due le strade automatiche — quindi lo dice chi
    # guarda. E la domanda e' quella giusta: «qual e' l'indice?» e' una domanda nostra, e chi guarda
    # non puo' saperla; «di chi e' questa immagine?» la sa, perche' ha la faccia davanti.

    def test_the_swap_button_appears_only_when_there_is_someone_to_swap_with(self):
        from mirto.gui.widgets import CameraCard

        base = {"id": "mac:A", "name": "MacBook", "original": "MacBook", "kind": "Integrata",
                "detail": "", "enabled": True, "connected": True, "status": "online",
                "node": "", "node_name": ""}
        sola = CameraCard({**base, "scambiabile": False})
        self.assertTrue(sola.wrong_btn.isHidden(), "con una camera sola non c'e' nessuno scambio possibile")
        due = CameraCard({**base, "scambiabile": True})
        self.assertFalse(due.wrong_btn.isHidden())

    def test_the_swap_is_not_asked_where_there_is_no_picture(self):
        """Senza immagine davanti, «non e' questa?» e' una domanda a cui non si puo' rispondere."""
        from mirto.gui.widgets import CameraCard

        spenta = {"id": "mac:A", "name": "MacBook", "original": "MacBook", "kind": "Integrata",
                  "detail": "", "enabled": False, "connected": True, "status": "",
                  "node": "", "node_name": "", "scambiabile": True}
        self.assertTrue(CameraCard(spenta).wrong_btn.isHidden())

    def test_the_card_says_which_camera_is_asking(self):
        from mirto.gui.widgets import CameraCard

        cam = {"id": "mac:TEL", "name": "iPhone 12", "original": "iPhone 12", "kind": "iPhone",
               "detail": "", "enabled": True, "connected": True, "status": "online",
               "node": "", "node_name": "", "scambiabile": True}
        card = CameraCard(cam)
        visti = []
        card.wrong_camera.connect(visti.append)
        card.wrong_btn.click()
        self.assertEqual(visti, ["mac:TEL"])

    # --- aggiornare il nodo da Mirto (N-02, 25/10) ------------------------

    def _stato_dialog(self, versione_nodo: str, aggiorna=None):
        from mirto.gui.stato_ui import StatoDialog

        letto = {"info": {"nome": "Ingresso", "versione": versione_nodo, "versione_api": "1.12",
                          "tipo": "raspberry", "modello": "Pi 4", "camere": [], "dispositivi": [],
                          "audio": {}},
                 "stato": {"nome": "Ingresso", "versione": versione_nodo, "attive": {}},
                 "camera": {}}

        class Ponte:
            def leggi(self):
                return letto

        ponte = Ponte()
        if aggiorna is not None:
            ponte.aggiorna = aggiorna
        return StatoDialog("Ingresso", ponte), letto

    def test_the_update_button_is_offered_only_to_an_older_node(self):
        """Un pulsante che non ha niente da fare e' peggio di un pulsante che non c'e'."""
        from mirto.actions.aggiorna_nodo import versione_che_abbiamo

        nostra = versione_che_abbiamo()
        self.assertTrue(nostra, "questa copia deve portarsi dietro il codice del nodo")
        pari, _l = self._stato_dialog(nostra)
        self.assertTrue(pari.aggiorna_nodo_btn.isHidden(), "il nodo e' gia' alla nostra versione")
        vecchio, _l2 = self._stato_dialog("0.1.0")
        self.assertFalse(vecchio.aggiorna_nodo_btn.isHidden())

    def test_a_node_that_does_not_say_its_version_is_not_pushed_an_update(self):
        dlg, _l = self._stato_dialog("")
        self.assertTrue(dlg.aggiorna_nodo_btn.isHidden(), "non si sa cos'ha: non si decide per lui")

    def test_the_two_buttons_do_not_both_say_update(self):
        """«Aggiorna» voleva dire «rileggi lo stato». Accanto a un pulsante che aggiorna il **nodo**
        sarebbe una trappola: quello che rilegge si chiama come quello che fa."""
        dlg, _l = self._stato_dialog("0.1.0")
        self.assertNotIn(dlg.rileggi_btn.text(), dlg.aggiorna_nodo_btn.text())
        self.assertNotEqual(dlg.rileggi_btn.text(), dlg.aggiorna_nodo_btn.text())

    def test_the_waiting_stops_when_the_node_comes_back_new(self):
        """L'installazione riavvia il nodo, quindi la risposta non puo' dire «fatto»: si guarda la
        versione finche' cambia. Qui il nodo «torna» gia' aggiornato, e l'attesa deve finire."""
        from mirto.actions.aggiorna_nodo import versione_che_abbiamo

        dlg, letto = self._stato_dialog("0.1.0", aggiorna=lambda *a: {"esito": "ok", "in_corso": True})
        dlg._tentativi = 0
        letto["info"]["versione"] = versione_che_abbiamo()
        letto["stato"]["versione"] = versione_che_abbiamo()
        dlg._guarda_se_e_tornato()
        self.assertIsNone(dlg._attesa, "l'attesa deve smettere, se no guarda per sempre")
        self.assertIn(versione_che_abbiamo(), dlg.nota.text())

    def test_a_node_that_failed_to_install_says_so(self):
        dlg, letto = self._stato_dialog("0.1.0", aggiorna=lambda *a: {"in_corso": True})
        dlg._versione_chiesta = "0.12.0"
        letto["stato"]["aggiornamento"] = {"esito": "fallito", "versione": "0.12.0",
                                           "messaggio": "non e' andata a buon fine"}
        dlg._guarda_se_e_tornato()
        self.assertIsNone(dlg._attesa)
        self.assertIn("buon fine", dlg.nota.text(), "il motivo si dice, non si nasconde")

    def test_the_outcome_of_the_previous_attempt_does_not_speak_for_this_one(self):
        """Il 26/10: un «fallito» rimasto dal tentativo di stamattina ha fatto dire «il nodo non e'
        riuscito a installare» mentre l'installazione nuova stava ancora andando. Un esito senza un
        tentativo a cui appartenere ha l'aria di una risposta, ed e' peggio di nessun esito."""
        from PySide6.QtCore import QTimer

        dlg, letto = self._stato_dialog("0.1.0", aggiorna=lambda *a: {"in_corso": True})
        dlg._attesa = QTimer(dlg)
        dlg._versione_chiesta = "0.12.1"
        letto["stato"]["aggiornamento"] = {"esito": "fallito", "versione": "0.12.0",
                                           "messaggio": "roba di stamattina"}
        dlg._guarda_se_e_tornato()
        self.assertIsNotNone(dlg._attesa, "si continua ad aspettare: quell'esito non e' di adesso")
        self.assertNotIn("roba di stamattina", dlg.nota.text())
        self.assertIn("finendo l", dlg.nota.text(), "e si dice che sta finendo")

    def test_it_never_asks_the_user_to_press_anything(self):
        """Marco, 26/10, aggiornando il 3B: *«ha scritto di premere RILEGGI perche' non rispondeva
        piu', ma e' andato»*, e *«si potrebbe cercare di non fargli fare rileggi, all'utente sembra
        una cosa che ha funzionato male»*. I due minuti erano tarati su un Pi 4, e un'attesa che
        finisce chiedendo di premere un pulsante racconta un guasto che non c'e'."""
        from PySide6.QtCore import QTimer

        from mirto.gui.stato_ui import ATTESA_NORMALE_S, ATTESA_PASSO_MS

        def muto(self):
            raise RuntimeError("non risponde: si sta riavviando")

        dlg, _letto = self._stato_dialog("0.1.0", aggiorna=lambda *a: {"in_corso": True})
        dlg.ponte.leggi = muto.__get__(dlg.ponte)
        dlg._attesa = QTimer(dlg)
        # Due minuti: prima qui si arrendeva, e su un 3B il nodo stava solo lavorando.
        dlg._tentativi = int(120 / (ATTESA_PASSO_MS / 1000))
        dlg._guarda_se_e_tornato()
        self.assertIsNotNone(dlg._attesa, "a due minuti non si smette")
        self.assertNotIn("Rileggi", dlg.nota.text())
        self.assertIn("Aspetto", dlg.nota.text())
        self.assertIn("min", dlg.nota.text(), "e si vede da quanto: un'attesa muta sembra piantata")
        # Molto oltre: non si smette nemmeno li', si guarda piu' piano e si dice che ci mette di piu'.
        dlg._tentativi = int((ATTESA_NORMALE_S + 60) / (ATTESA_PASSO_MS / 1000))
        dlg._guarda_se_e_tornato()
        self.assertIsNotNone(dlg._attesa, "e nemmeno dopo: finche' la finestra e' aperta si guarda")
        self.assertNotIn("Rileggi", dlg.nota.text())
        self.assertIn("del solito", dlg.nota.text())
        self.assertGreater(dlg._attesa.interval(), ATTESA_PASSO_MS, "si guarda piu' piano")

    # --- la scheda AI, al posto di «Cosa sorvegliare» e «Suoni» (25/10) ----

    def _impostazioni(self):
        """La finestra delle impostazioni con un core finto: quello che serve per guardare le schede."""
        import types

        from mirto.config import Config
        from mirto.gui.settings import SettingsDialog

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = Config(Path(tmp.name) / "c.yaml")
        camere = types.SimpleNamespace(all_cameras=lambda: [])
        # Due cose che la finestra tocca **mentre si costruisce**, non quando apri quella scheda:
        # `presence` (la scheda Presenza si popola subito) e `secrets` (il campo del token Telegram
        # chiede al portachiavi se c'e' gia' qualcosa). Un None qui non e' un errore in quella
        # scheda: e' un errore all'apertura della finestra, cioe' tutte le prove rosse insieme.
        presenza = types.SimpleNamespace(enabled=False, people=lambda: [], last_check=None)

        class Portachiavi:
            def __init__(self):
                self.dati = {}

            def get(self, nome):
                return self.dati.get(nome, "")

            def set(self, nome, valore):
                self.dati[nome] = valore

            def delete(self, nome):
                self.dati.pop(nome, None)

        core = types.SimpleNamespace(config=cfg, cameras=camere, actions=None, notifier=None,
                                     telemetry=None, secrets=Portachiavi(), recorder=None,
                                     schedule=None, presence=presenza, audio=None,
                                     # `_save` la chiama in fondo: senza, provare il salvataggio
                                     # darebbe un errore che non c'entra niente con quello che si prova.
                                     reload_config=lambda: None)
        return SettingsDialog(core)

    def test_saving_clears_the_unsaved_badge(self):
        """Marco, 27/10: *«rimane il messaggio nella lista "da salvare" anche se ho gia' salvato»*.

        L'elenco scrive «DA SALVARE» confrontando i nomi con quelli **in configurazione**. Scritta la
        configurazione quel confronto e' cambiato, ma nessuno rifaceva l'elenco: la scritta ambra
        restava. Si vedeva solo premendo Salva **senza chiudere**, che dal 23/10 e' il modo normale di
        usare questa finestra -- «salvare non chiude», chiesto da lui.
        """
        from unittest import mock

        from PySide6.QtWidgets import QLabel

        dlg = self._impostazioni()
        dlg._actions = [{"name": "Campanello", "node": "n1", "action": "audio_generato",
                         "kind": "generic", "quando": "sempre", "targets": ["person"],
                         "enabled": True}]
        dlg._refresh_tutte_le_azioni()

        def scritte():
            return " ".join(lb.text() for c in dlg.azioni_cards._cards
                            for lb in c.findChildren(QLabel))

        self.assertIn("DA SALVARE", scritte(), "prima di salvare lo dice, ed e' giusto")
        with mock.patch("mirto.gui.settings.QMessageBox") as box:
            dlg._save(close=False)
            self.assertFalse(box.warning.called, f"il salvataggio ha protestato: {box.warning.call_args}")
        self.assertNotIn("DA SALVARE", scritte(), "salvata: la scritta deve sparire da sola")
        self.assertEqual([a["name"] for a in (dlg.cfg.get("actions.items") or [])], ["Campanello"],
                         "e in configurazione c'e' davvero")

    def test_the_two_tabs_became_one_called_ai(self):
        """Marco, 25/10: *«i tab cosa sorvegliare e suoni devono andare in un tab solo chiamato AI»*."""
        dlg = self._impostazioni()
        nomi = [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())]
        self.assertIn("AI", nomi)
        self.assertNotIn("Cosa sorvegliare", nomi)
        self.assertNotIn("Suoni", nomi, "i suoni del nodo stanno in Nodi, non in una scheda loro")
        self.assertLess(nomi.index("AI"), nomi.index("Registrazione"), "AI sta subito dopo Generale")

    def test_ai_is_split_in_cameras_and_microphones(self):
        """Due modelli diversi, due soglie diverse: nella stessa pagina si confondevano."""
        dlg = self._impostazioni()
        sotto = [dlg.ai_tabs.tabText(i) for i in range(dlg.ai_tabs.count())]
        self.assertEqual(sotto, ["Camere", "Microfoni"])

    def test_the_model_moved_out_of_general_next_to_what_it_judges(self):
        """Un modello e la sua soglia sono la stessa decisione: separarli vuol dire cambiarne uno
        senza vedere l'altro. Qui si prova **dove stanno**, non che esistano."""
        dlg = self._impostazioni()
        cam = dlg.ai_tabs.widget(0)
        for campo in (dlg.model, dlg.n_thr, dlg.o_thr):
            self.assertTrue(cam.isAncestorOf(campo), "deve stare nella sottoscheda delle camere")
        generale = dlg.tabs.widget(0)
        self.assertFalse(generale.isAncestorOf(dlg.model), "in Generale non c'e' piu'")

    def test_the_microphone_things_are_in_the_microphone_tab(self):
        dlg = self._impostazioni()
        audio = dlg.ai_tabs.widget(1)
        for campo in (dlg.mac_mic, dlg.mac_cam, dlg.smoke_always):
            self.assertTrue(audio.isAncestorOf(campo))
        # La chiave vera la dice `SOUNDS`, e qui si prende da li' invece di scriverla a mano: la
        # prima stesura diceva «glass_break», che non esiste — un nome indovinato invece che letto.
        from mirto.audio.classes import SOUNDS

        for tipo in SOUNDS:
            self.assertTrue(audio.isAncestorOf(dlg.sound_boxes[tipo]), tipo)

    def test_nothing_was_lost_moving_the_tabs(self):
        """Il salvataggio legge gli stessi campi di prima: spostare una scheda non deve perderne uno.

        E' la prova che vale di piu' di tutte le altre di questo giro: un campo rimasto indietro non
        da' nessun errore — semplicemente quella impostazione smette di salvarsi, e si scopre fra
        settimane quando qualcosa non si ricorda piu' com'era.
        """
        dlg = self._impostazioni()
        for nome in ("model", "n_thr", "o_thr", "watch_boxes", "watch_other", "bird_species",
                     "mac_mic", "mac_cam", "sound_boxes", "smoke_always"):
            self.assertTrue(hasattr(dlg, nome), f"«{nome}» non c'e' piu': il salvataggio lo cerca")

    def test_the_node_sounds_live_with_the_node(self):
        """Non sono cose che Mirto riconosce: sono cose che un nodo suona, e appartengono al nodo.

        Cosi' sparisce da se' il titolo «I suoni del nodo» su una casa con cinque nodi — che era il
        difetto che Marco aveva visto: il titolo non si sbaglia se non lo devi scrivere.
        """
        dlg = self._impostazioni()
        sotto = [dlg.node_tabs.tabText(i) for i in range(dlg.node_tabs.count())]
        self.assertIn("Suoni", sotto)
        pagina = dlg.node_tabs.widget(sotto.index("Suoni"))
        self.assertTrue(pagina.isAncestorOf(dlg.suoni_cards))
        self.assertTrue(pagina.isAncestorOf(dlg.btn_suono_nuovo), "il caricamento resta")
        self.assertFalse(hasattr(dlg, "suoni_nodo"),
                         "niente tendina per scegliere il nodo: il nodo lo sceglie la pagina")

    def test_the_sound_rows_are_compact(self):
        dlg = self._impostazioni()
        self.assertTrue(dlg.suoni_cards.compatta, "una decina di suoni per nodo: righe basse")

    def test_general_keeps_only_what_is_really_general(self):
        """Una scheda «Generale» che contiene un po' di tutto non e' generale: e' il posto dove
        finisce quello che non si e' deciso dove mettere (Marco, 25/10)."""
        dlg = self._impostazioni()
        generale = dlg.tabs.widget(0)
        for campo, dove in ((dlg.cooldown, "AI → Camere"), (dlg.retention, "Registrazione"),
                            (dlg.model, "AI → Camere")):
            self.assertFalse(generale.isAncestorOf(campo), f"doveva andare in {dove}")
        self.assertTrue(generale.isAncestorOf(dlg.language), "la lingua e' generale davvero")

    def test_the_pause_went_next_to_the_threshold(self):
        """Sono le due manopole con cui si litiga quando gli avvisi sono troppi: una decide **se**
        avvisare, l'altra **quanto spesso**. Separate, si cerca in due schede la stessa cosa."""
        dlg = self._impostazioni()
        cam = dlg.ai_tabs.widget(0)
        self.assertTrue(cam.isAncestorOf(dlg.cooldown))
        self.assertTrue(cam.isAncestorOf(dlg.n_thr))

    def test_keeping_things_is_decided_where_things_are_kept(self):
        dlg = self._impostazioni()
        nomi = [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())]
        registrazione = dlg.tabs.widget(nomi.index("Registrazione"))
        self.assertTrue(registrazione.isAncestorOf(dlg.retention))

    def test_the_checkboxes_are_a_column_like_the_other_fields(self):
        """Marco: *«metti prima il testo e poi la checkbox, cosi' risulta disturbante»*.

        Le spunte avevano il testo attaccato a destra del quadratino, mentre ogni altra riga ha
        l'etichetta a sinistra e il campo a destra: due allineamenti nella stessa colonna. Adesso la
        spunta e' nuda e la domanda sta nell'etichetta — e il testo che aveva addosso non si e'
        perso, e' diventato il suggerimento.
        """
        dlg = self._impostazioni()
        for cb in (dlg.autostart, dlg.prevent_sleep, dlg.close_background, dlg.tm_stats,
                   dlg.tm_updates, dlg.rec_enabled):
            self.assertEqual(cb.text(), "", "il testo sta nell'etichetta, non sulla spunta")
        for cb in (dlg.autostart, dlg.prevent_sleep, dlg.close_background, dlg.tm_stats):
            self.assertTrue(cb.toolTip(), "quello che non ci sta in una riga breve va nel suggerimento")

    def test_the_other_categories_are_behind_a_button(self):
        """Marco: *«fallo diventare "mostra tutte le categorie": inutile averlo li', e' inusabile»*.

        Cinquantasei voci in un elenco alto tre righe si leggono una per volta, e nessuno le legge.
        Il punto della prova non e' il pulsante: e' che le spunte **vivono ancora dove il
        salvataggio le cerca**. Nasconderle avrebbe potuto voler dire perderle.
        """
        from PySide6.QtCore import Qt

        dlg = self._impostazioni()
        cam = dlg.ai_tabs.widget(0)
        self.assertTrue(cam.isAncestorOf(dlg.btn_altre), "il pulsante sta in AI → Camere")
        self.assertTrue(dlg.watch_other.isHidden(), "l'elenco non si vede piu'")
        self.assertGreater(dlg.watch_other.count(), 40, "le categorie ci sono tutte, solo nascoste")
        voce = dlg.watch_other.item(0)
        voce.setCheckState(Qt.CheckState.Checked)
        self.assertIn(voce.data(Qt.ItemDataRole.UserRole), dlg._watched(),
                      "il salvataggio legge ancora da li'")
        dlg._conta_altre()
        self.assertIn(voce.text(), dlg.altre_conto.text(),
                      "chi ha acceso una categoria mesi fa lo deve poter vedere senza aprire nulla")

    def test_the_boxes_are_packed_to_the_left(self):
        """*«Sfrutta lo spazio tra le checkbox per occupare meno spazio possibile»*.

        Erano quattro per riga su tutta la larghezza: per andare da «Gatto» a «Cane» l'occhio
        attraversava mezzo schermo di vuoto. La colonna in piu' a destra e' quella che si prende
        tutto quello che avanza, e tiene le altre larghe quanto il loro testo.
        """
        dlg = self._impostazioni()
        self.assertGreaterEqual(dlg.watch_colonne, 6)
        self.assertEqual(dlg.watch_grid.columnStretch(dlg.watch_colonne), 1,
                         "lo spazio che avanza va tutto a destra, non fra una spunta e l'altra")
        for c in range(dlg.watch_colonne):
            self.assertEqual(dlg.watch_grid.columnStretch(c), 0)

    def test_a_three_digit_field_is_three_digits_wide(self):
        """*«I tipi di input sono sempre enormi: ci si attende 3 caratteri e hanno lunghezza immensa»*.

        Il tetto era 200 pixel per tutti e, con `AllNonFixedFieldsGrow`, tutti ci arrivavano. Un
        campo lungo promette che dentro ci vada qualcosa di lungo: con «0.95» dentro la promessa e'
        falsa. Adesso la larghezza la decide il numero piu' lungo che quel campo accetta.

        Questa prova guarda anche una cosa che non si vede leggendo il codice: che la misura sia
        fatta **quando i campi sono dentro la finestra**. Il tetto dei 200 pixel era scritto bene e
        girava a vuoto — cercava i campi fra i figli del dialogo prima che le schede ci fossero, e
        non ne trovava nessuno. Se qualcuno rimette quella riga troppo in alto, qui il massimo torna
        a essere i sedici milioni di pixel di serie e la prova diventa rossa.
        """
        dlg = self._impostazioni()
        for campo, nome in ((dlg.n_thr, "soglia persona"), (dlg.o_thr, "soglia oggetti"),
                            (dlg.cooldown, "pausa fra due avvisi"), (dlg.retention, "conservazione")):
            self.assertLessEqual(campo.maximumWidth(), 120, f"«{nome}» e' ancora larghissimo")
            self.assertGreaterEqual(campo.maximumWidth(), 78, f"«{nome}» non ci sta piu' dentro")

    # --- un aggiornamento del nodo si deve vedere da dove si guarda (25/10) ---
    #
    # Marco, davanti a «Stato e diagnosi»: *«e' troppo nascosto. Se c'e' un aggiornamento disponibile
    # per il nodo devo saperlo sia da NODI che da Sistema e raggiungere velocemente la
    # funzionalita'»*. Il pulsante c'era: stava a tre clic, dentro una finestra che si apre quando
    # qualcosa non va — cioe' nel posto dove si cerca un guasto, non dove si guardano i nodi.

    def _impostazioni_con_nodo(self, versione: str):
        import types

        dlg = self._impostazioni()
        dlg.cfg.set("actions.nodes", [{"id": "n1", "name": "Ingresso", "url": "https://n1:8443"}])
        dlg.core.actions = types.SimpleNamespace(
            node_online={"n1": True}, node_meta={"n1": {"versione": versione, "modello": "Pi 4"}},
            node_info={"n1": {}}, node_overview=lambda: [])
        return dlg

    def test_the_banner_says_a_node_can_be_updated(self):
        from mirto.actions.aggiorna_nodo import versione_che_abbiamo

        dlg = self._impostazioni_con_nodo("0.1.0")
        dlg._mostra_nastro_aggiornamento()
        self.assertFalse(dlg.agg_nastro.isHidden(), "il nastro c'e' quando c'e' qualcosa da fare")
        self.assertIn("Ingresso", dlg.agg_testo.text())
        self.assertIn("0.1.0", dlg.agg_testo.text(), "da quale versione")
        self.assertIn(versione_che_abbiamo(), dlg.agg_testo.text(), "e a quale")

    def test_a_node_already_up_to_date_shows_nothing(self):
        """Un nastro che c'e' sempre non lo legge piu' nessuno: parla solo quando serve."""
        from mirto.actions.aggiorna_nodo import versione_che_abbiamo

        dlg = self._impostazioni_con_nodo(versione_che_abbiamo())
        dlg._mostra_nastro_aggiornamento()
        self.assertTrue(dlg.agg_nastro.isHidden())

    def test_a_node_that_does_not_answer_is_not_offered_an_update(self):
        """Mandare un pacchetto a un nodo che non risponde non e' un aggiornamento, e' un'attesa."""
        dlg = self._impostazioni_con_nodo("0.1.0")
        dlg.core.actions.node_online = {"n1": False}
        dlg._mostra_nastro_aggiornamento()
        self.assertTrue(dlg.agg_nastro.isHidden())
        self.assertEqual(dlg._nodi_da_aggiornare(), [])

    def test_the_node_card_says_it_too(self):
        """Con cinque nodi il nastro dice **che** c'e' qualcosa; la scheda dice **chi**."""
        dlg = self._impostazioni_con_nodo("0.1.0")
        riga = dlg._node_row(dlg.cfg.get("actions.nodes")[0])
        self.assertIn("da aggiornare", riga["sub"])
        self.assertIn("0.1.0", riga["dettaglio"], "nel pannello ci sta anche da quale versione")

    # --- la pagina «Nodi» nella finestra principale (N-03) -------------------

    def test_the_fleet_table_fills_and_keeps_its_rows(self):
        """La tabella si aggiorna ogni due secondi: se si ricostruisse, la riga scelta e il punto in
        cui si e' scorso sparirebbero mentre la guardi — una schermata inservibile."""
        from mirto.gui.flotta import VistaFlotta

        vista = VistaFlotta()
        righe = [{"id": "n1", "nome": "Ingresso", "stato": ("a riposo", "ok"),
                  "versione": ("0.12.0", "ok"), "aggiornabile": False, "spazio": ("52 GB liberi", "ok"),
                  "temperatura": ("48 °C", "ok"), "rete": ("Wi-Fi", "ok"), "avvisi": ("nessuno", "ok"),
                  "visto": ("3 min fa", "ok")}]
        vista.mostra(righe)
        self.assertEqual(vista.tabella.rowCount(), 1)
        self.assertEqual(vista.tabella.item(0, 0).text(), "Ingresso")
        prima = vista.tabella.item(0, 0)
        righe[0]["visto"] = ("5 min fa", "ok")
        vista.mostra(righe)
        self.assertIs(vista.tabella.item(0, 0), prima, "stessi nodi: le celle si riscrivono, non si rifanno")
        self.assertEqual(vista.tabella.item(0, 7).text(), "5 min fa")

    def test_the_update_button_is_only_in_the_row_that_needs_it(self):
        from mirto.gui.flotta import VistaFlotta
        from mirto.gui.sistema import COLONNE_FLOTTA

        col = [c for c, _t in COLONNE_FLOTTA].index("versione")
        vista = VistaFlotta()
        base = {"id": "n1", "nome": "Ingresso", "stato": ("", "ok"), "spazio": ("", "ok"),
                "temperatura": ("", "ok"), "rete": ("", "ok"), "avvisi": ("", "ok"), "visto": ("", "ok")}
        vista.mostra([dict(base, versione=("0.12.0", "ok"), aggiornabile=False)])
        self.assertIsNone(vista.tabella.cellWidget(0, col), "dove non serve, non c'e'")
        vista.mostra([dict(base, versione=("0.12.0 → 0.13.0", "attenzione"), aggiornabile=True)])
        self.assertIsNotNone(vista.tabella.cellWidget(0, col))

    def test_the_warnings_cell_leads_where_they_are_solved(self):
        """Il doppio clic sugli avvisi non apre il nodo: apre la schermata che scrive il rimedio
        accanto a ogni problema. Un numero che non porta da nessuna parte lascia soli."""
        from mirto.gui.flotta import VistaFlotta
        from mirto.gui.sistema import COLONNE_FLOTTA

        vista = VistaFlotta()
        base = {"id": "n1", "nome": "Ingresso", "stato": ("", "ok"), "versione": ("0.12.0", "ok"),
                "aggiornabile": False, "spazio": ("", "ok"), "temperatura": ("", "ok"),
                "rete": ("", "ok"), "visto": ("", "ok")}
        vista.mostra([dict(base, avvisi=("1 da guardare", "attenzione"),
                           avvisi_dettaglio=["La camera del nodo è spenta. — Riaccendila da Impostazioni."])])
        chiavi = [c for c, _t in COLONNE_FLOTTA]
        cella = vista.tabella.item(0, chiavi.index("avvisi"))
        self.assertIn("spenta", cella.toolTip(), "passandoci sopra si legge quale")
        self.assertIn("Riaccendila", cella.toolTip(), "e come si risolve")

        dove = []
        vista.apri_diagnosi.connect(dove.append)
        vista.apri_nodo.connect(lambda _n: dove.append("nodo"))
        vista._doppio_clic(0, chiavi.index("avvisi"))
        self.assertEqual(dove, ["n1"], "sugli avvisi si va alla diagnosi")
        vista._doppio_clic(0, chiavi.index("nome"))
        self.assertEqual(dove, ["n1", "nodo"], "altrove si apre il nodo, come prima")

    def test_a_node_without_warnings_does_not_pretend_to_explain(self):
        from mirto.gui.flotta import VistaFlotta
        from mirto.gui.sistema import COLONNE_FLOTTA

        vista = VistaFlotta()
        vista.mostra([{"id": "n1", "nome": "Ingresso", "stato": ("", "ok"), "versione": ("", "ok"),
                       "aggiornabile": False, "spazio": ("", "ok"), "temperatura": ("", "ok"),
                       "rete": ("", "ok"), "avvisi": ("nessuno", "ok"), "avvisi_dettaglio": [],
                       "visto": ("", "ok")}])
        chiavi = [c for c, _t in COLONNE_FLOTTA]
        dove = []
        vista.apri_diagnosi.connect(dove.append)
        vista.apri_nodo.connect(dove.append)
        vista._doppio_clic(0, chiavi.index("avvisi"))
        self.assertEqual(dove, ["n1"], "senza avvisi quella cella si comporta come le altre")
        self.assertIn("Nessun problema", vista.tabella.item(0, chiavi.index("avvisi")).toolTip())

    def test_an_empty_fleet_says_what_to_do(self):
        from mirto.gui.flotta import VistaFlotta

        vista = VistaFlotta()
        vista.mostra([])
        self.assertFalse(vista.vuoto.isHidden(), "senza nodi si dice dove si abbinano")
        self.assertTrue(vista.tabella.isHidden(), "e non si mostra una tabella vuota")

    def test_the_general_help_does_not_talk_about_things_that_moved(self):
        """Il «Come fare» spiegava la soglia persona e la pausa, che da quella scheda se ne sono
        andate: un aiuto che nomina cose che non ci sono manda a cercarle li' dentro."""
        from PySide6.QtWidgets import QLabel

        dlg = self._impostazioni()
        generale = dlg.tabs.widget(0)
        testi = " ".join(w.text() for w in generale.findChildren(QLabel) if w.text())
        self.assertNotIn("Soglia persona</b>: più bassa", testi)
        self.assertNotIn("Pausa tra due avvisi</b>: evita", testi)
        self.assertIn("AI", testi, "dice dove sono andate, invece di farle cercare")

    def test_the_camera_dialog_shows_what_the_node_answered(self):
        from mirto.gui.camera_ui import CameraDialog

        stato = {"attiva": True, "noir": False, "larghezza": 1920, "altezza": 1080, "fps": 15,
                 "sensore": "imx219", "vista_dal_sistema": True,
                 "risoluzioni": [{"etichetta": "640×480", "larghezza": 640, "altezza": 480, "fps": 15},
                                 {"etichetta": "1920×1080", "larghezza": 1920, "altezza": 1080, "fps": 15}]}
        dlg = CameraDialog("Balcone", stato)
        self.assertEqual(dlg.scelta(), {"attiva": True, "noir": False, "larghezza": 1920,
                                        "altezza": 1080, "fps": 15}, "parte da com'è il nodo adesso")
        dlg.accesa.setChecked(False)
        self.assertFalse(dlg.ris.isEnabled(), "a camera spenta la risoluzione non ha senso")
        self.assertFalse(dlg.scelta()["attiva"])

    def test_the_camera_dialog_survives_a_node_that_offers_nothing(self):
        from mirto.gui.camera_ui import CameraDialog

        dlg = CameraDialog("Nodo", {"attiva": False, "sensore": "", "risoluzioni": []})
        self.assertEqual(dlg.scelta()["attiva"], False)
        self.assertEqual(dlg.scelta()["larghezza"], 1280, "senza elenco si ricade sulla consigliata")

    def test_the_system_line_shows_the_space_even_without_video(self):
        """Chiesto da Marco il 21/10: lo spazio libero del computer è un'informazione, sempre."""
        from mirto.gui.app import recording_summary

        _livello, _titolo, dettaglio = recording_summary(
            {"enabled": False, "recorders": [],
             "usage": {"used": 0, "free": 221_400_000_000, "total": 500_000_000_000, "oldest": None}}, {})
        self.assertIn("liberi su", dettaglio, "si dice quanto spazio resta, anche senza video")
        self.assertNotIn(" dal ", dettaglio, "e non si inventa una data di inizio")

    def test_the_cap_cannot_be_bigger_than_the_disk(self):
        """Il 21/10 la finestra proponeva un tetto di 5 GB su un disco da 4,1: non vuol dire niente."""
        from mirto.gui.disco_ui import DiscoDialog

        stato = {"attiva": True, "disco": "/mnt/mirto-reg", "massimo_gb": 5,
                 "minimo_libero_percento": 10, "dischi": [],
                 "registrazione": {"usato": 1e9, "libero": 2.6e9, "totale": 4.1e9}, "esito": {}}

        class Ponte:
            def stato(self):
                return stato

        dlg = DiscoDialog("Bagno", Ponte())
        self.assertEqual(dlg.massimo.maximum(), 4, "il tetto non può superare il disco")
        self.assertEqual(dlg.massimo.value(), 4, "e un valore più grande si vede subito ridotto")

    # --- la finestra dell'audio, rifatta a tab il 22/10 ----------------------
    #
    # Fino a quel giorno chiedeva «dove deve suonare la sirena», e sbagliava domanda: la sirena e'
    # un'AZIONE, e il nodo ha dei DISPOSITIVI. Queste prove girano solo dove PySide6 e' installato —
    # non dove le scrivo — e sono l'unico posto in cui il giro completo (cursore -> chiave -> nodo)
    # viene eseguito davvero prima che lo esegua una persona.

    AUDIO_STATO = {"uscita": "usb", "scheda": "UACDemoV10", "bluetooth_mac": "",
                   "microfono": {"attivo": True, "scheda": "Device", "acceso_ora": True,
                                 "si_puo_scegliere": True},
                   "microfoni": [{"scheda": "Device", "etichetta": "USB PnP Sound Device [Device]"}],
                   "casse_trovate": {}, "esito": {}}
    DISPOSITIVI = {"predefinito": {"uscita": "usb", "scheda": "UACDemoV10", "bluetooth_mac": ""},
                   "jack": {"presente": True, "attivo": True, "scheda": "Headphones",
                            "nome": "bcm2835 Headphones"},
                   "usb": [{"scheda": "UACDemoV10", "nome": "UACDemoV1.0"}],
                   "hdmi": [],
                   "bluetooth": {"casse": [{"mac": "AA:BB:CC:DD:EE:FF", "nome": "JBL Flip 6",
                                            "collegata": False}], "trovate": {}, "esito": {}}}
    MANOPOLE = {"schede": {
        "UACDemoV10": {"manopole": [{"nome": "PCM", "indice": 0, "volume": True, "interruttore": True,
                                     "percento": 90, "acceso": True}]},
        "Device": {"manopole": [{"nome": "Mic", "indice": 0, "volume": True, "interruttore": True,
                                 "percento": 100, "acceso": True},
                                {"nome": "Auto Gain Control", "indice": 0, "volume": False,
                                 "interruttore": True, "acceso": False}]},
        "Headphones": {"manopole": [{"nome": "PCM", "indice": 0, "volume": True, "percento": 80}]}}}

    def _ponte_audio(self, stato=None, disp=None, manopole=None):
        """Il nodo finto, con tutto quello che la finestra gli puo' chiedere. Registra cosa riceve."""
        stato = stato if stato is not None else dict(self.AUDIO_STATO)
        disp = disp if disp is not None else dict(self.DISPOSITIVI)
        manopole = manopole if manopole is not None else dict(self.MANOPOLE)
        fatte = []

        class Ponte:
            chiamate = fatte

            def stato(self):
                return stato

            def dispositivi(self):
                return disp

            def manopole(self):
                return manopole

            def gira(self, scheda, manopola, valore):
                fatte.append(("gira", scheda, manopola, valore))
                return manopole

            def jack(self, attivo):
                fatte.append(("jack", attivo))
                disp["jack"] = dict(disp["jack"], attivo=attivo)
                return disp

            def cerca(self):
                fatte.append(("cerca",))
                return {"esito": "avviato", "attesa_s": 20}

            def abbina(self, mac):
                fatte.append(("abbina", mac))
                return {"esito": "avviato", "attesa_s": 60}

            def dimentica(self, mac):
                fatte.append(("dimentica", mac))
                return {"esito": "avviato", "attesa_s": 20}

            def prova(self, chiave=""):
                fatte.append(("prova", chiave))
                return {"esito": "ok", "chiave": chiave}   # un nodo API 1.14 rimanda la chiave

            def imposta(self, uscita, scheda="", bluetooth_mac=""):
                fatte.append(("imposta", uscita, scheda, bluetooth_mac))
                disp["predefinito"] = {"uscita": uscita, "scheda": scheda, "bluetooth_mac": bluetooth_mac}
                return disp

            def microfono(self, attivo, scheda=""):
                fatte.append(("microfono", attivo, scheda))
                stato["microfono"] = dict(stato.get("microfono") or {}, attivo=attivo, scheda=scheda)
                return stato

        return Ponte(), fatte

    @staticmethod
    def _etichette(dove) -> str:
        from PySide6.QtWidgets import QLabel

        return " | ".join(w.text() for w in dove.findChildren(QLabel))

    @staticmethod
    def _ripassa(dlg, secondi: float = 3.0) -> None:
        """Un giro di domanda al nodo, aspettando che la risposta torni.

        Dall'8/11 `_ripassa` non chiede piu' sul thread della finestra: `audio_state` interroga ALSA
        e durante un abbinamento puo' restare appesa dei secondi, e la finestra restava ferma
        davvero. La risposta torna con un segnale, che fra thread diversi Qt **accoda**: senza un
        giro di eventi non arriva mai. Chiamare `dlg._ripassa()` e guardare subito dopo com'e'
        andata e' proprio la prova che non si puo' piu' fare — ed e' quello che ha tenuto la CI
        rossa per due push.
        """
        import time as _t

        from PySide6.QtWidgets import QApplication

        dlg._ripassa()
        fine = _t.monotonic() + secondi
        while _t.monotonic() < fine:
            QApplication.processEvents()
            if not getattr(dlg, "_sto_chiedendo", False):
                QApplication.processEvents()
                return
            _t.sleep(0.01)
        raise AssertionError("il nodo finto non ha risposto entro il tempo")

    def test_the_audio_dialog_has_one_tab_per_kind_of_device(self):
        """Chiesto da Marco il 22/10, guardando il pannello finito: jack, Bluetooth e USB sono cose
        diverse e vanno separate; e in alto ci va scritto che quello e' un **nodo**."""
        from mirto.gui.audio_ui import AudioDialog

        ponte, _f = self._ponte_audio()
        dlg = AudioDialog("Ingresso", ponte)
        self.assertEqual([dlg.tabs.tabText(i) for i in range(dlg.tabs.count())],
                         ["Jack", "Bluetooth", "USB", "Microfono"])
        self.assertIn("Nodo Ingresso", self._etichette(dlg))
        self.assertNotIn("Dove deve suonare", self._etichette(dlg),
                         "la sirena e' un'azione: qui si configurano dispositivi, non dove suona lei")
        self.assertNotIn("Le azioni suonano da:", self._etichette(dlg),
                         "dal 24/10 non e' piu' vero: ogni azione sceglie la sua cassa (API 1.10)")
        self.assertIn("Uscita predefinita del nodo:", self._etichette(dlg),
                      "quello che resta e' l'uscita **del nodo**: la usa chi non dice niente")

    def test_the_box_at_the_bottom_follows_the_tab(self):
        """Il riquadro in basso dice di che cosa parla la scheda aperta, e non e' una tendina.

        Marco, 7/11: *«e' fuorviante vedere 3 scelte sui 3 tab in ogni scheda dei tab»*; l'8/11,
        provando: *«vorrei fosse statica»*. Una tendina che sotto «Bluetooth» propone anche il jack
        rende indecifrabile il pulsante di prova li' accanto: suona dalla cassa che sto guardando o
        da quella scelta nella tendina?
        """
        from mirto.gui.audio_ui import AudioDialog

        ponte, _f = self._ponte_audio()
        dlg = AudioDialog("Ingresso", ponte)
        self.assertFalse(hasattr(dlg, "scelta_uscita"), "la tendina non c'e' piu'")
        dlg.tabs.setCurrentIndex(0)
        self.assertEqual(dlg.uscita_di_questa_scheda()["chiave"], "jack:Headphones")
        dlg.tabs.setCurrentIndex(1)
        self.assertEqual(dlg.uscita_di_questa_scheda()["chiave"], "bluetooth:AA:BB:CC:DD:EE:FF")
        self.assertIn("JBL Flip 6", dlg.etichetta_uscita.text())
        dlg.tabs.setCurrentIndex(2)
        self.assertEqual(dlg.uscita_di_questa_scheda()["chiave"], "usb:UACDemoV10")
        dlg.tabs.setCurrentIndex(3)
        self.assertFalse(dlg.riquadro_visibile,
                         "il microfono non e' un'uscita: il riquadro sparisce invece di parlare d'altro")
        dlg.tabs.setCurrentIndex(0)
        self.assertTrue(dlg.riquadro_visibile, "e torna tornando su un'uscita")

    def test_a_bluetooth_speaker_says_what_kind_of_thing_it_is(self):
        """Marco, 8/11: *«c'e' il tipo jack, usb + modello, ma manca bluetooth»*."""
        from mirto.actions.pannello_audio import uscite_per_le_azioni

        nomi = {u["uscita"]: u["etichetta"] for u in uscite_per_le_azioni(self.DISPOSITIVI)}
        self.assertTrue(nomi["bluetooth"].startswith("Cassa Bluetooth"),
                        f"in un elenco dove le altre dicono come sono attaccate: {nomi['bluetooth']}")

    def test_using_this_device_for_the_actions_is_one_gesture(self):
        """«Usa per le azioni» manda l'uscita al nodo subito, e si spegne quando e' gia' quella."""
        from mirto.gui.audio_ui import AudioDialog

        ponte, fatte = self._ponte_audio()
        dlg = AudioDialog("Ingresso", ponte)
        dlg.tabs.setCurrentIndex(2)                       # USB: e' gia' il predefinito del nodo
        self.assertFalse(dlg.usa_btn.isEnabled())
        self.assertTrue(dlg.usa_btn.toolTip(), "spento senza dire perche' e' un pulsante rotto")
        dlg.tabs.setCurrentIndex(0)                       # il jack, che il nodo non sta usando
        self.assertTrue(dlg.usa_btn.isEnabled())
        dlg.usa_btn.click()
        self.assertIn(("imposta", "jack", "Headphones", ""), fatte)

    def test_switching_the_jack_off_goes_to_the_node_right_away(self):
        """Non e' una scelta da «Applica»: toglie il jack anche dai ripieghi, e si vede subito."""
        from mirto.gui.audio_ui import AudioDialog

        ponte, fatte = self._ponte_audio()
        dlg = AudioDialog("Ingresso", ponte)
        dlg.jack_on.setChecked(False)
        self.assertIn(("jack", False), fatte)
        dlg.tabs.setCurrentIndex(0)
        self.assertEqual(dlg.uscita_di_questa_scheda(), {}, "spento, non c'e' piu' niente da provare")
        self.assertFalse(dlg.prova_btn.isEnabled())
        self.assertFalse(dlg.usa_btn.isEnabled())

    def test_turning_a_knob_applies_it_now_and_with_the_key_the_node_wants(self):
        from PySide6.QtWidgets import QSlider

        from mirto.gui.audio_ui import AudioDialog

        ponte, fatte = self._ponte_audio()
        dlg = AudioDialog("Ingresso", ponte)
        dlg.tabs.setCurrentIndex(2)                       # USB
        cursori = dlg.tabs.widget(2).findChildren(QSlider)
        self.assertEqual([c.value() for c in cursori], [90], "il volume della cassa USB")
        cursori[0].setValue(55)
        cursori[0].sliderReleased.emit()
        self.assertIn(("gira", "UACDemoV10", "PCM,0", "55%"), fatte,
                      "il nodo vuole «nome,indice»: una chiave diversa e risponderebbe «non la ho»")

    def test_the_microphone_tab_keeps_its_knobs_and_its_choice(self):
        from PySide6.QtWidgets import QCheckBox, QSlider

        from mirto.gui.audio_ui import AudioDialog

        ponte, fatte = self._ponte_audio()
        dlg = AudioDialog("Ingresso", ponte)
        micro = dlg.tabs.widget(3)
        self.assertEqual([c.value() for c in micro.findChildren(QSlider)], [100], "guadagno al massimo")
        # Gli interruttori si chiamano tutti «acceso»: a dire quale manopola sono e\' l'etichetta
        # accanto. Quindi si guarda il risultato, che e\' la cosa che conta: uno solo spento, e
        # l'automatismo del guadagno c'e\'.
        spunte = micro.findChildren(QCheckBox)
        self.assertEqual(sum(1 for c in spunte if not c.isChecked()), 1,
                         "l'automatismo del guadagno dev'essere spento, ed e\' il nodo ad averlo fatto")
        self.assertIn("Automatismo del guadagno", self._etichette(micro))
        self.assertEqual(dlg.micro_scelto(), "Device")
        self.assertIsNone(dlg.scelta_microfono(), "nessuno l'ha toccato")
        # Spegnerlo arriva al nodo **adesso**: niente «Applica» da premere dopo (8/11). Fino a oggi
        # qui si guardava cosa sarebbe partito all'Applica, che e' la domanda di un pannello che non
        # c'e' piu'; e intanto il nodo finto non aveva nemmeno il metodo, quindi questa riga non era
        # mai arrivata fino in fondo.
        dlg.micro_on.setChecked(False)
        self.assertIn(("microfono", False, "Device"), fatte, "il nodo l'ha gia' saputo")
        self.assertIsNone(dlg.scelta_microfono(), "ed e' gia' andato: non resta niente da mandare")

    def test_the_microphone_tab_measures_the_level(self):
        """Chiesto da Marco il 22/10: «i sensori che leggono l'audio e fanno capire se le regolazioni
        vanno bene». Regolare un guadagno senza vedere il livello e\' regolare un volume a orecchie
        tappate."""
        from mirto.gui.audio_ui import AudioDialog

        ponte, _f = self._ponte_audio()
        dlg = AudioDialog("Ingresso", ponte)
        self.assertFalse(dlg.barra.isHidden(), "il nodo sta ascoltando: il livello si vede")
        self.assertTrue(dlg.spento.isHidden())
        dlg._livello_arrivato(0.30, 0.38)         # la voce, con l'automatismo spento
        self.assertIn("giusto", dlg.giudizio.text())
        self.assertIn("\u25ae", dlg.barra.text(), "la barretta si riempie, com'e\' in «Sistema»")
        dlg._livello_arrivato(0.40, 0.99)         # qualcuno urla nel microfono
        self.assertIn("Satura", dlg.giudizio.text())

    def test_a_pause_between_words_does_not_change_the_verdict(self):
        """Fra una parola e l'altra il livello va a zero: un giudizio che cambiasse li\' manderebbe ad
        alzare un guadagno gia\' giusto, tre volte al secondo."""
        from mirto.gui.audio_ui import AudioDialog

        ponte, _f = self._ponte_audio()
        dlg = AudioDialog("Ingresso", ponte)
        dlg._livello_arrivato(0.30, 0.38)
        prima = dlg.giudizio.text()
        dlg._livello_arrivato(0.0, 0.0)           # silenzio, subito dopo
        self.assertEqual(dlg.giudizio.text(), prima, "il picco si tiene per qualche secondo")

    def test_without_listening_there_is_nothing_to_measure(self):
        from mirto.gui.audio_ui import AudioDialog

        stato = dict(self.AUDIO_STATO,
                     microfono={"attivo": False, "scheda": "Device", "acceso_ora": False,
                                "si_puo_scegliere": True})
        ponte, _f = self._ponte_audio(stato=stato)
        dlg = AudioDialog("Ingresso", ponte)
        self.assertTrue(dlg.barra.isHidden())
        self.assertFalse(dlg.spento.isHidden(), "e si dice come farlo comparire")
        dlg.tabs.setCurrentIndex(3)
        self.assertIsNone(dlg._ascolto, "niente da ascoltare: nessun posto occupato sul nodo")

    def test_the_meter_lets_go_of_the_stream_when_the_window_closes(self):
        """Il nodo concede tre ascolti: uno tenuto da una finestra chiusa e\' uno tolto a chi serve."""
        import time as _t

        from mirto.gui.audio_ui import AudioDialog

        class ConFlusso:
            def __init__(self, dentro):
                self._dentro = dentro

            def __getattr__(self, nome):
                return getattr(self._dentro, nome)

            def flusso(self, stop=None):
                while stop is None or not stop.is_set():
                    yield b"\x00\x00" * 1600
                    _t.sleep(0.01)

        ponte, _f = self._ponte_audio()
        dlg = AudioDialog("Ingresso", ConFlusso(ponte))
        dlg.tabs.setCurrentIndex(3)
        self.assertIsNotNone(dlg._ascolto, "tab del microfono aperta e nodo in ascolto: si misura")
        dlg.done(0)
        self.assertIsNone(dlg._ascolto)
        self.assertTrue(dlg._ascolto_stop.is_set())

    def test_the_speaker_you_chose_stays_chosen(self):
        """Trovato su hardware il 21/10: scelta la JBL, dopo l'errore era selezionato un coso BLE.

        L'elenco si rifa' dopo ogni tentativo, e la scelta tornava sulla prima riga: il tentativo dopo
        sarebbe andato su un dispositivo che nessuno aveva scelto.
        """
        import time as _t

        from mirto.gui.audio_ui import AudioDialog

        disp = {**self.DISPOSITIVI,
                "bluetooth": {"casse": [], "trovate": {"quando": 0, "casse": [
                    {"mac": "FF:02:3C:D5:9A:7E", "nome": "BLE MF8470", "abbinata": False},
                    {"mac": "AA:BB:CC:DD:EE:FF", "nome": "JBL Flip 6", "abbinata": False}]}, "esito": {}}}
        stato = dict(self.AUDIO_STATO)
        ponte, fatte = self._ponte_audio(stato=stato, disp=disp)
        dlg = AudioDialog("Ingresso", ponte)
        # «quando» si mette **dopo** aver aperto la finestra, ed e' il punto: si mostrano solo le
        # ricerche fatte da quando e' aperta. Scriverlo prima, anche di un microsecondo, vuol dire
        # una ricerca «di prima» — che e' esattamente quella che non si deve vedere.
        disp["bluetooth"]["trovate"]["quando"] = _t.time()
        dlg._aggiorna_dispositivi()
        # Dall'8/11 le casse viste passare stanno nel **loro** elenco, separate da quelle che il
        # nodo ricorda: su una si preme «Abbina», sull'altra «Dimentica», e sono due cose diverse.
        # La lezione del 21/10 vale adesso qui: l'elenco si rifa' dopo ogni tentativo.
        dlg.trovate.setCurrentRow(1)
        self.assertEqual(dlg.cassa_trovata()["mac"], "AA:BB:CC:DD:EE:FF")
        dlg._abbina()
        stato["esito"] = {"azione": "abbina", "quando": 9, "esito": "errore", "messaggio": "non risponde"}
        self._ripassa(dlg)
        self.assertEqual(dlg.cassa_trovata().get("mac"), "AA:BB:CC:DD:EE:FF",
                         "e dopo l'errore resta scelta lei")
        self.assertIn("JBL Flip 6", dlg.nota.text(), "l'errore dice di quale cassa si parla")
        dlg._abbina()
        self.assertEqual([c for c in fatte if c[0] == "abbina"],
                         [("abbina", "AA:BB:CC:DD:EE:FF")] * 2, "riprovare non cambia cassa da se'")

    def test_there_is_nothing_to_pair_and_nothing_to_forget_until_there_is(self):
        """Un pulsante «Abbina» premibile quando non c'e' niente da abbinare e' un modo per far sbagliare."""
        from mirto.gui.audio_ui import AudioDialog

        disp = {**self.DISPOSITIVI, "bluetooth": {"casse": [], "trovate": {}, "esito": {}}}
        ponte, _f = self._ponte_audio(disp=disp)
        dlg = AudioDialog("Ingresso", ponte)
        # Un riquadro vuoto **dice** che e' vuoto, dentro, dove uno sta guardando (8/11): quindi una
        # riga c'e', ma non e' una cassa — non si sceglie e non porta dati. Contare le righe direbbe
        # «uno»; quello che conta e' che non ci sia niente da scegliere.
        self.assertEqual(dlg.cassa_scelta(), {}, "niente di scelto, perche' non c'e' niente")
        self.assertEqual(dlg.cassa_trovata(), {})
        self.assertFalse(dlg.abbina_btn.isEnabled())
        self.assertFalse(dlg.dimentica_btn.isEnabled())
        self.assertTrue(dlg.cerca_btn.isEnabled(), "si parte da qui")

    def test_a_paired_speaker_can_be_forgotten_but_only_on_purpose(self):
        from unittest import mock

        from PySide6.QtWidgets import QMessageBox

        from mirto.gui.audio_ui import AudioDialog

        ponte, fatte = self._ponte_audio()
        dlg = AudioDialog("Ingresso", ponte)
        dlg.casse.setCurrentRow(0)
        self.assertTrue(dlg.dimentica_btn.isEnabled(), "questa il nodo la ricorda")
        self.assertFalse(dlg.abbina_btn.isEnabled(), "ed e' gia' abbinata")
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            dlg._dimentica()
        self.assertEqual([c for c in fatte if c[0] == "dimentica"], [], "un «no» non tocca il nodo")
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            dlg._dimentica()
        self.assertEqual([c for c in fatte if c[0] == "dimentica"], [("dimentica", "AA:BB:CC:DD:EE:FF")])
        self.assertEqual(dlg.attesa, "dimentica", "lo fa root: si aspetta, non si blocca")

    def test_a_node_that_never_answers_does_not_freeze_the_panel(self):
        """Trovato provando il 21/10: «Cerca casse…» restava spento e non si capiva se stesse cercando."""
        import time as _t

        from mirto.gui.audio_ui import AudioDialog

        stato = dict(self.AUDIO_STATO,
                     esito={"azione": "cerca", "quando": 100, "esito": "ok", "messaggio": "1 casse"})
        ponte, _f = self._ponte_audio(stato=stato)
        dlg = AudioDialog("Ingresso", ponte)
        dlg._cerca()
        self._ripassa(dlg)
        self.assertEqual(dlg.attesa, "cerca", "l'esito di prima non e' la risposta di adesso")
        self.assertFalse(dlg.cerca_btn.isEnabled(), "e intanto il pulsante resta spento")
        dlg.inizio = _t.monotonic() - 300          # il nodo non risponde piu'
        self._ripassa(dlg)
        self.assertEqual(dlg.attesa, "", "l'attesa finisce")
        self.assertTrue(dlg.cerca_btn.isEnabled(), "il pulsante torna premibile")
        self.assertFalse(dlg.nota.sta_aspettando(), "la barra si ferma")
        self.assertIn("non ha risposto", dlg.nota.text(), "e si dice cos'e' successo")

    def test_the_audio_dialog_wakes_up_when_the_node_has_finished(self):
        """La ricerca dura quindici secondi: la finestra ripassa, non resta bloccata."""
        from mirto.gui.audio_ui import AudioDialog

        import time as _t

        stato = dict(self.AUDIO_STATO)
        disp = {**self.DISPOSITIVI, "bluetooth": {"casse": [], "trovate": {}, "esito": {}}}
        ponte, _f = self._ponte_audio(stato=stato, disp=disp)
        dlg = AudioDialog("Ingresso", ponte)
        dlg._cerca()
        self.assertEqual(dlg.attesa, "cerca")
        # «quando» e' adesso: dall'8/11 si mostrano solo le ricerche fatte dopo che la finestra e'
        # aperta, altrimenti all'apertura si rivedeva l'elenco di giorni prima con dentro casse
        # spente marcate «da abbinare».
        disp["bluetooth"] = {"casse": [], "esito": {}, "trovate": {"quando": _t.time(), "casse": [
            {"mac": "AA:BB:CC:DD:EE:FF", "nome": "Cassa del salotto", "abbinata": False}]}}
        stato["esito"] = {"azione": "cerca", "esito": "ok", "messaggio": "1 casse", "quando": 7}
        self._ripassa(dlg)
        self.assertEqual(dlg.attesa, "", "finita l'attesa")
        self.assertEqual(dlg.trovate.count(), 1, "e l'elenco di quelle viste passare e' arrivato")
        self.assertEqual(dlg.cassa_trovata(), {}, "nessuna scelta: «Abbina» non si accende da solo")
        self.assertTrue(dlg.cerca_btn.isEnabled())

    def test_the_test_sound_plays_on_the_device_of_this_tab(self):
        """Il din-don suona da **questa** scheda (API 1.14), non dall'uscita del nodo.

        Prima il nodo sapeva suonare solo da dove era configurato: sentire una cassa voleva dire
        prima renderla l'uscita del nodo, cioe' riavviare il servizio per un din-don.
        """
        from mirto.gui.audio_ui import AudioDialog

        ponte, fatte = self._ponte_audio()
        dlg = AudioDialog("Ingresso", ponte)
        dlg.tabs.setCurrentIndex(0)                       # il jack, che il nodo non sta usando
        self.assertTrue(dlg.prova_btn.isEnabled())
        dlg.prova_btn.click()
        self.assertIn(("prova", "jack:Headphones"), fatte)
        dlg.tabs.setCurrentIndex(1)
        dlg.prova_btn.click()
        self.assertIn(("prova", "bluetooth:AA:BB:CC:DD:EE:FF"), fatte)

    def test_an_old_node_that_cannot_try_a_speaker_says_so(self):
        """Un nodo prima dell'API 1.14 ignora la chiave: lo si dice, invece di far credere."""
        from mirto.gui.audio_ui import AudioDialog

        ponte, _f = self._ponte_audio()
        ponte.prova = lambda chiave="": {"esito": "ok"}   # nessuna chiave di ritorno: nodo vecchio
        dlg = AudioDialog("Ingresso", ponte)
        dlg.tabs.setCurrentIndex(0)
        dlg.prova_btn.click()
        self.assertIn("vecchio", dlg.nota.text())

    def test_the_audio_dialog_still_works_with_a_node_that_has_no_devices(self):
        """Un nodo prima di API 1.7 non conosce questa strada: non e' un guasto, e il resto deve reggere."""
        from mirto.gui.audio_ui import AudioDialog

        class Vecchio:
            def stato(self):
                return {"uscita": "jack", "scheda": "Headphones", "casse_trovate": {}, "esito": {}}

        dlg = AudioDialog("Nodo vecchio", Vecchio())
        self.assertEqual(dlg.disp, {})
        self.assertEqual(dlg.manopole_dati, {})
        self.assertEqual(dlg.tabs.count(), 4, "le tab ci sono lo stesso, vuote")
        self.assertEqual(dlg.uscita_di_questa_scheda(), {})
        self.assertFalse(dlg.prova_btn.isEnabled(), "non c'e' niente da provare")
        self.assertFalse(dlg.usa_btn.isEnabled(), "ne' da rendere predefinito")
        self.assertFalse(dlg.micro_on.isEnabled())

    # --- la finestra dell'azione, rifatta il 22/10 ---------------------------
    #
    # Prima di oggi non la provava **niente**: diciotto campi, nessuna prova. Queste girano solo dove
    # PySide6 e\' installato, cioe\' in CI.

    CAMERE = [{"name": "Ingresso", "enabled": True},
              {"name": "Fotocamera di iPhone 12 di Marco", "enabled": False},
              {"name": "Garage", "enabled": False}]

    def _azione(self, **extra) -> dict:
        a = {"name": "Sirena ingresso", "node": "n1", "action": "sirena", "kind": "siren",
             "targets": ["person"], "cameras": ["Ingresso"]}
        a.update(extra)
        return a

    def test_the_action_dialog_only_offers_cameras_that_are_switched_on(self):
        """Offrire una camera spenta vuol dire far configurare un'azione che non scattera\' mai."""
        from PySide6.QtCore import Qt

        from mirto.gui.settings import ActionDialog

        dlg = ActionDialog(self._azione(), "n1", self.CAMERE)
        nomi = [dlg.cams.item(i).data(Qt.ItemDataRole.UserRole) for i in range(dlg.cams.count())]
        self.assertEqual(nomi, ["Ingresso"], "l'iPhone non e\' acceso nella griglia: non si propone")

    def test_a_camera_already_chosen_is_shown_even_if_switched_off(self):
        """Toglierla in silenzio vorrebbe dire cambiare un'azione senza dirlo a nessuno."""
        from PySide6.QtCore import Qt

        from mirto.gui.settings import ActionDialog

        dlg = ActionDialog(self._azione(cameras=["Ingresso", "Garage"]), "n1", self.CAMERE)
        voci = [(dlg.cams.item(i).data(Qt.ItemDataRole.UserRole), dlg.cams.item(i).text())
                for i in range(dlg.cams.count())]
        self.assertEqual([n for n, _t in voci], ["Ingresso", "Garage"])
        self.assertIn("spenta", dict(voci)["Garage"], "e si vede che e\' spenta")

    MICROFONI = [{"id": "mac", "name": "Microfono del Mac"}, {"id": "n1", "name": "Ingresso · mic"}]

    def test_each_input_carries_its_own_recognitions(self):
        """Quello che deve riconoscere la camera sta sotto le camere, quello del microfono sotto i
        microfoni (24/10).

        Prima era una griglia sola, mescolata, con un'iconcina a distinguere le une dalle altre.
        Marco: *«anche se c'e' l'icona non e' chiaro»*, e *«avendo scelto camera ingresso la combo mi
        propone il microfono»*. Due griglie, ognuna sotto il suo input, e la domanda non si pone piu'.
        """
        from mirto.gui.settings import ActionDialog

        dlg = ActionDialog(self._azione(targets=["person"], cameras=["Ingresso"]),
                           "n1", self.CAMERE, microfoni=self.MICROFONI)
        viste = set(dlg.targets_cam._spunte)
        sentite = set(dlg.targets_mic._spunte)
        self.assertIn("person", viste)
        self.assertFalse(any(k.startswith("sound:") for k in viste),
                         "sotto le camere non si chiede di riconoscere un suono")
        self.assertTrue(sentite and all(k.startswith("sound:") for k in sentite),
                        "sotto i microfoni ci sono solo suoni")
        self.assertFalse(viste & sentite, "niente sta in tutt'e due")

    def test_the_recognitions_sit_under_the_input_they_belong_to(self):
        """L'ordine e' quello del ragionamento: da quali camere, cosa devono riconoscere, quanto
        sicure; e poi la stessa cosa per i microfoni."""
        from mirto.gui.settings import ActionDialog

        dlg = ActionDialog(self._azione(), "n1", self.CAMERE, microfoni=self.MICROFONI)
        riga = lambda w: dlg._form.getWidgetPosition(w)[0]  # noqa: E731
        self.assertLess(riga(dlg.cams), riga(dlg.targets_cam))
        self.assertLess(riga(dlg.targets_cam), riga(dlg.ai_cam))
        self.assertLess(riga(dlg.ai_cam), riga(dlg.mics), "i microfoni cominciano dopo le camere")
        self.assertLess(riga(dlg.mics), riga(dlg.targets_mic))
        self.assertLess(riga(dlg.targets_mic), riga(dlg.ai_mic))

    def test_both_lists_are_the_choice_not_a_filter(self):
        """Le due liste **sono** la scelta della schermata: non spariscono in base alla sorgente.

        «Il "solo per le camere" non ha senso, l'ho gia' scelta»: erano due domande per la stessa
        cosa, e la seconda arrivava dopo. Spariscono solo quando non c'e' niente da scegliere.
        """
        from mirto.gui.settings import ActionDialog

        dlg = ActionDialog(self._azione(), "n1", self.CAMERE, microfoni=self.MICROFONI)
        riga = lambda w: dlg._form.getWidgetPosition(w)[0]  # noqa: E731
        self.assertLess(riga(dlg.cams), riga(dlg.catena),
                        "le camere stanno dove si decide cosa fa scattare, non in fondo")
        self.assertFalse(dlg.cams.isHidden())
        self.assertFalse(dlg.mics.isHidden(), "i microfoni ci sono: si scelgono")
        self.assertEqual(dlg.mics.count(), 2)

        self.assertTrue(dlg.mics_vuoti.isHidden(), "con i microfoni, la nota non serve")
        senza_mic = ActionDialog(self._azione(), "n1", self.CAMERE)
        self.assertTrue(senza_mic.mics.isHidden(), "senza microfoni non c'e' niente da scegliere")
        self.assertFalse(senza_mic.mics_vuoti.isHidden(),
                         "ma si deve dire **dove** si accende, invece di sparire in silenzio")

    def test_cameras_and_microphones_are_saved_in_the_same_field(self):
        from PySide6.QtCore import Qt

        from mirto.gui.settings import ActionDialog

        dlg = ActionDialog(self._azione(targets=["person", "sound:glass"],
                                        cameras=["Ingresso", "Microfono del Mac"]),
                           "n1", self.CAMERE, microfoni=self.MICROFONI)
        scelte = {e.item(i).data(Qt.ItemDataRole.UserRole) for e in (dlg.cams, dlg.mics)
                  for i in range(e.count()) if e.item(i).checkState() == Qt.CheckState.Checked}
        self.assertEqual(scelte, {"Ingresso", "Microfono del Mac"},
                         "una camera e un microfono, spuntati ognuno nella sua lista")

    def test_an_action_opened_on_a_sound_starts_from_the_microphone(self):
        """Riaprendo un'azione fatta per un suono, si riparte dai suoni: lo dicono gli obiettivi."""
        from mirto.gui.settings import ActionDialog

        dlg = ActionDialog(self._azione(targets=["sound:glass"]), "n1", self.CAMERE,
                           microfoni=self.MICROFONI)
        self.assertEqual(dlg.targets_mic.scelte(), {"sound:glass"},
                         "il suono e' spuntato sotto i microfoni")
        self.assertFalse(dlg.targets_cam.scelte(), "e sotto le camere non c'e' niente di acceso")

    def test_the_targets_are_boxes_you_press_not_a_list(self):
        from mirto.gui.settings import ActionDialog

        dlg = ActionDialog(self._azione(kind="deterrent", targets=["cat"]), "n1", self.CAMERE)
        self.assertIn("cat", dlg.targets_cam._spunte, "i box ci sono")
        self.assertEqual(dlg.targets_cam.scelte(), {"cat"})
        dlg.targets_cam._spunte["dog"].cambia()
        self.assertEqual(dlg._targets_chosen, {"cat", "dog"}, "premere un box cambia la scelta")

    # --- il prank nella finestra, non solo nel motore (26/10) ---------------
    #
    # Marco, provando la sua azione «halloween»: *«nella schermata step 1 del wizard di creazione non
    # posso impostarla come prank»*. Il motore aveva l'eccezione dal mattino; la finestra no, e c'erano
    # TRE posti che toglievano «persona» a un dissuasore senza sapere che il prank esiste. Il peggiore
    # al salvataggio: si scegliva Prank, si spuntava Persona, e la risposta era «scegli almeno un
    # animale o un uccello» -- cioe' l'unica cosa per cui quel tipo esiste non si poteva salvare.

    def _prank(self, **extra) -> dict:
        a = self._azione(kind="deterrent", action="dissuasore", quando="prank",
                         targets=["person"], sounds=["sparo"])
        a.update(extra)
        return a

    def test_a_prank_keeps_the_person_among_its_targets(self):
        from mirto.gui.settings import ActionDialog

        dlg = ActionDialog(self._prank(), "n1", self.CAMERE)
        dlg._update()
        self.assertIn("person", dlg._targets_chosen,
                      "un prank e' l'unico dissuasore a cui «persona» e' permessa")

    def test_an_ordinary_deterrent_still_loses_the_person(self):
        """La regola vale ancora dove valeva: il prank non l'ha annacquata."""
        from mirto.gui.settings import ActionDialog

        dlg = ActionDialog(self._azione(kind="deterrent", action="dissuasore", quando="sempre",
                                        targets=["person", "cat"]), "n1", self.CAMERE)
        dlg._targets_kind = "intrusion"          # come se il tipo fosse appena diventato dissuasore
        dlg._update()
        self.assertNotIn("person", dlg._targets_chosen)

    def test_choosing_prank_changes_the_window_by_itself(self):
        """Senza il segnale la scelta non avrebbe effetto finche' non si toccava qualcos'altro: un
        campo che sembra non funzionare, che e' esattamente come l'ha vista Marco."""
        from mirto.gui.settings import ActionDialog

        dlg = ActionDialog(self._azione(kind="deterrent", action="dissuasore", quando="sempre",
                                        targets=["cat"]), "n1", self.CAMERE)
        dlg.quando.setCurrentIndex(dlg.quando.findData("prank"))
        self.assertIn("persona", dlg.hint.text().lower(),
                      "cambiando la tendina l'aiuto e' gia' quello del prank")

    def test_the_help_does_not_say_never_for_people_to_a_prank(self):
        """Lo stesso tipo, regole diverse: dire «mai per le persone» mentre si lascia spuntare
        «Persona rilevata» e' una finestra che si contraddice da sola."""
        from mirto.gui.settings import ActionDialog

        prank = ActionDialog(self._prank(), "n1", self.CAMERE)
        prank._update()
        self.assertNotIn("mai per le persone", prank.hint.text())
        self.assertIn("Prank", prank.hint.text())
        normale = ActionDialog(self._azione(kind="deterrent", action="dissuasore", quando="sempre",
                                            targets=["cat"]), "n1", self.CAMERE)
        normale._update()
        self.assertIn("mai per le persone", normale.hint.text(), "dove valeva, vale ancora")

    def test_saving_a_prank_on_a_person_is_allowed(self):
        """La prova del difetto: prima qui arrivava «scegli almeno un animale o un uccello»."""
        from unittest import mock

        from mirto.gui.settings import ActionDialog

        dlg = ActionDialog(self._prank(), "n1", self.CAMERE)
        dlg._update()
        # La finestra modale si tappa anche quando non dovrebbe aprirsi: se si aprisse, la prova si
        # pianterebbe ad aspettare un clic invece di dire cos'e' andato storto (lezione del 23/10).
        with mock.patch("mirto.gui.settings.QMessageBox") as box:
            dlg._accept()
            self.assertFalse(box.warning.called, f"ha protestato: {box.warning.call_args}")
        self.assertIsNotNone(getattr(dlg, "result_action", None),
                             "il salvataggio non deve rifiutare un prank sulle persone")
        self.assertIn("person", dlg.result_action["targets"])
        self.assertEqual(dlg.result_action["quando"], "prank")

    def test_saving_an_ordinary_deterrent_on_a_person_still_refuses(self):
        """Senza animali ne' uccelli un dissuasore normale non ha niente che lo faccia scattare: la
        «persona» si toglie, e quello che resta e' niente."""
        from unittest import mock

        from mirto.gui.settings import ActionDialog

        dlg = ActionDialog(self._azione(kind="deterrent", action="dissuasore", quando="sempre",
                                        targets=["person"], sounds=["sparo"]), "n1", self.CAMERE)
        with mock.patch("mirto.gui.settings.QMessageBox") as box:
            dlg._accept()
            self.assertTrue(box.warning.called, "e lo dice, invece di salvare un'azione che non scatta")
        self.assertIsNone(getattr(dlg, "result_action", None))

    # --- i limiti delle azioni «sempre» si vedono (26/10) -------------------
    #
    # Marco, leggendo la fascia: *«dici che sono sempre attivi. In che senso? come faccio a creare
    # un'azione campanello sempre attiva…»*. Guardando per rispondergli: pausa, tetto giornaliero e
    # fascia oraria il motore li applica a OGNI azione «sempre», ma le caselle si vedevano solo per i
    # dissuasori. Un campanello si prendeva 5 minuti di pausa e 40 scatti al giorno senza che nessuno
    # potesse vederli: suoni, risuoni dopo un minuto, non succede niente, e sembra rotto il campanello.

    def _campanello(self) -> dict:
        from mirto.actions.presets import azione_per_scopo

        a = azione_per_scopo("campanello", "n1", "audio_generato", "Campanello – Ingresso")
        a["cameras"] = ["Ingresso"]
        return a

    def test_an_always_on_action_shows_its_limits(self):
        from mirto.gui.settings import ActionDialog

        dlg = ActionDialog(self._campanello(), "n1", self.CAMERE)
        dlg._update()
        self.assertFalse(dlg.riga_limiti.isHidden(), "pausa e tetto si vedono")
        self.assertFalse(dlg.hours.isHidden(), "e la fascia oraria")

    def test_an_alarm_action_does_not_show_them(self):
        """Un tetto giornaliero su una sirena d'antifurto sarebbe un difetto, non una cautela: se c'è
        qualcuno in casa deve suonare, e quelle caselle là non devono nemmeno comparire."""
        from mirto.gui.settings import ActionDialog

        dlg = ActionDialog(self._azione(quando="protetta"), "n1", self.CAMERE)
        dlg._update()
        self.assertTrue(dlg.riga_limiti.isHidden())
        self.assertTrue(dlg.hours.isHidden())

    def test_the_limits_of_an_always_on_action_are_saved(self):
        """Si vedevano **e** non si salvavano: `_accept` li scriveva solo nel ramo del dissuasore."""
        from unittest import mock

        from mirto.gui.settings import ActionDialog

        dlg = ActionDialog(self._campanello(), "n1", self.CAMERE)
        dlg._update()
        dlg.cooldown.setValue(2)
        dlg.max_day.setValue(150)
        dlg.hours.setText("07:00-22:00")
        with mock.patch("mirto.gui.settings.QMessageBox") as box:
            dlg._accept()
            self.assertFalse(box.warning.called, f"ha protestato: {box.warning.call_args}")
        self.assertEqual(dlg.result_action["cooldown"], 120)
        self.assertEqual(dlg.result_action["max_per_day"], 150)
        self.assertEqual(dlg.result_action["hours"], "07:00-22:00")

    def test_a_broken_time_range_is_refused_there_too(self):
        from unittest import mock

        from mirto.gui.settings import ActionDialog

        dlg = ActionDialog(self._campanello(), "n1", self.CAMERE)
        dlg._update()
        dlg.hours.setText("dalle sette")
        with mock.patch("mirto.gui.settings.QMessageBox") as box:
            dlg._accept()
            self.assertTrue(box.warning.called, "una fascia che il motore non legge va detta subito")
        self.assertIsNone(getattr(dlg, "result_action", None))

    def test_every_subject_is_reachable_now_that_the_type_is_not_asked(self):
        """La prova di prima diceva che «persona» non si spegne. Il 23/10 e' cambiata, e per forza.

        Il lucchetto c'era perche' un'antintrusione senza persone non ha senso. Tolta la tendina del
        tipo, pero', gli animali si vedevano solo scegliendo «dissuasore» — che non si poteva piu'
        scegliere: un'azione per i gatti era diventata impossibile da creare. Adesso si vede tutto e
        si spegne tutto, e al posto del lucchetto c'e' un controllo che chiede almeno una scelta.
        """
        from mirto.gui.settings import ActionDialog

        dlg = ActionDialog(self._azione(), "n1", self.CAMERE)
        chiavi = set(dlg.targets_cam._spunte)
        self.assertIn("person", chiavi)
        self.assertIn("cat", chiavi, "gli animali si raggiungono senza passare da una tendina")
        persona = dlg.targets_cam._spunte["person"]
        self.assertTrue(persona.acceso)
        self.assertFalse(persona.bloccato, "si deve poter fare un'azione per i soli animali")
        persona.cambia()
        self.assertFalse(persona.acceso)

    def test_the_wizard_asks_for_at_least_one_subject(self):
        """Spuntata la camera, deve anche dire **cosa** riconoscere: due mancanze diverse."""
        from PySide6.QtCore import Qt

        from mirto.gui.settings import ActionWizard

        w = ActionWizard(None, "n1", self.CAMERE)
        self.assertIn("camera", w._cosa_manca(1).lower(), "prima di tutto: da dove")
        w.cams.item(0).setCheckState(Qt.CheckState.Checked)
        for s in w.targets_cam._spunte.values():
            if s.acceso:
                s.cambia()
        self.assertIn("riconoscere", w._cosa_manca(1).lower(),
                      "la camera c'e' ma non riconosce niente: non si passa")

    def test_people_and_animals_together_are_allowed_now(self):
        """Il divieto e' durato un'ora: il tempo di unificare le due strade del motore."""
        from PySide6.QtCore import Qt

        from mirto.gui.settings import ActionWizard

        w = ActionWizard(None, "n1", self.CAMERE)
        w.cams.item(0).setCheckState(Qt.CheckState.Checked)
        w.targets_cam._spunte["cat"].cambia()
        self.assertEqual(sorted(w.targets_cam.scelte()), ["cat", "person"])
        self.assertEqual(w._cosa_manca(1), "", "persone e animali insieme adesso si puo'")

    def test_an_input_you_ticked_must_have_something_to_recognise(self):
        """Marco, 24/10: *«ho selezionato i microfoni e sono riuscito ad andare avanti senza
        selezionare un evento per i microfoni»*.

        Il controllo chiedeva almeno un riconoscimento **in tutto**: con una camera e un microfono
        spuntati bastava dire cosa doveva vedere la camera, e il microfono restava scelto e muto.
        """
        from PySide6.QtCore import Qt

        from mirto.gui.settings import ActionWizard

        w = ActionWizard(None, "n1", self.CAMERE, microfoni=self.MICROFONI)
        self.assertIn("camera", w._cosa_manca(1).lower(),
                      "senza nessuna fonte spuntata, la prima cosa da dire e' da dove")
        w.cams.item(0).setCheckState(Qt.CheckState.Checked)
        self.assertEqual(w._cosa_manca(1), "", "la camera c'e' e «persona» e' gia' acceso")

        w.mics.item(0).setCheckState(Qt.CheckState.Checked)
        self.assertIn("suono", w._cosa_manca(1).lower(),
                      "spuntato un microfono, bisogna dire che suono deve riconoscere")
        chiave = next(k for k in w.targets_mic._spunte if k.startswith("sound:"))
        w.targets_mic._spunte[chiave].cambia()
        self.assertEqual(w._cosa_manca(1), "")

    def test_no_input_no_questions(self):
        """*«Se non seleziono una cam o un microfono non si devono vedere le possibili associazioni
        di riconoscimento AI»*: la solita domanda fuori posto."""
        from PySide6.QtCore import Qt

        from mirto.gui.settings import ActionWizard

        w = ActionWizard(None, "n1", self.CAMERE, microfoni=self.MICROFONI)
        w.vai(1)
        self.assertTrue(w.targets_cam.isHidden(), "nessuna camera spuntata: niente da chiedere")
        self.assertTrue(w.targets_mic.isHidden())
        w.cams.item(0).setCheckState(Qt.CheckState.Checked)
        self.assertFalse(w.targets_cam.isHidden(), "spuntata la camera, si chiede cosa riconoscere")
        self.assertTrue(w.targets_mic.isHidden(), "il microfono no: non l'hai scelto")

    def test_when_it_is_active_is_a_question_now(self):
        """Era nascosto dentro il tipo: «dissuasore» voleva dire «sempre», e nessuno poteva vederlo."""
        from mirto.gui.settings import ActionWizard

        w = ActionWizard(None, "n1", self.CAMERE)
        self.assertEqual(w.quando.currentData(), "protetta")
        vecchio = ActionWizard(self._azione(kind="deterrent", targets=["cat"]), "n1", self.CAMERE)
        self.assertEqual(vecchio.quando.currentData(), "sempre",
                         "un dissuasore gia' salvato resta attivo sempre, come prima")

    def test_the_boxes_ask_for_the_height_they_need(self):
        """Il 22/10 la griglia stava in 170 pixel e ne voleva 500: sei righe di riquadri sovrapposti.

        Dentro un modulo nessuno indovina quanto spazio vuole una griglia — glielo deve dire lei.
        """
        from mirto.gui.settings import ActionDialog

        dlg = ActionDialog(self._azione(kind="deterrent", targets=["cat"]), "n1", self.CAMERE)
        quanti = len(dlg.targets_cam._spunte)
        colonne = max(1, dlg.targets_cam._colonne)
        righe = -(-quanti // colonne)
        self.assertGreaterEqual(dlg.targets_cam.minimumHeight(), righe * dlg.targets_cam.ALTO,
                                f"{quanti} riquadri su {colonne} colonne non ci stanno in quell'altezza")

    def test_what_matters_is_at_the_top_and_what_goes_together_is_on_one_row(self):
        """Un'azione spenta non fa niente, e scoprirlo in fondo alla pagina e\' tardi."""
        from mirto.gui.settings import ActionDialog

        dlg = ActionDialog(self._azione(), "n1", self.CAMERE)
        riga = lambda w: dlg._form.getWidgetPosition(w)[0]  # noqa: E731
        self.assertLessEqual(riga(dlg.enabled), 2, "l'interruttore sta in cima")
        self.assertLess(riga(dlg.cams), riga(dlg.catena),
                        "prima cosa la fa scattare, poi cosa fa")
        self.assertLess(riga(dlg.targets_cam), riga(dlg.ai_cam),
                        "prima cosa riconoscere, poi chi lo giudica")
        # «Durata» non si chiede più (23/10): resta nel dato, calcolata dal pezzo più lungo. Il
        # ritardo resta qui nel modulo unico, e non c'è nel wizard.
        self.assertIs(dlg.delay.parent(), dlg.riga_tempi)
        self.assertIsNone(dlg.duration.parent(), "la durata non sta più in nessuna riga")
        self.assertIs(dlg.cooldown.parent(), dlg.riga_limiti, "pausa e scatti massimi sulla stessa riga")
        self.assertIs(dlg.max_day.parent(), dlg.riga_limiti)

    def test_the_telegram_tick_is_only_where_it_does_something(self):
        """Lo legge **solo** il percorso dei dissuasori: altrove il messaggio parte dall'evento."""
        from mirto.gui.settings import ActionDialog

        dlg = ActionDialog(self._azione(kind="deterrent", targets=["cat"]), "n1", self.CAMERE)
        self.assertFalse(dlg.notify.isHidden(), "un dissuasore senza questa spunta e\' muto")
        dlg.kind.setCurrentIndex(dlg.kind.findData("siren"))
        self.assertTrue(dlg.notify.isHidden())

    def test_every_label_explains_itself(self):
        """Ogni campo e\' un'occasione di lasciare una scelta com'e\' perche\' non si sa cosa fa.

        Il numero non si scrive a mano: il 24/10 un campo e\' stato tolto («chi la fa scattare», che
        era una domanda a cui la risposta stava due righe sotto) e questo test e\' diventato rosso per
        aver contato giusto. Adesso conta **le righe che ci sono**, cosi\' resta vero togliendo o
        aggiungendo un campo, e falso solo se qualcuno ne aggiunge uno senza spiegarlo.
        """
        from PySide6.QtWidgets import QLabel

        from mirto.gui.settings import ActionDialog

        dlg = ActionDialog(self._azione(), "n1", self.CAMERE)
        con_etichetta = [chiave for chiave, testo, _w in dlg._righe if testo]
        aiuti = [w for w in dlg.findChildren(QLabel) if w.text() == "\u24d8"]
        # «Almeno»: qualche (i) nasce dentro i pannelli (la striscia, i riquadri degli obiettivi) e
        # non da una riga del modulo. Quello che conta e\' che nessun campo con un'etichetta resti
        # senza spiegazione.
        self.assertGreaterEqual(len(aiuti), len(con_etichetta),
                                f"una (i) per ogni campo con un'etichetta: {con_etichetta}")
        self.assertTrue(all(len(w.toolTip()) > 40 for w in aiuti), "e ognuna spiega qualcosa")

    def test_the_wifi_dialog_lists_what_the_node_saw(self):
        """Le reti sono quelle che vede **il nodo**: il computer può essere sul cavo e non vederne."""
        from mirto.gui.wifi_ui import WifiDialog

        stato = {"reti": [{"ssid": "CasaMia", "segnale": 72, "sicurezza": "WPA2", "in_uso": True},
                          {"ssid": "CasaMia_Ospiti", "segnale": 40, "sicurezza": "aperta", "in_uso": False}],
                 "in_uso": "CasaMia", "indirizzi": ["192.168.2.3"], "esito": {}}

        class Ponte:
            chiamate = []

            def stato(self):
                return stato

            def cerca(self):
                Ponte.chiamate.append("cerca")
                return {"esito": "avviato"}

            def collega(self, ssid, password, nascosta):
                Ponte.chiamate.append((ssid, password, nascosta))
                return {"esito": "avviato"}

        dlg = WifiDialog("Balcone", Ponte())
        self.assertEqual(dlg.reti.count(), 3, "due reti più la voce della rete nascosta")
        self.assertEqual(dlg.ssid_scelto(), "CasaMia", "parte da quella a cui è attaccato adesso")
        self.assertIn("192.168.2.3", dlg.dove.text(), "si vede dove sta il nodo adesso")
        self.assertFalse(dlg.nome_nascosto.isVisibleTo(dlg), "il nome si scrive solo per le reti nascoste")
        dlg.password.setText("unapassword")
        dlg._collega()
        self.assertEqual(Ponte.chiamate[-1], ("CasaMia", "unapassword", False))
        self.assertEqual(dlg.password.text(), "", "la password non resta nemmeno nella finestra")
        self.assertFalse(dlg.ok_btn.isEnabled(), "mentre collega, il pulsante non si ripreme")
        dlg._cerca()
        self.assertIn("cerca", Ponte.chiamate)

    def test_the_wifi_password_can_come_from_this_computer(self):
        """N-08: la password del Wi-Fi di casa è già nel portachiavi, e riscriverla a mano si sbaglia."""
        from mirto.gui.wifi_ui import WifiDialog

        stato = {"reti": [{"ssid": "CasaMia", "segnale": 70, "sicurezza": "WPA2", "in_uso": False},
                          {"ssid": "HotelWifi", "segnale": 40, "sicurezza": "WPA2", "in_uso": False}],
                 "in_uso": "", "indirizzi": [], "esito": {}}
        chiamate = []

        class Ponte:
            def stato(self):
                return stato

            def collega(self, *a):
                chiamate.append(a)
                return {"esito": "avviato"}

            def reti_del_computer(self):
                return ["CasaMia"]

            def password_del_computer(self, ssid):
                chiamate.append(("portachiavi", ssid))
                return "unapasswordlunga"

            def perche_no(self):
                return "scrivila a mano"

        from unittest import mock

        from PySide6.QtWidgets import QLineEdit

        dlg = WifiDialog("Balcone", Ponte())
        dlg.reti.setCurrentIndex(0)
        self.assertTrue(dlg.portachiavi_btn.isEnabled(), "CasaMia la conosce: si può chiedere")
        dlg.reti.setCurrentIndex(1)
        self.assertFalse(dlg.portachiavi_btn.isEnabled(), "non si offre quello che il computer non sa")
        dlg.reti.setCurrentIndex(0)
        with mock.patch("mirto.gui.wifi_ui.QMessageBox") as box:
            box.question.return_value = box.StandardButton.No
            dlg._dal_portachiavi()
            self.assertTrue(box.question.called, "si chiede prima: il sistema aprirà una sua finestra")
        self.assertEqual(chiamate, [], "risposto no: al sistema non si chiede niente")
        with mock.patch("mirto.gui.wifi_ui.QMessageBox") as box:
            box.question.return_value = box.StandardButton.Yes
            dlg._dal_portachiavi()
        self.assertEqual(chiamate, [("portachiavi", "CasaMia")])
        self.assertEqual(dlg.password.text(), "unapasswordlunga")
        self.assertEqual(dlg.password.echoMode(), QLineEdit.EchoMode.Password, "resta coperta")
        dlg._collega()
        self.assertEqual(chiamate[-1], ("CasaMia", "unapasswordlunga", False))

    def test_a_computer_that_will_not_give_the_password_is_not_a_dead_end(self):
        from unittest import mock

        from mirto.gui.wifi_ui import WifiDialog

        class Ponte:
            def stato(self):
                return {"reti": [{"ssid": "CasaMia", "segnale": 70, "sicurezza": "WPA2", "in_uso": False}],
                        "in_uso": "", "indirizzi": [], "esito": {}}

            def reti_del_computer(self):
                return ["CasaMia"]

            def password_del_computer(self, _ssid):
                return ""

            def perche_no(self):
                return "scrivila a mano"

        dlg = WifiDialog("Balcone", Ponte())
        dlg.reti.setCurrentIndex(0)
        with mock.patch("mirto.gui.wifi_ui.QMessageBox") as box:
            box.question.return_value = box.StandardButton.Yes
            dlg._dal_portachiavi()
        self.assertEqual(dlg.password.text(), "", "niente password inventate")
        self.assertIn("mano", dlg.nota.text(), "e si dice cosa fare invece")

    def test_an_old_bridge_without_the_keychain_just_disables_the_button(self):
        """Le altre prove di questa finestra passano un ponte che non sa di portachiavi: deve bastare."""
        from mirto.gui.wifi_ui import WifiDialog

        class Ponte:
            def stato(self):
                return {"reti": [{"ssid": "CasaMia", "segnale": 70, "sicurezza": "WPA2", "in_uso": False}],
                        "in_uso": "", "indirizzi": [], "esito": {}}

        dlg = WifiDialog("Balcone", Ponte())
        dlg.reti.setCurrentIndex(0)
        self.assertEqual(dlg.conosciute, set())
        self.assertFalse(dlg.portachiavi_btn.isEnabled())

    def test_while_the_node_works_the_window_shows_it(self):
        """Trovato provando, il 21/10: «Collega» lavorava mezzo minuto senza dirlo.

        Una riga di testo piccola e grigia in fondo non basta: mezzo minuto senza segnali sembra un
        programma bloccato, e chi prova preme di nuovo o chiude.
        """
        from mirto.gui.wifi_ui import WifiDialog

        stato = {"reti": [{"ssid": "CasaMia", "segnale": 70, "sicurezza": "WPA2", "in_uso": False}],
                 "in_uso": "", "indirizzi": [], "esito": {}}

        class Ponte:
            def stato(self):
                return stato

            def collega(self, *_a):
                return {"esito": "avviato", "attesa_s": 60}

        dlg = WifiDialog("Balcone", Ponte())
        dlg.reti.setCurrentIndex(0)
        dlg.password.setText("unapasswordlunga")
        self.assertFalse(dlg.nota.sta_aspettando(), "a riposo non c'è nessuna barra")
        dlg._collega()
        self.assertTrue(dlg.nota.sta_aspettando(), "mentre il nodo lavora, la barra si muove")
        self.assertRegex(dlg.nota.text(), r"ancora (59|60) s", "e si dice quanto può volerci")
        stato["esito"] = {"azione": "collega", "esito": "ok", "messaggio": "collegato a «CasaMia»"}
        dlg._ripassa()
        self.assertFalse(dlg.nota.sta_aspettando(), "finito: la barra sparisce")
        self.assertIn("CasaMia", dlg.nota.text(), "e resta scritto com'è andata")

    def test_the_waiting_line_behaves_like_the_label_it_replaces(self):
        from mirto.gui.attesa import Attesa

        a = Attesa()
        a.setText("una nota normale")
        self.assertEqual(a.text(), "una nota normale")
        self.assertFalse(a.sta_aspettando())
        a.inizia("sto lavorando", 30)
        self.assertTrue(a.sta_aspettando())
        self.assertIn("sto lavorando", a.text())
        a.esito("è andata", True)
        self.assertEqual(a.text(), "è andata")
        self.assertFalse(a.sta_aspettando())

    def test_the_wifi_dialog_asks_the_name_of_a_hidden_network(self):
        from mirto.gui.wifi_ui import NASCOSTA, WifiDialog

        class Ponte:
            chiamate = []

            def stato(self):
                return {"reti": [], "in_uso": "", "indirizzi": [], "esito": {}}

            def collega(self, ssid, password, nascosta):
                Ponte.chiamate.append((ssid, password, nascosta))
                return {"esito": "avviato"}

        dlg = WifiDialog("Nodo", Ponte())
        self.assertEqual(dlg.reti.count(), 1, "senza ricerca c'è solo la voce della rete nascosta")
        self.assertEqual(dlg.reti.currentData(), NASCOSTA)
        self.assertTrue(dlg.nome_nascosto.isVisibleTo(dlg))
        dlg.nome_nascosto.setText("  RetePrivata  ")
        dlg.password.setText("unapassword")
        dlg._collega()
        self.assertEqual(Ponte.chiamate[-1], ("RetePrivata", "unapassword", True), "nome ripulito, nascosta")

    def test_the_wifi_dialog_refuses_a_password_the_node_would_refuse(self):
        """Una password di tre lettere non si manda nemmeno: il nodo la rifiuterebbe (8-63)."""
        from unittest import mock

        from mirto.gui.wifi_ui import WifiDialog

        class Ponte:
            chiamate = []

            def stato(self):
                return {"reti": [{"ssid": "CasaMia", "segnale": 70, "sicurezza": "WPA2", "in_uso": False}],
                        "in_uso": "", "indirizzi": [], "esito": {}}

            def collega(self, *a):
                Ponte.chiamate.append(a)
                return {"esito": "avviato"}

        dlg = WifiDialog("Nodo", Ponte())
        dlg.reti.setCurrentIndex(0)
        dlg.password.setText("abc")
        with mock.patch("mirto.gui.wifi_ui.QMessageBox") as avviso:
            dlg._collega()
        self.assertTrue(avviso.warning.called, "lo dice invece di far fallire il nodo")
        self.assertEqual(Ponte.chiamate, [], "e al nodo non arriva niente")
        self.assertEqual(dlg.attesa, "")
        dlg.password.clear()                      # rete aperta: la password vuota si accetta
        dlg._collega()
        self.assertEqual(Ponte.chiamate[-1], ("CasaMia", "", False))

    def test_the_wifi_dialog_wakes_up_when_the_node_has_connected(self):
        from mirto.gui.wifi_ui import WifiDialog

        stato = {"reti": [{"ssid": "CasaMia", "segnale": 70, "sicurezza": "WPA2", "in_uso": False}],
                 "in_uso": "", "indirizzi": [], "esito": {}}

        class Ponte:
            def stato(self):
                return stato

            def collega(self, *a):
                return {"esito": "avviato"}

        dlg = WifiDialog("Balcone", Ponte())
        dlg.reti.setCurrentIndex(0)
        dlg.password.setText("unapassword")
        dlg._collega()
        self.assertEqual(dlg.attesa, "collega")
        dlg._ripassa()
        self.assertEqual(dlg.attesa, "collega", "senza risposta del nodo, si continua ad aspettare")
        stato["reti"][0]["in_uso"] = True
        stato["in_uso"], stato["indirizzi"] = "CasaMia", ["192.168.2.21"]
        stato["esito"] = {"azione": "collega", "esito": "ok", "messaggio": "collegato a «CasaMia»"}
        dlg._ripassa()
        self.assertEqual(dlg.attesa, "")
        self.assertTrue(dlg.ok_btn.isEnabled())
        self.assertIn("192.168.2.21", dlg.dove.text(), "e si vede l'indirizzo nuovo")
        self.assertIn("CasaMia", dlg.nota.text(), "e la finestra dice com'è andata")

    DISCHI = [{"path": "/dev/sda1", "disk": "/dev/sda", "size": 512_000_000_000, "fstype": "ext4",
               "uuid": "1", "mount": "/mnt/mirto-reg", "model": "Samsung T7",
               "parts": [["/dev/sda1", "/mnt/mirto-reg"]], "is_part": True,
               "libero": 400_000_000_000, "totale": 512_000_000_000},
              {"path": "/dev/sdb1", "disk": "/dev/sdb", "size": 64_000_000_000, "fstype": "ntfs",
               "uuid": "", "mount": "", "model": "Kingston", "parts": [["/dev/sdb1", ""]], "is_part": True}]

    def _stato_disco(self, **extra):
        st = {"dischi": list(self.DISCHI), "quando": 10, "attiva": True, "disco": "/mnt/mirto-reg",
              "massimo_gb": 0, "minimo_libero_percento": 10,
              "formati_buoni": ["ext4", "exfat", "vfat"],
              "registrazione": {"attiva": True, "registra": True, "errore": "",
                                "usato": 120_000_000_000, "libero": 400_000_000_000,
                                "totale": 512_000_000_000},
              "esito": {}}
        st.update(extra)
        return st

    def _ponte_disco(self, stato, chiamate):
        class Ponte:
            def stato(self):
                return stato

            def cerca(self):
                chiamate.append(("cerca",))
                return {"esito": "avviato"}

            def usa(self, dev):
                chiamate.append(("usa", dev))
                return {"esito": "avviato"}

            def prepara(self, dev, conferma):
                chiamate.append(("prepara", dev, conferma))
                return {"esito": "avviato"}

            def limiti(self, attiva, massimo, minimo):
                chiamate.append(("limiti", attiva, massimo, minimo))
                return {}
        return Ponte()

    def test_the_disk_dialog_shows_what_the_node_answered(self):
        from mirto.gui.disco_ui import DiscoDialog

        dlg = DiscoDialog("Balcone", self._ponte_disco(self._stato_disco(), []))
        self.assertEqual(dlg.dischi.count(), 2)
        self.assertEqual(dlg.disco_scelto()["path"], "/dev/sda1")
        self.assertFalse(dlg.da_preparare(), "un ext4 si usa com'è")
        self.assertEqual(dlg.limiti_scelti(), {"attiva": True, "massimo_gb": 0,
                                               "minimo_libero_percento": 10})
        self.assertIn("400 GB", dlg.adesso.text(), "quanto spazio resta si legge subito")

    def test_a_disk_to_format_is_not_offered_for_recording(self):
        """Il pulsante che non cancella niente non deve nemmeno essere premibile su un disco ntfs."""
        from mirto.gui.disco_ui import DiscoDialog

        chiamate = []
        dlg = DiscoDialog("Balcone", self._ponte_disco(self._stato_disco(), chiamate))
        dlg.dischi.setCurrentIndex(1)
        self.assertTrue(dlg.da_preparare())
        self.assertFalse(dlg.usa_btn.isEnabled())
        dlg._usa()
        self.assertEqual(chiamate, [], "e se venisse premuto comunque, al nodo non arriva niente")

    def test_the_format_confirmation_asks_to_type_the_device_name(self):
        """L'unica cosa che cancella dati in tutto Mirto: la conferma non si sblocca da sola."""
        from mirto.gui.disco_ui import ConfermaDisco

        dlg = ConfermaDisco(self.DISCHI[1])
        self.assertFalse(dlg.ok_btn.isEnabled(), "parte bloccata, e Invio annulla")
        for scritto, pronto in (("si", False), ("/dev/sdb1", False), ("  /dev/sdb  ", True)):
            dlg.campo.setText(scritto)
            self.assertEqual(dlg.pronto(), pronto, scritto)
            self.assertEqual(dlg.ok_btn.isEnabled(), pronto, scritto)

    def test_formatting_starts_only_with_a_ready_confirmation(self):
        from unittest import mock

        from PySide6.QtWidgets import QDialog

        from mirto.gui.disco_ui import DiscoDialog

        chiamate = []
        dlg = DiscoDialog("Balcone", self._ponte_disco(self._stato_disco(), chiamate))
        dlg.dischi.setCurrentIndex(1)
        confermato, annullato = QDialog.DialogCode.Accepted, QDialog.DialogCode.Rejected
        for esito_finestra, pronto in ((confermato, False), (annullato, True)):
            with mock.patch("mirto.gui.disco_ui.ConfermaDisco") as finta:
                finta.return_value.exec.return_value = esito_finestra
                finta.return_value.pronto.return_value = pronto
                dlg._prepara()
            self.assertEqual(chiamate, [], f"{esito_finestra}/{pronto}")
        with mock.patch("mirto.gui.disco_ui.ConfermaDisco") as finta:
            finta.return_value.exec.return_value = confermato
            finta.return_value.pronto.return_value = True
            finta.return_value.dispositivo = "/dev/sdb"
            finta.return_value.campo.text.return_value = "/dev/sdb"
            dlg._prepara()
        self.assertEqual(chiamate, [("prepara", "/dev/sdb", "/dev/sdb")], "il disco intero, non la partizione")
        self.assertEqual(dlg.attesa, "prepara")
        self.assertFalse(dlg.ok_btn.isEnabled(), "mentre il nodo formatta non si preme altro")

    def test_the_disk_dialog_wakes_up_when_the_node_has_finished(self):
        from mirto.gui.disco_ui import DiscoDialog

        chiamate, stato = [], self._stato_disco()
        dlg = DiscoDialog("Balcone", self._ponte_disco(stato, chiamate))
        dlg.attiva.setChecked(False)
        dlg.massimo.setValue(128)
        dlg.minimo.setValue(15)
        dlg._limiti()
        self.assertEqual(chiamate, [("limiti", False, 128, 15)])
        dlg._ripassa()
        self.assertEqual(dlg.attesa, "limiti", "senza risposta del nodo si continua ad aspettare")
        stato["esito"] = {"azione": "limiti", "esito": "ok", "messaggio": "registrazione spenta"}
        stato["attiva"] = False
        dlg._ripassa()
        self.assertEqual(dlg.attesa, "")
        self.assertTrue(dlg.ok_btn.isEnabled(), "finito: si può chiedere altro")
        self.assertTrue(dlg.cerca_btn.isEnabled())

    IO = "Mirto su mac-di-marco"
    CHIAVI = [{"nome": "mirto-su-mac-di-marco", "cliente": IO, "indirizzo": "192.168.2.10",
               "quando": 1792400000},
              {"nome": "mirto-su-portatile", "cliente": "Mirto su portatile",
               "indirizzo": "192.168.2.11", "quando": 1792500000}]

    def _stato_abbinamenti(self, aperto=False, pin="", chiavi=None, **extra):
        st = {"chiavi": list(self.CHIAVI if chiavi is None else chiavi),
              "finestra": {"aperto": aperto, "scade_tra_s": 300 if aperto else 0,
                           "senza_scadenza": False, "abbinato": True},
              "chiave_installazione": False, "ritiro_installazione_tra_s": 0}
        if aperto and pin:
            st["finestra"]["pin"] = pin
        st.update(extra)
        return st

    def _ponte_abbinamenti(self, partenza, chiamate):
        prova = self

        class Ponte:
            corrente = dict(partenza)

            def stato(self):
                return dict(Ponte.corrente)

            def apri(self):
                chiamate.append(("apri",))
                Ponte.corrente = prova._stato_abbinamenti(aperto=True, pin="123456")
                return dict(Ponte.corrente)

            def chiudi(self):
                chiamate.append(("chiudi",))
                Ponte.corrente = prova._stato_abbinamenti()
                return dict(Ponte.corrente)

            def stacca(self, nome):
                chiamate.append(("stacca", nome))
                Ponte.corrente = prova._stato_abbinamenti(
                    chiavi=[k for k in prova.CHIAVI if k["nome"] != nome])
                return dict(Ponte.corrente)
        return Ponte()

    def test_the_pairings_dialog_says_who_is_paired_and_which_one_is_us(self):
        from mirto.gui.abbinamenti_ui import AbbinamentiDialog

        dlg = AbbinamentiDialog("Balcone", self._ponte_abbinamenti(self._stato_abbinamenti(), []), self.IO)
        self.assertEqual(dlg.elenco.count(), 2)
        testi = [dlg.elenco.item(i).text() for i in range(2)]
        self.assertTrue(any("192.168.2.11" in t for t in testi), testi)
        self.assertEqual(len([t for t in testi if "computer" in t]), 1,
                         "uno solo è questo computer, e si deve vedere quale")

    def test_the_pin_is_not_shown_while_the_window_is_closed(self):
        from mirto.gui.abbinamenti_ui import AbbinamentiDialog

        dlg = AbbinamentiDialog("Balcone", self._ponte_abbinamenti(self._stato_abbinamenti(), []), self.IO)
        self.assertEqual(dlg.pin.text(), "", "il PIN si vede solo a finestra aperta")
        self.assertTrue(dlg.apri_btn.isEnabled())
        self.assertFalse(dlg.chiudi_btn.isEnabled())

    def test_opening_shows_the_pin_and_closing_takes_it_away(self):
        from mirto.gui.abbinamenti_ui import AbbinamentiDialog

        chiamate = []
        dlg = AbbinamentiDialog("Balcone", self._ponte_abbinamenti(self._stato_abbinamenti(), chiamate),
                                self.IO)
        dlg._apri()
        self.assertEqual(chiamate, [("apri",)])
        self.assertIn("123456", dlg.pin.text())
        self.assertFalse(dlg.apri_btn.isEnabled())
        dlg._chiudi()
        self.assertEqual(chiamate[-1], ("chiudi",))
        self.assertEqual(dlg.pin.text(), "", "richiuso: il PIN sparisce anche dalla finestra")

    def test_detaching_a_computer_asks_first(self):
        from unittest import mock

        from mirto.gui.abbinamenti_ui import AbbinamentiDialog

        chiamate = []
        dlg = AbbinamentiDialog("Balcone", self._ponte_abbinamenti(self._stato_abbinamenti(), chiamate),
                                self.IO)
        dlg.elenco.setCurrentRow(1)                       # il portatile, non noi
        with mock.patch("mirto.gui.abbinamenti_ui.QMessageBox") as box:
            box.question.return_value = box.StandardButton.No
            dlg._stacca()
            self.assertTrue(box.question.called, "la conferma si chiede sempre")
        self.assertEqual(chiamate, [], "risposto no: non si stacca niente")
        with mock.patch("mirto.gui.abbinamenti_ui.QMessageBox") as box:
            box.question.return_value = box.StandardButton.Yes
            dlg._stacca()
        self.assertEqual(chiamate, [("stacca", "mirto-su-portatile")])
        self.assertEqual(dlg.elenco.count(), 1, "e l'elenco si aggiorna da sé")

    def test_the_last_pairing_cannot_be_detached_from_here(self):
        from mirto.gui.abbinamenti_ui import AbbinamentiDialog

        dlg = AbbinamentiDialog("Balcone", self._ponte_abbinamenti(
            self._stato_abbinamenti(chiavi=[self.CHIAVI[0]]), []), self.IO)
        self.assertEqual(dlg.elenco.count(), 1)
        self.assertFalse(dlg.stacca_btn.isEnabled(),
                         "il nodo lo rifiuterebbe: il pulsante non deve nemmeno provarci")

    # L'API la si prende da Mirto e non si scrive a mano: un nodo «in ordine» e\' un nodo aggiornato.
    # Il 22/10 alzarla a 1.6 ha fatto diventare rosse prove che parlavano di tutt'altro — qui due, piu'
    # nove in test_actions — e la diagnosi aveva ragione: quel nodo finto era indietro.
    NODO_INFO = {"nome": "Balcone", "modello": "Raspberry Pi 4 Model B", "versione": "0.7.1",
                 "versione_api": API_ATTESA}
    NODO_STATO = {"nome": "Balcone", "attivo_da_s": 93600, "temperatura_c": 46.2,
                  "host": "mirto-balcone", "indirizzi": ["192.168.2.21"],
                  "camere": [{"nome": "cam", "in_linea": True}],
                  "registrazione": {"attiva": True, "registra": True, "errore": "",
                                    "disco": "/mnt/mirto-reg", "usato": 120_000_000_000,
                                    "libero": 390_000_000_000, "totale": 512_000_000_000},
                  "audio": {"uscita": "bluetooth", "uscita_in_uso": "bluetooth", "collegata": True,
                            "abbinata": True, "nome": "JBL Flip 6"},
                  "abbinamento": {"aperto": False, "abbinato": True, "chiavi": 2,
                                  "chiave_installazione": False, "da": "Mirto su mac-di-marco"}}
    NODO_CAMERA = {"attiva": True, "vista_dal_sistema": True, "sensore": "imx219"}

    def _letto(self, **extra):
        d = {"info": dict(self.NODO_INFO), "stato": dict(self.NODO_STATO),
             "camera": dict(self.NODO_CAMERA)}
        d.update(extra)
        return d

    def _ponte_stato(self, letture, chiamate):
        class Ponte:
            resto = list(letture[1:])

            def leggi(self):
                chiamate.append("leggi")
                if Ponte.resto and len(chiamate) > 1:
                    return Ponte.resto.pop(0)
                return letture[0]
        return Ponte()

    @staticmethod
    def _testi(lay) -> list:
        return [lay.itemAt(i).widget().text() for i in range(lay.count())
                if lay.itemAt(i).widget() is not None]

    def test_the_status_dialog_says_when_there_is_nothing_wrong(self):
        from mirto.gui.stato_ui import StatoDialog

        chiamate = []
        dlg = StatoDialog("Balcone", self._ponte_stato([self._letto()], chiamate))
        self.assertEqual(chiamate, ["leggi"], "una lettura, non una a widget")
        self.assertEqual(dlg.guai(), [])
        self.assertTrue(any("Nessun problema" in t for t in self._testi(dlg.guai_lay)))
        testo = dlg.console.toPlainText()
        for etichetta, valore in dlg.righe():
            self.assertIn(etichetta, testo, "ogni riga sta nel riquadro")
            self.assertIn(valore, testo)
        self.assertNotIn("Balcone", self._etichette(dlg),
                         "il nome del nodo in cima era una riga di spazio buttata: la finestra si apre da lì")

    def test_the_status_box_lines_up_the_values(self):
        """Quindici righe di lunghezza diversa sono un muro; incolonnate si scorrono con l'occhio."""
        from mirto.gui.stato_ui import StatoDialog

        dlg = StatoDialog("Balcone", self._ponte_stato([self._letto()], []))
        # Dove comincia il valore si trova con un'espressione regolare, non spezzando su due spazi:
        # il separatore ne ha tre, e sull'etichetta piu\' lunga (che non ha riempimento) il conto
        # scivolava di uno. La prova era sbagliata, non il testo — scoperto in CI il 22/10.
        import re as _re

        inizi = set()
        for riga in dlg.console.toPlainText().splitlines():
            m = _re.match(r"^(\S.*?)\s{2,}(\S.*)$", riga)
            if m:
                inizi.add(m.start(2))
        self.assertTrue(inizi, "il riquadro non e\' vuoto")
        self.assertEqual(len(inizi), 1, f"i valori devono cominciare tutti alla stessa colonna: {inizi}")

    def test_the_status_dialog_puts_the_problem_first_with_its_remedy(self):
        from mirto.gui.stato_ui import StatoDialog

        rotto = self._letto(camera={"attiva": True, "vista_dal_sistema": False, "sensore": ""})
        dlg = StatoDialog("Balcone", self._ponte_stato([rotto], []))
        righe = self._testi(dlg.guai_lay)
        self.assertIn("non la vede", righe[0])
        self.assertIn("#E0564C", righe[0], "un errore si deve vedere rosso, non come il resto")
        self.assertIn("cavo piatto", righe[1], "e il rimedio sta subito sotto")

    def test_the_status_dialog_reads_again_when_asked(self):
        from mirto.gui.stato_ui import StatoDialog

        chiamate = []
        rotto = self._letto(stato=dict(self.NODO_STATO,
                                       registrazione=dict(self.NODO_STATO["registrazione"],
                                                          errore="disco non collegato", registra=False)))
        dlg = StatoDialog("Balcone", self._ponte_stato([rotto, self._letto()], chiamate))
        self.assertEqual(len(dlg.guai()), 1)
        # Si chiamava `_aggiorna`. Dal 25/10 si chiama `_rileggi`, perche' accanto e' nato un
        # pulsante che aggiorna **il nodo**, e due «Aggiorna» vicini sono una trappola.
        dlg._rileggi()
        self.assertEqual(chiamate, ["leggi", "leggi"])
        self.assertEqual(dlg.guai(), [], "ricollegato il disco, il problema sparisce")

    def test_the_report_goes_to_the_clipboard_without_secrets(self):
        from unittest import mock

        from mirto.gui.stato_ui import StatoDialog

        stato = dict(self.NODO_STATO)
        stato["abbinamento"] = dict(self.NODO_STATO["abbinamento"], aperto=True, pin="481516")
        dlg = StatoDialog("Balcone", self._ponte_stato([self._letto(stato=stato)], []))
        with mock.patch("mirto.gui.stato_ui.QApplication") as app:
            dlg._copia()
        testo = app.clipboard.return_value.setText.call_args[0][0]
        self.assertIn("Balcone", testo)
        self.assertIn("192.168.2.21", testo)
        self.assertNotIn("481516", testo, "il PIN non finisce in un messaggio di assistenza")
        self.assertNotEqual(dlg.nota.text(), "", "e si deve vedere che è stato copiato")

    def test_the_pairing_dialog_does_not_propose_a_factory_name(self):
        from mirto.gui.pairing_ui import PairDialog

        found = {"nome": "Node 96d3", "url": "https://mirto-node-96d3.local:8443",
                 "impronta": "AA:" * 31 + "AA", "senza_scadenza": True, "scade_tra_s": 0}
        dlg = PairDialog(found)
        self.assertEqual(dlg.name.text(), "", "«Node 96d3» non dice niente a nessuno: si chiede un nome")
        dlg2 = PairDialog(dict(found, nome="Balcone"))
        self.assertEqual(dlg2.name.text(), "Balcone", "un nome vero invece si propone")

    # --- la libreria delle azioni (23/10, rifatta il 24/10) -----------------

    PER_NODO = {
        "n1": [{"name": "Sirena dalla cassa – Ingresso", "node": "n1", "action": "sirena",
                "kind": "siren", "targets": ["person"], "duration": 60},
               {"name": "LED verde – Ingresso", "node": "n1", "action": "led", "kind": "light",
                "targets": ["person"], "duration": 10}],
        "n2": [{"name": "Sirena dalla cassa – Garage", "node": "n2", "action": "sirena",
                "kind": "siren", "targets": ["person"], "duration": 60}],
    }
    NOMI = {"nodo:n1": "Ingresso", "nodo:n2": "Garage"}

    def _libreria(self):
        from mirto.actions.pannello_azioni import libreria_aggregata
        from mirto.gui.settings import LibreriaDialog

        return LibreriaDialog(libreria_aggregata(self.PER_NODO), nomi=self.NOMI)

    def test_the_library_imports_more_than_one_at_a_time(self):
        """Chi apre la libreria sa gia' cosa vuole: farglielo fare una alla volta conta solo i clic."""
        from PySide6.QtCore import Qt

        dlg = self._libreria()
        self.assertEqual(dlg.elenco.count(), 2, "due nodi che propongono la stessa cosa fanno una voce")
        self.assertFalse(dlg.importa.isEnabled(), "senza spunte non c'e' niente da importare")
        for i in range(2):
            dlg.elenco.item(i).setCheckState(Qt.CheckState.Checked)
        self.assertTrue(dlg.importa.isEnabled())
        self.assertIn("2", dlg.importa.text(), "dice quante ne stai portando via")
        dlg._importa()
        self.assertEqual(len(dlg.scelte), 2)

    def test_the_library_takes_only_what_you_ticked(self):
        from PySide6.QtCore import Qt

        dlg = self._libreria()
        dlg.elenco.item(0).setCheckState(Qt.CheckState.Checked)   # «LED verde», in ordine alfabetico
        dlg._importa()
        self.assertEqual([a["action"] for a in dlg.scelte], ["led"])

    def test_the_same_action_on_two_nodes_lets_you_choose_where(self):
        """La stessa cosa proposta da due nodi: una riga sola, e il nodo si sceglie li' accanto."""
        from PySide6.QtCore import Qt

        dlg = self._libreria()
        titoli = [dlg.elenco.item(i).text().split("\n")[0] for i in range(dlg.elenco.count())]
        self.assertTrue(any("presente su 2 nodi" in x for x in titoli), titoli)
        riga = next(i for i, x in enumerate(titoli) if "presente su 2 nodi" in x)
        self.assertIn(riga, dlg.dove, "quella riga deve avere la tendina del nodo")
        combo = dlg.dove[riga]
        combo.setCurrentIndex(combo.findData("n2"))
        dlg.elenco.item(riga).setCheckState(Qt.CheckState.Checked)
        dlg._importa()
        self.assertEqual([a["node"] for a in dlg.scelte], ["n2"], "nasce sul nodo che hai scelto")

    # --- la striscia dei passi (tappa 3, 22/10 notte) -----------------------

    COMANDI_NODO = [{"nome": "sirena", "tipo": "siren", "descrizione": "Sirena"},
                    {"nome": "luci", "tipo": "light", "descrizione": "Luci"},
                    {"nome": "dissuasore", "tipo": "deterrent", "descrizione": "Suoni",
                     "suoni": ["falco", "sparo"]}]

    def test_an_old_action_opens_as_a_strip_of_one_piece(self):
        """Niente «prima» e «dopo»: la finestra mostra sempre una striscia, anche per le azioni vecchie."""
        from mirto.gui.settings import ActionDialog

        dlg = ActionDialog(self._azione(duration=90), "n1", self.CAMERE)
        passi = dlg.catena.passi()
        self.assertEqual([p.comando for p in passi], ["sirena"])
        self.assertEqual(passi[0].durata, 90, "la durata dell'azione diventa quella del suo unico pezzo")

    def test_a_chain_opens_with_all_its_pieces(self):
        from mirto.gui.settings import ActionDialog

        dlg = ActionDialog(self._azione(passi=[{"comando": "luci", "durata": 10, "pausa": 5},
                                               {"comando": "dissuasore", "durata": 2, "ripeti": 3}],
                                        ripeti_tutto=2), "n1", self.CAMERE)
        self.assertEqual([p.comando for p in dlg.catena.passi()], ["luci", "dissuasore"])
        self.assertEqual(dlg.catena.ripeti_tutto.value(), 2)

    def test_the_strip_drives_the_action_command(self):
        """Il tipo dell'azione si ricava dal primo pezzo: prima lo faceva una tendina."""
        from mirto.actions.pannello_catena import Passo
        from mirto.gui.settings import ActionDialog

        dlg = ActionDialog(self._azione(), "n1", self.CAMERE)
        dlg.set_node_commands(self.COMANDI_NODO)
        dlg.catena.set_passi([Passo(comando="luci", durata=10)])
        self.assertEqual(dlg.cmd.currentText(), "luci")
        self.assertEqual(dlg.kind.currentData(), "light", "una luce non e' una sirena")

    def test_a_new_action_is_born_without_a_name(self):
        """Marco, provando A-01: «il nome preinserito deve essere vuoto».

        Lo riempiva ``_command_picked`` leggendo la descrizione del primo comando del nodo — e siccome
        quel nome esisteva gia', ci attaccava anche il contatore: «Sirena dalla cassa (max 3 minuti) 3».
        Arrivava **dopo** l'apertura, quando rispondeva il nodo, quindi non bastava guardare la
        finestra appena aperta per accorgersene.
        """
        from mirto.gui.settings import ActionDialog

        dlg = ActionDialog(None, "n1", self.CAMERE,
                           taken_names={"Sirena", "Sirena dalla cassa (max 3 minuti)"})
        dlg.set_node_commands(self.COMANDI_NODO)
        self.assertEqual(dlg.name.text(), "", "una cosa nuova nasce senza nome")

    # --- la domanda segue il suono scelto (23/10 sera) ----------------------

    COMANDI_CON_DURATE = [{"nome": "sirena", "tipo": "siren", "descrizione": "Sirena dalla cassa"},
                          {"nome": "dissuasore", "tipo": "deterrent", "descrizione": "Suoni",
                           "suoni": ["falco", "sparo"],
                           "durate": {"falco": 1.5, "sparo": 0.9}}]

    def test_the_piece_asks_seconds_for_a_siren_and_times_for_a_shot(self):
        """Aprendo un pezzo «sirena» e scegliendoci dentro uno sparo restavano i 60 s della sirena.

        La domanda veniva decisa costruendo la finestra e non si rifaceva piu'. Adesso si rifa' a ogni
        cambio di suono, e si vede **una** delle due: o i secondi, o le volte.
        """
        from mirto.actions.pannello_catena import Passo
        from mirto.gui.catena_ui import PassoDialog

        dlg = PassoDialog(Passo(comando="sirena", durata=60.0), self.COMANDI_CON_DURATE)
        self.assertFalse(dlg._a_volte, "la sirena la genera il nodo: i secondi sono la domanda giusta")
        self.assertFalse(dlg.durata.isHidden())
        self.assertTrue(dlg.volte.isHidden())
        dlg.quale.setCurrentIndex(dlg.quale.findData("suono:dissuasore:sparo"))
        self.assertTrue(dlg._a_volte, "uno sparo si conta, non si cronometra")
        self.assertTrue(dlg.durata.isHidden(), "i 60 s della sirena non restano addosso al colpo")
        self.assertFalse(dlg.volte.isHidden())

    # --- dove si fa questo pezzo (23/10, tappa 3) ---------------------------

    DUE_NODI = [{"nome": "luci", "tipo": "light", "descrizione": "Luci", "dispositivo": "nodo:n1"},
                {"nome": "sirena", "tipo": "siren", "descrizione": "Sirena", "dispositivo": "nodo:n1"},
                {"nome": "sirena", "tipo": "deterrent", "descrizione": "Suoni", "dispositivo": "nodo:n2",
                 "suoni": ["sparo"], "durate": {"sparo": 0.9}}]
    DOVE = [{"chiave": "nodo:n1", "nome": "Ingresso"}, {"chiave": "nodo:n2", "nome": "Garage"}]

    def test_editing_a_piece_does_not_move_it_in_the_chain(self):
        """Marco, 25/10: *«se lo modifico e salvo, nel flusso ha perso "insieme" e c'e' scritto subito»*.

        Il pezzo esce da `passo_da_scelte`, che lo costruisce **da zero** dalle caselle della
        finestra: tutto quello che la finestra non chiede nasceva al valore di partenza, e per un
        pezzo dentro un gruppo «al valore di partenza» vuol dire *fuori dal gruppo*. Un gruppo
        costruito con tre clic si scioglieva aprendo il cog e premendo OK senza toccare niente.

        La prova non elenca i campi da conservare: se li ricava da `Passo`, togliendo quelli che la
        finestra chiede davvero. Cosi' il prossimo campo che nasce o e' una domanda della finestra, o
        questa prova pretende che sopravviva — e un campo nuovo non puo' sparire in silenzio.
        """
        import dataclasses

        from mirto.actions.passi import Passo
        from mirto.gui.catena_ui import PassoDialog

        chiesti = {"comando", "suono", "durata", "volte", "ripeti", "dispositivo", "uscita"}
        restanti = [f for f in dataclasses.fields(Passo) if f.name not in chiesti]
        self.assertTrue(restanti, "se la finestra chiede tutto, questa prova non serve piu'")
        suoi = {"bool": True, "float": 7.5, "int": 3, "str": "x"}
        valori = {}
        for campo in restanti:
            self.assertIn(campo.type, suoi,
                          f"«{campo.name}» e' di un tipo nuovo: aggiorna questa prova con un valore suo")
            valori[campo.name] = suoi[campo.type]
        prima = Passo(comando="sirena", durata=3, **valori)
        dlg = PassoDialog(prima, self.DUE_NODI, dispositivi=self.DOVE, dispositivo="nodo:n1")
        dlg._salva()
        self.assertIsNotNone(dlg.risultato, "il pezzo non e' nemmeno uscito dalla finestra")
        for nome, atteso in valori.items():
            self.assertEqual(getattr(dlg.risultato, nome), atteso,
                             f"«{nome}» si e' perso aprendo e chiudendo la finestra")
        self.assertFalse(hasattr(dlg, "pausa"),
                         "«Poi aspetta» non si chiede piu' qui: l'attesa e' la freccia fra i riquadri")

    def test_the_piece_asks_where_only_when_there_is_a_choice(self):
        """Con un nodo solo, «Dove» sarebbe una domanda con una risposta sola."""
        from mirto.actions.pannello_catena import Passo
        from mirto.gui.catena_ui import PassoDialog

        solo = PassoDialog(Passo(comando="luci", durata=5), self.DUE_NODI,
                           dispositivi=self.DOVE[:1], dispositivo="nodo:n1")
        self.assertTrue(solo.dove.isHidden())
        due = PassoDialog(Passo(comando="luci", durata=5), self.DUE_NODI,
                          dispositivi=self.DOVE, dispositivo="nodo:n1")
        self.assertFalse(due.dove.isHidden())
        self.assertEqual(due.dove.currentData(), "nodo:n1", "parte da dove sta l'azione")

    def test_changing_where_changes_what_you_can_choose(self):
        """Due nodi possono avere un comando con lo **stesso nome** e due cose diverse dentro.

        All'ingresso «sirena» e' un ululato generato — i secondi sono la domanda giusta; in garage e'
        un dissuasore con dentro uno sparo da nove decimi, e li' si conta. Se cambiando nodo l'elenco
        non si rifacesse, si sceglierebbe il comando di un nodo per eseguirlo su un altro.
        """
        from mirto.actions.pannello_catena import Passo
        from mirto.gui.catena_ui import PassoDialog

        dlg = PassoDialog(Passo(comando="sirena", durata=60), self.DUE_NODI,
                          dispositivi=self.DOVE, dispositivo="nodo:n1")
        self.assertFalse(dlg._a_volte, "la sirena dell'ingresso si tiene accesa a secondi")
        dlg.dove.setCurrentIndex(dlg.dove.findData("nodo:n2"))
        self.assertEqual(dlg._dispositivo, "nodo:n2")
        # Il testo della voce porta anche l'icona (🔊 o 🎵): qui interessa **quali** suoni ci sono,
        # non come sono scritti, quindi si guarda la chiave — che e' anche quella che si salva.
        self.assertIn("suono:sirena:sparo",
                      [dlg.quale.itemData(i) for i in range(dlg.quale.count())],
                      "i suoni sono quelli del nodo scelto adesso")
        dlg.quale.setCurrentIndex(dlg.quale.findData("suono:sirena:sparo"))
        self.assertTrue(dlg._a_volte, "in garage quella sirena e' un suono che si conta")

    def test_a_piece_that_stays_where_it_was_does_not_write_the_device(self):
        """La promessa della prova 19: salvare senza spostare niente non aggiunge una riga."""
        from mirto.actions.pannello_catena import Passo
        from mirto.gui.catena_ui import PassoDialog

        dlg = PassoDialog(Passo(comando="luci", durata=5), self.DUE_NODI,
                          dispositivi=self.DOVE, dispositivo="nodo:n1")
        dlg._salva()
        self.assertEqual(dlg.risultato.dispositivo, "")
        dlg2 = PassoDialog(Passo(comando="luci", durata=5), self.DUE_NODI,
                           dispositivi=self.DOVE, dispositivo="nodo:n1")
        dlg2.dove.setCurrentIndex(dlg2.dove.findData("nodo:n2"))
        dlg2._salva()
        self.assertEqual(dlg2.risultato.dispositivo, "nodo:n2", "spostandolo invece si scrive")

    def test_the_play_button_becomes_stop_while_it_sounds(self):
        """Due «ferma» diversi: un pezzo si spegne, il flusso si interrompe (24/10)."""
        from mirto.actions.pannello_catena import Passo
        from mirto.gui.catena_ui import StrisciaDiPassi

        striscia = StrisciaDiPassi()
        striscia.set_contesto(self.COMANDI_CON_DURATE, casse=[{"nome": "cassa"}], nodo_in_linea=True,
                              dispositivo="nodo:n1")
        striscia.set_passi([Passo(comando="dissuasore", suono="sparo", volte=3, durata=3.0),
                            Passo(comando="sirena", durata=10)])
        self.assertAlmostEqual(striscia.secondi_del_pezzo(0), 3 * 0.9 + 0.2, places=2,
                               msg="con «quante volte» dura quanto il suono, non quanto chiediamo")
        self.assertEqual(striscia.secondi_del_pezzo(1), 10.0)

        fermati = []
        striscia.ferma_pezzo.connect(fermati.append)
        striscia.in_prova(2.0, indice=0)
        self.assertEqual(striscia.prova.text(), "Ferma")
        striscia._pezzo_o_ferma(0)
        self.assertEqual(fermati, [0], "ripremendo quel play si ferma quel pezzo")
        self.assertEqual(striscia.prova.text(), "Prova tutta la catena")

        catene = []
        striscia.ferma_catena.connect(lambda: catene.append(True))
        striscia.in_prova(5.0)
        striscia._prova_o_ferma()
        self.assertEqual(catene, [True], "il pulsante grande ferma il flusso, coda compresa")

    def test_a_counted_piece_never_also_repeats(self):
        """«volte» e «ripeti» insieme fanno tre per tre: nove spari per un «× 3»."""
        from mirto.actions.pannello_catena import Passo
        from mirto.gui.catena_ui import PassoDialog

        dlg = PassoDialog(Passo(comando="sirena", durata=60.0, ripeti=3), self.COMANDI_CON_DURATE)
        dlg.quale.setCurrentIndex(dlg.quale.findData("suono:dissuasore:sparo"))
        dlg.volte.setValue(3)
        dlg._salva()
        self.assertEqual(dlg.risultato.comando, "dissuasore")
        self.assertEqual(dlg.risultato.volte, 3)
        self.assertEqual(dlg.risultato.ripeti, 1)
        self.assertLess(dlg.risultato.durata, 4.0, "i secondi si rifanno sul suono scelto")

    def test_a_single_simple_piece_is_still_saved_the_old_way(self):
        from unittest import mock

        """Una configurazione che non usa le catene non deve cambiare di una riga."""
        from mirto.actions.pannello_catena import Passo
        from mirto.gui.settings import ActionDialog

        dlg = ActionDialog(self._azione(), "n1", self.CAMERE)
        dlg.set_node_commands(self.COMANDI_NODO)
        dlg.catena.set_passi([Passo(comando="sirena", durata=60)])
        with mock.patch("mirto.gui.settings.QMessageBox"):
            dlg._accept()
        self.assertIsNotNone(dlg.result_action)
        self.assertNotIn("passi", dlg.result_action, "un passo semplice resta «action» + «duration»")
        self.assertEqual(dlg.result_action["action"], "sirena")
        self.assertEqual(dlg.result_action["duration"], 60)

    def test_a_real_chain_is_saved_as_steps(self):
        from unittest import mock

        from mirto.actions.pannello_catena import Passo
        from mirto.gui.settings import ActionDialog

        dlg = ActionDialog(self._azione(), "n1", self.CAMERE)
        dlg.set_node_commands(self.COMANDI_NODO)
        dlg.catena.set_passi([Passo(comando="luci", durata=10, pausa=5),
                              Passo(comando="sirena", durata=120)])
        dlg.catena.ripeti_tutto.setValue(3)
        with mock.patch("mirto.gui.settings.QMessageBox"):
            dlg._accept()
        a = dlg.result_action
        self.assertEqual([p["comando"] for p in a["passi"]], ["luci", "sirena"])
        self.assertEqual(a["ripeti_tutto"], 3)
        self.assertGreaterEqual(a["duration"], 120,
                                "la durata dell'azione non puo' tagliare un pezzo piu' lungo")

    def test_a_node_without_a_speaker_says_what_is_missing_in_the_plus_menu(self):
        """La voce resta visibile e porta dove si aggiusta: nasconderla non insegna niente."""
        from mirto.gui.settings import ActionDialog

        dlg = ActionDialog(self._azione(), "n1", self.CAMERE)
        dlg.set_node_commands(self.COMANDI_NODO)
        dlg.set_stato_nodo(casse=[], in_linea=True)
        testi = [a.text() for a in dlg.catena._piu_menu.actions()]
        self.assertTrue(any("cassa" in t.lower() for t in testi),
                        f"la voce si vede comunque, e dice cosa manca: {testi}")
        self.assertTrue(any("—" in t and "cassa" in t.lower() for t in testi),
                        f"e dice cosa manca, in vista e non dentro un sotto-menu: {testi}")

    def test_the_plus_is_always_the_same_button(self):
        """Creare e distruggere un pulsante col suo menu a ogni ridisegno fa crollare Qt in chiusura."""
        from mirto.actions.pannello_catena import Passo
        from mirto.gui.settings import ActionDialog

        dlg = ActionDialog(self._azione(), "n1", self.CAMERE)
        dlg.set_node_commands(self.COMANDI_NODO)
        prima = dlg.catena._piu_btn
        for _ in range(5):
            dlg.catena.set_passi([Passo(comando="luci", durata=10)])
            dlg.catena.set_passi([])
        self.assertIs(dlg.catena._piu_btn, prima, "il «+» si aggiorna, non si ricrea")
        self.assertGreater(len(dlg.catena._piu_menu.actions()), 0, "e il menu si riempie ogni volta")

    def test_the_test_button_is_off_until_there_is_something_to_test(self):
        from mirto.actions.pannello_catena import Passo
        from mirto.gui.settings import ActionDialog

        dlg = ActionDialog(self._azione(), "n1", self.CAMERE)
        dlg.catena.set_passi([])
        self.assertFalse(dlg.catena.prova.isEnabled())
        self.assertTrue(dlg.catena.prova.toolTip(), "un pulsante spento dice sempre perche'")
        dlg.set_stato_nodo(casse=["jack"], in_linea=True)
        dlg.catena.set_passi([Passo(comando="luci", durata=10)])
        self.assertTrue(dlg.catena.prova.isEnabled())

    def test_moving_a_piece_keeps_them_all(self):
        from mirto.actions.pannello_catena import Passo
        from mirto.gui.settings import ActionDialog

        dlg = ActionDialog(self._azione(), "n1", self.CAMERE)
        dlg.catena.set_passi([Passo(comando="luci"), Passo(comando="sirena")])
        dlg.catena._sposta(1, -1)
        self.assertEqual([p.comando for p in dlg.catena.passi()], ["sirena", "luci"])
        dlg.catena._togli(0)
        self.assertEqual([p.comando for p in dlg.catena.passi()], ["luci"])

    def test_the_wait_lives_on_the_arrow(self):
        """L'attesa si aggiunge dal «+» e si legge sulla freccia, non dentro il riquadro."""
        from mirto.actions.pannello_catena import ATTESA, Passo
        from mirto.gui.settings import ActionDialog

        dlg = ActionDialog(self._azione(), "n1", self.CAMERE)
        dlg.catena.set_passi([Passo(comando="luci", durata=10)])
        dlg.catena._aggiungi(ATTESA)
        self.assertGreater(dlg.catena.passi()[0].pausa, 0)

    def test_a_manual_action_has_no_strip_at_all(self):
        """«Ascolta da Telegram» non fa niente al nodo: una striscia vuota farebbe solo dubitare."""
        from mirto.gui.settings import ActionDialog

        dlg = ActionDialog(self._azione(kind="audio"), "n1", self.CAMERE)
        self.assertTrue(dlg.catena.isHidden())
        dlg.kind.setCurrentIndex(dlg.kind.findData("siren"))
        self.assertFalse(dlg.catena.isHidden())

    # --- il wizard (22/10 notte, dopo che Marco ha visto il modulo unico) ----

    def test_the_wizard_asks_four_things_not_eighteen(self):
        from mirto.gui.settings import ActionWizard

        w = ActionWizard(None, "n1", self.CAMERE)
        self.assertEqual(w.pile.count(), 4)
        self.assertEqual(w.pile.currentIndex(), 0)
        self.assertFalse(w.indietro.isEnabled(), "dalla prima non si torna indietro")
        # «isHidden», non «isVisible»: una finestra mai aperta non ha niente di visibile, e
        # «isVisible» sarebbe falso anche per un pulsante che c'è. Le prove di qui lo sapevano già
        # dal 22/10; io no, e mi è costato una suite rossa sul Mac di Marco.
        self.assertFalse(w.avanti.isHidden())
        w.vai(len(w.PAGINE) - 1)
        self.assertTrue(w.avanti.isHidden(), "nell'ultima schermata «Avanti» non ha dove andare")

    def test_the_step_title_is_loud_enough_to_be_a_title(self):
        """E' l'unica cosa che dice a che punto sei: a 18 px in minuscolo era una riga qualsiasi."""
        from mirto.gui.settings import ActionWizard

        w = ActionWizard(None, "n1", self.CAMERE)
        self.assertEqual(w.passo_titolo.text(), w.passo_titolo.text().upper())
        self.assertTrue(w.passo_titolo.text().strip())

    def test_the_first_screen_is_as_short_as_it_is(self):
        """Tre campi in croce non devono stare in una finestra alta come la schermata piu' lunga.

        «Ignored» nella politica delle dimensioni riguarda il desiderio, non l'obbligo: l'altezza
        **minima** di una pagina nascosta continua a tenere su la finestra, e la griglia dei
        riconoscimenti ne chiede duecento pixel. Marco, 24/10: «troppo spazio tra i campi di testo e
        la barra sotto con i pulsanti».
        """
        from mirto.gui.settings import ActionWizard

        w = ActionWizard(None, "n1", self.CAMERE, microfoni=self.MICROFONI)
        w.vai(1)
        seconda = w.pile.minimumSizeHint().height()
        w.vai(0)
        prima = w.pile.minimumSizeHint().height()
        # Non si guarda `height()`: quella e' la dimensione **chiesta**, e senza nessuno che disegni
        # la finestra resta quella del minimo. Quello che conta e' il minimo, che e' proprio la cosa
        # che le pagine nascoste tenevano su.
        self.assertEqual(w.pile.widget(1).maximumHeight(), 0,
                         "una pagina che non e' quella aperta non deve tenere su la finestra")
        self.assertLess(prima, seconda,
                        "la prima schermata ha tre campi, la seconda due griglie: non possono "
                        "chiedere la stessa altezza")

    def test_the_threshold_lives_inside_the_box(self):
        """Marco, 24/10: *«se selezioni piu' box si creano N righe di sicurezza; conviene aggiungere
        l'informazione sulla sicurezza direttamente dentro il box, con la combo di valori li'»*.

        Dieci cose spuntate facevano dieci righe sotto la griglia, e la soglia e' una proprieta' di
        quella cosa li'. Dentro il riquadro, e solo quando e' acceso: una tendina su una cosa che non
        riconosci e' una domanda senza oggetto.
        """
        from mirto.gui.settings import ActionDialog

        dlg = ActionDialog(self._azione(targets=["person"], cameras=["Ingresso"]), "n1", self.CAMERE)
        persona = dlg.targets_cam._spunte["person"]
        gatto = dlg.targets_cam._spunte["cat"]
        self.assertIsNotNone(persona.soglia, "il riquadro acceso ha la sua tendina «AI»")
        # `isVisible()` qui direbbe sempre di no: la finestra non viene aperta da nessuno, e un
        # figlio di una finestra chiusa non e' visibile. Quello che si chiede e' se e' **nascosto**.
        self.assertFalse(persona._riga_soglia.isHidden())
        self.assertTrue(gatto._riga_soglia.isHidden(), "spento, non chiede niente")
        self.assertEqual(persona.soglia.itemText(0), "standard 60%",
                         "la prima voce dice il numero che si segue lasciandola stare, "
                         "senza parentesi: nella tendina lo spazio e' tutto")
        self.assertEqual(gatto.soglia.itemText(0), "standard 50%",
                         "e non e' lo stesso per tutti: un gatto non e' una persona")
        persona.soglia.setCurrentIndex(persona.soglia.findData(0.9))
        self.assertEqual(dlg.targets_cam.valori(), {"person": 0.9})
        gatto.cambia()
        self.assertFalse(gatto._riga_soglia.isHidden(), "acceso, la tendina compare")

    def test_the_plus_tells_your_sounds_from_the_node_ones(self):
        """Il nodo sa quali suoni fa lui; quello che sa il nodo deve arrivare fino al «+» (24/10)."""
        from mirto.actions.pannello_catena import SUONO_GENERATO, SUONO_TUO
        from mirto.gui.settings import ActionDialog

        dlg = ActionDialog(self._azione(), "n1", self.CAMERE)
        dlg.set_node_commands(self.COMANDI_NODO)
        self.assertIsNone(dlg.catena._suoni_propri, "finche' non risponde, non si indovina")
        dlg.set_suoni_del_nodo(["falco"])
        self.assertEqual(dlg.catena._suoni_propri, ["falco"],
                         "il passaggio dalla finestra alla striscia e' quello che qui si puo' rompere")
        self.assertNotEqual(SUONO_GENERATO, SUONO_TUO)

    def test_the_cog_shows_the_same_icons_as_the_plus(self):
        """Nel «+» un suono tuo si distingueva, aprendo il pezzo col cog tornavano tutti uguali:
        la stessa domanda con due risposte diverse (Marco, 24/10)."""
        from mirto.actions.pannello_catena import SUONO_TUO, Passo
        from mirto.gui.catena_ui import PassoDialog

        comandi = [{"nome": "dissuasore", "tipo": "deterrent", "suoni": ["falco", "brano_tuo"]}]
        dlg = PassoDialog(Passo(comando="dissuasore", suono="falco"), comandi,
                          suoni_propri=["falco"])
        testi = [dlg.quale.itemText(i) for i in range(dlg.quale.count())]
        tuo = next(x for x in testi if "brano_tuo" in x)
        self.assertTrue(tuo.startswith(SUONO_TUO), f"«{tuo}» non dice che e' un file tuo")
        suo = next(x for x in testi if "falco" in x)
        self.assertFalse(suo.startswith(SUONO_TUO))

    def test_the_plus_asks_where_first(self):
        """Il menu del «+» e' l'ordine del ragionamento: dove, con cosa, cosa (24/10, tappa 2)."""
        from mirto.gui.catena_ui import StrisciaDiPassi

        s = StrisciaDiPassi()
        s.set_contesto(self.DUE_NODI, casse=["cassa"], dispositivi=self.DOVE, dispositivo="nodo:n1",
                       uscite_per={"nodo:n1": [{"chiave": "usb:X", "etichetta": "Cassa USB"},
                                               {"chiave": "bluetooth:A", "etichetta": "Portico"}],
                                   "nodo:n2": []})
        voci = s._piu_menu.actions()
        rami = [a.text() for a in voci if a.menu() is not None]
        self.assertTrue(rami, "con due nodi il primo livello sono i nodi")
        primo = next(a for a in voci if a.menu() is not None)
        dentro = [x.text() for x in primo.menu().actions()]
        self.assertTrue(any("Cassa USB" in x for x in dentro),
                        f"sotto il nodo ci sono le sue casse: {dentro}")

    def test_with_one_device_the_plus_starts_from_its_parts(self):
        """Non si fa una domanda che ha una risposta sola: con un nodo si parte dai componenti."""
        from mirto.gui.catena_ui import StrisciaDiPassi

        s = StrisciaDiPassi()
        uno = [d for d in self.DOVE if d["chiave"] == "nodo:n1"]
        s.set_contesto([c for c in self.DUE_NODI if c.get("dispositivo") == "nodo:n1"],
                       casse=["cassa"], dispositivi=uno, dispositivo="nodo:n1",
                       uscite_per={"nodo:n1": [{"chiave": "usb:X", "etichetta": "Cassa USB"}]})
        testi = [a.text() for a in s._piu_menu.actions()]
        self.assertTrue(any("Cassa USB" in x for x in testi),
                        f"la cassa si vede subito, senza passare dal nome del nodo: {testi}")

    def test_the_cog_can_correct_the_speaker(self):
        """Il «+» la sceglie, il cog la corregge: una cassa sbagliata si scopre sentendo."""
        from mirto.actions.pannello_catena import Passo
        from mirto.gui.catena_ui import PassoDialog

        dlg = PassoDialog(Passo(comando="sirena", durata=10), self.DUE_NODI,
                          dispositivi=self.DOVE, dispositivo="nodo:n1",
                          uscite_per={"nodo:n1": [{"chiave": "usb:X", "etichetta": "Cassa USB"}]})
        self.assertEqual(dlg.uscita.itemData(0), "", "la prima resta «quella del nodo»")
        dlg.uscita.setCurrentIndex(dlg.uscita.findData("usb:X"))
        dlg._salva()
        self.assertEqual(dlg.risultato.uscita, "usb:X")

    def test_a_box_can_hold_something_that_starts_with_it(self):
        """Il «+» del riquadro aggiunge **insieme**, quello della striscia aggiunge **dopo**:
        e' la differenza fra una fila e un accordo (tappa 3, 24/10)."""
        from mirto.actions.pannello_catena import Passo
        from mirto.gui.catena_ui import StrisciaDiPassi

        s = StrisciaDiPassi()
        s.set_contesto(self.DUE_NODI, casse=["cassa"], dispositivi=self.DOVE, dispositivo="nodo:n1")
        s.set_passi([Passo(comando="sirena", durata=3)])
        s._aggiungi("pezzo:nodo:n2||sirena|sparo", dentro=0)
        passi = s.passi()
        self.assertEqual(len(passi), 2)
        self.assertTrue(passi[1].nel_gruppo, "il secondo parte insieme al primo")
        self.assertEqual(passi[1].dispositivo, "nodo:n2", "ed e' dell'altro nodo")
        s._aggiungi("pezzo:nodo:n1||luci|")
        self.assertFalse(s.passi()[2].nel_gruppo, "il «+» della striscia aggiunge in fila, come prima")

    def test_together_is_a_clasp_not_a_wait(self):
        """Fra due pezzi dello stesso gruppo non c'e' un'attesa da cambiare: c'e' il **modo**.

        Fino alla tappa 5 questa graffa era spenta, perche' non c'era niente da scegliere. Dal 25/10
        e' l'unico posto da cui si cambia come parte il gruppo — insieme o a turno — e si clicca:
        quello che si legge si cambia dov'e' scritto.
        """
        from mirto.actions.pannello_catena import Passo, riquadri
        from mirto.gui.catena_ui import StrisciaDiPassi

        s = StrisciaDiPassi()
        s.set_contesto(self.DUE_NODI, casse=["cassa"], dispositivi=self.DOVE, dispositivo="nodo:n1")
        s.set_passi([Passo(comando="sirena", durata=3),
                     Passo(comando="luci", durata=3, nel_gruppo=True)])
        dati = riquadri(s.passi(), self.DUE_NODI, dispositivo="nodo:n1")
        self.assertTrue(dati[0].unito)
        self.assertEqual(dati[0].freccia, "insieme")
        freccia = s._freccia(0, dati[0].freccia, dati[0].unito)
        self.assertTrue(freccia.isEnabled(), "si clicca, e apre la scelta del modo")
        self.assertNotIn("▶", freccia.text(), "non e' la freccia di un'attesa")

    def test_the_mode_is_changed_on_the_head_of_the_group(self):
        """La graffa sta fra due pezzi, ma il modo e' del gruppo: si scrive sul capogruppo.

        Il pezzo di destra e' quello che si e' attaccato, e scrivere li' il modo vorrebbe dire
        poter avere un gruppo «uno insieme e uno a turno». Si scrive sul capo, sempre — anche
        cliccando la graffa fra il secondo e il terzo pezzo.
        """
        from mirto.actions.pannello_catena import Passo, riquadri
        from mirto.gui.catena_ui import StrisciaDiPassi

        s = StrisciaDiPassi()
        s.set_contesto(self.DUE_NODI, casse=["cassa"], dispositivi=self.DOVE, dispositivo="nodo:n1")
        s.set_passi([Passo(comando="sirena", durata=3),
                     Passo(comando="luci", durata=3, nel_gruppo=True),
                     Passo(comando="luci", durata=3, nel_gruppo=True)])
        s._metti_modo(0, "a_turno")
        passi = s.passi()
        self.assertTrue(passi[0].a_turno, "il modo e' scritto sul capogruppo")
        self.assertFalse(passi[1].a_turno or passi[2].a_turno, "e in nessun altro posto")
        dati = riquadri(passi, self.DUE_NODI, dispositivo="nodo:n1")
        self.assertEqual([d.freccia for d in dati[:2]], ["a turno", "a turno"],
                         "tutt'e due le graffe del gruppo dicono la stessa cosa: e' un gruppo solo")

    def test_the_arrow_between_the_last_two_still_finds_the_head(self):
        from mirto.actions.pannello_catena import Passo
        from mirto.gui.catena_ui import StrisciaDiPassi

        s = StrisciaDiPassi()
        s.set_contesto(self.DUE_NODI, casse=["cassa"], dispositivi=self.DOVE, dispositivo="nodo:n1")
        s.set_passi([Passo(comando="sirena", durata=3),
                     Passo(comando="luci", durata=3, nel_gruppo=True),
                     Passo(comando="luci", durata=3, nel_gruppo=True)])
        from mirto.actions.passi import capogruppo

        self.assertEqual(capogruppo(s.passi(), 2), 0, "la graffa fra il 2o e il 3o parla del gruppo di 0")

    def test_the_main_button_is_the_coloured_one(self):
        """In un wizard «Avanti» e' quello che si cerca: grigio come gli altri lo si cerca ogni volta."""
        from mirto.gui.settings import ActionWizard

        w = ActionWizard(None, "n1", self.CAMERE)
        self.assertEqual(w.avanti.objectName(), "primario")
        self.assertEqual(w.fine.objectName(), "primario", "nell'ultima schermata l'azione e' «Salva»")

    def test_one_click_one_tick(self):
        """Niente «semiselezione»: la riga non si seleziona, si spunta (24/10)."""
        from PySide6.QtWidgets import QAbstractItemView

        from mirto.gui.settings import ActionWizard

        w = ActionWizard(None, "n1", self.CAMERE, microfoni=self.MICROFONI)
        for elenco in (w.cams, w.mics):
            self.assertEqual(elenco.selectionMode(), QAbstractItemView.SelectionMode.NoSelection,
                             "selezionare una camera non vuol dire niente: non c'e' nessun «apri»")
            self.assertLessEqual(elenco.height(), elenco.count() * 26 + 6,
                                 "alto quanto le voci che ha, non novanta pixel sempre")
            self.assertLessEqual(elenco.maximumWidth(), 460, "un campo, non una barra")

    def test_the_wizard_does_not_let_you_leave_a_screen_half_done(self):
        """Scoprire alla fine che mancava una cosa della prima pagina vuol dire rifare il giro."""
        from mirto.gui.settings import ActionWizard

        w = ActionWizard(None, "n1", self.CAMERE)
        self.assertTrue(w._cosa_manca(0), "senza nome non si passa")
        w.name.setText("Campanello")
        self.assertEqual(w._cosa_manca(0), "")
        self.assertTrue(w._cosa_manca(2), "senza pezzi non si passa dalla terza")

    def test_save_waits_for_the_end_when_you_are_creating(self):
        """Un'azione nuova a meta' non e' un'azione, e un pulsante acceso che ti rimanda indietro
        e' una presa in giro. Modificando invece e' sempre acceso: per cambiare un numero non si
        passano quattro schermate (23/10)."""
        from mirto.gui.settings import ActionWizard

        nuova = ActionWizard(None, "n1", self.CAMERE)
        for i in range(3):
            nuova.vai(i)
            self.assertFalse(nuova.fine.isEnabled(), f"creando, «Salva» non si accende alla {i}")
        nuova.vai(3)
        self.assertTrue(nuova.fine.isEnabled(), "nell'ultima si")

        modifica = ActionWizard(self._azione(), "n1", self.CAMERE)
        for i in range(4):
            modifica.vai(i)
            self.assertTrue(modifica.fine.isEnabled(), f"modificando, «Salva» e' sempre acceso ({i})")

    def test_the_window_fits_the_page_it_shows(self):
        """Un QStackedWidget chiede lo spazio della pagina piu' grande: quattro schermate di cui una
        lunga fanno quattro finestre lunghe, e tre su quattro restano mezze vuote.

        Fino al 29/10 la prova dava per scontato che la piu' alta fosse la seconda schermata. Dal
        27/10 l'ultima porta il riassunto della tipologia ed e' diventata lei la piu' alta: la prova
        falliva (280 contro 421) mentre la finestra faceva esattamente il suo lavoro. Adesso la piu'
        alta e la piu' bassa si scelgono misurando i moduli, e si guarda prima la causa — le pagine
        nascoste non tengono su la finestra — poi l'effetto."""
        from mirto.gui.settings import ActionWizard

        w = ActionWizard(None, "n1", self.CAMERE)
        quante = len(w.PAGINE)
        for i in range(quante):
            w.vai(i)
            for k in range(w.pile.count()):
                self.assertEqual(w.pile.widget(k).maximumHeight() == 0, k != i,
                                 f"alla schermata {i}, la pagina {k} tiene su la finestra o e' schiacciata")
        moduli = [w._moduli[i].sizeHint().height() for i in range(quante)]
        bassa, alta = moduli.index(min(moduli)), moduli.index(max(moduli))
        w.vai(bassa)
        corta = w.height()
        w.vai(alta)
        self.assertGreaterEqual(w.height(), corta, "la finestra cresce dove serve")
        w.vai(bassa)
        self.assertEqual(w.height(), corta, "e si stringe dove non serve")

    def test_the_delay_is_not_asked_anymore(self):
        """Era il ritardo fra l'evento e l'azione, di quando un'azione faceva una cosa sola: con la
        striscia, «aspetta prima di partire» e' un pezzo come gli altri."""
        from mirto.gui.settings import ActionWizard

        w = ActionWizard(None, "n1", self.CAMERE)
        in_pagina = [c for _t, _s, chiavi in w.PAGINE for c in chiavi]
        self.assertNotIn("ritardo", in_pagina)

    def test_the_wizard_shows_each_field_once_and_only_once(self):
        """Stessi campi della finestra di prima, disposti a pagine: nessuno perso, nessuno doppio."""
        from mirto.gui.settings import ActionWizard

        w = ActionWizard(None, "n1", self.CAMERE)
        in_pagina = [c for _t, _s, chiavi in w.PAGINE for c in chiavi]
        self.assertEqual(sorted(in_pagina), sorted(set(in_pagina)), "un campo in due schermate")
        dichiarate = {chiave for chiave, _t, _w in w._righe}
        # «ritardo» e «durata» non si chiedono piu': restano nel dato, non in una schermata.
        self.assertEqual(set(in_pagina), dichiarate - {"ritardo"},
                         f"campi non collocati: {dichiarate - set(in_pagina) - {'ritardo'}}")

    def test_the_type_is_not_asked_anymore(self):
        """Chi configura sa che vuole far suonare la sirena, non che «e' di tipo sirena».

        La prova e' cambiata il 23/10 e vale la pena dire perche'. Diceva «un dissuasore in fondo
        rende dissuasore tutta l'azione»: era la regola del 22/10, ed e' durata un giorno. Con il
        caso del recinto — una voce che dice a una persona di allontanarsi — dedurre «dissuasore» dal
        comando avrebbe fatto sparire «persona» dagli obiettivi al salvataggio. Adesso il tipo lo
        decide **chi fa scattare** l'azione, e i comandi decidono solo i limiti di durata.
        """
        from mirto.actions.pannello_catena import Passo
        from mirto.gui.settings import ActionWizard

        w = ActionWizard(None, "n1", self.CAMERE)
        self.assertNotIn("tipo", {chiave for chiave, _t, _wid in w._righe},
                         "il tipo non e' piu' una riga da riempire")
        w.set_node_commands(self.COMANDI_NODO)
        w.catena.set_passi([Passo(comando="luci", durata=10), Passo(comando="dissuasore", durata=3)])
        self.assertNotEqual(w.kind.currentData(), "deterrent",
                            "una voce puntata su una persona non fa un dissuasore: e' il recinto")
        w.catena.set_passi([Passo(comando="luci", durata=10), Passo(comando="sirena", durata=20)])
        self.assertEqual(w.kind.currentData(), "siren", "i comandi decidono i limiti di durata")

    def test_a_manual_action_does_not_become_a_siren_by_itself(self):
        """Un'azione «ascolta da Telegram» non deve cambiare natura perche' c'era un comando scritto."""
        from mirto.gui.settings import ActionWizard

        w = ActionWizard(self._azione(kind="audio", action="sirena"), "n1", self.CAMERE)
        w.set_node_commands(self.COMANDI_NODO)
        self.assertEqual(w.kind.currentData(), "audio")

    def test_the_wizard_saves_the_same_action_as_the_old_form(self):
        from unittest import mock

        from PySide6.QtCore import Qt

        from mirto.actions.pannello_catena import Passo
        from mirto.gui.settings import ActionWizard

        w = ActionWizard(None, "n1", self.CAMERE)
        w.set_node_commands(self.COMANDI_NODO)
        w.cams.item(0).setCheckState(Qt.CheckState.Checked)   # da dove guarda: da oggi si dice
        w.name.setText("Campanello")
        w.catena.set_passi([Passo(comando="luci", durata=10, pausa=5),
                            Passo(comando="sirena", durata=20)])
        with mock.patch("mirto.gui.settings.QMessageBox"):
            w._accept()
        a = w.result_action
        self.assertIsNotNone(a, "le quattro schermate erano complete: doveva salvare")
        self.assertEqual(a["name"], "Campanello")
        self.assertEqual(a["kind"], "siren", "il tipo e' una conseguenza, e finisce comunque salvato")
        self.assertEqual([p["comando"] for p in a["passi"]], ["luci", "sirena"])

    def test_saving_from_the_first_screen_still_checks_the_others(self):
        """Niente azioni a meta': si salva da dove si vuole, ma si controlla tutto."""
        from unittest import mock

        from PySide6.QtCore import Qt

        from mirto.gui.settings import ActionWizard

        w = ActionWizard(None, "n1", self.CAMERE)
        w.cams.item(0).setCheckState(Qt.CheckState.Checked)
        w.name.setText("Senza pezzi")
        w.catena.set_passi([])
        # L'avviso e' una finestra modale: in una prova senza nessuno davanti resterebbe li' per
        # sempre, e la suite morirebbe di attesa invece che di errore.
        with mock.patch("mirto.gui.settings.QMessageBox.warning"):
            w._accept()
        self.assertIsNone(w.result_action, "senza pezzi non si salva")
        self.assertEqual(w.pile.currentIndex(), 2, "e ti porta sulla schermata che manca")

    def test_saving_an_action_never_opens_a_window_by_itself(self):
        """La prova che e' nata da una suite piantata: «_accept» non deve aprire niente.

        Avevo messo l'avviso sulle normative dentro il salvataggio. Una finestra modale li' dentro
        vuol dire che chiunque salvi un'azione senza qualcuno davanti — una prova automatica, per
        esempio — resta ad aspettare un clic che non arriva mai. La suite di Marco si e' piantata
        cosi', e la correzione non e' stata rattoppare le prove: l'avviso lo da' chi apre la
        finestra, non la finestra.
        """
        from unittest import mock

        from PySide6.QtCore import Qt

        from mirto.actions.pannello_catena import Passo
        from mirto.gui.settings import ActionWizard

        w = ActionWizard(None, "n1", self.CAMERE)
        w.set_node_commands(self.COMANDI_NODO)
        w.cams.item(0).setCheckState(Qt.CheckState.Checked)
        w.name.setText("Recinto")
        w.catena.set_passi([Passo(comando="dissuasore", durata=5)])
        with mock.patch("mirto.gui.settings.QMessageBox.information") as detto, \
                mock.patch("mirto.gui.settings.QMessageBox.warning") as avvisato:
            w._accept()
        self.assertIsNotNone(w.result_action)
        self.assertFalse(detto.called or avvisato.called,
                         "salvare non apre finestre: nessuno potrebbe chiuderle")

    def test_what_gets_flagged_is_decided_without_opening_anything(self):
        """Il simbolino si calcola da una funzione, non da una finestra: cosi' si prova."""
        from mirto.actions.avvisi import CATEGORIE_NOSTRE, avvisi, suoni_di

        tipi = {"dissuasore": "deterrent", "luci": "light"}
        recinto = {"targets": ["person"], "action": "dissuasore", "duration": 5}
        self.assertTrue(avvisi(recinto["targets"], suoni_di(recinto, tipi), CATEGORIE_NOSTRE))
        gatti = {"targets": ["cat"], "action": "dissuasore", "sounds": ["falco"], "duration": 5}
        self.assertEqual(avvisi(gatti["targets"], suoni_di(gatti, tipi), CATEGORIE_NOSTRE), [])

    def test_a_sound_is_asked_in_times_not_in_seconds(self):
        """Il nodo ripete il suono finche' il tempo scade: «per 8 secondi» erano tre spari."""
        from mirto.actions.pannello_catena import Passo
        from mirto.gui.catena_ui import PassoDialog

        comandi = [{"nome": "dissuasore", "tipo": "deterrent", "suoni": ["sparo"],
                    "durate": {"sparo": 2.0}}]
        dlg = PassoDialog(Passo(comando="dissuasore", suono="sparo", durata=6.0), comandi)
        self.assertTrue(dlg._a_volte, "il nodo dichiara la durata: si chiede quante volte")
        self.assertEqual(dlg.volte.value(), 3, "sei secondi di uno sparo da due sono tre spari")
        self.assertTrue(dlg.quanto_dura.text(), "e dice subito quanti secondi fa")
        dlg.volte.setValue(1)
        dlg._salva()
        # Il «meno di un giro intero» era il trucco di prima, e non funzionava: adesso a dire quante
        # volte si sente e' «volte», che il nodo esegue contando. I secondi sono contabilita'.
        self.assertEqual(dlg.risultato.volte, 1, "uno sparo solo: una ripetizione, non 1,9 secondi")
        self.assertAlmostEqual(dlg.risultato.durata, 2.2, places=2)

    def test_an_old_node_still_asks_for_seconds(self):
        """Un nodo che non dichiara le durate non sa rispondere a «quante volte»: niente bugie."""
        from mirto.actions.pannello_catena import Passo
        from mirto.gui.catena_ui import PassoDialog

        dlg = PassoDialog(Passo(comando="dissuasore", suono="falco", durata=8.0), self.COMANDI_NODO)
        self.assertFalse(dlg._a_volte)
        dlg._salva()
        self.assertEqual(dlg.risultato.durata, 8.0)

    def test_a_sound_box_can_be_heard_on_its_own(self):
        """Cercando un suono fra cinque, sentire ogni volta anche la luce e l'attesa e' un modo per
        non provarlo (23/10)."""
        from mirto.actions.pannello_catena import Passo
        from mirto.gui.settings import ActionDialog

        dlg = ActionDialog(self._azione(), "n1", self.CAMERE)
        dlg.set_node_commands(self.COMANDI_NODO)
        dlg.set_stato_nodo(casse=["jack"], in_linea=True)
        dlg.catena.set_passi([Passo(comando="luci", durata=10),
                              Passo(comando="dissuasore", suono="falco", durata=3)])
        from PySide6.QtWidgets import QToolButton

        visti = []
        dlg.catena.prova_un_pezzo.connect(visti.append)
        riquadri = [w for w in dlg.catena._dentro.children() if type(w).__name__ == "_Riquadro"]
        self.assertEqual(len(riquadri), 2)
        from mirto.i18n import _

        def senti(box):
            return [b for b in box.findChildren(QToolButton)
                    if b.toolTip() == _("Senti solo questo pezzo")]
        self.assertEqual(len(senti(riquadri[1])), 1, "il pezzo che suona si puo' sentire da solo")
        self.assertEqual(senti(riquadri[0]), [], "una luce non si «sente»")
        # Spostare e sentire erano lo stesso triangolo: adesso sono icone diverse, e la prova lo dice.
        icone = [b.icon().cacheKey() for b in riquadri[1].findChildren(QToolButton) if not b.icon().isNull()]
        self.assertEqual(len(icone), len(set(icone)), "due pulsanti con la stessa icona")

    def test_the_big_button_says_what_it_tests(self):
        from mirto.gui.settings import ActionDialog

        dlg = ActionDialog(self._azione(), "n1", self.CAMERE)
        self.assertIn("catena", dlg.catena.prova.text().lower(),
                      "«Prova» da solo non diceva che prova tutto il giro")

    def test_the_piece_dialog_only_offers_the_sounds_of_its_command(self):
        from mirto.actions.pannello_catena import Passo
        from mirto.gui.catena_ui import PassoDialog

        dlg = PassoDialog(Passo(comando="dissuasore", suono="falco"), self.COMANDI_NODO)
        self.assertEqual(dlg.quale.currentData(), "suono:dissuasore:falco",
                         "per un suono si sceglie il suono, non il dispositivo")
        dlg.quale.setCurrentIndex(dlg.quale.findData("suono:sirena:"))
        dlg._salva()
        self.assertEqual((dlg.risultato.comando, dlg.risultato.suono), ("sirena", ""),
                         "e noi sappiamo da quale comando esce")
        luce = PassoDialog(Passo(comando="luci"), self.COMANDI_NODO)
        self.assertFalse(luce.suono.isEnabled(), "una luce non ha suoni: niente tendina vuota")


class ComandiDelLettoreTest(unittest.TestCase):
    """I comandi del lettore non sono caratteri emoji.

    «⏮ ⏭ ⏸» per Unicode sono emoji: Windows li disegna con il font a colori, e nella barra del
    lettore uscivano colorati in mezzo agli altri comandi bianchi (visto sul PC il 19/10). «▶»
    invece no — è un carattere di testo — ed è il motivo per cui solo quei due si notavano.
    Icone disegnate: stesso aspetto su ogni sistema, nessun font di mezzo.
    """

    EMOJI = {"\u23ee": "⏮", "\u23ed": "⏭", "\u23f8": "⏸", "\u23ef": "⏯", "\u23f9": "⏹"}
    ICONE = ("ui-play", "ui-pause", "ui-skip-back", "ui-skip-forward")

    def test_no_emoji_character_ends_up_on_a_button(self):
        codice = (Path(__file__).resolve().parents[1] / "mirto/gui/review.py").read_text(encoding="utf-8")
        for riga in codice.splitlines():
            if "setText(" not in riga and "_big(" not in riga:
                continue
            for carattere, nome in self.EMOJI.items():
                self.assertNotIn(carattere, riga,
                                 f"«{nome}» è un emoji: su Windows esce colorato ({riga.strip()})")

    def test_the_player_icons_exist_and_can_be_recoloured(self):
        icone = Path(__file__).resolve().parents[1] / "mirto/gui/icons"
        for nome in self.ICONE:
            percorso = icone / f"{nome}.svg"
            self.assertTrue(percorso.exists(), f"manca l'icona {nome}.svg")
            self.assertIn("currentColor", percorso.read_text(encoding="utf-8"),
                          f"{nome}.svg deve potersi ricolorare dal tema")



class NomeDellaCameraDelNodoTest(unittest.TestCase):
    """Il nome di fabbrica di una camera di nodo segue il nodo; quello scelto da una persona vince.

    Chi ha due camere su un nodo vuole chiamarle «Porta» e «Giardino», non «Ingresso – cam» e
    «Ingresso – cam2». Il nome scelto sta nelle preferenze della camera, non nella riga che arriva dal
    nodo: così la rinomina del nodo non lo cancella, e svuotando il campo si torna al nome del nodo.
    """

    class FintaConfig:
        def __init__(self, dati=None):
            self.dati = dict(dati or {})
            self.salvata = 0

        def get(self, chiave, difetto=None):
            return self.dati.get(chiave, difetto)

        def set(self, chiave, valore):
            self.dati[chiave] = valore

        def save(self):
            self.salvata += 1

    def gestore(self, cfg):
        """Il pezzo di CameraManager che serve qui: rename() non tocca altro."""
        import threading
        import types

        from mirto.cameras.manager import CameraManager

        finto = types.SimpleNamespace(config=cfg, _lock=threading.Lock(), sources={},
                                      watcher=types.SimpleNamespace(known={}),
                                      _remember=lambda *_a: None)
        return lambda cam_id, nome: CameraManager.rename(finto, cam_id, nome)

    def test_a_chosen_name_lives_apart_from_the_one_the_node_gives(self):
        cfg = self.FintaConfig({"cameras.network": [{"id": "studio", "name": "Ingresso", "node": "n1",
                                                     "node_cam": "cam"}]})
        rinomina = self.gestore(cfg)
        rinomina("net:studio", "Porta")
        self.assertEqual(cfg.get("cameras.overrides")["net:studio"]["name"], "Porta")
        self.assertEqual(cfg.get("cameras.network")[0]["name"], "Ingresso",
                         "il nome che arriva dal nodo resta: quello scelto è una preferenza sopra")
        self.assertGreaterEqual(cfg.salvata, 1)

    def test_an_empty_name_goes_back_to_the_node_name(self):
        cfg = self.FintaConfig({"cameras.network": [{"id": "studio", "name": "Ingresso", "node": "n1"}],
                                "cameras.overrides": {"net:studio": {"name": "Porta"}}})
        self.gestore(cfg)("net:studio", "   ")
        self.assertNotIn("name", cfg.get("cameras.overrides")["net:studio"])

    def test_renaming_the_node_does_not_touch_a_chosen_name(self):
        """Le due cose insieme: il nodo cambia nome, la camera che ha un nome suo non si muove."""
        from mirto.actions.nodes import segui_il_nome

        cfg = self.FintaConfig({"cameras.network": [{"id": "studio", "name": "Studio", "node": "n1"}],
                                "cameras.overrides": {"net:studio": {"name": "Porta"}}})
        seguito = segui_il_nome(cfg, "n1", "Studio", "Ingresso")
        self.assertEqual(seguito["camere"], ["Ingresso"], "il nome di fabbrica segue il nodo")
        self.assertEqual(cfg.get("cameras.overrides")["net:studio"]["name"], "Porta",
                         "ma quello che si vede resta «Porta», perché l'ha scelto una persona")


class LAvvisoDelBuioSiDiceUnaVoltaMaNonPerSempreTest(unittest.TestCase):
    """«Immagine troppo scura» si dice una volta per camera — ma per **questo** testo, non per sempre.

    Marco, 25/10: *«la camera del Raspberry 3B non e' NoIR, quindi non ha visione notturna»*. La
    frase tornava ogni notte su una camera che non puo' risolverla, e un avviso che si ripete insegna
    a ignorare gli avvisi. Da li' il «una volta sola».

    Poi pero' quella frase diceva di procurarsi «un modulo NoIR con illuminatore»: a chi la NoIR ce
    l'ha gia', di ricomprare quello che ha; a chi ha una webcam USB, una cosa che per lei non esiste.
    Detta una volta per sempre sarebbe rimasta sbagliata per sempre. Il numero accanto al nome della
    camera e' la via d'uscita, e costa un avviso in piu' a chi aveva sentito quello sbagliato.
    """

    class Finto:
        def __init__(self, dati=None):
            self.dati = dict(dati or {})
            self.detti = []
            self.salvata = 0

        # fa da MirtoCore e da Config insieme: a `_buio_una_volta` servono solo questi
        config = property(lambda self: self)
        get = lambda self, chiave, difetto=None: self.dati.get(chiave, difetto)   # noqa: E731
        set = lambda self, chiave, valore: self.dati.__setitem__(chiave, valore)  # noqa: E731
        camera_name = lambda self, cam_id: "Ingresso"                             # noqa: E731

        def save(self):
            self.salvata += 1

        def _message(self, testo):
            self.detti.append(testo)

    def buio(self, finto, cam_id="cam1"):
        from mirto.core import MirtoCore

        MirtoCore._buio_una_volta(finto, cam_id)

    def test_it_is_said_once_and_then_never_again(self):
        finto = self.Finto()
        self.buio(finto)
        self.buio(finto)
        self.buio(finto)
        self.assertEqual(len(finto.detti), 1, "ripeterlo insegna a ignorare gli avvisi")
        self.assertGreaterEqual(finto.salvata, 1, "e deve sopravvivere alla chiusura del programma")

    def test_another_camera_is_another_story(self):
        finto = self.Finto()
        self.buio(finto, "cam1")
        self.buio(finto, "cam2")
        self.assertEqual(len(finto.detti), 2)

    def test_it_never_tells_a_noir_owner_to_buy_a_noir(self):
        """Il rimedio dipende dalla camera, e il messaggio non sa quale sia: allora li dice tutti e due."""
        finto = self.Finto()
        self.buio(finto)
        testo = finto.detti[0]
        self.assertIn("faretti a infrarossi", testo, "e' questo che serve a una NoIR, non un'altra NoIR")
        self.assertNotIn("modulo NoIR", testo)
        self.assertIn("una luce dove guarda", testo, "e questo a tutte le altre, webcam USB comprese")

    def test_a_camera_that_heard_the_wrong_one_hears_the_right_one_once(self):
        """Chi aveva sentito la versione sbagliata la risente giusta — una volta, e poi basta."""
        finto = self.Finto({"cameras.dark_told": ["cam1"]})      # com'era segnato fino all'8/11
        self.buio(finto)
        self.assertEqual(len(finto.detti), 1, "la versione vecchia non vale per quella nuova")
        self.buio(finto)
        self.assertEqual(len(finto.detti), 1, "ma la nuova vale, e non si ricomincia da capo")
        segnate = finto.get("cameras.dark_told")
        self.assertEqual(len(segnate), 1,
                         f"l'elenco non deve crescere a ogni ritocco del testo: {segnate}")


class ProveCheNonSiPiantanoTest(unittest.TestCase):
    """Nessuna prova deve poter aprire una finestra modale: resterebbe li' per sempre.

    Nata il 23/10, dopo che la suite di Marco si e' piantata: avevo messo l'avviso sulle normative
    dentro «_accept», e una prova che salva un'azione si e' messa ad aspettare un clic che non
    sarebbe mai arrivato. Una suite che si pianta e' peggio di una suite rossa — una rossa dice cosa
    non va, una piantata fa solo perdere tempo e sembra colpa del computer.
    """

    PERICOLOSI = {"_accept", "_avvisa_una_volta", "_test_azione", "_delete_action", "_info"}

    def test_no_test_can_hang_on_a_modal_window(self):
        import ast
        from pathlib import Path

        sorgente = Path(__file__).read_text(encoding="utf-8")
        righe = sorgente.splitlines()
        guai = []
        for n in ast.walk(ast.parse(sorgente)):
            if not (isinstance(n, ast.FunctionDef) and n.name.startswith("test_")):
                continue
            corpo = "\n".join(righe[n.lineno - 1:n.end_lineno])
            chiamate = {c.func.attr for c in ast.walk(n)
                        if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)}
            if (chiamate & self.PERICOLOSI) and "mock.patch" not in corpo:
                guai.append(f"{n.name} chiama {sorted(chiamate & self.PERICOLOSI)}")
        self.assertEqual(guai, [], "queste prove possono aprire una finestra e restare appese")


class UnAnteprimaNonCopiaQuelloCheButtaTest(unittest.TestCase):
    """Q-07: quanto ridurre un fotogramma prima di convertirlo.

    L'aritmetica sta fuori da Qt (``mirto/anteprime.py``) e si prova qui, dove PySide6 non c'e'.
    Le due cose che si possono sbagliare in un verso che non si vede subito: ridurre quando il
    riquadro e' piu' grande del fotogramma (l'immagine diventa morbida proprio sulla camera che
    qualcuno sta guardando a schermo pieno) e cambiare le proporzioni.
    """

    def test_a_tiny_thumbnail_does_not_get_two_million_pixels(self):
        from mirto.anteprime import misura_anteprima

        misura = misura_anteprima(1920, 1080, 176, 99)
        self.assertIsNotNone(misura)
        larghezza, altezza = misura
        self.assertLess(larghezza * altezza, 1920 * 1080 // 20, "il risparmio e' il motivo di Q-07")
        self.assertGreaterEqual(larghezza, 176, "ma non meno di quello che si disegna")

    def test_the_proportions_are_kept(self):
        from mirto.anteprime import misura_anteprima

        larghezza, altezza = misura_anteprima(1920, 1080, 176, 99)
        self.assertAlmostEqual(larghezza / altezza, 1920 / 1080, places=1)

    def test_a_frame_smaller_than_the_box_is_left_alone(self):
        """Ridurre e poi ingrandire si vede: meglio convertire tutto che perdere."""
        from mirto.anteprime import misura_anteprima

        self.assertIsNone(misura_anteprima(640, 360, 1920, 1080))
        self.assertIsNone(misura_anteprima(640, 360, 640, 360))

    def test_the_box_is_a_box_to_fit_into_not_to_fill(self):
        """Un riquadro larghissimo e basso: comanda il lato che sta piu' stretto, come il disegno."""
        from mirto.anteprime import misura_anteprima

        larghezza, altezza = misura_anteprima(1920, 1080, 1900, 200)
        self.assertLessEqual(altezza, 260, "l'altezza decide: 200 di riquadro, piu' il margine")

    def test_a_growing_tile_keeps_a_floor(self):
        """La piastrella cresce fra due fotogrammi, e la camera potrebbe averne finiti."""
        from mirto.anteprime import misura_anteprima

        larghezza, _a = misura_anteprima(1920, 1080, 160, 90, minimo=320)
        self.assertEqual(larghezza, 320)

    def test_the_floor_never_makes_it_bigger_than_it_was(self):
        from mirto.anteprime import misura_anteprima

        self.assertIsNone(misura_anteprima(240, 135, 160, 90, minimo=320))

    def test_nothing_rounds_down_to_an_empty_image(self):
        from mirto.anteprime import misura_anteprima

        for argomenti in [(1920, 1080, 1, 1), (1920, 1080, 0, 0), (0, 0, 176, 99),
                          (1920, 1080, -4, 99)]:
            self.assertIsNone(misura_anteprima(*argomenti), argomenti)


class IlColoreDellAnteprimaNonSiScambiaTest(unittest.TestCase):
    """Q-07 ha tolto la conversione BGR→RGB: ora la legge Qt (``Format_BGR888``).

    E' un cambio che, se sbagliato, non da' nessun errore: scambia il rosso con il blu in **ogni**
    anteprima del programma, e si scopre guardando una maglietta. Qui si converte un fotogramma di un
    colore noto e si rilegge il pixel, cosi' la CI lo vede al posto mio -- PySide6 non si installa
    nell'ambiente dove lavoro.
    """

    @classmethod
    def setUpClass(cls):
        try:
            from PySide6.QtWidgets import QApplication
        except ImportError:
            raise unittest.SkipTest("PySide6 non installato")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication([])

    def _fotogramma(self, blu, verde, rosso, larghezza=320, altezza=180):
        import numpy as np

        frame = np.zeros((altezza, larghezza, 3), dtype=np.uint8)
        frame[:, :] = (blu, verde, rosso)      # OpenCV tiene i canali in questo ordine
        return frame

    def test_a_red_frame_comes_out_red(self):
        from mirto.gui.widgets import frame_to_qimage

        img = frame_to_qimage(self._fotogramma(0, 0, 255), (320, 180))
        colore = img.pixelColor(10, 10)
        self.assertEqual((colore.red(), colore.green(), colore.blue()), (255, 0, 0),
                         "rosso e blu scambiati: il formato dei canali non corrisponde a OpenCV")

    def test_a_blue_frame_comes_out_blue(self):
        from mirto.gui.widgets import frame_to_qimage

        img = frame_to_qimage(self._fotogramma(255, 0, 0), (320, 180))
        colore = img.pixelColor(10, 10)
        self.assertEqual((colore.red(), colore.green(), colore.blue()), (0, 0, 255))

    def test_the_box_really_makes_a_smaller_image(self):
        """Il risparmio deve arrivare fino all'immagine, non fermarsi al conto."""
        from mirto.gui.widgets import frame_to_qimage

        frame = self._fotogramma(0, 128, 0, 1920, 1080)
        piccola = frame_to_qimage(frame, (176, 99))
        self.assertLess(piccola.width(), 400)
        self.assertEqual(frame_to_qimage(frame).width(), 1920, "senza riquadro resta com'era")

    def test_a_non_contiguous_view_does_not_blow_up(self):
        """Una vista numpy a passi negativi: ``frame.data`` non esiste, e la conversione esplodeva.

        Qui conta che non si pianti e che legga i byte nell'ordine in cui stanno -- la vista di un
        fotogramma rosso con i canali rovesciati e' blu, perche' rovesciati sono -- non che indovini
        quale ordine aveva in mente chi l'ha passata.
        """
        from mirto.gui.widgets import frame_to_qimage

        vista = self._fotogramma(0, 0, 255)[:, :, ::-1]
        colore = frame_to_qimage(vista, (320, 180)).pixelColor(10, 10)
        self.assertEqual((colore.red(), colore.green(), colore.blue()), (0, 0, 255))
