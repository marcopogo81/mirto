# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""SMS opzionali con provider generico.

Tre modi per collegare un provider:
  1. ``twilio``    – preset pronto (servono Account SID, numero mittente e token)
  2. ``http``      – qualunque servizio con API web: URL, metodo, intestazioni e corpo
                     con segnaposto ``{numero}``, ``{testo}``, ``{api_key}``
  3. ``email2sms`` – provider che convertono un'email in SMS (usa l'SMTP già configurato)

Gli SMS contengono solo testo e il link al messaggio Telegram (gli MMS via API
non arrivano in Italia). Limite giornaliero per tenere sotto controllo i costi.
"""

from __future__ import annotations

import base64
import json
import logging
import threading
import urllib.parse
from datetime import date
from pathlib import Path

from ..credentials import SMS_HTTP_API_KEY, SMS_TWILIO_TOKEN, SecretStore
from .httputil import request
from ..i18n import _

log = logging.getLogger(__name__)


class SmsError(RuntimeError):
    pass


class SmsProvider:
    name = "base"

    def send(self, number: str, text: str) -> None:  # pragma: no cover - interfaccia
        raise NotImplementedError


class HttpTemplateProvider(SmsProvider):
    """Provider HTTP configurabile a mano, senza scrivere codice."""

    name = "http"

    def __init__(self, cfg: dict, secrets: SecretStore, http=request):
        self.cfg = cfg
        self.secrets = secrets
        self.http = http

    def _fill(self, template: str, values: dict, mode: str) -> str:
        out = template
        for key, val in values.items():
            if mode == "url":
                rep = urllib.parse.quote(val, safe="")
            elif mode == "json":
                rep = json.dumps(val)[1:-1]  # escape JSON senza virgolette esterne
            elif mode == "form":
                rep = urllib.parse.quote_plus(val)
            else:
                rep = val
            out = out.replace("{" + key + "}", rep)
        return out

    def send(self, number: str, text: str) -> None:
        values = {"numero": number, "testo": text, "api_key": self.secrets.get(SMS_HTTP_API_KEY) or ""}
        ctype = self.cfg.get("content_type", "application/json")
        body_mode = "json" if "json" in ctype else "form" if "x-www-form-urlencoded" in ctype else "raw"
        url = self._fill(self.cfg.get("url", ""), values, "url")
        if not url.startswith("https://"):
            raise SmsError(_('Il provider SMS deve usare https://'))
        headers = {k: self._fill(str(v), values, "raw") for k, v in (self.cfg.get("headers") or {}).items()}
        body = self.cfg.get("body") or ""
        data = self._fill(body, values, body_mode).encode() if body else None
        if data is not None:
            headers.setdefault("Content-Type", ctype)
        resp = self.http(self.cfg.get("method", "POST"), url, data=data, headers=headers, timeout=30)
        ok_codes = self.cfg.get("success_status") or [200, 201, 202]
        must = self.cfg.get("success_contains") or ""
        if resp.status not in ok_codes or (must and must not in resp.text):
            raise SmsError(_('Invio SMS fallito ({status}): {v}', status=resp.status, v=resp.text[:200]))


class TwilioProvider(SmsProvider):
    name = "twilio"

    def __init__(self, cfg: dict, secrets: SecretStore, http=request):
        self.sid = cfg.get("account_sid", "")
        self.sender = cfg.get("from_number", "")
        self.secrets = secrets
        self.http = http

    def send(self, number: str, text: str) -> None:
        token = self.secrets.get(SMS_TWILIO_TOKEN)
        if not (self.sid and self.sender and token):
            raise SmsError(_('Twilio: mancano Account SID, numero mittente o token'))
        auth = base64.b64encode(f"{self.sid}:{token}".encode()).decode()
        data = urllib.parse.urlencode({"To": number, "From": self.sender, "Body": text}).encode()
        resp = self.http(
            "POST", f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}/Messages.json", data=data,
            headers={"Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        if resp.status not in (200, 201):
            raise SmsError(_("Twilio: errore {status}: {text}", status=resp.status, text=resp.text[:200]))


class EmailToSmsProvider(SmsProvider):
    name = "email2sms"

    def __init__(self, cfg: dict, email_notifier):
        self.template = cfg.get("address_template", "")
        self.email = email_notifier

    def send(self, number: str, text: str) -> None:
        if self.email is None:
            raise SmsError(_("Email-to-SMS: configura prima l'email"))
        address = self.template.replace("{numero}", number.lstrip("+"))
        self.email.send(self.email.build_text_message("Mirto", text, to=[address]))


def make_provider(cfg: dict, secrets: SecretStore, email_notifier=None, http=request) -> SmsProvider:
    kind = cfg.get("provider", "http")
    if kind == "twilio":
        return TwilioProvider(cfg.get("twilio", {}), secrets, http)
    if kind == "email2sms":
        return EmailToSmsProvider(cfg.get("email2sms", {}), email_notifier)
    return HttpTemplateProvider(cfg.get("http", {}), secrets, http)


class SmsNotifier:
    """Applica limite giornaliero e modalità (sempre / solo se Telegram fallisce)."""

    def __init__(self, cfg: dict, provider: SmsProvider, counter_file: Path):
        self.cfg = cfg
        self.provider = provider
        self.counter_file = Path(counter_file)
        self._lock = threading.Lock()

    def _load(self) -> dict:
        try:
            data = json.loads(self.counter_file.read_text())
            if data.get("day") == date.today().isoformat():
                return data
        except Exception:
            pass
        return {"day": date.today().isoformat(), "sent": 0}

    def should_send(self, telegram_ok: bool) -> bool:
        if not self.cfg.get("enabled"):
            return False
        return self.cfg.get("mode", "always") == "always" or not telegram_ok

    def send_all(self, text: str) -> int:
        recipients = [r for r in self.cfg.get("recipients", []) if r]
        limit = int(self.cfg.get("daily_limit", 20))
        sent = 0
        with self._lock:
            counter = self._load()
            try:
                for number in recipients:
                    if counter["sent"] >= limit:
                        log.warning("Limite giornaliero SMS raggiunto (%d)", limit)
                        break
                    counter["sent"] += 1  # contato anche se il provider fallisce: meglio prudenti sui costi
                    self.provider.send(number, text[:300])
                    sent += 1
            finally:
                self.counter_file.parent.mkdir(parents=True, exist_ok=True)
                self.counter_file.write_text(json.dumps(counter))
        return sent


def event_sms_text(ev, link: str | None) -> str:
    what = ev.short_text().lower() if hasattr(ev, "short_text") else _('persona rilevata')
    text = f"Mirto: {what} - {ev.camera_name} - {ev.when.strftime('%d/%m %H:%M')}"
    return f"{text} {link}" if link else text
