# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Guida utente dentro l'app e riquadri d'aiuto delle Impostazioni.

* ``GuideDialog``: mostra ``GUIDA_UTENTE.md`` e si apre direttamente al capitolo richiesto;
  i collegamenti interni (``#4-configurare-telegram``) portano al capitolo, quelli web si aprono
  nel browser.
* ``help_box``: riquadro "Come fare" con i passi essenziali, uno per riga, e il collegamento
  "Apri la guida" alla sezione che li spiega per intero.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from . import theme as T
from ..i18n import _, get_language


GUIDE_FILES = {"it": "GUIDA_UTENTE.md", "en": "USER_GUIDE.md"}


def guide_path(lang: str | None = None) -> Path | None:
    """Guida nella lingua dell'interfaccia (GUIDA_UTENTE.md, USER_GUIDE.md…) accanto al programma
    (cartella del progetto o pacchetto PyInstaller); se manca, quella italiana."""
    roots = [Path(__file__).resolve().parents[2]]
    if getattr(sys, "_MEIPASS", None):
        roots.insert(0, Path(sys._MEIPASS))
    names = [GUIDE_FILES.get(lang or get_language(), ""), GUIDE_FILES["it"]]
    for name in filter(None, names):
        for root in roots:
            p = root / name
            if p.exists():
                return p
    return None


def slug(text: str) -> str:
    """Come GitHub: "4. Configurare Telegram" → "4-configurare-telegram"."""
    s = text.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s)
    return re.sub(r"\s", "-", s)


class GuideDialog(QDialog):
    def __init__(self, parent=None, heading: str = ""):
        super().__init__(parent)
        self.setWindowTitle(_('Guida utente – Mirto'))
        self.resize(860, 760)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 10)
        self.view = QTextBrowser()
        self.view.setOpenLinks(False)
        self.view.anchorClicked.connect(self._link)
        self.view.setStyleSheet(f"QTextBrowser {{ background:{T.PANEL}; color:{T.TEXT}; border:none; "
                                f"border-radius:10px; padding:18px; font-size:{T.SIZE_BODY}px; }}")
        lay.addWidget(self.view, 1)
        row = QHBoxLayout()
        top = QPushButton(_("Indice"))
        top.clicked.connect(lambda: self.go(_("Indice")))
        close = QPushButton(_("Chiudi"))
        close.clicked.connect(self.accept)
        row.addWidget(top)
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)
        path = guide_path()
        if path:
            self.view.setMarkdown(path.read_text(encoding="utf-8"))
        else:
            self.view.setMarkdown(_('# Guida non trovata\n\nIl file della guida non è accanto al programma.'))
        self._headings = self._collect_headings()
        if heading:
            self.go(heading)

    def _collect_headings(self) -> dict[str, int]:
        out: dict[str, int] = {}
        block = self.view.document().begin()
        while block.isValid():
            if block.blockFormat().headingLevel() > 0:
                text = block.text().strip()
                out.setdefault(text, block.position())
                out.setdefault(slug(text), block.position())
            block = block.next()
        return out

    def go(self, heading: str) -> bool:
        """Porta in cima alla finestra il capitolo ``heading`` (testo del titolo o suo "slug")."""
        pos = self._headings.get(heading.strip()) or self._headings.get(slug(heading))
        if pos is None:  # titolo che inizia così (es. "10. SMS")
            pos = next((p for t, p in self._headings.items() if t.startswith(heading.strip())), None)
        num = re.match(r"\d+[a-z]*\.", heading.strip())
        if pos is None and num:  # stesso numero di capitolo (guida in un'altra lingua)
            pos = next((p for t, p in self._headings.items() if t.startswith(num.group(0) + " ")), None)
        if pos is None:
            return False
        cur = QTextCursor(self.view.document())
        cur.setPosition(pos)
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())  # così il titolo finisce in alto e non in fondo
        self.view.setTextCursor(cur)
        self.view.ensureCursorVisible()
        return True

    def _link(self, url: QUrl) -> None:
        if url.scheme() in ("http", "https", "mailto"):
            QDesktopServices.openUrl(url)
        elif url.fragment():
            self.go(url.fragment())


def open_guide(parent=None, heading: str = "") -> None:
    GuideDialog(parent, heading).exec()


def aiuto_in_fondo(scheda):
    """Sposta il riquadro «Come fare» in fondo alla scheda, lasciando tutto il resto com'è.

    In cima distrae: chi apre Impostazioni cerca un interruttore, non una lezione, e si ritrova dieci
    righe di spiegazione prima di qualunque cosa. In fondo resta a disposizione di chi la vuole.
    Chiesto da Marco il 21/10. Funziona anche quando la scheda è dentro un QScrollArea.
    """
    box = scheda.findChild(QFrame, "helpBox")
    if box is not None:
        lay = box.parent().layout() if box.parent() else None
        if lay is not None and lay.indexOf(box) != -1:
            lay.removeWidget(box)
            lay.addWidget(box)
    return scheda


def guide_link(heading: str, parent_dialog=None) -> QLabel:
    """Solo il collegamento alla guida, senza il riquadro dei passi.

    Marco, 27/10, guardando la scheda Azioni: *«togli il box come fare, mantieni solo il link alla
    guida»*. E aveva ragione lì: da quando in cima c'è il riassunto delle tipologie, i tre passi del
    riquadro ripetevano con altre parole quello che si era appena letto — e due spiegazioni della
    stessa cosa nella stessa schermata non sono il doppio della chiarezza, sono il dubbio di non aver
    capito la prima. Dove il riquadro è l'unica spiegazione resta dov'è.
    """
    link = QLabel(_("<a href='#' style='color:{info}; text-decoration:none;'>📖 Guida completa: {heading} →</a>",
                    info=T.INFO, heading=heading))
    link.setTextFormat(Qt.TextFormat.RichText)
    link.setCursor(Qt.CursorShape.PointingHandCursor)
    link.setStyleSheet("background:transparent; border:none;")
    link.linkActivated.connect(lambda _h: open_guide(parent_dialog or link.window(), heading))
    return link


def help_box(steps: list[str], heading: str, parent_dialog=None, title: str | None = None) -> QFrame:
    """Riquadro con i passi essenziali (uno per riga, numerati) e il link alla guida completa."""
    box = QFrame()
    box.setObjectName("helpBox")
    box.setStyleSheet(f"#helpBox {{ background:#1E2938; border:1px solid {T.LINE}; "
                      f"border-left:4px solid {T.INFO}; border-radius:8px; }}")
    lay = QVBoxLayout(box)
    lay.setContentsMargins(14, 10, 14, 10)
    lay.setSpacing(6)
    head = QLabel(title or _('Come fare'))
    head.setStyleSheet(f"font-weight:700; color:{T.TEXT}; background:transparent; border:none;")
    lay.addWidget(head)
    for i, step in enumerate(steps, 1):
        row = QHBoxLayout()
        row.setSpacing(8)
        num = QLabel(f"{i}.")
        num.setFixedWidth(18)
        num.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
        num.setStyleSheet(f"color:{T.INFO}; font-weight:700; background:transparent; border:none;")
        text = QLabel(step)
        text.setWordWrap(True)
        text.setTextFormat(Qt.TextFormat.RichText)
        text.setStyleSheet(f"color:{T.TEXT}; background:transparent; border:none;")
        row.addWidget(num, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(text, 1)
        lay.addLayout(row)
    link = QLabel(_("<a href='#' style='color:{info}; text-decoration:none;'>📖 Guida completa: {heading} →</a>", info=T.INFO, heading=heading))
    link.setTextFormat(Qt.TextFormat.RichText)
    link.setCursor(Qt.CursorShape.PointingHandCursor)
    link.setStyleSheet("background:transparent; border:none; margin-top:4px;")
    link.linkActivated.connect(lambda _h: open_guide(parent_dialog or box.window(), heading))
    lay.addWidget(link)
    return box
