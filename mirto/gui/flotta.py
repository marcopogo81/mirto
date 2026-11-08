# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""La vista di flotta: tutti i nodi in una tabella, una riga per nodo (N-03).

Con un nodo basta la riga di *Sistema*. Con otto, le domande diventano **confronti** — chi non
risponde, chi è rimasto indietro, a chi sta finendo il disco, chi non vede niente da tre giorni — e
un elenco di schede da aprire una per volta non risponde a nessuna delle quattro.

Qui dentro non si decide **cosa** si legge: le righe arrivano già fatte da `sistema.righe_flotta`,
che è testo puro e si prova dove Qt non c'è. Questo file è il vestito, e di proposito non sa niente:
mette le celle, i colori e due comandi. Se un giorno la stessa tabella servisse altrove — una pagina
web, un rapporto — quello che conta è già fuori di qui.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..i18n import _
from . import theme as T
from .sistema import COLONNE_FLOTTA

COLORI = {"ok": T.ARMED, "attenzione": T.PAUSED, "rotto": T.ALARM, "assente": T.FAINT}


class VistaFlotta(QWidget):
    """Tutti i nodi in una tabella. Doppio clic apre quel nodo; il pulsante lo aggiorna."""

    apri_nodo = Signal(str)
    apri_diagnosi = Signal(str)
    aggiorna_nodo = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        intro = QLabel(_("Tutti i nodi abbinati, uno per riga. Doppio clic su un nodo per aprirlo in "
                         "Impostazioni, o sulla colonna <b>Avvisi</b> per vedere quali sono e come si "
                         "risolvono; dove c'è un aggiornamento, il pulsante lo manda."))
        intro.setTextFormat(Qt.TextFormat.RichText)
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color:{T.MUTED};")
        lay.addWidget(intro)
        self.tabella = QTableWidget(0, len(COLONNE_FLOTTA))
        self.tabella.setHorizontalHeaderLabels([_(titolo) for _chiave, titolo in COLONNE_FLOTTA])
        self.tabella.verticalHeader().setVisible(False)
        self.tabella.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tabella.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tabella.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabella.setAlternatingRowColors(True)
        self.tabella.setSortingEnabled(False)   # l'ordine è quello della configurazione: si impara
        self.tabella.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.tabella.horizontalHeader().setStretchLastSection(True)
        self.tabella.cellDoubleClicked.connect(self._doppio_clic)
        lay.addWidget(self.tabella, 1)
        self.vuoto = QLabel(_("Nessun nodo abbinato. Impostazioni → Nodi → «Cerca nodi in rete»."))
        self.vuoto.setWordWrap(True)
        self.vuoto.setStyleSheet(f"color:{T.MUTED}; padding:10px; border:1px dashed {T.LINE}; "
                                 "border-radius:10px;")
        lay.addWidget(self.vuoto)
        self._ids: list[str] = []
        self._guai: dict[str, list[str]] = {}   # per nodo: gli avvisi con il loro rimedio

    def _doppio_clic(self, riga: int, colonna: int) -> None:
        """Doppio clic sugli **avvisi** porta dove si risolvono; altrove apre il nodo.

        La diagnosi di un nodo e' una schermata che esiste gia' e che accanto a ogni problema scrive
        il rimedio: il lavoro qui non e' rifarla, e' **portarci**. Un numero che non porta da nessuna
        parte dice solo che c'e' qualcosa che non va, e lascia soli (Marco, 25/10).
        """
        if not 0 <= riga < len(self._ids):
            return
        chiavi = [c for c, _t in COLONNE_FLOTTA]
        if chiavi[colonna] == "avvisi" and self._guai.get(self._ids[riga]):
            self.apri_diagnosi.emit(self._ids[riga])
            return
        self.apri_nodo.emit(self._ids[riga])

    def mostra(self, righe: list[dict]) -> None:
        """Rifà la tabella solo quando cambia **quali** nodi ci sono; se no riscrive le celle.

        La differenza non è il tempo di disegno: è che ricostruire la tabella butta via la riga
        selezionata e il punto in cui si era scorso, e questa schermata si aggiorna ogni due
        secondi — resterebbe inservibile mentre la guardi.
        """
        self.vuoto.setVisible(not righe)
        self.tabella.setVisible(bool(righe))
        ids = [r["id"] for r in righe]
        if ids != self._ids:
            self._ids = ids
            self.tabella.setRowCount(len(righe))
        for i, riga in enumerate(righe):
            for col, (chiave, _titolo) in enumerate(COLONNE_FLOTTA):
                if chiave == "nome":
                    testo, stato = riga["nome"], ""
                else:
                    testo, stato = riga.get(chiave) or ("", "assente")
                cella = self.tabella.item(i, col)
                if cella is None:
                    cella = QTableWidgetItem()
                    self.tabella.setItem(i, col, cella)
                cella.setText(str(testo))
                if stato:
                    cella.setForeground(QColor(COLORI.get(stato, T.TEXT)))
                if chiave == "avvisi":
                    self._guai[riga["id"]] = riga.get("avvisi_dettaglio") or []
                    cella.setToolTip(self._spiega(riga["id"]))
                if chiave == "nome":
                    cella.setData(Qt.ItemDataRole.UserRole, riga["id"])
            self._pulsante(i, riga)

    def _spiega(self, nid: str) -> str:
        """Cosa dice la cella degli avvisi passandoci sopra: ogni problema con il suo rimedio."""
        guai = self._guai.get(nid) or []
        if not guai:
            return _("Nessun problema: il nodo fa tutto quello che deve.")
        return "\n\n".join([*guai, _("Doppio clic per aprire «Stato e diagnosi» di questo nodo.")])

    def _pulsante(self, i: int, riga: dict) -> None:
        """«Aggiorna» vive **nella riga del nodo che lo riguarda**, non in una barra in cima.

        Con otto nodi un pulsante in cima vuol dire «aggiorna quello scelto», e scegliere la riga
        giusta è un passaggio in più in cui si sbaglia nodo. Qui il pulsante c'è solo dove serve, e
        indica da sé di chi parla.
        """
        col = [c for c, _t in COLONNE_FLOTTA].index("versione")
        if not riga.get("aggiornabile"):
            self.tabella.setCellWidget(i, col, None)
            return
        btn = QPushButton(_("Aggiorna {v}", v=str(riga["versione"][0])))
        btn.setObjectName("small")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip(_("Manda a questo nodo il programma che questo computer ha dentro"))
        btn.clicked.connect(lambda _c=False, nid=riga["id"]: self.aggiorna_nodo.emit(nid))
        self.tabella.setCellWidget(i, col, btn)
