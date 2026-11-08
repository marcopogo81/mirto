# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""La striscia dei passi: i riquadri con le frecce in mezzo, e il «+» che propone il pezzo dopo.

È il disegno che Marco ha fatto la sera del 22 ottobre, in riga:

    ┌──────────────┐   5 s   ┌─────────────────────┐
    │ 💡 LUCI      │ ──────▶ │ 🦅 DISSUASORE       │
    │ accendi 10 s │         │ suono: falco  × 3   │
    └──────────────┘         └─────────────────────┘
                   ripeti tutto × 3

Ogni riquadro è un passo, col suo giro dentro (durata, «× N»); ogni freccia è un collegamento e porta
l'attesa; la striscia intera ha il suo «ripeti tutto». Il «× N» del passo sta **dentro** il riquadro e
quello della catena **attorno**: due posti diversi perché sono due cose diverse, e il disegno lo dice
senza spiegarlo.

Le decisioni — cosa proporre dopo una luce, cosa fare su un nodo senza cassa, cosa scrivere dentro un
riquadro — non stanno qui: stanno in ``actions/pannello_catena.py``, dove si provano senza aprire una
finestra. Qui c'è solo il disegno.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import theme as T
from ..actions.pannello_catena import (
    ATTESA,
    EMOJI,
    _tipo_di,
    a_volte,
    comandi_di,
    comando_e_suono,
    componenti_di,
    cose_da_fare,
    pezzo_da_chiave,
    passo_da_scelte,
    vuole_una_cassa,
    lunghezza_per,
    quante_volte,
    secondi_per,
    PAUSA_PROPOSTA,
    Passo,
    cosa_manca_per_provare,
    limiti_ripetizione,
    passo_da_pezzo,
    passo_proposto,
    riassunto,
    riordina,
    riquadri,
    si_puo_aggiungere,
    suggerimenti,
    suoni_del_nodo,
)
from ..actions.passi import A_TURNO, INSIEME, MAX_PAUSA, MAX_RIPETI, capogruppo
from ..i18n import _


class _Riquadro(QFrame):
    """Un passo come riquadro: si clicca per cambiarlo, e si sposta con le frecce ai suoi lati."""

    modifica = Signal()
    togli = Signal()
    sposta = Signal(int)          # -1 a sinistra, +1 a destra
    prova = Signal()              # prova **questo** pezzo: chiesto il 23/10, serve sui suoni
    insieme = Signal()            # aggiungi qualcosa che parte **insieme** a questo (tappa 3)

    LARGO, ALTO = 196, 164

    def __init__(self, dati, primo: bool, ultimo: bool, provabile: bool = False, parent=None,
                 in_prova: bool = False):
        super().__init__(parent)
        self.setObjectName("passo")
        self.setFixedSize(self.LARGO, self.ALTO)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(3)
        testa = QHBoxLayout()
        testa.setSpacing(4)
        titolo = QLabel(f"{dati.icona} {dati.titolo}")
        titolo.setStyleSheet(f"font-size:{T.SIZE_BODY}px; font-weight:600; background:transparent;")
        titolo.setWordWrap(True)
        testa.addWidget(titolo, 1)
        via = QToolButton()
        via.setText("✕")
        via.setToolTip(_('Togli questo pezzo'))
        via.setAutoRaise(True)
        via.clicked.connect(self.togli.emit)
        testa.addWidget(via, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(testa)
        for riga in dati.righe:
            etichetta = QLabel(riga)
            etichetta.setWordWrap(True)
            etichetta.setStyleSheet(f"font-size:{T.SIZE_SMALL}px; color:{T.MUTED}; background:transparent;")
            lay.addWidget(etichetta)
        lay.addStretch(1)
        if dati.avviso:
            nota = QLabel(dati.avviso)
            nota.setWordWrap(True)
            nota.setStyleSheet(f"font-size:{T.SIZE_SMALL}px; color:{T.PAUSED}; background:transparent;")
            lay.addWidget(nota)
        piedi = QHBoxLayout()
        piedi.setSpacing(4)
        # Icone, non caratteri: «▶» per spostare e «▶» per sentire erano lo stesso triangolo, e si
        # distinguevano solo provando. Ora spostare ha le frecce col fermo, sentire ha il triangolo
        # pieno, e cambiare ha l'ingranaggio (23/10).
        for icona, verso, attivo, aiuto in (("ui-skip-back", -1, not primo, _('Sposta a sinistra')),
                                            ("ui-skip-forward", +1, not ultimo, _('Sposta a destra'))):
            b = QToolButton()
            b.setIcon(T.icon(icona, T.MUTED if attivo else T.FAINT, 15))
            b.setToolTip(aiuto)
            b.setAutoRaise(True)
            b.setEnabled(attivo)
            b.clicked.connect(lambda _c=False, v=verso: self.sposta.emit(v))
            piedi.addWidget(b)
        piedi.addStretch(1)
        if provabile:
            # Provare **questo** suono, senza far partire tutta la catena: quando se ne cerca uno
            # fra cinque, sentire ogni volta anche la luce e l'attesa è un modo per non provarlo.
            senti = QToolButton()
            senti.setIcon(T.icon("ui-pause" if in_prova else "ui-play",
                                 T.ALARM if in_prova else T.INFO, 16))
            senti.setToolTip(_('Ferma') if in_prova else _('Senti solo questo pezzo'))
            senti.setAutoRaise(True)
            senti.clicked.connect(self.prova.emit)
            piedi.addWidget(senti)
        # Il «+» del riquadro: *«il box del pezzo deve avere anche un simbolo + per aggiungere
        # qualcosa in parallelo»* (Marco, 24/10). Non è il «+» della striscia, che aggiunge **dopo**:
        # questo aggiunge **insieme**, ed è la differenza fra una fila e un accordo.
        aggiungi = QToolButton()
        aggiungi.setText("＋")
        aggiungi.setToolTip(_('Aggiungi qualcosa che parte insieme a questo'))
        aggiungi.setAutoRaise(True)
        aggiungi.clicked.connect(self.insieme.emit)
        piedi.addWidget(aggiungi)
        cambia = QToolButton()
        cambia.setIcon(T.icon("settings", T.MUTED, 16))
        cambia.setToolTip(_('Cambia questo pezzo'))
        cambia.setAutoRaise(True)
        cambia.clicked.connect(self.modifica.emit)
        piedi.addWidget(cambia)
        lay.addLayout(piedi)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.modifica.emit()
        super().mouseDoubleClickEvent(event)


class PassoDialog(QDialog):
    """Un passo da vicino: comando, suono, durata, quante volte, e l'attesa dopo."""

    def __init__(self, passo: Passo, comandi: list[dict], parent=None,
                 dispositivi: list | None = None, dispositivo: str = "",
                 suoni_propri: list | None = None, uscite_per: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle(_('Il pezzo'))
        self.risultato: Passo | None = None
        # Il pezzo com'era: questa finestra cambia **cosa fa**, non **dove sta nella catena**, e
        # quello che non chiede deve tornare indietro intatto — il gruppo, il modo, e dal 25/10
        # anche l'attesa. Vedi `_salva`.
        self._prima = passo
        self._tutti = list(comandi or [])
        self._propri = suoni_propri
        self._uscite_per = dict(uscite_per or {})
        self._predefinito = str(dispositivo or "")
        self._dispositivo = passo.dispositivo or self._predefinito
        # Solo i dispositivi che sanno **eseguire** qualcosa, cioè i nodi. «Questo computer» ascolta e
        # avvisa, non ha morsetti: metterlo qui vorrebbe dire lasciar costruire un pezzo che non
        # partirà mai, e accorgersene solo al primo scatto (23/10).
        self._dispositivi = [d for d in (dispositivi or []) if str(d.get("chiave", "")).startswith("nodo:")]
        self._comandi = comandi_di(self._tutti, self._dispositivo)
        f = QFormLayout(self)
        f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.dove = QComboBox()
        for d in self._dispositivi:
            self.dove.addItem(str(d.get("nome") or d.get("chiave")), str(d.get("chiave")))
        if self._dispositivo and self.dove.findData(self._dispositivo) < 0 and self._dispositivo:
            self.dove.addItem(self._dispositivo, self._dispositivo)
        self.dove.setCurrentIndex(max(0, self.dove.findData(self._dispositivo)))
        self._suoni_nodo = suoni_del_nodo(self._comandi, self._propri)
        self._e_un_suono = vuole_una_cassa(_tipo_di(passo.comando, self._comandi))
        self.comando = QComboBox()
        self.comando.setEditable(True)
        for c in self._comandi:
            self.comando.addItem(str(c.get("nome")), str(c.get("nome")))
        self.comando.setCurrentText(passo.comando)
        # Per un suono il dispositivo non si chiede: si chiede che suono far sentire, e da quale
        # comando esca lo sappiamo noi. «Sirena» è un suono come «falco», non un'altra famiglia.
        self.quale = QComboBox()
        # L'icona anche qui: nel «+» si distingueva un suono tuo da uno del nodo, e aprendo il pezzo
        # col cog tornavano tutti uguali — la stessa domanda con due risposte diverse (24/10).
        for v in self._suoni_nodo:
            self.quale.addItem(f"{v['icona']}  {v['nome']}", v["chiave"])
        scelto = f"suono:{passo.comando}:{passo.suono}"
        self.quale.setCurrentIndex(max(0, self.quale.findData(scelto)))
        # **Da quale uscita** di quel dispositivo (API 1.10). Il «+» la sceglie già; qui si corregge,
        # perché una cassa sbagliata è il genere di cosa che si scopre sentendo.
        self.uscita = QComboBox()
        self._rifai_uscite(passo.uscita)
        self.suono = QComboBox()
        self.suono.addItem(_('Quello del nodo (a rotazione)'), "")
        self.durata = QDoubleSpinBox()
        self.durata.setRange(0, 1800)
        self.durata.setDecimals(1)
        self.durata.setSuffix(" s")
        self.durata.setSpecialValueText(_("Quanto dura l'azione"))
        self.durata.setValue(float(passo.durata))
        # Per un suono la domanda è **quante volte**, non per quanto: il nodo ripete il suono finché
        # il tempo scade, quindi «sparo per 8 secondi» sono tre o quattro spari. I secondi li
        # calcoliamo noi, e servono la lunghezza del suono — che il nodo dichiara dall'API 1.8.
        self._lunghezza = lunghezza_per(passo.comando, passo.suono, self._comandi)
        self.volte = QSpinBox()
        self.volte.setRange(1, 60)
        self.volte.setPrefix("× ")
        self.volte.setValue(passo.volte or quante_volte(passo.durata, self._lunghezza))
        self.quanto_dura = QLabel("")
        self.quanto_dura.setStyleSheet(f"font-size:{T.SIZE_SMALL}px; color:{T.MUTED};")
        self.volte.valueChanged.connect(lambda _v: self._quanto_fa())
        self.quale.currentIndexChanged.connect(lambda _i: self._suono_cambiato())
        self.ripeti = QSpinBox()
        self.ripeti.setRange(1, MAX_RIPETI)
        self.ripeti.setPrefix("× ")
        self.ripeti.setValue(int(passo.ripeti))
        self._a_volte = False
        # «Dove» sta in cima perché decide il resto: cambiando nodo cambiano i comandi, i suoni e
        # perfino la domanda (un nodo può avere una sirena generata e un altro un suono registrato
        # con lo stesso nome). Con un nodo solo la riga non compare: sarebbe una domanda con una
        # risposta sola.
        if len(self._dispositivi) > 1:
            f.addRow(_('Dove'), self.dove)
        if self._e_un_suono and self.uscita.count() > 1:
            f.addRow(_('Da quale cassa'), self.uscita)
        if self._e_un_suono and self._suoni_nodo:
            f.addRow(_('Suono'), self.quale)
            # La nota si scrive solo adesso che la porta esiste davvero: fino al 24/10 «Impostazioni →
            # Suoni» non c'era, e mandarci qualcuno sarebbe stato un giro a vuoto.
            dove = QLabel(_('I tuoi suoni si caricano in Impostazioni → Nodi → Suoni.'))
            dove.setStyleSheet(f"font-size:{T.SIZE_SMALL}px; color:{T.MUTED};")
            f.addRow("", dove)
        else:
            f.addRow(_('Comando sul nodo'), self.comando)
            f.addRow(_('Suono'), self.suono)
        # Tutt'e due le domande stanno nel modulo, e se ne vede **una sola**: quale, lo decide il suono
        # scelto *in questo momento*. Prima la scelta si faceva qui, costruendo la finestra, e non si
        # rifaceva più: aprendo un pezzo «sirena» e scegliendoci dentro uno sparo restavano i sessanta
        # secondi della sirena addosso a un colpo da nove decimi (23/10, trovato da Marco).
        f.addRow(_('Quante volte'), self.volte)
        f.addRow("", self.quanto_dura)
        f.addRow(_('Per quanto'), self.durata)
        f.addRow(_('Quante volte di fila'), self.ripeti)
        bottoni = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bottoni.accepted.connect(self._salva)
        bottoni.rejected.connect(self.reject)
        f.addRow(bottoni)
        self._modulo = f
        self.dove.currentIndexChanged.connect(lambda _i: self._dove_cambiato())
        self.comando.currentTextChanged.connect(lambda _t: self._comando_cambiato(passo.suono))
        self.suono.currentIndexChanged.connect(lambda _i: self._aggiorna_domanda())
        self._suoni_del_comando(passo.suono)
        self._aggiorna_domanda()

    def _chiave(self) -> str:
        """Cosa è stato scelto, in una stringa sola: ``suono:<comando>:<suono>``."""
        if self._e_un_suono and self._suoni_nodo:
            return str(self.quale.currentData() or "")
        nome = self.comando.currentText().strip()
        return f"suono:{nome}:{self.suono.currentData() or ''}" if nome else ""

    def _aggiorna_domanda(self) -> None:
        """Una domanda sola alla volta, e quella giusta per il suono scelto adesso."""
        comando, suono = comando_e_suono(self._chiave())
        self._lunghezza = lunghezza_per(comando, suono, self._comandi, self._dispositivo)
        self._a_volte = a_volte(comando, suono, self._comandi, self._dispositivo)
        for campo, visibile in ((self.volte, self._a_volte), (self.quanto_dura, self._a_volte),
                                (self.durata, not self._a_volte), (self.ripeti, not self._a_volte)):
            self._modulo.setRowVisible(campo, visibile)
        self._quanto_fa()
        self.adjustSize()

    def _rifai_uscite(self, scelta: str = "") -> None:
        """Le casse del dispositivo scelto adesso. La prima voce è «quella del nodo», cioè vuota.

        Un nodo prima dell'API 1.10 non le dichiara: resta la voce sola, la riga non si mostra e il
        pezzo non scrive niente — che è esattamente come suonava prima.
        """
        tenere = scelta or str(self.uscita.currentData() or "")
        self.uscita.blockSignals(True)
        self.uscita.clear()
        self.uscita.addItem(_('Quella configurata sul nodo'), "")
        for u in (self._uscite_per.get(self._dispositivo) or []):
            self.uscita.addItem(str(u.get("etichetta") or u.get("chiave")), str(u.get("chiave") or ""))
        self.uscita.setCurrentIndex(max(0, self.uscita.findData(tenere)))
        self.uscita.blockSignals(False)

    def _dove_cambiato(self) -> None:
        """Cambiando nodo cambia tutto quello che sta sotto: i comandi, i suoni e la domanda.

        Lo stesso nome può essere due cose diverse su due nodi — una sirena generata di qua, un suono
        registrato di là — quindi non basta ricordarsi la scelta: va rifatto l'elenco.
        """
        self._dispositivo = str(self.dove.currentData() or self._predefinito)
        self._comandi = comandi_di(self._tutti, self._dispositivo)
        # Le casse sono di quel nodo: cambiando nodo, la cassa scelta prima non esiste più.
        self._rifai_uscite()
        self._suoni_nodo = suoni_del_nodo(self._comandi, self._propri)
        scelto = str(self.quale.currentData() or "")
        self.quale.blockSignals(True)
        self.quale.clear()
        for v in self._suoni_nodo:
            self.quale.addItem(f"{v['icona']}  {v['nome']}", v["chiave"])
        self.quale.setCurrentIndex(max(0, self.quale.findData(scelto)))
        self.quale.blockSignals(False)
        self._suoni_del_comando(str(self.suono.currentData() or ""))
        self._aggiorna_domanda()

    def _comando_cambiato(self, suono: str = "") -> None:
        self._suoni_del_comando(suono)
        self._aggiorna_domanda()

    def _quanto_fa(self) -> None:
        """Sotto la domanda, la risposta in secondi: così non si scopre dopo quanto dura davvero."""
        secondi = secondi_per(self.volte.value(), self._lunghezza)
        self.quanto_dura.setText(_('fa {s} s in tutto', s=f"{secondi:.1f}".replace(".", ",")))

    def _suono_cambiato(self) -> None:
        """Cambiando suono cambia la domanda, non solo i secondi che vale un «× 3»."""
        self._aggiorna_domanda()

    def _suoni_del_comando(self, scelto: str = "") -> None:
        """I suoni li dichiara il comando: un comando che non suona non mostra una tendina vuota."""
        c = next((x for x in self._comandi if str(x.get("nome")) == self.comando.currentText()), None)
        suoni = [str(s) for s in ((c or {}).get("suoni") or [])]
        voluto = scelto or self.suono.currentData() or ""
        self.suono.blockSignals(True)
        self.suono.clear()
        self.suono.addItem(_('Quello del nodo (a rotazione)'), "")
        for s in suoni:
            self.suono.addItem(s, s)
        self.suono.setCurrentIndex(max(0, self.suono.findData(voluto)))
        self.suono.blockSignals(False)
        self.suono.setEnabled(bool(suoni))
        self.suono.setToolTip("" if suoni else _('Questo comando non ha suoni da scegliere'))

    def _salva(self) -> None:
        # Niente conti qui dentro: il pezzo lo costruisce «passo_da_scelte», che sta in un modulo
        # senza finestre e ha i suoi test — compresa la regola che «volte» e «ripeti» non vanno mai
        # accese insieme, che altrimenti tre spari diventano nove.
        passo = passo_da_scelte(self._chiave(), int(self.volte.value()), float(self.durata.value()),
                                int(self.ripeti.value()), float(self._prima.pausa), self._comandi,
                                dispositivo=self._dispositivo, predefinito=self._predefinito)
        if passo is None:
            return
        # La cassa vale solo per un pezzo che suona: scriverla su una luce sarebbe una riga che non
        # vuol dire niente, e il nodo la rifiuterebbe.
        passo.uscita = str(self.uscita.currentData() or "") if self._e_un_suono else ""
        # **Dove sta nella catena non si tocca.** Marco, 25/10: *«se lo modifico e salvo, nel flusso
        # ha perso "insieme" come collegamento e c'è scritto subito»*. Il pezzo esce da
        # `passo_da_scelte`, che lo costruisce da zero dalle caselle di questa finestra: tutto quello
        # che la finestra non chiede nasceva al valore di partenza, e per un pezzo dentro un gruppo
        # «al valore di partenza» vuol dire **fuori dal gruppo**. Non è una svista di un campo: è
        # tutta la riga di campi che questa finestra non riguarda, e si ricopiano qui tutti insieme.
        passo.nel_gruppo = self._prima.nel_gruppo
        passo.a_turno = self._prima.a_turno
        # L'attesa non si chiede più qui (25/10). Marco: *«"poi aspetta" non serve a nulla: l'azione
        # successiva può essere l'attesa, e si crea un doppio ritardo»*. Aveva due posti — questa
        # casella e la freccia fra i riquadri — e due posti per lo stesso numero vogliono dire che
        # prima o poi li metti tutt'e due. Resta la freccia, che è dove l'attesa **si vede**. Il
        # valore di prima si riporta: toglierle la casella non deve azzerare le attese già scritte.
        self.risultato = passo
        self.accept()


class StrisciaDiPassi(QWidget):
    """La catena intera: i riquadri, le frecce, il «+», «ripeti tutto», il riassunto e la prova."""

    cambiata = Signal()
    prova_chiesta = Signal()
    prova_un_pezzo = Signal(int)   # l'indice del pezzo da far sentire da solo
    # Due «ferma» diversi, e non è pignoleria: un pezzo solo non ha una coda dietro, quindi basta
    # spegnere l'audio; la catena intera ha i passi dopo già programmati, e fermarla vuol dire anche
    # disfare la coda (Marco, 24/10: «se premi solo play nel blocco basta fermare l'audio; se premi
    # il play del flusso devi bloccare il flusso»).
    ferma_pezzo = Signal(int)
    ferma_catena = Signal()
    scorciatoia = Signal(str)     # "audio_del_nodo": porta dove si aggiusta quello che manca

    STILE = f"""
    QFrame#passo {{ background: rgba(255,255,255,0.035); border: 1px solid {T.LINE};
                    border-radius: 12px; }}
    QFrame#passo:hover {{ border: 1px solid {T.INFO}; }}
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(self.STILE)
        self._passi: list[Passo] = []
        self._comandi: list[dict] = []
        self._dispositivi: list = []
        self._dispositivo = ""
        self._nomi: dict = {}
        self._casse: list = []
        # Le uscite audio di ogni dispositivo, dal nodo (GET /dispositivi): {"nodo:n1": [{chiave,
        # etichetta}]}. Senza, il «+» offre «la cassa del nodo» — un nodo prima dell'API 1.10 ha
        # un'uscita sola e non sa dire quale, e sparire sarebbe peggio: quel nodo suona benissimo.
        self._uscite_per: dict = {}
        # Qual è la cassa **configurata** di ogni dispositivo: {"nodo:n1": "usb:UACDemoV10"}. Serve
        # per sapere se un pezzo senza uscita («quella configurata») e uno che nomina una cassa sono
        # la stessa linea. Vuoto vuol dire «non lo so», e allora non si accusa nessuno.
        self._uscita_in_uso: dict = {}
        self._suoni_propri: list | None = None   # i suoni che fa il nodo: None = non lo sappiamo
        self._in_linea: bool | None = None
        self._avvisa = False
        self._durata_azione = 0.0
        # Verticalmente **fissa**: in una QFormLayout un widget che può crescere cresce, e una striscia
        # alta mezza pagina per far vedere un pulsante è esattamente quello che è successo la prima
        # sera. Larga sì: i riquadri scorrono in orizzontale.
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self._rotolo = QScrollArea()
        self._rotolo.setWidgetResizable(True)
        self._rotolo.setFrameShape(QFrame.Shape.NoFrame)
        self._rotolo.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._rotolo.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._rotolo.setFixedHeight(_Riquadro.ALTO + 14)
        self._dentro = QWidget()
        self._riga = QHBoxLayout(self._dentro)
        self._riga.setContentsMargins(2, 2, 2, 2)
        self._riga.setSpacing(6)
        self._rotolo.setWidget(self._dentro)
        self._crea_piu()
        lay.addWidget(self._rotolo)
        sotto = QHBoxLayout()
        sotto.setSpacing(10)
        self.ripeti_tutto = QSpinBox()
        minimo, massimo = limiti_ripetizione()
        self.ripeti_tutto.setRange(minimo, massimo)
        self.ripeti_tutto.setPrefix(_('ripeti tutto × '))
        self.ripeti_tutto.setToolTip(_('Quante volte si ripete la striscia intera, dall\'inizio'))
        self.ripeti_tutto.setMaximumWidth(170)
        self.ripeti_tutto.valueChanged.connect(lambda _v: self._ridisegna())
        sotto.addWidget(self.ripeti_tutto)
        self.conto = QLabel("")
        self.conto.setStyleSheet(f"font-size:{T.SIZE_SMALL}px; color:{T.MUTED};")
        sotto.addWidget(self.conto, 1)
        self._in_prova: int | None = None      # indice del pezzo che sta suonando
        self._prova_tutta = False
        self.prova = QPushButton(_('Prova tutta la catena'))
        self.prova.setToolTip(_('Esegue tutti i pezzi in fila, sul nodo: la senti prima di salvarla'))
        self.prova.setMinimumHeight(34)
        self.prova.clicked.connect(self._prova_o_ferma)
        sotto.addWidget(self.prova)
        lay.addLayout(sotto)
        self.consigli = QLabel("")
        self.consigli.setWordWrap(True)
        self.consigli.setStyleSheet(f"font-size:{T.SIZE_SMALL}px; color:{T.FAINT};")
        lay.addWidget(self.consigli)

    # --- quello che sa dal mondo attorno ------------------------------------
    def set_contesto(self, comandi: list[dict], casse: list | None = None,
                     nodo_in_linea: bool | None = None, avvisa: bool = False,
                     durata_azione: float = 0.0, dispositivi: list | None = None,
                     dispositivo: str = "", suoni_propri: list | None = None,
                     uscite_per: dict | None = None, uscita_in_uso: dict | None = None) -> None:
        self._comandi = list(comandi or [])
        self._dispositivi = list(dispositivi or [])
        self._dispositivo = str(dispositivo or "")
        self._nomi = {str(d.get("chiave")): str(d.get("nome") or d.get("chiave"))
                      for d in self._dispositivi}
        self._casse = list(casse or [])
        # Quali suoni il nodo fa da sé: serve solo a dare l'icona giusta — l'altoparlante a quelli
        # suoi, la nota a un file caricato. Finché non lo sappiamo resta None, e si mostra
        # l'altoparlante: dire «è tuo» di un suono che non lo è sarebbe peggio che non dire niente.
        if suoni_propri is not None:
            self._suoni_propri = list(suoni_propri)
        if uscite_per is not None:
            self._uscite_per = dict(uscite_per)
        if uscita_in_uso is not None:
            self._uscita_in_uso = dict(uscita_in_uso)
        self._in_linea = nodo_in_linea
        self._avvisa = bool(avvisa)
        self._durata_azione = float(durata_azione or 0)
        self._ridisegna()

    def set_passi(self, passi) -> None:
        self._passi = [p if isinstance(p, Passo) else Passo.da_dict(p) for p in (passi or [])]
        self._ridisegna()

    def passi(self) -> list[Passo]:
        return list(self._passi)

    def passi_salvabili(self) -> list[dict]:
        """Come vanno scritti in configurazione: solo quello che è stato scelto davvero."""
        return [p.come_dict() for p in self._passi]

    # --- il disegno ----------------------------------------------------------
    def _svuota(self) -> None:
        """Toglie i riquadri, e li fa cancellare **a Qt**, non alla raccolta rifiuti di Python.

        Era «setParent(None)» e basta: il widget restava vivo in mano a Python, e chi lo possedeva
        davvero — Qt — se lo ritrovava sotto i piedi alla chiusura del programma. Sul Mac di Marco è
        finita in un segmentation fault dopo che la suite aveva già detto OK, ed è il genere di
        errore che non si vede mai in una prova singola: si vede dopo ottocento.
        """
        while self._riga.count():
            voce = self._riga.takeAt(0)
            w = voce.widget()
            if w is None or w is self._piu_btn:
                continue              # il «+» è sempre lo stesso: si rimette, non si ricrea
            w.setParent(None)
            w.deleteLater()

    def _ridisegna(self) -> None:
        self._svuota()
        dati = riquadri(self._passi, self._comandi, dispositivo=self._dispositivo, nomi=self._nomi,
                        in_uso=self._uscita_in_uso)
        for i, d in enumerate(dati):
            dove = self._passi[i].dispositivo or self._dispositivo
            suona = vuole_una_cassa(_tipo_di(self._passi[i].comando, self._comandi, dove))
            box = _Riquadro(d, primo=(i == 0), ultimo=(i == len(dati) - 1),
                            provabile=bool(suona and self._in_linea), parent=self._dentro,
                            in_prova=(self._in_prova == i))
            box.prova.connect(lambda _c=False, k=i: self._pezzo_o_ferma(k))
            box.modifica.connect(lambda _c=False, k=i: self._cambia(k))
            box.togli.connect(lambda _c=False, k=i: self._togli(k))
            box.sposta.connect(lambda verso, k=i: self._sposta(k, verso))
            box.insieme.connect(lambda _c=False, k=i: self._chiedi_insieme(k))
            self._riga.addWidget(box)
            if d.freccia:
                self._riga.addWidget(self._freccia(i, d.freccia, d.unito))
        self._aggiorna_piu()
        self._riga.addWidget(self._piu_btn)
        self._riga.addStretch(1)
        self.conto.setText(riassunto(self._passi, self._durata_azione, self.ripeti_tutto.value()))
        avvisi = suggerimenti(self._passi, self._comandi, self._avvisa, self._casse)
        self.consigli.setText("  ·  ".join(avvisi))
        self.consigli.setVisible(bool(avvisi))
        manca = cosa_manca_per_provare(self._passi, self._in_linea)
        self.prova.setEnabled(not manca)
        if manca:
            self.prova.setToolTip(manca)
        self.cambiata.emit()

    def _prova_o_ferma(self) -> None:
        if self._prova_tutta:
            self.ferma_catena.emit()
            self.fine_prova()
        else:
            self.prova_chiesta.emit()

    def _pezzo_o_ferma(self, i: int) -> None:
        if self._in_prova == i:
            self.ferma_pezzo.emit(i)
            self.fine_prova()
        else:
            self.prova_un_pezzo.emit(i)

    def in_prova(self, secondi: float, indice: int | None = None) -> None:
        """Qualcosa sta suonando: i pulsanti diventano «ferma» finché non finisce.

        I secondi li conta la striscia, non il nodo: con «quante volte» il nodo suona il **file
        intero**, che può essere molto più lungo dei cinque secondi che la prova gli chiede — ed è il
        motivo per cui un brano da mezzo minuto sembrava non finire mai (24/10).
        """
        from PySide6.QtCore import QTimer  # noqa: PLC0415

        self._prova_tutta = indice is None
        self._in_prova = indice
        self._aggiorna_prova()
        self._ridisegna()
        QTimer.singleShot(int(max(1.0, secondi) * 1000) + 300, self._fine_se_e_ancora_lei)

    def fine_prova(self) -> None:
        self._prova_tutta = False
        self._in_prova = None
        self._aggiorna_prova()
        self._ridisegna()

    def _fine_se_e_ancora_lei(self) -> None:
        try:
            self.fine_prova()
        except RuntimeError:
            pass        # finestra chiusa mentre suonava

    def _aggiorna_prova(self) -> None:
        in_corso = self._prova_tutta or self._in_prova is not None
        self.prova.setText(_('Ferma') if in_corso else _('Prova tutta la catena'))

    def secondi_del_pezzo(self, i: int) -> float:
        """Quanto suonerà davvero questo pezzo: con «quante volte» lo decide la lunghezza del suono."""
        if not (0 <= i < len(self._passi)):
            return 0.0
        p = self._passi[i]
        if p.volte:
            dove = p.dispositivo or self._dispositivo
            return secondi_per(p.volte, lunghezza_per(p.comando, p.suono, self._comandi, dove))
        return float(p.durata or 0)

    def _freccia(self, i: int, testo: str, unito: bool = False) -> QToolButton:
        b = QToolButton()
        b.setAutoRaise(True)
        b.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        if unito:
            # Fra due pezzi dello stesso gruppo non c'è un'attesa da cambiare: c'è una graffa. Il
            # segno è diverso perché la cosa è diversa — un'attesa si può accorciare, questa no.
            #
            # Ma **si clicca**, e dal 25/10 è l'unico posto da cui si cambia il modo del gruppo. Il
            # piano diceva di chiederlo nel «+», come primo dei tre passi: l'ho messo qui perché
            # questa graffa è già la cosa che dice come i due pezzi stanno insieme, e si cambia
            # quello che si legge, dov'è scritto. Chiederlo nel «+» voleva dire un menu di quattro
            # livelli e una domanda fatta prima di sapere cosa si sta aggiungendo.
            b.setText(f"⧓  {testo}")
            b.setToolTip(_('Come parte questo gruppo: clicca per scegliere fra «insieme» e «a turno»'))
            b.clicked.connect(lambda _c=False, k=i: self._chiedi_modo(k))
            return b
        b.setText(f"──  {testo}  ▶")
        b.setToolTip(_('Quanto aspettare prima del pezzo dopo'))
        b.clicked.connect(lambda _c=False, k=i: self._cambia_attesa(k))
        return b

    def _crea_piu(self) -> None:
        """Il «+» e il suo menu, creati **una volta sola**.

        Prima nascevano e morivano a ogni ridisegno — venti volte in una prova sola — e un QToolButton
        con un QMenu attaccato che viene creato e distrutto in continuazione è un modo collaudato di
        far crollare Qt quando chiude. Qui cambia solo il contenuto: il pulsante è sempre lo stesso.
        """
        from PySide6.QtWidgets import QMenu  # noqa: PLC0415

        self._piu_btn = QToolButton()
        self._piu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._piu_btn.setStyleSheet(f"""QToolButton {{ border: 1px dashed {T.LINE}; border-radius: 12px;
                                                       color: {T.MUTED}; font-size: {T.SIZE_BODY}px; }}
                                        QToolButton:hover {{ border: 1px dashed {T.INFO}; color: {T.TEXT}; }}
                                        QToolButton::menu-indicator {{ image: none; }}""")
        self._piu_menu = QMenu(self._piu_btn)
        self._piu_btn.setMenu(self._piu_menu)

    def _aggiorna_piu(self) -> None:
        """Cosa propone il «+» adesso: cambia l'elenco, non il pulsante."""
        # Un riquadro tratteggiato come gli altri, non una sbarra stretta: il «+» è il posto da cui si
        # comincia, e la prima sera sembrava un pezzo di barra di scorrimento.
        self._piu_btn.setText("＋\n" + (_('Aggiungi un pezzo') if self._passi else _('Comincia da qui')))
        self._piu_btn.setFixedSize(110 if self._passi else _Riquadro.LARGO, _Riquadro.ALTO)
        self._piu_menu.clear()
        puo = si_puo_aggiungere(self._passi)
        self._piu_btn.setEnabled(puo)
        if not puo:
            self._piu_btn.setToolTip(_('La catena è già lunga: più di così diventa difficile da leggere.'))
            return
        self._piu_btn.setToolTip(_('Aggiungi un pezzo'))
        # L'attesa non è di nessun dispositivo: è la freccia fra due riquadri, e sta in cima.
        if self._passi:
            attesa = self._piu_menu.addAction(f"{EMOJI[ATTESA]}  " + _('Aspetta'))
            attesa.setToolTip(_("Lascia passare qualche secondo prima del passo dopo"))
            attesa.triggered.connect(lambda _c=False: self._aggiungi(ATTESA))
            self._piu_menu.addSeparator()
        self._riempi_menu(self._piu_menu)

    def _riempi_menu(self, menu, dentro: int | None = None) -> None:
        """L'albero del «+»: **dove**, con **cosa**, **cosa fargli fare** (24/10).

        ``dentro`` è l'indice del pezzo a cui la cosa nuova va attaccata **insieme**; senza, si
        aggiunge in fondo alla fila. È lo stesso albero per tutt'e due, e deve restarlo: due menu che
        offrono cose diverse sarebbero due programmi diversi nella stessa finestra.
        """
        # Con un dispositivo solo il primo livello non si apre: non si fa una domanda che ha una
        # risposta sola, e si parte dai suoi componenti.
        dispositivi = self._dispositivi or [{"chiave": self._dispositivo, "nome": _('Questo nodo')}]
        for d in dispositivi:
            dove = str(d.get("chiave") or "")
            comandi = comandi_di(self._comandi, dove)
            # Le casse di **questo** nodo le sappiamo; di un altro, no — e «non lo so» non è «non ne
            # ha»: dire a un nodo che non ha casse quando nessuno gliel'ha chiesto sarebbe una bugia.
            pezzi = componenti_di(comandi, self._uscite_per.get(dove),
                                  casse=self._casse if dove == self._dispositivo else None)
            if not pezzi:
                continue
            sotto = menu if len(dispositivi) == 1 else menu.addMenu(str(d.get("nome") or dove))
            for comp in pezzi:
                self._voci_del_componente(sotto, comp, comandi, dove, dentro)

    def _voci_del_componente(self, dove_menu, comp, comandi: list[dict], dispositivo: str,
                             dentro: int | None = None) -> None:
        """Le cose che un componente sa fare. Con una sola, niente sotto-menu: si mette lì."""
        if comp.manca:
            # Spenta mai: resta cliccabile e porta dove si aggiusta. Una voce spenta dice «non
            # puoi»; questa dice «ti serve una cassa, vieni che te la configuro». E sta **qui**, in
            # vista, non dentro un sotto-menu che nessuno apre.
            voce = dove_menu.addAction(_('{testo} — {manca}', testo=f"{comp.icona}  {comp.testo}",
                                         manca=comp.manca))
            voce.setToolTip(comp.manca)
            voce.triggered.connect(lambda _c=False, s=comp.scorciatoia: self.scorciatoia.emit(s))
            return
        cose = cose_da_fare(comp, comandi, self._suoni_propri, dispositivo=dispositivo)
        if not cose:
            return
        # Non si chiama «dentro»: quel nome è già il parametro che dice **a quale pezzo** attaccare
        # la cosa nuova. Chiamarlo così lo sostituiva, e il pezzo da attaccare diventava un menu —
        # `TypeError: unsupported operand type(s) for +: 'QMenu' and 'int'`, trovato da Marco al
        # primo clic sul «+» di un riquadro (24/10).
        sotto_menu = dove_menu if len(cose) == 1 else dove_menu.addMenu(f"{comp.icona}  {comp.testo}")
        for p in cose:
            testo = f"{p.icona}  {p.testo}" if p.icona else p.testo
            if len(cose) == 1:
                testo = f"{comp.icona}  {comp.testo}"
            voce = sotto_menu.addAction(testo)
            voce.setToolTip(p.avviso or p.spiega)
            voce.triggered.connect(lambda _c=False, k=p.chiave, d=dentro: self._aggiungi(k, d))

    # --- le mosse ------------------------------------------------------------
    def _chiedi_insieme(self, i: int) -> None:
        """Il «+» di un riquadro: la stessa scelta del «+» della striscia, ma attaccata a questo."""
        from PySide6.QtGui import QCursor  # noqa: PLC0415
        from PySide6.QtWidgets import QMenu  # noqa: PLC0415

        if not (0 <= i < len(self._passi)) or not si_puo_aggiungere(self._passi):
            return
        # Il menu si tiene da parte finché è aperto: un QMenu che non ha nessuno che lo regge può
        # sparire da sotto le dita mentre lo si guarda.
        self._menu_insieme = QMenu(self)
        self._riempi_menu(self._menu_insieme, dentro=i)
        self._menu_insieme.exec(QCursor.pos())

    def _chiedi_modo(self, i: int) -> None:
        """Come parte il gruppo del pezzo ``i+1``: insieme, o a turno.

        Si sceglie da un menu di due voci con scritto cosa vogliono dire, e non da un interruttore
        che mostra una parola per volta: una parola sola si legge come un'etichetta, e nessuno
        scopre che ce n'era un'altra.
        """
        from PySide6.QtGui import QCursor  # noqa: PLC0415
        from PySide6.QtWidgets import QMenu  # noqa: PLC0415

        capo = capogruppo(self._passi, i + 1)
        if capo is None:
            return
        self._menu_modo = QMenu(self)
        for modo, testo, spiega in (
                (INSIEME, _('insieme'), _('Partono tutti nello stesso istante')),
                (A_TURNO, _('a turno'), _('A ogni scatto ne parte uno solo, la volta dopo il prossimo'))):
            voce = self._menu_modo.addAction(testo)
            voce.setToolTip(spiega)
            voce.setCheckable(True)
            voce.setChecked((modo == A_TURNO) == bool(self._passi[capo].a_turno))
            voce.triggered.connect(lambda _c=False, m=modo, k=capo: self._metti_modo(k, m))
        self._menu_modo.exec(QCursor.pos())

    def _metti_modo(self, capo: int, modo: str) -> None:
        if 0 <= capo < len(self._passi):
            self._passi[capo].a_turno = (modo == A_TURNO)
            self._ridisegna()

    def _aggiungi(self, chiave: str, dentro: int | None = None) -> None:
        if chiave == ATTESA:
            if self._passi:
                self._passi[-1].pausa = self._passi[-1].pausa or PAUSA_PROPOSTA
                self._ridisegna()
            return
        passo = passo_da_pezzo(chiave, comandi_di(self._comandi, pezzo_da_chiave(chiave)[0]) or self._comandi)
        if passo is None:
            passo = passo_proposto(chiave, self._comandi)
        if passo is None:
            return
        if passo.dispositivo == self._dispositivo:
            passo.dispositivo = ""      # «quello dell'azione» non si scrive: resta una riga corta
        if dentro is None:
            self._passi.append(passo)
        else:
            # Attaccato a quello: entra nel suo gruppo, e si infila **subito dopo** di lui — non in
            # fondo alla fila, che vorrebbe dire nel gruppo di un altro pezzo. Come parte il gruppo
            # non lo decide questo: lo dice il capogruppo, e di suo è «insieme».
            passo.nel_gruppo = True
            self._passi.insert(min(dentro + 1, len(self._passi)), passo)
        self._ridisegna()

    def _togli(self, i: int) -> None:
        if 0 <= i < len(self._passi):
            del self._passi[i]
            self._ridisegna()

    def _sposta(self, i: int, verso: int) -> None:
        self._passi = riordina(self._passi, i, i + int(verso))
        self._ridisegna()

    def _cambia(self, i: int) -> None:
        if not (0 <= i < len(self._passi)):
            return
        dlg = PassoDialog(self._passi[i], self._comandi, self,
                          dispositivi=self._dispositivi, dispositivo=self._dispositivo,
                          suoni_propri=self._suoni_propri, uscite_per=self._uscite_per)
        if dlg.exec() and dlg.risultato is not None:
            self._passi[i] = dlg.risultato
            self._ridisegna()

    def _cambia_attesa(self, i: int) -> None:
        if not (0 <= i < len(self._passi)):
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(_('Attesa'))
        f = QFormLayout(dlg)
        quanto = QDoubleSpinBox()
        quanto.setRange(0, MAX_PAUSA)
        quanto.setDecimals(1)
        quanto.setSuffix(" s")
        quanto.setSpecialValueText(_('Subito'))
        quanto.setValue(float(self._passi[i].pausa))
        f.addRow(_('Aspetta'), quanto)
        bottoni = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bottoni.accepted.connect(dlg.accept)
        bottoni.rejected.connect(dlg.reject)
        f.addRow(bottoni)
        if dlg.exec():
            self._passi[i].pausa = float(quanto.value())
            self._ridisegna()
