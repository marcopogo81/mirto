# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
import sys
import tempfile
import types
import unittest
from pathlib import Path

from mirto.cameras.discovery import CameraInfo, DiscoveryWatcher
from mirto.config import Config
from mirto import credentials
from mirto.credentials import SecretStore


class ConfigTest(unittest.TestCase):
    def test_defaults_merge_and_save(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.yaml"
            p.write_text("detection:\n  person_threshold: 0.5\n", encoding="utf-8")
            cfg = Config(p)
            self.assertEqual(cfg.get("detection.person_threshold"), 0.5)
            self.assertEqual(cfg.get("detection.confirm_frames"), 2)  # dal default
            cfg.set("notifications.telegram.chat_id", "-100123")
            cfg.save()
            again = Config(p)
            self.assertEqual(again.get("notifications.telegram.chat_id"), "-100123")
            self.assertIsNone(again.get("non.esiste"))


class IlPortachiaviDelNomeVecchioSiEreditaTest(unittest.TestCase):
    """Cambiando nome, i segreti non devono diventare invisibili (8/11).

    Il portachiavi tiene i segreti sotto il **nome del servizio**. Rinominando il programma quel
    nome e' passato da «MyHouse» a «Mirto», e tutto quello che c'era sotto il nome di prima — token
    di Telegram, password delle camere di rete, **e le chiavi dei nodi** — e' rimasto dov'era,
    invisibile. Marco ha riaperto Mirto e ha trovato «Telegram attivo ma token mancante» e due nodi
    con «chiave condivisa mancante»: nulla era perso, semplicemente nessuno andava piu' a cercarlo
    col nome giusto. Le cartelle questa eredita' ce l'avevano gia' dal 7/11; il portachiavi no,
    perche' e' l'unico posto dove i dati non sono file e non si vedono guardando il disco.
    """

    class FintoPortachiavi:
        def __init__(self, dentro=None):
            self.dentro = dict(dentro or {})
            self.letture = []

        def get_password(self, servizio, nome):
            self.letture.append(servizio)
            return self.dentro.get((servizio, nome))

        def set_password(self, servizio, nome, valore):
            self.dentro[(servizio, nome)] = valore

        def delete_password(self, servizio, nome):
            if (servizio, nome) not in self.dentro:
                raise KeyError(nome)
            del self.dentro[(servizio, nome)]

    def test_a_secret_written_under_the_old_name_is_found_and_moved(self):
        kr = self.FintoPortachiavi({("MyHouse", "telegram_bot_token"): "IL-TOKEN"})
        store = SecretStore(service="Mirto", backend=kr, servizio_di_prima="MyHouse")
        self.assertEqual(store.get("telegram_bot_token"), "IL-TOKEN")
        self.assertEqual(kr.dentro.get(("Mirto", "telegram_bot_token")), "IL-TOKEN",
                         "il segreto va spostato sotto il nome di adesso")
        self.assertNotIn(("MyHouse", "telegram_bot_token"), kr.dentro,
                         "si sposta, non si copia: un segreto in due posti dopo si cambia in uno solo")

    def test_the_node_key_too(self):
        """Quella che ha fatto dire «chiave condivisa mancante» su due nodi abbinati."""
        nome = credentials.node_key_name("ingresso")
        kr = self.FintoPortachiavi({("MyHouse", nome): "CHIAVE"})
        self.assertEqual(SecretStore("Mirto", kr, servizio_di_prima="MyHouse").get(nome), "CHIAVE")

    def test_the_new_name_wins_and_the_old_one_is_not_even_asked(self):
        kr = self.FintoPortachiavi({("Mirto", "email_password"): "NUOVA",
                                    ("MyHouse", "email_password"): "VECCHIA"})
        store = SecretStore("Mirto", kr, servizio_di_prima="MyHouse")
        self.assertEqual(store.get("email_password"), "NUOVA")
        self.assertEqual(kr.letture, ["Mirto"],
                         "su macOS ogni lettura puo' far comparire una richiesta di consenso: se il "
                         "segreto c'e' gia', il nome di prima non si disturba")

    def test_a_secret_nowhere_stays_nowhere(self):
        kr = self.FintoPortachiavi()
        self.assertIsNone(SecretStore("Mirto", kr, servizio_di_prima="MyHouse").get("sms_http_api_key"))

    def test_if_the_move_fails_the_value_comes_back_anyway(self):
        """Meglio funzionare con l'eredita' a meta' che non vedere le proprie chiavi."""
        kr = self.FintoPortachiavi({("MyHouse", "email_password"): "VECCHIA"})
        kr.set_password = lambda *a: (_ for _ in ()).throw(RuntimeError("portachiavi in sola lettura"))
        store = SecretStore("Mirto", kr, servizio_di_prima="MyHouse")
        with self.assertLogs("mirto.credentials", level="ERROR"):
            self.assertEqual(store.get("email_password"), "VECCHIA")

    def test_a_refused_read_is_said_out_loud(self):
        """Un «no» del Portachiavi non puo' sparire in un log a livello debug.

        `keyring` restituisce None quando la voce manca e alza quando la lettura **fallisce** — su
        macOS, tipicamente, perche' e' comparsa la richiesta di consenso e si e' risposto di no.
        Senza avviso uno vede «token mancante» e non ha modo di collegarlo a una finestra chiusa di
        fretta: il segreto resta bloccato sotto il nome di prima per sempre.
        """
        kr = self.FintoPortachiavi()
        kr.get_password = lambda servizio, nome: (_ for _ in ()).throw(RuntimeError("negato"))  \
            if servizio == "MyHouse" else None
        store = SecretStore("Mirto", kr, servizio_di_prima="MyHouse")
        with self.assertLogs("mirto.credentials", level="WARNING") as registro:
            self.assertIsNone(store.get("telegram_bot_token"))
        self.assertIn("telegram_bot_token", "\n".join(registro.output))
        self.assertIn("MyHouse", "\n".join(registro.output))

    def test_deleting_takes_away_the_old_copy_too(self):
        nome = credentials.rtsp_name("ingresso")
        kr = self.FintoPortachiavi({("Mirto", nome): "a:b", ("MyHouse", nome): "a:vecchia"})
        SecretStore("Mirto", kr, servizio_di_prima="MyHouse").delete(nome)
        self.assertEqual(kr.dentro, {}, "togliendo una camera non deve restarne la password di prima")

    def test_the_same_name_twice_is_not_an_inheritance(self):
        kr = self.FintoPortachiavi({("Mirto", "email_password"): "X"})
        store = SecretStore("Mirto", kr, servizio_di_prima="Mirto")
        self.assertIsNone(store.servizio_di_prima, "ereditare da se' stessi non vuol dire niente")
        self.assertEqual(store.get("email_password"), "X")


class LaCartellaDelNomeVecchioSiEreditaTest(unittest.TestCase):
    """Cambiando nome, la configurazione non deve sparire (7/11).

    Il programma si chiamava MyHouse e teneva i suoi dati in una cartella con quel nome.
    Chiamandosi Mirto ne cerca un'altra: senza ereditare, al primo avvio uno ritrova il
    programma come appena installato — niente camere, niente nodi, nessun amministratore —
    con i suoi dati ancora sul disco, dove pero' nessuno li cerca piu'.
    """

    def _finte_cartelle(self, radice: Path):
        """`user_data_dir(nome)` finto: una sottocartella per nome, come sul sistema vero."""
        return lambda nome, appauthor=False: str(radice / nome)

    def test_moves_the_old_folder_once(self):
        from unittest import mock

        from mirto import config as C

        with tempfile.TemporaryDirectory() as d:
            radice = Path(d)
            vecchia = radice / C.NOME_DI_PRIMA
            vecchia.mkdir()
            (vecchia / "config.yaml").write_text("actions:\n  armed: true\n", encoding="utf-8")
            with mock.patch.object(C, "user_data_dir", self._finte_cartelle(radice)), \
                    mock.patch.dict("os.environ", {}, clear=True):
                nuova = C.data_dir()
            self.assertEqual(nuova.name, C.APP_NAME)
            self.assertEqual((nuova / "config.yaml").read_text(encoding="utf-8"),
                             "actions:\n  armed: true\n")
            self.assertFalse(vecchia.exists(),
                             "si sposta, non si copia: due cartelle vive sono una trappola")

    def test_an_inhabited_new_folder_wins(self):
        """Se la cartella nuova e' gia' abitata, la vecchia non si tocca piu'."""
        from unittest import mock

        from mirto import config as C

        with tempfile.TemporaryDirectory() as d:
            radice = Path(d)
            vecchia, nuova = radice / C.NOME_DI_PRIMA, radice / C.APP_NAME
            vecchia.mkdir(); (vecchia / "config.yaml").write_text("vecchia", encoding="utf-8")
            nuova.mkdir(); (nuova / "config.yaml").write_text("nuova", encoding="utf-8")
            with mock.patch.object(C, "user_data_dir", self._finte_cartelle(radice)), \
                    mock.patch.dict("os.environ", {}, clear=True):
                out = C.data_dir()
            self.assertEqual((out / "config.yaml").read_text(encoding="utf-8"), "nuova")
            self.assertTrue(vecchia.exists(), "la vecchia resta dov'e': non e' roba da buttare")

    def test_nothing_to_inherit_is_not_an_error(self):
        from unittest import mock

        from mirto import config as C

        with tempfile.TemporaryDirectory() as d:
            radice = Path(d)
            with mock.patch.object(C, "user_data_dir", self._finte_cartelle(radice)), \
                    mock.patch.dict("os.environ", {}, clear=True):
                out = C.data_dir()
            self.assertTrue(out.is_dir())
            self.assertEqual(out.name, C.APP_NAME)

    def test_the_old_variable_still_works(self):
        """`MYHOUSE_DATA_DIR` sta negli appunti di chi usava il programma: non deve tacere."""
        from unittest import mock

        from mirto import config as C

        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict("os.environ", {"MYHOUSE_DATA_DIR": d}, clear=True):
                self.assertEqual(C.data_dir(), Path(d))
            with mock.patch.dict("os.environ", {"MIRTO_DATA_DIR": d, "MYHOUSE_DATA_DIR": "/no"},
                                 clear=True):
                self.assertEqual(C.data_dir(), Path(d), "il nome nuovo vince su quello vecchio")


class CredentialsTest(unittest.TestCase):
    def test_memory_fallback(self):
        store = SecretStore(backend=None)
        store._kr = None
        store.set("x", "segreto")
        self.assertEqual(store.get("x"), "segreto")
        store.delete("x")
        self.assertIsNone(store.get("x"))


class PortachiaviContatore:
    """Portachiavi finto che conta quante volte viene interrogato."""

    def __init__(self, valori=None):
        self.valori = dict(valori or {})
        self.letture: list[str] = []

    def get_password(self, servizio, nome):
        self.letture.append(nome)
        return self.valori.get(nome)

    def set_password(self, servizio, nome, valore):
        self.valori[nome] = valore

    def delete_password(self, servizio, nome):
        self.valori.pop(nome, None)


class SecretCacheTest(unittest.TestCase):
    """W-05: il portachiavi si interroga una volta sola per segreto.

    Finché l'app non è firmata, macOS chiede il consenso a ogni lettura: con il token
    riletto a ogni evento, una notifica di intrusione si fermava dietro una finestra
    del Portachiavi. Firmata o no, rileggere lo stesso segreto mille volte non serve.
    """

    def test_the_keychain_is_asked_once_per_secret(self):
        kr = PortachiaviContatore({"telegram_bot_token": "T"})
        store = SecretStore(backend=kr)
        for _ in range(5):
            self.assertEqual(store.get("telegram_bot_token"), "T")
        self.assertEqual(kr.letture, ["telegram_bot_token"])

    def test_a_missing_secret_is_not_asked_again(self):
        """Quello che conta e' che la **seconda** volta non si torni al portachiavi.

        Dall'8/11 la prima volta le domande sono due: prima col nome di adesso, poi col nome di
        prima, per ereditare i segreti rimasti sotto «MyHouse». Non e' un ritorno al difetto del
        22/10 (la richiesta di consenso che ricompariva all'infinito): il consenso si chiede per
        **leggere il contenuto** di una voce che esiste, e qui non esiste da nessuna delle due
        parti. Dove invece esiste, quella seconda domanda e' l'eredita' che fa il suo mestiere, e
        avviene una volta sola perche' subito dopo il segreto e' sotto il nome di adesso.

        Quindi non si conta quante domande: si guarda che dopo la prima non ne arrivino altre.
        """
        kr = PortachiaviContatore()
        store = SecretStore(backend=kr)
        self.assertIsNone(store.get("email_password"))
        dopo_la_prima = list(kr.letture)
        self.assertIsNone(store.get("email_password"))
        self.assertEqual(kr.letture, dopo_la_prima,
                         "un segreto che non c'e' non si richiede al portachiavi")

    def test_writing_and_deleting_keep_the_memory_honest(self):
        kr = PortachiaviContatore({"x": "vecchio"})
        store = SecretStore(backend=kr)
        self.assertEqual(store.get("x"), "vecchio")
        store.set("x", "nuovo")
        self.assertEqual(store.get("x"), "nuovo")
        store.delete("x")
        self.assertIsNone(store.get("x"))
        self.assertEqual(kr.letture, ["x"], "dopo una scrittura il valore è già noto")

    def test_forget_asks_the_keychain_again(self):
        kr = PortachiaviContatore({"x": "uno"})
        store = SecretStore(backend=kr)
        self.assertEqual(store.get("x"), "uno")
        kr.valori["x"] = "due"  # cambiato da fuori (Portachiavi, un altro MyHouse)
        store.forget()
        self.assertEqual(store.get("x"), "due")
        self.assertEqual(kr.letture, ["x", "x"])

    def test_a_no_from_the_keychain_is_remembered_for_a_while(self):
        """Un errore non e\' una risposta e si riprova - ma non ogni due secondi, per sempre.

        22/10, prima apertura dell\'app impacchettata: la richiesta di consenso del Portachiavi
        compariva una decina di volte e non smetteva. Il segreto letto si ricordava, il **rifiuto** no,
        e chi rileggeva in un giro corto - il registratore, ogni due secondi - ridomandava all\'infinito.
        Dire di no a una finestra e vedersela tornare due secondi dopo e\' il modo piu\' veloce per far
        chiudere un programma. Il principio resta: fra cinque minuti si ritenta.
        """
        class Rotto(PortachiaviContatore):
            def get_password(self, servizio, nome):
                self.letture.append(nome)
                raise RuntimeError("portachiavi bloccato")

        kr = Rotto({"x": "uno"})
        adesso = [1000.0]
        store = SecretStore(backend=kr, clock=lambda: adesso[0])
        self.assertIsNone(store.get("x"))
        for _ in range(50):                       # il giro del registratore, per un minuto e mezzo
            adesso[0] += 2.0
            self.assertIsNone(store.get("x"))
        self.assertEqual(len(kr.letture), 1, "un no chiesto una volta, non cinquanta")
        adesso[0] += credentials.ATTESA_DOPO_UN_NO
        self.assertIsNone(store.get("x"))
        self.assertEqual(len(kr.letture), 2, "passato il tempo, si riprova: l\'errore non e\' una risposta")

    def test_after_a_no_a_write_makes_it_ask_again(self):
        """Chi ha appena scritto quel segreto il permesso ce l\'ha: il no non vale piu\'."""
        class Rotto(PortachiaviContatore):
            def __init__(self, valori=None):
                super().__init__(valori)
                self.rompi = True

            def get_password(self, servizio, nome):
                self.letture.append(nome)
                if self.rompi:
                    raise RuntimeError("portachiavi bloccato")
                return self.valori.get(nome)

        kr = Rotto()
        store = SecretStore(backend=kr)
        self.assertIsNone(store.get("x"))
        kr.rompi = False
        store.set("x", "nuovo")
        self.assertEqual(store.get("x"), "nuovo")

    def test_forgetting_also_forgets_a_no(self):
        class Rotto(PortachiaviContatore):
            def get_password(self, servizio, nome):
                self.letture.append(nome)
                raise RuntimeError("portachiavi bloccato")

        kr = Rotto()
        store = SecretStore(backend=kr)
        self.assertIsNone(store.get("x"))
        store.forget()
        self.assertIsNone(store.get("x"))
        self.assertEqual(len(kr.letture), 2, "chi chiede di rileggere vuole che si ridomandi")


class WatcherTest(unittest.TestCase):
    def test_added_removed_changed(self):
        seq = [
            [CameraInfo("a", "A", index=0)],
            [CameraInfo("a", "A", index=0), CameraInfo("b", "B", index=1)],
            [CameraInfo("b", "B", index=0)],
            [CameraInfo("b", "B", index=0)],
        ]
        events = []
        w = DiscoveryWatcher(
            on_added=lambda c: events.append(("+", c.id)),
            on_removed=lambda c: events.append(("-", c.id)),
            on_changed=lambda c: events.append(("~", c.id, c.index)),
            discover=lambda: seq.pop(0),
        )
        # "a" sparisce alla terza scansione, ma serve che manchi due volte di fila (isteresi, W-03)
        w.scan(); w.scan(); w.scan()
        self.assertEqual(events, [("+", "a"), ("+", "b"), ("~", "b", 0)])
        w.scan()
        self.assertEqual(events, [("+", "a"), ("+", "b"), ("~", "b", 0), ("-", "a")])


class MacDiscoveryTest(unittest.TestCase):
    def test_unique_id_and_order(self):
        class Dev:
            def __init__(self, uid, name):
                self._uid, self._name = uid, name
            def uniqueID(self): return self._uid
            def localizedName(self): return self._name
            def modelID(self): return "m"
            def isConnected(self): return True

        fake = types.ModuleType("AVFoundation")
        fake.AVMediaTypeVideo, fake.AVMediaTypeMuxed = "vide", "muxx"
        fake.AVCaptureDevice = types.SimpleNamespace(
            devicesWithMediaType_=lambda t: [Dev("FT1", "FaceTime HD Camera")] if t == "vide" else [Dev("MX", "Muxed")]
        )
        sys.modules["AVFoundation"] = fake
        try:
            from mirto.cameras import discovery_macos
            cams = discovery_macos.discover()
        finally:
            del sys.modules["AVFoundation"]
        self.assertEqual([c.id for c in cams], ["mac:FT1", "mac:MX"])
        self.assertEqual([c.index for c in cams], [0, 1])


class WindowsDiscoveryTest(unittest.TestCase):
    def test_path_is_stable_id(self):
        fake = types.ModuleType("cv2_enumerate_cameras")
        fake.enumerate_cameras = lambda backend: [
            types.SimpleNamespace(index=1400, name="USB Cam", path=r"\\?\usb#vid_1", vid=1, pid=2, backend=1400)
        ]
        sys.modules["cv2_enumerate_cameras"] = fake
        try:
            from mirto.cameras import discovery_windows
            cams = discovery_windows.discover()
        finally:
            del sys.modules["cv2_enumerate_cameras"]
        self.assertEqual(cams[0].id, r"win:\\?\usb#vid_1")
        self.assertEqual(cams[0].index, 1400)


if __name__ == "__main__":
    unittest.main()


class RtspCredentialsTest(unittest.TestCase):
    """S-02: utente e password delle camere nel portachiavi, mai nel file di configurazione né nei registri."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.cfg = Config(self.d / "config.yaml")
        self.secrets = SecretStore(backend=None)
        self.secrets._kr = None  # portachiavi finto: resta in memoria

    def tearDown(self):
        self.tmp.cleanup()

    def test_redact_url_on_every_shape(self):
        from mirto.netutil import redact_url

        cases = {
            "rtsp://utente:p@ss@192.168.1.64:554/stream": "rtsp://utente:***@192.168.1.64:554/stream",
            "rtsp://mirto:segreta@casa.local/cam": "rtsp://mirto:***@casa.local/cam",
            "rtsp://192.168.1.64:554/stream": "rtsp://192.168.1.64:554/stream",
            "http://[2001:db8::1]:8080/cam": "http://[2001:db8::1]:8080/cam",
            "": "",
        }
        for url, atteso in cases.items():
            self.assertEqual(redact_url(url), atteso, url)
        self.assertNotIn("segreta", redact_url("rtsp://mirto:segreta@casa.local/cam"))

    def test_old_logs_are_cleaned_of_passwords_at_startup(self):
        """Trovato provando S-02 il 19/10: due password di camera in chiaro in mirto.log.

        Erano righe del 17 e 18 ottobre, scritte **prima** che `redact_url` esistesse (18/10 10:24),
        quindi il codice di oggi non perde più niente. Ma il file resta, ed è proprio quello che un
        utente allega a una segnalazione: una password che non doveva essere scritta va togliata
        anche dai registri già scritti, non solo dai prossimi.
        """
        from mirto.netutil import scrub_logs

        logs = self.d / "logs"
        logs.mkdir()
        sporco = ("2026-10-17 13:46:49 INFO mirto.cameras.manager: Camera Ingresso: indirizzo aggiornato "
                  "rtsp://mirto-ingresso.local:8554/cam → rtsp://mirto:4ab8c848a260a128ac0dc80f@192.168.1.18:8554/cam\n")
        innocue = ("2026-10-16 18:41:32 ERROR mirto.notify.dispatcher: Email non inviata: password non impostata\n"
                   "2026-10-18 12:00:00 INFO mirto.cameras.manager: Camera Ingresso: rtsp://mirto:***@192.168.2.2:8554/cam\n"
                   "2026-10-18 12:00:01 INFO mirto.core: avviso inviato a marco@example.com\n")
        (logs / "mirto.log").write_text(sporco + innocue, encoding="utf-8")
        (logs / "mirto.log.3").write_text(sporco, encoding="utf-8")   # anche i file ruotati

        self.assertEqual(scrub_logs(logs), 2, "una riga per file, e i file ruotati non si dimenticano")
        for nome in ("mirto.log", "mirto.log.3"):
            testo = (logs / nome).read_text(encoding="utf-8")
            self.assertNotIn("4ab8c848a260a128ac0dc80f", testo, "la password non deve restare da nessuna parte")
            self.assertIn("rtsp://mirto:***@192.168.1.18:8554/cam", testo,
                          "il resto della riga resta: serve a capire cosa era successo")
        testo = (logs / "mirto.log").read_text(encoding="utf-8")
        self.assertIn("marco@example.com", testo, "un indirizzo di posta non è una password")
        self.assertIn("password non impostata", testo, "e nemmeno la parola «password» in un errore")
        self.assertEqual(scrub_logs(logs), 0, "ripassare non trova più niente e non riscrive i file")

    def test_a_clean_log_folder_is_left_alone(self):
        from mirto.netutil import scrub_logs

        logs = self.d / "logs"
        logs.mkdir()
        (logs / "mirto.log").write_text("2026-10-19 10:00:00 INFO mirto.core: tutto bene\n", encoding="utf-8")
        prima = (logs / "mirto.log").stat().st_mtime_ns
        self.assertEqual(scrub_logs(logs), 0)
        self.assertEqual((logs / "mirto.log").stat().st_mtime_ns, prima,
                         "senza niente da togliere il file non si riscrive nemmeno")
        self.assertEqual(scrub_logs(self.d / "non-esiste"), 0, "nessun registro: niente da fare, nessun errore")

    def test_the_startup_cleans_the_logs_before_reopening_them(self):
        """Guardia: la ripulitura va fatta prima di riattaccare il registro al file.

        Altrimenti si riscrive un file che il programma tiene già aperto, e il registro successivo
        continua a scrivere dalla posizione di prima: in mezzo resterebbe spazzatura.
        """
        sorgente = (Path(__file__).resolve().parents[1] / "mirto" / "logging_setup.py").read_text(encoding="utf-8")
        self.assertIn("scrub_logs", sorgente)
        self.assertLess(sorgente.index("scrub_logs("), sorgente.index("RotatingFileHandler"),
                        "prima si ripulisce, poi si riapre")

    def test_url_and_credentials_round_trip(self):
        from mirto.netutil import split_credentials, with_credentials

        url, creds = split_credentials("rtsp://utente:p%40ss@192.168.1.64:554/stream")
        self.assertEqual((url, creds), ("rtsp://192.168.1.64:554/stream", "utente:p@ss"))
        self.assertEqual(with_credentials(url, creds), "rtsp://utente:p%40ss@192.168.1.64:554/stream")
        self.assertEqual(with_credentials(url, ""), url)

    def test_migration_takes_credentials_out_of_the_file(self):
        from mirto.credentials import rtsp_name
        from mirto.netutil import move_camera_credentials

        self.cfg.set("cameras.network", [
            {"id": "ingresso", "name": "Ingresso", "url": "rtsp://mirto:p%40ss@192.168.1.64:554/cam"},
            {"id": "giardino", "name": "Giardino", "url": "rtsp://192.168.1.70:554/s1"},
        ])
        self.cfg.save()
        self.assertEqual(move_camera_credentials(self.cfg, self.secrets), 1)

        testo = (self.d / "config.yaml").read_text(encoding="utf-8")
        self.assertNotIn("p%40ss", testo)
        self.assertNotIn("@192.168.1.64", testo, "nessuna credenziale resta nel file")
        self.assertIn("rtsp://192.168.1.64:554/cam", testo)
        self.assertEqual(self.secrets.get(rtsp_name("ingresso")), "mirto:p@ss")
        self.assertIsNone(self.secrets.get(rtsp_name("giardino")))
        self.assertEqual(move_camera_credentials(self.cfg, self.secrets), 0)  # già fatto

    def test_config_file_is_readable_only_by_its_owner(self):
        """La configurazione dice dove sono le camere e com'è fatta la casa: non la legge nessun altro.

        Su Unix sono i permessi ``0600``. Su Windows ``os.chmod`` non tocca le ACL — muove solo il
        flag «sola lettura» — quindi lì la promessa si verifica leggendo le ACL vere: nessun diritto
        a Everyone, Users o Authenticated Users. Restano SYSTEM e gli amministratori, che su Windows
        leggono qualunque file e non si possono escludere: SECURITY.md lo dice.

        Fino al 19/10 su Windows questa prova si **saltava**: la frase di SECURITY.md non la
        controllava nessuno proprio sul sistema in cui era falsa (trovato durante W-01).
        """
        self.cfg.set("general.language", "it")
        self.cfg.save()
        percorso = self.d / "config.yaml"
        if not sys.platform.startswith("win"):
            self.assertEqual(percorso.stat().st_mode & 0o777, 0o600)
            return
        sddl = acl_in_sddl(percorso)
        if not sddl:
            self.skipTest("le ACL non si sono lette (PowerShell non disponibile)")
        for sigla, chi in (("WD", "Everyone"), ("BU", "Utenti"), ("AU", "Utenti autenticati")):
            self.assertNotIn(f";;;{sigla})", sddl, f"la configurazione è leggibile da {chi}: {sddl}")


    def test_manager_opens_with_credentials_but_logs_without(self):
        import logging

        from mirto.cameras.manager import CameraManager
        from mirto.credentials import rtsp_name

        self.cfg.set("cameras.network", [{"id": "ingresso", "name": "Ingresso",
                                          "url": "rtsp://192.168.1.64:554/cam"}])
        self.secrets.set(rtsp_name("ingresso"), "mirto:segretissima")
        aperte = []
        mgr = CameraManager(self.cfg, discover=lambda: [], capture_factory=lambda info: aperte.append(info.url),
                            secrets=self.secrets)
        info = mgr._net_info(self.cfg.get("cameras.network")[0])
        self.assertNotIn("segretissima", info.url, "l'indirizzo in configurazione resta pulito")

        with self.assertLogs("mirto.cameras.manager", level="DEBUG") as registro:
            logging.getLogger("mirto.cameras.manager").debug("apertura di %s", info.url)
            mgr._open(info)
        self.assertEqual(aperte, ["rtsp://mirto:segretissima@192.168.1.64:554/cam"])
        self.assertNotIn("segretissima", "\n".join(registro.output))

    def test_recorder_builds_the_url_with_the_credentials(self):
        from mirto.cameras.discovery import CameraInfo as Info
        from mirto.cameras.source import CameraSource

        src = CameraSource(Info(id="net:ingresso", name="Ingresso", kind="network",
                                url="rtsp://192.168.1.64:554/cam"))
        self.assertEqual(src.stream_url(), "rtsp://192.168.1.64:554/cam")
        src.credentials = lambda: "mirto:segretissima"
        self.assertEqual(src.stream_url(), "rtsp://mirto:segretissima@192.168.1.64:554/cam")
        self.assertNotIn("segretissima", src.info.url)


class ProbingAndHysteresisTest(unittest.TestCase):
    """W-03: la ricerca per indice non tocca le camere aperte e una scansione mancata non le scollega."""

    def test_probing_skips_the_cameras_already_open(self):
        from mirto.cameras import discovery as D

        aperte = []

        class FakeCap:
            def __init__(self, index):
                aperte.append(index)
                self.index = index

            def isOpened(self):
                return self.index in (0, 2)

            def release(self):
                pass

        fake_cv2 = types.SimpleNamespace(VideoCapture=FakeCap)
        # Il cv2 vero va **rimesso**, non tolto: togliendolo, il prossimo «import cv2» ne importa
        # una copia nuova, diversa da quella che i moduli di MyHouse hanno già in mano — e una prova
        # successiva che sostituisce «cv2.imencode» finisce sulla copia sbagliata senza accorgersene.
        vero_cv2 = sys.modules.get("cv2")
        sys.modules["cv2"] = fake_cv2
        try:
            found = D.discover_by_probing(max_index=3, busy={0})
        finally:
            if vero_cv2 is not None:
                sys.modules["cv2"] = vero_cv2
            else:
                sys.modules.pop("cv2", None)
        self.assertNotIn(0, aperte, "la camera in uso non si apre una seconda volta")
        self.assertEqual(aperte, [1, 2])
        self.assertEqual([c.index for c in found], [0, 2], "quella aperta si dà per presente")

    def test_manager_tells_the_scan_which_indices_are_open(self):
        from mirto.cameras.manager import CameraManager

        chiesti = []

        def discover(busy=()):
            chiesti.append(set(busy))
            return []

        mgr = CameraManager(self.cfg_vuota(), discover=discover)
        mgr.sources["mac:1"] = types.SimpleNamespace(
            info=CameraInfo(id="mac:1", name="Integrata", kind="local", index=0))
        mgr.sources["net:x"] = types.SimpleNamespace(
            info=CameraInfo(id="net:x", name="Ingresso", kind="network", url="rtsp://h/cam"))
        self.assertEqual(mgr.open_indices(), {0})
        mgr.watcher.scan()
        self.assertEqual(chiesti, [{0}], "la scansione sa quali indici sono in uso")

    def cfg_vuota(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Config(Path(tmp.name) / "config.yaml")

    def test_one_missed_scan_does_not_unplug_a_camera(self):
        from mirto.cameras.discovery import DiscoveryWatcher

        cam = CameraInfo(id="mac:1", name="Integrata", kind="local", index=0)
        elenco = [cam]
        tolte, aggiunte = [], []
        w = DiscoveryWatcher(on_added=aggiunte.append, on_removed=tolte.append, discover=lambda: list(elenco))
        w.scan()
        self.assertEqual([c.id for c in aggiunte], ["mac:1"])

        elenco.clear()
        w.scan()
        self.assertEqual(tolte, [], "una scansione a vuoto non ferma la camera")
        self.assertIn("mac:1", w.known)
        w.scan()
        self.assertEqual([c.id for c in tolte], ["mac:1"], "due di fila sì")
        self.assertNotIn("mac:1", w.known)

        elenco.append(cam)  # tornata: niente strascichi del conteggio
        w.scan()
        self.assertEqual([c.id for c in aggiunte], ["mac:1", "mac:1"])
        elenco.clear()
        w.scan()
        self.assertEqual(len(tolte), 1)

    def test_scan_runs_in_its_own_thread_outside_macos(self):
        import threading

        from mirto.cameras.manager import CameraManager, default_threaded_discovery

        self.assertFalse(default_threaded_discovery("darwin"), "su macOS resta sul thread principale")
        self.assertTrue(default_threaded_discovery("win32"))
        self.assertTrue(default_threaded_discovery("linux"))

        visto = []
        pronto = threading.Event()

        def discover(busy=()):
            visto.append(threading.current_thread().name)
            pronto.set()
            return []

        cfg = self.cfg_vuota()
        cfg.set("cameras.network", [])
        mgr = CameraManager(cfg, discover=discover)
        mgr.start(threaded_discovery=True)
        try:
            self.assertTrue(pronto.wait(5), "la scansione non è partita")
        finally:
            mgr.stop()
        self.assertNotIn("MainThread", visto, "la scansione non deve girare sul thread della finestra")


def acl_in_sddl(percorso: Path) -> str:
    """Le ACL del file in forma SDDL, dove i gruppi sono sigle fisse invece di nomi tradotti.

    ``icacls`` stampa i nomi nella lingua di Windows («Utenti» su un sistema italiano): un test non
    può cercarli. L'SDDL invece è sempre uguale — Everyone è ``WD`` in ogni lingua — e ``Get-Acl``
    lo restituisce già pronto.
    """
    import subprocess

    try:
        res = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                              f"(Get-Acl -LiteralPath '{percorso}').Sddl"],
                             capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return ""
    return res.stdout.strip() if res.returncode == 0 else ""


class LElencoNonSiRiordinaSottoIlDitoTest(unittest.TestCase):
    """Marco, 25/10: *«se attivo la cam dell'iPhone si attiva quella del MacBook e viceversa»*.

    Non si attivava l'altra: l'elenco di «Tutte le cam» si **riordinava** nel momento del clic. Le
    camere accese stavano in cima, quindi accenderne una la faceva saltare sopra l'altra, le schede
    si ricostruivano nell'ordine nuovo e sotto il dito finiva un'altra camera — con il suo
    interruttore, che a quel punto sembrava essersi mosso da solo.

    La regola che ne esce, e che vale per qualunque elenco in cui si agisce: **l'ordine non dipende
    da quello che l'utente sta facendo adesso.** Puo' dipendere da una camera che si collega o si
    scollega — succede da se', non mentre premi — ma non da un interruttore che si preme li' dentro.
    """

    def gestore(self, camere):
        import threading
        import types as _t

        from mirto.cameras.manager import CameraManager

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = Config(Path(tmp.name) / "c.yaml")
        mgr = CameraManager(cfg, discover=lambda busy=(): [])
        mgr.watcher = _t.SimpleNamespace(known={c.id: c for c in camere},
                                         start=lambda *a, **k: None, stop=lambda *a, **k: None)
        mgr.reload = lambda: None
        mgr._lock = threading.RLock()
        return mgr

    def test_turning_one_on_does_not_move_the_rows(self):
        iphone = CameraInfo(id="mac:IPHONE", name="Fotocamera di iPhone 12 di Marco", kind="local", index=0)
        mac = CameraInfo(id="mac:BUILTIN", name="Fotocamera di MacBook Air", kind="local", index=1)
        mgr = self.gestore([iphone, mac])
        ordine = [c["id"] for c in mgr.all_cameras()]
        for cam_id, acceso in (("mac:IPHONE", False), ("mac:BUILTIN", False),
                               ("mac:IPHONE", True), ("mac:BUILTIN", True)):
            with self.subTest(cam=cam_id, acceso=acceso):
                mgr.set_enabled(cam_id, acceso)
                self.assertEqual([c["id"] for c in mgr.all_cameras()], ordine,
                                 "l'elenco si e' mosso sotto il dito di chi ha premuto")

    def test_the_right_camera_is_the_one_that_changes(self):
        """Il dato non e' mai stato sbagliato, ed e' la meta' che non va rotta sistemando l'ordine."""
        iphone = CameraInfo(id="mac:IPHONE", name="iPhone", kind="local", index=0)
        mac = CameraInfo(id="mac:BUILTIN", name="MacBook", kind="local", index=1)
        mgr = self.gestore([iphone, mac])
        mgr.set_enabled("mac:IPHONE", False)
        stato = {c["id"]: c["enabled"] for c in mgr.all_cameras()}
        self.assertEqual(stato, {"mac:IPHONE": False, "mac:BUILTIN": True})

    def test_a_camera_that_unplugs_may_move(self):
        """Quello si': succede da se', non mentre si preme, e in fondo e' dove serve che stia."""
        viva = CameraInfo(id="mac:A", name="Zeta", kind="local", index=0)
        mgr = self.gestore([viva])
        mgr.set_enabled("mac:A", True)
        mgr.watcher.known = {}
        self.assertEqual([c["connected"] for c in mgr.all_cameras()], [False])


class ChiCiSiaDietroUnIndiceLoDiceChiGuardaTest(unittest.TestCase):
    """Il nome lo da' il sistema, la camera la apre OpenCV **per posizione**, e i due ordini differiscono.

    Il 25/10, sul Mac di Marco, la scheda «Fotocamera di iPhone 12» mostrava il MacBook Air. La sonda
    ha escluso le due strade automatiche: nessun segnale di AVFoundation cambia quando apriamo un
    indice, e nessuno dei due elenchi che Apple offre spiega l'ordine di OpenCV. Quell'ordine dipende
    da come quella versione di OpenCV chiede le camere, non si legge da fuori, e indovinarlo vuol dire
    vederlo cambiare al prossimo aggiornamento.

    Quindi lo dice chi sta guardando l'immagine. Una volta, e resta scritto.
    """

    def gestore(self, camere):
        import threading
        import types as _t

        from mirto.cameras.manager import CameraManager

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = Config(Path(tmp.name) / "c.yaml")
        mgr = CameraManager(cfg, discover=lambda busy=(): [])
        mgr.watcher = _t.SimpleNamespace(known={c.id: c for c in camere},
                                         start=lambda *a, **k: None, stop=lambda *a, **k: None)
        mgr.reload = lambda: None
        mgr._lock = threading.RLock()
        return mgr

    def due(self):
        return [CameraInfo(id="mac:MAC", name="MacBook Air", kind="local", index=0),
                CameraInfo(id="mac:TEL", name="iPhone 12", kind="local", index=1)]

    def test_two_cameras_swap_the_index_they_open_with(self):
        mgr = self.gestore(self.due())
        self.assertEqual((mgr.indice_opencv("mac:MAC"), mgr.indice_opencv("mac:TEL")), (0, 1))
        self.assertTrue(mgr.scambia_indici("mac:MAC", "mac:TEL"))
        self.assertEqual((mgr.indice_opencv("mac:MAC"), mgr.indice_opencv("mac:TEL")), (1, 0))

    def test_both_sides_move(self):
        """Se questa mostra l'altra, l'altra mostra questa: sistemarne una sola lascerebbe
        due nomi sullo stesso indice, cioe' una camera aperta due volte e una mai."""
        mgr = self.gestore(self.due())
        mgr.scambia_indici("mac:TEL", "mac:MAC")
        indici = sorted(mgr.indice_opencv(c) for c in ("mac:MAC", "mac:TEL"))
        self.assertEqual(indici, [0, 1], "restano due indici diversi")

    def test_it_is_written_down_and_stays(self):
        mgr = self.gestore(self.due())
        mgr.scambia_indici("mac:MAC", "mac:TEL")
        ov = mgr.config.get("cameras.overrides")
        self.assertEqual(ov["mac:MAC"]["opencv_index"], 1)
        self.assertEqual(ov["mac:TEL"]["opencv_index"], 0)

    def test_swapping_twice_goes_back(self):
        mgr = self.gestore(self.due())
        mgr.scambia_indici("mac:MAC", "mac:TEL")
        mgr.scambia_indici("mac:MAC", "mac:TEL")
        self.assertEqual((mgr.indice_opencv("mac:MAC"), mgr.indice_opencv("mac:TEL")), (0, 1))

    def test_a_network_camera_has_no_index_to_swap(self):
        """Una camera di rete si apre con un indirizzo, non con una posizione: non c'e' niente da scambiare."""
        rete = CameraInfo(id="net:1", name="Cancello", kind="network", url="rtsp://x")
        mgr = self.gestore([self.due()[0], rete])
        self.assertIsNone(mgr.indice_opencv("net:1"))
        self.assertFalse(mgr.scambia_indici("mac:MAC", "net:1"))

    def test_a_camera_cannot_swap_with_itself(self):
        mgr = self.gestore(self.due())
        self.assertFalse(mgr.scambia_indici("mac:MAC", "mac:MAC"))
        self.assertEqual(mgr.indice_opencv("mac:MAC"), 0)


class RiaprireQuandoCambiaLIndiceTest(unittest.TestCase):
    """Scambiare due camere non faceva niente finche' non si riavviava MyHouse.

    `reload()` aggiunge le sorgenti che mancano e toglie quelle che non servono piu', ma una camera
    gia' aperta la lasciava stare: l'indice nuovo restava scritto in configurazione e nessuno lo
    usava. Marco, provando lo scambio: *«non succede nulla che faccia capire che e' avvenuto
    qualcosa»* — ed era letterale, non succedeva niente.

    `reload` vuol dire «riapplica la configurazione», e **da dove si apre una camera e'
    configurazione** quanto l'essere accesa o spenta.
    """

    def gestore(self, camere, aperte):
        import threading
        import types as _t

        from mirto.cameras.manager import CameraManager

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = Config(Path(tmp.name) / "c.yaml")

        def apri(info):
            aperte.append((info.id, info.index))
            return _t.SimpleNamespace(isOpened=lambda: True, read=lambda: (False, None),
                                      release=lambda: None, set=lambda *a: None, get=lambda *a: 0)

        mgr = CameraManager(cfg, discover=lambda busy=(): [], capture_factory=apri)
        mgr.watcher = _t.SimpleNamespace(known={c.id: c for c in camere},
                                         start=lambda *a, **k: None, stop=lambda *a, **k: None)
        mgr._lock = threading.RLock()
        return mgr

    def test_swapping_reopens_both_cameras_with_the_new_index(self):
        camere = [CameraInfo(id="mac:MAC", name="MacBook", kind="local", index=0),
                  CameraInfo(id="mac:TEL", name="iPhone", kind="local", index=1)]
        aperte: list = []
        mgr = self.gestore(camere, aperte)
        mgr.reload()
        self.assertEqual(sorted(aperte), [("mac:MAC", 0), ("mac:TEL", 1)])
        aperte.clear()
        mgr.scambia_indici("mac:MAC", "mac:TEL")
        self.assertEqual(sorted(aperte), [("mac:MAC", 1), ("mac:TEL", 0)],
                         "le due camere si riaprono subito, ognuna con l'indice dell'altra")

    def test_nothing_is_reopened_when_nothing_changed(self):
        """Riaprire una camera per niente vuol dire un buco nel video: si fa solo se serve."""
        camere = [CameraInfo(id="mac:MAC", name="MacBook", kind="local", index=0),
                  CameraInfo(id="mac:TEL", name="iPhone", kind="local", index=1)]
        aperte: list = []
        mgr = self.gestore(camere, aperte)
        mgr.reload()
        aperte.clear()
        mgr.reload()
        self.assertEqual(aperte, [])
