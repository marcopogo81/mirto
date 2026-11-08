# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Finestra «Disco del nodo»: dove registra il nodo, quanto tiene, e come si prepara un disco nuovo.

È l'unico pannello che può **cancellare** qualcosa, e si vede: preparare un disco sta dietro un pulsante
separato, con una finestra che elenca cos'altro c'è dentro e che non si sblocca finché non si riscrive a
mano il nome del dispositivo. Nessun valore è preselezionato, così nessuno formatta premendo Invio.

Il resto — scegliere un disco già buono, accendere o spegnere la registrazione, i due limiti dell'anello
— è normale amministrazione: si applica col pulsante in basso.
"""

from __future__ import annotations

import html

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
    QSpinBox,
    QVBoxLayout,
)

from . import theme as T
from .attesa import Attesa
from ..i18n import _
from .testi import testo_audio_registrazione


def gb(byte) -> str:
    """Byte in gigabyte, come li scrivono i produttori dei dischi (quelli che si leggono sulla scatola)."""
    try:
        return _('{gb:.0f} GB', gb=float(byte) / 1e9)
    except (TypeError, ValueError):
        return "?"


class ConfermaDisco(QDialog):
    """La conferma di una formattazione: si sblocca solo riscrivendo il nome del dispositivo."""

    def __init__(self, disco: dict, parent=None):
        super().__init__(parent)
        self.dispositivo = str(disco.get("disk") or "")
        self.setWindowTitle(_('Preparare il disco'))
        self.setModal(True)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        titolo = QLabel(_("<b style='font-size:15px'>Si cancella tutto quello che c'è su {dispositivo}</b>",
                          dispositivo=html.escape(self.dispositivo)))
        titolo.setWordWrap(True)
        lay.addWidget(titolo)
        parti = [str(p[0]) + (_(' · montato in {dove}', dove=str(p[1])) if len(p) > 1 and p[1] else "")
                 for p in (disco.get("parts") or []) if p]
        dentro = QLabel(_('Il disco «{modello}» ({quanto}) contiene:\n{elenco}',
                          modello=str(disco.get("model") or "USB"), quanto=gb(disco.get("size")),
                          elenco="\n".join("    " + p for p in parti) or _('    (nessuna partizione)')))
        dentro.setWordWrap(True)
        dentro.setStyleSheet(f"color:{T.MUTED}; font-size:12px;")
        lay.addWidget(dentro)
        spiega = QLabel(_('Il disco viene riscritto da zero (ext4) e usato per le registrazioni. Non si torna indietro: se lì c\'è qualcosa che ti serve, copialo prima su un altro disco.'))
        spiega.setWordWrap(True)
        lay.addWidget(spiega)
        lay.addWidget(QLabel(_('Per confermare, riscrivi qui «{dispositivo}»:', dispositivo=self.dispositivo)))
        self.campo = QLineEdit()
        self.campo.setPlaceholderText(self.dispositivo)
        lay.addWidget(self.campo)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_btn.setText(_("Cancella e prepara"))
        self.ok_btn.setEnabled(False)
        self.ok_btn.setStyleSheet("QPushButton { background:#B3443B; color:#FFFFFF; font-weight:600; }")
        annulla = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        annulla.setText(_("Annulla"))
        annulla.setDefault(True)          # il tasto Invio annulla, non formatta
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.campo.textChanged.connect(lambda _t=None: self.ok_btn.setEnabled(self.pronto()))

    def pronto(self) -> bool:
        return self.campo.text().strip() == self.dispositivo and bool(self.dispositivo)


class DiscoDialog(QDialog):
    """Il disco delle registrazioni del nodo: quello in uso, quelli collegati, i limiti dell'anello."""

    def __init__(self, nome: str, ponte, parent=None):
        super().__init__(parent)
        self.ponte = ponte
        self.setWindowTitle(_('Disco del nodo'))
        self.setModal(True)
        self.stato = dict(ponte.stato() or {})
        self.attesa = ""
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.addWidget(QLabel(_("<b style='font-size:15px'>{escape}</b>", escape=html.escape(nome))))

        self.adesso = QLabel("")
        self.adesso.setWordWrap(True)
        self.adesso.setStyleSheet(f"color:{T.MUTED}; font-size:12px;")
        lay.addWidget(self.adesso)

        lay.addWidget(QLabel(_('Dischi USB collegati al nodo:')))
        riga = QHBoxLayout()
        self.dischi = QComboBox()
        self.cerca_btn = QPushButton(_('Cerca dischi…'))
        riga.addWidget(self.dischi, 1)
        riga.addWidget(self.cerca_btn)
        lay.addLayout(riga)

        riga2 = QHBoxLayout()
        self.usa_btn = QPushButton(_('Registra qui'))
        self.prepara_btn = QPushButton(_('Prepara il disco…'))
        self.prepara_btn.setStyleSheet("QPushButton { color:#E8A33D; }")
        riga2.addWidget(self.usa_btn)
        riga2.addWidget(self.prepara_btn)
        riga2.addStretch(1)
        lay.addLayout(riga2)

        self.attiva = QCheckBox(_('Registra sempre (ad anello: i file più vecchi si cancellano da soli)'))
        self.attiva.setChecked(bool(self.stato.get("attiva", True)))
        lay.addWidget(self.attiva)
        limiti = QHBoxLayout()
        limiti.addWidget(QLabel(_('Non superare')))
        self.massimo = QSpinBox()
        # Un tetto più grande del disco non vuol dire niente: il 21/10 la finestra ne proponeva 5 su un
        # disco da 4,1 GB (era il valore vecchio del nodo) e nessuno poteva accorgersene. Il massimo è
        # il disco stesso; se il valore che arriva dal nodo è più grande, si vede subito ridotto.
        rec = self.stato.get("registrazione") or {}
        quanto_sta = int(float(rec.get("totale") or 0) / 1e9)
        self.massimo.setRange(0, quanto_sta or 65536)
        self.massimo.setSuffix(_(' GB'))
        self.massimo.setSpecialValueText(_('nessun tetto'))
        self.massimo.setValue(int(self.stato.get("massimo_gb") or 0))
        if quanto_sta:
            self.massimo.setToolTip(_('Al massimo quanto il disco: {quanto} GB.', quanto=quanto_sta))
        limiti.addWidget(self.massimo)
        limiti.addWidget(QLabel(_('e tieni libero almeno')))
        self.minimo = QSpinBox()
        self.minimo.setRange(1, 50)
        self.minimo.setSuffix(_(' %'))
        self.minimo.setValue(int(self.stato.get("minimo_libero_percento") or 10))
        limiti.addWidget(self.minimo)
        limiti.addStretch(1)
        lay.addLayout(limiti)

        # Il microfono dentro il video registrato. Sta **qui**, fra i limiti dell'anello, perche' e'
        # una scelta sulla registrazione di questo nodo; e sta sotto una riga che dice cosa comporta,
        # perche' non e' una comodita' in piu' ma un cambio di politica: fino al 26/10 il programma
        # prometteva in tre posti che l'audio non viene mai registrato.
        audio = dict(self.stato.get("audio") or {})
        self.audio_prima = bool(audio.get("acceso"))
        self.audio = QCheckBox(_("Registra anche l'audio del microfono del nodo"))
        self.audio.setChecked(self.audio_prima)
        # Un nodo prima della 0.12.1 non ha quel campo nello stato: la spunta si **spegne**, invece
        # di lasciar premere una cosa che quel nodo rifiuterebbe con un 400. E' la stessa regola di
        # «Aggiorna il nodo», che compare solo se c'e' qualcosa da aggiornare.
        self.audio.setEnabled(bool(audio))
        lay.addWidget(self.audio)
        nota_audio = QLabel(testo_audio_registrazione(audio))
        nota_audio.setWordWrap(True)
        nota_audio.setStyleSheet(f"color:{T.MUTED}; font-size:12px;")
        lay.addWidget(nota_audio)

        self.nota = Attesa()        # riga di note che sa anche dire «sto aspettando» e com'è andata
        lay.addWidget(self.nota)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_btn.setText(_("Applica"))
        self.ok_btn.setStyleSheet(f"QPushButton {{ background:{T.ARMED}; color:#FFFFFF; font-weight:600; }}")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Chiudi"))
        buttons.accepted.connect(self._limiti)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

        self.cerca_btn.clicked.connect(self._cerca)
        self.usa_btn.clicked.connect(self._usa)
        self.prepara_btn.clicked.connect(self._prepara)
        self.dischi.currentIndexChanged.connect(self._aggiorna_pulsanti)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._ripassa)
        self._riempi()
        self.resize(560, self.sizeHint().height())

    # --- lettura dello stato -------------------------------------------------
    def _riempi(self) -> None:
        scelto = str(self.dischi.currentData() or "") if self.dischi.count() else ""
        self.dischi.blockSignals(True)
        self.dischi.clear()
        for d in (self.stato.get("dischi") or []):
            if not isinstance(d, dict) or not d.get("path"):
                continue
            formato = str(d.get("fstype") or "")
            if not int(d.get("size") or 0):
                # Un lettore di schede senza scheda dentro si presenta come un disco da 0 byte: il
                # 21/10, provandolo, la finestra offriva di «preparare» il nulla, e la formattazione
                # sarebbe morta con un errore incomprensibile.
                self.dischi.addItem(_('{path} · lettore senza scheda · {modello}', path=str(d["path"]),
                                      modello=str(d.get("model") or "USB")), str(d["path"]))
                continue
            testo = _('{path} · {quanto} · {formato} · {modello}', path=str(d["path"]), quanto=gb(d.get("size")),
                      formato=formato or _('da preparare'), modello=str(d.get("model") or "USB"))
            if d.get("libero") is not None:
                testo += _(' · {quanto} liberi', quanto=gb(d.get("libero")))
            self.dischi.addItem(testo, str(d["path"]))
        self.dischi.blockSignals(False)
        if scelto:
            i = self.dischi.findData(scelto)
            if i >= 0:
                self.dischi.setCurrentIndex(i)
        self._aggiorna_adesso()
        self._aggiorna_pulsanti()

    def _aggiorna_adesso(self) -> None:
        rec = self.stato.get("registrazione") or {}
        disco = str(self.stato.get("disco") or "")
        dove = _('sul disco USB {dove}', dove=disco) if disco else _('sulla microSD')
        pezzi = [_('Adesso registra {dove}.', dove=dove) if self.stato.get("attiva", True)
                 else _('Adesso la registrazione è spenta.')]
        if rec.get("libero") is not None:
            pezzi.append(_('{usato} di video, {libero} liberi su {totale}.', usato=gb(rec.get("usato")),
                           libero=gb(rec.get("libero")), totale=gb(rec.get("totale"))))
        if rec.get("errore"):
            pezzi.append(_('Problema: {errore}', errore=str(rec["errore"])))
        elif rec.get("registra") is False and self.stato.get("attiva", True):
            pezzi.append(_('In questo momento non sta registrando (camera spenta, o disco scollegato).'))
        self.adesso.setText(" ".join(pezzi))

    def disco_scelto(self) -> dict:
        path = str(self.dischi.currentData() or "")
        for d in (self.stato.get("dischi") or []):
            if isinstance(d, dict) and str(d.get("path") or "") == path:
                return d
        return {}

    def da_preparare(self) -> bool:
        d = self.disco_scelto()
        if not d:
            return False
        buoni = self.stato.get("formati_buoni") or ["ext4", "exfat", "vfat"]
        return str(d.get("fstype") or "") not in buoni

    def vuoto(self) -> bool:
        """Lettore senza scheda: un disco da 0 byte non si prepara e non ci si registra."""
        d = self.disco_scelto()
        return bool(d) and not int(d.get("size") or 0)

    def _aggiorna_pulsanti(self, *_a) -> None:
        c_e = bool(self.disco_scelto()) and not self.vuoto()
        self.usa_btn.setEnabled(c_e and not self.da_preparare() and not self.attesa)
        self.prepara_btn.setEnabled(c_e and not self.attesa)
        if self.attesa:
            return
        if self.vuoto():
            self.nota.setText(_('Questo è un lettore di schede senza scheda dentro (0 byte). Infila la scheda, stacca e riattacca il lettore, poi premi «Cerca dischi…».'))
        elif not (self.stato.get("dischi") or []):
            self.nota.setText(_('Nessun disco USB trovato: il nodo registra sulla microSD, con un tetto basso. Collega un SSD o una chiavetta e premi «Cerca dischi…». Il disco da cui parte il nodo non viene mai proposto.'))
        elif self.da_preparare():
            self.nota.setText(_('Questo disco ha un formato su cui non si può registrare: va preparato, e preparare cancella tutto quello che contiene.'))
        else:
            self.nota.setText(_('«Registra qui» sposta le registrazioni su questo disco; quelle vecchie restano dove sono.'))

    def limiti_scelti(self) -> dict:
        return {"attiva": self.attiva.isChecked(), "massimo_gb": self.massimo.value(),
                "minimo_libero_percento": self.minimo.value()}

    # --- cose che chiedono al nodo ------------------------------------------
    def _chiedi(self, fn, *args):
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            return fn(*args)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, _('Disco del nodo'), _('Il nodo non ha accettato la richiesta: {exc}', exc=exc))
            return None
        finally:
            QApplication.restoreOverrideCursor()

    def _aspetta(self, azione: str, testo: str, risposta=None, secondi: int = 20) -> None:
        self.attesa = azione
        for w in (self.cerca_btn, self.usa_btn, self.prepara_btn, self.ok_btn):
            w.setEnabled(False)
        try:
            secondi = int((risposta or {}).get("attesa_s") or secondi)
        except (TypeError, ValueError, AttributeError):
            pass
        self.nota.inizia(testo, secondi)
        self.timer.start(3000)

    def _cerca(self) -> None:
        risposta = self._chiedi(self.ponte.cerca)
        if risposta is None:
            return
        self._aspetta("cerca", _('Sto cercando i dischi collegati al nodo.'), risposta, 15)

    def _usa(self) -> None:
        d = self.disco_scelto()
        if not d or self.da_preparare():
            return
        risposta = self._chiedi(self.ponte.usa, str(d["path"]))
        if risposta is None:
            return
        self._aspetta("usa", _('Sto spostando le registrazioni su questo disco.'), risposta, 45)

    def _prepara(self) -> None:
        d = self.disco_scelto()
        if not d:
            return
        conferma = ConfermaDisco(d, self)
        if conferma.exec() != QDialog.DialogCode.Accepted or not conferma.pronto():
            return
        risposta = self._chiedi(self.ponte.prepara, conferma.dispositivo, conferma.campo.text().strip())
        if risposta is None:
            return
        self._aspetta("prepara", _('Sto preparando il disco. Non staccarlo.'), risposta, 300)

    def _limiti(self) -> None:
        # L'audio **prima** dei limiti, e solo se e' cambiato: il nodo lo rifiuta con un motivo
        # preciso (microfono spento, ffmpeg che manca) e quel motivo va letto, non sepolto sotto un
        # «sto salvando». Se lo rifiuta, la spunta torna com'era: una spunta che resta accesa su una
        # cosa che il nodo non ha accettato e' la bugia peggiore di tutte.
        if self.audio.isChecked() != self.audio_prima and getattr(self.ponte, "audio", None) is not None:
            if self._chiedi(self.ponte.audio, self.audio.isChecked()) is None:
                self.audio.setChecked(self.audio_prima)
                return
            self.audio_prima = self.audio.isChecked()
        scelta = self.limiti_scelti()
        risposta = self._chiedi(self.ponte.limiti, scelta["attiva"], scelta["massimo_gb"],
                                scelta["minimo_libero_percento"])
        if risposta is None:
            return
        self._aspetta("limiti", _('Sto salvando: il nodo riavvia la registrazione.'), None, 20)

    def _ripassa(self) -> None:
        """Ogni tre secondi, finché il nodo lavora: ha finito? Com'è andata?"""
        try:
            nuovo = dict(self.ponte.stato() or {})
        except Exception:  # noqa: BLE001
            return
        esito = nuovo.get("esito") or {}
        if str(esito.get("azione") or "") != self.attesa:
            return
        self.timer.stop()
        atteso, self.attesa = self.attesa, ""
        self.cerca_btn.setEnabled(True)       # i due che _riempi() non tocca
        self.ok_btn.setEnabled(True)
        self.stato = nuovo
        andata = str(esito.get("esito") or "")
        messaggio = str(esito.get("messaggio") or "")
        self._riempi()
        if andata == "ok":
            self.nota.esito(_('Fatto: {messaggio}', messaggio=messaggio) if atteso != "cerca"
                            else _('Ricerca finita: {messaggio}', messaggio=messaggio), True)
        else:
            self.nota.esito(_('Non riuscito: {messaggio}', messaggio=messaggio), False)


def disco_dialog(parent, ponte, nome: str) -> bool:
    """Apre la finestra del disco del nodo. True se la finestra si è aperta."""
    try:
        dlg = DiscoDialog(nome, ponte, parent)
    except Exception as exc:  # noqa: BLE001
        QMessageBox.warning(parent, _('Disco del nodo'), _('Il nodo non risponde: {exc}', exc=exc))
        return False
    dlg.exec()
    return True
