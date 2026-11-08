# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Finestra «Wi-Fi del nodo»: le reti che vede il nodo, e il collegamento, senza aprire un terminale.

L'elenco lo fa **il nodo**, non questo computer: è l'unico modo di sapere se da dove metterai il
dispositivo il segnale arriva davvero, e funziona anche se il computer è attaccato col cavo e di reti
non ne vede nessuna. La password viaggia dentro la connessione cifrata già verificata per impronta, non
compare in nessun registro, e il nodo resta attaccato al cavo: se la password è sbagliata non si perde
il nodo, si riprova. Il «ponte» è l'oggetto che parla col nodo, così la finestra si prova anche senza
un Raspberry acceso.
"""

from __future__ import annotations

import html
import logging

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from . import theme as T
from .attesa import Attesa
from ..i18n import _

log = logging.getLogger(__name__)

NASCOSTA = "\x00nascosta"      # la voce "rete nascosta": non è un SSID, non può coincidere con uno


class WifiDialog(QDialog):
    """Le stesse scelte di «mirto-nodo wifi»: cerca le reti, scegli, password, collega."""

    def __init__(self, nome: str, ponte, parent=None):
        super().__init__(parent)
        self.ponte = ponte
        self.setWindowTitle(_('Wi-Fi del nodo'))
        self.setModal(True)
        self.stato = dict(ponte.stato() or {})
        self.attesa = ""          # "cerca" o "collega" mentre il nodo lavora
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.addWidget(QLabel(_("<b style='font-size:15px'>{escape}</b>", escape=html.escape(nome))))

        self.dove = QLabel("")
        self.dove.setWordWrap(True)
        self.dove.setStyleSheet(f"color:{T.MUTED}; font-size:12px;")
        lay.addWidget(self.dove)

        lay.addWidget(QLabel(_('Rete Wi-Fi, fra quelle che vede il nodo:')))
        riga = QHBoxLayout()
        self.reti = QComboBox()
        self.cerca_btn = QPushButton(_('Cerca reti…'))
        riga.addWidget(self.reti, 1)
        riga.addWidget(self.cerca_btn)
        lay.addLayout(riga)

        self.nome_nascosto = QLineEdit()
        self.nome_nascosto.setPlaceholderText(_('Nome esatto della rete nascosta'))
        lay.addWidget(self.nome_nascosto)

        lay.addWidget(QLabel(_('Password della rete:')))
        riga_pwd = QHBoxLayout()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText(_('lasciala vuota se la rete è aperta'))
        self.portachiavi_btn = QPushButton(_('Prendila da questo computer'))
        self.portachiavi_btn.setToolTip(_('La password è già nel portachiavi di questo computer, che è sulla stessa rete: la chiede al sistema invece di farti riscrivere trenta caratteri.'))
        riga_pwd.addWidget(self.password, 1)
        riga_pwd.addWidget(self.portachiavi_btn)
        lay.addLayout(riga_pwd)
        self.mostra = QCheckBox(_('Mostra la password'))
        lay.addWidget(self.mostra)

        self.nota = Attesa()        # riga di note che sa anche dire «sto aspettando» e com'è andata
        lay.addWidget(self.nota)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_btn.setText(_("Collega"))
        self.ok_btn.setStyleSheet(f"QPushButton {{ background:{T.ARMED}; color:#FFFFFF; font-weight:600; }}")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Chiudi"))
        buttons.accepted.connect(self._collega)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

        self.cerca_btn.clicked.connect(self._cerca)
        self.portachiavi_btn.clicked.connect(self._dal_portachiavi)
        self.reti.currentIndexChanged.connect(self._mostra_campi)
        self.mostra.toggled.connect(self._mostra_password)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._ripassa)
        # le reti di cui **questo** computer ha già la password: solo i nomi, non le password. Un ponte
        # che non sa risponderle (un nodo provato senza portachiavi) lascia il pulsante spento.
        try:
            elenca = getattr(ponte, "reti_del_computer", None)
            self.conosciute = set(elenca() if elenca is not None else ())
        except Exception:  # noqa: BLE001
            self.conosciute = set()
        self._riempi_reti()
        self._mostra_campi()
        self.resize(520, self.sizeHint().height())

    # --- stato della finestra ------------------------------------------------
    def _riempi_reti(self) -> None:
        """L'elenco dell'ultima ricerca fatta dal nodo, più la voce per una rete nascosta."""
        scelto = str(self.reti.currentData() or "") if self.reti.count() else ""
        self.reti.blockSignals(True)
        self.reti.clear()
        for r in (self.stato.get("reti") or []):
            if not isinstance(r, dict) or not r.get("ssid"):
                continue
            sicurezza = str(r.get("sicurezza") or "aperta")
            etichetta = _('{ssid} · segnale {segnale}% · {sicurezza}', ssid=str(r["ssid"]),
                          segnale=int(r.get("segnale") or 0),
                          sicurezza=_('aperta') if sicurezza == "aperta" else sicurezza)
            if r.get("in_uso"):
                etichetta += _(' · in uso')
            self.reti.addItem(etichetta, str(r["ssid"]))
        self.reti.addItem(_('Rete nascosta: scrivo io il nome…'), NASCOSTA)
        prima = scelto or str(self.stato.get("in_uso") or "")
        if prima:
            i = self.reti.findData(prima)
            if i >= 0:
                self.reti.setCurrentIndex(i)
        self.reti.blockSignals(False)
        self.nome_nascosto.setVisible(self.reti.currentData() == NASCOSTA)
        in_uso = str(self.stato.get("in_uso") or "")
        indirizzi = ", ".join(str(a) for a in (self.stato.get("indirizzi") or []))
        self.dove.setText(_('Adesso il nodo è su «{rete}», indirizzo {indirizzi}.',
                            rete=in_uso or _('solo cavo di rete'),
                            indirizzi=indirizzi or _('sconosciuto')))

    def _mostra_campi(self, *_a) -> None:
        # currentIndexChanged passa l'indice: non serve, ma va accettato
        nascosta = self.reti.currentData() == NASCOSTA
        self.nome_nascosto.setVisible(nascosta)
        self.portachiavi_btn.setEnabled(self.ssid_scelto() in self.conosciute)
        if self.attesa:
            return
        if not (self.stato.get("reti") or []):
            self.nota.setText(_('Il nodo non ha ancora cercato le reti: premi «Cerca reti…». Cerca lui, da dove è messo, così sai se il segnale arriva.'))
        else:
            self.nota.setText(_('Il nodo resta attaccato al cavo finché il Wi-Fi non funziona: se la password è sbagliata non lo perdi, riprovi. Stacca il cavo solo dopo aver visto qui sopra l\'indirizzo nuovo.'))

    def _mostra_password(self, acceso: bool) -> None:
        self.password.setEchoMode(QLineEdit.EchoMode.Normal if acceso else QLineEdit.EchoMode.Password)

    def ssid_scelto(self) -> str:
        dato = str(self.reti.currentData() or "") if self.reti.count() else ""
        return self.nome_nascosto.text().strip() if dato == NASCOSTA else dato

    def scelta(self) -> dict:
        return {"ssid": self.ssid_scelto(), "password": self.password.text(),
                "nascosta": self.reti.currentData() == NASCOSTA}

    # --- cose che chiedono al nodo ------------------------------------------
    @staticmethod
    def _quanto(risposta, difetto: int) -> int:
        """Quanto dice di metterci il nodo: è lui a saperlo, noi abbiamo solo un'idea."""
        try:
            return int((risposta or {}).get("attesa_s") or difetto)
        except (TypeError, ValueError, AttributeError):
            return difetto

    def _chiedi(self, fn, *args):
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            return fn(*args)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, _('Wi-Fi del nodo'), _('Il nodo non ha accettato la richiesta: {exc}', exc=exc))
            return None
        finally:
            QApplication.restoreOverrideCursor()

    def _dal_portachiavi(self) -> None:
        """Chiede la password al sistema. È il sistema che chiede il permesso, non noi: lo si dice prima."""
        ssid = self.ssid_scelto()
        if ssid not in self.conosciute:
            return
        if QMessageBox.question(self, _('Wi-Fi del nodo'),
                                _('Chiedo a questo computer la password di «{ssid}» e la mando al nodo. Il sistema può chiederti il permesso. La password non viene mostrata, non finisce in nessun registro e Mirto non la conserva.', ssid=ssid),
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            password = self.ponte.password_del_computer(ssid)
        except Exception as exc:  # noqa: BLE001
            password = ""
            log.warning("Portachiavi: %s", exc)
        finally:
            QApplication.restoreOverrideCursor()
        if not password:
            self.nota.setText(self.ponte.perche_no())
            return
        self.password.setText(password)
        self.mostra.setChecked(False)         # presa dal sistema: non si mostra per sbaglio
        self.nota.setText(_('Password presa da questo computer. Premi «Collega».'))

    def _cerca(self) -> None:
        risposta = self._chiedi(self.ponte.cerca)
        if risposta is None:
            return
        self.attesa = "cerca"
        self.cerca_btn.setEnabled(False)
        self.nota.inizia(_('Sto cercando le reti dal nodo.'), self._quanto(risposta, 25))
        self.timer.start(3000)

    def _collega(self) -> None:
        scelta = self.scelta()
        if not scelta["ssid"]:
            QMessageBox.warning(self, _('Wi-Fi del nodo'), _('Scegli una rete, o scrivi il nome di quella nascosta.'))
            return
        if scelta["password"] and not 8 <= len(scelta["password"]) <= 63:
            QMessageBox.warning(self, _('Wi-Fi del nodo'),
                                _('La password di una rete Wi-Fi sta fra 8 e 63 caratteri. Lasciala vuota solo se la rete è aperta.'))
            return
        risposta = self._chiedi(self.ponte.collega, scelta["ssid"], scelta["password"], scelta["nascosta"])
        if risposta is None:
            return
        self.attesa = "collega"
        self.ok_btn.setEnabled(False)
        self.cerca_btn.setEnabled(False)
        self.password.clear()          # la password non resta nemmeno nella finestra
        self.nota.inizia(_('Sto collegando il nodo a «{ssid}». Lascia il cavo attaccato.', ssid=scelta["ssid"]),
                         self._quanto(risposta, 60))
        self.timer.start(3000)

    def _ripassa(self) -> None:
        """Ogni tre secondi, finché il nodo sta lavorando: ha finito? Com'è andata?"""
        try:
            nuovo = dict(self.ponte.stato() or {})
        except Exception:  # noqa: BLE001
            return
        esito = nuovo.get("esito") or {}
        if str(esito.get("azione") or "") != self.attesa:
            return
        self.timer.stop()
        atteso, self.attesa = self.attesa, ""
        self.stato = nuovo
        andata = str(esito.get("esito") or "")
        messaggio = str(esito.get("messaggio") or "")
        self._riempi_reti()
        self.cerca_btn.setEnabled(True)
        self.ok_btn.setEnabled(True)
        if atteso == "cerca":
            self.nota.esito(_('Ricerca finita: {messaggio}. Scegli la rete e scrivi la password.', messaggio=messaggio)
                            if andata == "ok"
                            else _('Ricerca non riuscita: {messaggio}', messaggio=messaggio), andata == "ok")
        else:
            self.nota.esito(_('Collegato: {messaggio}. Ora puoi staccare il cavo di rete.', messaggio=messaggio)
                            if andata == "ok"
                            else _('Non collegato: {messaggio}. Il nodo è ancora sul cavo: riprova.', messaggio=messaggio),
                            andata == "ok")


def wifi_dialog(parent, ponte, nome: str) -> bool:
    """Apre la finestra del Wi-Fi del nodo. True se la finestra si è aperta."""
    try:
        dlg = WifiDialog(nome, ponte, parent)
    except Exception as exc:  # noqa: BLE001
        QMessageBox.warning(parent, _('Wi-Fi del nodo'), _('Il nodo non risponde: {exc}', exc=exc))
        return False
    dlg.exec()
    return True
