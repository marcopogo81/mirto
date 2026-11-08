# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Email di avviso: foto in allegato e nel testo, più il link al messaggio Telegram.

Provider di default: Gmail con **password per app** (nessun progetto Google Cloud).
Preset per gli altri provider diffusi e configurazione personalizzata.
"""

from __future__ import annotations

import html
import logging
import smtplib
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from ..credentials import EMAIL_PASSWORD, SecretStore
from ..netssl import ssl_context
from ..i18n import N_, _

log = logging.getLogger(__name__)

# host, porta, cifratura, autenticazione
PRESETS: dict[str, dict] = {
    "gmail": {"label": "Gmail", "host": "smtp.gmail.com", "port": 465, "security": "ssl", "auth": "app_password"},
    "google_workspace": {"label": "Google Workspace", "host": "smtp.gmail.com", "port": 465, "security": "ssl", "auth": "app_password"},
    "outlook": {"label": "Outlook.com / Hotmail", "host": "smtp-mail.outlook.com", "port": 587, "security": "starttls", "auth": "oauth2"},
    "microsoft365": {"label": "Microsoft 365", "host": "smtp.office365.com", "port": 587, "security": "starttls", "auth": "oauth2"},
    "yahoo": {"label": "Yahoo Mail", "host": "smtp.mail.yahoo.com", "port": 465, "security": "ssl", "auth": "app_password"},
    "icloud": {"label": "iCloud Mail", "host": "smtp.mail.me.com", "port": 587, "security": "starttls", "auth": "app_password"},
    "libero": {"label": "Libero", "host": "smtp.libero.it", "port": 465, "security": "ssl", "auth": "password"},
    "aruba": {"label": "Aruba", "host": "smtps.aruba.it", "port": 465, "security": "ssl", "auth": "password"},
    "custom": {"label": N_("Personalizzato"), "host": "", "port": 465, "security": "ssl", "auth": "password"},
}


class EmailConfigError(RuntimeError):
    pass


class EmailNotifier:
    def __init__(self, cfg: dict, secrets: SecretStore, smtp_factory=None):
        self.cfg = cfg
        self.secrets = secrets
        preset = PRESETS.get(cfg.get("provider") or "gmail", PRESETS["custom"])
        self.host = cfg.get("host") or preset["host"]
        self.port = int(cfg.get("port") or preset["port"])
        self.security = (cfg.get("security") or preset["security"]).lower()
        self.auth = preset["auth"]
        self.username = cfg.get("username", "")
        self.sender = cfg.get("sender") or self.username
        self.recipients = [r for r in cfg.get("recipients", []) if r]
        self._smtp_factory = smtp_factory

    def check(self) -> None:
        if self.auth == "oauth2":
            raise EmailConfigError(_("Questo provider richiede l'accesso OAuth2, non ancora disponibile in Mirto"))
        if not self.host or not self.username or not self.recipients:
            raise EmailConfigError(_('Email: mancano server, utente o destinatari'))
        if self.security not in ("ssl", "starttls"):
            raise EmailConfigError(_('Email: la connessione deve essere cifrata (ssl o starttls)'))
        if not self.secrets.get(EMAIL_PASSWORD):
            raise EmailConfigError(_('Email: password (per Gmail: password per app) non impostata'))

    # --- composizione -----------------------------------------------------
    def build_event_message(self, ev, telegram_link: str | None, telegram_sent: bool = False) -> EmailMessage:
        msg = EmailMessage()
        msg["Subject"] = ev.title()
        msg["From"] = self.sender
        msg["To"] = ", ".join(self.recipients)
        msg["Date"] = formatdate(localtime=True)
        if telegram_link:
            link_txt = _('Apri il messaggio su Telegram: {telegram_link}', telegram_link=telegram_link)
        elif telegram_sent:  # chat privata o gruppo semplice: Telegram non crea link ai messaggi
            link_txt = _('La foto è stata inviata anche su Telegram.')
        else:
            link_txt = _('Telegram non era raggiungibile: la foto è in allegato e sarà inviata su Telegram appena possibile.')
        what = ev.short_text() if hasattr(ev, "short_text") else _('Persona rilevata')
        head = _('Avviso di PROVA (nessun allarme reale)') if getattr(ev, "is_test", False) else what
        text = (_('{head}\n\nCamera: {camera_name}\nData e ora: {when_text}\nConfidenza: {score:.0%}\n\n{link_txt}\n\nFoto in allegato.\n— Mirto', head=head, camera_name=ev.camera_name, when_text=ev.when_text(), score=ev.score, link_txt=link_txt))
        msg.set_content(text)
        cid = make_msgid(domain="mirto.local")
        link_html = (_('<p><a href="{escape}">Apri il messaggio su Telegram</a></p>', escape=html.escape(telegram_link))
                     if telegram_link else f"<p>{html.escape(link_txt)}</p>")
        body = (
            f"<html><body style='font-family:sans-serif'>"
            f"<h2 style='color:#c0392b'>{_('Avviso di PROVA') if getattr(ev, 'is_test', False) else html.escape(what)}</h2>"
            + _("<p><b>Camera:</b> {camera}<br><b>Data e ora:</b> {when}<br><b>Confidenza:</b> {score:.0%}</p>",
                camera=html.escape(ev.camera_name), when=ev.when_text(), score=ev.score)
            + f"{link_html}"
            f"<img src='cid:{cid[1:-1]}' style='max-width:100%;border-radius:6px'>"
            f"<p style='color:#888'>— Mirto</p></body></html>"
        )
        msg.add_alternative(body, subtype="html")
        jpeg = ev.jpeg()
        msg.get_payload()[1].add_related(jpeg, maintype="image", subtype="jpeg", cid=cid)
        msg.add_attachment(jpeg, maintype="image", subtype="jpeg", filename=ev.filename())
        return msg

    def build_text_message(self, subject: str, text: str, to: list[str] | None = None) -> EmailMessage:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.sender
        msg["To"] = ", ".join(to or self.recipients)
        msg["Date"] = formatdate(localtime=True)
        msg.set_content(text)
        return msg

    # --- invio ------------------------------------------------------------
    def _connect(self):
        ctx = ssl_context()
        if self._smtp_factory:
            return self._smtp_factory(self)
        if self.security == "ssl":
            smtp = smtplib.SMTP_SSL(self.host, self.port, context=ctx, timeout=30)
        else:
            smtp = smtplib.SMTP(self.host, self.port, timeout=30)
            smtp.starttls(context=ctx)
        smtp.login(self.username, self.secrets.get(EMAIL_PASSWORD) or "")
        return smtp

    def send(self, msg: EmailMessage) -> None:
        self.check()
        smtp = self._connect()
        try:
            smtp.send_message(msg)
        finally:
            try:
                smtp.quit()
            except Exception:
                pass

    def send_event(self, ev, telegram_link: str | None, telegram_sent: bool = False) -> None:
        self.send(self.build_event_message(ev, telegram_link, telegram_sent))
        log.info("Email inviata a %d destinatari", len(self.recipients))
