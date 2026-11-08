# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Tema "centrale notturna": colori, caratteri, icone e foglio di stile dell'interfaccia.

Regole del tema
* Fondo blu-ardesia scuro: i video risaltano e di notte lo schermo non abbaglia.
* Il colore è riservato allo STATO (protetta, in pausa, non protetta, allarme):
  tutto il resto è neutro, così lo stato si legge a colpo d'occhio.
* Niente bordi annidati: le aree si distinguono per fondo e spazio.
* Icone disegnate (Lucide, licenza ISC) al posto delle emoji.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer

from .colori import (  # noqa: F401  (ripresi qui: tutto il programma li chiede a «theme»)
    ALARM, ARMED, BG, DISARMED, FAINT, INFO, LINE, MUTED, OBJECT, PANEL, PAUSED, RAISED, SIZE_BODY,
    SIZE_SMALL, SIZE_STATE, SIZE_TITLE, STATE_COLORS, TEXT)

ICONS = Path(__file__).resolve().parent / "icons"


@lru_cache(maxsize=256)
def icon_pixmap(name: str, color: str = TEXT, size: int = 20) -> QPixmap:
    """Icona Lucide ricolorata. Pixmap ad alta definizione per gli schermi Retina."""
    path = ICONS / f"{name}.svg"
    scale = 2
    pm = QPixmap(size * scale, size * scale)
    pm.fill(Qt.GlobalColor.transparent)
    if path.exists():
        data = path.read_text(encoding="utf-8").replace("currentColor", color)
        renderer = QSvgRenderer(QByteArray(data.encode("utf-8")))
        p = QPainter(pm)
        renderer.render(p, QRectF(0, 0, size * scale, size * scale))
        p.end()
    pm.setDevicePixelRatio(scale)
    return pm


def icon(name: str, color: str = TEXT, size: int = 20) -> QIcon:
    return QIcon(icon_pixmap(name, color, size))


def palette() -> QPalette:
    """Palette scura per lo stile Fusion: rende coerenti anche finestre di dialogo e menu."""
    p = QPalette()
    roles = {
        QPalette.ColorRole.Window: BG, QPalette.ColorRole.WindowText: TEXT,
        QPalette.ColorRole.Base: RAISED, QPalette.ColorRole.AlternateBase: PANEL,
        QPalette.ColorRole.Text: TEXT, QPalette.ColorRole.Button: RAISED,
        QPalette.ColorRole.ButtonText: TEXT, QPalette.ColorRole.ToolTipBase: RAISED,
        QPalette.ColorRole.ToolTipText: TEXT, QPalette.ColorRole.Highlight: INFO,
        QPalette.ColorRole.HighlightedText: "#FFFFFF", QPalette.ColorRole.PlaceholderText: FAINT,
        QPalette.ColorRole.Link: INFO,
    }
    for role, color in roles.items():
        p.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(FAINT))
    return p


CHEV_DOWN = (ICONS / "ui-chevron-down.svg").as_posix()
CHEV_UP = (ICONS / "ui-chevron-up.svg").as_posix()
CHECK = (ICONS / "check-white.svg").as_posix()

STYLE = f"""
* {{ font-size: {SIZE_BODY}px; }}
QMainWindow, QDialog {{ background: {BG}; }}
QToolTip {{ background: {RAISED}; color: {TEXT}; border: 1px solid {LINE}; padding: 6px; font-size: {SIZE_SMALL}px; }}

/* pannelli */
#rail, #timeline {{ background: {PANEL}; border-radius: 14px; }}
#sectionTitle {{ color: {MUTED}; font-size: {SIZE_SMALL}px; font-weight: 600; }}
#console {{ background: #0B1017; border: 1px solid {LINE}; border-radius: 10px; }}
#muted {{ color: {MUTED}; }}
#faint {{ color: {FAINT}; font-size: {SIZE_SMALL}px; }}

/* pulsanti: piatti, in rilievo leggero; il colore solo per l'azione principale */
QPushButton {{
    background: {RAISED}; color: {TEXT}; border: none; border-radius: 10px;
    padding: 10px 14px; font-weight: 600; text-align: left;
}}
QPushButton:hover {{ background: #364862; }}
QPushButton:pressed {{ background: #22303f; }}
QPushButton:disabled {{ color: {FAINT}; background: #263244; }}
/* L'azione principale si vede da lontano: in un wizard «Avanti» e' quello che si cerca, e un
   pulsante grigio come gli altri lo si cerca ogni volta (Marco, 24/10). */
QPushButton#primario {{ background: {INFO}; color: #FFFFFF; text-align: center; }}
QPushButton#primario:hover {{ background: #74ACF4; }}
QPushButton#primario:pressed {{ background: #4A86D8; }}
QPushButton#primario:disabled {{ background: #2B3B52; color: {FAINT}; }}
QPushButton#danger {{ background: transparent; color: {ALARM}; border: 1px solid {ALARM}; }}
QPushButton#danger:hover {{ background: rgba(229,72,77,0.15); }}
/* La tendina «AI» dentro un riquadro: deve stare in venti pixel e non rubare l'occhio al nome. */
QComboBox#minuta {{ padding: 1px 4px; min-height: 18px; font-size: 10px; border-radius: 6px; }}
QComboBox#minuta::drop-down {{ width: 14px; }}
QPushButton#small {{ padding: 6px 10px; font-size: {SIZE_SMALL}px; font-weight: 500; text-align: center; }}
QPushButton#seg {{ background: transparent; color: {MUTED}; padding: 8px 14px; border-radius: 10px; font-size: {SIZE_BODY + 1}px; }}
QPushButton#seg:hover {{ background: rgba(255,255,255,0.05); color: {TEXT}; }}
QPushButton#seg:checked {{ background: {PANEL}; color: {TEXT}; }}
#camCard {{ background: {PANEL}; border-radius: 12px; }}
QScrollArea {{ background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QPushButton::menu-indicator {{ subcontrol-position: right center; right: 10px; }}

/* campi e controlli */
QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateEdit, QTimeEdit {{
    background: {RAISED}; color: {TEXT}; border: 1px solid {LINE}; border-radius: 8px;
    padding: 5px 8px; min-height: 24px; selection-background-color: {INFO};
}}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QDateEdit:focus, QTimeEdit:focus {{ border: 1px solid {INFO}; }}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QDateEdit:hover, QTimeEdit:hover {{
    border: 1px solid #5C6F8A; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{ color: {FAINT}; }}
/* menu a tendina: zona della freccia separata e freccia chiara, ben visibile */
QComboBox {{ padding-right: 34px; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 28px;
    border-left: 1px solid {LINE}; border-top-right-radius: 8px; border-bottom-right-radius: 8px;
    background: rgba(255,255,255,0.06); }}
QComboBox::drop-down:hover {{ background: rgba(91,155,240,0.25); }}
QComboBox::down-arrow {{ image: url("{CHEV_DOWN}"); width: 14px; height: 14px; }}
QComboBox::down-arrow:on {{ image: url("{CHEV_UP}"); }}
QComboBox QAbstractItemView {{ background: {RAISED}; color: {TEXT}; selection-background-color: {INFO};
    border: 1px solid {LINE}; padding: 4px; outline: none; }}
/* numeri, date e ore: pulsanti su/giù visibili a destra */
QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit {{ padding-right: 30px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button, QDateEdit::up-button, QTimeEdit::up-button {{
    subcontrol-origin: border; subcontrol-position: top right; width: 26px;
    border-left: 1px solid {LINE}; border-bottom: 1px solid {LINE}; border-top-right-radius: 8px;
    background: rgba(255,255,255,0.06); }}
QSpinBox::down-button, QDoubleSpinBox::down-button, QDateEdit::down-button, QTimeEdit::down-button {{
    subcontrol-origin: border; subcontrol-position: bottom right; width: 26px;
    border-left: 1px solid {LINE}; border-bottom-right-radius: 8px; background: rgba(255,255,255,0.06); }}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover, QDateEdit::up-button:hover, QTimeEdit::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover, QDateEdit::down-button:hover,
QTimeEdit::down-button:hover {{ background: rgba(91,155,240,0.25); }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow, QDateEdit::up-arrow, QTimeEdit::up-arrow {{
    image: url("{CHEV_UP}"); width: 11px; height: 11px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow, QDateEdit::down-arrow, QTimeEdit::down-arrow {{
    image: url("{CHEV_DOWN}"); width: 11px; height: 11px; }}
QDateEdit::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 26px;
    border-left: 1px solid {LINE}; background: rgba(255,255,255,0.06); }}
QCheckBox {{ spacing: 8px; }}
/* caselle di spunta e scelte: bordo chiaro, spunta bianca su azzurro (in tutte le finestre) */
QCheckBox::indicator, QRadioButton::indicator {{ width: 18px; height: 18px; border: 2px solid #6B7F9C;
    background: {RAISED}; }}
QCheckBox::indicator {{ border-radius: 5px; }}
QRadioButton::indicator {{ border-radius: 11px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {INFO}; }}
QCheckBox::indicator:checked {{ background: {INFO}; border-color: {INFO}; image: url("{CHECK}"); }}
QRadioButton::indicator:checked {{ background: {INFO}; border-color: #FFFFFF; }}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{ border-color: #3A4A60; background: #1E2836; }}
QTabWidget::pane {{ border: none; background: {PANEL}; border-radius: 12px; padding: 8px; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 8px 14px; border: none; font-weight: 600; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {INFO}; }}
QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ border-radius: 10px; margin: 2px 0; }}
QListWidget::item:selected {{ background: {RAISED}; }}
QListWidget::item:hover {{ background: rgba(255,255,255,0.04); }}
QMenu {{ background: {RAISED}; color: {TEXT}; border: 1px solid {LINE}; padding: 6px; }}
QMenu::item {{ padding: 8px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {INFO}; color: #FFFFFF; }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: 6px 8px; }}
QMenuBar {{ background: {BG}; color: {TEXT}; }}
QSplitter::handle {{ background: transparent; width: 10px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {RAISED}; border-radius: 5px; min-height: 30px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {RAISED}; border-radius: 5px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; border: none; background: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
QStatusBar {{ color: {MUTED}; }}
QMessageBox QLabel {{ color: {TEXT}; }}
"""
