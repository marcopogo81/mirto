# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Programmazione automatica di inserimento e disinserimento.

Ogni regola è un "evento" a un'ora precisa::

    - nome: Notte
      azione: inserisci          # inserisci | disinserisci
      ora: "22:30"
      giorni: [lun, mar, mer, gio, ven]   # vuoto = tutti i giorni
      dal: "2026-12-20"          # facoltativo: valida solo da questa data…
      al: "2027-01-06"           # …fino a questa (comprese)
      attiva: true

Un cambio fatto a mano (interfaccia o Telegram) resta valido fino al prossimo
evento programmato. Se il Mac era spento o in stop quando un evento doveva
scattare, alla ripresa viene applicato solo l'ultimo evento mancato (lo stato
"giusto" in quel momento), non tutta la sequenza.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from .i18n import N_, _

log = logging.getLogger(__name__)

DAYS = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]  # lunedì = 0 come in Python
DAY_NAMES = [N_("lunedì"), N_("martedì"), N_("mercoledì"), N_("giovedì"), N_("venerdì"), N_("sabato"),
             N_("domenica")]
ACTIONS = {"inserisci", "disinserisci"}
CHECK_SECONDS = 20
MAX_GAP_SECONDS = 600  # oltre questo intervallo tra due controlli consideriamo che il Mac fosse in stop


@dataclass
class ScheduleRule:
    nome: str
    azione: str
    ora: str
    giorni: list = field(default_factory=list)
    dal: str = ""
    al: str = ""
    attiva: bool = True

    @classmethod
    def from_dict(cls, d: dict) -> "ScheduleRule":
        r = cls(
            nome=str(d.get("nome") or "").strip() or _('Senza nome'),
            azione=str(d.get("azione", "")).strip().lower(),
            ora=str(d.get("ora", "")).strip(),
            giorni=[str(g).strip().lower()[:3] for g in (d.get("giorni") or [])],
            dal=str(d.get("dal") or "").strip(),
            al=str(d.get("al") or "").strip(),
            attiva=bool(d.get("attiva", True)),
        )
        r.validate()
        return r

    def to_dict(self) -> dict:
        return {"nome": self.nome, "azione": self.azione, "ora": self.ora, "giorni": list(self.giorni),
                "dal": self.dal, "al": self.al, "attiva": self.attiva}

    def validate(self) -> None:
        if self.azione not in ACTIONS:
            raise ValueError(_("azione '{azione}' non valida (inserisci o disinserisci)", azione=self.azione))
        try:
            hh, mm = self.ora.split(":")
            if not (0 <= int(hh) < 24 and 0 <= int(mm) < 60):
                raise ValueError
        except ValueError:
            raise ValueError(_("ora '{ora}' non valida (formato HH:MM)", ora=self.ora)) from None
        bad = [g for g in self.giorni if g not in DAYS]
        if bad:
            raise ValueError(_('giorni non validi: {join} (usa {join2})', join=', '.join(bad), join2=', '.join(DAYS)))
        for v in (self.dal, self.al):
            if v:
                date.fromisoformat(v)
        if self.dal and self.al and date.fromisoformat(self.dal) > date.fromisoformat(self.al):
            raise ValueError(_("la data 'dal' è successiva alla data 'al'"))

    @property
    def armed(self) -> bool:
        return self.azione == "inserisci"

    def applies_on(self, day: date) -> bool:
        if not self.attiva:
            return False
        if self.giorni and DAYS[day.weekday()] not in self.giorni:
            return False
        if self.dal and day < date.fromisoformat(self.dal):
            return False
        if self.al and day > date.fromisoformat(self.al):
            return False
        return True

    def at(self, day: date) -> datetime:
        hh, mm = (int(x) for x in self.ora.split(":"))
        return datetime(day.year, day.month, day.day, hh, mm)

    def describe(self) -> str:
        days = "tutti i giorni" if not self.giorni or len(self.giorni) == 7 else \
            ", ".join(g for g in DAYS if g in self.giorni)
        period = ""
        if self.dal or self.al:
            fmt = lambda v: date.fromisoformat(v).strftime("%d/%m/%Y")  # noqa: E731
            period = f", dal {fmt(self.dal)}" if self.dal else ""
            period += f" al {fmt(self.al)}" if self.al else ""
        state = "" if self.attiva else " [disattivata]"
        return f"{self.ora} {self.azione.upper()} – {days}{period} ({self.nome}){state}"


def load_rules(raw: list) -> tuple[list[ScheduleRule], list[str]]:
    rules, errors = [], []
    for i, d in enumerate(raw or [], 1):
        try:
            rules.append(ScheduleRule.from_dict(d))
        except Exception as exc:
            errors.append(_("Regola {i}: {exc}", i=i, exc=exc))
    return rules, errors


def occurrences(rules: list[ScheduleRule], start: datetime, end: datetime) -> list[tuple[datetime, ScheduleRule]]:
    """Eventi con istante in (start, end], in ordine di tempo."""
    out = []
    day = start.date()
    while day <= end.date():
        for r in rules:
            if r.applies_on(day):
                t = r.at(day)
                if start < t <= end:
                    out.append((t, r))
        day += timedelta(days=1)
    out.sort(key=lambda x: x[0])
    return out


def next_event(rules: list[ScheduleRule], now: datetime, horizon_days: int = 400):
    """Prossimo evento dopo ``now`` (entro ``horizon_days``), oppure None."""
    day = now.date()
    for _day in range(horizon_days):
        todays = sorted((r.at(day), r) for r in rules if r.applies_on(day))
        todays = [(t, r) for t, r in todays if t > now]
        if todays:
            return todays[0]
        day += timedelta(days=1)
    return None


def describe_when(t: datetime, now: datetime) -> str:
    if t.date() == now.date():
        return _('oggi alle {t:%H:%M}', t=t)
    if t.date() == now.date() + timedelta(days=1):
        return _('domani alle {t:%H:%M}', t=t)
    return _("{day} {t:%d/%m} alle {t:%H:%M}", day=_(DAY_NAMES[t.weekday()]), t=t)


def action_word(azione: str) -> str:
    """"inserisci" -> "ATTIVA PROTEZIONE" nella lingua dell'interfaccia."""
    return _("ATTIVA PROTEZIONE") if azione == "inserisci" else _("DISATTIVA PROTEZIONE")


class Scheduler:
    """Controlla le regole ogni 20 secondi e inserisce/disinserisce il sistema."""

    def __init__(self, core, clock=time.time):
        self.core = core
        self.clock = clock
        self._last_check = clock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.errors: list[str] = []

    @property
    def enabled(self) -> bool:
        return bool(self.core.config.get("schedule.enabled", True))

    def rules(self) -> list[ScheduleRule]:
        rules, self.errors = load_rules(self.core.config.get("schedule.rules") or [])
        return rules

    def next(self):
        if not self.enabled:
            return None
        return next_event(self.rules(), datetime.fromtimestamp(self.clock()))

    def tick(self) -> ScheduleRule | None:
        """Applica gli eventi scattati dall'ultimo controllo. Restituisce la regola applicata."""
        now_ts = self.clock()
        start, end = datetime.fromtimestamp(self._last_check), datetime.fromtimestamp(now_ts)
        gap = now_ts - self._last_check
        self._last_check = now_ts
        if not self.enabled:
            return None
        due = occurrences(self.rules(), start, end)
        if not due:
            return None
        if gap > MAX_GAP_SECONDS and len(due) > 1:
            log.info("Programmazione: %d eventi mancati durante lo stop, applico solo l'ultimo", len(due))
        when, rule = due[-1]  # lo stato giusto è quello dell'ultimo evento
        current = bool(self.core.config.get("actions.armed", False))
        if current != rule.armed:
            self.core.set_armed(rule.armed, source=f"programmazione «{rule.nome}»")
        log.info("Programmazione: %s (%s) alle %s", rule.azione, rule.nome, when.strftime("%H:%M"))
        return rule

    def start(self) -> None:
        self._last_check = self.clock()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="programmazione", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(CHECK_SECONDS):
            try:
                self.tick()
            except Exception:
                log.exception("Errore nella programmazione")

    def status_text(self) -> str:
        rules = self.rules()
        lines = ["⏰ Programmazione " + (_("ATTIVA") if self.enabled else _("SOSPESA"))]
        if not rules:
            lines.append(_('Nessuna regola. Si configurano in Impostazioni → Programmazione.'))
        for r in sorted(rules, key=lambda r: r.ora):
            lines.append("• " + r.describe())
        nxt = self.next()
        if nxt:
            t, r = nxt
            lines.append(_("Prossimo: {when} → {what} ({name})", when=describe_when(t, datetime.fromtimestamp(self.clock())),
                           what=action_word(r.azione), name=r.nome))
        for e in self.errors:
            lines.append("⚠️ " + e)
        return "\n".join(lines)
