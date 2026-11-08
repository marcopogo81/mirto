# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Finestra «Altri computer»: chi è abbinato a un nodo, come aggiungerne uno, come staccarne uno.

Un nodo risponde solo ai Mirto che hanno una chiave, e la chiave si ottiene una volta sola col PIN.
Finché questo pannello non c'era, aggiungere il portatile di casa voleva dire andare sul nodo col
terminale (``mirto-nodo abbina``) e leggere il PIN da lì.

Due cose delicate, trattate come tali: il PIN si vede solo mentre la finestra è aperta — e si può
richiudere subito — e staccare un abbinamento chiede conferma, perché quel computer per rientrare ha
bisogno di un PIN nuovo. L'ultimo abbinamento non si stacca da qui: lo rifiuta il nodo, perché
lascerebbe la casa senza nessun Mirto.
"""

from __future__ import annotations

import html
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from . import theme as T
from ..actions import pannello_abbinamenti as pannello
from ..i18n import _


class AbbinamentiDialog(QDialog):
    """I Mirto abbinati a un nodo: elencarli, aggiungerne uno, staccarne uno."""

    def __init__(self, nome: str, ponte, io: str, parent=None):
        super().__init__(parent)
        self.ponte = ponte
        self.io = io            # come si chiama questo computer per il nodo: non si stacca per sbaglio
        self.setWindowTitle(_('Altri computer'))
        self.setModal(True)
        self.stato = dict(ponte.stato() or {})
        self.aperto_da = time.time()    # da qui si conta il tempo che resta, letto insieme allo stato
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.addWidget(QLabel(_("<b style='font-size:15px'>{escape}</b>", escape=html.escape(nome))))
        spiega = QLabel(_('Questo nodo risponde solo ai Mirto che hanno una chiave. Per aggiungere un altro computer: premi «Apri l\'abbinamento», poi su quell\'altro computer cerca il nodo e scrivi il PIN che compare qui.'))
        spiega.setWordWrap(True)
        spiega.setStyleSheet(f"color:{T.MUTED}; font-size:12px;")
        lay.addWidget(spiega)

        self.elenco = QListWidget()
        self.elenco.setMinimumHeight(110)
        lay.addWidget(self.elenco)

        self.pin = QLabel("")
        self.pin.setWordWrap(True)
        self.pin.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.pin)

        riga = QHBoxLayout()
        self.apri_btn = QPushButton(_("Apri l'abbinamento"))
        self.chiudi_btn = QPushButton(_('Chiudi l\'abbinamento'))
        self.stacca_btn = QPushButton(_('Stacca…'))
        self.stacca_btn.setStyleSheet("QPushButton { color:#E8A33D; }")
        riga.addWidget(self.apri_btn)
        riga.addWidget(self.chiudi_btn)
        riga.addWidget(self.stacca_btn)
        riga.addStretch(1)
        lay.addLayout(riga)

        self.nota = QLabel("")
        self.nota.setWordWrap(True)
        self.nota.setStyleSheet(f"color:{T.MUTED}; font-size:12px;")
        lay.addWidget(self.nota)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText(_("Chiudi"))
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

        self.apri_btn.clicked.connect(self._apri)
        self.chiudi_btn.clicked.connect(self._chiudi)
        self.stacca_btn.clicked.connect(self._stacca)
        self.elenco.currentRowChanged.connect(self._aggiorna_pulsanti)
        self.orologio = QTimer(self)      # il tempo che resta scende da solo, senza chiedere al nodo
        self.orologio.timeout.connect(self._scrivi_pin)
        self.righe: list[dict] = []
        self._riempi()
        if self.finestra().get("aperto") and not self.finestra().get("senza_scadenza"):
            self.orologio.start(1000)
        self.resize(540, self.sizeHint().height())

    # --- lettura dello stato -------------------------------------------------
    def chiavi(self) -> list[dict]:
        return [k for k in (self.stato.get("chiavi") or []) if isinstance(k, dict)]

    def _riempi(self) -> None:
        scelto = self.nome_scelto()
        self.elenco.clear()
        self.righe = pannello.righe(self.stato, self.io)
        for r in self.righe:
            voce = QListWidgetItem(r["testo"])
            voce.setData(Qt.ItemDataRole.UserRole, r["nome"])
            self.elenco.addItem(voce)
        if not self.righe:
            self.elenco.addItem(_('(nessuno: il nodo non risponde a nessun Mirto)'))
        if scelto:
            for i in range(self.elenco.count()):
                if self.elenco.item(i).data(Qt.ItemDataRole.UserRole) == scelto:
                    self.elenco.setCurrentRow(i)
                    break
        self._scrivi_pin()
        self._aggiorna_pulsanti()

    def nome_scelto(self) -> str:
        voce = self.elenco.currentItem()
        return str(voce.data(Qt.ItemDataRole.UserRole) or "") if voce is not None else ""

    def finestra(self) -> dict:
        f = self.stato.get("finestra")
        return dict(f) if isinstance(f, dict) else {}

    def _scrivi_pin(self) -> None:
        f = self.finestra()
        if not f.get("aperto") or not f.get("pin"):
            self.orologio.stop()
            self.pin.setText("")
            self.pin.setStyleSheet("")
            return
        if f.get("senza_scadenza"):
            resta = _('non scade finché il nodo non viene abbinato')
        else:
            manca = max(0, int(f.get("scade_tra_s") or 0) - int(time.time() - self.aperto_da))
            resta = _('ancora {minuti} min {secondi:02d} s', minuti=manca // 60, secondi=manca % 60)
            if manca <= 0:
                self.orologio.stop()
                self.stato["finestra"] = dict(f, aperto=False, pin="")
                self.nota.setText(_('Tempo scaduto: se serve ancora, riapri.'))
                return self._scrivi_pin()
        self.pin.setText(_("PIN da scrivere sull'altro computer: <b style='font-size:22px'>{pin}</b> · {resta}",
                           pin=html.escape(str(f["pin"])), resta=resta))
        self.pin.setStyleSheet(f"color:{T.TEXT};")

    def _aggiorna_pulsanti(self, *_a) -> None:
        aperta = bool(self.finestra().get("aperto"))
        self.apri_btn.setEnabled(not aperta)
        self.chiudi_btn.setEnabled(aperta)
        self.stacca_btn.setEnabled(pannello.si_puo_staccare(self.righe, self.nome_scelto()))
        testo = pannello.nota(self.stato, self.righe)
        if testo or not self.nota.text():
            self.nota.setText(testo)

    # --- cose che chiedono al nodo ------------------------------------------
    def _chiedi(self, fn, *args):
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            return fn(*args)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, _('Altri computer'), _('Il nodo non ha accettato la richiesta: {exc}', exc=exc))
            return None
        finally:
            QApplication.restoreOverrideCursor()

    def _apri(self) -> None:
        risposta = self._chiedi(self.ponte.apri)
        if risposta is None:
            return
        self.aperto_da = time.time()
        self.stato = dict(risposta)
        self.nota.setText(_('Aperto: sull\'altro computer apri Mirto, premi «Cerca nodi in rete», controlla l\'impronta e scrivi il PIN.'))
        self._riempi()
        self.orologio.start(1000)

    def _chiudi(self) -> None:
        risposta = self._chiedi(self.ponte.chiudi)
        if risposta is None:
            return
        self.stato = dict(risposta)
        self.orologio.stop()
        self.nota.setText(_('Chiuso: nessun altro computer può abbinarsi.'))
        self._riempi()

    def _stacca(self) -> None:
        nome = self.nome_scelto()
        riga = next((r for r in self.righe if r["nome"] == nome), None)
        if riga is None:
            return
        domanda = pannello.domanda(riga)
        if QMessageBox.question(self, _('Altri computer'), domanda,
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        risposta = self._chiedi(self.ponte.stacca, nome)
        if risposta is None:
            return
        self.stato = dict(risposta)
        self.nota.setText(_('Ritirata la chiave dell\'installazione: il nodo non la accetta più.')
                          if riga["installazione"] else _('Staccato: {nome}', nome=nome))
        self._riempi()


def abbinamenti_dialog(parent, ponte, nome: str, io: str = "") -> bool:
    """Apre la finestra degli abbinamenti del nodo. True se la finestra si è aperta."""
    try:
        dlg = AbbinamentiDialog(nome, ponte, io, parent)
    except Exception as exc:  # noqa: BLE001
        QMessageBox.warning(parent, _('Altri computer'), _('Il nodo non risponde: {exc}', exc=exc))
        return False
    dlg.exec()
    return True
