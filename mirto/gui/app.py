# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Finestra principale di Mirto – stile "centrale notturna".

  ┌ fascia di stato colorata (verde protetta · ambra in pausa · grigio non protetta · rosso allarme) ┐
  │ colonna stato e comandi │ griglia camere / Tutte le cam │ cronologia eventi │
Menu del Mac: Mirto, Prove, Aiuto.
"""

from __future__ import annotations

import html
import logging
import re
import subprocess
import sys
import threading
import time

from PySide6.QtCore import QObject, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QKeySequence, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, SOSTIENI_URL, __version__
from ..config import data_dir
from ..actions.aggiorna_nodo import aggiornamento_del_nodo
from ..actions.diagnosi import rete_debole, rete_in_parole
from ..cameras.manager import default_threaded_discovery
from ..core import MirtoCore
from . import theme as T
from .widgets import (
    event_thumbnail,
    SizeSlider,
    mono_font,
    confirm_warning,
    HouseLogo,
    AddTile,
    CameraCard,
    CameraGrid,
    CameraTile,
    EventRow,
    StateBand,
    StatusRow,
    action_button,
    label,
)
from ..i18n import N_, _, questo_computer
from .sistema import (  # le righe di Sistema: testo senza Qt, cosi' si provano anche senza finestra
    MODEL_NAMES, STATE_WORDS, SUONA_DA, TELEGRAM_HELP, _duration, _esc, _gb, _pad,
    _short_action_name, ai_detail, cameras_detail, email_detail, legenda_icone, level_bar,
    node_recording_text, nodes_detail, notifications_summary, recording_summary, riga_vista_flotta,
    righe_flotta, species_line, telegram_detail,
)

log = logging.getLogger(__name__)


class Bridge(QObject):
    """Porta nel thread dell'interfaccia le notifiche che arrivano da altri thread."""

    source_added = Signal(str, str)        # id, nome
    source_removed = Signal(str)
    status_changed = Signal(str, str)      # id, stato
    camera_state = Signal(str, dict)       # id, {"motion": bool} / {"dark": bool}
    event = Signal(object)
    message = Signal(str)
    clip = Signal(str)                     # id dell'evento la cui clip è pronta
    done = Signal(object)                  # (callback, risultato) dei lavori in background
    update = Signal(str)                   # è uscita una versione nuova di Mirto


def open_file(path) -> None:
    path = str(path)
    if sys.platform == "darwin":
        subprocess.Popen(["open", path])
    elif sys.platform.startswith("win"):
        import os  # noqa: PLC0415

        os.startfile(path)  # noqa: S606
    else:
        subprocess.Popen(["xdg-open", path])


class TextDialog(QDialog):
    """Finestra con un testo lungo (es. risultato dei test)."""

    def __init__(self, title: str, text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 520)
        box = QPlainTextEdit(text)
        box.setReadOnly(True)
        mono = QFont("Menlo")
        mono.setPointSize(13)
        box.setFont(mono)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText(_("Chiudi"))
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(box)
        lay.addWidget(buttons)


class MainWindow(QMainWindow):
    REFRESH_MS = 100  # ~10 immagini al secondo per riquadro

    def __init__(self, core: MirtoCore):
        super().__init__()
        self.core = core
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(1100, 640)
        screen = QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else None
        if avail is not None:  # la finestra deve stare tutta nello schermo (MacBook Air: ~1470x920)
            self.resize(min(1440, avail.width() - 40), min(900, avail.height() - 40))
            self.move(avail.x() + (avail.width() - self.width()) // 2, avail.y() + 10)
        else:
            self.resize(1440, 900)
        self.tiles: dict[str, CameraTile] = {}
        self.single_id: str | None = None
        self.person_until: dict[str, float] = {}
        self.cam_nodes: dict[str, str] = {}  # camera → nome del nodo che la trasmette
        self.cards: dict[str, CameraCard] = {}

        b = self.bridge = Bridge()
        b.source_added.connect(self._add_tile)
        b.source_removed.connect(self._remove_tile)
        b.status_changed.connect(self._set_status)
        b.camera_state.connect(self._camera_state)
        b.event.connect(self._on_event)
        b.message.connect(lambda t: self.statusBar().showMessage(t, 15000))
        b.done.connect(lambda pair: pair[0](pair[1]))
        core.cameras.on_source_added.append(lambda s: b.source_added.emit(s.info.id, s.info.name))
        core.cameras.on_source_removed.append(b.source_removed.emit)
        core.cameras.on_status.append(b.status_changed.emit)
        core.on_camera_state.append(b.camera_state.emit)
        core.on_event.append(b.event.emit)
        core.on_message.append(b.message.emit)
        b.clip.connect(self._clip_ready)
        core.on_clip.append(lambda event_id, _path: b.clip.emit(event_id))
        b.update.connect(self._new_version)
        core.on_update.append(b.update.emit)

        self._build_menu_bar()
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.band = StateBand()
        outer.addWidget(self.band)
        body = QHBoxLayout()
        body.setContentsMargins(14, 14, 14, 8)
        body.setSpacing(14)
        # la colonna scorre se lo schermo è basso (le righe di Sistema possono essere molte)
        rail_scroll = QScrollArea()
        rail_scroll.setWidget(self._build_rail())
        rail_scroll.setWidgetResizable(True)
        rail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        rail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        rail_scroll.setFixedWidth(452)
        body.addWidget(rail_scroll)
        # a destra: camere in alto, cronologia degli eventi in una striscia orizzontale sotto
        right = QVBoxLayout()
        right.setSpacing(14)
        right.addWidget(self._build_cameras(), 1)
        self.timeline_panel = self._build_timeline()
        right.addWidget(self.timeline_panel)
        body.addLayout(right, 1)
        outer.addLayout(body, 1)
        self.setCentralWidget(root)
        # piè di pagina: autore a destra della barra di stato (i messaggi restano a sinistra)
        credit = label(_('Mirto {version} · Marco Dalicco · Assisted by Claude Code + Claude Opus 5.5', version=__version__),
                       obj="faint")
        credit.setContentsMargins(0, 0, 12, 0)
        self.statusBar().addPermanentWidget(credit)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(self.REFRESH_MS)
        # Su macOS la scansione delle camere va fatta dal thread principale (run loop di AVFoundation);
        # altrove gira nel thread del DiscoveryWatcher e questo timer resta fermo.
        self.scan_timer = QTimer(self)
        self.scan_timer.timeout.connect(core.cameras.scan)
        if not default_threaded_discovery():
            self.scan_timer.start(int(float(core.config.get("cameras.rescan_seconds", 3)) * 1000))
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self._update_status)
        self.status_timer.start(1000)
        self._update_status()

    # ------------------------------------------------------------------ costruzione
    def _build_rail(self) -> QWidget:
        """Colonna sinistra: stato in grande, i due comandi che lo cambiano, azioni, salute del sistema."""
        rail = QFrame()
        rail.setObjectName("rail")
        rail.setFixedWidth(440)
        lay = QVBoxLayout(rail)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(12)
        self.state_icon = HouseLogo(64)  # il logo dell'app: colore = stato, l'obiettivo mette a fuoco
        self.state_word = label("", size=T.SIZE_STATE, weight=800)
        # sotto lo stato, in rosso, finché la camera resta evidenziata dopo una persona rilevata
        self.alarm_word = label(_("RILEVAMENTO"), weight=800)
        self.alarm_word.setStyleSheet(f"color:{T.ALARM}; font-size:{T.SIZE_TITLE}px; letter-spacing:2px;")
        self.alarm_word.hide()
        words = QVBoxLayout()
        words.setSpacing(0)
        words.addStretch(1)
        words.addWidget(self.state_word)
        words.addWidget(self.alarm_word)
        words.addStretch(1)
        top.addWidget(self.state_icon)
        top.addLayout(words, 1)
        lay.addLayout(top)
        self.state_detail = label(obj="muted")
        self.state_detail.setWordWrap(True)
        self.state_detail.setStyleSheet(f"color:{T.MUTED}; font-size:{T.SIZE_SMALL + 1}px;")
        lay.addWidget(self.state_detail)
        lay.addSpacing(6)

        self.arm_button = action_button(_("ATTIVA PROTEZIONE"), "shield-check")
        self.arm_button.clicked.connect(self._toggle_armed)
        self.silence_button = action_button(_("Silenzia 1 ora"), "bell-off")
        self.silence_button.clicked.connect(self._toggle_silence)
        # "Ferma tutto" compare in alto solo quando serve: protezione attiva, nodi con azioni
        # configurate e qualcosa davvero in corso (azione accesa, in partenza o in attesa di conferma)
        self.stop_button = action_button(_("FERMA TUTTO"), "octagon-x", obj="danger", color=T.ALARM,
                                         tooltip=_('Spegne tutte le azioni sui nodi e disattiva la protezione'))
        self.stop_button.clicked.connect(self._stop_all)
        self.stop_button.setVisible(False)
        lay.addWidget(self.stop_button)
        lay.addWidget(self.arm_button)
        lay.addWidget(self.silence_button)

        lay.addSpacing(14)
        settings = action_button(_("Impostazioni"), "settings",
                                 tooltip=_('Generale, AI, registrazione, programmazione, presenza, notifiche, nodi, azioni e prove'))
        settings.clicked.connect(self._open_settings)
        lay.addWidget(settings)

        lay.addSpacing(10)
        # "Sistema": riquadro scuro con carattere a spaziatura fissa, ad altezza fissa (occupa lo spazio
        # rimasto nella colonna): se le righe sono tante scorre al suo interno invece di allargarsi
        console = QFrame()
        console.setObjectName("console")
        con = QVBoxLayout(console)
        con.setContentsMargins(12, 10, 6, 10)
        con.setSpacing(6)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 6, 0)
        head.addWidget(label(_("Sistema"), obj="sectionTitle"))
        head.addStretch(1)
        self.console_clock = label("")
        self.console_clock.setFont(mono_font(T.SIZE_SMALL - 2))
        self.console_clock.setStyleSheet(f"color:{T.FAINT};")
        self.console_clock.setToolTip(_("Ora dell'ultimo aggiornamento (ogni secondo)"))
        head.addWidget(self.console_clock)
        con.addLayout(head)
        rows_host = QWidget()
        rows_host.setStyleSheet("background: transparent;")
        rows = QVBoxLayout(rows_host)
        rows.setContentsMargins(0, 2, 6, 0)
        rows.setSpacing(8)
        self.row_boot = StatusRow(mono=True)
        self.row_cams = StatusRow(mono=True)
        self.row_ai = StatusRow(mono=True)
        self.row_notify = StatusRow(mono=True)   # Notifiche: Telegram, Email, SMS sotto un'unica voce
        self.row_nodes = StatusRow(mono=True)
        self.row_sched = StatusRow(mono=True)
        self.row_presence = StatusRow(mono=True)
        self.row_audio = StatusRow(mono=True)
        self.row_rec = StatusRow(mono=True)
        rows.addWidget(self._build_pair_card())
        for r in (self.row_boot, self.row_cams, self.row_ai, self.row_rec, self.row_audio, self.row_notify, self.row_nodes,
                  self.row_sched, self.row_presence):
            r.apri_chiudi.connect(self._sistema_apri_chiudi)
            rows.addWidget(r)
        rows.addStretch(1)
        rows_scroll = QScrollArea()
        rows_scroll.setWidget(rows_host)
        rows_scroll.setWidgetResizable(True)
        rows_scroll.setFrameShape(QFrame.Shape.NoFrame)
        rows_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        rows_scroll.setStyleSheet("QScrollArea { background: transparent; }")
        rows_scroll.viewport().setStyleSheet("background: transparent;")
        con.addWidget(rows_scroll, 1)
        console.setMinimumHeight(380)
        lay.addWidget(console, 1)  # prende tutta l'altezza rimasta, sempre la stessa
        return rail

    def _build_pair_card(self) -> QWidget:
        """Riquadro "Nuovo nodo trovato → Abbina": compare solo mentre un nodo nuovo è abbinabile."""
        card = QFrame()
        card.setObjectName("pairCard")
        card.setStyleSheet(f"QFrame#pairCard {{ border:1px solid {T.INFO}; border-radius:8px; "
                           f"background: rgba(91,155,240,0.10); }}")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)
        self.pair_text = QLabel()
        self.pair_text.setWordWrap(True)
        self.pair_text.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.pair_text)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.pair_button = QPushButton(_('  Abbina…'))
        self.pair_button.setIcon(T.icon("link", "#FFFFFF", 16))
        self.pair_button.setStyleSheet(f"QPushButton {{ background:{T.INFO}; color:#FFFFFF; font-weight:600; }}")
        self.pair_button.clicked.connect(self._pair_found_node)
        ignore = QPushButton(_("Ignora"))
        ignore.setToolTip(_('Non proporlo più fino al riavvio di Mirto'))
        ignore.clicked.connect(self._ignore_found_node)
        row.addWidget(self.pair_button)
        row.addWidget(ignore)
        row.addStretch(1)
        lay.addLayout(row)
        card.hide()
        self.pair_card = card
        self._pair_found: dict | None = None
        return card

    def _update_pair_card(self) -> None:
        found = self.core.actions.pairing.current() if self.core.actions else []
        self._pair_found = found[0] if found else None
        self.pair_card.setVisible(bool(found))
        if not found:
            return
        f = found[0]
        quanto = (_('· pronto da abbinare') if f.get("senza_scadenza")
                  else _('· abbinabile ancora {mins} min', mins=max(1, f["scade_tra_s"] // 60)))
        more = _("<br><span style='color:{faint}'>e altri {v}</span>", faint=T.FAINT, v=len(found) - 1) if len(found) > 1 else ""
        self.pair_text.setText(_("<b style='color:{info}'>Nuovo nodo trovato</b><br><b>{escape}</b> <span style='color:{muted}'>{quanto}</span>{more}", info=T.INFO, escape=html.escape(f['nome']), muted=T.MUTED, quanto=quanto, more=more))

    def _pair_found_node(self) -> None:
        from .pairing_ui import pair_node_dialog  # noqa: PLC0415

        found = self._pair_found
        if not found:
            return
        name = pair_node_dialog(self, self.core, dict(found, scade_tra_s=max(0, int(found["scade"] - time.time()))))
        self._update_pair_card()
        if not name:
            return
        if QMessageBox.question(
                self, _('Nodo abbinato'),
                _('{name} è abbinato: le sue camere compaiono da sole in pochi secondi.\n\nVuoi creare adesso le sue azioni (sirena, luci…)? Si apre Impostazioni → Azioni: con «Libreria azioni» importi quelle già pronte, poi premi Salva.', name=name)) == QMessageBox.StandardButton.Yes:
            self._open_settings(tab="Azioni", node_name=name)

    def _ignore_found_node(self) -> None:
        if self._pair_found and self.core.actions:
            self.core.actions.pairing.ignore(self._pair_found["impronta"])
        self._update_pair_card()

    def _build_cameras(self) -> QWidget:
        """Parte centrale: intestazione con le due viste, poi griglia, camera ingrandita o "Tutte le cam"."""
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(6)
        self.view_group = QButtonGroup(self)
        self.view_group.setExclusive(True)
        self.tab_grid = QPushButton()
        self.tab_all = QPushButton()
        self.tab_review = QPushButton(_("  Rivedi"))
        self.tab_review.setToolTip(_("Le registrazioni giorno per giorno, con gli eventi sulla barra delle 24 ore"))
        # «Nodi» compare **solo se ce n'è almeno uno**: in una casa senza nodi è una scheda che non
        # si potrà mai aprire, e una scheda vuota insegna a non premerla (N-03, 25/10).
        self.tab_nodi = QPushButton(_("  Nodi"))
        self.tab_nodi.setToolTip(_("Tutti i nodi in una tabella: stato, versione, spazio, temperatura e ultimo evento"))
        self.tab_nodi.setVisible(False)
        for b, icon_name in ((self.tab_grid, "layout-grid"), (self.tab_all, "list-checks"),
                             (self.tab_review, "clock"), (self.tab_nodi, "shield-check")):
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # la barra spaziatrice resta a Rivedi (riproduci/pausa)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setIcon(T.icon(icon_name, T.TEXT, 18))
            self.view_group.addButton(b)
            head.addWidget(b)
        self.tab_grid.setChecked(True)
        self.tab_grid.clicked.connect(lambda: self._show_grid())
        self.tab_all.clicked.connect(lambda: self._show_all_cams())
        self.tab_review.clicked.connect(lambda: self._show_review())
        self.tab_nodi.clicked.connect(lambda: self._show_nodi())
        head.addSpacing(10)
        self.back_button = action_button(_('Torna alla griglia'), "arrow-left", obj="small", color=T.MUTED)
        self.back_button.setIconSize(QSize(16, 16))
        self.back_button.clicked.connect(lambda: self._show_grid())
        self.back_button.hide()
        head.addWidget(self.back_button)
        self.single_photo_button = action_button(_("Foto"), "camera", obj="small", color=T.MUTED,
                                                 tooltip=_('Salva e apre una foto di questa camera'))
        self.single_photo_button.setIconSize(QSize(16, 16))
        self.single_photo_button.clicked.connect(lambda: self.single_id and self._snapshot_camera(self.single_id))
        self.single_photo_button.hide()
        head.addWidget(self.single_photo_button)
        head.addStretch(1)
        self.view_hint = label(obj="faint")
        head.addWidget(self.view_hint)
        lay.addLayout(head)

        self.grid = CameraGrid()
        self.grid.set_size(self.core.config.get("gui.grid_size", "medium"))
        # cursore in basso a destra, come in Foto e nel Finder: grandezza delle anteprime
        self.size_slider = SizeSlider(self.grid.size_value())
        self.size_slider.changed.connect(self._grid_size_changed)
        self.add_tile = AddTile()
        self.add_tile.clicked.connect(lambda: self._show_all_cams())
        self.single = CameraTile("", "")
        self.single.double_clicked.connect(lambda _id: self._show_grid())
        self.single.photo_requested.connect(self._snapshot_camera)
        self.single.setToolTip(_('Doppio clic per tornare alla griglia'))
        self.stack = QStackedWidget()
        self.stack.addWidget(self.grid)
        self.stack.addWidget(self.single)
        self.stack.addWidget(self._build_all_cams())
        from .review import ReviewView  # noqa: PLC0415

        self.review = ReviewView(self.core)
        self.stack.addWidget(self.review)
        from .flotta import VistaFlotta  # noqa: PLC0415

        self.flotta = VistaFlotta()
        self.flotta.apri_nodo.connect(self._apri_impostazioni_sul_nodo)
        self.flotta.apri_diagnosi.connect(lambda nid: self._apri_impostazioni_sul_nodo(nid, diagnosi=True))
        self.flotta.aggiorna_nodo.connect(self._aggiorna_nodo_dalla_flotta)
        self.stack.addWidget(self.flotta)
        lay.addWidget(self.stack, 1)
        foot = QHBoxLayout()
        foot.setContentsMargins(0, 0, 4, 0)
        foot.addStretch(1)
        foot.addWidget(self.size_slider)
        lay.addLayout(foot)
        self._update_view_header()
        return host

    def _build_all_cams(self) -> QWidget:
        """Sezione "Tutte le cam": ogni camera vista dal computer, con l'interruttore per usarla o no."""
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        intro = label(_("Qui ci sono tutte le camere che il computer vede. Accendi l'interruttore per farle usare a Mirto: compaiono nella griglia, vengono controllate e mandano gli avvisi. Le camere spente non vengono nemmeno aperte (la lucina resta spenta)."), obj="muted")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        # le scritte delle schede vanno a capo; la barra orizzontale (ora con lo stile del tema)
        # compare solo se la finestra è davvero più stretta dei pulsanti, così nulla resta nascosto
        inner = QWidget()
        self.cards_lay = QVBoxLayout(inner)
        self.cards_lay.setContentsMargins(0, 0, 6, 0)
        self.cards_lay.setSpacing(10)
        self.cards_empty = label(_('Nessuna camera trovata.\nCollega una camera oppure controlla il permesso in Impostazioni di Sistema → Privacy e sicurezza → Fotocamera.'), obj="muted")
        self.cards_empty.setWordWrap(True)
        self.cards_lay.addWidget(self.cards_empty)
        self.cards_lay.addStretch(1)
        scroll.setWidget(inner)
        self.cards_scroll = scroll
        lay.addWidget(scroll, 1)

        # Camere che si annunciano in rete (mDNS). Il riquadro resta SEMPRE visibile e le
        # righe non vengono mai distrutte: farlo apparire e sparire lasciava su macOS
        # un rettangolo vuoto non ridisegnato nello spazio liberato.
        self.found_box = QFrame()
        self.found_box.setObjectName("camCard")
        found_lay = QVBoxLayout(self.found_box)
        found_lay.setContentsMargins(14, 10, 14, 10)
        found_lay.setSpacing(6)
        head = QHBoxLayout()
        head_icon = QLabel()
        head_icon.setPixmap(T.icon_pixmap("wifi", T.INFO, 18))
        head.addWidget(head_icon)
        head.addWidget(label(_('Trovate in rete'), weight=700))
        head.addStretch(1)
        # stato dei controlli di rete: si vede che Mirto sta guardando, quasi in tempo reale
        self.net_status = label("", obj="faint")
        head.addWidget(self.net_status)
        self.net_refresh_btn = action_button(_('Ricontrolla ora'), "refresh-cw", obj="small", color=T.MUTED,
                                             tooltip=_('Cerca subito le camere in rete e ricontrolla gli indirizzi'))
        self.net_refresh_btn.setIconSize(QSize(16, 16))
        self.net_refresh_btn.clicked.connect(self._net_refresh_now)
        head.addWidget(self.net_refresh_btn)
        found_lay.addLayout(head)
        self.found_empty = label("", obj="faint")
        self.found_empty.setWordWrap(True)
        found_lay.addWidget(self.found_empty)
        self.found_rows = QVBoxLayout()
        self.found_rows.setSpacing(4)
        found_lay.addLayout(self.found_rows)
        self.found_pool: list[dict] = []   # righe riutilizzate: nascoste quando non servono
        self.found_cams: list[dict] = []
        self.found_keys: list[str] | None = None
        lay.addWidget(self.found_box)

        row = QHBoxLayout()
        add_net = action_button(_('Aggiungi camera di rete…'), "plus", obj="small", color=T.MUTED,
                                tooltip=_('Camera IP con indirizzo RTSP o HTTP'))
        add_net.setIconSize(QSize(16, 16))
        add_net.clicked.connect(self._add_network_camera)
        row.addWidget(add_net)
        self.unhide_btn = action_button(_('Mostra le camere nascoste'), "eye", obj="small", color=T.MUTED,
                                        tooltip=_('Rimette nel riquadro le camere trovate in rete che avevi nascosto'))
        self.unhide_btn.setIconSize(QSize(16, 16))
        self.unhide_btn.clicked.connect(self._unhide_found)
        self.unhide_btn.setVisible(False)
        row.addWidget(self.unhide_btn)
        row.addStretch(1)
        lay.addLayout(row)
        self.cards_timer = QTimer(self)
        self.cards_timer.timeout.connect(self._refresh_cards)
        return page

    def _build_timeline(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("timeline")
        panel.setFixedHeight(196)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(label(_("Eventi"), size=T.SIZE_TITLE, weight=700))
        self.events_count = label(obj="faint")
        head.addWidget(self.events_count)
        head.addSpacing(12)
        # Foto | Video: tutte le foto degli eventi, oppure solo gli eventi che hanno il video
        self.events_mode = self.core.config.get("gui.events_view", "photos")
        self.events_mode_group = QButtonGroup(self)
        self.events_mode_group.setExclusive(True)
        self.ev_photos_btn = QPushButton(_("Foto"))
        self.ev_videos_btn = QPushButton(_("Video"))
        for b, mode, tip in ((self.ev_photos_btn, "photos", _("Tutti gli eventi, con la loro foto")),
                             (self.ev_videos_btn, "videos", _("Solo gli eventi con il video: doppio clic per guardarlo"))):
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setChecked(self.events_mode == mode)
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _c=False, m=mode: self._set_events_mode(m))
            self.events_mode_group.addButton(b)
            head.addWidget(b)
        head.addStretch(1)
        lay.addLayout(head)

        self.events = QListWidget()
        self.events.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        # una riga orizzontale di foto, dalla più recente a sinistra; scorre in orizzontale.
        # Ogni evento è un'icona (foto con l'ora sopra): niente widget dentro la lista, più robusto e leggero.
        self.events.setViewMode(QListWidget.ViewMode.IconMode)
        self.events.setFlow(QListWidget.Flow.LeftToRight)
        self.events.setWrapping(False)
        self.events.setMovement(QListWidget.Movement.Static)
        self.events.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.events.setUniformItemSizes(True)
        self.events.setIconSize(QSize(208, 117))
        self.events.setGridSize(QSize(218, 127))
        self.events.setSpacing(0)
        self.events.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.events.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.events.itemDoubleClicked.connect(self._open_event)
        self.events.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
        act_del = QAction(_('Elimina selezionati'), self.events)
        act_del.setShortcuts([QKeySequence(QKeySequence.StandardKey.Delete), QKeySequence(Qt.Key.Key_Backspace)])
        act_del.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        act_del.triggered.connect(self._delete_selected_events)
        act_review = QAction(_('Rivedi in Mirto'), self.events)
        act_review.triggered.connect(self._review_event)
        self.events.addAction(act_review)
        act_photo = QAction(_('Apri la foto'), self.events)
        act_photo.triggered.connect(self._open_event_photo)
        self.events.addAction(act_photo)
        self.events.addAction(act_del)
        for ev in self.core.store.recent(100):
            self._add_event_item(ev, top=False)
        lay.addWidget(self.events, 1)

        self.events_empty = label(_('Nessun evento.\nQuando viene rilevata una persona compare qui.'), obj="faint")
        self.events_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.events_empty.setWordWrap(True)
        lay.addWidget(self.events_empty)

        row = head  # i pulsanti stanno nell'intestazione, a destra
        for text, icon_name, slot, tip in (
            (_("Cartella"), "folder-open", lambda: open_file(data_dir() / "events"), _('Apri la cartella delle foto e dei video')),
            (_("Elimina"), "trash", self._delete_selected_events, _('Elimina gli eventi selezionati (tasto ⌫)')),
            (_("Svuota"), "x", self._delete_all_events, _('Elimina tutti gli eventi e le loro foto')),
        ):
            btn = action_button(text, icon_name, obj="small", color=T.MUTED, tooltip=tip)
            btn.setIconSize(QSize(14, 14))
            btn.setStyleSheet("QPushButton { padding: 6px 6px; }")
            btn.clicked.connect(slot)
            row.addWidget(btn)
        self._update_events_empty()
        return panel

    def _grid_size_changed(self, value: int) -> None:
        self.grid.set_size(value)
        # salvataggio un attimo dopo l'ultimo movimento, non a ogni pixel
        if not hasattr(self, "_size_save"):
            self._size_save = QTimer(self)
            self._size_save.setSingleShot(True)
            self._size_save.timeout.connect(self._save_grid_size)
        self._size_save.start(600)

    # ---- Sistema: le righe che si aprono e si chiudono --------------------------------------
    #
    # *Sistema* dice molte cose e le dice tutte insieme: chiuse, le righe lunghe stanno dietro un più.
    # Quello che è aperto se lo ricorda la configurazione, così chi tiene d'occhio i nodi non li riapre
    # a ogni avvio (scelto con Marco il 22/10). Le chiavi sono "camere", "ai", "notifiche", "suoni",
    # "registrazione" e "nodo:<id>" per ogni nodo.

    def _aperte(self) -> set[str]:
        if not hasattr(self, "_sistema_aperte"):
            salvate = self.core.config.get("gui.sistema_aperte") or []
            self._sistema_aperte = {str(x) for x in salvate if isinstance(x, (str, int))}
        return self._sistema_aperte

    def _aperta(self, chiave: str) -> bool:
        return chiave in self._aperte()

    def _nodi_aperti(self) -> set[str]:
        return {c[len("nodo:"):] for c in self._aperte() if c.startswith("nodo:")}

    def _sistema_apri_chiudi(self, chiave: str) -> None:
        # «aggiorna:<id>» non apre niente: porta dove l'aggiornamento si fa. Sta qui perche' i
        # collegamenti di *Sistema* arrivano tutti da questa porta, e una porta sola e' piu' facile
        # da tenere onesta di due.
        if chiave == "flotta":
            self._show_nodi()
            return
        if chiave.startswith("azione:"):
            from urllib.parse import unquote  # noqa: PLC0415

            nome = unquote(chiave[len("azione:"):])
            stato = self.core.accendi_o_spegni_azione(nome)
            if stato:
                self._info(_('Azione «{nome}»: adesso è {stato}.', nome=nome, stato=stato))
            self._update_status()
            return
        if chiave.startswith("aggiorna:"):
            self._apri_impostazioni_sul_nodo(chiave[len("aggiorna:"):], aggiorna=True)
            return
        if chiave.startswith("volume:"):
            # «volume:<nodo>:<percento>»: il nodo può avere i due punti nel nome? No — un id di nodo
            # non li ha — ma si divide da destra comunque, così l'unica cosa che conta (il numero) non
            # dipende da com'è fatto il resto.
            quale, _due, quanto = chiave[len("volume:"):].rpartition(":")
            if quale and quanto.isdigit():
                msg = self.core.volume_cassa(quale, int(quanto))
                if msg:
                    self._info(msg)
                self._update_status()
            return
        if chiave.startswith("prank:"):
            # Da *Sistema* si **spegne** e basta: accenderla vuol dire scegliere per quanto, e una
            # scelta non si fa con un clic su una riga di riassunto. Si accende dalla scheda del nodo.
            msg = self.core.prank(chiave[len("prank:"):], 0)
            if msg:
                self._info(msg)
            self._update_status()
            return
        aperte = self._aperte()
        aperte.discard(chiave) if chiave in aperte else aperte.add(chiave)
        self.core.config.set("gui.sistema_aperte", sorted(aperte))
        try:
            self.core.config.save()
        except Exception:
            log.exception("Salvataggio delle righe aperte in Sistema fallito")
        self._update_status()

    def _save_grid_size(self) -> None:
        self.core.config.set("gui.grid_size", int(self.size_slider.value()))
        try:
            self.core.config.save()
        except Exception:
            log.exception("Salvataggio della grandezza delle anteprime fallito")

    def _test_items(self) -> list:
        """Le prove, usate nella scheda Impostazioni → Prove e nel menu del Mac."""
        return [
            (_('Invia un avviso di prova completo'), "circle-alert", self._test_alert,
             _('Foto dal vivo su Telegram, email ed eventuali SMS')),
            (_('Messaggio Telegram di prova'), "send", self._test_telegram, _('Solo testo nel gruppo')),
            (_('Email di prova'), "mail", self._test_email, _('Ai destinatari configurati')),
            (_('SMS di prova'), "send", self._test_sms, _('Al primo numero configurato')),
            None,
            (_('Scatta una foto da tutte le camere'), "camera", self._snapshot, _('Per controllare le inquadrature')),
            (_("Controlla i nodi"), "shield", self._test_nodes, _('Raggiungibilità dei dispositivi esterni')),
            None,
            (_('Esegui i test automatici del programma'), "flask-conical", self._run_unit_tests,
             "Circa 30 secondi"),
        ]

    def _build_test_menu(self) -> QMenu:
        m = QMenu(_("Prove"), self)
        for it in self._test_items():
            if it is None:
                m.addSeparator()
                continue
            act = QAction(T.icon(it[1], T.TEXT, 18), it[0], self)
            act.triggered.connect(it[2])
            m.addAction(act)
        return m

    def _build_menu_bar(self) -> None:
        bar = self.menuBar()
        app_menu = bar.addMenu("Mirto")
        a = QAction(_('Impostazioni…'), self)
        a.setShortcut("Ctrl+,")
        a.triggered.connect(self._open_settings)
        app_menu.addAction(a)
        w = QAction(_('Chiudi finestra (resta in background)'), self)
        w.setShortcut("Ctrl+W")
        w.triggered.connect(self.close)
        app_menu.addAction(w)
        # Cmd+Q (Ctrl+Q su Windows) esce DAVVERO, dopo conferma: anche con "Non chiederlo più" attivo
        q = QAction(_('Esci da Mirto'), self)
        q.setShortcut(QKeySequence.StandardKey.Quit)
        q.setMenuRole(QAction.MenuRole.QuitRole)
        q.triggered.connect(lambda: self.quit_from_tray())
        app_menu.addAction(q)
        if sys.platform == "darwin":
            # QuitRole, su macOS, **sposta** la voce nel menu dell'applicazione (quello accanto
            # alla mela). Serve: così ⌘Q e il «Quit» di sistema passano dalla nostra conferma
            # invece di ammazzare il programma e lasciare le camere a metà. Il prezzo era che dal
            # menu «Mirto» la voce spariva, mentre su Windows restava: due menu diversi sulle due
            # macchine, e sul Mac nessun modo di uscire se non sapevi la scorciatoia (Marco, 7/11).
            # Qui la rimettiamo visibile. Senza scorciatoia, perché ce l'ha già quella spostata e
            # due voci con ⌘Q litigherebbero.
            esci = QAction(_('Esci da Mirto'), self)
            esci.setMenuRole(QAction.MenuRole.NoRole)
            esci.triggered.connect(lambda: self.quit_from_tray())
            app_menu.addAction(esci)
        bar.addMenu(self._build_test_menu())
        help_menu = bar.addMenu(_("Aiuto"))
        h1 = QAction(_('Comandi Telegram'), self)
        h1.triggered.connect(lambda: TextDialog(_('Comandi Telegram'), _(TELEGRAM_HELP), self).exec())
        help_menu.addAction(h1)
        h2 = QAction(_('Guida utente'), self)
        h2.triggered.connect(self._open_readme)
        help_menu.addAction(h2)
        h3 = QAction(_('Apri la cartella dei log'), self)
        h3.triggered.connect(lambda: open_file(data_dir() / "logs"))
        help_menu.addAction(h3)
        h6 = QAction(_('Sostieni Mirto'), self)
        h6.triggered.connect(self._sostieni)
        help_menu.addAction(h6)
        h5 = QAction(_("Condizioni d'uso"), self)
        h5.triggered.connect(self._legal_notice)
        help_menu.addAction(h5)
        h4 = QAction(_('Informazioni su Mirto'), self)
        h4.triggered.connect(self._informazioni)
        help_menu.addAction(h4)

    def _informazioni(self) -> None:
        """Informazioni: la versione, e **quale codice sta girando**.

        L'ultima riga è quella che conta, e c'è per un motivo preciso: il 23/10 Marco ha provato per
        mezz'ora una finestra avviata tre ore prima, mentre sul disco c'era già la correzione. Né lui
        né io potevamo accorgercene guardando. Adesso si vede da qui, e se il disco è più avanti il
        programma lo dice a voce alta invece di lasciar perdere tempo a qualcuno.
        """
        from ..versione import avviso_codice_vecchio, riga  # noqa: PLC0415

        testo = _('Mirto {version}\nVideosorveglianza locale con avvisi Telegram ed email.\n\n'
                  'Icone: Lucide (licenza ISC).', version=__version__) + "\n\n" + riga()
        vecchio = avviso_codice_vecchio()
        if vecchio:
            QMessageBox.warning(self, "Mirto", testo + "\n\n⚠ " + vecchio)
            return
        QMessageBox.information(self, "Mirto", testo)

    # ------------------------------------------------------------------ utilità
    def _info(self, text: str) -> None:
        self.statusBar().showMessage(text, 12000)

    def _bg(self, fn, on_done) -> None:
        """Esegue ``fn`` in un thread e passa il risultato a ``on_done`` nel thread dell'interfaccia."""
        def work():
            try:
                res = fn()
            except Exception as exc:  # noqa: BLE001
                res = _('Errore: {exc}', exc=exc)
            self.bridge.done.emit((on_done, res))
        threading.Thread(target=work, daemon=True).start()

    def _popup(self, text) -> None:
        QMessageBox.information(self, "Mirto", str(text))

    # ------------------------------------------------------------------ comandi
    def _toggle_armed(self) -> None:
        armed = bool(self.core.config.get("actions.armed", False))
        if not armed:
            ok = QMessageBox.question(
                self, _('Attiva protezione'),
                _("Attivando la protezione riceverai gli avvisi (Telegram, email) e, se configurate, partiranno le azioni dei nodi (sirena, luci, altre uscite) quando viene confermata un'intrusione.\n\nContinuare?"),
            )
            if ok != QMessageBox.StandardButton.Yes:
                return
        self._info(self.core.handlers.arm(not armed))
        self._update_status()

    def _toggle_silence(self) -> None:
        st = self.core.alert_state()
        if st["paused_until"] or st["camera_paused"]:
            self._info(self.core.handlers.resume())
        else:
            self._info(self.core.handlers.pause(60))
        self._update_status()

    def _stop_all(self) -> None:
        # La domanda dice quello che succede **adesso**: a protezione gia' disinserita, prometterne
        # la disattivazione e' una frase che non corrisponde a niente, e fa esitare proprio chi ha
        # fretta di zittire qualcosa (25/10).
        domanda = (_('Spegnere tutte le azioni sui nodi e disattivare la protezione?')
                   if self.core.config.get("actions.armed", False)
                   else _('Spegnere tutte le azioni in corso sui nodi?'))
        if confirm_warning(self, _('Ferma tutto'), domanda, _('Ferma tutto')):
            self._info(self.core.handlers.stop_all())
            self._update_status()

    def _livelli_dei_microfoni(self) -> dict:
        """``{id del nodo: livello}`` dei microfoni che Mirto sta davvero ascoltando.

        Il livello non si chiede al nodo: lo misura già il riconoscitore dei suoni su quel flusso.
        Un nodo che non compare qui ha il microfono acceso ma nessuno che lo ascolti, ed è una
        differenza che vale la pena vedere — è la stessa che il 24/10 aveva fatto credere a Marco
        che il microfono USB non funzionasse.
        """
        fuori = {}
        audio = getattr(self.core, "audio", None)
        for s in (audio.status() if audio else []):
            ident = str(s.get("id") or "")
            if not ident.startswith("node:"):
                continue
            # La chiave c'è se Mirto **ha** quella sorgente; il valore è il livello, oppure None
            # se il flusso non sta arrivando. Sono due cose diverse e vanno distinte: «non lo
            # ascolto» e «lo ascolto e non sento niente» mandano a cercare in posti diversi.
            # Due numeri, non uno: il **livello** riempie la barretta (si muove, ed è quello che
            # fa vedere che il microfono sente), il **picco tenuto** serve al giudizio sul guadagno.
            # L'RMS da solo non lo sa dare: fra una parola e l'altra scende a zero.
            fuori[ident[len("node:"):]] = ({"livello": float(s.get("level") or 0.0),
                                            "picco": float(s.get("picco") or 0.0)}
                                           if s.get("live") else None)
        return fuori

    def _apri_impostazioni_sul_nodo(self, nid: str, aggiorna: bool = False, diagnosi: bool = False) -> None:
        """Da *Sistema* al posto dove il nodo si aggiorna, in un clic.

        Si va alla scheda **Nodi** con quel nodo gia' scelto: la' il nastro «Aggiornamento
        disponibile» e' in cima con il suo pulsante. Si potrebbe anche far partire la domanda da
        qui, e di proposito non si fa: chi clicca da Sistema sta guardando lo stato, non ha ancora
        detto «fallo adesso» (25/10).
        """
        nome = ""
        for nd in (self.core.config.get("actions.nodes") or []):
            if str(nd.get("id")) == str(nid):
                nome = str(nd.get("name") or "")
        self._open_settings(tab="Nodi", node_name=nome, aggiorna_nodo=aggiorna, stato_nodo=diagnosi)

    def _open_settings(self, tab: str = "", node_name: str = "", aggiorna_nodo: bool = False,
                       stato_nodo: bool = False) -> None:
        from .settings import SettingsDialog  # noqa: PLC0415

        SettingsDialog(self.core, self, tests=self._test_items(), tab=tab, node_name=node_name,
                       aggiorna_nodo=aggiorna_nodo, stato_nodo=stato_nodo).exec()
        self._update_status()

    def _sostieni(self) -> None:
        """Aiuto → Sostieni Mirto: dice a cosa servono i soldi e apre la pagina solo se l'utente vuole."""
        from PySide6.QtGui import QDesktopServices  # noqa: PLC0415
        from PySide6.QtCore import QUrl  # noqa: PLC0415

        scelta = QMessageBox.question(
            self, _('Sostieni Mirto'),
            _('Mirto è gratuito e open source, e resta così: niente versione a pagamento, niente '
              'funzioni chiuse, nessuna pubblicità.\n\nLe donazioni coprono le spese che un programma '
              'come questo ha comunque: i certificati per firmare i download su macOS e Windows, '
              'l\'hardware su cui provarlo, i server per gli aggiornamenti.\n\nVuoi aprire la pagina?'),
            QMessageBox.StandardButton.Open | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Open)
        if scelta == QMessageBox.StandardButton.Open:
            QDesktopServices.openUrl(QUrl(SOSTIENI_URL))

    def _open_readme(self) -> None:
        """Guida utente dentro l'app (si apre all'indice)."""
        from .guide import open_guide  # noqa: PLC0415

        open_guide(self)

    def _snapshot_camera(self, cam_id: str) -> None:
        """Foto di una sola camera (pulsante Foto della scheda, tasto destro sul riquadro)."""
        f = self.core.snapshot_camera(cam_id, data_dir() / "foto")
        if f is None:
            self._popup(_('Nessuna immagine disponibile da questa camera in questo momento.'))
            return
        open_file(f)
        self._info(_('Foto salvata in {f}', f=f))

    def _snapshot(self) -> None:
        files = self.core.snapshot_all(data_dir() / "prove")
        if not files:
            self._popup(_('Nessuna immagine disponibile dalle camere.'))
            return
        for f in files:
            open_file(f)
        self._info(_('Salvate {len} foto in {v}', len=len(files), v=data_dir() / 'prove'))

    # --- prove ---
    def _test_alert(self) -> None:
        if QMessageBox.question(
            self, _('Avviso di prova'),
            _('Invio un avviso di PROVA completo (foto dal vivo) su Telegram, email ed eventuali SMS,\nanche se la protezione è disattivata. Procedere?')) == QMessageBox.StandardButton.Yes:
            self._popup(self.core.send_test_alert())

    def _test_telegram(self) -> None:
        n = self.core.notifier
        self._bg(lambda: _('Messaggio Telegram inviato.') if n.send_text_now(_('✅ Mirto: messaggio di prova'))
                 else _('Invio non riuscito: controlla Impostazioni → Notifiche → Telegram (token, ID del gruppo).'), self._popup)

    def _test_email(self) -> None:
        from ..notify.email import EmailNotifier  # noqa: PLC0415

        cfg = self.core.config.get("notifications.email") or {}
        if not cfg.get("enabled"):
            self._popup(_("L'email è spenta: attivala in Impostazioni → Notifiche → Email."))
            return
        n = EmailNotifier(cfg, self.core.secrets)

        def go():
            n.send(n.build_text_message(_('Mirto: email di prova'), _('Se leggi questa email, Mirto può inviarti gli avvisi.')))
            self.core.notifier.last_error.pop("email", None)
            return _('Email di prova inviata.')
        self._bg(go, self._popup)

    def _test_sms(self) -> None:
        from ..notify.email import EmailNotifier  # noqa: PLC0415
        from ..notify.sms import make_provider  # noqa: PLC0415

        cfg = self.core.config.get("notifications.sms") or {}
        if not cfg.get("enabled") or not cfg.get("recipients"):
            self._popup(_('Gli SMS sono spenti o senza destinatari (Impostazioni → Notifiche → SMS).'))
            return
        prov = make_provider(cfg, self.core.secrets,
                             EmailNotifier(self.core.config.get("notifications.email") or {}, self.core.secrets))

        def go():
            prov.send(cfg["recipients"][0], _('Mirto: SMS di prova'))
            self.core.notifier.last_error.pop("sms", None)
            return _('SMS di prova inviato.')
        self._bg(go, self._popup)

    def _test_nodes(self) -> None:
        eng = self.core.actions
        if not eng or not (self.core.config.get("actions.nodes") or []):
            self._popup(_('Nessun nodo configurato (Impostazioni → Nodi).'))
            return

        def go():
            eng.check_nodes()
            return "\n".join(eng.status_lines())
        self._bg(go, self._popup)

    def _run_unit_tests(self) -> None:
        from ..selftest import run_unit_tests  # noqa: PLC0415

        self._info(_('Test automatici in corso (circa 30 secondi)…'))

        def show(res):
            if isinstance(res, str):
                self._popup(res)
                return
            ok, text = res
            TextDialog(_('Test superati ✅') if ok else _('Alcuni test NON superati ❌'), text, self).exec()
        self._bg(run_unit_tests, show)

    # ------------------------------------------------------------------ stato
    def _update_status(self) -> None:
        core = self.core
        # ora dell'ultimo aggiornamento del riquadro Sistema
        self.console_clock.setText("aggiornato " + time.strftime("%H:%M:%S"))
        try:
            self.stop_button.setVisible(core.stop_all_visible())
        except Exception:
            log.exception("Stato di Ferma tutto non disponibile")
        st = core.alert_state()
        boot = core.startup_state()
        hhmm = lambda t: time.strftime("%H:%M", time.localtime(t))  # noqa: E731
        mode = st["mode"]
        color = T.STATE_COLORS[mode]
        self._update_tray()

        # stato principale
        word, detail = (_(x, computer=questo_computer()) if x else "" for x in STATE_WORDS[mode])
        if mode == "paused":
            mins = max(1, int((st["paused_until"] - time.time()) / 60))
            word = _('In pausa')
            detail = (_('Avvisi sospesi fino alle {hhmm} (ancora {mins} min). La protezione resta attiva.', hhmm=hhmm(st['paused_until']), mins=mins))
        if st["camera_paused"] and mode != "disarmed":
            names = ", ".join(_("{camera} fino alle {hhmm}", camera=core.camera_name(c), hhmm=hhmm(t)) for c, t in st["camera_paused"].items())
            detail += _('\nCamere silenziate: {names}.', names=names)
        if st["last_sent"]:
            detail += _('\nUltimo avviso inviato alle {hhmm}.', hhmm=hhmm(st['last_sent']))
        self.state_word.setText(word)
        self.state_word.setStyleSheet(f"font-size:{T.SIZE_STATE}px; font-weight:800; color:{color};")
        self.state_icon.set_mode(mode)
        if mode == "disarmed":
            self._clear_alarm()
        self.alarm_word.setVisible(self.state_icon.alarming())
        self.state_detail.setText(detail)
        self.band.set_color(color)
        self.setWindowTitle(f"{APP_NAME} – {word}")

        # comandi che cambiano lo stato
        if st["armed"]:
            self.arm_button.setText("  " + _("DISATTIVA PROTEZIONE"))
            self.arm_button.setIcon(T.icon("shield-off", T.TEXT, 20))
            self.arm_button.setStyleSheet("")
        else:
            self.arm_button.setText("  " + _("ATTIVA PROTEZIONE"))
            self.arm_button.setIcon(T.icon("shield-check", "#FFFFFF", 20))
            self.arm_button.setStyleSheet(f"QPushButton {{ background:{T.ARMED}; color:#FFFFFF; }}"
                                          f"QPushButton:hover {{ background:#27a073; }}")
        silenced = bool(st["paused_until"] or st["camera_paused"])
        # "Silenzia" serve solo con la protezione attiva e dopo un allarme (ultimi 30 minuti);
        # se gli avvisi sono già sospesi resta visibile come "Riattiva avvisi"
        recent_alarm = bool(st.get("last_sent")) and time.time() - st["last_sent"] < 30 * 60
        self.silence_button.setVisible(silenced or (st["armed"] and recent_alarm))
        if silenced:
            self.silence_button.setText(_('  Riattiva avvisi'))
            self.silence_button.setIcon(T.icon("bell", "#1B2330", 20))
            self.silence_button.setStyleSheet(f"QPushButton {{ background:{T.PAUSED}; color:#1B2330; }}")
        else:
            self.silence_button.setText("  " + _("Silenzia 1 ora"))
            self.silence_button.setIcon(T.icon("bell-off", T.TEXT, 20))
            self.silence_button.setStyleSheet("")

        # salute del sistema
        # le camere hanno la loro riga con i dettagli: qui solo il resto dell'avvio
        pending = [n for k, n in (("core", _("avvio")), ("telegram", "Telegram"), ("detector", _('AI Recognition')))
                   if boot[k]]
        self.row_boot.setVisible(bool(pending))
        if pending:
            self.row_boot.set(T.PAUSED, _('Avvio in corso: ') + ", ".join(pending) + "…")

        srcs = list(core.cameras.sources.values())
        online = sum(1 for s in srcs if s.status == "online")
        try:
            cams = core.cameras.all_cameras()
            new_net = len(core.cameras.discovered_network_cameras())
        except Exception:
            log.exception("Elenco camere non disponibile")
            cams, new_net = [], 0
        # quali camere sono trasmesse da un nodo (es. il Raspberry con la sirena)
        overview = core.actions.node_overview() if core.actions and core.config.get("actions.nodes") else []
        from ..actions.presets import match_node_cameras  # noqa: PLC0415

        cam_to_node, node_cams = match_node_cameras(core.config.get("cameras.network") or [], overview)
        node_names = {n["id"]: n["name"] for n in overview}
        self.cam_nodes = {cid: node_names.get(nid, nid) for cid, nid in cam_to_node.items()}
        aperta = self._aperta("camere")
        cam_detail = cameras_detail(cams, new_net, self.cam_nodes, aperta)
        # il più serve solo se c'è davvero qualcosa di nascosto: camere spente o non più collegate
        altre = any(not c["enabled"] or not c["connected"] for c in cams)
        tip = _('Camere usate da Mirto; si scelgono in Tutte le cam')
        quanto = dict(chiave="camere", aperto=aperta, espandibile=altre)
        if boot["cameras"]:
            self.row_cams.set(T.PAUSED, _("Camere: collegamento {online}/{total}…", online=online, total=len(srcs)) if srcs else _('Camere: ricerca…'),
                              tip, cam_detail, **quanto)
        elif not srcs:
            self.row_cams.set(T.PAUSED, _('Nessuna camera in uso'), tip, cam_detail, **quanto)
        elif online == len(srcs):
            self.row_cams.set(T.ARMED, _('Camere in linea {online}/{len}', online=online, len=len(srcs)), tip, cam_detail, **quanto)
        else:
            self.row_cams.set(T.ALARM, _('Camere in linea {online}/{len}', online=online, len=len(srcs)), tip, cam_detail, **quanto)

        model = MODEL_NAMES.get(core.config.get("detection.model", "yolox_nano"),
                                core.config.get("detection.model", ""))
        if boot["detector"]:
            self.row_ai.set(T.PAUSED, _('AI Recognition · {model}: caricamento…', model=model))
        elif core.detector is None:
            self.row_ai.set(T.DISARMED, _('AI Recognition · {model}', model=model), "",
                            _('In attesa di una camera attiva'))
        else:
            aperta = self._aperta("ai")
            self.row_ai.set(T.ARMED, _('AI Recognition · {model}', model=model), _('Riconoscimento delle persone, tutto in locale'),
                            ai_detail(core) if aperta else "", chiave="ai", aperto=aperta, espandibile=True)

        self._update_audio_row()
        self._update_rec_row()

        errors = dict(core.notifier.last_error)  # copia: il dict cambia in un altro thread
        bot = core.notifier.bot
        channels = []
        tg_detail = telegram_detail(bot.info if bot else {}, bool(bot and bot.chat_id))
        if boot["telegram"]:
            channels.append(("wait", _('Telegram: collegamento…'), ""))
        elif not bot:
            channels.append(("off", _('Telegram non configurato'), ""))
        elif bot.online and "telegram" not in errors:
            channels.append(("ok", _('Telegram collegato'), tg_detail))
        else:
            channels.append(("error", _('Telegram non raggiungibile'),
                             _esc(errors.get("telegram", "")) + ("<br>" + tg_detail if tg_detail else "")))
        mail = core.config.get("notifications.email") or {}
        mail_detail = email_detail(mail)
        if not mail.get("enabled"):
            channels.append(("off", _('Email spenta'), ""))
        elif "email" in errors:
            channels.append(("error", _('Email: errore di invio'), _esc(errors["email"]) + "<br>" + mail_detail))
        else:
            channels.append(("ok", _('Email pronta'), mail_detail))
        sms = core.config.get("notifications.sms") or {}
        if sms.get("enabled"):
            channels.append(("error", _("SMS: errore di invio"), _esc(errors["sms"])) if "sms" in errors
                            else ("ok", _("SMS pronti ({provider})", provider=_esc(sms.get("provider", ""))), ""))
        n_wait, wait_on = core.notifier.pending_summary()
        if n_wait:  # avvisi caduti che il dispatcher sta ancora ritentando (fino a 24 ore)
            what = ", ".join(wait_on)
            channels.append(("wait", _('1 avviso in attesa di invio ({what})', what=what) if n_wait == 1
                             else _('{n} avvisi in attesa di invio ({what})', n=n_wait, what=what), ""))
        aperta = self._aperta("notifiche")
        level, title, detail, espandibile = notifications_summary(
            channels, bool(core.config.get("actions.armed", False)), aperta)
        self.row_notify.set(level, title, _("Impostazioni → Notifiche"), detail,
                            chiave="notifiche", aperto=aperta, espandibile=espandibile)

        nodes = core.config.get("actions.nodes") or []
        self.row_nodes.setVisible(bool(nodes))
        if nodes and core.actions:
            on = sum(1 for n in overview if n["online"])
            busy = any(n["active"] for n in overview)
            color = T.ALARM if on < len(nodes) or busy else T.ARMED
            # Chi ha un aggiornamento da fare lo deve leggere **sulla riga chiusa**: Sistema sta in
            # una colonna che si guarda di sfuggita, e una cosa da fare che compare solo aprendo un
            # nodo non la sa nessuno (Marco, 25/10). Il conto si fa qui, una volta, e serve sia al
            # titolo sia al collegamento dentro la riga di ogni nodo.
            da_aggiornare = {}
            for n in overview:
                agg = aggiornamento_del_nodo(n.get("info") or {})
                if agg and n.get("online"):
                    da_aggiornare[n["id"]] = agg[1]
            title = _('Nodi in linea {on}/{len}', on=on, len=len(nodes)) + (_(' · azione in corso') if busy else "")
            if da_aggiornare:
                title += (_(' · 1 da aggiornare') if len(da_aggiornare) == 1
                          else _(' · {n} da aggiornare', n=len(da_aggiornare)))
            # qui il più non sta sulla sezione: ce l'ha ogni nodo, dentro il testo, perché è il
            # singolo nodo che si vuole aprire quando i nodi sono cinque (chiesto il 22/10)
            self.row_nodes.set(color, title,
                               legenda_icone() + "\n" + _("Stato letto dai nodi: ogni minuto, ogni 5 s durante un'azione"),
                               riga_vista_flotta(len(nodes))
                               + nodes_detail(overview, node_cams, cams, self._nodi_aperti(), da_aggiornare,
                                              self._livelli_dei_microfoni(),
                                              armata=bool(core.actions.armed)))
            # La tabella della flotta si riempie da qui: le stesse letture, un vestito diverso. Si
            # calcola solo mentre quella pagina si guarda — e' una schermata, non un servizio.
            if hasattr(self, "tab_nodi"):
                self.tab_nodi.setVisible(True)
            if hasattr(self, "flotta") and self.stack.currentIndex() == 4:
                try:
                    ultimi = self.core.store.ultimo_per_camera()
                except Exception:  # noqa: BLE001  (il registro degli eventi non deve fermare una vista)
                    ultimi = {}
                self.flotta.mostra(righe_flotta(overview, node_cams, ultimi, da_aggiornare,
                                                armata=bool(core.actions.armed)))
        elif hasattr(self, "tab_nodi"):
            self.tab_nodi.setVisible(False)

        self._update_pair_card()

        pres = core.presence
        self.row_presence.setVisible(pres.enabled)
        if pres.enabled:
            if not pres.last_check:
                self.row_presence.set(T.PAUSED, _('Presenza: primo controllo…'))
            else:
                people = pres.people()
                inside = sum(1 for x in people if x["home"])
                lines = [f"&nbsp;&nbsp;{'🏠' if x['home'] else '🚶'} {html.escape(x['name'])} – "
                         f"{_('in casa') if x['home'] else _('fuori')}" for x in people]
                if pres.last_change:
                    lines.append(_('Ultimo cambio automatico: {escape}', escape=html.escape(pres.last_change)))
                self.row_presence.set(T.INFO if inside else T.DISARMED,
                                      _('Presenza: {inside} in casa su {len}', inside=inside, len=len(people)),
                                      _('Telefoni sul Wi-Fi di casa (Impostazioni → Presenza)'), "<br>".join(lines))

        has_rules = bool(core.config.get("schedule.rules"))
        self.row_sched.setVisible(has_rules)
        if has_rules:
            line = core.schedule_line().replace("⏰ ", "")
            self.row_sched.set(T.INFO if core.scheduler.enabled else T.DISARMED, line)

        self._update_view_header()

        # riquadri: camere silenziate e fine degli avvisi "persona"
        now = time.time()
        for cam_id, until in list(self.person_until.items()):
            if now > until:
                if cam_id in self.tiles:
                    self.tiles[cam_id].set_badge("")
                del self.person_until[cam_id]
        for cam_id, tile in self.tiles.items():
            if cam_id in self.person_until:
                continue
            muted = st["camera_paused"].get(cam_id)
            if muted:
                tile.set_badge(_('Silenziata fino alle {hhmm}', hhmm=hhmm(muted)), "warn")
            elif tile.badge_text.startswith(_("Silenziata fino alle {hhmm}", hhmm="").strip()):
                tile.set_badge("")

    def _clear_alarm(self) -> None:
        """Protezione spenta: toglie fascia lampeggiante, logo rosso, RILEVAMENTO e bordi rossi."""
        self.band.stop_flash() if self.band._timer.isActive() else None
        for cam_id in list(self.person_until):
            tile = self.tiles.get(cam_id)
            if tile:
                tile.set_badge("")
            if self.single_id == cam_id:
                self.single.set_badge("")
        self.person_until.clear()

    # ------------------------------------------------------------------ eventi
    def _on_event(self, ev) -> None:
        from ..detection.labels import display_name  # noqa: PLC0415

        label_key = getattr(ev, "label", "person")
        self._add_event_item({"id": ev.id, "ts": ev.ts, "camera_name": ev.camera_name, "score": ev.score,
                              "image_path": ev.image_path, "label": label_key})
        is_person = label_key == "person"
        if not self.core.config.get("actions.armed", False):
            return  # protezione spenta: l'evento resta nella cronologia, ma niente rosso né evidenze
        if is_person:
            self.band.flash(8)  # la fascia rossa solo per le persone
            self.state_icon.alarm(60, fast=8)  # logo rosso per un minuto, messa a fuoco nervosa all'inizio
            self.alarm_word.show()
        tile = self.tiles.get(ev.camera_id)
        if tile:
            what = _('Persona rilevata') if is_person else display_name(label_key)
            text = f"{what} alle {time.strftime('%H:%M:%S', time.localtime(ev.ts))}"
            level = "alert" if is_person else "object"
            if is_person or tile.badge_level != "alert":  # una persona resta in evidenza sopra il resto
                tile.set_badge(text, level)
                self.person_until[ev.camera_id] = time.time() + 60
                if self.single_id == ev.camera_id:
                    self.single.set_badge(text, level)

    def _add_event_item(self, ev: dict, top: bool = True) -> None:
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, ev.get("image_path"))
        item.setData(Qt.ItemDataRole.UserRole + 1, ev["id"])
        has_clip = False
        if ev.get("image_path"):
            from pathlib import Path  # noqa: PLC0415

            has_clip = Path(ev["image_path"]).with_suffix(".mp4").exists()
        item.setData(Qt.ItemDataRole.UserRole + 2, has_clip)
        item.setIcon(QIcon(event_thumbnail(ev)))
        tip = time.strftime("%d/%m/%Y %H:%M:%S", time.localtime(ev["ts"])) + f" · {ev['camera_name']}"
        if ev.get("image_path"):
            from pathlib import Path  # noqa: PLC0415

            if Path(ev["image_path"]).with_suffix(".mp4").exists():
                tip += "\n" + _("Con video: doppio clic nella vista Video, oppure tasto destro → Rivedi in Mirto")
        item.setToolTip(tip)
        if top:
            self.events.insertItem(0, item)
            while self.events.count() > 300:
                self.events.takeItem(self.events.count() - 1)
        else:
            self.events.addItem(item)
        item.setHidden(getattr(self, "events_mode", "photos") == "videos" and not has_clip)
        self._update_events_empty()

    def _set_events_mode(self, mode: str) -> None:
        """Striscia degli eventi: «photos» = tutti, «videos» = solo quelli con il video."""
        self.events_mode = mode
        for i in range(self.events.count()):
            it = self.events.item(i)
            it.setHidden(mode == "videos" and not it.data(Qt.ItemDataRole.UserRole + 2))
        self._update_events_empty()
        self.core.config.set("gui.events_view", mode)
        try:
            self.core.config.save()
        except Exception:
            log.exception("Salvataggio della vista degli eventi fallito")

    def _update_events_empty(self) -> None:
        videos = getattr(self, "events_mode", "photos") == "videos"
        n = sum(1 for i in range(self.events.count()) if not self.events.item(i).isHidden()) if videos \
            else self.events.count()
        if hasattr(self, "events_empty"):
            self.events_empty.setText(_("Nessun video degli eventi.\nCon la registrazione attiva ogni evento ha il suo "
                                        "video.") if videos else
                                      _('Nessun evento.\nQuando viene rilevata una persona compare qui.'))
            self.events_empty.setVisible(n == 0)
            self.events.setVisible(n > 0)
        if hasattr(self, "events_count"):
            self.events_count.setText(f"{n}" if n else "")

    def _delete_selected_events(self) -> None:
        items = self.events.selectedItems()
        if not items:
            self._info(_('Seleziona prima uno o più eventi.'))
            return
        if QMessageBox.question(self, _('Elimina eventi'),
                                _('Eliminare {len} eventi e le loro foto?', len=len(items))) != QMessageBox.StandardButton.Yes:
            return
        ids = [it.data(Qt.ItemDataRole.UserRole + 1) for it in items]
        n = self.core.store.delete([i for i in ids if i])
        for it in items:
            self.events.takeItem(self.events.row(it))
        self._update_events_empty()
        self._info(_('Eliminati {n} eventi.', n=n))

    def _delete_all_events(self) -> None:
        if self.events.count() == 0:
            return
        if not confirm_warning(self, _('Svuota eventi'), _('Eliminare TUTTI gli eventi e le loro foto?'),
                               _('Elimina tutto'), _("L'operazione non si può annullare.")):
            return
        n = self.core.store.delete_all()
        self.events.clear()
        self._update_events_empty()
        self._info(_('Eliminati {n} eventi.', n=n))

    def _open_event(self, item: QListWidgetItem) -> None:
        """Doppio clic: il video dell'evento se c'è, altrimenti la foto."""
        from pathlib import Path  # noqa: PLC0415

        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            clip = Path(path).with_suffix(".mp4")
            # vista Foto: la foto; vista Video: il video
            open_file(clip if clip.exists() and self.events_mode == "videos" else path)

    def _review_event(self) -> None:
        """Tasto destro → Rivedi: la vista Rivedi sulla camera e sull'ora dell'evento."""
        for it in self.events.selectedItems()[:1]:
            ev = self.core.store.get(it.data(Qt.ItemDataRole.UserRole + 1) or "")
            if ev:
                self._show_review(ev["camera_id"], float(ev["ts"]) - 5)

    def _open_event_photo(self) -> None:
        for it in self.events.selectedItems()[:1]:
            if it.data(Qt.ItemDataRole.UserRole):
                open_file(it.data(Qt.ItemDataRole.UserRole))

    def _clip_ready(self, event_id: str) -> None:
        """Clip pronta: l'anteprima dell'evento prende il ▶."""
        for i in range(self.events.count()):
            it = self.events.item(i)
            if it.data(Qt.ItemDataRole.UserRole + 1) == event_id:
                ev = self.core.store.get(event_id)
                if ev:
                    it.setData(Qt.ItemDataRole.UserRole + 2, True)
                    it.setHidden(False)
                    it.setIcon(QIcon(event_thumbnail(ev)))
                    it.setToolTip(it.toolTip().split("\n")[0] + "\n" + _("Con video: doppio clic nella vista Video, oppure tasto destro → Rivedi in Mirto"))
                    self._update_events_empty()
                break

    def _camera_state(self, cam_id: str, change: dict) -> None:
        tile = self.tiles.get(cam_id)
        if not tile or cam_id in self.person_until:
            return
        if change.get("dark"):
            tile.set_badge(_('Immagine troppo scura'), "warn")
        elif change.get("dark") is False and tile.badge_text == _('Immagine troppo scura'):
            tile.set_badge("")
        elif change.get("motion") is True and not tile.badge_text:
            tile.set_badge(_("Movimento"), "info")
        elif change.get("motion") is False and tile.badge_text == _("Movimento"):
            tile.set_badge("")

    def _update_audio_row(self) -> None:
        """Riga "Suoni" in Sistema: microfoni in ascolto, livello dal vivo, ultimo suono riconosciuto."""
        from ..detection.labels import display_name  # noqa: PLC0415

        core = self.core
        mic = getattr(self, "mic", None)
        mac_on = bool(core.config.get("audio.mac_mic", False))
        mon = core.audio
        sources = mon.status() if mon else []
        self.row_audio.setVisible(mac_on or bool(sources))
        if not (mac_on or sources):
            return
        lines = []
        for s in sources:
            state = (_("in ascolto") if s["live"] else _("nessun audio")) + "  " + level_bar(s["level"])
            lines.append(f"&nbsp;&nbsp;{'🎙' if s['live'] else '⚠'} {html.escape(s['name'])} – {state}")
            if s.get("last_heard"):
                kind, score, ts = s["last_heard"]
                lines.append("&nbsp;&nbsp;&nbsp;&nbsp;" + _("ultimo suono: {name} {score:.0%} alle {hhmm}",
                             name=display_name("sound:" + kind), score=score,
                             hhmm=time.strftime("%H:%M:%S", time.localtime(ts))))
            if s.get("error"):
                lines.append("&nbsp;&nbsp;&nbsp;&nbsp;⚠ " + html.escape(s["error"]))
        aperta = self._aperta("suoni")
        pieno = "<br>".join(lines)
        detail = pieno if aperta else ""
        quanto = dict(chiave="suoni", aperto=aperta, espandibile=bool(pieno))
        tip = _("Impostazioni → AI → Microfoni")
        if mic is not None and mic.error:
            # un microfono rotto lo si dice sempre, anche a riga chiusa: è un guasto, non un dettaglio
            self.row_audio.set(T.ALARM, _("Suoni: problema con il microfono"), tip,
                               "&nbsp;&nbsp;⚠ " + html.escape(mic.error) + ("<br>" + detail if detail else ""),
                               **quanto)
        elif mac_on and (mic is None or not mic.running):
            self.row_audio.set(T.PAUSED, _("Suoni: avvio del microfono…"), tip, detail, **quanto)
        elif mon and mon.model_state == "loading":
            self.row_audio.set(T.PAUSED, _("Suoni: scarico il modello (~15 MB)…"), tip, detail, **quanto)
        elif mon and mon.model_state == "error":
            self.row_audio.set(T.ALARM, _("Suoni: modello non disponibile (nuovo tentativo tra 30 min)"), tip, detail, **quanto)
        elif any(s["live"] for s in sources):
            n = sum(1 for s in sources if s["live"])
            self.row_audio.set(T.ARMED, _("Suoni in ascolto ({n})", n=n), tip, detail, **quanto)
        else:
            self.row_audio.set(T.PAUSED, _("Suoni: in attesa di audio dal microfono"), tip, detail, **quanto)

    def _update_rec_row(self) -> None:
        """Riga "Registrazione" in Sistema."""
        rec = getattr(self.core, "recording", None)
        if rec is None:
            self.row_rec.setVisible(False)
            return
        st = rec.status()
        sources = dict(self.core.cameras.sources)
        modes = {cid: rec.mode_for(cid) for cid in sources}
        names = {cid: src.info.name for cid, src in sources.items()}
        level, title, detail = recording_summary(st, modes, names)
        aperta = self._aperta("registrazione")
        self.row_rec.set(level, title, _("Impostazioni → Registrazione"), detail if aperta else "",
                         chiave="registrazione", aperto=aperta, espandibile=bool(detail))

    # ------------------------------------------------------------------ riquadri
    def _add_tile(self, cam_id: str, name: str) -> None:
        if cam_id in self.tiles:
            return
        tile = CameraTile(cam_id, name)
        tile.double_clicked.connect(self._show_single)
        tile.photo_requested.connect(self._snapshot_camera)
        self.tiles[cam_id] = tile
        self._relayout()

    def _remove_tile(self, cam_id: str) -> None:
        tile = self.tiles.pop(cam_id, None)
        if tile:
            tile.setParent(None)
            tile.deleteLater()
        if self.single_id == cam_id:
            self._show_grid()
        self._relayout()

    def _set_status(self, cam_id: str, status: str) -> None:
        if cam_id in self.tiles:
            self.tiles[cam_id].set_status(status)
        if cam_id == self.single_id:
            self.single.set_status(status)

    def _relayout(self) -> None:
        self.grid.set_widgets(list(self.tiles.values()) + [self.add_tile])
        self._update_view_header()

    def _update_view_header(self) -> None:
        cams = self.core.cameras.all_cameras()
        found = sum(1 for c in cams if c["connected"])
        active = len(self.tiles)
        self.tab_grid.setText(_('  Griglia camere ({active})', active=active))
        self.tab_all.setText(_('  Tutte le cam ({found})', found=found))
        if not found:
            self.add_tile.set_text(_('Nessuna camera trovata'), _('Collega una camera o controlla il permesso Fotocamera'))
        elif not active:
            self.add_tile.set_text(_('Nessuna camera attiva'), _('{found} trovate: tocca qui per scegliere quali usare', found=found))
        else:
            self.add_tile.set_text(_('Tutte le cam'), _('{active} in uso su {found} trovate · tocca per scegliere', active=active, found=found))
        page = self.stack.currentIndex()
        if hasattr(self, "timeline_panel"):  # la striscia degli eventi serve con le camere dal vivo, non altrove
            self.timeline_panel.setVisible(page in (0, 1))
        self.back_button.setVisible(page == 1)
        self.single_photo_button.setVisible(page == 1)
        self.size_slider.setVisible(page == 0)
        if page == 0:
            self.view_hint.setText(_('Doppio clic su una camera per ingrandirla'))
        elif page == 1:
            self.view_hint.setText(self.single.name)
        elif page == 3:
            self.view_hint.setText(_('Barra spaziatrice: riproduci o metti in pausa'))
        elif page == 4:
            self.view_hint.setText(_('Doppio clic su un nodo per aprirlo in Impostazioni'))
        else:
            self.view_hint.setText(_('{sum} in uso', sum=sum(1 for c in cams if c['enabled'] and c['connected'])))

    def _show_single(self, cam_id: str) -> None:
        tile = self.tiles.get(cam_id)
        if not tile:
            return
        self._leave_review()
        self.cards_timer.stop()
        self.core.cameras.network_watch(False)
        self.single_id = cam_id
        self.single.cam_id = cam_id
        self.single.set_name(tile.name)
        src = self.core.cameras.sources.get(cam_id)
        self.single.set_status(src.status if src else "connecting")
        self.single.set_badge(tile.badge_text, tile.badge_level or "alert", blink=False)
        self.single.last_seq = -1
        self.stack.setCurrentIndex(1)
        self.tab_grid.setChecked(True)
        self._update_view_header()

    def _leave_review(self) -> None:
        if hasattr(self, "review"):
            self.review.deactivate()

    def _show_review(self, cam_id: str | None = None, at: float | None = None) -> None:
        """Pagina «Rivedi» (eventualmente su una camera e un istante: dal tasto destro su un evento)."""
        self.single_id = None
        self.cards_timer.stop()
        self.core.cameras.network_watch(False)
        self.stack.setCurrentIndex(3)
        self.tab_review.setChecked(True)
        self.review.activate(cam_id, at)
        self.review.setFocus()
        self._update_view_header()

    def _show_nodi(self) -> None:
        """La vista di flotta: tutti i nodi, una riga per nodo."""
        self._leave_review()
        self.single_id = None
        self.cards_timer.stop()
        self.core.cameras.network_watch(False)
        self.stack.setCurrentIndex(4)
        self.tab_nodi.setChecked(True)
        self._update_status()      # la tabella si riempie subito, non al prossimo giro
        self._update_view_header()

    def _aggiorna_nodo_dalla_flotta(self, nid: str) -> None:
        """Dal pulsante nella riga: si apre il nodo in Impostazioni, già sulla domanda.

        L'aggiornamento vero resta un posto solo — la finestra «Stato del nodo», che sa mostrare
        l'attesa e dire com'è andata. Qui si decide **a chi**, non si rifà **come**.
        """
        self._apri_impostazioni_sul_nodo(nid, aggiorna=True)

    def _show_grid(self) -> None:
        self._leave_review()
        self.single_id = None
        self.cards_timer.stop()
        self.core.cameras.network_watch(False)
        self.stack.setCurrentIndex(0)
        self.tab_grid.setChecked(True)
        self._relayout()

    def _show_all_cams(self) -> None:
        self._leave_review()
        self.single_id = None
        self.core.cameras.network_watch(True)  # controlli ogni 2 s finché si guarda questa pagina
        self.stack.setCurrentIndex(2)
        self.tab_all.setChecked(True)
        self._refresh_cards()
        self.cards_timer.start(1000)
        self._update_view_header()

    # ------------------------------------------------------------------ Tutte le cam
    def _refresh_cards(self) -> None:
        # il riquadro "Trovate in rete" si aggiorna per primo e da solo: un errore nelle
        # schede delle camere non deve lasciarlo fermo su un elenco vecchio
        self._refresh_found()
        cams = self.core.cameras.all_cameras()
        for c in cams:  # camere trasmesse da un nodo: lo si dice sulla scheda
            node = c.get("node_name") or getattr(self, "cam_nodes", {}).get(c["id"])
            if node:
                c["detail"] = f"nodo {node}" + (f" · {c['detail']}" if c.get("detail") else "")
        layout_key = [(c["id"], c.get("node_name", "")) for c in cams]
        if layout_key != getattr(self, "_cards_key", None):
            self._cards_key = layout_key
            for card in self.cards.values():
                card.setParent(None)
                card.deleteLater()
            self.cards.clear()
            for h in getattr(self, "_card_headers", []):
                h.setParent(None)
                h.deleteLater()
            self._card_headers = []
            pos, group = 0, None
            has_nodes = any(c.get("node") for c in cams)
            for cam in cams:
                g = cam.get("node_name", "") if cam["connected"] else "__off__"
                if g != group and (g or has_nodes):
                    # intestazione di gruppo: "Camere" / "Nodo Ingresso Tricks" / "Non collegate"
                    text = (_("Camere") if g == "" else _('Non collegate adesso') if g == "__off__"
                            else _('Nodo {g}', g=g))
                    head = label(text, weight=700)
                    head.setStyleSheet(f"color:{T.MUTED}; font-size:{T.SIZE_SMALL + 1}px; margin-top:6px;")
                    self.cards_lay.insertWidget(pos, head)
                    self._card_headers.append(head)
                    pos += 1
                group = g
                card = CameraCard(cam)
                card.toggled.connect(self._toggle_camera)
                card.rename_requested.connect(self._rename_camera)
                card.wrong_camera.connect(self._wrong_camera)
                card.preview_requested.connect(self._preview_camera)
                card.remove_requested.connect(self._remove_network_camera)
                card.photo_requested.connect(self._snapshot_camera)
                self.cards_lay.insertWidget(pos, card)
                pos += 1
                self.cards[cam["id"]] = card
        else:
            for cam in cams:
                self.cards[cam["id"]].update_cam(cam)
        self.cards_empty.setVisible(not cams)
        sources = self.core.cameras.sources
        for cam_id, card in self.cards.items():
            src = sources.get(cam_id)
            if src and card.cam["enabled"]:
                frame, _ts, seq = src.latest()
                if frame is not None and seq != card.last_seq:
                    card.last_seq = seq
                    card.set_frame(frame)
        self._update_view_header()

    def _toggle_camera(self, cam_id: str, on: bool) -> None:
        cams = self.core.cameras.all_cameras()
        name = next((c["name"] for c in cams if c["id"] == cam_id), cam_id)
        if not on:
            others = [c for c in cams if c["enabled"] and c["connected"] and c["id"] != cam_id]
            if not others and not confirm_warning(
                    self, _('Spegni camera'),
                    _("«{name}» è l'unica camera in uso: spegnendola Mirto non sorveglierà più nulla.", name=name),
                    _('Spegni comunque')):
                self._refresh_cards()
                return
        self.core.cameras.set_enabled(cam_id, on)
        self._info(f"{name}: {_('attiva, compare nella griglia') if on else _('spenta, Mirto non la usa più')}.")
        self._refresh_cards()

    def _wrong_camera(self, cam_id: str) -> None:
        """«Questa immagine è di un'altra camera»: lo dice chi la sta guardando, e si scambiano.

        Nome e ID vengono dal sistema operativo; la camera però la apre OpenCV **per posizione**, e
        l'ordine in cui OpenCV elenca i dispositivi non è quello del sistema — sul Mac, con un iPhone
        vicino, l'elenco cambia e i due nomi finiscono sulle immagini sbagliate. Quell'ordine non si
        può leggere da fuori e indovinarlo vuol dire vederlo cambiare al prossimo aggiornamento.

        Perciò non si chiede «qual è l'indice giusto», che è una domanda nostra: si chiede **di chi è
        l'immagine**, che è l'unica cosa che chi guarda sa con certezza. Lo scambio lo facciamo noi.
        """
        cams = [c for c in self.core.cameras.all_cameras() if c.get("scambiabile")]
        mia = next((c for c in cams if c["id"] == cam_id), None)
        altre = [c for c in cams if c["id"] != cam_id]
        if mia is None or not altre:
            return
        nomi = [c["name"] for c in altre]
        scelta, ok = QInputDialog.getItem(
            self, _('Di chi è questa immagine?'),
            _("Qui sopra c'è scritto «{name}». Se l'immagine che vedi è di un'altra camera,\n"
              "scegli quale: i due nomi si scambiano, e resta così.", name=mia["name"]),
            nomi, 0, False)
        if not ok or scelta not in nomi:
            return
        altra = altre[nomi.index(scelta)]
        if not self.core.cameras.scambia_indici(cam_id, altra["id"]):
            self._info(_('Non sono riuscito a scambiarle: guarda Stato e diagnosi.'))
            return
        # Le due immagini si svuotano **subito**, perché lo scambio si veda succedere: le camere si
        # riaprono in un attimo e la figura nuova prende il posto di questa. Senza, le due schede
        # restavano con la foto di prima finché non arrivava un fotogramma, e Marco: *«non succede
        # nulla che faccia capire che è avvenuto qualcosa»*. Una cosa che avviene si deve vedere
        # avvenire, se no chi guarda ripreme il pulsante e la rimette come stava.
        for cid in (cam_id, altra["id"]):
            card = self.cards.get(cid)
            if card is not None:
                card.last_seq = -1
                card.set_message(_('riapro…'))
        self._info(_('Fatto: «{a}» e «{b}» si sono scambiate. Guarda le immagini.',
                     a=mia["name"], b=altra["name"]))
        self._refresh_cards()

    def _rename_camera(self, cam_id: str) -> None:
        cam = next((c for c in self.core.cameras.all_cameras() if c["id"] == cam_id), None)
        if not cam:
            return
        name, ok = QInputDialog.getText(self, _('Rinomina camera'),
                                        _("Nome da usare nell'app e negli avvisi\n(vuoto = «{original}»):", original=cam['original']),
                                        QLineEdit.EchoMode.Normal, cam["name"])
        if not ok:
            return
        self.core.cameras.rename(cam_id, name)
        new = name.strip() or cam["original"]
        if cam_id in self.tiles:
            self.tiles[cam_id].set_name(new)
        if self.single.cam_id == cam_id:
            self.single.set_name(new)
        self._refresh_cards()

    def _preview_camera(self, cam_id: str) -> None:
        """Accende per qualche secondo una camera spenta, per riconoscerla."""
        from ..cameras.source import open_capture  # noqa: PLC0415

        info = self.core.cameras.watcher.known.get(cam_id)
        card = self.cards.get(cam_id)
        if not info or not card:
            return
        card.set_message(_('Accensione…'))

        def grab():
            cap = open_capture(info)
            try:
                if not cap.isOpened():
                    return None
                best, best_mean = None, -1.0
                deadline = time.time() + 4
                while time.time() < deadline:
                    ok, frame = cap.read()
                    if ok and frame is not None:
                        m = float(frame.mean())
                        if m > best_mean:
                            best, best_mean = frame, m
                        if m > 8:
                            break
                    else:
                        time.sleep(0.05)
                return best
            finally:
                cap.release()

        def show(frame):
            c = self.cards.get(cam_id)
            if c is None:
                return
            if frame is None or isinstance(frame, str):
                c.set_message(_('Nessuna immagine'))
            else:
                c.set_frame(frame)
        self._bg(grab, show)

    def _found_row(self, i: int) -> dict:
        """Riga ``i`` del riquadro "Trovate in rete", creata una volta sola e poi riutilizzata."""
        while len(self.found_pool) <= i:
            k = len(self.found_pool)
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(10)
            name_lb = label("", weight=600)
            detail_lb = label("", obj="faint")
            h.addWidget(name_lb)
            h.addWidget(detail_lb, 1)
            add = action_button(_("Aggiungi"), "plus", obj="small", color=T.ARMED,
                                tooltip=_('Aggiunge questa camera a Mirto; se cambia indirizzo la ritrova da sola'))
            add.setIconSize(QSize(16, 16))
            add.clicked.connect(lambda _=False, k=k: self._found_action(k, add=True))
            h.addWidget(add)
            hide = action_button(_("Nascondi"), "x", obj="small", color=T.MUTED,
                                 tooltip=_('Toglie questa camera dal riquadro (si può rimettere in fondo alla pagina)'))
            hide.setIconSize(QSize(16, 16))
            hide.clicked.connect(lambda _=False, k=k: self._found_action(k, add=False))
            h.addWidget(hide)
            w.setVisible(False)
            self.found_rows.addWidget(w)
            self.found_pool.append({"w": w, "name": name_lb, "detail": detail_lb})
        return self.found_pool[i]

    def _found_action(self, k: int, add: bool) -> None:
        if k >= len(self.found_cams):
            return
        cam = self.found_cams[k]
        if add:
            self._add_found_camera(cam)
        else:
            self._hide_found_camera(cam)

    def _refresh_found(self) -> None:
        """Aggiorna la sezione "Trovate in rete" (le righe cambiano solo quando cambia l'elenco)."""
        try:
            found = self.core.cameras.discovered_network_cameras()
        except Exception:
            log.exception("Elenco camere trovate in rete non disponibile")
            found = []
        self._update_net_status()
        n_hidden = len(self.core.config.get("cameras.hidden_found") or [])
        self.unhide_btn.setVisible(n_hidden > 0)
        self.unhide_btn.setText(_('  Mostra le camere nascoste ({n_hidden})', n_hidden=n_hidden))
        keys = [f"{c['service']}|{c['url']}|{','.join(c.get('also', []))}|{c.get('alive')}" for c in found]
        if keys == self.found_keys:
            return
        self.found_keys = keys
        self.found_cams = found
        for i, cam in enumerate(found):
            row = self._found_row(i)
            detail = cam["url"]
            if cam.get("also"):
                detail += _(' · risponde anche su ') + ", ".join(cam["also"])
            elif not cam.get("alive", True):
                detail += _(' · non risponde')
            row["name"].setText(cam["name"])
            row["detail"].setText(detail)
            row["w"].setVisible(True)
        for row in self.found_pool[len(found):]:
            row["w"].setVisible(False)
        if self.core.cameras.network_watch_state() is None:
            self.found_empty.setText(_('Ricerca delle camere in rete non disponibile.'))
        else:
            self.found_empty.setText(_('Nessuna camera nuova in rete: quelle trovate sono già in Mirto.'))
        self.found_empty.setVisible(not found)
        # se l'altezza del riquadro cambia, ridisegna tutta la pagina (anche le zone native)
        QTimer.singleShot(0, self._repaint_all_cams)

    def _repaint_all_cams(self) -> None:
        for w in (self.cards_scroll.viewport(), self.found_box, self.found_box.parentWidget()):
            if w is not None:
                w.update()

    def _update_net_status(self) -> None:
        state = self.core.cameras.network_watch_state()
        self.net_refresh_btn.setEnabled(state is not None)
        if state is None:
            self.net_status.setText(_('Ricerca in rete spenta'))
            self.net_status.setStyleSheet(f"color:{T.FAINT};")
            return
        if state["refreshing"] or not state["last"]:
            self.net_status.setText(_('● Controllo della rete…'))
            self.net_status.setStyleSheet(f"color:{T.INFO};")
        else:
            ago = max(0, int(time.time() - state["last"]))
            when = time.strftime("%H:%M:%S", time.localtime(state["last"]))
            self.net_status.setText(_('● Rete controllata alle {when}', when=when)
                                    + (_(" ({ago} s fa)", ago=ago) if ago >= 3 else "")
                                    + _(" · ogni {interval:g} s", interval=state['interval']))
            self.net_status.setStyleSheet(f"color:{T.ARMED if ago <= state['interval'] + 3 else T.FAINT};")

    def _net_refresh_now(self) -> None:
        self.core.cameras.network_refresh_now()
        self.net_status.setText(_('● Controllo della rete…'))
        self.net_status.setStyleSheet(f"color:{T.INFO};")

    def _hide_found_camera(self, cam: dict) -> None:
        self.core.cameras.hide_discovered(cam["service"])
        self._info(_('«{name}» nascosta dalle camere trovate in rete.', name=cam['name']))
        self.found_keys = None
        self._refresh_found()

    def _unhide_found(self) -> None:
        n = self.core.cameras.unhide_discovered()
        self._info(_('Una camera di nuovo visibile fra quelle trovate in rete.') if n == 1
                   else _('{n} camere di nuovo visibili tra quelle trovate in rete.', n=n))
        self.found_keys = None
        self._refresh_found()

    def _add_found_camera(self, cam: dict) -> None:
        name, ok = QInputDialog.getText(self, _('Aggiungi camera trovata in rete'),
                                        _("{name}\n{url}\n\nNome da usare nell'app e negli avvisi:", name=cam['name'], url=cam['url']),
                                        QLineEdit.EchoMode.Normal, cam["suggested"])
        if not ok:
            return
        cam_id = self.core.cameras.add_discovered(cam["service"], name)
        if cam_id is None:
            self._popup(_('La camera non risponde più in rete: controlla che sia accesa e riprova.'))
            return
        self._info(_('Camera di rete «{v}» aggiunta: la trovi nella griglia.', v=name.strip() or cam['suggested']))
        self.found_keys = None
        self._refresh_cards()

    def _add_network_camera(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle(_('Aggiungi camera di rete'))
        form = QFormLayout(dlg)
        name = QLineEdit()
        name.setPlaceholderText(_("es. Giardino"))
        url = QLineEdit()
        url.setPlaceholderText("rtsp://utente:password@192.168.1.50:554/stream1")
        url.setMinimumWidth(420)
        form.addRow(_("Nome"), name)
        form.addRow(_("Indirizzo"), url)
        form.addRow(label(_("L'indirizzo lo trovi nel manuale o nell'app della camera (RTSP o HTTP)."), obj="faint"))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(_("Aggiungi"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if not dlg.exec():
            return
        u = url.text().strip()
        if not re.match(r"^(rtsp|rtsps|http|https)://", u, re.I):
            self._popup(_("L'indirizzo deve iniziare con rtsp:// oppure http://"))
            return
        n = name.text().strip() or _('Camera di rete')
        nets = self.core.config.get("cameras.network") or []
        base = re.sub(r"[^a-z0-9]+", "-", n.lower()).strip("-") or "camera"
        cid, k = base, 2
        while any(x.get("id") == cid for x in nets):
            cid, k = f"{base}-{k}", k + 1
        from ..credentials import rtsp_name  # noqa: PLC0415
        from ..netutil import split_credentials  # noqa: PLC0415

        u, creds = split_credentials(u)  # utente e password nel portachiavi, non in configurazione
        if creds:
            self.core.secrets.set(rtsp_name(cid), creds)
        nets.append({"id": cid, "name": n, "url": u})
        self.core.config.set("cameras.network", nets)
        self.core.config.save()
        self.core.cameras.reload()
        self._info(_('Camera di rete «{n}» aggiunta.', n=n))
        self._refresh_cards()

    def _remove_network_camera(self, cam_id: str) -> None:
        nets = self.core.config.get("cameras.network") or []
        net = next((x for x in nets if f"net:{x.get('id')}" == cam_id), None)
        if not net:
            return
        if QMessageBox.question(self, _('Rimuovi camera'), _('Togliere «{v}» da Mirto?', v=net.get('name') or net['id'])) \
                != QMessageBox.StandardButton.Yes:
            return
        self.core.config.set("cameras.network", [x for x in nets if x is not net and x.get("id") != net["id"]])
        from ..credentials import rtsp_name  # noqa: PLC0415

        self.core.secrets.delete(rtsp_name(str(net["id"])))  # via anche la password dal portachiavi
        overrides = self.core.config.get("cameras.overrides") or {}
        overrides.pop(cam_id, None)
        self.core.config.set("cameras.overrides", overrides)
        self.core.config.save()
        self.core.cameras.reload()
        self._refresh_cards()

    def _refresh(self) -> None:
        sources = self.core.cameras.sources
        page = self.stack.currentIndex()
        if page in (2, 3):  # Tutte le cam e Rivedi: niente immagini dal vivo da aggiornare
            return
        targets = [(self.single_id, self.single)] if page == 1 and self.single_id else list(self.tiles.items())
        for cam_id, tile in targets:
            src = sources.get(cam_id)
            if not src:
                continue
            frame, ts, seq = src.latest()
            if frame is not None and seq != tile.last_seq:
                tile.last_seq = seq
                tile.show_frame(frame, ts)

    # ------------------------------------------------------------------ icona nella barra in alto (tray)
    def _legal_notice(self) -> None:
        from .legal_ui import LegalNoticeDialog  # noqa: PLC0415

        LegalNoticeDialog(self, first_time=False).exec()

    def _new_version(self, version: str) -> None:
        from .telemetry_ui import open_releases  # noqa: PLC0415

        self.statusBar().showMessage(_("È disponibile Mirto {version}", version=version), 60000)
        box = QMessageBox(QMessageBox.Icon.Information, _("Nuova versione"),
                          _("È disponibile Mirto {version} (tu hai la {current}).", version=version,
                            current=__version__), parent=self)
        get = box.addButton(_("Vai alla pagina di download"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(_("Più tardi"), QMessageBox.ButtonRole.RejectRole)
        box.setWindowModality(Qt.WindowModality.NonModal)
        box.buttonClicked.connect(lambda btn: open_releases() if btn is get else None)
        box.show()

    def setup_tray(self) -> None:
        """Icona di Mirto nella barra dei menu (macOS) o nell'area di notifica (Windows).

        Chiudendo la finestra Mirto propone di restare in background: la sorveglianza continua e un clic
        sull'icona riapre l'interfaccia.
        """
        from PySide6.QtWidgets import QSystemTrayIcon  # noqa: PLC0415

        self.tray = None
        self._quitting = False
        self._tray_key = None
        self._tray_told = False
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(self)
        self.tray.activated.connect(self._tray_activated)
        if sys.platform != "darwin":  # su macOS il clic apre direttamente la finestra
            menu = QMenu(self)
            menu.addAction(_('Apri Mirto'), self.show_from_tray)
            self._tray_arm = menu.addAction(_('Attiva protezione'), self._toggle_armed)
            menu.addSeparator()
            menu.addAction(_('Esci da Mirto'), self.quit_from_tray)
            self.tray.setContextMenu(menu)
        self._update_tray()
        self.tray.show()
        QApplication.instance().setQuitOnLastWindowClosed(False)
        # clic sull'icona nel Dock con la finestra nascosta: la riapre
        QApplication.instance().applicationStateChanged.connect(self._app_state_changed)

    @staticmethod
    @staticmethod
    def _sagoma_arbusto() -> QPainterPath:
        """La sagoma dell'arbusto, in un quadrato di 22 unita': lobi tondi sopra, base piatta.

        I lobi si uniscono con la regola di riempimento «winding» e poi `simplified()` ne fa un
        contorno solo: con la regola normale le sovrapposizioni diventerebbero buchi, e disegnando
        i cerchi uno per uno il contorno mostrerebbe anche gli archi interni.
        """
        s = QPainterPath()
        s.setFillRule(Qt.FillRule.WindingFill)
        for cx, cy, r in ((6.0, 12.2, 3.4), (9.4, 9.8, 4.3), (13.4, 10.2, 4.0), (16.4, 12.4, 3.2)):
            s.addEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
        s.addRect(QRectF(2.6, 12.2, 17.0, 5.2))
        return s.simplified()

    @staticmethod
    def tray_pixmap(color: str, size: int = 22, pieno: bool | None = None) -> QPixmap:
        """L'arbusto col colore dello stato: di contorno sulla barra del Mac, pieno altrove.

        Nella barra dei menu di macOS le icone sono glifi sottili e monocromi, e un contorno con il
        pallino colorato dentro è esattamente ciò che ci si aspetta. Nell'area di notifica di
        Windows invece le icone intorno sono quadrati pieni e saturi: a 16 pixel un contorno grigio
        da 2 px **non si vede** — lo ha fatto notare Marco durante W-01 (19/10), guardando la sua
        barra. Lì si disegna pieno del colore dello stato, con un bordo più scuro perché resti
        leggibile anche su una barra chiara.

        Fino al 7/11 era una casetta, perché il programma si chiamava MyHouse; per qualche ora è
        stata una bacca con la foglia. Ora è la stessa sagoma dell'icona dell'applicazione, in
        piccolo: l'arbusto con l'obiettivo al centro. I mirtilli no — a 16 px sono tre pixel scuri
        che sporcano e non dicono niente.
        """
        if pieno is None:
            pieno = sys.platform != "darwin"
        scale = 2
        pm = QPixmap(size * scale, size * scale)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.scale(scale, scale)
        sagoma = MainWindow._sagoma_arbusto()
        lente = QRectF(11 - 3.0, 12.6 - 3.0, 6.0, 6.0)
        if pieno:
            p.setPen(QPen(QColor(color).darker(185), 1.0))
            p.setBrush(QColor(color))
            p.drawPath(sagoma)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(color).darker(300))
            p.drawEllipse(lente)
            p.end()
            pm.setDevicePixelRatio(scale)
            return pm
        pen = QPen(QColor("#8A97A8"), 2.0)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(sagoma)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        p.drawEllipse(lente.adjusted(0.4, 0.4, -0.4, -0.4))
        p.end()
        pm.setDevicePixelRatio(scale)
        return pm

    def _update_tray(self) -> None:
        if getattr(self, "tray", None) is None:
            return
        st = self.core.alert_state()
        alarm = bool(getattr(self, "state_icon", None) and self.state_icon.alarming())
        mode = "alarm" if alarm else st["mode"]
        key = (mode, st["armed"])
        if key == self._tray_key:
            return
        self._tray_key = key
        self.tray.setIcon(QIcon(self.tray_pixmap(T.STATE_COLORS[mode])))
        what = {"active": "protetta", "paused": _('avvisi in pausa'), "disarmed": _('non protetta'),
                "alarm": _('persona rilevata!')}[mode]
        self.tray.setToolTip(f"Mirto – {what}" + ("" if self.isVisible() else _(' · clic per aprire')))
        if hasattr(self, "_tray_arm"):
            self._tray_arm.setText(_('Disattiva protezione') if st["armed"] else _('Attiva protezione'))

    def _tray_activated(self, reason) -> None:
        from PySide6.QtWidgets import QSystemTrayIcon  # noqa: PLC0415

        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_from_tray()

    def _app_state_changed(self, state) -> None:
        if state == Qt.ApplicationState.ApplicationActive and not self.isVisible() and not self._quitting:
            self.show_from_tray()

    def show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self._tray_key = None
        self._update_tray()

    def quit_from_tray(self) -> None:
        self.show_from_tray()
        if confirm_warning(self, _('Chiudere Mirto?'),
                           _('Chiudendo Mirto la sorveglianza si ferma: niente più avvisi né azioni.'),
                           _('Esci da Mirto'), _('Le camere restano spente finché non lo riapri.')):
            self._quitting = True
            self.close()

    def _ask_close(self) -> str:
        """"background", "quit" o "cancel". La scelta "resta in background" si può ricordare."""
        from PySide6.QtCore import QSettings  # noqa: PLC0415
        from PySide6.QtWidgets import QCheckBox  # noqa: PLC0415

        settings = QSettings("Mirto", "Mirto")
        if settings.value("close/background", False, type=bool):
            return "background"
        box = QMessageBox(self)
        box.setWindowTitle(_('Chiudere Mirto?'))
        box.setIcon(QMessageBox.Icon.Question)
        where = _("nella barra in alto, vicino all'orologio") if sys.platform == "darwin" else \
            _("nell'area di notifica, vicino all'orologio")
        box.setText(_('Vuoi lasciare Mirto acceso in background?'))
        box.setInformativeText(_('La sorveglianza continua e trovi Mirto come icona {where}: un clic la riapre.\n\nUscendo del tutto la sorveglianza si ferma: niente più avvisi né azioni.', where=where))
        stay = box.addButton(_('Resta in background'), QMessageBox.ButtonRole.AcceptRole)
        stay.setStyleSheet(f"QPushButton {{ background:{T.ARMED}; color:#FFFFFF; }}")
        quit_ = box.addButton(_('Esci da Mirto'), QMessageBox.ButtonRole.DestructiveRole)
        quit_.setStyleSheet(f"QPushButton {{ color:{T.ALARM}; }}")
        box.addButton(_("Annulla"), QMessageBox.ButtonRole.RejectRole)
        remember = QCheckBox(_('Non chiederlo più: chiudendo la finestra resta sempre in background\n(per uscire davvero: menu Mirto → Esci da Mirto, oppure ⌘Q)'))
        box.setCheckBox(remember)
        box.setDefaultButton(stay)
        box.exec()
        if box.clickedButton() is stay:
            if remember.isChecked():
                settings.setValue("close/background", True)
            return "background"
        return "quit" if box.clickedButton() is quit_ else "cancel"

    def closeEvent(self, event):  # noqa: N802
        if not self._quitting:
            if getattr(self, "tray", None) is not None:
                choice = self._ask_close()
                if choice != "quit":
                    event.ignore()
                    if choice == "background":
                        self.hide()
                        self._tray_key = None
                        self._update_tray()
                        if not self._tray_told:
                            self._tray_told = True
                            self.tray.showMessage(_('Mirto è ancora acceso'),
                                                  _("La sorveglianza continua. Clicca l'icona per riaprirlo."),
                                                  QIcon(self.tray_pixmap(T.ARMED)), 5000)
                    return
            elif not confirm_warning(self, _('Chiudere Mirto?'),
                                     _('Chiudendo Mirto la sorveglianza si ferma: niente più avvisi né azioni.'),
                                     _('Esci da Mirto'),
                                     _('Le camere restano spente finché non lo riapri.')):
                event.ignore()
                return
        self._quitting = True
        if getattr(self, "tray", None) is not None:
            self.tray.hide()
        self.timer.stop()
        self.cards_timer.stop()
        self.scan_timer.stop()
        self.status_timer.stop()
        self.core.stop()
        super().closeEvent(event)
        QApplication.instance().quit()


def run_gui() -> int:
    from ..actions.engine import enable_actions  # noqa: PLC0415

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    # icona nel Dock (macOS) e nella barra delle applicazioni (Windows), anche avviando con python -m mirto
    icon_file = T.ICONS / "app" / ("app.png" if sys.platform == "darwin" else "app_256.png")
    if icon_file.exists():
        app.setWindowIcon(QIcon(str(icon_file)))
    app.setStyle("Fusion")          # disegno uniforme, rispetta la palette scura
    app.setPalette(T.palette())
    app.setStyleSheet(T.STYLE)
    core = MirtoCore()
    from .legal_ui import ensure_accepted  # noqa: PLC0415

    if not ensure_accepted(core.config):  # condizioni d'uso: prima di aprire camere e microfoni
        return 0
    enable_actions(core)
    win = MainWindow(core)
    win.setup_tray()
    app.aboutToQuit.connect(core.stop)  # anche uscendo dal Dock con la finestra nascosta
    win.show()
    from ..cameras.permissions import ensure_camera_permission  # noqa: PLC0415

    if getattr(core.config, "load_warning", ""):
        # La configurazione era rovinata o illeggibile e si è ripartiti dai predefiniti: va detto,
        # altrimenti l'utente vede le impostazioni azzerate e pensa di aver perso tutto (Q-06).
        QMessageBox.warning(win, _('Impostazioni'),
                            _('Mirto è partito con le impostazioni predefinite perché non ha potuto '
                              'leggere le tue.\n\nDettaglio: {dettaglio}\n\nSe hai modificato il file a '
                              'mano, la copia di prima è accanto a quello nuovo con l\'aggiunta «.rotto».',
                              dettaglio=core.config.load_warning))
    allowed, msg = ensure_camera_permission()  # sul thread principale, prima di aprire le camere
    if not allowed:
        QMessageBox.warning(win, _('Permesso Camera'), msg)
    core.start(threaded_discovery=default_threaded_discovery())
    from .mic_qt import MacMicCapture  # noqa: PLC0415

    win.mic = MacMicCapture(core, win)  # si accende solo se attivato in Impostazioni → Suoni
    from .telemetry_ui import ask_consent  # noqa: PLC0415

    QTimer.singleShot(2500, lambda: ask_consent(core, win))  # una volta sola, a finestra già aperta
    return app.exec()
