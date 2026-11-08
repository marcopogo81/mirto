# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Smistamento degli avvisi: prima Telegram, poi email (con il link), poi SMS.

Gli invii avvengono in un thread dedicato, così l'analisi video non si blocca.
Se un canale non risponde, gli altri partono lo stesso e quello caduto viene ritentato per 24 ore, con
attese crescenti (1 minuto, 5, 15). Vale sia per Telegram sia per l'email: chi usa solo l'email non deve
perdere un'intrusione per un guasto del server di posta. L'SMS no: costa e ha un tetto giornaliero.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from ..config import Config, data_dir
from ..credentials import TELEGRAM_TOKEN, SecretStore
from ..events import Event, EventStore
from .email import EmailNotifier
from .sms import SmsNotifier, event_sms_text, make_provider
from .telegram import BotHandlers, TelegramAPI, TelegramBot, TelegramRateLimited

log = logging.getLogger(__name__)

RETRY_SECONDS = 60
CLIP_WAIT_SECONDS = 600  # una clip aspetta la sua foto al massimo 10 minuti
RETRY_MAX_AGE = 24 * 3600
RETRY_MAX_ITEMS = 20
RATE_LIMIT_MAX_WAIT = 30  # secondi: oltre, l'avviso va in coda invece di tenere fermo tutto
RETRY_BACKOFF = (60, 300, 900)  # attesa prima del 1°, 2°, 3° e successivi tentativi


@dataclass
class _Pending:
    """Un avviso che aspetta di partire, con i canali su cui è ancora da consegnare."""

    event: Event
    extra_buttons: list = field(default_factory=list)
    first_try: float = field(default_factory=time.time)
    channels: set[str] = field(default_factory=set)  # "telegram", "email" (l'SMS non si ritenta)
    attempts: int = 0
    next_try: float = 0.0

    def schedule(self, now: float) -> None:
        """Prossimo tentativo: 1 minuto, poi 5, poi 15 (un server di posta giù non si sblocca insistendo)."""
        self.attempts += 1
        self.next_try = now + RETRY_BACKOFF[min(self.attempts, len(RETRY_BACKOFF)) - 1]


class NotificationDispatcher:
    def __init__(self, config: Config, secrets: SecretStore, store: EventStore, handlers: BotHandlers,
                 telegram_api_factory: Callable[[str], TelegramAPI] = TelegramAPI,
                 email_factory=EmailNotifier, sms_http=None, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep):
        self.config = config
        self.secrets = secrets
        self.store = store
        self.handlers = handlers
        self.clock = clock  # orologio degli invii e delle attese fra un tentativo e l'altro
        self._sleep = sleep
        self._tg_factory = telegram_api_factory
        self._email_factory = email_factory
        self._sms_http = sms_http
        self.bot: TelegramBot | None = None
        self._bot_token: str | None = None
        self._q: queue.Queue = queue.Queue()
        self._pending: list[_Pending] = []
        self._clips_waiting: list[tuple] = []  # clip pronte la cui foto non è ancora partita
        self._clip_q: queue.Queue = queue.Queue()
        self._clip_thread: threading.Thread | None = None
        self._clip_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_error: dict[str, str] = {}
        self.last_sent: float = 0.0  # ora dell'ultimo avviso consegnato (Telegram o email)

    # --- configurazione ---------------------------------------------------
    def _telegram_cfg(self) -> dict:
        return self.config.get("notifications.telegram") or {}

    def setup_bot(self) -> None:
        """(Ri)crea il bot Telegram dalla configurazione corrente.

        Se il token non cambia, il bot già attivo viene solo aggiornato (gruppo e utenti):
        due bot attivi insieme con lo stesso token si "rubano" i messaggi (errore 409 di Telegram).
        """
        cfg = self._telegram_cfg()
        token = self.secrets.get(TELEGRAM_TOKEN)
        if self.bot and cfg.get("enabled") and token and token == self._bot_token:
            self.bot.chat_id = str(cfg.get("chat_id", ""))
            self.bot.allowed = {int(u) for u in cfg.get("allowed_user_ids", []) or []}
            self.bot.refresh_info_async()  # gruppo o utenti possono essere cambiati
            log.info("Bot Telegram aggiornato")
            return
        if self.bot:
            self.bot.stop()
            self.bot = None
        self._bot_token = token
        if cfg.get("enabled") and token:
            # Il bot parte anche senza ID del gruppo: così risponde a /id e si può completare la configurazione
            self.bot = TelegramBot(self._tg_factory(token), cfg.get("chat_id", ""), cfg.get("allowed_user_ids", []),
                                   self.handlers)
            self.bot.start()
            log.info("Bot Telegram avviato%s", "" if cfg.get("chat_id") else " (manca l'ID del gruppo: scrivi /id al bot)")
        elif cfg.get("enabled"):
            log.warning("Telegram attivo ma token mancante")

    def _email(self) -> EmailNotifier | None:
        cfg = self.config.get("notifications.email") or {}
        return self._email_factory(cfg, self.secrets) if cfg.get("enabled") else None

    def _sms(self) -> SmsNotifier | None:
        cfg = self.config.get("notifications.sms") or {}
        if not cfg.get("enabled"):
            return None
        kwargs = {"http": self._sms_http} if self._sms_http else {}
        provider = make_provider(cfg, self.secrets, self._email(), **kwargs)
        return SmsNotifier(cfg, provider, data_dir() / "sms_counter.json")

    # --- ciclo di vita ----------------------------------------------------
    def start(self) -> None:
        self.setup_bot()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="notifiche", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._q.put(None)
        if self.bot:
            self.bot.stop()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    # --- API --------------------------------------------------------------
    def armed(self) -> bool:
        """Con il sistema disinserito non parte nessun avviso automatico (Telegram, email, SMS)."""
        return bool(self.config.get("actions.armed", False))

    def notify_event(self, ev: Event, extra_buttons: list | None = None, force: bool = False) -> None:
        """``force``: invia anche a sistema disinserito (solo per gli avvisi di prova)."""
        if not self.armed() and not force:
            log.info("Sistema disinserito: evento %s salvato senza inviare avvisi", ev.id)
            return
        self._q.put(("event", _Pending(ev, extra_buttons or [])))

    def notify_text(self, text: str, force: bool = False) -> None:
        """Avviso di servizio (camera o nodo offline, ecc.): solo Telegram e solo se inserito.

        ``force``: anche a protezione disattivata (dissuasori, che lavorano sempre)."""
        if not self.armed() and not force:
            log.info("Sistema disinserito: avviso non inviato (%s)", text)
            return
        self._q.put(("text", text))

    def notify_clip(self, event_id: str, path, caption: str) -> None:
        """Clip di un evento pronta: parte su Telegram come risposta alla foto, se la foto è stata inviata
        (con la protezione disattivata la foto non parte, quindi nemmeno la clip)."""
        if not self.config.get("recording.telegram_clip", True):
            return
        self._clip_q.put((event_id, path, caption, time.time()))
        with self._clip_lock:
            if self._clip_thread is not None and self._clip_thread.is_alive():
                return
            # thread a parte (resta acceso): un video lento da caricare non ritarda mai la foto di un allarme
            self._clip_thread = threading.Thread(target=self._run_clips, name="notifiche-video", daemon=True)
            self._clip_thread.start()

    def _run_clips(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._clip_q.get(timeout=5)
            except queue.Empty:
                item = None
            if item is not None and not self._send_clip(item):
                self._clips_waiting.append(item)
            if self._clips_waiting and item is None:
                waiting, self._clips_waiting = self._clips_waiting, []
                self._clips_waiting = [c for c in waiting if not self._send_clip(c)]

    def _send_clip(self, item) -> bool:
        """True se inviata o da non inviare più; False se la foto non è ancora partita (si riprova)."""
        event_id, path, caption, added = item
        if not self.bot or not self.bot.chat_id or time.time() - added > CLIP_WAIT_SECONDS:
            return True
        try:
            return self.bot.send_event_clip(event_id, path, caption)
        except FileNotFoundError:
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("Telegram: clip non inviata (%s)", exc)
            return time.time() - added > 120  # rete o Telegram giù: qualche altro tentativo

    def send_text_now(self, text: str, buttons=None) -> bool:
        if not self.bot or not self.bot.chat_id:
            return False
        try:
            self.bot.send_text(text, buttons)
            return True
        except Exception as exc:
            log.warning("Messaggio Telegram non inviato: %s", exc)
            return False

    def flush(self, timeout: float = 10) -> None:
        """Attende lo svuotamento della coda (usato nei test)."""
        end = time.time() + timeout
        while not self._q.empty() and time.time() < end:
            time.sleep(0.05)
        time.sleep(0.1)

    # --- lavoro -----------------------------------------------------------
    def _run(self) -> None:
        last_retry = time.time()
        while not self._stop.is_set():
            try:
                item = self._q.get(timeout=5)
            except queue.Empty:
                item = None
            if item:
                kind, payload = item
                try:
                    if kind == "event":
                        self._deliver(payload)
                    elif kind == "text":
                        self.send_text_now(payload)
                except Exception:
                    log.exception("Errore durante l'invio degli avvisi")
            if time.time() - last_retry >= RETRY_SECONDS:
                last_retry = time.time()
                self._retry_pending()

    def _send_telegram(self, p: _Pending) -> str | None | bool:
        """Il link del messaggio (o None) se inviato, False se fallito.

        Se Telegram chiede di aspettare (429) si aspetta quanto chiede, al massimo
        ``RATE_LIMIT_MAX_WAIT``, e si riprova **una volta sola, con gli stessi pulsanti**:
        un avviso che torna un minuto dopo senza «Ferma tutto» arriva quando non serve più.
        """
        if not self.bot or not self.bot.chat_id:
            return False
        for ultimo_tentativo in (False, True):
            try:
                link = self.bot.send_event(p.event, p.extra_buttons)
                self.last_error.pop("telegram", None)
                return link
            except TelegramRateLimited as exc:
                self.last_error["telegram"] = str(exc)
                if ultimo_tentativo:
                    log.warning("Telegram: invio fallito (%s)", exc)
                    return False
                attesa = min(exc.retry_after, RATE_LIMIT_MAX_WAIT)
                log.info("Telegram: troppi messaggi, aspetto %.0f s e riprovo", attesa)
                self._sleep(attesa)
            except Exception as exc:
                self.last_error["telegram"] = str(exc)
                log.warning("Telegram: invio fallito (%s)", exc)
                return False
        return False

    def _send_email(self, email, ev: Event, link: str | None, telegram_sent: bool) -> bool:
        try:
            email.send_event(ev, link, telegram_sent=telegram_sent)
        except Exception as exc:  # noqa: BLE001
            self.last_error["email"] = str(exc)
            log.error("Email non inviata: %s", exc)
            return False
        self.last_error.pop("email", None)
        self.last_sent = self.clock()
        return True

    def _queue_retry(self, p: _Pending) -> None:
        p.schedule(self.clock())
        self._pending.append(p)
        if len(self._pending) > RETRY_MAX_ITEMS:
            self._pending = self._pending[-RETRY_MAX_ITEMS:]

    def pending_summary(self) -> tuple[int, list[str]]:
        """Quanti avvisi aspettano ancora di partire e su quali canali (per Sistema → Notifiche)."""
        items = list(self._pending)
        return len(items), sorted({c for p in items for c in p.channels})

    def _deliver(self, p: _Pending) -> None:
        ev = p.event
        tg = self._send_telegram(p)
        tg_ok = tg is not False
        link = tg if isinstance(tg, str) else None
        if tg_ok:
            self.last_sent = self.clock()
        if tg_ok and link:
            ev.telegram_link = link
            self.store.update(ev.id, telegram_link=link)
        failed = set()
        if not tg_ok and self._telegram_cfg().get("enabled"):
            failed.add("telegram")

        email = self._email()
        if email is not None and not self._send_email(email, ev, link, tg_ok):
            failed.add("email")

        if failed:
            ev.freeze()  # la foto dell'evento resta quella, anche se si riparte fra un'ora
            # al nuovo tentativo niente pulsanti di conferma: sarebbero ormai scaduti
            self._queue_retry(_Pending(ev, [], p.first_try, failed))

        sms = self._sms()
        is_person = getattr(ev, "label", "person") == "person" or getattr(ev, "is_test", False)
        if sms and is_person and sms.should_send(tg_ok):  # SMS (a pagamento) solo per le persone
            try:
                sms.send_all(event_sms_text(ev, link))
                self.last_error.pop("sms", None)
            except Exception as exc:
                self.last_error["sms"] = str(exc)
                log.error("SMS non inviato: %s", exc)

    def _retry_pending(self) -> None:
        """Ritenta solo i canali ancora falliti, con le attese di ``schedule`` e per al massimo 24 ore."""
        now = self.clock()
        still: list[_Pending] = []
        down: set[str] = set()  # canali già risultati giù in questo giro: non si insiste evento per evento
        for p in self._pending:
            if now - p.first_try > RETRY_MAX_AGE:
                log.warning("Avviso %s non consegnato entro 24 ore su %s: rinuncio",
                            p.event.id, ", ".join(sorted(p.channels)))
                continue
            if p.next_try > now or (p.channels & down):
                still.append(p)
                continue
            left: set[str] = set()
            if "telegram" in p.channels:
                tg = self._send_telegram(p)
                if tg is False:
                    left.add("telegram")
                else:
                    self.last_sent = now
                    if isinstance(tg, str):
                        p.event.telegram_link = tg
                        self.store.update(p.event.id, telegram_link=tg)
            if "email" in p.channels:
                email = self._email()  # se nel frattempo l'email è stata spenta, non c'è più niente da ritentare
                if email is not None and not self._send_email(email, p.event, p.event.telegram_link,
                                                              "telegram" not in left):
                    left.add("email")
            down |= left
            if left:
                p.channels = left
                p.schedule(now)
                still.append(p)
        self._pending = still
