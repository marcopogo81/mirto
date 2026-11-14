# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Finestra "Abbina nodo": mostra l'impronta, chiede il PIN del nodo e il nome, e abbina senza chiave copiata."""

from __future__ import annotations

import html

from PySide6.QtCore import Qt
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)

from . import theme as T
from .widgets import mono_font
from ..i18n import _


def short_fp(fp: str) -> str:
    """Impronta su due righe, più facile da confrontare a colpo d'occhio."""
    parts = fp.split(":")
    half = (len(parts) + 1) // 2
    return ":".join(parts[:half]) + "\n" + ":".join(parts[half:]) if len(parts) > 8 else fp


class PairDialog(QDialog):
    def __init__(self, found: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_('Nuovo nodo trovato'))
        self.setModal(True)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        mins = max(1, int(found.get("scade_tra_s", 0)) // 60) if found.get("scade_tra_s") else 0
        quanto = (" " + _('· pronto da abbinare') if found.get("senza_scadenza")
                  else (_(' · abbinabile ancora {mins} min', mins=mins) if mins else ""))
        head = QLabel(_("<b style='font-size:15px'>{escape}</b><br><span style='color:{muted}'>{escape2}", escape=html.escape(found.get('nome') or 'Nodo'), muted=T.MUTED, escape2=html.escape(found['url']))
                      + quanto + "</span>")
        lay.addWidget(head)
        lay.addWidget(QLabel(_('Impronta del certificato del nodo:')))
        fp = QLabel(short_fp(found["impronta"]))
        fp.setFont(mono_font(12))
        fp.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        fp.setStyleSheet(f"background:{T.PANEL}; border:1px solid {T.LINE}; border-radius:6px; padding:8px;")
        lay.addWidget(fp)
        note = QLabel(_('Per esserne certo confrontala con quella mostrata sul nodo (<code>mirto-nodo stato</code>). Abbina solo un dispositivo che riconosci: potrà essere comandato da Mirto.'))
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{T.MUTED}; font-size:12px;")
        lay.addWidget(note)
        lay.addWidget(QLabel(_('PIN mostrato dal nodo (6 cifre):')))
        self.pin = QLineEdit()
        self.pin.setPlaceholderText(_('es. 408315'))
        self.pin.setMaxLength(6)
        self.pin.setValidator(QIntValidator(0, 999999, self))
        self.pin.setFont(mono_font(13))
        lay.addWidget(self.pin)
        pin_note = QLabel(_('Lo scrive il nodo: <code>mirto-nodo abbina</code>, oppure <code>mirto-pin.txt</code> nella microSD al primo avvio.'))
        pin_note.setWordWrap(True)
        pin_note.setStyleSheet(f"color:{T.MUTED}; font-size:12px;")
        lay.addWidget(pin_note)
        lay.addWidget(QLabel(_('Nome di questo nodo:')))
        from ..actions.pairing import nome_di_fabbrica  # noqa: PLC0415

        proposto = (found.get("nome") or "").strip()
        self.name = QLineEdit("" if nome_di_fabbrica(proposto) else proposto)
        self.name.setPlaceholderText(_("es. Garage"))
        lay.addWidget(self.name)
        nome_note = QLabel(_('Serve, e va scelto adesso: è il nome con cui il nodo compare in Mirto e negli avvisi, e viene scritto anche sul dispositivo (il nome in rete resta <code>mirto-node-XXXX.local</code>). Configurando più nodi uno dopo l\'altro è così che si riconosce quale camera è di quale nodo.'))
        nome_note.setWordWrap(True)
        nome_note.setStyleSheet(f"color:{T.MUTED}; font-size:12px;")
        lay.addWidget(nome_note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setText(_("Abbina"))
        ok.setStyleSheet(f"QPushButton {{ background:{T.ARMED}; color:#FFFFFF; font-weight:600; }}")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.resize(460, self.sizeHint().height())

    def _accept(self):
        if len(self.pin.text().strip()) != 6:
            self.pin.setFocus()
        elif self.name.text().strip():
            self.accept()
        else:
            self.name.setFocus()


def pair_node_dialog(parent, core, found: dict) -> str | None:
    """Chiede conferma e abbina. Restituisce il nome del nodo abbinato, o None."""
    if not core.actions:
        return None
    dlg = PairDialog(found, parent)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        nd = core.actions.pair_new_node(found, dlg.name.text().strip(), dlg.pin.text().strip())
    except Exception as exc:  # noqa: BLE001
        QApplication.restoreOverrideCursor()
        QMessageBox.warning(parent, _('Abbinamento non riuscito'),
                            _('{exc}\n\nSe il PIN è sbagliato o la finestra di abbinamento è scaduta, sul nodo: mirto-nodo abbina (mostra un PIN nuovo).', exc=exc))
        return None
    QApplication.restoreOverrideCursor()
    passo_del_wifi(parent, core, nd)
    return nd["name"]


def passo_del_wifi(parent, core, nd: dict) -> bool:
    """Ultimo passo dell'abbinamento: il Wi-Fi, finché il cavo c'è ancora.

    Sta qui e non nei due posti da cui si abbina — le Impostazioni e l'avviso «Nuovo nodo trovato» —
    perché un passo offerto da una porta sola è lo stesso difetto che stiamo correggendo, solo
    spostato. Chi abbinerà da un terzo posto, domani, se lo ritrova senza saperlo.

    Non solleva mai: il nodo è già abbinato e funziona: se questo passo non riesce, l'abbinamento non
    deve diventare un errore. Al peggio il Wi-Fi si mette dopo, da *Impostazioni → Nodi → Wi-Fi*, che
    è dov'è sempre stato. Restituisce ``True`` se la finestra del Wi-Fi è stata aperta.
    """
    from ..actions.diagnosi import serve_mettere_il_wifi  # noqa: PLC0415

    try:
        client = (core.actions.clients or {}).get(str(nd["id"]))
        if client is None or not serve_mettere_il_wifi(client.status().get("rete")):
            return False
    except Exception:  # noqa: BLE001
        return False
    if QMessageBox.question(
            parent, _('Ultimo passo: il Wi-Fi'),
            _('{name} è collegato col cavo di rete. Se lo staccherai — e un nodo da esterno finisce su '
              'un palo — sparirà, e per rimetterlo in rete servirà di nuovo il cavo.\n\n'
              'Vuoi metterlo sul Wi-Fi adesso, finché è raggiungibile? Il cavo resta attaccato finché '
              'il Wi-Fi non funziona.', name=nd["name"]),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes) != QMessageBox.StandardButton.Yes:
        return False
    try:
        from .wifi_ui import ponte_del_nodo, wifi_dialog  # noqa: PLC0415

        wifi_dialog(parent, ponte_del_nodo(client), nd.get("name") or str(nd["id"]))
    except Exception as exc:  # noqa: BLE001
        QMessageBox.information(parent, _('Wi-Fi'),
                                _('Non riesco ad aprire il Wi-Fi del nodo adesso ({exc}). '
                                  'Lo trovi in Impostazioni → Nodi → Wi-Fi.', exc=exc))
    return True
