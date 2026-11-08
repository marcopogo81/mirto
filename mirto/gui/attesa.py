# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""«Sto lavorando»: una barra che si muove, il tempo che resta, e alla fine un esito che si vede.

Nasce da una prova vera, il 21/10: premendo «Collega» nel pannello del Wi-Fi il nodo lavorava per mezzo
minuto e la finestra lo diceva con una riga di testo piccola, grigia, in fondo. Mezzo minuto senza
segnali sembra un programma bloccato — e chi prova finisce per premere di nuovo, o per chiudere.

Si comporta come l'etichetta che sostituisce (``text()``/``setText()``), così le finestre la usano al
posto della loro riga di note senza cambiare altro, e in più:

* ``inizia()`` fa comparire la barra e, se si sa quanto può durare, il conto alla rovescia;
* ``esito()`` la fa sparire e colora la risposta — verde se è andata, ambra se no.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel, QProgressBar, QVBoxLayout, QWidget

from . import theme as T
from ..i18n import _


class Attesa(QWidget):
    """Riga di note di una finestra, che sa anche dire «sto aspettando» e «è andata così»."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.testo = QLabel("")
        self.testo.setWordWrap(True)
        self.barra = QProgressBar()
        self.barra.setRange(0, 0)          # indeterminata: si muove senza promettere una percentuale
        self.barra.setTextVisible(False)
        self.barra.setFixedHeight(6)
        self.barra.setVisible(False)
        lay.addWidget(self.testo)
        lay.addWidget(self.barra)
        self._base = ""
        self._fino = 0.0
        self._aspetto = False
        self._orologio = QTimer(self)
        self._orologio.timeout.connect(self._conta)
        self._stile(T.MUTED)

    # --- come l'etichetta che sostituisce -------------------------------------
    def text(self) -> str:
        return self.testo.text()

    def setText(self, testo: str) -> None:
        """Una nota normale: niente barra, niente colore."""
        self._ferma()
        self._base = str(testo)
        self._stile(T.MUTED)
        self.testo.setText(self._base)

    # --- attesa ----------------------------------------------------------------
    def inizia(self, testo: str, secondi: float = 0) -> None:
        self._base = str(testo)
        self._fino = time.monotonic() + float(secondi or 0)
        self._stile(T.TEXT)
        self.testo.setText(self._base)
        self._barra(True)
        if secondi:
            self._conta()
            self._orologio.start(1000)

    def esito(self, testo: str, ok: bool = True) -> None:
        self._ferma()
        self._base = str(testo)
        self._stile(T.ARMED if ok else "#E8A33D")
        self.testo.setText(self._base)

    def sta_aspettando(self) -> bool:
        # Non si chiede a Qt se la barra «è visibile»: un widget figlio di una finestra non ancora
        # mostrata risponde comunque no, anche subito dopo setVisible(True). Lo stato lo tiene questa
        # classe, e _barra() e' l'unico posto che muove le due cose insieme. (CI rossa, 21/10.)
        return self._aspetto

    # --- dentro -----------------------------------------------------------------
    def _barra(self, accesa: bool) -> None:
        self._aspetto = bool(accesa)
        self.barra.setVisible(bool(accesa))

    def _ferma(self) -> None:
        self._orologio.stop()
        self._barra(False)

    def _conta(self) -> None:
        manca = int(max(0.0, self._fino - time.monotonic()))
        if manca <= 0:
            # Il tempo dichiarato è una stima: scaduta quella, si dice che si sta ancora aspettando
            # invece di mostrare «ancora 0 s», che sembrerebbe un conto fermo.
            self._orologio.stop()
            self.testo.setText(_('{testo} · ancora un momento…', testo=self._base))
            return
        self.testo.setText(_('{testo} · ancora {manca} s', testo=self._base, manca=manca))

    def _stile(self, colore: str) -> None:
        peso = "600" if colore != T.MUTED else "400"
        self.testo.setStyleSheet(f"color:{colore}; font-size:12px; font-weight:{peso};")
