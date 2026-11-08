# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Componenti dell'interfaccia nello stile "centrale notturna"."""

from __future__ import annotations

import math
import time

import cv2
import numpy as np

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QAbstractButton,
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import theme as T
from ..anteprime import misura_anteprima
from ..i18n import N_, _
from .sistema import colonne_griglia
from .testi import immagine_ferma


class _Spunta(QFrame):
    """Un riquadro che si accende e si spegne: emoji sopra, nome sotto, che va a capo.

    Se gli si danno delle ``scelte``, in fondo compare «AI» e una tendina — ma solo quando il
    riquadro e' acceso. Marco, 24/10: *«se selezioni piu' box si creano N righe di sicurezza; conviene
    aggiungere l'informazione sulla sicurezza direttamente dentro il box, con la combo di valori li'
    dentro»*. Ha ragione: la soglia e' una proprieta' di quella cosa li', non una riga che le sta
    sotto — e dieci cose spuntate facevano dieci righe sotto la griglia.
    """

    cliccata = Signal()
    valore_cambiato = Signal()

    def __init__(self, testo: str, emoji: str, acceso: bool, bloccato: bool = False,
                 scelte=None, valore: float = 0.0, parent=None):
        super().__init__(parent)
        self.setObjectName("spunta")
        self.acceso, self.bloccato = bool(acceso), bool(bloccato)
        self.setCursor(Qt.CursorShape.ArrowCursor if bloccato else Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 3, 4, 3)
        lay.setSpacing(0)
        faccia = QLabel(emoji)
        faccia.setAlignment(Qt.AlignmentFlag.AlignCenter)
        faccia.setStyleSheet("font-size:15px; background:transparent;")
        nome = QLabel(testo)
        nome.setWordWrap(True)
        nome.setToolTip(testo)      # se il nome e' lungo e va stretto, si legge passandoci sopra
        # In mezzo, non in alto: il nome sta su una riga o su tre, e con «in alto» lo spazio che
        # avanza finiva tutto sotto il testo — «lo spazio tra il testo e la parte sotto del box e'
        # enorme» (Marco, 24/10). Centrato, quello che avanza si divide in due e non si nota.
        nome.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        nome.setStyleSheet(f"font-size:{T.SIZE_SMALL}px; background:transparent;")
        lay.addWidget(faccia)
        lay.addWidget(nome, 1)
        self.soglia = None
        if scelte:
            from PySide6.QtWidgets import QComboBox  # noqa: PLC0415

            riga = QWidget()
            riga.setStyleSheet("background:transparent;")
            dentro = QHBoxLayout(riga)
            dentro.setContentsMargins(0, 0, 0, 0)
            dentro.setSpacing(4)
            sigla = QLabel("AI")
            sigla.setStyleSheet(f"font-size:10px; font-weight:700; color:{T.MUTED}; background:transparent;")
            self.soglia = QComboBox()
            self.soglia.setObjectName("minuta")
            for etichetta, quanto in scelte:
                self.soglia.addItem(etichetta, quanto)
            self.soglia.setCurrentIndex(max(0, self.soglia.findData(round(float(valore or 0), 2))))
            self.soglia.currentIndexChanged.connect(lambda _i: self.valore_cambiato.emit())
            dentro.addWidget(sigla)
            dentro.addWidget(self.soglia, 1)
            riga.setVisible(self.acceso)
            self._riga_soglia = riga
            lay.addWidget(riga)
        self._dipingi()

    def valore(self) -> float:
        """La soglia scelta dentro questo riquadro. Zero = «standard», e zero non si salva."""
        return float(self.soglia.currentData() or 0) if self.soglia is not None else 0.0

    def _dipingi(self) -> None:
        self.setProperty("acceso", self.acceso)
        self.setProperty("bloccato", self.bloccato)
        self.style().unpolish(self)
        self.style().polish(self)

    def cambia(self) -> None:
        if self.bloccato:
            return
        self.acceso = not self.acceso
        if self.soglia is not None:
            # Spento, il riquadro non chiede niente: una tendina su una cosa che non riconosci e'
            # una domanda senza oggetto.
            self._riga_soglia.setVisible(self.acceso)
        self._dipingi()
        self.cliccata.emit()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.cambia()
        super().mousePressEvent(event)


class GrigliaDiSpunte(QWidget):
    """Scelte multiple come riquadri accendibili, non come un elenco con le spunte.

    Chiesto il 22/10: «i due elenchi vorrei non comparissero come elenchi ma come box cliccabili».
    Un elenco con le caselle si legge una riga per volta e costringe a mirare una casella di 13
    pixel; dei riquadri uguali si guardano tutti insieme e si premono dove capita.

    Una voce può essere **bloccata**: resta accesa e non si spegne. Serve alle azioni antintrusione,
    dove «persona» non è una scelta — è la ragione per cui quell'azione esiste.
    """

    cambiata = Signal()
    LARGO, ALTO = 140, 80
    SPAZIO = 5                 # fra un riquadro e l'altro: otto erano un corridoio (24/10)
    STILE = f"""
    QFrame#spunta {{ background: rgba(255,255,255,0.035); border: 1px solid {T.LINE};
                     border-radius: 10px; }}
    QFrame#spunta:hover {{ border: 1px solid {T.INFO}; }}
    QFrame#spunta[acceso="true"] {{ background: rgba(91,155,240,0.20); border: 2px solid {T.INFO}; }}
    QFrame#spunta[bloccato="true"] {{ background: rgba(255,255,255,0.06); border: 1px dashed {T.LINE}; }}
    """

    def __init__(self, parent=None, massimo: int = 4, largo: int = 0, alto: int = 0):
        super().__init__(parent)
        self.setStyleSheet(self.STILE)
        # Misura e colonne si possono cambiare: venti riquadri da 140x80 su quattro colonne fanno
        # cinque righe, e la finestra diventa un rotolo — Marco il 24/10, «la finestra e' troppo alta
        # e i box troppo grossi». Piu' piccoli e piu' per riga si legge tutto in un colpo d'occhio.
        if largo:
            self.LARGO = int(largo)
        if alto:
            self.ALTO = int(alto)
        self._voci: list[tuple] = []
        self._massimo = massimo
        self._colonne = 0
        self._spunte: dict = {}
        self._lay = QGridLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(self.SPAZIO)

    def valori(self) -> dict:
        """Le soglie scelte dentro i riquadri **accesi**: lo zero vuol dire «standard»."""
        fuori = {}
        for chiave, s in self._spunte.items():
            if s.acceso and s.valore() > 0:
                fuori[chiave] = s.valore()
        return fuori

    def set_voci(self, voci) -> None:
        """``[(chiave, testo, emoji, acceso, bloccato)]``, nell'ordine in cui vanno mostrate.

        Una voce puo' portarsi dietro altre due cose: ``scelte`` (le voci della tendina «AI» dentro
        il riquadro) e ``valore`` (quella scelta adesso). Stanno sulla voce e non sulla griglia
        perche' lo «standard» non e' lo stesso per tutti — una persona 60%, un gatto 50%, un gruppo
        di uccelli 35% — e scriverlo nella tendina e' il posto dove si guarda.
        """
        self._voci = [tuple(v) for v in voci]
        self._colonne = 0
        self._disponi()

    def scelte(self) -> set:
        """Le chiavi accese adesso."""
        return {k for k, s in self._spunte.items() if s.acceso}

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._disponi()

    def _disponi(self) -> None:
        quante = colonne_griglia(self.width(), self.LARGO, self._lay.spacing(), self._massimo)
        if quante == self._colonne and len(self._spunte) == len(self._voci):
            return
        self._colonne = quante
        acceso_adesso = {k: s.acceso for k, s in self._spunte.items()}
        # Rifacendo la griglia non si perde nemmeno la soglia appena scelta, per lo stesso motivo
        # per cui non si perde l'accensione: la larghezza della finestra non e' una risposta.
        valore_adesso = {k: s.valore() for k, s in self._spunte.items() if s.valore() > 0}
        while self._lay.count():
            voce = self._lay.takeAt(0)
            # Il widget si tiene per mano. `voce.widget()` chiamato tre volte di fila sembra la stessa
            # cosa tre volte, e non lo e': dopo `setParent(None)` quel riquadro non ha piu' ne' un
            # genitore ne' un riferimento di Python, PySide6 lo distrugge, e la chiamata dopo torna
            # `None` — con il controllo di sopra gia' passato. Marco il 24/10, aprendo la seconda
            # schermata del wizard: `AttributeError: 'NoneType' object has no attribute 'deleteLater'`
            # a ogni spunta, e la griglia che restava mezza costruita.
            riquadro = voce.widget()
            if riquadro is not None:
                riquadro.setParent(None)
                riquadro.deleteLater()
        self._spunte = {}
        # L'altezza se la deve chiedere: dentro un modulo nessuno indovina quanto spazio vuole una
        # griglia, e il 22/10 il risultato era sei righe di riquadri **sovrapposti** — il modulo ne
        # dava 170 di pixel e la griglia ne voleva 500. Un minimo dichiarato, e il problema sparisce.
        righe = -(-len(self._voci) // quante) if self._voci else 0
        self.setMinimumHeight(righe * self.ALTO + max(0, righe - 1) * self._lay.spacing())
        for i, voce in enumerate(self._voci):
            chiave, testo, emoji, acceso, bloccato = voce[:5]
            scelte = voce[5] if len(voce) > 5 else None
            # Rifacendo la griglia non si perde quello che si era appena acceso: la larghezza della
            # finestra non è un buon motivo per dimenticare una scelta.
            s = _Spunta(testo, emoji, acceso_adesso.get(chiave, acceso), bloccato,
                        scelte=scelte,
                        valore=valore_adesso.get(chiave, voce[6] if len(voce) > 6 else 0.0))
            s.setFixedSize(self.LARGO, self.ALTO)
            s.cliccata.connect(self.cambiata.emit)
            s.valore_cambiato.connect(self.cambiata.emit)
            self._spunte[chiave] = s
            self._lay.addWidget(s, i // quante, i % quante)


class GrigliaDiBox(QWidget):
    """Box tutti uguali, icona sopra e una riga di testo sotto, che vanno a capo da soli.

    Nasce il 22/10 al posto di un elenco di sette pulsanti lunghi una riga ciascuno: «invece di un
    elenco vorrei dei box ordinati, quattro per riga ma dipende dallo spazio». Dipende davvero —
    quante colonne ci stiano lo decide la larghezza, e il conto sta in ``colonne_griglia``, fuori di
    qui, perché è l'unica parte che si possa provare dove l'interfaccia non gira.

    Le voci si danno **già ordinate**: l'ordine giusto lo sa chi le crea, non la griglia.
    """

    LARGO, ALTO = 148, 86
    STILE = f"""
    QToolButton#boxGriglia {{ background: rgba(255,255,255,0.035); border: 1px solid {T.LINE};
                              border-radius: 10px; font-size: {T.SIZE_SMALL}px; padding: 8px 4px; }}
    QToolButton#boxGriglia:hover {{ border: 1px solid {T.INFO}; background: rgba(91,155,240,0.12); }}
    QToolButton#boxGriglia:pressed {{ background: rgba(91,155,240,0.22); }}
    """

    def __init__(self, parent=None, massimo: int = 4):
        super().__init__(parent)
        self.setStyleSheet(self.STILE)
        self._voci: list[tuple[str, str, object]] = []
        self._massimo = massimo
        self._colonne = 0
        self._lay = QGridLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(8)

    def set_voci(self, voci) -> None:
        """``[(testo, icona, azione)]``, nell'ordine in cui vanno mostrate."""
        self._voci = [(str(a), str(b), c) for a, b, c in voci]
        self._colonne = 0
        self._disponi()

    def bottoni(self) -> list[QToolButton]:
        return self.findChildren(QToolButton)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._disponi()

    def _disponi(self) -> None:
        quante = colonne_griglia(self.width(), self.LARGO, self._lay.spacing(), self._massimo)
        if quante == self._colonne and self._lay.count() == len(self._voci):
            return                     # stessa forma di prima: rifarla farebbe solo sfarfallare
        self._colonne = quante
        while self._lay.count():
            voce = self._lay.takeAt(0)
            # Il widget si tiene per mano. `voce.widget()` chiamato tre volte di fila sembra la stessa
            # cosa tre volte, e non lo e': dopo `setParent(None)` quel riquadro non ha piu' ne' un
            # genitore ne' un riferimento di Python, PySide6 lo distrugge, e la chiamata dopo torna
            # `None` — con il controllo di sopra gia' passato. Marco il 24/10, aprendo la seconda
            # schermata del wizard: `AttributeError: 'NoneType' object has no attribute 'deleteLater'`
            # a ogni spunta, e la griglia che restava mezza costruita.
            riquadro = voce.widget()
            if riquadro is not None:
                riquadro.setParent(None)
                riquadro.deleteLater()
        righe = -(-len(self._voci) // quante) if self._voci else 0
        self.setMinimumHeight(righe * self.ALTO + max(0, righe - 1) * self._lay.spacing())
        for i, (testo, icona, azione) in enumerate(self._voci):
            self._lay.addWidget(self._box(testo, icona, azione), i // quante, i % quante)
        for c in range(quante):
            self._lay.setColumnStretch(c, 0)

    def _box(self, testo: str, icona: str, azione) -> QToolButton:
        b = QToolButton()
        b.setObjectName("boxGriglia")
        b.setText(testo)
        b.setToolTip(testo)
        b.setIcon(T.icon(icona, T.MUTED, 22))
        b.setIconSize(QSize(22, 22))
        # L'icona sopra e il testo sotto le mette Qt da sé: è il modo più solido di tenerli allineati
        # in box della stessa misura, senza due etichette da incolonnare a mano.
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        b.setFixedSize(self.LARGO, self.ALTO)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(azione)
        return b

STATUS_TEXT = {
    "online": (N_('In linea'), T.ARMED),
    "connecting": (N_('Connessione…'), T.PAUSED),
    "offline": (N_('Non raggiungibile'), T.ALARM),
    "stopped": (N_("Ferma"), T.DISARMED),
}


class ElencoDiSpunte(QListWidget):
    """Un elenco dove si spunta **cliccando la riga**, non centrando il quadratino.

    Marco, 24/10, guardando una camera mezza selezionata: *«si cliccano a meta', permettendo una
    semiselezione che non serve a nulla, e bisogna cliccare due volte per spuntare la casella»*. E'
    il comportamento di serie di Qt: il primo clic **seleziona** la riga (che qui non vuol dire
    niente: non c'e' nessun «apri»), e solo un clic preciso sul quadratino la spunta.

    Qui la selezione non esiste e tutta la riga e' il quadratino. Un clic, una spunta.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        voce = self.itemAt(event.position().toPoint())
        if voce is not None and bool(voce.flags() & Qt.ItemFlag.ItemIsUserCheckable):
            dentro = self.visualItemRect(voce)
            # Sul quadratino ci pensa Qt: toccarlo anche noi vorrebbe dire spuntare e dispuntare.
            if event.position().x() > dentro.left() + 26:
                voce.setCheckState(Qt.CheckState.Unchecked
                                   if voce.checkState() == Qt.CheckState.Checked
                                   else Qt.CheckState.Checked)
                return
        super().mousePressEvent(event)


def frame_to_qimage(frame: np.ndarray, dentro: tuple[int, int] | None = None,
                    minimo: int = 0) -> QImage:
    """Il fotogramma in un'immagine Qt, rimpicciolito **prima** di essere convertito.

    ``dentro`` sono i pixel veri del riquadro in cui finirà: senza di esso si converte tutto, che è
    quello che si faceva prima e che con sei camere costava ~1 GB/s di copie sul thread della
    finestra. Il conto di quanto ridurre sta in ``mirto.anteprime``, che si prova senza Qt.

    Il colore non si converte più: Qt sa leggere il BGR di OpenCV da sé (``Format_BGR888``), e una
    conversione in meno è un fotogramma intero in meno da copiare.
    """
    if dentro is not None:
        misura = misura_anteprima(frame.shape[1], frame.shape[0], dentro[0], dentro[1], minimo=minimo)
        if misura is not None:
            # INTER_LINEAR, non INTER_AREA. Da manuale per rimpicciolire si usa AREA, ed e' quello che
            # diceva il piano; misurato il 26/10 su 1920x1080 e' la cosa piu' lenta di tutte --
            # 7,6 ms contro 0,16 -- e renderebbe Q-07 un peggioramento. In cambio LINEAR, che prende
            # 2x2 pixel per ognuno che scrive, lascia un po' di scalettatura sui disegni fini (un muro
            # di mattoni, una rete) in un'anteprima da 176 pixel. Non tocca cosa Mirto *vede*: il
            # riconoscimento ha il suo ridimensionamento in detection/, e non passa da qui.
            frame = cv2.resize(frame, misura, interpolation=cv2.INTER_LINEAR)
    if not frame.flags["C_CONTIGUOUS"]:
        # QImage legge la memoria così com'è: su una vista con passi diversi vedrebbe l'immagine
        # sbagliata invece di dare errore.
        frame = np.ascontiguousarray(frame)
    h, w, _ch = frame.shape
    return QImage(frame.data, w, h, frame.strides[0], QImage.Format.Format_BGR888).copy()


def label(text: str = "", obj: str = "", size: int | None = None, weight: int | None = None,
          color: str | None = None) -> QLabel:
    lb = QLabel(text)
    if obj:
        lb.setObjectName(obj)
    css = []
    if size:
        css.append(f"font-size:{size}px;")
    if weight:
        css.append(f"font-weight:{weight};")
    if color:
        css.append(f"color:{color};")
    if css:
        lb.setStyleSheet("".join(css))
    return lb


def action_button(text: str, icon_name: str = "", obj: str = "", color: str = T.TEXT, tooltip: str = "") -> QPushButton:
    b = QPushButton(("  " + text) if icon_name else text)
    if icon_name:
        b.setIcon(T.icon(icon_name, color, 20))
    if obj:
        b.setObjectName(obj)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if tooltip:
        b.setToolTip(tooltip)
    return b


class HouseLogo(QWidget):
    """Il logo di Mirto (casa con l'obiettivo) che mostra lo stato e "vive", come l'icona dell'app.

    * protetta: alone e lucina verdi; l'obiettivo "mette a fuoco": la ghiera gira avanti e
      indietro a scatti casuali e la lente si allarga e si stringe;
    * in pausa: ambra, movimenti più lenti;
    * non protetta: grigio e fermo;
    * rilevamento (``alarm``): rosso, messa a fuoco nervosa.
    """

    def __init__(self, size: int = 60, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.mode = "disarmed"
        self.alarm_until = 0.0
        self.fast_until = 0.0
        self.angle = 0.0          # ghiera di messa a fuoco (gradi)
        self.angle_target = 0.0
        self.zoom = 1.0           # dimensione della lente (1 = normale)
        self.zoom_target = 1.0
        self.glow = 0.0           # 0..1, respiro dell'alone
        self._t = 0.0
        self._hold = 0            # fotogrammi di pausa prima del prossimo "giro" di messa a fuoco
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    # --- stato ---------------------------------------------------------
    def set_mode(self, mode: str) -> None:
        if mode == "disarmed":  # protezione spenta: niente più rosso
            self.alarm_until = self.fast_until = 0.0
        if mode == self.mode:
            self._ensure_timer()
            return
        self.mode = mode
        self._ensure_timer()
        self.update()

    def alarm(self, seconds: float = 60, fast: float = 8) -> None:
        import time  # noqa: PLC0415

        now = time.time()
        self.alarm_until = now + seconds
        self.fast_until = now + fast
        self._hold = 0
        self._ensure_timer()

    def alarming(self) -> bool:
        import time  # noqa: PLC0415

        return time.time() < self.alarm_until

    def _ensure_timer(self) -> None:
        live = self.mode != "disarmed" or self.alarming()
        if live and not self._timer.isActive():
            self._timer.start(33)
        elif not live and self._timer.isActive():
            self._timer.stop()
            self.angle_target, self.zoom_target, self.zoom = self.angle, 1.0, 1.0
            self.update()

    # --- animazione ----------------------------------------------------
    def _tick(self) -> None:
        import math  # noqa: PLC0415
        import random  # noqa: PLC0415
        import time  # noqa: PLC0415

        now = time.time()
        fast = now < self.fast_until
        alarm = now < self.alarm_until
        if self.mode == "disarmed" and not alarm:
            self._ensure_timer()
            return
        slow = self.mode == "paused" and not alarm
        # nuova "ricerca del fuoco": un giro casuale avanti o indietro e una nuova apertura della lente
        if self._hold > 0:
            self._hold -= 1
        elif abs(self.angle_target - self.angle) < 1.5 and abs(self.zoom_target - self.zoom) < 0.02:
            span = 140 if fast else 70 if not slow else 35
            self.angle_target = self.angle + random.choice((-1, 1)) * random.uniform(span * 0.3, span)
            self.zoom_target = random.uniform(0.78, 1.18) if not slow else random.uniform(0.92, 1.06)
            self._hold = random.randint(2, 8) if fast else random.randint(15, 60) if not slow else random.randint(40, 110)
        ease = 0.22 if fast else 0.09 if not slow else 0.05
        self.angle += (self.angle_target - self.angle) * ease
        self.zoom += (self.zoom_target - self.zoom) * ease
        self._t += 0.15 if fast else 0.05
        self.glow = 0.5 + 0.5 * math.sin(self._t)
        self.update()

    # --- disegno -------------------------------------------------------
    def paintEvent(self, _event) -> None:  # noqa: N802 (nome Qt)
        import math  # noqa: PLC0415

        from PySide6.QtGui import QLinearGradient, QRadialGradient  # noqa: PLC0415

        alarm = self.alarming()
        live = alarm or self.mode != "disarmed"
        state = QColor(T.ALARM if alarm else T.STATE_COLORS.get(self.mode, T.DISARMED))
        s = float(min(self.width(), self.height()))
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)

        # alone esterno del colore dello stato (respira)
        m = s * 0.09
        tile = QRectF(m, m, s - 2 * m, s - 2 * m)
        rad = tile.width() * 0.24
        if live:
            for k, a in ((3, 26), (2, 40), (1, 60)):
                c = QColor(state)
                c.setAlpha(int(a * (0.55 + 0.45 * self.glow)))
                p.setBrush(c)
                g = m * 0.33 * k
                p.drawRoundedRect(tile.adjusted(-g, -g, g, g), rad + g, rad + g)

        # piastrella come l'icona: sfumatura blu-ardesia con bordo e riflesso in alto
        grad = QLinearGradient(tile.topLeft(), tile.bottomLeft())
        grad.setColorAt(0.0, QColor("#3A4D6A"))
        grad.setColorAt(1.0, QColor("#131A25"))
        p.setBrush(grad)
        border = QColor(state if live else T.LINE)
        border.setAlpha(200 if live else 255)
        p.setPen(QPen(border, max(1.0, s * 0.022)))
        p.drawRoundedRect(tile, rad, rad)
        p.setPen(Qt.PenStyle.NoPen)
        shine = QLinearGradient(tile.topLeft(), tile.center())
        shine.setColorAt(0.0, QColor(255, 255, 255, 38))
        shine.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(shine)
        p.drawRoundedRect(tile.adjusted(2, 2, -2, -tile.height() * 0.5), rad * 0.8, rad * 0.8)

        def X(u):  # noqa: N802 (coordinate dentro la piastrella)
            return tile.x() + u * tile.width()

        def Y(v):  # noqa: N802
            return tile.y() + v * tile.height()

        w = tile.width()
        # casa: alone del colore dello stato + tratto chiaro con sfumatura
        house = QPainterPath()
        house.moveTo(X(0.16), Y(0.47))
        house.lineTo(X(0.50), Y(0.17))
        house.lineTo(X(0.84), Y(0.47))
        house.moveTo(X(0.27), Y(0.40))
        house.lineTo(X(0.27), Y(0.83))
        house.lineTo(X(0.73), Y(0.83))
        house.lineTo(X(0.73), Y(0.40))
        if live:
            halo = QColor(state)
            halo.setAlpha(int(70 + 50 * self.glow))
            pen = QPen(halo, w * 0.15)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.strokePath(house, pen)
        stroke = QLinearGradient(QPointF(0, Y(0.15)), QPointF(0, Y(0.85)))
        if live:
            stroke.setColorAt(0.0, QColor("#FFFFFF"))
            stroke.setColorAt(1.0, QColor(state).lighter(135))
        else:
            stroke.setColorAt(0.0, QColor("#B8C2CF"))
            stroke.setColorAt(1.0, QColor("#6B7A8F"))
        pen = QPen(stroke, w * 0.075)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.strokePath(house, pen)

        # obiettivo: ghiera azzurra con riflesso, corpo scuro, lente che si allarga e si stringe
        cx, cy = X(0.50), Y(0.615)
        R = w * 0.175
        blue = QColor(T.INFO if live else "#5A6A80")
        ring = QRadialGradient(QPointF(cx - R * 0.3, cy - R * 0.35), R * 1.3)
        ring.setColorAt(0.0, blue.lighter(150))
        ring.setColorAt(1.0, blue.darker(115))
        p.setBrush(ring)
        p.drawEllipse(QPointF(cx, cy), R, R)
        p.setBrush(QColor("#0B1018"))
        p.drawEllipse(QPointF(cx, cy), R * 0.80, R * 0.80)
        # tacche della ghiera di messa a fuoco
        tick = QColor(blue.lighter(170) if live else "#7D8BA0")
        tick.setAlpha(230)
        tp = QPen(tick, w * 0.022)
        tp.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(tp)
        for k in range(10):
            a = math.radians(self.angle + k * 36)
            r1, r2 = R * (0.66 if k % 2 else 0.60), R * 0.76
            p.drawLine(QPointF(cx + r1 * math.cos(a), cy + r1 * math.sin(a)),
                       QPointF(cx + r2 * math.cos(a), cy + r2 * math.sin(a)))
        p.setPen(Qt.PenStyle.NoPen)
        lr = R * 0.50 * self.zoom
        lens = QRadialGradient(QPointF(cx - lr * 0.25, cy - lr * 0.3), lr * 1.2)
        if live:
            lens.setColorAt(0.0, QColor("#4C86D6"))
            lens.setColorAt(0.55, QColor("#1D3C6B"))
            lens.setColorAt(1.0, QColor("#0A1424"))
        else:
            lens.setColorAt(0.0, QColor("#3A4658"))
            lens.setColorAt(1.0, QColor("#141B26"))
        p.setBrush(lens)
        p.drawEllipse(QPointF(cx, cy), lr, lr)
        p.setBrush(QColor("#05080D"))
        p.drawEllipse(QPointF(cx, cy), lr * 0.45, lr * 0.45)
        if live:  # riflesso colorato dello stato dentro la lente
            tint = QColor(state)
            tint.setAlpha(int(60 + 60 * self.glow))
            p.setBrush(tint)
            p.drawEllipse(QPointF(cx + lr * 0.28, cy + lr * 0.30), lr * 0.18, lr * 0.18)
        p.setBrush(QColor(255, 255, 255, 230 if live else 110))
        p.drawEllipse(QPointF(cx - R * 0.33, cy - R * 0.36), R * 0.17, R * 0.17)
        p.setBrush(QColor(255, 255, 255, 120 if live else 50))
        p.drawEllipse(QPointF(cx - R * 0.10, cy - R * 0.50), R * 0.07, R * 0.07)

        # lucina di stato sul tetto
        lx, ly, rr = X(0.50), Y(0.355), w * 0.058
        if live:
            g = QRadialGradient(QPointF(lx, ly), rr * 3.2)
            c1 = QColor(state)
            c1.setAlpha(int(150 + 80 * self.glow))
            c2 = QColor(state)
            c2.setAlpha(0)
            g.setColorAt(0.0, c1)
            g.setColorAt(1.0, c2)
            p.setBrush(g)
            p.drawEllipse(QPointF(lx, ly), rr * 3.2, rr * 3.2)
        led = QRadialGradient(QPointF(lx - rr * 0.3, ly - rr * 0.3), rr * 1.3)
        led.setColorAt(0.0, QColor("#FFFFFF") if live else QColor("#9AA8BA"))
        led.setColorAt(0.45, state.lighter(120) if live else QColor("#6B7A8F"))
        led.setColorAt(1.0, state.darker(130) if live else QColor("#4A586C"))
        p.setBrush(led)
        p.drawEllipse(QPointF(lx, ly), rr, rr)
        p.end()


class StateBand(QFrame):
    """Fascia colorata in cima alla finestra: lo stato si capisce anche da lontano.

    Quando viene rilevata una persona lampeggia in rosso per qualche secondo.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(6)
        self._color = T.DISARMED
        self._flash_left = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._paint(self._color)

    def _paint(self, color: str) -> None:
        self.setStyleSheet(f"background:{color}; border:none;")

    def set_color(self, color: str) -> None:
        self._color = color
        if not self._timer.isActive():
            self._paint(color)

    def flash(self, seconds: int = 8) -> None:
        self._flash_left = seconds * 2
        self._timer.start(500)

    def stop_flash(self) -> None:
        self._flash_left = 0
        self._timer.stop()
        self._paint(self._color)

    def _tick(self) -> None:
        self._flash_left -= 1
        self._paint(T.ALARM if self._flash_left % 2 else self._color)
        if self._flash_left <= 0:
            self._timer.stop()
            self._paint(self._color)


def mono_font(size: int):
    """Carattere a spaziatura fissa del sistema (SF Mono/Menlo su Mac, Consolas su Windows)."""
    from PySide6.QtGui import QFontDatabase  # noqa: PLC0415

    f = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    f.setPixelSize(size)
    return f


class StatusRow(QWidget):
    """Riga di stato della colonna sinistra: pallino colorato + testo (es. "Telegram collegato").

    Con ``mono=True`` usa il carattere da console, come una riga di log.

    Le righe lunghe si aprono e si chiudono: con ``chiave`` compare un **più** a sinistra del pallino,
    e il clic arriva fuori come ``apri_chiudi``. Qui dentro non si decide niente — cosa è aperto se lo
    ricorda la finestra, che passa già il testo giusto: così la riga resta una riga e basta.
    """

    apri_chiudi = Signal(str)     # la chiave della sezione (o "nodo:<id>") su cui si è cliccato

    def __init__(self, parent=None, mono: bool = False):
        super().__init__(parent)
        self.mono = mono
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.piu = QLabel("")
        self.piu.setTextFormat(Qt.TextFormat.RichText)
        self.piu.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.piu.setCursor(Qt.CursorShape.PointingHandCursor)
        self.piu.setFixedWidth(12)   # fisso: così le righe senza più restano incolonnate con le altre
        self.piu.linkActivated.connect(self._cliccato)
        self.dot = QLabel("●")
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.detail = QLabel()   # righe di dettaglio sotto la voce (es. gruppo e utenti Telegram)
        self.detail.setWordWrap(True)
        self.detail.setTextFormat(Qt.TextFormat.RichText)
        self.detail.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.detail.linkActivated.connect(self._cliccato)
        self.detail.setStyleSheet(f"color:{T.MUTED}; font-size:{T.SIZE_SMALL - (2 if mono else 1)}px;")
        self.detail.hide()
        if mono:
            self.text.setFont(mono_font(T.SIZE_SMALL))
            self.detail.setFont(mono_font(T.SIZE_SMALL - 2))
            self.piu.setFont(mono_font(T.SIZE_SMALL))
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        col.addWidget(self.text)
        col.addWidget(self.detail)
        lay.addWidget(self.piu, 0, Qt.AlignmentFlag.AlignTop)
        lay.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(col, 1)
        self._last = None

    def _cliccato(self, href: str) -> None:
        """I collegamenti di Sistema cominciano con ``sistema:``; gli altri non sono roba nostra."""
        if href.startswith("sistema:"):
            self.apri_chiudi.emit(href[len("sistema:"):])

    def set(self, color: str, text: str, tooltip: str = "", detail: str = "",
            chiave: str = "", aperto: bool = False, espandibile: bool | None = None) -> None:
        """``detail``: testo HTML semplice, una voce per riga (``<br>``).

        ``chiave`` accende il più; ``espandibile`` dice se c'è davvero qualcosa da aprire (per difetto:
        c'è un dettaglio). Il testo di ``detail`` lo compone chi chiama, già aperto o già chiuso.
        """
        if espandibile is None:
            espandibile = bool(detail)
        key = (color, text, tooltip, detail, chiave, aperto, espandibile)
        if key == self._last:  # aggiornata ogni secondo: evita di ridisegnare se non cambia nulla
            return
        self._last = key
        self.dot.setStyleSheet(f"color:{color}; font-size:{T.SIZE_SMALL}px;")
        self.text.setText(text)
        size = T.SIZE_SMALL if self.mono else T.SIZE_SMALL + 1
        tone = "#C9D4E1" if self.mono else T.TEXT
        self.text.setStyleSheet(f"color:{tone if color != T.DISARMED else T.MUTED}; font-size:{size}px;")
        segno = "−" if aperto else "+"
        self.piu.setText(f"<a href='sistema:{chiave}' style='text-decoration:none'>{segno}</a>"
                         if chiave and espandibile else "")
        self.detail.setText(detail)
        self.detail.setVisible(bool(detail))
        self.setToolTip(tooltip)


class VideoView(QWidget):
    """Immagine dal vivo con nome, stato e avvisi disegnati SOPRA il video, come nelle centrali.

    Disegna in ``paintEvent`` (non con ``setPixmap``) così il riquadro si può
    ridimensionare liberamente in entrambe le direzioni.
    """

    double_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(160, 90)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.name = ""
        self.status = "connecting"
        self.badge = ""
        self.badge_color = T.ALARM
        self.border_color = ""   # bordo di evidenza: rosso persona, blu movimento, ambra avvisi
        self.border_width = 0
        self._last: QPixmap | None = None
        self.last_frame_ts = 0.0   # quando è stato preso: serve a dire che l'immagine non è in diretta

    def sizeHint(self):  # noqa: N802
        return QSize(480, 270)

    # Sotto questa larghezza non si scende, anche in una piastrella minuscola: la piastrella cresce
    # (la finestra si allarga, o un doppio clic la porta a schermo pieno) e l'immagine ferma dell'ultimo
    # fotogramma resterebbe lì, ingrandita, su una camera che intanto ha smesso di mandarne.
    ANTEPRIMA_MINIMA = 320

    def show_frame(self, frame: np.ndarray, ts: float = 0.0) -> None:
        # Pixel veri, non quelli della finestra: su un Retina sono il doppio, e chiederli logici
        # darebbe un'immagine morbida su metà dei Mac.
        scala = self.devicePixelRatioF() or 1.0
        dentro = (max(1, int(self.width() * scala)), max(1, int(self.height() * scala)))
        self._last = QPixmap.fromImage(frame_to_qimage(frame, dentro, minimo=self.ANTEPRIMA_MINIMA))
        self.last_frame_ts = ts or time.time()
        self.update()

    def refresh_overlay(self) -> None:
        self.update()

    def paintEvent(self, event):  # noqa: N802
        w, h = max(1, self.width()), max(1, self.height())
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, w, h), 12, 12)
        p.setClipPath(clip)
        p.fillRect(0, 0, w, h, QColor("#0E141C"))
        if self._last is not None and not self._last.isNull():
            pw, ph = self._last.width(), self._last.height()
            scale = min(w / pw, h / ph)
            tw, th = pw * scale, ph * scale
            p.drawPixmap(QRectF((w - tw) / 2, (h - th) / 2, tw, th), self._last, QRectF(0, 0, pw, ph))
            if self.status != "online":
                p.fillRect(0, 0, w, h, QColor(14, 20, 28, 150))  # immagine "spenta" se la camera non è in linea
                # …e scritto sopra, perché un'immagine scura resta un'immagine: chi guarda deve
                # sapere che non è in diretta e di quando è. Vedi immagine_ferma() in testi.py.
                avviso = immagine_ferma(self.last_frame_ts)
                f = QFont(self.font())
                f.setPixelSize(14)
                f.setWeight(QFont.Weight.DemiBold)
                p.setFont(f)
                tw = p.fontMetrics().horizontalAdvance(avviso) + 28
                pill = QRectF((w - tw) / 2, h / 2 - 16, tw, 32)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(14, 20, 28, 210))
                p.drawRoundedRect(pill, 16, 16)
                p.setPen(QColor("#FFFFFF"))
                p.drawText(pill, Qt.AlignmentFlag.AlignCenter, avviso)
        else:
            p.setPen(QColor(T.MUTED))
            p.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, _('In attesa di immagini…'))
        # fascia scura in basso per leggere il nome sopra il video
        p.fillRect(QRectF(0, h - 44, w, 44), QColor(0, 0, 0, 120))
        status_text, status_color = STATUS_TEXT.get(self.status, (self.status, T.DISARMED))
        status_text = _(status_text)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(status_color))
        p.drawEllipse(QRectF(14, h - 27, 10, 10))
        f = QFont(self.font())
        f.setPixelSize(13)
        p.setFont(f)
        status_w = p.fontMetrics().horizontalAdvance(status_text) + 16
        p.setPen(QColor("#DDE3EA"))
        p.drawText(QRectF(w - status_w - 14, h - 40, status_w, 32),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, status_text)
        f.setPixelSize(16)
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(QColor("#FFFFFF"))
        name_w = max(10, w - 32 - status_w - 24)
        name = p.fontMetrics().elidedText(self.name, Qt.TextElideMode.ElideRight, int(name_w))
        p.drawText(QRectF(32, h - 40, name_w, 32), Qt.AlignmentFlag.AlignVCenter, name)
        if self.badge:
            f.setPixelSize(14)
            f.setWeight(QFont.Weight.Bold)
            p.setFont(f)
            tw2 = p.fontMetrics().horizontalAdvance(self.badge) + 28
            p.setBrush(QColor(self.badge_color))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(QRectF(12, 12, tw2, 30), 15, 15)
            p.setPen(QColor("#FFFFFF"))
            p.drawText(QRectF(12, 12, tw2, 30), Qt.AlignmentFlag.AlignCenter, self.badge)
        if self.border_color and self.border_width:
            bw = self.border_width
            p.setClipping(False)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(self.border_color), bw))
            p.drawRoundedRect(QRectF(bw / 2, bw / 2, w - bw, h - bw), 12, 12)
        p.end()

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)


class CameraTile(QWidget):
    """Riquadro di una camera: solo il video, con le informazioni sovrapposte."""

    double_clicked = Signal(str)
    photo_requested = Signal(str)

    def __init__(self, cam_id: str, name: str, parent=None):
        super().__init__(parent)
        self.cam_id = cam_id
        self.last_seq = -1
        self.video = VideoView()
        self.video.name = name
        self.video.double_clicked.connect(lambda: self.double_clicked.emit(self.cam_id))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.video)
        self.setToolTip(_('Doppio clic per ingrandire · tasto destro per scattare una foto'))
        self.badge_level = ""
        self._blink_left = 0
        self._blink = QTimer(self)
        self._blink.timeout.connect(self._blink_tick)
        self.photo_btn = QPushButton(_('  Foto'), self)
        self.photo_btn.setIcon(T.icon("camera", "#FFFFFF", 16))
        self.photo_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.photo_btn.setToolTip(_('Salva e apre una foto di questa camera'))
        self.photo_btn.setStyleSheet(
            "QPushButton { background: rgba(14,20,28,0.78); color: #FFFFFF; border: none;"
            " border-radius: 8px; padding: 6px 12px; font-weight: 600; }"
            "QPushButton:hover { background: rgba(14,20,28,0.95); }")
        self.photo_btn.adjustSize()
        self.photo_btn.clicked.connect(lambda: self.cam_id and self.photo_requested.emit(self.cam_id))
        self.photo_btn.hide()

    def contextMenuEvent(self, event) -> None:  # noqa: N802 (nome Qt)
        from PySide6.QtWidgets import QMenu  # noqa: PLC0415

        if not self.cam_id:
            return
        menu = QMenu(self)
        shot = menu.addAction(T.icon("camera", T.TEXT, 18), _('Foto ora'))
        zoom = menu.addAction(T.icon("layout-grid", T.TEXT, 18), _('Ingrandisci / torna alla griglia'))
        chosen = menu.exec(event.globalPos())
        if chosen is shot:
            self.photo_requested.emit(self.cam_id)
        elif chosen is zoom:
            self.double_clicked.emit(self.cam_id)

    # pulsante Foto sopra il video: compare quando il mouse passa sulla camera
    def resizeEvent(self, event) -> None:  # noqa: N802 (nome Qt)
        super().resizeEvent(event)
        b = self.photo_btn
        b.move(self.width() - b.width() - 10, self.height() - b.height() - 10)

    def enterEvent(self, event) -> None:  # noqa: N802 (nome Qt)
        super().enterEvent(event)
        if self.cam_id:
            self.photo_btn.show()
            self.photo_btn.raise_()

    def leaveEvent(self, event) -> None:  # noqa: N802 (nome Qt)
        super().leaveEvent(event)
        self.photo_btn.hide()

    @property
    def name(self) -> str:
        return self.video.name

    def set_name(self, name: str) -> None:
        self.video.name = name
        self.video.refresh_overlay()

    def set_status(self, status: str) -> None:
        self.video.status = status
        self.video.refresh_overlay()

    def set_badge(self, text: str, level: str = "alert", blink: bool = True) -> None:
        """Etichetta sopra il video e bordo colorato attorno alla camera.

        Persona ("alert"): bordo rosso spesso che lampeggia per 8 secondi e poi resta fisso;
        movimento ("info"): bordo blu sottile; avvisi ("warn"): bordo ambra sottile.
        """
        color = {"alert": T.ALARM, "info": T.INFO, "warn": T.PAUSED, "object": T.OBJECT}.get(level, T.ALARM)
        self.video.badge = text
        self.video.badge_color = color
        self.badge_level = level if text else ""
        if not text:
            self._blink.stop()
            self.video.border_color, self.video.border_width = "", 0
        elif level in ("alert", "object"):
            self.video.border_color, self.video.border_width = color, 6
            if blink:
                self._blink_left = 16
                self._blink.start(500)
        else:
            self._blink.stop()
            self.video.border_color, self.video.border_width = color, 3
        self.video.refresh_overlay()

    def _blink_tick(self) -> None:
        self._blink_left -= 1
        self.video.border_width = 6 if self._blink_left % 2 == 0 else 0
        if self._blink_left <= 0:
            self._blink.stop()
            self.video.border_width = 6
        self.video.refresh_overlay()

    @property
    def badge_text(self) -> str:
        return self.video.badge

    def show_frame(self, frame: np.ndarray, ts: float = 0.0) -> None:
        self.video.show_frame(frame, ts)


def _event_overlay(pix: QPixmap, when: str, sub: str, person: bool, k: int = 1) -> QPixmap:
    """Scrive ora e camera SOPRA la foto, su una fascia scura in basso: si leggono su qualunque immagine."""
    out = QPixmap(pix)
    w, h = out.width(), out.height()
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    band = h * 0.42
    from PySide6.QtGui import QLinearGradient  # noqa: PLC0415

    grad = QLinearGradient(0, h - band, 0, h)
    grad.setColorAt(0.0, QColor(10, 15, 22, 0))
    grad.setColorAt(0.45, QColor(10, 15, 22, 170))
    grad.setColorAt(1.0, QColor(10, 15, 22, 235))
    p.fillRect(QRectF(0, h - band, w, band), grad)
    f = QFont()
    f.setPixelSize(20 * k)
    f.setWeight(QFont.Weight.Bold)
    p.setFont(f)
    p.setPen(QColor("#FFFFFF"))
    p.drawText(QRectF(10 * k, h - 46 * k, w - 20 * k, 24 * k),
               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, when)
    f2 = QFont()
    f2.setPixelSize(12 * k)
    p.setFont(f2)
    p.setPen(QColor("#D5DDE8"))
    sub = QFontMetrics(f2).elidedText(sub, Qt.TextElideMode.ElideRight, w - 20 * k)
    p.drawText(QRectF(10 * k, h - 22 * k, w - 20 * k, 16 * k),
               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, sub)
    if not person:  # animali, veicoli, oggetti: puntino viola in alto
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(T.OBJECT))
        p.drawEllipse(QRectF(w - 18 * k, 8 * k, 10 * k, 10 * k))
    p.end()
    return out


def _play_badge(pix: QPixmap, scale: int) -> None:
    """Cerchio scuro con ▶ bianco: l'evento ha la sua clip video."""
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = 13 * scale
    cx, cy = pix.width() - r - 8 * scale, r + 8 * scale
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(0, 0, 0, 150))
    p.drawEllipse(QPointF(cx, cy), r, r)
    p.setBrush(QColor("#FFFFFF"))
    t = r * 0.45
    path = QPainterPath()
    path.moveTo(cx - t * 0.7, cy - t)
    path.lineTo(cx + t, cy)
    path.lineTo(cx - t * 0.7, cy + t)
    path.closeSubpath()
    p.drawPath(path)
    p.end()


def event_thumbnail(ev: dict, w: int = 208, h: int = 117) -> QPixmap:
    """Foto dell'evento ritagliata a 16:9 con ora e camera scritte sopra (per la striscia degli eventi)."""
    import time  # noqa: PLC0415

    from ..detection.labels import display_name  # noqa: PLC0415

    lt = time.localtime(ev["ts"])
    today = time.strftime("%d/%m") == time.strftime("%d/%m", lt)
    lab = ev.get("label") or "person"
    what = "" if lab == "person" else f"{display_name(lab)} · "
    sub = (_("Oggi") if today else time.strftime("%d/%m", lt)) + f" · {what}{ev['camera_name']}"
    scale = 2  # nitida sugli schermi Retina
    pix = QPixmap(ev["image_path"]) if ev.get("image_path") else QPixmap()
    if pix.isNull():
        pix = QPixmap(w * scale, h * scale)
        pix.fill(QColor("#0E141C"))
    else:
        pix = pix.scaled(w * scale, h * scale, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                         Qt.TransformationMode.SmoothTransformation)
        pix = pix.copy((pix.width() - w * scale) // 2, (pix.height() - h * scale) // 2, w * scale, h * scale)
    pix.setDevicePixelRatio(1)
    out = _event_overlay(pix, time.strftime("%H:%M", lt), sub, lab == "person", scale)
    if ev.get("image_path"):
        from pathlib import Path  # noqa: PLC0415

        if Path(ev["image_path"]).with_suffix(".mp4").exists():  # c'è la clip: pulsante ▶ in alto a destra
            _play_badge(out, scale)
    # angoli arrotondati
    rounded = QPixmap(out.size())
    rounded.fill(Qt.GlobalColor.transparent)
    p = QPainter(rounded)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, out.width(), out.height()), 8 * scale, 8 * scale)
    p.setClipPath(path)
    p.drawPixmap(0, 0, out)
    p.end()
    rounded.setDevicePixelRatio(scale)
    return rounded


class EventRow(QWidget):
    """Voce della cronologia: anteprima, ora in evidenza, camera.

    ``vertical=True``: scheda della striscia orizzontale degli eventi, con ora e camera scritte sopra la foto.
    """

    THUMB_W, THUMB_H = 208, 117
    CARD_W, CARD_H = THUMB_W + 12, THUMB_H + 12

    def __init__(self, ev: dict, parent=None, vertical: bool = False):
        super().__init__(parent)
        import time  # noqa: PLC0415

        from ..detection.labels import display_name  # noqa: PLC0415

        lt = time.localtime(ev["ts"])
        today = time.strftime("%d/%m") == time.strftime("%d/%m", lt)
        lab = ev.get("label") or "person"
        what = "" if lab == "person" else f"{display_name(lab)} · "
        full = (_("Oggi") if today else time.strftime("%d/%m", lt)) + f" · {what}{ev['camera_name']}"
        self.setToolTip(time.strftime("%d/%m/%Y %H:%M:%S", lt) + f" · {what}{ev['camera_name']}")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)  # selezione gestita dalla lista

        if vertical:
            lay = QVBoxLayout(self)
            lay.setContentsMargins(6, 6, 6, 6)
            tw, th = self.THUMB_W, self.THUMB_H
            thumb = QLabel()
            thumb.setFixedSize(tw, th)
            thumb.setStyleSheet("background:#0E141C; border-radius:8px;")
            pix = QPixmap(ev["image_path"]) if ev.get("image_path") else QPixmap()
            if pix.isNull():
                pix = QPixmap(tw, th)
                pix.fill(QColor("#0E141C"))
            else:
                pix = pix.scaled(tw, th, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                 Qt.TransformationMode.SmoothTransformation).copy(0, 0, tw, th)
            thumb.setPixmap(_event_overlay(pix, time.strftime("%H:%M", lt), full, lab == "person"))
            lay.addWidget(thumb)
            return

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(12)
        thumb = QLabel()
        thumb.setFixedSize(96, 54)
        thumb.setStyleSheet("background:#0E141C; border-radius:6px;")
        if ev.get("image_path"):
            pix = QPixmap(ev["image_path"])
            if not pix.isNull():
                thumb.setPixmap(pix.scaled(96, 54, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                           Qt.TransformationMode.SmoothTransformation))
        text = QVBoxLayout()
        text.setSpacing(2)
        when = label(time.strftime("%H:%M", lt), size=T.SIZE_TITLE, weight=700)
        f = QFont()
        f.setPixelSize(T.SIZE_SMALL)
        sub = label(QFontMetrics(f).elidedText(full, Qt.TextElideMode.ElideRight, 150), obj="muted")
        sub.setStyleSheet(f"color:{T.MUTED}; font-size:{T.SIZE_SMALL}px;")
        text.addWidget(when)
        text.addWidget(sub)
        lay.addWidget(thumb)
        lay.addLayout(text, 1)


class SizeSlider(QWidget):
    """Cursore della grandezza delle anteprime, in stile macOS (come in Foto e nel Finder)."""

    changed = Signal(int)

    def __init__(self, value: int = 45, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QSlider  # noqa: PLC0415

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        small, big = QLabel(), QLabel()
        small.setPixmap(T.icon_pixmap("image", T.FAINT, 12))
        big.setPixmap(T.icon_pixmap("image", T.FAINT, 18))
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(int(value))
        self.slider.setFixedWidth(170)
        self.slider.setToolTip(_('Grandezza delle anteprime (tutto a destra: riempiono lo spazio)'))
        self.slider.setStyleSheet(
            "QSlider { background: transparent; height: 20px; }"
            "QSlider::groove:horizontal { height: 4px; border-radius: 2px; background: #3A4A60; }"
            "QSlider::sub-page:horizontal { height: 4px; border-radius: 2px; background: #8FA6C2; }"
            "QSlider::handle:horizontal { width: 16px; height: 16px; margin: -6px 0; border-radius: 8px;"
            " background: #F2F5F9; border: 1px solid #B8C3D1; }"
            "QSlider::handle:horizontal:hover { background: #FFFFFF; }")
        self.slider.valueChanged.connect(self.changed.emit)
        lay.addWidget(small)
        lay.addWidget(self.slider)
        lay.addWidget(big)

    def value(self) -> int:
        return self.slider.value()


class AddTile(QWidget):
    """Casella tratteggiata in fondo alla griglia: porta alla sezione "Tutte le cam"."""

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.title = _('Tutte le cam')
        self.detail = ""
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(_('Scegli quali camere usa Mirto'))
        self._hover = False

    def set_text(self, title: str, detail: str) -> None:
        self.title, self.detail = title, detail
        self.update()

    def enterEvent(self, e):  # noqa: N802
        self._hover = True
        self.update()

    def leaveEvent(self, e):  # noqa: N802
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e):  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def paintEvent(self, event):  # noqa: N802
        w, h = self.width(), self.height()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(T.INFO if self._hover else T.LINE), 2, Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(QColor(255, 255, 255, 10 if self._hover else 0))
        p.drawRoundedRect(QRectF(1, 1, w - 2, h - 2), 12, 12)
        size = 36 if h > 160 else 26
        p.drawPixmap(int((w - size) / 2), int(h / 2 - size - 18), T.icon_pixmap("plus", T.INFO if self._hover else T.MUTED, size))
        f = QFont(self.font())
        f.setPixelSize(17)
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(QColor(T.TEXT))
        p.drawText(QRectF(10, h / 2 - 12, w - 20, 26), Qt.AlignmentFlag.AlignCenter, self.title)
        f.setPixelSize(13)
        f.setWeight(QFont.Weight.Normal)
        p.setFont(f)
        p.setPen(QColor(T.MUTED))
        p.drawText(QRectF(14, h / 2 + 16, w - 28, h / 2 - 20),
                   (Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop).value | Qt.TextFlag.TextWordWrap.value,
                   self.detail)
        p.end()


class CameraGrid(QWidget):
    """Griglia delle camere usate da Mirto: caselle 16:9 sempre della stessa misura.

    Anche con una sola camera resta una griglia (almeno 2 colonne, l'ultima casella
    è "Tutte le cam"), così è chiaro che qui compaiono tutte le camere attive.
    """

    GAP = 14
    MAX_CELL_W = 640
    SIZES = {"small": 20, "medium": 45, "large": 75, "max": 100}  # vecchi valori salvati → cursore 0-100
    MIN_W, MAX_W = 220, 1400  # larghezza di una casella agli estremi del cursore (100 = riempie)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._widgets: list[QWidget] = []
        self._value = 45
        self.max_cell_w = self.MAX_CELL_W

    def size_value(self) -> int:
        return self._value

    def set_size(self, value) -> None:
        """Grandezza delle anteprime da 0 (piccolissime) a 100 (riempiono lo spazio)."""
        if isinstance(value, str):
            value = self.SIZES.get(value, 45)
        try:
            value = max(0, min(100, int(value)))
        except (TypeError, ValueError):
            value = 45
        self._value = value
        if value >= 100:
            self.max_cell_w = 100000
        else:  # scala esponenziale: il cursore si muove in modo uniforme anche sulle misure piccole
            self.max_cell_w = self.MIN_W * (self.MAX_W / self.MIN_W) ** (value / 100)
        self._place()

    def set_widgets(self, widgets: list[QWidget]) -> None:
        for w in self._widgets:
            if w not in widgets:
                w.hide()
        self._widgets = list(widgets)
        for w in self._widgets:
            w.setParent(self)
            w.show()
        self._place()

    def resizeEvent(self, event):  # noqa: N802
        self._place()
        super().resizeEvent(event)

    def _place(self) -> None:
        n = len(self._widgets)
        if not n:
            return
        W, H, g = self.width(), self.height(), self.GAP
        best = (0.0, 2)
        for cols in range(2, max(2, n) + 1):
            rows = math.ceil(n / cols)
            cw = min((W - g * (cols - 1)) / cols, ((H - g * (rows - 1)) / rows) * 16 / 9, self.max_cell_w)
            if cw > best[0] + 0.5:
                best = (cw, cols)
        cw, cols = best
        cw = max(cw, 120.0)
        ch = cw * 9 / 16
        total = cols * cw + g * (cols - 1)
        x0 = y0 = 0.0  # griglia allineata in alto a sinistra
        for i, w in enumerate(self._widgets):
            r, c = divmod(i, cols)
            w.setGeometry(int(x0 + c * (cw + g)), int(y0 + r * (ch + g)), int(cw), int(ch))


class Switch(QAbstractButton):
    """Interruttore acceso/spento, grande e leggibile."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(52, 30)

    def sizeHint(self):  # noqa: N802
        return QSize(52, 30)

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        on = self.isChecked()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(T.ARMED if on else "#46566E"))
        if not self.isEnabled():
            p.setOpacity(0.4)
        p.drawRoundedRect(QRectF(0, 0, 52, 30), 15, 15)
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QRectF(25 if on else 3, 3, 24, 24))
        p.end()


class CameraCard(QFrame):
    """Voce della sezione "Tutte le cam": anteprima, nome, tipo, stato, interruttore."""

    toggled = Signal(str, bool)
    rename_requested = Signal(str)
    # «L'immagine che vedo qui è di un'altra camera»: nome e immagine non corrispondono, e lo dice
    # chi sta guardando (25/10). Vedi `CameraManager.scambia_indici` per il perché non lo indoviniamo.
    wrong_camera = Signal(str)
    preview_requested = Signal(str)
    remove_requested = Signal(str)
    photo_requested = Signal(str)

    def __init__(self, cam: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("camCard")
        self.cam_id = cam["id"]
        self.last_seq = -1
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 12, 16, 12)
        lay.setSpacing(16)
        self.thumb = QLabel()
        self.thumb.setFixedSize(176, 99)
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb.setStyleSheet(f"background:#0E141C; border-radius:8px; color:{T.FAINT}; font-size:{T.SIZE_SMALL}px;")
        lay.addWidget(self.thumb)

        text = QVBoxLayout()
        text.setSpacing(4)
        self.name_lb = label("", size=T.SIZE_TITLE, weight=700)
        self.kind_lb = label("", obj="faint")
        # le scritte vanno a capo invece di allargare la scheda (niente barra orizzontale)
        for lb in (self.name_lb, self.kind_lb):
            lb.setWordWrap(True)
            lb.setMinimumWidth(80)
        self.state_row = StatusRow()
        text.addStretch(1)
        text.addWidget(self.name_lb)
        text.addWidget(self.kind_lb)
        text.addWidget(self.state_row)
        text.addStretch(1)
        lay.addLayout(text, 1)

        self.preview_btn = action_button(_("Anteprima"), "eye", obj="small", color=T.MUTED,
                                         tooltip=_('Accende la camera per qualche secondo per vedere cosa inquadra'))
        self.preview_btn.clicked.connect(lambda: self.preview_requested.emit(self.cam_id))
        self.rename_btn = action_button(_("Rinomina"), "pencil", obj="small", color=T.MUTED,
                                        tooltip=_("Nome usato nell'app e negli avvisi"))
        self.rename_btn.clicked.connect(lambda: self.rename_requested.emit(self.cam_id))
        self.remove_btn = action_button(_("Rimuovi"), "trash", obj="small", color=T.MUTED,
                                        tooltip=_('Toglie questa camera di rete da Mirto'))
        self.remove_btn.clicked.connect(lambda: self.remove_requested.emit(self.cam_id))
        self.photo_btn = action_button(_("Foto"), "camera", obj="small", color=T.MUTED,
                                       tooltip=_('Salva e apre una foto di questa camera'))
        self.photo_btn.clicked.connect(lambda: self.photo_requested.emit(self.cam_id))
        # Compare solo quando c'è davvero un'altra camera locale con cui scambiarsi: con una sola
        # camera non c'è nessuno scambio possibile, e un pulsante che non può funzionare è peggio di
        # un pulsante che non c'è.
        self.wrong_btn = action_button(_("Non è questa?"), "refresh-cw", obj="small", color=T.MUTED,
                                       tooltip=_("Se l'immagine qui sopra è di un'altra camera, dillo: "
                                                 "il nome e l'immagine si rimettono d'accordo"))
        self.wrong_btn.clicked.connect(lambda: self.wrong_camera.emit(self.cam_id))
        self.wrong_btn.hide()
        for b in (self.photo_btn, self.preview_btn, self.rename_btn, self.wrong_btn, self.remove_btn):
            b.setIconSize(QSize(16, 16))
            lay.addWidget(b)

        sw_box = QVBoxLayout()
        sw_box.setSpacing(4)
        self.switch = Switch()
        self.switch.toggled.connect(lambda on: self.toggled.emit(self.cam_id, on))
        self.switch_lb = label("", obj="faint")
        self.switch_lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sw_box.addStretch(1)
        sw_box.addWidget(self.switch, 0, Qt.AlignmentFlag.AlignHCenter)
        sw_box.addWidget(self.switch_lb)
        sw_box.addStretch(1)
        box = QWidget()
        box.setFixedWidth(84)
        box.setLayout(sw_box)
        lay.addWidget(box)
        self.update_cam(cam)

    def update_cam(self, cam: dict) -> None:
        self.cam = cam
        self.name_lb.setText(cam["name"])
        kind = cam.get("kind") or _("Camera")
        if cam["name"] != cam.get("original"):
            kind += f" · {cam.get('original')}"
        if cam.get("detail"):
            kind += f" · {cam['detail']}"
        self.kind_lb.setText(kind)
        if not cam["connected"]:
            self.state_row.set(T.DISARMED, _('Non collegata adesso'))
        elif not cam["enabled"]:
            self.state_row.set(T.DISARMED, _('Spenta: Mirto non la usa'))
        else:
            text, color = {"online": (_('In uso · in linea'), T.ARMED),
                           "offline": (_('In uso · non raggiungibile'), T.ALARM)}.get(
                cam.get("status"), (_('In uso · collegamento…'), T.PAUSED))
            self.state_row.set(color, text)
        self.switch.blockSignals(True)
        self.switch.setChecked(cam["enabled"])
        self.switch.blockSignals(False)
        self.switch_lb.setText(_("Attiva") if cam["enabled"] else _("Spenta"))
        self.switch_lb.setStyleSheet(f"color:{T.ARMED if cam['enabled'] else T.FAINT}; font-size:{T.SIZE_SMALL}px;"
                                     "font-weight:600;")
        self.preview_btn.setVisible(cam["connected"] and not cam["enabled"])
        self.photo_btn.setVisible(cam["connected"] and cam["enabled"])
        # Solo dove l'immagine si sta vedendo: senza immagine davanti, «non è questa?» è una domanda
        # a cui non si può rispondere.
        self.wrong_btn.setVisible(bool(cam.get("scambiabile")) and cam["connected"] and cam["enabled"])
        # le camere di un nodo si tolgono rimuovendo il nodo (Impostazioni → Nodi)
        self.remove_btn.setVisible(cam["id"].startswith("net:") and not cam.get("node"))
        if not cam["enabled"] or not cam["connected"]:
            # niente video: scritta al posto dell'anteprima (ma non cancella un'anteprima appena scattata)
            pm = self.thumb.pixmap()
            showing_live = self.last_seq >= 0
            placeholder = (pm is None or pm.isNull()) and self.thumb.text() in ("", _("Spenta"), _('Non collegata'))
            if showing_live or placeholder:
                self.thumb.setPixmap(QPixmap())
                self.thumb.setText(_("Spenta") if cam["connected"] else _('Non collegata'))
                self.last_seq = -1

    def set_frame(self, frame: np.ndarray) -> None:
        # Qui il riquadro è fisso e l'immagine non verrà mai ingrandita: nessun minimo da tenere, ed è
        # il posto dove si risparmia di più — 176×99 di una camera 1080p è il 2 % dei pixel.
        pix = QPixmap.fromImage(frame_to_qimage(frame, (176, 99)))
        self.thumb.setText("")
        self.thumb.setPixmap(pix.scaled(176, 99, Qt.AspectRatioMode.KeepAspectRatio,
                                        Qt.TransformationMode.SmoothTransformation))

    def set_message(self, text: str) -> None:
        self.thumb.setPixmap(QPixmap())
        self.thumb.setText(text)


class WarningSign(QWidget):
    """Cartello di pericolo: triangolo giallo con il punto esclamativo, che pulsa piano."""

    YELLOW = "#FFC928"

    def __init__(self, size: int = 76, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._t = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)

    def _tick(self) -> None:
        self._t += 0.09
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 (nome Qt)
        import math  # noqa: PLC0415

        from PySide6.QtGui import QLinearGradient, QRadialGradient  # noqa: PLC0415

        pulse = 0.5 + 0.5 * math.sin(self._t)
        s = float(min(self.width(), self.height()))
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        cx, cy = s / 2, s * 0.56

        # alone giallo che pulsa
        glow = QRadialGradient(QPointF(cx, cy), s * 0.5)
        c1 = QColor(self.YELLOW)
        c1.setAlpha(int(70 + 90 * pulse))
        c2 = QColor(self.YELLOW)
        c2.setAlpha(0)
        glow.setColorAt(0.0, c1)
        glow.setColorAt(1.0, c2)
        p.setBrush(glow)
        p.drawEllipse(QPointF(cx, cy), s * 0.5, s * 0.5)

        # triangolo con angoli arrotondati (si "respira" leggermente)
        k = 0.97 + 0.03 * pulse
        top, left, right = (QPointF(cx, s * (0.5 - 0.40 * k)), QPointF(cx - s * 0.42 * k, s * (0.5 + 0.34 * k)),
                            QPointF(cx + s * 0.42 * k, s * (0.5 + 0.34 * k)))
        tri = QPainterPath()
        tri.moveTo(top)
        tri.lineTo(right)
        tri.lineTo(left)
        tri.closeSubpath()
        fill = QLinearGradient(top, QPointF(cx, left.y()))
        fill.setColorAt(0.0, QColor("#FFE066"))
        fill.setColorAt(1.0, QColor("#F5A800"))
        pen = QPen(QColor("#F5A800"), s * 0.09)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(fill)
        p.drawPath(tri)
        p.setPen(Qt.PenStyle.NoPen)

        # punto esclamativo semplice e pieno
        dark = QColor("#1B2330")
        p.setBrush(dark)
        w = s * 0.095
        p.drawRoundedRect(QRectF(cx - w / 2, s * 0.33, w, s * 0.25), w / 2, w / 2)
        p.drawEllipse(QPointF(cx, s * 0.69), w * 0.62, w * 0.62)
        p.end()


class WarningDialog(QDialog):
    """Conferma importante con il cartello giallo: "Annulla" è il pulsante predefinito."""

    def __init__(self, title: str, text: str, confirm: str, parent=None, detail: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 18)
        lay.setSpacing(20)
        lay.addWidget(WarningSign(76), 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(8)
        head = QLabel(title)
        head.setStyleSheet(f"font-size:{T.SIZE_TITLE + 2}px; font-weight:800; color:{T.TEXT};")
        col.addWidget(head)
        body = QLabel(text)
        body.setWordWrap(True)
        body.setMinimumWidth(320)
        body.setStyleSheet(f"font-size:{T.SIZE_BODY}px; color:{T.TEXT};")
        col.addWidget(body)
        if detail:
            more = QLabel(detail)
            more.setWordWrap(True)
            more.setStyleSheet(f"font-size:{T.SIZE_SMALL}px; color:{T.MUTED};")
            col.addWidget(more)
        col.addSpacing(10)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton(_("Annulla"))
        cancel.clicked.connect(self.reject)
        ok = QPushButton(confirm)
        ok.setStyleSheet(f"QPushButton {{ background:{T.ALARM}; color:#FFFFFF; font-weight:700; padding:8px 18px; }}"
                         "QPushButton:hover { background:#cf3d42; }")
        ok.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        col.addLayout(row)
        lay.addLayout(col, 1)
        cancel.setDefault(True)
        cancel.setFocus()


def confirm_warning(parent, title: str, text: str, confirm: str, detail: str = "") -> bool:
    """Chiede conferma con il cartello di pericolo; True solo se si preme il pulsante rosso."""
    return WarningDialog(title, text, confirm, parent, detail).exec() == QDialog.DialogCode.Accepted
