# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
import json
import threading
import time
import os
import tempfile
import unittest
from email import message_from_bytes, policy
from pathlib import Path

import numpy as np

from mirto.config import Config
from mirto.credentials import EMAIL_PASSWORD, SMS_HTTP_API_KEY, SMS_TWILIO_TOKEN, TELEGRAM_TOKEN, SecretStore
from mirto.events import Event, EventStore
from mirto.notify.email import EmailConfigError, EmailNotifier
from mirto.notify.httputil import Response
from mirto.notify.sms import HttpTemplateProvider, SmsNotifier, TwilioProvider
from mirto.notify.telegram import BotHandlers, TelegramBot, TelegramError, message_link


def mem_secrets(**values):
    s = SecretStore()
    s._kr = None
    for k, v in values.items():
        s.set(k, v)
    return s


def sample_event():
    return Event("cam1", "Ingresso", 0.73, np.full((240, 320, 3), 90, np.uint8))


class FakeAPI:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail
        self.next_id = 41

    def call(self, method, params=None, files=None, timeout=None):
        if self.fail:
            raise TelegramError("rete assente")
        self.calls.append((method, params, files))
        if method == "getUpdates":  # il bot in ascolto non riceve nulla nei test
            time.sleep(0.05)
            return []
        self.next_id += 1
        return {"message_id": self.next_id}

    def download_file(self, file_id):
        return b"OGG"


class RecordingHandlers(BotHandlers):
    def __init__(self):
        self.log = []

    def stop_all(self):
        self.log.append("stop"); return "Tutto fermo"

    def arm(self, armed):
        self.log.append(("arm", armed)); return "ok"

    def false_alarm(self, event_id):
        self.log.append(("fa", event_id)); return "Segnato come falso allarme"

    def play_voice(self, audio, mime):
        self.log.append(("voice", audio, mime)); return "Riprodotto"


class TelegramTest(unittest.TestCase):
    def test_link(self):
        self.assertEqual(message_link("-1001234567", 9), "https://t.me/c/1234567/9")
        self.assertIsNone(message_link("-4567", 9))

    def test_send_event_with_buttons(self):
        api = FakeAPI()
        bot = TelegramBot(api, "-1009999", [1], BotHandlers())
        link = bot.send_event(sample_event(), [("Attiva Uscita 1", "ok:abc")])
        self.assertEqual(link, "https://t.me/c/9999/42")
        method, params, files = api.calls[0]
        self.assertEqual(method, "sendPhoto")
        self.assertTrue(files["photo"][1].startswith(b"\xff\xd8"))  # JPEG
        flat = [b["callback_data"] for row in params["reply_markup"]["inline_keyboard"] for b in row]
        self.assertIn("ok:abc", flat)
        self.assertIn("st", flat)

    def test_authorization_and_commands(self):
        api, h = FakeAPI(), RecordingHandlers()
        bot = TelegramBot(api, "-100", [111], h)
        bot.handle_update({"message": {"chat": {"id": 5}, "from": {"id": 999}, "text": "/ferma"}})
        self.assertEqual(h.log, [], "utente non autorizzato: nessuna azione")
        bot.handle_update({"message": {"chat": {"id": 5}, "from": {"id": 111}, "text": "/ferma"}})
        bot.handle_update({"message": {"chat": {"id": 5}, "from": {"id": 111}, "text": "/inserisci@MirtoBot"}})
        bot.handle_update({"message": {"chat": {"id": 5}, "from": {"id": 111},
                                       "voice": {"file_id": "f", "mime_type": "audio/ogg"}}})
        self.assertEqual(h.log, ["stop", ("arm", True), ("voice", b"OGG", "audio/ogg")])

    def test_callbacks(self):
        api, h = FakeAPI(), RecordingHandlers()
        bot = TelegramBot(api, "-100", [111], h)
        bot.handle_update({"callback_query": {"id": "q1", "from": {"id": 222}, "data": "st"}})
        self.assertEqual(h.log, [])
        bot.handle_update({"callback_query": {"id": "q2", "from": {"id": 111, "first_name": "Marco"},
                                              "data": "fa:ev1", "message": {"chat": {"id": -100}}}})
        self.assertEqual(h.log, [("fa", "ev1")])

    def test_old_commands_ignored(self):
        import time as _t
        api, h = FakeAPI(), RecordingHandlers()
        bot = TelegramBot(api, "-100", [111], h)
        bot.handle_update({"message": {"chat": {"id": 5}, "from": {"id": 111}, "text": "/ferma",
                                       "date": int(_t.time()) - 3600}})
        self.assertEqual(h.log, [])
        bot.handle_update({"message": {"chat": {"id": 5}, "from": {"id": 111}, "text": "/ferma",
                                       "date": int(_t.time())}})
        self.assertEqual(h.log, ["stop"])

    def test_backlog_skipped_on_start(self):
        class BacklogAPI(FakeAPI):
            def call(self, method, params=None, files=None, timeout=None):
                self.calls.append((method, params, files))
                if method == "getUpdates" and params.get("offset") == -1:
                    return [{"update_id": 500}]
                return []
        api = BacklogAPI()
        bot = TelegramBot(api, "-100", [111], RecordingHandlers())
        self.assertEqual(bot.state, "starting")
        bot._skip_backlog()
        self.assertEqual(bot._offset, 501)
        self.assertEqual(bot.state, "online")

    def test_id_command_for_setup(self):
        api = FakeAPI()
        bot = TelegramBot(api, "-100", [], BotHandlers())
        bot.handle_update({"message": {"chat": {"id": -1005}, "from": {"id": 77}, "text": "/id"}})
        self.assertIn("77", api.calls[0][1]["text"])


class FakeSMTP:
    sent = []

    def __init__(self, notifier):
        pass

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)

    def quit(self):
        pass


class EmailTest(unittest.TestCase):
    def cfg(self, **kw):
        base = {"enabled": True, "provider": "gmail", "username": "utente@gmail.com", "recipients": ["a@x.it", "b@y.it"]}
        base.update(kw)
        return base

    def test_gmail_preset_and_message(self):
        n = EmailNotifier(self.cfg(), mem_secrets(**{EMAIL_PASSWORD: "abcd efgh ijkl mnop"}), smtp_factory=FakeSMTP)
        self.assertEqual((n.host, n.port, n.security), ("smtp.gmail.com", 465, "ssl"))
        FakeSMTP.sent.clear()
        n.send_event(sample_event(), "https://t.me/c/1/2")
        msg = message_from_bytes(FakeSMTP.sent[0].as_bytes(), policy=policy.default)
        self.assertIn("persona rilevata", msg["Subject"])
        types = [p.get_content_type() for p in msg.walk()]
        self.assertIn("text/html", types)
        self.assertGreaterEqual(types.count("image/jpeg"), 2, "foto inline + allegato")
        html = next(p for p in msg.walk() if p.get_content_type() == "text/html").get_content()
        self.assertIn("https://t.me/c/1/2", html)
        self.assertIn("cid:", html)

    def test_private_chat_text(self):
        n = EmailNotifier(self.cfg(), mem_secrets(**{EMAIL_PASSWORD: "x"}))
        text = n.build_event_message(sample_event(), None, telegram_sent=True).get_body(("plain",)).get_content()
        self.assertIn("inviata anche su Telegram", text)
        self.assertNotIn("non era raggiungibile", text)

    def test_checks(self):
        with self.assertRaises(EmailConfigError):
            EmailNotifier(self.cfg(), mem_secrets()).check()  # password mancante
        with self.assertRaises(EmailConfigError):
            EmailNotifier(self.cfg(provider="outlook"), mem_secrets(**{EMAIL_PASSWORD: "x"})).check()


class SmsTest(unittest.TestCase):
    def test_http_template(self):
        seen = {}

        def http(method, url, data=None, headers=None, timeout=30):
            seen.update(method=method, url=url, data=data, headers=headers)
            return Response(200, b'{"status":"OK"}')

        cfg = {"url": "https://sms.example/send?to={numero}", "headers": {"Authorization": "Bearer {api_key}"},
               "body": '{"to":"{numero}","text":"{testo}"}', "success_contains": "OK"}
        HttpTemplateProvider(cfg, mem_secrets(**{SMS_HTTP_API_KEY: "K1"}), http).send("+39333", 'Ciao "casa"')
        self.assertEqual(seen["url"], "https://sms.example/send?to=%2B39333")
        self.assertEqual(json.loads(seen["data"]), {"to": "+39333", "text": 'Ciao "casa"'})
        self.assertEqual(seen["headers"]["Authorization"], "Bearer K1")

    def test_http_requires_https(self):
        with self.assertRaises(Exception):
            HttpTemplateProvider({"url": "http://x"}, mem_secrets(), lambda *a, **k: None).send("1", "t")

    def test_twilio(self):
        seen = {}

        def http(method, url, data=None, headers=None, timeout=30):
            seen.update(url=url, data=data, headers=headers)
            return Response(201, b"{}")

        TwilioProvider({"account_sid": "AC1", "from_number": "+1555"}, mem_secrets(**{SMS_TWILIO_TOKEN: "t"}), http).send("+39333", "x")
        self.assertIn("/Accounts/AC1/Messages.json", seen["url"])
        self.assertIn(b"To=%2B39333", seen["data"])

    def test_daily_limit_and_mode(self):
        sent = []
        prov = type("P", (), {"send": lambda self, n, t: sent.append(n)})()
        with tempfile.TemporaryDirectory() as d:
            n = SmsNotifier({"enabled": True, "mode": "fallback", "daily_limit": 3, "recipients": ["1", "2"]},
                            prov, Path(d) / "c.json")
            self.assertFalse(n.should_send(telegram_ok=True))
            self.assertTrue(n.should_send(telegram_ok=False))
            n.send_all("a"); n.send_all("b")
            self.assertEqual(len(sent), 3)


class DispatcherSetupTest(unittest.TestCase):
    def test_bot_starts_without_chat_id_for_id_command(self):
        from mirto.notify import dispatcher as dmod
        with tempfile.TemporaryDirectory() as d:
            cfg = Config(Path(d) / "c.yaml")
            cfg.set("notifications.telegram", {"enabled": True, "chat_id": "", "allowed_user_ids": []})
            api = FakeAPI()
            disp = dmod.NotificationDispatcher(cfg, mem_secrets(**{TELEGRAM_TOKEN: "T"}), None, BotHandlers(),
                                               telegram_api_factory=lambda t: api)
            disp.setup_bot(); disp.bot.stop()
            self.assertIsNotNone(disp.bot)
            self.assertFalse(disp.send_text_now("prova"), "senza ID del gruppo non invia")
            disp.bot.handle_update({"message": {"chat": {"id": 42}, "from": {"id": 7}, "text": "/id"}})
            last_msg = [c for c in api.calls if c[0] == "sendMessage"][-1]
            self.assertIn("42", last_msg[1]["text"])
            first = disp.bot
            cfg.set("notifications.telegram", {"enabled": True, "chat_id": "-4000000001", "allowed_user_ids": [7]})
            disp.setup_bot()
            self.assertIs(disp.bot, first, "stesso token: il bot non viene ricreato")
            self.assertEqual(disp.bot.chat_id, "-4000000001")
            self.assertEqual(disp.bot.allowed, {7})


class Clock:
    def __init__(self, t: float = 1_800_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


class RateLimitedAPI(FakeAPI):
    """Telegram risponde «troppi messaggi, riprova fra N secondi» le prime volte, poi accetta."""

    def __init__(self, quante: int = 1, retry_after: float = 3):
        super().__init__()
        self.da_rifiutare = quante
        self.retry_after = retry_after

    def call(self, method, params=None, files=None, timeout=None):
        from mirto.notify.telegram import TelegramRateLimited

        if method == "sendPhoto" and self.da_rifiutare > 0:
            self.da_rifiutare -= 1
            raise TelegramRateLimited(self.retry_after)
        return super().call(method, params, files, timeout)


class RateLimitTest(unittest.TestCase):
    """Q-04: un 429 di Telegram non fa perdere l'avviso, e il ritentativo tiene i pulsanti.

    Prima ogni «ok: false» era un errore qualsiasi: l'avviso finiva in coda e tornava un minuto
    dopo **senza** pulsanti, cioè senza «Ferma tutto» proprio mentre qualcuno è in casa.
    """

    def setUp(self):
        from mirto.notify import dispatcher as dmod

        self.dmod = dmod
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        os.environ["MIRTO_DATA_DIR"] = self.tmp.name
        self.cfg = Config(d / "c.yaml")
        self.cfg.set("notifications.telegram", {"enabled": True, "chat_id": "-1007", "allowed_user_ids": [1]})
        self.store = EventStore(d)
        self.addCleanup(self.store.close)
        self.attese: list[float] = []
        self.clock = Clock()

    def apparecchia(self, api):
        disp = self.dmod.NotificationDispatcher(
            self.cfg, mem_secrets(**{TELEGRAM_TOKEN: "T"}), self.store, BotHandlers(),
            telegram_api_factory=lambda token: api, clock=self.clock,
            sleep=self.attese.append)
        disp.setup_bot()
        disp.bot.stop()
        return disp

    def consegna(self, disp):
        ev = self.store.save(sample_event())
        disp._deliver(self.dmod._Pending(ev, first_try=self.clock()))
        return ev

    def test_a_rate_limit_is_waited_out_and_the_alert_keeps_its_buttons(self):
        api = RateLimitedAPI(quante=1, retry_after=3)
        disp = self.apparecchia(api)
        self.consegna(disp)
        self.assertEqual(self.attese, [3], "deve aspettare quanto chiede Telegram, poi riprovare")
        self.assertEqual(disp._pending, [], "l'avviso è partito: non va messo in coda")
        foto = [c for c in api.calls if c[0] == "sendPhoto"]
        self.assertEqual(len(foto), 1)
        tasti = [b["text"] for riga in foto[0][1]["reply_markup"]["inline_keyboard"] for b in riga]
        self.assertIn("⛔ Ferma tutto", tasti, "il ritentativo deve avere ancora i pulsanti")

    def test_a_very_long_wait_is_capped(self):
        disp = self.apparecchia(RateLimitedAPI(quante=1, retry_after=600))
        self.consegna(disp)
        self.assertEqual(self.attese, [30], "mezz'ora di attesa bloccherebbe la coda degli avvisi")

    def test_if_it_keeps_refusing_the_alert_goes_to_the_queue(self):
        disp = self.apparecchia(RateLimitedAPI(quante=5, retry_after=2))
        self.consegna(disp)
        self.assertEqual(self.attese, [2], "un solo ritentativo subito, poi si passa alla coda")
        self.assertEqual(len(disp._pending), 1)
        self.assertEqual(disp._pending[0].channels, {"telegram"})


class TelegramApiRateLimitTest(unittest.TestCase):
    def test_the_api_reads_retry_after(self):
        from mirto.notify import telegram as tg

        class Risposta:
            status = 429
            def json(self):
                return {"ok": False, "description": "Too Many Requests: retry after 7",
                        "parameters": {"retry_after": 7}}

        vera = tg.request
        tg.request = lambda *a, **k: Risposta()
        self.addCleanup(setattr, tg, "request", vera)
        with self.assertRaises(tg.TelegramRateLimited) as caso:
            tg.TelegramAPI("T").call("sendPhoto", {})
        self.assertEqual(caso.exception.retry_after, 7)
        self.assertIsInstance(caso.exception, tg.TelegramError, "resta un errore di Telegram")


class PollingStopTest(unittest.TestCase):
    """Q-04: fermare il bot deve sbloccare il long polling, non lasciarlo appeso 45 secondi."""

    class BloccanteAPI(FakeAPI):
        def __init__(self):
            super().__init__()
            self.sbloccato = threading.Event()

        def call(self, method, params=None, files=None, timeout=None):
            if method == "getUpdates" and (params or {}).get("timeout", 0) > 0:
                self.sbloccato.wait(5)      # il long polling vero resta appeso fino a 30 s
                self.sbloccato.clear()
                return []
            if method == "getUpdates":
                self.sbloccato.set()        # una chiamata con timeout 0 lo sblocca
            return super().call(method, params, files, timeout)

    def test_stop_unblocks_the_long_polling_and_waits_for_the_thread(self):
        api = self.BloccanteAPI()
        bot = TelegramBot(api, "-1007", [1], BotHandlers())
        bot.start()
        thread = bot._thread
        time.sleep(0.2)
        inizio = time.perf_counter()
        bot.stop()
        self.assertLess(time.perf_counter() - inizio, 1.0, "stop() non deve aspettare il timeout")
        self.assertFalse(thread.is_alive(), "il thread del bot deve essere finito quando stop() ritorna")

    def test_the_first_conflict_after_a_restart_is_not_a_warning(self):
        class ConflittoAPI(FakeAPI):
            def call(self, method, params=None, files=None, timeout=None):
                if method == "getUpdates" and (params or {}).get("timeout", 0) > 0:
                    raise TelegramError("Conflict: terminated by other getUpdates request")
                return super().call(method, params, files, timeout)

        bot = TelegramBot(ConflittoAPI(), "-1007", [1], BotHandlers(), sleep=lambda _s: bot._stop.set())
        with self.assertLogs("mirto.notify.telegram", level="INFO") as registro:
            bot._run()
        self.assertFalse([r for r in registro.output if r.startswith("WARNING")],
                         "il bot precedente sta ancora chiudendo: non è un problema dell'utente")

        from mirto.notify.telegram import CONFLICT_GRACE

        bot2 = TelegramBot(ConflittoAPI(), "-1007", [1], BotHandlers(), sleep=lambda _s: bot2._stop.set())
        # un conflitto che dura oltre il periodo di grazia: quello sì va detto all'utente
        orologio = iter([1000.0, 1000.0 + CONFLICT_GRACE + 1])
        bot2.clock = lambda: next(orologio, 1000.0 + CONFLICT_GRACE + 1)
        with self.assertLogs("mirto.notify.telegram", level="INFO") as registro:
            bot2._run()
        self.assertTrue([r for r in registro.output if r.startswith("WARNING")])


class DispatcherTest(unittest.TestCase):
    """Q-01: un canale caduto viene ritentato (anche l'email), con attese crescenti e una sola consegna."""

    def setUp(self):
        from mirto.notify import dispatcher as dmod

        self.dmod = dmod
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        os.environ["MIRTO_DATA_DIR"] = self.tmp.name
        self.cfg = Config(d / "c.yaml")
        self.cfg.set("notifications.telegram", {"enabled": True, "chat_id": "-1007", "allowed_user_ids": [1]})
        self.cfg.set("notifications.email", {"enabled": True, "provider": "gmail", "username": "m@gmail.com",
                                             "recipients": ["a@x.it"]})
        self.store = EventStore(d)
        self.api = FakeAPI(fail=True)
        self.clock = Clock()
        self.emails: list[tuple] = []
        self.mail_failures = 0
        prova = self

        class Mail(EmailNotifier):
            def send_event(self, ev, link, telegram_sent=False):
                if prova.mail_failures > 0:
                    prova.mail_failures -= 1
                    raise OSError("server di posta non raggiungibile")
                prova.emails.append((link, ev.jpeg()))

        self.disp = dmod.NotificationDispatcher(
            self.cfg, mem_secrets(**{TELEGRAM_TOKEN: "T", EMAIL_PASSWORD: "P"}), self.store, BotHandlers(),
            telegram_api_factory=lambda token: self.api, email_factory=Mail, clock=self.clock)
        self.disp.setup_bot()
        self.disp.bot.stop()  # niente long polling nel test

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def deliver(self):
        ev = self.store.save(sample_event())
        self.disp._deliver(self.dmod._Pending(ev, first_try=self.clock()))
        return ev

    def test_telegram_down_then_retry(self):
        ev = self.deliver()
        self.assertEqual([link for link, _foto in self.emails], [None],
                         "email inviata anche senza Telegram, senza link")
        self.assertEqual(len(self.disp._pending), 1)
        self.assertEqual(self.disp._pending[0].channels, {"telegram"})
        self.api.fail = False
        self.disp._retry_pending()
        self.assertEqual(len(self.disp._pending), 1, "il primo tentativo non è prima di un minuto")
        self.clock.t += 61
        self.disp._retry_pending()
        self.assertEqual(self.disp._pending, [])
        self.assertTrue(self.store.get(ev.id)["telegram_link"].startswith("https://t.me/c/7/"))

    def test_email_down_is_retried_with_the_same_photo(self):
        self.api.fail = False
        self.mail_failures = 2
        ev = self.deliver()
        foto = ev.jpeg()
        self.assertEqual(self.emails, [])
        self.assertEqual(self.disp._pending[0].channels, {"email"},
                         "Telegram è partito: si ritenta solo la posta")
        self.assertIn("email", self.disp.last_error)

        self.clock.t += 61
        self.disp._retry_pending()  # secondo fallimento: la prossima attesa sale a 5 minuti
        self.assertEqual(self.emails, [])
        self.assertEqual(self.disp._pending[0].attempts, 2)
        self.clock.t += 61
        self.disp._retry_pending()
        self.assertEqual(self.emails, [], "con 5 minuti di attesa non si insiste ogni minuto")
        self.clock.t += 300
        self.disp._retry_pending()

        self.assertEqual(len(self.emails), 1, "una sola email, non una per tentativo")
        self.assertEqual(self.emails[0][1], foto, "la foto è quella dell'evento, non una nuova")
        self.assertEqual(self.disp._pending, [])
        self.assertNotIn("email", self.disp.last_error)

    def test_both_channels_down_are_retried_one_by_one(self):
        self.mail_failures = 1
        self.deliver()
        self.assertEqual(self.disp._pending[0].channels, {"telegram", "email"})
        self.api.fail = False  # torna Telegram, la posta funziona di nuovo
        self.clock.t += 61
        self.disp._retry_pending()
        self.assertEqual(self.disp._pending, [])
        self.assertEqual(len(self.emails), 1)
        self.assertEqual(self.disp.pending_summary(), (0, []))

    def test_queue_is_capped_and_summarised(self):
        for _i in range(self.dmod.RETRY_MAX_ITEMS + 5):
            self.deliver()
        self.assertEqual(len(self.disp._pending), self.dmod.RETRY_MAX_ITEMS)
        self.assertEqual(self.disp.pending_summary(), (self.dmod.RETRY_MAX_ITEMS, ["telegram"]))

    def test_given_up_after_24_hours(self):
        self.deliver()
        self.clock.t += self.dmod.RETRY_MAX_AGE + 1
        self.disp._retry_pending()
        self.assertEqual(self.disp._pending, [], "dopo 24 ore si smette di ritentare")


if __name__ == "__main__":
    unittest.main()


class TelegramInfoTest(unittest.TestCase):
    def test_refresh_info_group_bot_and_users(self):
        class InfoAPI:
            def call(self, method, params=None, files=None, timeout=None):
                if method == "getMe":
                    return {"username": "MirtoBot", "first_name": "Mirto", "can_read_all_group_messages": True}
                if method == "getChat":
                    return {"id": -5, "type": "group", "title": "Mirto Admin"}
                if method == "getChatMemberCount":
                    return 3
                if method == "getChatMember":
                    uid = params["user_id"]
                    if uid == 1:
                        return {"status": "creator", "user": {"id": 1, "first_name": "Marco", "last_name": "Rossi",
                                                              "username": "marco"}}
                    if uid == 2:
                        return {"status": "left", "user": {"id": 2, "first_name": "Luca"}}
                    raise TelegramError("user not found")
                raise AssertionError(method)

        bot = TelegramBot(InfoAPI(), "-5", [1, 2, 3], BotHandlers())
        info = bot.refresh_info()
        self.assertEqual(info["bot"], "@MirtoBot")
        self.assertEqual(info["chat_title"], "Mirto Admin")
        self.assertEqual(info["chat_type"], "group")
        self.assertEqual(info["members"], 3)
        users = {u["id"]: u for u in info["users"]}
        self.assertEqual(users[1]["name"], "Marco Rossi")
        self.assertTrue(users[1]["in_group"])
        self.assertTrue(users[1]["admin"])
        self.assertFalse(users[2]["in_group"])
        self.assertTrue(users[3]["unknown"])
        bot.stop()
