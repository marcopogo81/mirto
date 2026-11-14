# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""La finestra della casa smart finta: aggiungi dispositivi, girali, e guarda i due versi insieme.

**A cosa serve davvero.** Il simulatore del nodo non ha una finestra e non ne ha bisogno: di un nodo
si prova il contratto, e il contratto si legge nelle prove. Di un impianto domotico invece serve
**vedere**, perché le due cose che devono funzionare sono due movimenti opposti e nessuno dei due si
capisce leggendo un file di registro:

* **giro l'interruttore qui, e in Mirto parte un'azione** — è il verso dei trigger;
* **Mirto manda un comando, e qui la lampadina si accende** — è il verso delle uscite.

Con questa finestra aperta accanto a Mirto si vedono entrambi nello stesso istante. Senza, si guarda
un elenco di messaggi e si spera.

**Perché i testi non passano dal traduttore.** È uno strumento di sviluppo, come
``--simulatore-nodo``: nessun utente lo aprirà mai. Mettere le sue frasi nel catalogo vorrebbe dire
far tradurre in sette lingue parole che vedremo soltanto noi.

**Il filo.** I cambiamenti arrivano dal filo che legge dal broker, non da quello dell'interfaccia:
passano da un segnale Qt, che è il modo in cui in Mirto si rimbalza il lavoro sul filo giusto.
"""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
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
from ..actions.smarthome_finta import (
    DESCRIZIONE_MARCHE,
    ESPHOME,
    LUCE,
    MARCHE,
    PRESA,
    SENSORE,
    TIPI,
    ZIGBEE,
    CasaFinta,
    DispositivoFinto,
)

log = logging.getLogger(__name__)

ETICHETTA_TIPI = {LUCE: "Luce", PRESA: "Presa", SENSORE: "Sensore"}


class _Ponte(QObject):
    """Porta i cambiamenti dal filo del broker a quello dell'interfaccia."""

    cambiato = Signal(str, str)      # identificativo, da chi (finestra | comando)


class FinestraCasaFinta(QWidget):
    """La casa finta, da guardare e da toccare."""

    def __init__(self, casa: CasaFinta, parent=None) -> None:
        super().__init__(parent)
        self.casa = casa
        self._ponte = _Ponte()
        self._ponte.cambiato.connect(self._segna)
        self.casa.su_cambio = lambda d, da_chi: self._ponte.cambiato.emit(d.identificativo, da_chi)
        self._righe: dict[str, tuple[QLabel, QPushButton]] = {}

        self.setWindowTitle("Casa smart finta — Mirto")
        fuori = QVBoxLayout(self)
        fuori.setSpacing(12)
        fuori.setContentsMargins(16, 16, 16, 16)
        fuori.addWidget(self._testa())
        fuori.addWidget(self._tabella())
        fuori.addWidget(self._aggiunta())
        fuori.addWidget(QLabel("<b>Cosa succede</b>"))
        self.diario = QPlainTextEdit()
        self.diario.setReadOnly(True)
        self.diario.setFont(mono_font(11))
        self.diario.setMinimumHeight(140)
        fuori.addWidget(self.diario)
        self.resize(760, 640)
        self._ridisegna()
        self._scrivi(f"Casa finta in piedi. Broker su 127.0.0.1:{self.casa.porta}.")

    # --- i pezzi della finestra ----------------------------------------------
    def _testa(self) -> QWidget:
        """L'indirizzo del broker, grosso e selezionabile: è la sola cosa da copiare in Mirto."""
        scatola = QFrame()
        scatola.setStyleSheet(
            f"QFrame {{ background:{T.PANEL}; border:1px solid {T.LINE}; border-radius:8px; }}")
        dentro = QVBoxLayout(scatola)
        dentro.setContentsMargins(14, 12, 14, 12)
        titolo = QLabel("<b style='font-size:15px'>Casa smart finta</b>")
        dentro.addWidget(titolo)
        indirizzo = QLabel(f"127.0.0.1:{self.casa.porta}")
        indirizzo.setFont(mono_font(15))
        indirizzo.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        dentro.addWidget(indirizzo)
        nota = QLabel("Questo è l'indirizzo del broker da scrivere in Mirto, in "
                      "<i>Impostazioni → SmartHome</i>. Il broker sta dentro questa finestra: "
                      "chiudendola, i dispositivi sparicono.")
        nota.setWordWrap(True)
        nota.setStyleSheet(f"color:{T.MUTED}; font-size:12px;")
        dentro.addWidget(nota)
        return scatola

    def _tabella(self) -> QWidget:
        scatola = QWidget()
        self.griglia = QGridLayout(scatola)
        self.griglia.setHorizontalSpacing(12)
        self.griglia.setVerticalSpacing(6)
        return scatola

    def _aggiunta(self) -> QWidget:
        scatola = QFrame()
        scatola.setStyleSheet(f"QFrame {{ border-top:1px solid {T.LINE}; }}")
        riga = QHBoxLayout(scatola)
        riga.setContentsMargins(0, 10, 0, 0)
        self.nome = QLineEdit()
        self.nome.setPlaceholderText("Nome del dispositivo (es. Luce del portico)")
        riga.addWidget(self.nome, 3)
        self.tipo = QComboBox()
        for t in TIPI:
            self.tipo.addItem(ETICHETTA_TIPI[t], t)
        riga.addWidget(self.tipo, 1)
        self.marca = QComboBox()
        for m in MARCHE:
            self.marca.addItem(f"{m} — {DESCRIZIONE_MARCHE[m]}", m)
        riga.addWidget(self.marca, 4)
        metti = QPushButton("Aggiungi")
        metti.setStyleSheet(f"QPushButton {{ background:{T.ARMED}; color:#FFFFFF; font-weight:600; }}")
        metti.clicked.connect(self._aggiungi)
        riga.addWidget(metti, 1)
        return scatola

    # --- disegnare ------------------------------------------------------------
    def _ridisegna(self) -> None:
        """Rifà la tabella da zero. Pochi dispositivi: non vale la pena di essere furbi."""
        while self.griglia.count():
            arnese = self.griglia.takeAt(0).widget()
            if arnese is not None:
                arnese.deleteLater()
        self._righe.clear()
        for colonna, testo in enumerate(("Dispositivo", "Marca", "Tipo", "Stato", "", "")):
            intestazione = QLabel(f"<b>{testo}</b>" if testo else "")
            intestazione.setStyleSheet(f"color:{T.MUTED}; font-size:11px;")
            self.griglia.addWidget(intestazione, 0, colonna)
        for r, d in enumerate(self.casa.elenco(), start=1):
            self.griglia.addWidget(QLabel(d.nome), r, 0)
            marca = QLabel(d.marca)
            marca.setStyleSheet(f"color:{T.MUTED};")
            marca.setToolTip(DESCRIZIONE_MARCHE[d.marca])
            self.griglia.addWidget(marca, r, 1)
            self.griglia.addWidget(QLabel(ETICHETTA_TIPI[d.tipo]), r, 2)
            stato = QLabel()
            stato.setFont(mono_font(12))
            self.griglia.addWidget(stato, r, 3)
            gira = QPushButton()
            gira.clicked.connect(lambda _=False, i=d.identificativo: self._gira(i))
            self.griglia.addWidget(gira, r, 4)
            togli = QPushButton("Togli")
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
            stato.setText("rileva" if d.acceso else "niente")
            gira.setText("Fai passare qualcuno" if not d.acceso else "Non passa più nessuno")
        else:
            stato.setText("acceso" if d.acceso else "spento")
            gira.setText("Spegni" if d.acceso else "Accendi")
        stato.setStyleSheet(f"color:{T.ARMED if d.acceso else T.MUTED}; font-weight:600;")

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
            self._scrivi(f"Tolto «{d.nome}». Se Mirto lo aveva in elenco, deve sparire anche da lì.")
            self._ridisegna()

    def _aggiungi(self) -> None:
        nome = self.nome.text().strip()
        if not nome:
            self.nome.setFocus()
            return
        identificativo = self._identificativo_per(nome)
        d = DispositivoFinto(identificativo, nome, self.tipo.currentData(), self.marca.currentData())
        self.casa.aggiungi(d)
        self.nome.clear()
        self._scrivi(f"Aggiunto «{nome}» ({d.marca}). " + {
            ESPHOME: "Si annuncia: Mirto lo deve trovare da solo.",
            ZIGBEE: "Non si annuncia, ma sta nell'elenco della sua marca: Mirto lo deve trovare da lì.",
        }.get(d.marca, "Maleducato: non si annuncia e non parla nessuna lingua nota, "
                       "Mirto lo trova solo con l'ascolto grezzo."))
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
        comera = ("rileva" if d.acceso else "niente") if d.tipo == SENSORE else \
                 ("acceso" if d.acceso else "spento")
        if da_chi == "comando":
            self._scrivi(f"← Mirto ha comandato «{d.nome}»: ora è {comera}.")
        else:
            self._scrivi(f"→ «{d.nome}» messo a {comera} da qui: Mirto lo deve vedere cambiare.")

    def closeEvent(self, event) -> None:      # noqa: N802 - è il nome che vuole Qt
        self.casa.su_cambio = None
        self.casa.ferma()
        super().closeEvent(event)


def apri_casa_finta(porta: int = 0) -> int:
    """Avvia la casa finta e la sua finestra. È quello che fa ``--simulatore-smarthome``."""
    from PySide6.QtWidgets import QApplication  # noqa: PLC0415

    app = QApplication.instance() or QApplication([])
    casa = CasaFinta(porta=porta).avvia()
    finestra = FinestraCasaFinta(casa)
    finestra.show()
    print(f"Casa smart finta in piedi. Broker MQTT su 127.0.0.1:{casa.porta}")
    print("In Mirto: Impostazioni → SmartHome → indirizzo del broker.", flush=True)
    return app.exec()
