# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Condizioni d'uso: vanno accettate al primo avvio, prima che Mirto apra camere e microfoni.

Mirto è distribuito come **solo software**: invia comandi ai nodi e alle loro uscite, mentre la scelta,
l'installazione e l'uso conforme di ciò che vi si collega restano di chi lo usa. Le condizioni lo dicono in modo
chiaro e chiedono un'accettazione esplicita (casella + «Accetto»); rifiutando, Mirto si chiude.
Restano consultabili da Aiuto → Condizioni d'uso. Se il testo cambia in modo sostanziale si alza
``LEGAL_VERSION`` e vanno riaccettate una volta.

Non è una consulenza legale: il testo va fatto rivedere da un avvocato prima di vendere qualcosa.
"""

from __future__ import annotations

import datetime as dt

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..i18n import N_, _
from . import theme as T

LEGAL_VERSION = 2
GUIDE_HEADING = N_("17. Sicurezza, privacy e condizioni d'uso")


def notice_items() -> list[tuple[str, str]]:
    """(titolo, testo) delle condizioni, nell'ordine in cui compaiono."""
    return [
        (_("Solo software"),
         _("Mirto analizza le immagini e invia comandi ai nodi e alle loro uscite (relè, prese, altoparlanti, "
           "servizi HTTP). Scegliere, installare e usare in modo conforme i dispositivi che vi colleghi è una tua "
           "responsabilità, come rispettare le norme sulla loro installazione.")),
        (_("Inquadra solo la tua proprietà"),
         _("Se le camere riprendono strada, scale, cortili o giardini condivisi valgono le regole sulla protezione "
           "dei dati (in Italia del Garante Privacy): inquadratura limitata al necessario e cartello "
           "«area videosorvegliata» prima della zona ripresa.")),
        (_("Avvisa chi vive o lavora in casa"),
         _("Familiari, ospiti e collaboratori domestici devono sapere che ci sono camere, microfoni e il controllo dei "
           "telefoni (Presenza). Per chi lavora da te valgono anche le regole sul controllo a distanza dei lavoratori.")),
        (_("Nessun dispositivo deve poter fare del male"),
         _("Non collegare dispositivi che possano ferire o mettere in pericolo qualcuno o impedirgli di uscire. "
           "Evita allarmi acustici ripetuti senza motivo: Mirto e il nodo li fermano comunque dopo 3 minuti.")),
        (_("Nessuna garanzia"),
         _("Mirto non è un impianto antifurto certificato e non sostituisce un servizio di vigilanza: può non "
           "rilevare un evento, non inviare un avviso o non eseguire un comando. È fornito «così com'è», senza "
           "garanzie, secondo la licenza Apache 2.0.")),
        (_("Uso a tuo rischio"),
         _("Nei limiti consentiti dalla legge, gli autori di Mirto non rispondono di danni dovuti a un uso improprio "
           "o non conforme, ai dispositivi collegati, a mancati rilevamenti o avvisi, né all'uso delle immagini "
           "registrate.")),
    ]


class LegalNoticeDialog(QDialog):
    """``first_time``: casella «Ho letto e accetto» + Accetto / Esci. Altrimenti solo lettura (Chiudi)."""

    def __init__(self, parent=None, first_time: bool = True):
        super().__init__(parent)
        self.setWindowTitle(_("Condizioni d'uso"))
        self.setModal(True)
        self.setMinimumWidth(620)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 18)
        lay.setSpacing(10)
        head = QLabel(_("Condizioni d'uso di Mirto") if not first_time
                      else _("Prima di iniziare: leggi e accetta le condizioni d'uso"))
        head.setWordWrap(True)
        head.setStyleSheet(f"font-size:{T.SIZE_TITLE + 2}px; font-weight:800; color:{T.TEXT};")
        lay.addWidget(head)
        body = QWidget()
        col = QVBoxLayout(body)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(10)
        for title, text in notice_items():
            t = QLabel(f"<b>{title}</b><br>{text}")
            t.setWordWrap(True)
            t.setTextFormat(Qt.TextFormat.RichText)
            t.setStyleSheet(f"font-size:{T.SIZE_BODY}px; color:{T.TEXT};")
            col.addWidget(t)
        note = QLabel(_("Le regole cambiano da paese a paese: in caso di dubbio chiedi a un professionista."))
        note.setWordWrap(True)
        note.setStyleSheet(f"font-size:{T.SIZE_SMALL}px; color:{T.MUTED};")
        col.addWidget(note)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(body)
        scroll.setMinimumHeight(380)
        lay.addWidget(scroll, 1)
        self.agree = None
        if first_time:
            self.agree = QCheckBox(_("Ho letto e accetto le condizioni d'uso"))
            lay.addWidget(self.agree)
        row = QHBoxLayout()
        guide = QPushButton(_("Leggi nella guida"))
        guide.clicked.connect(self._guide)
        row.addWidget(guide)
        row.addStretch(1)
        if first_time:
            leave = QPushButton(_("Esci da Mirto"))
            leave.clicked.connect(self.reject)
            row.addWidget(leave)
            ok = QPushButton(_("Accetto"))
            ok.setEnabled(False)
            self.agree.toggled.connect(ok.setEnabled)
            ok.setStyleSheet(f"QPushButton:enabled {{ background:{T.INFO}; color:#FFFFFF; font-weight:700; "
                             "padding:8px 18px; }")
        else:
            ok = QPushButton(_("Chiudi"))
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        lay.addLayout(row)

    def _guide(self) -> None:
        from .guide import open_guide  # noqa: PLC0415

        open_guide(self, _(GUIDE_HEADING))


def accepted(config) -> bool:
    return int(config.get("general.legal_notice", 0) or 0) >= LEGAL_VERSION


def ensure_accepted(config, parent=None) -> bool:
    """True se le condizioni sono (già o ora) accettate. Va chiamata prima di aprire camere e microfoni."""
    if accepted(config):
        return True
    dlg = LegalNoticeDialog(parent, first_time=True)
    if dlg.exec() == QDialog.DialogCode.Accepted and dlg.agree is not None and dlg.agree.isChecked():
        config.set("general.legal_notice", LEGAL_VERSION)
        config.set("general.legal_accepted_at", dt.datetime.now().astimezone().isoformat(timespec="seconds"))
        config.save()
        return True
    return False
