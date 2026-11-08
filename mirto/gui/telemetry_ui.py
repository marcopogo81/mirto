# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Domanda sulle statistiche anonime (primo avvio) e finestra "Cosa viene inviato"."""

from __future__ import annotations

import json

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout

from .. import RILASCI_URL, telemetry
from ..i18n import _
from . import theme as T


def preview_text(core) -> str:
    """Il battito esattamente come partirebbe (con un ID d'esempio se non c'è ancora il consenso)."""
    data = telemetry.payload(core.config, core.telemetry_stats())
    if not data["id"]:
        data["id"] = "<" + _("ID casuale, creato solo se rispondi sì") + ">"
    return json.dumps(data, ensure_ascii=False, indent=2)


class ConsentDialog(QDialog):
    """Sì / No alle statistiche anonime. Il pulsante predefinito è «No, grazie»."""

    def __init__(self, core, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Statistiche d'uso"))
        self.setModal(True)
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 18)
        lay.setSpacing(10)
        head = QLabel(_("Aiuti a migliorare Mirto?"))
        head.setStyleSheet(f"font-size:{T.SIZE_TITLE + 2}px; font-weight:800; color:{T.TEXT};")
        lay.addWidget(head)
        body = QLabel(_(
            "Se rispondi <b>sì</b>, una volta al giorno Mirto invia un breve messaggio: un numero creato a caso (non ricavato da te, dal computer o da un account; lo puoi rinnovare), la versione, il sistema, la lingua, quante camere e nodi usi <b>per fasce</b> e quali funzioni sono accese. Serve solo a sapere quante installazioni sono attive.<br><br><b>Non escono mai</b> foto, video, audio, nomi delle camere, indirizzi, dati di Telegram o della posta. Il nostro server non legge né salva gli indirizzi IP. Puoi cambiare idea in Impostazioni → Generale: ritirando il consenso il numero viene cancellato anche dal server.<br><br>Se rispondi <b>no</b>, Mirto invia <b>una sola volta</b> un «no» senza numero né dati del computer (solo la versione di Mirto), per contare anche chi rifiuta. Se chiudi questa finestra senza scegliere, te lo richiede al prossimo avvio.<br><br>In ogni caso, una volta al giorno Mirto chiede qual è l'ultima versione (inviando solo la propria): si può spegnere nelle Impostazioni."))
        body.setWordWrap(True)
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setStyleSheet(f"font-size:{T.SIZE_BODY}px; color:{T.TEXT};")
        lay.addWidget(body)
        self.preview = QPlainTextEdit(preview_text(core))
        self.preview.setReadOnly(True)
        self.preview.setVisible(False)
        self.preview.setMinimumHeight(200)
        lay.addWidget(self.preview)
        row = QHBoxLayout()
        show = QPushButton(_("Mostra cosa viene inviato"))
        show.clicked.connect(lambda: (self.preview.setVisible(not self.preview.isVisible()), self.adjustSize()))
        row.addWidget(show)
        row.addStretch(1)
        self.answer = None  # "yes" / "no"; chiusa in altro modo = nessuna risposta
        no = QPushButton(_("No, grazie"))
        no.clicked.connect(lambda: (setattr(self, "answer", "no"), self.reject()))
        yes = QPushButton(_("Sì, invia le statistiche d'uso"))
        yes.setStyleSheet(f"QPushButton {{ background:{T.INFO}; color:#FFFFFF; font-weight:700; padding:8px 18px; }}")
        yes.clicked.connect(lambda: (setattr(self, "answer", "yes"), self.accept()))
        row.addWidget(no)
        row.addWidget(yes)
        lay.addLayout(row)
        no.setDefault(True)
        no.setFocus()


def ask_consent(core, parent=None) -> None:
    """Chiede una volta sola, e solo se c'è un server a cui inviare. Conta solo un pulsante premuto:
    chiudere la finestra (o uscire da Mirto) lascia la domanda per il prossimo avvio."""
    tel = core.telemetry
    if tel.asked() or not tel.available():
        return
    dlg = ConsentDialog(core, parent)
    dlg.exec()
    if dlg.answer in ("yes", "no") and not tel.asked():
        tel.set_consent(dlg.answer == "yes")


class PreviewDialog(QDialog):
    def __init__(self, core, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Cosa viene inviato"))
        self.setMinimumSize(520, 360)
        lay = QVBoxLayout(self)
        info = QLabel(_("Il messaggio giornaliero delle statistiche d'uso, esattamente come parte (niente altro):"))
        info.setWordWrap(True)
        lay.addWidget(info)
        text = QPlainTextEdit(preview_text(core))
        text.setReadOnly(True)
        lay.addWidget(text, 1)
        close = QPushButton(_("Chiudi"))
        close.clicked.connect(self.accept)
        lay.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)


def open_releases() -> None:
    QDesktopServices.openUrl(QUrl(RILASCI_URL))
