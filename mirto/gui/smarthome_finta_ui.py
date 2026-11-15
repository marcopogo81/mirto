# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""La finestra della casa smart finta: aggiungi dispositivi, girali, e guarda i due versi insieme.

**A cosa serve davvero.** Il simulatore del nodo non ha una finestra e non ne ha bisogno: di un nodo
si prova il contratto, e il contratto si legge nelle prove. Di un impianto domotico invece serve
**vedere**, perché le due cose che devono funzionare sono due movimenti opposti e nessuno dei due si
capisce leggendo un file di registro:

* **giro l'interruttore qui, e in Mirto parte un'azione** — è il verso dei trigger;
* **Mirto manda un comando, e qui la lampadina si accende** — è il verso delle uscite.

Con questa finestra aperta accanto a Mirto si vedono entrambi nello stesso istante.

**Tre difetti della prima stesura, il 14/11, e le regole che ne restano.** La finestra era chiara con
una scatola blu in cima e un campo ridotto a tre puntini. Tutti e tre erano miei:

1. **Vestire l'applicazione non è facoltativo.** Il tema di Mirto vive su ``QApplication``: palette,
   foglio di stile, icona. Aprendo una ``QApplication`` nuda si ottiene una finestra di sistema con
   dentro i nostri colori scuri, che è il peggio dei due mondi. Ora c'è ``vesti_come_mirto()``.
2. **``QLabel`` è una sottoclasse di ``QFrame``.** Un foglio di stile ``QFrame {…}`` si applica
   quindi a *ogni etichetta dentro*, e ognuna si ritrova fondo e bordo del pannello. Si sceglie per
   nome — ``QFrame#testa {…}`` — come fa già il riquadro «Nuovo nodo trovato» in ``app.py``.
3. **Un menu lungo schiaccia i vicini.** La voce «esphome — si annuncia da sé (…)» si prendeva tutta
   la riga e il campo del nome spariva: non ci si poteva scrivere, l'aggiunta si rifiutava in
   silenzio, e sembrava che «Aggiungi» fosse rotto. Nel menu ora c'è solo la marca, e cosa comporta
   si legge in una riga sotto, che cambia con la scelta. Una prova guarda che quel campo resti largo.

**I testi passano dal traduttore, come tutto il resto.** Il 14/11 li avevo lasciati fuori dicendo
che è uno strumento di sviluppo e lo vediamo solo noi. Marco, il giorno dopo: *«se voglio avere
collaboratori i simulatori devono essere pubblici ed in lingua»*. Ha ragione, e il patto era già
scritto nel ``CONTRIBUTING``: *«the project is documented in English so that anyone can read it and
contribute»*. Un simulatore è la prima cosa che apre chi vuole dare una mano e non ha un impianto
in casa — cioè quasi tutti — e aprirgli davanti una finestra in una lingua che non legge è dirgli
che il progetto non è per lui.

**Il filo.** I cambiamenti arrivano dal filo che legge dal broker, non da quello dell'interfaccia:
passano da un segnale Qt, che è il modo in cui in Mirto si rimbalza il lavoro sul filo giusto.
"""

from __future__ import annotations

import logging
import sys
import time

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import theme as T
from .widgets import mono_font
from ..i18n import _
from ..actions.smarthome_finta import (
    ESPHOME,
    LUCE,
    MALEDUCATO,
    MARCHE,
    PRESA,
    SENSORE,
    TIPI,
    ZIGBEE,
    CasaFinta,
    DispositivoFinto,
    accendi,
)

log = logging.getLogger(__name__)

def descrizione_marca(marca: str) -> str:
    """Cosa comporta quella marca, tradotto. Le frasi stanno qui e non in «smarthome_finta»:
    devono stare dentro «_()» al momento di mostrarle, altrimenti il traduttore non le vede e una
    tabella costruita all'import resterebbe nella lingua di quando e' partito il programma."""
    return {
        ESPHOME: _("si annuncia da sé: Mirto lo trova subito"),
        ZIGBEE: _("non si annuncia, ma parla la sua lingua: Mirto lo trova dal suo elenco"),
        MALEDUCATO: _("non si annuncia e non parla nessuna lingua nota: va cercato a orecchio"),
    }.get(marca, "")


def cosa_deve_fare_mirto(marca: str) -> str:
    """Cosa Mirto **deve fare adesso** con un dispositivo di quella marca, per chi sta provando.

    Non è `descrizione_marca`, e il 15/11 le ho unificate sbagliando. Quella dice *cos'è* la marca,
    e sta accanto alla tendina mentre scegli. Questa dice *cosa guardare adesso che l'hai aggiunto*,
    e va nel diario: chi prova e non lo sa resta a chiedersi perché Mirto non lo trova. Si somigliano
    e servono a due momenti diversi, e tenerne una sola vuol dire perdere quella che aiuta.
    """
    return {
        ESPHOME: _("Si annuncia: Mirto lo deve trovare da solo."),
        ZIGBEE: _("Non si annuncia, ma sta nell'elenco della sua marca: Mirto lo deve trovare da lì."),
    }.get(marca, _("Maleducato: non si annuncia e non parla nessuna lingua nota, "
                   "Mirto lo trova solo con l'ascolto grezzo."))


def etichetta_tipo(tipo: str) -> str:
    """Il tipo in parole. Si traduce qui e non in una tabella: una tabella di stringhe fatta
    all'import resterebbe nella lingua di quando e' partito il programma."""
    return {LUCE: _("Luce"), PRESA: _("Presa"), SENSORE: _("Sensore")}.get(tipo, tipo)

# Il campo del nome non deve mai scendere sotto questa larghezza: è il difetto 3 qui sopra, e la
# prova che lo guarda usa lo stesso numero. Sotto i duecento pixel non ci si scrive un nome.
LARGHEZZA_MINIMA_NOME = 220


def vesti_come_mirto(app) -> None:
    """Mette addosso all'applicazione il tema di Mirto: palette, foglio di stile, icona.

    Lo fa già ``run_gui``, ma una finestra che si apre da sola — questa, e domani qualunque altro
    strumento — parte da una ``QApplication`` nuda. Senza questo passo si ottiene una finestra
    chiara di sistema con dentro i nostri colori scuri: il peggio dei due mondi.
    """
    app.setApplicationName("Mirto")
    icona = T.ICONS / "app" / ("app.png" if sys.platform == "darwin" else "app_256.png")
    if icona.exists():
        app.setWindowIcon(QIcon(str(icona)))
    app.setStyle("Fusion")
    app.setPalette(T.palette())
    app.setStyleSheet(T.STYLE)


class _Ponte(QObject):
    """Porta i cambiamenti dal filo del broker a quello dell'interfaccia."""

    cambiato = Signal(str, str)      # identificativo, da chi (finestra | comando)


class FinestraCasaFinta(QDialog):
    """La casa finta, da guardare e da toccare.

    È una ``QDialog`` e non una ``QWidget`` per una ragione sola: il foglio di stile di Mirto dà il
    fondo scuro a ``QMainWindow`` e ``QDialog``. Una ``QWidget`` nuda resta chiara, e dentro ci si
    ritrovano pannelli scuri su fondo di sistema.
    """

    def __init__(self, casa: CasaFinta, parent=None) -> None:
        super().__init__(parent)
        self.casa = casa
        self._ponte = _Ponte()
        self._ponte.cambiato.connect(self._segna)
        self.casa.su_cambio = lambda d, da_chi: self._ponte.cambiato.emit(d.identificativo, da_chi)
        self._righe: dict[str, tuple[QLabel, QPushButton]] = {}

        self.setWindowTitle(_("Casa smart finta — Mirto"))
        self.setMinimumWidth(780)
        fuori = QVBoxLayout(self)
        fuori.setSpacing(14)
        fuori.setContentsMargins(18, 18, 18, 18)
        fuori.addWidget(self._testa())
        fuori.addWidget(self._titolo("I DISPOSITIVI DI CASA"))
        fuori.addWidget(self._tabella())
        fuori.addWidget(self._titolo("AGGIUNGINE UN ALTRO"))
        fuori.addWidget(self._aggiunta())
        fuori.addWidget(self._titolo("COSA SUCCEDE"))
        self.diario = QPlainTextEdit()
        self.diario.setObjectName("console")
        self.diario.setReadOnly(True)
        self.diario.setFont(mono_font(12))
        self.diario.setMinimumHeight(150)
        fuori.addWidget(self.diario)
        self.resize(820, 700)
        self._ridisegna()
        self._scrivi(_("Casa finta in piedi. Broker su 127.0.0.1:{porta}.", porta=self.casa.porta))

    # --- i pezzi della finestra ----------------------------------------------
    @staticmethod
    def _titolo(testo: str) -> QLabel:
        riga = QLabel(testo)
        riga.setObjectName("sectionTitle")
        riga.setStyleSheet(f"color:{T.MUTED}; font-size:{T.SIZE_SMALL}px; font-weight:600; "
                           "letter-spacing:1px;")
        return riga

    def _testa(self) -> QWidget:
        """Il logo e l'indirizzo del broker: la sola cosa da copiare in Mirto, grossa e selezionabile.

        Lo stile si sceglie **per nome** (``QFrame#testa``), se no lo prende ogni ``QLabel`` qui
        dentro, perché ``QLabel`` è una ``QFrame``. Niente bordo: il tema dice che le aree si
        distinguono per fondo e spazio, non per riquadri annidati.
        """
        scatola = QFrame()
        scatola.setObjectName("testa")
        scatola.setStyleSheet(f"QFrame#testa {{ background:{T.PANEL}; border-radius:14px; }}")
        riga = QHBoxLayout(scatola)
        riga.setContentsMargins(16, 14, 16, 14)
        riga.setSpacing(14)
        logo = QLabel()
        marchio = T.ICONS / "app" / "app.png"
        if marchio.exists():
            logo.setPixmap(QIcon(str(marchio)).pixmap(44, 44))
        logo.setFixedSize(44, 44)
        logo.setAlignment(Qt.AlignmentFlag.AlignTop)
        riga.addWidget(logo)
        dentro = QVBoxLayout()
        dentro.setSpacing(4)
        titolo = QLabel(_("Casa smart finta"))
        titolo.setStyleSheet(f"color:{T.TEXT}; font-size:{T.SIZE_TITLE}px; font-weight:700;")
        dentro.addWidget(titolo)
        self.indirizzo = QLabel(f"127.0.0.1:{self.casa.porta}")
        self.indirizzo.setFont(mono_font(16))
        self.indirizzo.setStyleSheet(f"color:{T.INFO}; font-weight:700;")
        self.indirizzo.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        dentro.addWidget(self.indirizzo)
        # Non mandare a cercare una cosa che non c'è ancora: la scheda SmartHome arriva col passo 1,
        # e fino ad allora una riga che dice «vai in Impostazioni» fa perdere tempo a chi prova.
        nota = QLabel(_("È l'indirizzo del broker da scrivere in Mirto, in <i>Impostazioni → "
                        "SmartHome</i>.<br>Il broker sta dentro questa finestra: chiudendola, i "
                        "dispositivi spariscono."))
        nota.setTextFormat(Qt.TextFormat.RichText)
        nota.setWordWrap(True)
        nota.setStyleSheet(f"color:{T.MUTED}; font-size:{T.SIZE_SMALL}px;")
        dentro.addWidget(nota)
        riga.addLayout(dentro, 1)
        return scatola

    def _tabella(self) -> QWidget:
        scatola = QWidget()
        self.griglia = QGridLayout(scatola)
        self.griglia.setContentsMargins(4, 0, 4, 0)
        self.griglia.setHorizontalSpacing(14)
        self.griglia.setVerticalSpacing(8)
        self.griglia.setColumnStretch(0, 1)
        return scatola

    def _aggiunta(self) -> QWidget:
        """La riga per aggiungere, e sotto una riga che spiega la marca scelta.

        La spiegazione sta **fuori** dal menu: dentro rubava tutta la larghezza e il campo del nome
        spariva. Fuori si legge meglio e cambia sotto gli occhi quando cambi marca.
        """
        scatola = QWidget()
        colonna = QVBoxLayout(scatola)
        colonna.setContentsMargins(4, 0, 4, 0)
        colonna.setSpacing(6)
        riga = QHBoxLayout()
        riga.setSpacing(8)
        self.nome = QLineEdit()
        self.nome.setPlaceholderText(_("Nome del dispositivo, per esempio «Luce del portico»"))
        self.nome.setMinimumWidth(LARGHEZZA_MINIMA_NOME)
        self.nome.returnPressed.connect(self._aggiungi)
        riga.addWidget(self.nome, 1)
        self.tipo = QComboBox()
        for t in TIPI:
            self.tipo.addItem(etichetta_tipo(t), t)
        self.tipo.setFixedWidth(130)
        riga.addWidget(self.tipo)
        self.marca = QComboBox()
        for m in MARCHE:
            self.marca.addItem(m, m)   # il nome di una marca non si traduce
        self.marca.setFixedWidth(170)
        self.marca.currentIndexChanged.connect(self._spiega_la_marca)
        riga.addWidget(self.marca)
        metti = QPushButton(_("Aggiungi"))
        metti.setObjectName("primario")
        metti.clicked.connect(self._aggiungi)
        riga.addWidget(metti)
        colonna.addLayout(riga)
        self.spiegazione = QLabel()
        self.spiegazione.setWordWrap(True)
        self.spiegazione.setStyleSheet(f"color:{T.FAINT}; font-size:{T.SIZE_SMALL}px;")
        colonna.addWidget(self.spiegazione)
        self._spiega_la_marca()
        return scatola

    def _spiega_la_marca(self, _indice: int = 0) -> None:
        # l'argomento c'è perché «currentIndexChanged» manda l'indice: un metodo che non lo accetta
        # solleva, e la riga di spiegazione resterebbe ferma sulla marca di prima
        marca = self.marca.currentData()
        self.spiegazione.setText(f"{marca}: {descrizione_marca(marca)}")
        self.spiegazione.setStyleSheet(f"color:{T.FAINT}; font-size:{T.SIZE_SMALL}px;")

    # --- disegnare ------------------------------------------------------------
    def _ridisegna(self) -> None:
        """Rifà la tabella da zero. Pochi dispositivi: non vale la pena di essere furbi."""
        while self.griglia.count():
            arnese = self.griglia.takeAt(0).widget()
            if arnese is not None:
                arnese.deleteLater()
        self._righe.clear()
        for colonna, testo in enumerate((_("Dispositivo"), _("Marca"), _("Tipo"), _("Stato"), "", "")):
            intestazione = QLabel(testo)
            intestazione.setStyleSheet(f"color:{T.FAINT}; font-size:{T.SIZE_SMALL}px;")
            self.griglia.addWidget(intestazione, 0, colonna)
        for r, d in enumerate(self.casa.elenco(), start=1):
            nome = QLabel(d.nome)
            nome.setStyleSheet(f"color:{T.TEXT};")
            self.griglia.addWidget(nome, r, 0)
            marca = QLabel(d.marca)
            marca.setStyleSheet(f"color:{T.MUTED}; font-size:{T.SIZE_SMALL}px;")
            marca.setToolTip(descrizione_marca(d.marca))
            self.griglia.addWidget(marca, r, 1)
            tipo = QLabel(etichetta_tipo(d.tipo))
            tipo.setStyleSheet(f"color:{T.MUTED}; font-size:{T.SIZE_SMALL}px;")
            self.griglia.addWidget(tipo, r, 2)
            stato = QLabel()
            self.griglia.addWidget(stato, r, 3)
            gira = QPushButton()
            gira.setObjectName("small")
            gira.setMinimumWidth(170)
            gira.clicked.connect(lambda _=False, i=d.identificativo: self._gira(i))
            self.griglia.addWidget(gira, r, 4)
            togli = QPushButton(_("Togli"))
            togli.setObjectName("small")
            togli.clicked.connect(lambda _=False, i=d.identificativo: self._togli(i))
            self.griglia.addWidget(togli, r, 5)
            self._righe[d.identificativo] = (stato, gira)
            self._aggiorna_riga(d.identificativo)

    def _aggiorna_riga(self, identificativo: str) -> None:
        pezzi = self._righe.get(identificativo)
        d = self.casa.dispositivi.get(identificativo)
        if pezzi is None or d is None:
            return
        stato, gira = pezzi
        if d.tipo == SENSORE:
            stato.setText(_("rileva") if d.acceso else _("niente"))
            gira.setText(_("Non passa più nessuno") if d.acceso else _("Fai passare qualcuno"))
        else:
            stato.setText(_("acceso") if d.acceso else _("spento"))
            gira.setText(_("Spegni") if d.acceso else _("Accendi"))
        stato.setStyleSheet(f"color:{T.ARMED if d.acceso else T.FAINT}; font-weight:600;")

    def _scrivi(self, testo: str) -> None:
        self.diario.appendPlainText(f"{time.strftime('%H:%M:%S')}  {testo}")

    # --- quello che si può fare ----------------------------------------------
    def _gira(self, identificativo: str) -> None:
        d = self.casa.dispositivi.get(identificativo)
        if d is None:
            return
        self.casa.cambia(identificativo, not d.acceso, da_chi="finestra")

    def _togli(self, identificativo: str) -> None:
        d = self.casa.dispositivi.get(identificativo)
        if d is not None and self.casa.togli(identificativo):
            self._scrivi(_("Tolto «{nome}». Se Mirto lo aveva in elenco, deve sparire anche da lì.", nome=d.nome))
            self._ridisegna()

    def _aggiungi(self) -> None:
        nome = self.nome.text().strip()
        if not nome:
            # Prima questo passo era muto, e insieme al campo schiacciato faceva sembrare rotto il
            # pulsante. Un rifiuto va detto dov'è successo.
            self.spiegazione.setText(_("Scrivi prima un nome: serve a riconoscerlo nell'elenco."))
            self.spiegazione.setStyleSheet(f"color:{T.PAUSED}; font-size:{T.SIZE_SMALL}px;")
            self.nome.setFocus()
            return
        d = DispositivoFinto(self._identificativo_per(nome), nome,
                             self.tipo.currentData(), self.marca.currentData())
        self.casa.aggiungi(d)
        self.nome.clear()
        self._spiega_la_marca()
        self._scrivi(_("Aggiunto «{nome}» ({marca}). {cosa}", nome=nome, marca=d.marca,
                       cosa=cosa_deve_fare_mirto(d.marca)))
        self._ridisegna()

    def _identificativo_per(self, nome: str) -> str:
        """Un identificativo leggibile e non ripetuto, come fa un dispositivo vero col suo nome."""
        base = "".join(c if c.isalnum() else "_" for c in nome.lower()).strip("_") or "dispositivo"
        if base not in self.casa.dispositivi:
            return base
        n = 2
        while f"{base}_{n}" in self.casa.dispositivi:
            n += 1
        return f"{base}_{n}"

    # --- quello che arriva da fuori ------------------------------------------
    def _segna(self, identificativo: str, da_chi: str) -> None:
        d = self.casa.dispositivi.get(identificativo)
        if d is None:
            return
        self._aggiorna_riga(identificativo)
        comera = (_("rileva") if d.acceso else _("niente")) if d.tipo == SENSORE else \
                 (_("acceso") if d.acceso else _("spento"))
        if da_chi == "comando":
            self._scrivi(_("← Mirto ha comandato «{nome}»: ora è {comera}.", nome=d.nome, comera=comera))
        else:
            self._scrivi(_("→ «{nome}» messo a {comera} da qui: Mirto lo deve vedere cambiare.", nome=d.nome, comera=comera))

    def reject(self) -> None:
        """Esc è «chiudi», non «annulla».

        Una ``QDialog`` con Esc chiama ``reject()``, che la nasconde **senza passare da
        ``closeEvent``**: la finestra sparirebbe e la casa resterebbe in piedi, con la porta 1883
        occupata da un broker che non si vede più.
        """
        self.close()

    def closeEvent(self, event) -> None:      # noqa: N802 - è il nome che vuole Qt
        self.casa.su_cambio = None
        self.casa.ferma()
        super().closeEvent(event)


def apri_casa_finta(porta: int = 0) -> int:
    """Avvia la casa finta e la sua finestra. È quello che fa ``--simulatore-smarthome``."""
    from PySide6.QtWidgets import QApplication  # noqa: PLC0415

    # Prima la casa, poi la finestra: se la porta è occupata si dice e si esce, senza aprire una
    # finestra vuota e senza una traccia di stack addosso a chi stava solo provando.
    casa, perche = accendi(porta)
    if casa is None:
        print(perche, file=sys.stderr, flush=True)
        return 1
    app = QApplication.instance() or QApplication([])
    vesti_come_mirto(app)
    finestra = FinestraCasaFinta(casa)
    finestra.show()
    print(_("Casa smart finta in piedi. Broker MQTT su 127.0.0.1:{porta}", porta=casa.porta))
    print(_("In Mirto: Impostazioni → SmartHome → indirizzo del broker."), flush=True)
    return app.exec()
