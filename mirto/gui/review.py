# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Vista «Rivedi»: le registrazioni di una camera giorno per giorno.

* barra delle 24 ore: tratti registrati, eventi colorati (persona rosso, animali e oggetti viola, suoni azzurro),
  buchi vuoti; clic per andare a quell'ora, rotellina per ingrandire;
* lettore (Qt Multimedia) che passa da solo allo spezzone successivo, velocità 1×–16×;
* evento precedente/successivo, ±10 s, foto del fotogramma, esportazione di un intervallo in MP4.

La logica senza interfaccia sta in ``recording/timeline.py``.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from pathlib import Path

from PySide6.QtCore import QDate, QPointF, QRectF, QSize, Qt, QTime, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTimeEdit,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from . import theme as T
from ..i18n import _, questo_computer
from ..recording import timeline as TL
from ..recording.manager import RemoteSegment

log = logging.getLogger(__name__)

EVENT_COLORS = {"person": T.ALARM, "other": T.OBJECT, "sound": T.INFO}
NODE_COLOR = "#2BA39A"   # tratti registrati solo sul nodo
SPEEDS = (1, 2, 4, 8, 16)
MIN_WINDOW = 3 * 60       # ingrandimento massimo: 3 minuti sulla barra
DRAG_PX = 6               # oltre questo spostamento il clic diventa una selezione da ingrandire
PRE_EVENT = 5             # andando a un evento si parte 5 s prima


class TimelineBar(QWidget):
    """Barra della giornata.

    * clic: ``seek(t)`` (su un evento: 5 s prima dell'evento);
    * trascinamento: seleziona un intervallo e lo ingrandisce; doppio clic o Esc tornano allo zoom precedente;
    * rotellina: ingrandisce attorno al mouse; Maiusc + rotellina (o rotellina orizzontale) scorre.
    """

    seek = Signal(float)
    zoom_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(122)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.day0 = self.day1 = 0.0          # giornata intera
        self.t0 = self.t1 = 0.0              # finestra visibile (ingrandimento)
        self.spans: list[tuple[float, float]] = []
        self.node_spans: list[tuple[float, float]] = []  # registrato solo sul nodo (si scarica al clic)
        self.events: list[dict] = []
        self.cursor_t: float | None = None
        self._press_x: float | None = None
        self._selecting = False
        self._sel: tuple[float, float] | None = None   # selezione in corso (x iniziale, x attuale)
        self._history: list[tuple[float, float]] = []  # zoom precedenti, per tornare indietro
        self._pills: list[tuple[QRectF, object]] = []  # pulsanti disegnati nella barra (zoom indietro, 24 ore)
        self._eventi_versione = 0        # cambia quando arrivano eventi nuovi
        self._gruppi: tuple | None = None  # (chiave, gruppi): vedi _gruppi_eventi()

    # --- dati -------------------------------------------------------------------
    def set_day(self, t0: float, t1: float) -> None:
        self.day0, self.day1 = t0, t1
        self.t0, self.t1 = t0, t1
        self._history.clear()
        self.update()
        self.zoom_changed.emit()

    def set_data(self, spans: list[tuple[float, float]], events: list[dict],
                 node_spans: list[tuple[float, float]] | None = None) -> None:
        self.spans, self.events = spans, events
        self.node_spans = node_spans or []
        self._eventi_versione += 1
        self.update()

    def set_cursor(self, t: float | None) -> None:
        self.cursor_t = t
        if t is not None and self.zoomed and not (self.t0 <= t <= self.t1):
            # la riproduzione esce dalla parte ingrandita: la finestra la segue (senza cambiare ingrandimento)
            span = self.t1 - self.t0
            t0 = t - span * 0.1 if t > self.t1 else t - span * 0.9
            self._set_window(t0, t0 + span)
        self.update()

    @property
    def zoomed(self) -> bool:
        return (self.t1 - self.t0) < (self.day1 - self.day0) - 1

    def _set_window(self, t0: float, t1: float) -> None:
        span = min(self.day1 - self.day0, max(MIN_WINDOW, t1 - t0))
        t0 = min(max(t0, self.day0), self.day1 - span)
        self.t0, self.t1 = t0, t0 + span
        self.update()
        self.zoom_changed.emit()

    def zoom_to(self, t0: float, t1: float) -> None:
        """Ingrandisce su [t0, t1], ricordando lo zoom attuale per poter tornare indietro."""
        self._history.append((self.t0, self.t1))
        self._set_window(t0, t1)

    def zoom_back(self) -> None:
        """Torna allo zoom precedente (o alla giornata intera)."""
        if self._history:
            self._set_window(*self._history.pop())
        else:
            self.reset_zoom()

    def reset_zoom(self) -> None:
        self._history.clear()
        self._set_window(self.day0, self.day1)

    def window_text(self) -> str:
        return f"{TL.fmt_clock(self.t0, seconds=False)} – {TL.fmt_clock(self.t1, seconds=False)}"

    # --- geometria --------------------------------------------------------------
    def _track(self) -> QRectF:
        return QRectF(8, 52, max(10, self.width() - 16), 46)

    def x_of(self, t: float) -> float:
        r = self._track()
        span = max(1.0, self.t1 - self.t0)
        return r.left() + (t - self.t0) / span * r.width()

    def t_of(self, x: float) -> float:
        r = self._track()
        frac = min(1.0, max(0.0, (x - r.left()) / r.width()))
        return self.t0 + frac * (self.t1 - self.t0)

    def event_at(self, x: float) -> dict | None:
        near = [e for e in self.events if self.t0 <= e["ts"] <= self.t1 and abs(self.x_of(e["ts"]) - x) <= 5]
        return min(near, key=lambda e: abs(self.x_of(e["ts"]) - x)) if near else None

    # --- disegno ----------------------------------------------------------------
    def paintEvent(self, _ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self._track()
        # pista vuota (niente registrazione) e futuro tratteggiato
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#151D28"))
        p.drawRoundedRect(r, 6, 6)
        now = time.time()
        if self.t0 < now < self.t1:
            fut = QRectF(self.x_of(now), r.top(), r.right() - self.x_of(now), r.height())
            p.setBrush(QColor(255, 255, 255, 10))
            p.drawRect(fut)
        # tratti registrati
        for color, spans in ((NODE_COLOR, self.node_spans), ("#4F7DB3", self.spans)):
            self._paint_spans(p, r, QColor(color), spans)
        # tacche delle ore
        self._paint_rest(p, r)

    def _paint_spans(self, p: QPainter, r: QRectF, color: QColor, spans) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        for a, b in spans:
            if b < self.t0 or a > self.t1:
                continue
            xa, xb = self.x_of(max(a, self.t0)), self.x_of(min(b, self.t1))
            p.drawRoundedRect(QRectF(xa, r.top() + 6, max(3.0, xb - xa), r.height() - 12), 3, 3)

    def _gruppi_eventi(self) -> list[tuple]:
        """I puntini da disegnare, calcolati una volta per ogni disegno diverso.

        La posizione dei puntini dipende solo da finestra visibile, larghezza e eventi: finché quelle
        tre cose non cambiano il risultato è identico, e rifarlo a ogni ridisegno — dieci volte al
        secondo mentre il video scorre — era la lentezza che si sentiva con una giornata piena.
        """
        chiave = (self.t0, self.t1, self.width(), self._eventi_versione)
        if self._gruppi is not None and self._gruppi[0] == chiave:
            return self._gruppi[1]
        gruppi = TL.group_events(self.events, self.t0, self.t1, self.x_of)
        self._gruppi = (chiave, gruppi)
        return gruppi

    def _paint_rest(self, p: QPainter, r: QRectF) -> None:
        # tacche delle ore
        span = self.t1 - self.t0
        step = (3 * 3600 if span > 12 * 3600 else 3600 if span > 3 * 3600 else 900 if span > 3600
                else 300 if span > 20 * 60 else 60)
        k = int((self.t0 - self.day0) // step) + 1  # tacche contate dalla mezzanotte locale, non dall'UTC
        first = self.day0 + k * step
        font = QFont(self.font())
        font.setPixelSize(11)
        p.setFont(font)
        t = self.t0 if self.t0 == self.day0 else first
        while t <= self.t1 + 1:
            x = self.x_of(t)
            p.setPen(QPen(QColor(T.LINE), 1))
            p.drawLine(QPointF(x, r.bottom()), QPointF(x, r.bottom() + 5))
            p.setPen(QColor(T.MUTED))
            p.drawText(QRectF(x - 30, r.bottom() + 5, 60, 14), Qt.AlignmentFlag.AlignCenter,
                       TL.fmt_clock(t, seconds=False))
            t += step
        # eventi: puntino colorato sopra la pista e una linea sottile dentro; eventi vicini (meno di 4 px) diventano
        # un solo puntino un po' più grande, così la barra non si riempie di blocchi
        for x, kind, quanti in self._gruppi_eventi():
            color = QColor(EVENT_COLORS[kind])
            line = QColor(color)
            line.setAlpha(170)
            p.setPen(QPen(line, 1.2))
            p.drawLine(QPointF(x, r.top() + 2), QPointF(x, r.bottom() - 2))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            radius = 3.5 if quanti == 1 else 5
            p.drawEllipse(QPointF(x, r.top() - 6), radius, radius)
        # selezione da ingrandire (trascinamento)
        if self._sel is not None:
            a, b = sorted(self._sel)
            band = QRectF(a, r.top() - 12, b - a, r.height() + 14)
            p.setPen(QPen(QColor(T.INFO), 1))
            fill = QColor(T.INFO)
            fill.setAlpha(60)
            p.setBrush(fill)
            p.drawRect(band)
            p.setPen(QColor("#FFFFFF"))
            p.drawText(QRectF(a, r.bottom() - 18, max(b - a, 120), 16), Qt.AlignmentFlag.AlignCenter,
                       f"{TL.fmt_clock(self.t_of(a), seconds=False)} – {TL.fmt_clock(self.t_of(b), seconds=False)}")
        # cursore di riproduzione con l'ora
        if self.cursor_t is not None and self.t0 <= self.cursor_t <= self.t1:
            x = self.x_of(self.cursor_t)
            p.setPen(QPen(QColor("#FFFFFF"), 2))
            p.drawLine(QPointF(x, r.top() - 2), QPointF(x, r.bottom() + 2))
            label = TL.fmt_clock(self.cursor_t)
            w = 64
            box = QRectF(min(max(x - w / 2, 0), self.width() - w), 27, w, 18)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#FFFFFF"))
            p.drawRoundedRect(box, 5, 5)
            p.setPen(QColor("#0E141C"))
            bold = QFont(font)
            bold.setBold(True)
            p.setFont(bold)
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, label)
        self._paint_zoom_row(p, font)
        p.end()

    def _paint_zoom_row(self, p: QPainter, font: QFont) -> None:
        """Riga in alto: con lo zoom, i pulsanti per tornare indietro e l'intervallo; senza, il suggerimento."""
        self._pills = []
        small = QFont(font)
        small.setPixelSize(12)
        p.setFont(small)
        if not self.zoomed:
            return  # le istruzioni stanno nell'intestazione del riquadro
        x = 8.0
        bold = QFont(small)
        bold.setBold(True)
        for text, action in ((_("‹ Indietro"), self.zoom_back), (_("24 ore"), self.reset_zoom)):
            w = p.fontMetrics().horizontalAdvance(text) + 22
            box = QRectF(x, 2, w, 20)
            p.setPen(QPen(QColor(T.INFO), 1))
            fill = QColor(T.INFO)
            fill.setAlpha(45)
            p.setBrush(fill)
            p.drawRoundedRect(box, 10, 10)
            p.setPen(QColor("#FFFFFF"))
            p.setFont(bold)
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
            p.setFont(small)
            self._pills.append((box, action))
            x += w + 6
        p.setPen(QColor(T.INFO))
        p.drawText(QRectF(x + 4, 2, self.width() - x - 12, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   _("Zoom {window} · doppio clic o Esc per tornare indietro", window=self.window_text()))

    # --- mouse ------------------------------------------------------------------
    def mousePressEvent(self, ev) -> None:  # noqa: N802
        if ev.button() == Qt.MouseButton.LeftButton:
            pos = ev.position()
            for box, action in self._pills:  # pulsanti dello zoom dentro la barra
                if box.contains(pos):
                    self._press_x = None
                    action()
                    return
            self._press_x = ev.position().x()
            self._selecting = False
            self._sel = None

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        x = ev.position().x()
        if self._press_x is not None and (self._selecting or abs(x - self._press_x) > DRAG_PX):
            self._selecting = True  # trascinamento: si disegna la selezione da ingrandire
            r = self._track()
            self._sel = (self._press_x, min(max(x, r.left()), r.right()))
            self.update()
            return
        e = self.event_at(x)
        if e:
            from ..detection.labels import display_name  # noqa: PLC0415

            text = f"{TL.fmt_clock(e['ts'])} · {display_name(e.get('label') or 'person')}"
            n = sum(1 for o in self.events if abs(self.x_of(o["ts"]) - x) <= 5)
            if n > 1:
                text += " " + _("(e altri {n} vicini: trascina per ingrandire)", n=n - 1)
        else:
            text = TL.fmt_clock(self.t_of(x)) + "  ·  " + _("trascina per ingrandire")
        QToolTip.showText(ev.globalPosition().toPoint(), text, self)

    def mouseReleaseEvent(self, ev) -> None:  # noqa: N802
        if ev.button() != Qt.MouseButton.LeftButton or self._press_x is None:
            return
        x = ev.position().x()
        if self._selecting and self._sel is not None:
            a, b = sorted(self._sel)
            self._sel = None
            if b - a > DRAG_PX:
                self.zoom_to(self.t_of(a), self.t_of(b))
        else:  # semplice clic: si va a quell'ora (o a quell'evento)
            e = self.event_at(x)
            self.seek.emit(e["ts"] - PRE_EVENT if e else self.t_of(x))
        self._press_x = None
        self._selecting = False
        self.update()

    def mouseDoubleClickEvent(self, ev) -> None:  # noqa: N802
        if any(box.contains(ev.position()) for box, _a in self._pills):
            return  # doppio clic su un pulsante dello zoom: vale come un clic solo
        if ev.button() == Qt.MouseButton.LeftButton and self.zoomed:
            self.zoom_back()

    def keyPressEvent(self, ev) -> None:  # noqa: N802
        if ev.key() == Qt.Key.Key_Escape and (self._sel is not None or self.zoomed):
            if self._sel is not None:  # Esc durante il trascinamento: annulla la selezione
                self._sel, self._press_x, self._selecting = None, None, False
                self.update()
            else:
                self.zoom_back()
        else:
            super().keyPressEvent(ev)

    def wheelEvent(self, ev) -> None:  # noqa: N802
        """Rotellina: ingrandisce attorno al mouse. Maiusc + rotellina o rotellina orizzontale: scorre."""
        dy, dx = ev.angleDelta().y() / 120, ev.angleDelta().x() / 120
        span = self.t1 - self.t0
        if dx or (dy and ev.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            shift = -(dx or dy) * span * 0.1
            self._set_window(self.t0 + shift, self.t1 + shift)
            return
        if not dy:
            return
        center = self.t_of(ev.position().x())
        new_span = min(self.day1 - self.day0, max(MIN_WINDOW, span * (0.8 ** dy)))
        frac = (center - self.t0) / max(1.0, span)
        t0 = center - frac * new_span
        self._set_window(t0, t0 + new_span)


class ReviewView(QWidget):
    """La pagina «Rivedi» della finestra principale."""

    done = Signal(object)   # (callback, risultato) dei lavori in background

    def __init__(self, core, parent=None):
        super().__init__(parent)
        self.core = core
        self.cam_id = ""
        self.cam_name = ""
        self.segments = []
        self.events: list[dict] = []
        self.all_events: list[dict] = []
        self.local_segments = []      # registrati sul Mac (e clip degli eventi)
        self.node_segments: list = []  # solo sul nodo (RemoteSegment), scaricati al bisogno
        self._node_key: tuple = ()    # (camera, giorno) a cui si riferisce node_segments
        self._node_asked = 0.0
        self.cur_event_ts: float | None = None  # evento raggiunto con ⏮ ⏭ o con un clic sulla barra
        self.current = None           # spezzone aperto nel lettore
        self.pending_offset: float | None = None
        self.playing = False
        self.day = dt.date.today()
        self.player = None
        self.done.connect(self._done, Qt.ConnectionType.QueuedConnection)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # così le scorciatoie (spazio, frecce) funzionano

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.cam_combo = QComboBox()
        self.cam_combo.setMinimumWidth(220)
        self.cam_combo.currentIndexChanged.connect(lambda _i: self._camera_changed())
        top.addWidget(self.cam_combo)
        top.addSpacing(10)
        self.prev_day = self._button("◀", _("Giorno prima"), lambda: self._shift_day(-1))
        top.addWidget(self.prev_day)
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("dd/MM/yyyy")
        self.date_edit.setDate(QDate.currentDate())
        self.date_edit.setMaximumDate(QDate.currentDate())
        self.date_edit.dateChanged.connect(lambda d: self._set_day(dt.date(d.year(), d.month(), d.day())))
        top.addWidget(self.date_edit)
        self.next_day = self._button("▶", _("Giorno dopo"), lambda: self._shift_day(1))
        top.addWidget(self.next_day)
        top.addWidget(self._button(_("Oggi"), _("Torna a oggi"), lambda: self._go_today()))
        top.addSpacing(10)
        self.folder_button = self._button(_("📁 Cartella"), _("Apre la cartella dei video di questo giorno"),
                                          self._open_folder)
        top.addWidget(self.folder_button)
        top.addSpacing(10)
        self.info = QLabel()
        self.info.setStyleSheet(f"color:{T.MUTED};")
        self.info.setMinimumWidth(10)  # testo lungo: si accorcia invece di allargare la finestra
        self.info.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.info.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.info, 1)
        lay.addLayout(top)

        self.video_host = QWidget()
        self.video_host.setStyleSheet("background:#000; border-radius:10px;")
        self.video_host.setMinimumHeight(240)
        vlay = QVBoxLayout(self.video_host)
        vlay.setContentsMargins(0, 0, 0, 0)
        self.video = None
        self.empty = QLabel()
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setStyleSheet(f"color:{T.MUTED}; font-size:15px; background:transparent;")
        vlay.addWidget(self.empty)
        lay.addWidget(self.video_host, 1)

        # riquadro della linea del tempo: istruzioni e legenda in alto, la barra, i comandi sotto
        panel = QFrame()
        panel.setObjectName("reviewPanel")
        panel.setStyleSheet(f"QFrame#reviewPanel {{ background:{T.PANEL}; border:1px solid {T.LINE}; "
                            "border-radius:12px; }")
        play = QVBoxLayout(panel)
        play.setContentsMargins(16, 12, 16, 14)
        play.setSpacing(6)
        head = QHBoxLayout()
        howto = QLabel(_("<b>Rotellina</b>: ingrandisci · <b>Trascina</b>: scegli un intervallo · "
                         "<b>Clic</b>: vai a un'ora"))
        howto.setTextFormat(Qt.TextFormat.RichText)
        howto.setStyleSheet(f"color:{T.MUTED}; font-size:12px; background:transparent; border:none;")
        head.addWidget(howto, 1)
        legend = QLabel(
            f"<span style='color:{T.ALARM}'>●</span> " + _("persone") +
            f" &nbsp;&nbsp; <span style='color:{T.OBJECT}'>●</span> " + _("animali e oggetti") +
            f" &nbsp;&nbsp; <span style='color:{T.INFO}'>●</span> " + _("suoni") +
            " &nbsp;&nbsp; <span style='color:#4F7DB3'>▬</span> " + _("sul {computer}", computer=questo_computer()) +
            f" &nbsp;&nbsp; <span style='color:{NODE_COLOR}'>▬</span> " + _("sul nodo"))
        legend.setTextFormat(Qt.TextFormat.RichText)
        legend.setStyleSheet(f"color:{T.MUTED}; font-size:12px; background:transparent; border:none;")
        head.addWidget(legend, 0, Qt.AlignmentFlag.AlignRight)
        play.addLayout(head)

        self.bar = TimelineBar()
        self.bar.seek.connect(self.seek)
        self.bar.setStyleSheet("background:transparent; border:none;")
        play.addWidget(self.bar)

        ctl = QHBoxLayout()
        ctl.setSpacing(8)
        ctl.addWidget(self._big("", _("Evento precedente"), lambda: self._jump_event(False),
                                icona="ui-skip-back"))
        ctl.addWidget(self._big("−10 s", _("Indietro di 10 secondi (freccia ←)"), lambda: self._nudge(-10)))
        self.play_button = self._big("", _("Riproduci / pausa (barra spaziatrice)"), self.toggle_play,
                                     accent=True, icona="ui-play")
        self.play_button.setMinimumWidth(72)
        ctl.addWidget(self.play_button)
        ctl.addWidget(self._big("+10 s", _("Avanti di 10 secondi (freccia →)"), lambda: self._nudge(10)))
        ctl.addWidget(self._big("", _("Evento successivo"), lambda: self._jump_event(True),
                                icona="ui-skip-forward"))
        ctl.addSpacing(6)
        self.speed = QComboBox()
        for s in SPEEDS:
            self.speed.addItem(f"{s}×", s)
        self.speed.setToolTip(_("Velocità"))
        self.speed.setMinimumHeight(40)
        self.speed.setMinimumWidth(84)
        self.speed.currentIndexChanged.connect(lambda _i: self._apply_speed())
        ctl.addWidget(self.speed)
        ctl.addStretch(1)
        self.clock = QLabel("--:--:--")
        self.clock.setStyleSheet("font-size:24px; font-weight:800; background:transparent; border:none;")
        ctl.addWidget(self.clock)
        ctl.addSpacing(14)
        ctl.addWidget(self._big(_("📷 Foto"), _("Salva il fotogramma che stai guardando"), self._save_frame))
        ctl.addWidget(self._big(_("⤓ Esporta…"), _("Salva un intervallo in un file MP4"), self._export))
        play.addLayout(ctl)
        lay.addWidget(panel)

        from PySide6.QtGui import QKeySequence, QShortcut  # noqa: PLC0415

        for key, slot in ((Qt.Key.Key_Space, self.toggle_play), (Qt.Key.Key_Left, lambda: self._nudge(-10)),
                          (Qt.Key.Key_Right, lambda: self._nudge(10)), (Qt.Key.Key_Escape, self._escape)):
            sc = QShortcut(QKeySequence(key), self)  # tasti attivi su tutta la pagina, non solo sul suo sfondo
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(slot)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.reload_timer = QTimer(self)  # oggi: nuovi spezzoni e nuovi eventi ogni 30 s
        self.reload_timer.timeout.connect(self._reload_today)

    def _escape(self) -> None:
        """Esc: annulla la selezione in corso, altrimenti torna allo zoom precedente."""
        bar = self.bar
        if bar._sel is not None:
            bar._sel, bar._press_x, bar._selecting = None, None, False
            bar.update()
        elif bar.zoomed:
            bar.zoom_back()

    # --- costruzione -------------------------------------------------------------
    @staticmethod
    def _big(text: str, tip: str, slot, accent: bool = False, icona: str = "") -> QPushButton:
        """Pulsante grande dei comandi del lettore (``accent``: quello principale, quello che riproduce).

        I simboli dei comandi sono **icone disegnate**, non caratteri. I caratteri «⏮ ⏭ ⏸» sono
        emoji per Unicode: Windows li prende dal font a colori e nella barra del lettore uscivano
        colorati in mezzo agli altri comandi bianchi (visto sul PC il 19/10). Un'icona ricolorata dal
        tema è uguale su tutti i sistemi e non dipende dai font installati.
        """
        b = QPushButton(text)
        if icona:
            b.setIcon(T.icon(icona, "#FFFFFF", 20))
            b.setIconSize(QSize(20, 20))
        b.setToolTip(tip)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # la barra spaziatrice resta a riproduci/pausa
        bg, hover = (T.INFO, "#6FA8F5") if accent else (T.RAISED, "#3A4E6A")
        b.setStyleSheet(f"QPushButton {{ background:{bg}; color:#FFFFFF; border:none; border-radius:10px; "
                        f"padding:8px 16px; font-size:16px; font-weight:700; min-height:24px; text-align:center; }}"
                        f"QPushButton:hover {{ background:{hover}; }}")
        b.clicked.connect(slot)
        return b

    @staticmethod
    def _button(text: str, tip: str, slot) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName("small")
        b.setToolTip(tip)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(slot)
        return b

    def _ensure_player(self) -> bool:
        if self.player is not None:
            return True
        try:
            from PySide6.QtMultimedia import QMediaPlayer  # noqa: PLC0415
            from PySide6.QtMultimediaWidgets import QVideoWidget  # noqa: PLC0415
        except ImportError as exc:
            self._show_empty(_("Lettore video non disponibile: {exc}", exc=exc))
            return False
        self.video = QVideoWidget()
        self.video.setStyleSheet("background:#000;")
        self.video_host.layout().insertWidget(0, self.video, 1)
        self.player = QMediaPlayer(self)
        self.player.setVideoOutput(self.video)
        self.player.mediaStatusChanged.connect(self._media_status)
        self.player.errorOccurred.connect(self._player_error)
        return True

    # --- apertura e chiusura della pagina ------------------------------------------
    def activate(self, cam_id: str | None = None, at: float | None = None) -> None:
        """La pagina diventa visibile (eventualmente su una camera e un istante precisi)."""
        self.date_edit.setMaximumDate(QDate.currentDate())  # Mirto resta acceso: dopo mezzanotte c'è un giorno in più
        self._fill_cameras(cam_id)
        if at is not None:
            d = dt.date.fromtimestamp(at)
            self.date_edit.blockSignals(True)
            self.date_edit.setDate(QDate(d.year, d.month, d.day))
            self.date_edit.blockSignals(False)
            self.day = d
        self._load(start_at=at)
        self.timer.start(250)
        self.reload_timer.start(30000)

    def deactivate(self) -> None:
        self.timer.stop()
        self.reload_timer.stop()
        if self.player is not None:
            self.player.pause()
        self.playing = False
        self._mostra_riproduci()

    def _fill_cameras(self, prefer: str | None) -> None:
        rec = self.core.recording
        cams = rec.recorded_cameras()
        names = {cid: src.info.name for cid, src in dict(self.core.cameras.sources).items()}
        known = {cid for cid, _n in cams}
        cams += [(cid, n) for cid, n in names.items() if cid not in known]  # anche quelle che non hanno ancora video
        want = prefer or self.cam_id or (cams[0][0] if cams else "")
        self.cam_combo.blockSignals(True)
        self.cam_combo.clear()
        for cid, name in cams:
            self.cam_combo.addItem(names.get(cid, name), cid)
        idx = self.cam_combo.findData(want)
        self.cam_combo.setCurrentIndex(max(0, idx))
        self.cam_combo.blockSignals(False)
        self.cam_id = self.cam_combo.currentData() or ""
        self.cam_name = self.cam_combo.currentText()

    # --- dati della giornata -----------------------------------------------------------
    def _camera_changed(self) -> None:
        self.cam_id = self.cam_combo.currentData() or ""
        self.cam_name = self.cam_combo.currentText()
        self._load()

    def _shift_day(self, days: int) -> None:
        d = self.day + dt.timedelta(days=days)
        if d > dt.date.today():
            return
        self.date_edit.setMaximumDate(QDate.currentDate())
        self.date_edit.setDate(QDate(d.year, d.month, d.day))

    def _go_today(self) -> None:
        self.date_edit.setMaximumDate(QDate.currentDate())
        self.date_edit.setDate(QDate.currentDate())

    def _set_day(self, d: dt.date) -> None:
        self.day = d
        self._load()

    def _read_day(self) -> tuple[float, float]:
        t0, t1 = TL.day_bounds(self.day)
        rec = self.core.recording
        local = [s for s in rec.segments(self.cam_id, t0=t0, t1=t1) if s.end >= t0 and s.start < t1] \
            if self.cam_id else []
        self.all_events = self.core.store.between(t0, t1, self.cam_id) if self.cam_id else []
        pre = float(rec.cfg("pre_seconds", 10))
        post = float(rec.cfg("post_seconds", 20))
        local += TL.clip_segments(self.all_events, local, pre, post)  # «solo eventi»: le clip
        self.local_segments = sorted(local, key=lambda x: x.start)
        self._merge()
        return t0, t1

    def _merge(self) -> None:
        """Mac + nodo: dal nodo solo i pezzi che sul Mac mancano (es. Mac spento), per non vederli due volte."""
        t0, t1 = TL.day_bounds(self.day)
        local = self.local_segments
        remote = [r for r in self.node_segments if TL.covered(local, r.start, r.end) < 0.5 * max(1.0, r.duration)] \
            if self._node_key == (self.cam_id, self.day) else []
        self.segments = sorted(local + remote, key=lambda x: x.start)
        # sulla barra solo gli eventi che si possono guardare (gli altri hanno solo la foto: stanno nella striscia)
        self.events = TL.playable_events(self.all_events, self.segments)
        self.bar.set_data(TL.spans(local, t0, t1), self.events, TL.spans(remote, t0, t1))
        self._remote_count = len(remote)

    def _ask_node(self) -> None:
        """Chiede al nodo (in background) cosa ha registrato in questo giorno."""
        rec = self.core.recording
        if not self.cam_id or rec.node_client_for(self.cam_id) is None:
            return
        key = (self.cam_id, self.day)
        t0, t1 = TL.day_bounds(self.day)
        self._node_asked = time.time()
        cam = self.cam_id

        def done(result) -> None:
            if key != (self.cam_id, self.day) or not isinstance(result, list):
                return  # cambiati giorno o camera, oppure nodo che non risponde: si tiene quello che si sapeva
            was_empty = not self.segments
            self._node_key = key
            self.node_segments = result
            self._merge()
            self._update_info()
            if was_empty and self.segments and self.current is None:
                self.empty.hide()
                start = (self.segments[-1].end - 60) if self.day == dt.date.today() else self.segments[0].start
                self.seek(start)

        self._run(lambda: rec.node_segments(cam, t0, t1), done)

    def _update_info(self) -> None:
        t0, t1 = TL.day_bounds(self.day)
        recorded = sum(b - a for a, b in TL.spans(self.segments, t0, t1))
        text = _("{n} eventi con video su {m} · {h} da rivedere", n=len(self.events), m=len(self.all_events),
                 h=_fmt_duration(recorded)) if self.cam_id else ""
        if getattr(self, "_remote_count", 0):
            text += " · " + _("{n} min solo sul nodo", n=self._remote_count)
        self.info.setToolTip("")
        if self.cam_id and self.core.recording.mode_for(self.cam_id) == "events":
            text += " · " + _("solo eventi")
            self.info.setToolTip(_("Modalità «solo eventi»: sulla barra ci sono le clip degli eventi e gli ultimi "
                                   "minuti. Per rivedere tutta la giornata scegli «continua» in Impostazioni → "
                                   "Registrazione."))
        self.info.setText(text)

    def _load(self, start_at: float | None = None) -> None:
        self._stop_player()
        t0, t1 = TL.day_bounds(self.day)
        self.bar.set_day(t0, t1)
        self.next_day.setEnabled(self.day < dt.date.today())
        self.node_segments, self._node_key = [], ()
        t0, t1 = self._read_day()
        self._update_info()
        self._ask_node()
        if not self.cam_id:
            self._show_empty(_("Nessuna camera registra ancora: attiva la registrazione in Impostazioni → "
                               "Registrazione."))
            return
        if not self.segments and self.core.recording.node_client_for(self.cam_id) is not None:
            self._show_empty(_("Chiedo al nodo cosa ha registrato…"))
            return
        if not self.segments:
            mode = self.core.recording.mode_for(self.cam_id)
            hint = (_("Nessuna clip di eventi per questa camera in questo giorno. In modalità «solo eventi» si "
                      "tengono solo le clip; per rivedere tutta la giornata scegli «continua» in Impostazioni → "
                      "Registrazione.")
                    if mode == "events" else _("Nessuna registrazione di questa camera in questo giorno."))
            self._show_empty(hint)
            return
        self.empty.hide()
        if start_at is None:  # oggi: l'ultimo minuto registrato; altri giorni: l'inizio
            start_at = (self.segments[-1].end - 60) if self.day == dt.date.today() else self.segments[0].start
        self.seek(start_at)

    def _open_folder(self) -> None:
        """Cartella del giorno: per la registrazione continua quella della camera (un file al minuto, ora UTC nel
        nome); altrimenti quella del giorno con foto e clip degli eventi (events/AAAA-MM-GG)."""
        from ..config import data_dir  # noqa: PLC0415
        from .app import open_file  # noqa: PLC0415

        day_dir = data_dir() / "events" / self.day.strftime("%Y-%m-%d")
        cam_dir = self.core.recording.store.cam_dir(self.cam_id) if self.cam_id else None
        continuous = bool(self.cam_id) and self.core.recording.mode_for(self.cam_id) == "continuous"
        for folder in ((cam_dir, day_dir) if continuous else (day_dir, cam_dir)):
            if folder is not None and folder.is_dir():
                open_file(folder)
                return
        self.info.setText(_("Nessun file per questo giorno"))

    def _reload_today(self) -> None:
        if self.day == dt.date.today() and self.cam_id:
            self._read_day()
            self._ask_node()  # anche il nodo ha registrato nel frattempo

    def _show_empty(self, text: str) -> None:
        self.empty.setText(text)
        self.empty.show()
        if self.video is not None:
            self.video.hide()
        self.bar.set_cursor(None)
        self.clock.setText("--:--:--")

    # --- riproduzione -----------------------------------------------------------------
    def seek(self, t: float) -> None:
        # un clic su un evento della barra parte PRE_EVENT secondi prima: quell'evento diventa "quello corrente"
        self.cur_event_ts = next((e["ts"] for e in self.events if abs(e["ts"] - PRE_EVENT - t) < 0.5), None)
        if not self.segments or not self._ensure_player():
            return
        pos = TL.locate(self.segments, t)
        if pos is None:
            self.bar.set_cursor(t)
            self.clock.setText(TL.fmt_clock(t))
            self.info.setText(_("Nessuna registrazione dopo le {hhmm}", hhmm=TL.fmt_clock(t, seconds=False)))
            return
        self.empty.hide()
        self.video.show()
        if self.current is not None and pos.segment.path == self.current.path:
            if self.pending_offset is not None:  # il file si sta ancora aprendo: vale l'ultima richiesta
                self.pending_offset = pos.offset
            else:
                self.player.setPosition(int(pos.offset * 1000))
        else:
            self._open(pos.segment, pos.offset)
        self.bar.set_cursor(pos.segment.start + pos.offset)

    def _open(self, segment, offset: float, reload: bool = False) -> None:
        self.current = segment
        self.pending_offset = offset
        if isinstance(segment, RemoteSegment) and not self._cached(segment):
            # solo sul nodo: si scarica (in background) e poi si apre, se nel frattempo non si è andati altrove
            self.info.setText(_("Scarico dal nodo {node} il minuto delle {hhmm}…", node=segment.node,
                                hhmm=TL.fmt_clock(segment.start, seconds=False)))
            cam = self.cam_id

            def done(result) -> None:
                if self.current is not segment:
                    return
                if isinstance(result, str):
                    self.info.setText(_("Non riesco a scaricare dal nodo: {err}", err=result))
                    return
                self._update_info()
                self._open(segment, self.pending_offset or 0.0, reload=True)

            self._run(lambda: self.core.recording.fetch(cam, segment), done)
            return
        url = QUrl.fromLocalFile(str(segment.path))
        if reload or self.player.source() == url:
            # stesso file (pagina riaperta, o file cresciuto): Qt ignorerebbe setSource, quindi lo si svuota prima
            self.player.setSource(QUrl())
        self.player.setSource(url)
        self._apply_speed()

    @staticmethod
    def _cached(seg) -> bool:
        try:
            return seg.path.exists() and seg.path.stat().st_size == seg.size
        except OSError:
            return False

    def _media_status(self, status) -> None:
        from PySide6.QtMultimedia import QMediaPlayer  # noqa: PLC0415

        S = QMediaPlayer.MediaStatus
        if status in (S.LoadedMedia, S.BufferedMedia) and self.pending_offset is not None:
            self.player.setPosition(int(self.pending_offset * 1000))
            self.pending_offset = None
            if self.playing:
                self.player.play()
            else:  # mostra il fotogramma anche in pausa
                self.player.play()
                QTimer.singleShot(120, lambda: (not self.playing) and self.player and self.player.pause())
        elif status == S.EndOfMedia and self.current is not None:
            self._advance()
        elif status == S.InvalidMedia and self.current is not None:
            self._player_error(None, _("file video non leggibile"))

    def _advance(self) -> None:
        """Fine dello spezzone: se è quello ancora in scrittura (oggi) e nel frattempo è cresciuto, si riapre dal punto
        raggiunto; altrimenti si passa al successivo."""
        cur = self.current
        reached = cur.start + self.player.duration() / 1000
        if self.day == dt.date.today():
            self._read_day()
            fresh = next((s for s in self.segments if s.path == cur.path), None)
            if fresh is not None and fresh.end > reached + 2:
                self._open(fresh, reached - fresh.start, reload=True)
                return
        nxt = TL.next_segment(self.segments, cur)
        if nxt is not None:
            self._open(nxt, 0.0)
        else:
            self.playing = False
            self._mostra_riproduci()

    def _player_error(self, _err, message: str) -> None:
        """File non leggibile: lo si dice e si passa allo spezzone successivo."""
        log.warning("Lettore: %s (%s)", message, self.current.path if self.current else "")
        self.info.setText(_("Video non leggibile ({msg}): passo al successivo", msg=message))
        if self.current is not None:
            nxt = TL.next_segment(self.segments, self.current)
            if nxt is not None:
                self._open(nxt, 0.0)

    def toggle_play(self) -> None:
        if self.player is None or self.current is None:
            return
        self.playing = not self.playing
        self._mostra_riproduci(self.playing)
        if self.playing:
            self.player.play()
        else:
            self.player.pause()

    def _apply_speed(self) -> None:
        if self.player is not None:
            self.player.setPlaybackRate(float(self.speed.currentData() or 1))

    def _stop_player(self) -> None:
        if self.player is not None:
            self.player.stop()
        self.current = None
        self.pending_offset = None
        self.playing = False
        self._mostra_riproduci()

    def now_t(self) -> float | None:
        if self.current is None or self.player is None:
            return None
        if self.pending_offset is not None:
            return self.current.start + self.pending_offset
        return self.current.start + self.player.position() / 1000

    def _tick(self) -> None:
        t = self.now_t()
        if t is None:
            return
        self.bar.set_cursor(t)
        self.clock.setText(TL.fmt_clock(t))

    def _nudge(self, seconds: float) -> None:
        t = self.now_t()
        if t is not None:
            self.seek(t + seconds)

    def _mostra_riproduci(self, in_riproduzione: bool = False) -> None:
        """Icona del pulsante centrale: pausa mentre va, riproduci quando è fermo."""
        nome = "ui-pause" if in_riproduzione else "ui-play"
        self.play_button.setIcon(T.icon(nome, "#FFFFFF", 20))

    def _jump_event(self, forward: bool) -> None:
        """⏮ ⏭: si scorrono gli eventi uno per uno, partendo da quello che si sta guardando."""
        t = self.now_t()
        if self.cur_event_ts is not None:
            ref = self.cur_event_ts
        elif t is not None:
            ref = t + PRE_EVENT  # il punto guardato, come se fosse l'istante di un evento
        else:
            ref = self.bar.day0 if forward else self.bar.day1
        e = TL.neighbor_event(self.events, ref, forward)
        if e is None:
            self.info.setText(_("Nessun altro evento dopo questo") if forward
                              else _("Nessun altro evento prima di questo"))
            return
        self.seek(e["ts"] - PRE_EVENT)
        self.cur_event_ts = e["ts"]
        self.info.setText(_("Evento delle {hhmm}", hhmm=TL.fmt_clock(e["ts"])))

    # --- foto ed esportazione ------------------------------------------------------------
    def _done(self, pair) -> None:
        """Nel thread dell'interfaccia: risultato di un lavoro in background."""
        then, result = pair
        then(result)

    def _run(self, work, then) -> None:
        def job():
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001  (la finestra deve sempre ricevere una risposta)
                log.exception("Lavoro in background non riuscito")
                result = str(exc) or exc.__class__.__name__
            self.done.emit((then, result))

        threading.Thread(target=job, daemon=True).start()

    def _safe_name(self) -> str:
        return "".join(ch if ch.isalnum() else "_" for ch in self.cam_name)[:40] or "camera"

    def _save_frame(self) -> None:
        t = self.now_t()
        if t is None or self.current is None:
            return
        from ..config import data_dir  # noqa: PLC0415
        from .app import open_file  # noqa: PLC0415

        folder = data_dir() / "foto"
        folder.mkdir(parents=True, exist_ok=True)
        out = folder / f"foto_{self._safe_name()}_{time.strftime('%Y%m%d-%H%M%S', time.localtime(t))}.jpg"
        seg, off = self.current, t - self.current.start

        def done(err: str) -> None:
            if err:
                QMessageBox.warning(self, _("Foto"), err)
            else:
                open_file(out)

        self._run(lambda: self.core.recording.frame_at(seg, off, out), done)

    def _export(self) -> None:
        t = self.now_t() or self.bar.day0
        t = min(max(t, self.bar.day0 + 30), self.bar.day1 - 31)  # l'intervallo proposto resta nella giornata
        dlg = ExportDialog(t, self)
        if not dlg.exec():
            return
        a, b = dlg.interval(self.day)
        if b <= a:
            QMessageBox.warning(self, _("Esporta"), _("L'ora di fine deve venire dopo quella di inizio."))
            return
        name = f"Mirto_{self._safe_name()}_{time.strftime('%Y%m%d_%H%M%S', time.localtime(a))}.mp4"
        path, _filter = QFileDialog.getSaveFileName(self, _("Esporta video"), str(Path.home() / "Desktop" / name),
                                                    "MP4 (*.mp4)")
        if not path:
            return
        out = Path(path if path.lower().endswith(".mp4") else path + ".mp4")
        self.info.setText(_("Esportazione in corso…"))

        def done(err: str) -> None:
            if err:
                QMessageBox.warning(self, _("Esporta"), err)
                self.info.setText("")
                return
            from .app import open_file  # noqa: PLC0415

            self.info.setText(_("Esportato: {name}", name=out.name))
            open_file(out.parent)

        cam, segs = self.cam_id, list(self.segments)  # anche le clip degli eventi (camere «solo eventi»)
        self._run(lambda: self.core.recording.export(cam, a, b, out, segments=segs), done)


class ExportDialog(QDialog):
    """Da che ora a che ora esportare (di serie: un minuto attorno al punto che stai guardando)."""

    def __init__(self, t: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Esporta video"))
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.start = QTimeEdit()
        self.end = QTimeEdit()
        for w, at in ((self.start, t - 30), (self.end, t + 30)):
            w.setDisplayFormat("HH:mm:ss")
            lt = time.localtime(at)
            w.setTime(QTime(lt.tm_hour, lt.tm_min, lt.tm_sec))
            w.setMaximumWidth(200)
        form.addRow(_("Dalle"), self.start)
        form.addRow(_("Alle"), self.end)
        lay.addLayout(form)
        note = QLabel(_("Il video viene copiato senza ricodifica: può iniziare qualche secondo prima."))
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{T.MUTED}; font-size:12px;")
        lay.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(_("Esporta…"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def interval(self, day: dt.date) -> tuple[float, float]:
        def epoch(w: QTimeEdit) -> float:
            q = w.time()
            return time.mktime(dt.datetime.combine(day, dt.time(q.hour(), q.minute(), q.second())).timetuple())

        return epoch(self.start), epoch(self.end)


def _fmt_duration(seconds: float) -> str:
    m = int(seconds // 60)
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{m} min"
