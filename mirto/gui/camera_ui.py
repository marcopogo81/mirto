# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Finestra «Camera del nodo»: accesa o spenta, modello NoIR, risoluzione — senza aprire un terminale.

I nodi avranno camere diverse: NoIR per chi ha i faretti a infrarossi, normali per gli altri. Il
sensore è lo stesso nei due modelli, quindi la scelta la fa una persona — e finché stava solo in
``mirto-nodo camera``, quella persona doveva aprire una sessione ssh su ogni nodo.
"""

from __future__ import annotations

import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QVBoxLayout,
)

from . import theme as T
from ..i18n import _


class CameraDialog(QDialog):
    """Le stesse scelte di «mirto-nodo camera», con le parole che servono per decidere."""

    def __init__(self, nome: str, stato: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_('Camera del nodo'))
        self.setModal(True)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.addWidget(QLabel(_("<b style='font-size:15px'>{escape}</b>", escape=html.escape(nome))))

        sensore = str(stato.get("sensore") or "")
        if sensore:
            vista = QLabel(_('Camera collegata al nodo: <b>{sensore}</b>', sensore=html.escape(sensore)))
            vista.setStyleSheet(f"color:{T.MUTED}; font-size:12px;")
        else:
            vista = QLabel(_('Il nodo <b>non vede nessuna camera</b>. Quasi sempre è il cavo piatto: spegni il nodo, stacca e rimetti il cavo (contatti verso il connettore HDMI, fermo premuto a fondo). Se su questo nodo una camera non c\'è, lasciala spenta: Mirto smette di cercarla.'))
            vista.setStyleSheet("color:#E8A33D; font-size:12px;")
        vista.setWordWrap(True)
        lay.addWidget(vista)

        self.accesa = QCheckBox(_('Camera accesa'))
        self.accesa.setChecked(bool(stato.get("attiva", True)))
        lay.addWidget(self.accesa)

        self.noir = QCheckBox(_('È il modello NoIR'))
        self.noir.setChecked(bool(stato.get("noir", False)))
        lay.addWidget(self.noir)
        nota = QLabel(_('NoIR vuol dire «senza filtro infrarossi»: serve solo se hai faretti a infrarossi che illuminano la scena, ed è così che quelle camere vedono di notte. Senza faretti, nel buio non vede niente nemmeno la NoIR, e di giorno i colori vengono rosati. Nel dubbio lasciala non spuntata.'))
        nota.setWordWrap(True)
        nota.setStyleSheet(f"color:{T.MUTED}; font-size:12px;")
        lay.addWidget(nota)

        lay.addWidget(QLabel(_('Risoluzione:')))
        self.ris = QComboBox()
        self.scelte = [r for r in (stato.get("risoluzioni") or []) if isinstance(r, dict)]
        for r in self.scelte:
            self.ris.addItem(str(r.get("etichetta") or ""))
        ora = (stato.get("larghezza"), stato.get("altezza"), stato.get("fps"))
        for i, r in enumerate(self.scelte):
            if (r.get("larghezza"), r.get("altezza"), r.get("fps")) == ora:
                self.ris.setCurrentIndex(i)
                break
        lay.addWidget(self.ris)
        banda = QLabel(_('Più risoluzione vuol dire più banda e più spazio: 1280×720 basta per riconoscere una persona, 1920×1080 serve per leggere un volto o una targa.'))
        banda.setWordWrap(True)
        banda.setStyleSheet(f"color:{T.MUTED}; font-size:12px;")
        lay.addWidget(banda)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setText(_("Applica"))
        ok.setStyleSheet(f"QPushButton {{ background:{T.ARMED}; color:#FFFFFF; font-weight:600; }}")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.accesa.toggled.connect(self._aggiorna)
        self._aggiorna()
        self.resize(460, self.sizeHint().height())

    def _aggiorna(self) -> None:
        """Camera spenta: modello e risoluzione non hanno senso, quindi si spengono anche loro."""
        acceso = self.accesa.isChecked()
        self.noir.setEnabled(acceso)
        self.ris.setEnabled(acceso)

    def scelta(self) -> dict:
        i = self.ris.currentIndex()
        r = self.scelte[i] if 0 <= i < len(self.scelte) else {}
        return {"attiva": self.accesa.isChecked(), "noir": self.noir.isChecked(),
                "larghezza": int(r.get("larghezza", 1280)), "altezza": int(r.get("altezza", 720)),
                "fps": int(r.get("fps", 15))}


def camera_dialog(parent, client, nome: str) -> dict | None:
    """Legge la camera dal nodo, chiede le scelte e le manda. Restituisce lo stato nuovo, o None."""
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        stato = client.camera_state()
    except Exception as exc:  # noqa: BLE001
        QMessageBox.warning(parent, _('Camera del nodo'),
                            _('Il nodo non risponde: {exc}', exc=exc))
        return None
    finally:
        QApplication.restoreOverrideCursor()
    dlg = CameraDialog(nome, stato, parent)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        return client.set_camera(**dlg.scelta())
    except Exception as exc:  # noqa: BLE001
        QMessageBox.warning(parent, _('Camera del nodo'),
                            _('Il nodo non ha accettato la richiesta: {exc}', exc=exc))
        return None
    finally:
        QApplication.restoreOverrideCursor()
