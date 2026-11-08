# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Finestra «Stato del nodo»: prima cosa non va, poi com'è fatto, poi il rapporto da incollare.

L'ordine è quello che serve a chi guarda: in cima i problemi col rimedio accanto, sotto i numeri. Al
contrario di come viene naturale scriverla — prima l'identità, poi i dettagli, e i guai da dedurre.

Dal 22/10 i numeri stanno in un **riquadro a caratteri fissi**, scuro, con le colonne allineate: sono
quindici righe «etichetta → valore», e incolonnate si scorrono con l'occhio invece di leggerle una per
una. È anche lo stesso testo che il pulsante copia, quindi quello che si vede è quello che si manda.
Il nome del nodo non c'è più in cima: la finestra si apre da quel nodo, dirlo di nuovo era una riga di
spazio buttata.

Il pulsante che copia il rapporto esiste per un motivo preciso: un tester che scrive «non va» ci costa
tre messaggi per capire cosa. Quel testo risponde a tutti e tre, non contiene segreti, e si incolla in
un messaggio senza sapere cos'è un registro di sistema.
"""

from __future__ import annotations

import html

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import theme as T
from .widgets import confirm_warning
from ..actions.diagnosi import ERRORE, diagnosi, rapporto, righe_stato, testo_console
from ..i18n import _

COLORE = {ERRORE: "#E0564C", "avviso": "#E8A33D"}

# L'attesa dopo «Aggiorna il nodo». I due minuti di prima erano tarati su un Pi 4: su un 3B
# `install.sh` ricostruisce la configurazione di MediaMTX e riavvia due servizi, e ci mette di più —
# il 26/10 la finestra si è arresa su un nodo che stava funzionando benissimo. Oltre `ATTESA_NORMALE_S`
# non si smette: si dice che ci sta mettendo di più e si guarda più piano.
ATTESA_PASSO_MS = 2000
ATTESA_PASSO_LENTO_MS = 5000
ATTESA_NORMALE_S = 300.0        # cinque minuti: un 3B lento ci sta dentro


def _da_quanto(secondi: float) -> str:
    """«40 s», «2 min»: da quanto si sta aspettando. Serve a far vedere che qualcosa si muove.

    Un'attesa muta si legge come un programma piantato, e chi la legge preme qualcosa — che è
    esattamente quello che non deve servire.
    """
    secondi = max(0.0, float(secondi))
    if secondi < 90:
        return _('{n} s', n=int(secondi))
    return _('{n} min', n=int(secondi // 60))


class StatoDialog(QDialog):
    """Lo stato di un nodo e i suoi problemi, con il rimedio accanto a ognuno."""

    def __init__(self, nome: str, ponte, parent=None, aggiorna_subito: bool = False):
        super().__init__(parent)
        self.ponte = ponte
        # Chi arriva qui dal pulsante «Aggiorna il nodo» di un'altra schermata ha gia' detto cosa
        # vuole: la finestra si apre e parte da li', invece di fargli cercare lo stesso pulsante una
        # seconda volta. La conferma la chiede lo stesso — e' l'unico punto dove si dice che il nodo
        # si riavvia (25/10).
        self._aggiorna_subito = bool(aggiorna_subito)
        self.setWindowTitle(_('Stato del nodo'))
        self.setModal(True)
        self.letto = dict(ponte.leggi() or {})
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        self.guai_box = QWidget()
        self.guai_lay = QVBoxLayout(self.guai_box)
        self.guai_lay.setContentsMargins(0, 0, 0, 0)
        self.guai_lay.setSpacing(8)
        lay.addWidget(self.guai_box)

        # Il riquadro dei numeri: scuro, a caratteri fissi, selezionabile ma non scrivibile. Non è
        # un vezzo — l'allineamento in colonne regge solo con caratteri di larghezza uguale.
        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.console.setStyleSheet(
            "QPlainTextEdit { background:#0B0E13; color:#E6EAF0; border:1px solid #27303D;"
            " border-radius:8px; padding:10px;"
            " font-family:'SF Mono','Menlo','Consolas','DejaVu Sans Mono',monospace; font-size:12px; }")
        self.console.setMinimumHeight(260)
        lay.addWidget(self.console, 1)

        lay.addSpacing(6)             # i pulsanti staccati dal riquadro: non sono parte di quel testo
        riga = QHBoxLayout()
        # Si chiamava «Aggiorna», e voleva dire «richiedi lo stato». Dal 25/10, accanto, c'è un
        # pulsante che aggiorna **il nodo**: due «Aggiorna» vicini sono una trappola, e quello che
        # rilegge si chiama come quello che fa — `Rileggi`.
        self.rileggi_btn = QPushButton(_('Rileggi'))
        self.rileggi_btn.setToolTip(_('Richiede di nuovo lo stato al nodo'))
        self.aggiorna_nodo_btn = QPushButton(_('Aggiorna il nodo'))
        self.aggiorna_nodo_btn.setToolTip(_('Manda al nodo il programma che questo computer ha dentro'))
        self.aggiorna_nodo_btn.hide()
        self.copia_btn = QPushButton(_("Copia per l'assistenza"))
        riga.addWidget(self.rileggi_btn)
        riga.addWidget(self.aggiorna_nodo_btn)
        riga.addWidget(self.copia_btn)
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

        self.rileggi_btn.clicked.connect(self._rileggi)
        self.aggiorna_nodo_btn.clicked.connect(self._aggiorna_il_nodo)
        self.copia_btn.clicked.connect(self._copia)
        self._attesa = None        # il controllo periodico mentre il nodo si reinstalla e riparte
        self._tentativi = 0
        # La versione che abbiamo appena mandato: serve a non prendere per questo tentativo l'esito
        # di quello di prima (26/10).
        self._versione_chiesta = ""
        self._mostra()
        self.resize(660, 560)
        if self._aggiorna_subito and self.da_aggiornare():
            # Dopo che la finestra e' in piedi, non durante: una domanda modale dentro `__init__`
            # lascia a meta' la finestra che la fa (e il 23/10 ha piantato la suite, una volta).
            QTimer.singleShot(0, self._aggiorna_il_nodo)

    # --- quello che si legge -------------------------------------------------
    def guai(self) -> list[dict]:
        return diagnosi(self.letto.get("info") or {}, self.letto.get("stato") or {},
                        self.letto.get("camera") or {})

    def righe(self) -> list[tuple[str, str]]:
        return righe_stato(self.letto.get("info") or {}, self.letto.get("stato") or {},
                           self.letto.get("camera") or {})

    def testo_rapporto(self) -> str:
        return rapporto(self.letto.get("info") or {}, self.letto.get("stato") or {},
                        self.letto.get("camera") or {})

    @staticmethod
    def _svuota(lay) -> None:
        while lay.count():
            voce = lay.takeAt(0)
            w = voce.widget()
            if w is not None:
                w.setParent(None)

    def _mostra(self) -> None:
        self.aggiorna_nodo_btn.setVisible(self.da_aggiornare())
        self._svuota(self.guai_lay)
        self.console.setPlainText(testo_console(self.righe()))
        guai = self.guai()
        if not guai:
            riga = QLabel(_('Nessun problema: il nodo fa tutto quello che deve.'))
            riga.setWordWrap(True)
            riga.setStyleSheet(f"color:{T.ARMED}; font-weight:600;")
            self.guai_lay.addWidget(riga)
        for g in guai:
            colore = COLORE.get(str(g.get("livello")), T.MUTED)
            testo = QLabel(_("<b style='color:{colore}'>{testo}</b>", colore=colore,
                             testo=html.escape(str(g.get("testo") or ""))))
            testo.setWordWrap(True)
            self.guai_lay.addWidget(testo)
            if g.get("rimedio"):
                rimedio = QLabel(html.escape(str(g["rimedio"])))
                rimedio.setWordWrap(True)
                rimedio.setStyleSheet(f"color:{T.MUTED}; font-size:12px; margin-left:10px;")
                self.guai_lay.addWidget(rimedio)

    # --- cose che chiedono al nodo ------------------------------------------
    def versione_sul_nodo(self) -> str:
        return str((self.letto.get("info") or {}).get("versione") or "")

    def da_aggiornare(self) -> bool:
        """Il nodo è più vecchio del programma che questo computer ha dentro?

        Si guarda la versione **del nodo**, non quella dell'API: l'API dice cosa sa fare, la versione
        dice cosa ha installato, e il pacchetto che mandiamo è esattamente quello. Senza il codice
        del nodo (può succedere in un pacchetto dell'app) non si offre niente: un pulsante che non
        può funzionare è peggio di un pulsante che non c'è.
        """
        from ..actions.aggiorna_nodo import aggiornamento_del_nodo  # noqa: PLC0415

        return aggiornamento_del_nodo(self.letto.get("info") or {}) is not None

    def _aggiorna_il_nodo(self) -> None:
        """Manda al nodo il codice che questo computer ha dentro (N-02).

        La risposta del nodo dice «in corso», non «fatto»: l'installazione riavvia il suo servizio,
        quindi una risposta «ho finito» non potrebbe mai arrivare. Da qui in poi si guarda, ogni due
        secondi, finché la versione cambia o il nodo racconta com'è andata.
        """
        from ..actions.aggiorna_nodo import fai_pacchetto  # noqa: PLC0415

        if not hasattr(self.ponte, "aggiorna"):
            return
        try:
            crudo, versione, sha = fai_pacchetto()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, _('Aggiorna il nodo'),
                                _('Non riesco a preparare il pacchetto: {exc}', exc=exc))
            return
        if not confirm_warning(
                self, _('Aggiorna il nodo'),
                _('Il nodo passa dalla versione {vecchia} alla {nuova}. Si riavvia: per un minuto '
                  'non risponde, e le azioni in corso si fermano.',
                  vecchia=self.versione_sul_nodo() or "?", nuova=versione),
                _('Aggiorna')):
            return
        self.aggiorna_nodo_btn.setEnabled(False)
        self.nota.setText(_('Mando il programma al nodo ({kb} KB)…', kb=len(crudo) // 1024))
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.ponte.aggiorna(crudo, versione, sha)
        except Exception as exc:  # noqa: BLE001
            self.aggiorna_nodo_btn.setEnabled(True)
            self.nota.setText("")
            QMessageBox.warning(self, _('Aggiorna il nodo'), _('Il nodo non l\'ha accettato: {exc}', exc=exc))
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.nota.setText(_('Il nodo sta installando e si riavvia. Aspetto…'))
        self._tentativi = 0
        self._versione_chiesta = versione
        self._attesa = QTimer(self)
        self._attesa.timeout.connect(self._guarda_se_e_tornato)
        self._attesa.start(ATTESA_PASSO_MS)

    def _guarda_se_e_tornato(self) -> None:
        """Aspetta che il nodo torni, **senza chiedere niente a chi guarda**.

        Marco, 26/10, aggiornando il nodo più lento (un 3B): *«ha scritto di premere RILEGGI per
        capire se era online perché non rispondeva più, ma è andato»*, e poi: *«si potrebbe cercare
        di non fargli fare rileggi, all'utente sembra una cosa che ha funzionato male»*. Aveva
        ragione due volte. I due minuti erano tarati su un Pi 4: su un 3B `install.sh` ricostruisce
        la configurazione di MediaMTX e riavvia due servizi, e ci mette di più. E un'attesa che
        finisce chiedendo di premere un pulsante racconta un guasto che non c'è: chi legge non sa che
        quel pulsante fa la stessa cosa che il programma sta già facendo da sé.

        Quindi: si guarda più a lungo, si dice **da quanto** e **cosa** sta succedendo, e non si
        smette mai di guardare finché la finestra è aperta. «Rileggi» resta, per chi lo vuole, ma
        nessun testo lo chiede.
        """
        self._tentativi += 1
        passati = self._tentativi * ATTESA_PASSO_MS / 1000
        try:
            letto = dict(self.ponte.leggi() or {})
        except Exception:  # noqa: BLE001  (si sta riavviando: è quello che ci aspettiamo)
            letto = {}
        if letto:
            self.letto = letto
            esito = dict((letto.get("stato") or {}).get("aggiornamento") or {})
            if not self.da_aggiornare():
                self._basta_aspettare()
                self.nota.setText(_('Fatto: il nodo è alla versione {v}.', v=self.versione_sul_nodo()))
                self._mostra()
                return
            # L'esito vale solo se è **di questo tentativo**. Il nodo lo cancella quando accetta un
            # pacchetto nuovo (API 1.13); un nodo più vecchio no, e allora ci si regola sulla
            # versione: un «fallito» rimasto dal tentativo di stamattina diceva che non era riuscito
            # mentre stava ancora installando.
            fallito = esito.get("esito") in ("fallito", "rotto")
            sua = str(esito.get("versione") or "")
            if fallito and (not sua or sua == self._versione_chiesta):
                self._basta_aspettare()
                self.nota.setText(_('Il nodo non è riuscito a installare: {msg}',
                                    msg=esito.get("messaggio") or _('motivo non detto')))
                self._mostra()
                return
            self.nota.setText(_('Il nodo risponde di nuovo e sta finendo l\'installazione… ({quanto})',
                                quanto=_da_quanto(passati)))
            return
        if passati < ATTESA_NORMALE_S:
            self.nota.setText(_('Il nodo sta installando e si riavvia. Aspetto… ({quanto})',
                                quanto=_da_quanto(passati)))
        else:
            # Più del solito, ma non è un errore e non c'è niente da premere: si continua a guardare,
            # più piano per non martellare un nodo che sta lavorando.
            self._attesa.setInterval(ATTESA_PASSO_LENTO_MS)
            self.nota.setText(_('Ci sta mettendo più del solito ({quanto}). Continuo a guardare: se il '
                                'nodo è acceso, finisce da sé.', quanto=_da_quanto(passati)))

    def _basta_aspettare(self) -> None:
        if self._attesa is not None:
            self._attesa.stop()
            self._attesa = None
        self.aggiorna_nodo_btn.setEnabled(True)

    def _rileggi(self) -> None:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.letto = dict(self.ponte.leggi() or {})
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, _('Stato del nodo'), _('Il nodo non risponde: {exc}', exc=exc))
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.nota.setText("")
        self._mostra()

    def _copia(self) -> None:
        testo = self.testo_rapporto()
        try:
            QApplication.clipboard().setText(testo)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, _('Stato del nodo'), _('Non riesco a copiare: {exc}', exc=exc))
            return
        self.nota.setText(_('Copiato: ora puoi incollarlo in un messaggio. Dentro non ci sono password né PIN.'))


def stato_dialog(parent, ponte, nome: str, aggiorna_subito: bool = False) -> bool:
    """Apre la finestra dello stato del nodo. True se la finestra si è aperta.

    ``aggiorna_subito``: la finestra si apre già sulla domanda dell'aggiornamento, per chi è
    arrivato qui premendo «Aggiorna il nodo» altrove.
    """
    try:
        dlg = StatoDialog(nome, ponte, parent, aggiorna_subito=aggiorna_subito)
    except Exception as exc:  # noqa: BLE001
        QMessageBox.warning(parent, _('Stato del nodo'), _('Il nodo non risponde: {exc}', exc=exc))
        return False
    dlg.exec()
    return True
