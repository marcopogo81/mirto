# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Elenchi "a schede" per le Impostazioni: una scheda per elemento, selezione ben visibile, pulsanti sulla riga.

Più chiaro di una lista di testo: la scheda selezionata ha bordo e fondo azzurri, ogni riga ha il suo pallino di
stato e i suoi pulsanti (es. Modifica, Elimina), quindi non serve "seleziona e poi premi in fondo".
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLayout, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from . import theme as T
from ..i18n import _

CARD_STYLE = f"""
QFrame#card {{ background: rgba(255,255,255,0.035); border: 1px solid {T.LINE}; border-radius: 10px; }}
QFrame#card:hover {{ border: 1px solid #4A5D78; }}
QFrame#card[selected="true"] {{ background: rgba(91,155,240,0.18); border: 2px solid {T.INFO}; }}
QFrame#cardProposal {{ background: transparent; border: 1px dashed #5C6F8A; border-radius: 10px; }}
QFrame#cardProposal:hover {{ border: 1px dashed {T.INFO}; }}
QFrame#cardAdd {{ background: transparent; border: 1px dashed {T.LINE}; border-radius: 10px; }}
QFrame#cardAdd:hover {{ border: 1px dashed {T.INFO}; }}
QPushButton#cardGo {{ padding: 5px 12px; font-size: {T.SIZE_SMALL}px; font-weight: 700; text-align: center;
                      border-radius: 8px; background: {T.ARMED}; color: #FFFFFF; }}
QPushButton#cardGo:hover {{ background: #27a073; }}
QLabel#cardTitle {{ font-weight: 700; font-size: {T.SIZE_BODY}px; background: transparent; }}
QLabel#cardSub {{ color: {T.MUTED}; font-size: {T.SIZE_SMALL}px; background: transparent; }}
QPushButton#cardBtn {{ padding: 5px 10px; font-size: {T.SIZE_SMALL}px; font-weight: 600; text-align: center;
                       border-radius: 8px; }}
QPushButton#cardDanger {{ padding: 5px 10px; font-size: {T.SIZE_SMALL}px; font-weight: 600; text-align: center;
                          border-radius: 8px; background: transparent; color: {T.ALARM}; border: 1px solid {T.ALARM}; }}
QPushButton#cardDanger:hover {{ background: rgba(229,72,77,0.15); }}
"""


class _Card(QFrame):
    clicked = Signal()
    double_clicked = Signal()

    def mousePressEvent(self, ev):  # noqa: N802
        if ev.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(ev)

    def mouseDoubleClickEvent(self, ev):  # noqa: N802
        self.double_clicked.emit()
        super().mouseDoubleClickEvent(ev)


class CardList(QWidget):
    """Righe: dict con ``title``, ``sub`` (HTML), ``dot`` (colore o None), ``buttons`` [(testo, funzione, pericolo)]."""

    current_changed = Signal(int)
    activated = Signal(int)

    def __init__(self, empty_text: str = "", selectable: bool = True, accordion: bool = False,
                 compatta: bool = False, parent=None):
        super().__init__(parent)
        self.selectable = selectable
        self.accordion = accordion   # con un ramo: clic sulla scheda (o sulla freccia) lo apre e lo chiude
        # Righe basse, col sottotitolo **accanto** al titolo invece che sotto: per un elenco che si
        # scorre — i suoni di un nodo, che sono una decina — l'altezza doppia vuol dire metterne
        # cinque in una pagina invece di dieci. Marco, 25/10: *«fai più corti gli item della lista»*.
        self.compatta = compatta
        self.expanded = True
        self._chevrons: list[QPushButton] = []
        self.empty_text = empty_text
        self._cards: list[_Card] = []
        self._detail: QWidget | None = None  # ramo mostrato sotto la scheda selezionata (es. azioni del nodo)
        self._subs: list[QLabel] = []
        self._dots: list[QLabel] = []
        self._current = -1
        self.setStyleSheet(CARD_STYLE)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(6)
        self._empty = QLabel(empty_text)
        self._empty.setWordWrap(True)
        self._empty.setStyleSheet(f"color:{T.MUTED}; padding:10px; border:1px dashed {T.LINE}; border-radius:10px;")
        self._lay.addWidget(self._empty)

    # --- contenuto ---------------------------------------------------------------
    def set_detail(self, widget: QWidget) -> None:
        """Ramo dell'albero che compare subito sotto la scheda selezionata."""
        self._detail = widget
        self._place_detail()

    def toggle(self, i: int) -> None:
        """Accordion: la scheda già selezionata si apre/chiude, un'altra scheda si seleziona aperta."""
        if i == self._current:
            self.expanded = not self.expanded
            self._place_detail()
        else:
            self.expanded = True
            self.set_current_row(i)

    def _place_detail(self) -> None:
        for k, ch in enumerate(self._chevrons):
            open_ = k == self._current and self.expanded
            ch.setIcon(T.icon("chevron-down" if open_ else "chevron-right", T.TEXT if open_ else T.MUTED, 18))
            ch.setToolTip(_("Chiudi") if open_ else _("Apri: azioni e camere"))
        if self._detail is None:
            return
        self._lay.removeWidget(self._detail)
        if 0 <= self._current < len(self._cards) and (self.expanded or not self.accordion):
            self._lay.insertWidget(self._current + 2, self._detail)  # 0 = testo "vuoto", poi le schede
            self._detail.show()
        else:
            self._detail.hide()

    def set_rows(self, rows: list[dict]) -> None:
        keep = self._current
        if self._detail is not None:
            self._lay.removeWidget(self._detail)
        for c in self._cards:
            c.setParent(None)
            c.deleteLater()
        self._cards, self._subs, self._dots, self._chevrons = [], [], [], []
        for i, row in enumerate(rows):
            self._cards.append(self._make(i, row))
            self._lay.addWidget(self._cards[-1])
        self._empty.setVisible(not rows)
        self._current = -1
        self._place_detail()
        if rows and self.selectable:
            self.set_current_row(min(max(keep, 0), len(rows) - 1), emit=keep != min(max(keep, 0), len(rows) - 1))
        elif keep != -1:
            self.current_changed.emit(-1)

    def update_row(self, i: int, sub: str | None = None, dot: str | None = None) -> None:
        """Aggiorna stato e sottotitolo di una riga senza ricostruire l'elenco (niente sfarfallio)."""
        if 0 <= i < len(self._cards):
            if sub is not None and self._subs[i].text() != sub:
                self._subs[i].setText(sub)
            if dot is not None:
                self._dots[i].setStyleSheet(f"color:{dot}; font-size:14px; background:transparent;")

    @staticmethod
    def _cresce(etichetta: QLabel) -> QLabel:
        """Etichetta che va a capo e la cui altezza dipende dalla larghezza.

        Senza questo Qt calcola l'altezza della scheda come se il testo stesse su una riga: un titolo
        lungo va a capo e finisce fuori dalla scheda, tagliato a metà. Visto il 21/10 sull'azione
        «Suoni contro uccelli e animali (rapace, sparo, gracchio…) – Studio», col sottotitolo mozzato.
        """
        etichetta.setWordWrap(True)
        pol = etichetta.sizePolicy()
        pol.setHeightForWidth(True)
        pol.setVerticalPolicy(QSizePolicy.Policy.Minimum)
        etichetta.setSizePolicy(pol)
        etichetta.setMinimumHeight(0)
        return etichetta

    def _make(self, i: int, row: dict) -> _Card:
        card = _Card()
        card.setObjectName({"proposal": "cardProposal", "add": "cardAdd"}.get(row.get("style", ""), "card"))
        card.setProperty("selected", False)
        card.setCursor(Qt.CursorShape.PointingHandCursor if self.selectable else Qt.CursorShape.ArrowCursor)
        # la scheda si adatta in altezza a quello che contiene, invece di tagliarlo
        pol = card.sizePolicy()
        pol.setHeightForWidth(True)
        pol.setVerticalPolicy(QSizePolicy.Policy.Minimum)
        card.setSizePolicy(pol)
        lay = QHBoxLayout(card)
        lay.setContentsMargins(12, 4, 8, 4) if self.compatta else lay.setContentsMargins(12, 9, 10, 9)
        lay.setSpacing(8 if self.compatta else 10)
        lay.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        dot = QLabel("●")
        dot.setStyleSheet(f"color:{row.get('dot') or T.FAINT}; font-size:14px; background:transparent;")
        dot.setVisible(row.get("dot") is not None)
        # In alto quando il testo va su due righe; in mezzo quando la riga e' una sola, se no il
        # pallino resta appeso al bordo di sopra di una riga alta venti pixel.
        lay.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter if self.compatta else Qt.AlignmentFlag.AlignTop)
        col = QHBoxLayout() if self.compatta else QVBoxLayout()
        col.setSpacing(6 if self.compatta else 2)
        title = QLabel(row.get("title", ""))
        title.setObjectName("cardTitle")
        title.setTextFormat(Qt.TextFormat.RichText)
        if row.get("style") in ("proposal", "add"):
            title.setStyleSheet(f"color:{T.MUTED};")
        self._cresce(title)
        sub = QLabel(row.get("sub", ""))
        sub.setObjectName("cardSub")
        self._cresce(sub)
        sub.setTextFormat(Qt.TextFormat.RichText)
        sub.setVisible(bool(row.get("sub")) or "sub" in row)
        col.addWidget(title)
        col.addWidget(sub)
        if self.compatta:
            col.addStretch(1)      # titolo e sottotitolo a sinistra, il resto della riga vuoto
        lay.addLayout(col, 1)
        if self.accordion:
            ch = QPushButton()
            ch.setObjectName("cardBtn")
            ch.setFixedSize(30, 30)
            ch.setCursor(Qt.CursorShape.PointingHandCursor)
            ch.setStyleSheet("QPushButton { background: transparent; padding: 0; }"
                             "QPushButton:hover { background: rgba(255,255,255,0.08); }")
            ch.clicked.connect(lambda _c=False, k=i: self.toggle(k))
            lay.insertWidget(0, ch, 0, Qt.AlignmentFlag.AlignVCenter)
            self._chevrons.append(ch)
        if row.get("icon"):
            ic = QLabel()
            ic.setPixmap(T.icon_pixmap(row["icon"], row.get("icon_color", T.MUTED), 18))
            ic.setStyleSheet("background:transparent;")
            lay.insertWidget(0, ic, 0, Qt.AlignmentFlag.AlignTop)
        for voce in row.get("buttons", []):
            # (testo, azione, pericoloso) oppure (testo, azione, pericoloso, "icona"): col nome di
            # un'icona il pulsante diventa la sola icona, e il testo resta come suggerimento. Serve
            # per le cose che stanno accanto a un nome e non devono rubargli la riga.
            text, fn = voce[0], voce[1]
            danger = voce[2] if len(voce) > 2 else False
            icona = voce[3] if len(voce) > 3 else ""
            b = QPushButton("" if icona else text)
            b.setObjectName({True: "cardDanger", "go": "cardGo"}.get(danger, "cardBtn"))
            if icona:
                b.setIcon(T.icon(icona, T.MUTED, 16))
                b.setToolTip(text)
                b.setFixedSize(30, 30)
            b.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _c=False, f=fn, k=i: (self.set_current_row(k), f()))
            lay.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)
        card.clicked.connect(lambda k=i: self.toggle(k) if self.accordion else self.set_current_row(k))
        card.double_clicked.connect(lambda k=i: self.activated.emit(k))
        self._subs.append(sub)
        self._dots.append(dot)
        return card

    # --- selezione ---------------------------------------------------------------
    def count(self) -> int:
        return len(self._cards)

    def current_row(self) -> int:
        return self._current

    def set_current_row(self, i: int, emit: bool = True) -> None:
        if not self.selectable or not (0 <= i < len(self._cards)):
            return
        changed = i != self._current
        self._current = i
        for k, c in enumerate(self._cards):
            c.setProperty("selected", k == i)
            c.style().unpolish(c)
            c.style().polish(c)
        self._place_detail()
        if changed and emit:
            self.current_changed.emit(i)


def primary_button(text: str, color: str = T.INFO, tall: bool = True) -> QPushButton:
    """Pulsante principale colorato (es. "Cerca nodi in rete")."""
    b = QPushButton(text)
    pad = "14px 18px" if tall else "8px 14px"
    b.setStyleSheet(f"QPushButton {{ background:{color}; color:#FFFFFF; font-weight:700; padding:{pad}; "
                    f"text-align:center; border-radius:10px; font-size:{T.SIZE_BODY + (1 if tall else 0)}px; }}"
                    f"QPushButton:hover {{ background:{color}; border:2px solid #FFFFFF; }}"
                    f"QPushButton:disabled {{ background:#3A4A60; color:{T.MUTED}; }}")
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def settings_style() -> str:
    """Selezioni ben visibili in tutta la finestra Impostazioni (schede, elenchi, caselle, menu a tendina)."""
    check = (T.ICONS / "check-white.svg").as_posix()
    return f"""
/* Le schede si devono vedere come schede anche da ferme (prima erano trasparenti: si capiva che
   fossero cliccabili solo col mouse sopra) e si devono vedere ATTACCATE al pannello che aprono:
   linguette con gli angoli alti arrotondati, appoggiate sul bordo del pannello, non chip che
   galleggiano per conto loro. E un po' di aria sopra, per non toccare il testo che le precede.
   Tutt'e due chieste da Marco il 21/10, guardando la pagina dei nodi. */
QTabWidget::pane {{ border: 1px solid {T.LINE}; border-radius: 10px; top: -1px;
                   background: rgba(255,255,255,0.02); }}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{ background: rgba(255,255,255,0.05); color: {T.MUTED}; padding: 9px 16px;
               margin-top: 8px; margin-right: 4px; border: 1px solid {T.LINE}; border-bottom: none;
               border-top-left-radius: 9px; border-top-right-radius: 9px; font-weight: 600; }}
QTabBar::tab:hover {{ background: rgba(255,255,255,0.10); color: {T.TEXT}; border-color: #4A5D78; }}
QTabBar::tab:selected {{ background: {T.INFO}; color: #FFFFFF; border-color: {T.INFO};
                        margin-top: 4px; padding-bottom: 11px; }}
QListWidget::item {{ padding: 8px 10px; margin: 3px 0; border-radius: 9px; border: 1px solid {T.LINE};
                    background: rgba(255,255,255,0.035); }}
QListWidget::item:hover {{ border: 1px solid #4A5D78; }}
QListWidget::item:selected {{ background: rgba(91,155,240,0.22); border: 2px solid {T.INFO}; color: {T.TEXT}; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 18px; height: 18px; border: 2px solid #5C6F8A;
                                                background: {T.RAISED}; }}
QCheckBox::indicator {{ border-radius: 5px; }}
QRadioButton::indicator {{ border-radius: 11px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {T.INFO}; }}
QCheckBox::indicator:checked {{ background: {T.INFO}; border-color: {T.INFO}; image: url({check}); }}
QRadioButton::indicator:checked {{ background: {T.INFO}; border-color: #FFFFFF; }}
QComboBox QAbstractItemView::item:selected {{ background: {T.INFO}; color: #FFFFFF; }}
"""


class Branch(QFrame):
    """Ramo dell'albero: linea verticale a sinistra, intestazione con icona (es. ⚡ Azioni) e foglie sotto."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("branch")
        self.setStyleSheet(f"QFrame#branch {{ border-left: 2px solid {T.LINE}; margin-left: 16px; }}")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(14, 4, 0, 6)
        self.lay.setSpacing(6)

    def header(self, icon: str, text: str, color: str = T.INFO, collapsible: bool = False) -> QLabel:
        """Intestazione del ramo; con ``collapsible`` un clic (o la freccia) apre e chiude il contenuto."""
        head = _Card()
        head.setStyleSheet("background: transparent;")
        row = QHBoxLayout(head)
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(8)
        ic = QLabel()
        ic.setPixmap(T.icon_pixmap(icon, color, 18))
        lab = QLabel(text)
        lab.setTextFormat(Qt.TextFormat.RichText)
        lab.setStyleSheet("font-weight:700; font-size:15px;")
        if collapsible:
            self._open = True
            self._chev = QLabel()
            self._chev.setPixmap(T.icon_pixmap("chevron-down", T.TEXT, 16))
            row.addWidget(self._chev)
            head.setCursor(Qt.CursorShape.PointingHandCursor)
            head.setToolTip(_('Clic per aprire o chiudere'))
            head.clicked.connect(self.toggle)
        row.addWidget(ic)
        row.addWidget(lab, 1)
        self.lay.addWidget(head)
        self._body = QWidget()
        self._body_lay = QVBoxLayout(self._body)
        self._body_lay.setContentsMargins(0, 0, 0, 0)
        self._body_lay.setSpacing(6)
        self.lay.addWidget(self._body)
        self.lay = self._body_lay  # ciò che si aggiunge dopo l'intestazione sta nel contenuto apribile
        return lab

    def toggle(self) -> None:
        self._open = not self._open
        self._body.setVisible(self._open)
        self._chev.setPixmap(T.icon_pixmap("chevron-down" if self._open else "chevron-right",
                                           T.TEXT if self._open else T.MUTED, 16))
