# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""La scheda «SmartHome»: il broker di casa, e i dispositivi che ci si trovano dentro.

**La forma è quella della scheda Nodi, e non è un vezzo.** Pulsantone in cima che cerca, elenco a
sinistra in schede, dettaglio del selezionato a destra. Chi ha già abbinato un nodo sa già come si
usa questa, e chi impara qui sa già usare quella. Il 14/11 l'avevo inventata diversa — una tabella
tutta mia — e Marco l'ha fermata: *«guarda come è strutturata come layout, non ti inventare cose
nuove ogni volta»*. Aveva ragione due volte: una perché l'utente non deve imparare due dialetti, e
una perché una forma già fatta si scrive in meno tempo di una nuova.

**La regola che decide il resto: non si configura, si riconosce.** È lo stesso patto dei nodi. Qui
dentro non si scrive mai un «argomento MQTT» come primo passo: si preme un pulsante, e quello che
c'è in casa compare col suo nome e il suo stato, che si muove sotto gli occhi.

**La scheda guarda, non possiede.** Il collegamento vive nel nucleo (``Domotica``), e questa scheda
mostra quello che il nucleo **sta già** facendo. All'apertura non riparte da «non collegato»: era il
difetto del 14/11, con il riquadro *Sistema* che elencava i dispositivi e la scheda che diceva di
non aver trovato niente.

**Il filo.** I cambiamenti arrivano dal filo che legge dal broker: passano da un segnale Qt, come
gli avvisi dei nodi.
"""

from __future__ import annotations

import html
import logging

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import theme as T
from .cards import CardList, primary_button
from ..actions.smarthome import SCONOSCIUTA, Domotica
from ..i18n import _

log = logging.getLogger(__name__)


class _Ponte(QObject):
    cambiato = Signal()


class SchedaSmartHome(QWidget):
    """La scheda delle impostazioni. ``valori()`` dà quello che va salvato nella configurazione."""

    def __init__(self, cfg, secrets, campo_password: QLineEdit, parent=None, domotica=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.secrets = secrets
        self.password = campo_password
        # La domotica vive nel nucleo, non qui: questa scheda la configura e la guarda, non la
        # possiede. Se ne possedesse una sua, chiudendo la finestra sparirebbe.
        self.domotica = domotica if domotica is not None else Domotica(cfg, secrets)
        self._ponte = _Ponte()
        self._ponte.cambiato.connect(self._ridisegna)
        self._scelti: list[str] = []          # identificativi, nello stesso ordine delle schede
        dati = cfg.get("smarthome") or {}
        self._adottati = list(dati.get("adopted") or [])

        fuori = QVBoxLayout(self)
        fuori.setSpacing(8)
        self.acceso = QCheckBox(_('Usa la domotica di casa (MQTT)'))
        self.acceso.setChecked(bool(dati.get("enabled")))
        fuori.addWidget(self.acceso)
        fuori.addWidget(self._riga_del_broker(dati))
        self.cerca_btn = primary_button(_('  Cerca i dispositivi di casa'))
        self.cerca_btn.setIcon(T.icon("wifi", "#FFFFFF", 20))
        self.cerca_btn.clicked.connect(self.collega)
        fuori.addWidget(self.cerca_btn)
        self.stato = QLabel()
        self.stato.setWordWrap(True)
        fuori.addWidget(self.stato)
        fuori.addLayout(self._corpo())
        fuori.addWidget(self._grezzo())
        fuori.addWidget(self._pronto())
        self._mostra_adesso()

    # --- i pezzi --------------------------------------------------------------
    def _riga_del_broker(self, dati: dict) -> QWidget:
        scatola = QWidget()
        riga = QHBoxLayout(scatola)
        riga.setContentsMargins(0, 0, 0, 0)
        riga.setSpacing(8)
        riga.addWidget(QLabel(_('Broker')))
        self.host = QLineEdit(str(dati.get("host") or ""))
        self.host.setPlaceholderText(_("indirizzo del broker, per esempio 192.168.1.10"))
        self.host.setMinimumWidth(220)
        self.host.returnPressed.connect(self.collega)
        riga.addWidget(self.host, 1)
        self.porta = QSpinBox()
        self.porta.setRange(1, 65535)
        self.porta.setValue(int(dati.get("port") or 1883))
        self.porta.setFixedWidth(90)
        riga.addWidget(self.porta)
        self.utente = QLineEdit(str(dati.get("username") or ""))
        self.utente.setPlaceholderText(_("utente (se serve)"))
        self.utente.setFixedWidth(150)
        riga.addWidget(self.utente)
        riga.addWidget(self.password)
        return scatola

    def _corpo(self) -> QHBoxLayout:
        """Elenco a sinistra, dettaglio a destra: la stessa forma della scheda Nodi."""
        corpo = QHBoxLayout()
        corpo.setSpacing(14)
        colonna = QVBoxLayout()
        colonna.setSpacing(6)
        testa = QLabel(_('DISPOSITIVI DI CASA'))
        testa.setStyleSheet(f"color:{T.MUTED}; font-weight:800; font-size:11px; letter-spacing:1.4px; "
                            f"padding:2px 0 6px 2px; border-bottom:1px solid {T.LINE}; margin-top:6px;")
        colonna.addWidget(testa)
        self.schede = CardList(_('Nessun dispositivo. Scrivi l\'indirizzo del broker e premi '
                                 '«Cerca i dispositivi di casa».'))
        self.schede.current_changed.connect(lambda _r: self._mostra_scelto())
        colonna.addWidget(self.schede)
        colonna.addStretch(1)
        sinistra = QWidget()
        sinistra.setLayout(colonna)
        sinistra.setFixedWidth(300)
        corpo.addWidget(sinistra)

        self.dettaglio = QWidget()
        destra = QVBoxLayout(self.dettaglio)
        destra.setContentsMargins(0, 6, 0, 4)
        destra.setSpacing(6)
        self.d_nome = QLabel("")
        self.d_nome.setStyleSheet("font-weight:700; font-size:15px;")
        destra.addWidget(self.d_nome)
        self.d_riga = QLabel("")
        self.d_riga.setWordWrap(True)
        self.d_riga.setTextFormat(Qt.TextFormat.RichText)
        self.d_riga.setStyleSheet(f"color:{T.MUTED};")
        destra.addWidget(self.d_riga)
        self.d_gira = QPushButton("")
        self.d_gira.clicked.connect(self._gira_scelto)
        destra.addWidget(self.d_gira)
        self.d_togli = QPushButton(_('Dimentica questo dispositivo'))
        self.d_togli.setObjectName("danger")
        self.d_togli.clicked.connect(self._dimentica_scelto)
        destra.addWidget(self.d_togli)
        destra.addStretch(1)
        self.dettaglio.hide()
        corpo.addWidget(self.dettaglio, 1)
        return corpo

    def _pronto(self) -> QWidget:
        """La riga «sei pronto», con due esempi scritti.

        Un rimando alla guida qui non serve a niente: chi è arrivato fin qui vuole sapere *adesso*
        cosa può farci, e se non glielo diciamo resta a guardare un elenco di lampadine senza capire
        che cosa ha appena ottenuto.
        """
        self.scatola_pronto = QFrame()
        self.scatola_pronto.setObjectName("pronto")
        self.scatola_pronto.setStyleSheet(
            "QFrame#pronto { background: rgba(47,179,131,0.12); border-radius: 8px; }")
        dentro = QVBoxLayout(self.scatola_pronto)
        dentro.setContentsMargins(12, 10, 12, 10)
        self.riga_pronto = QLabel()
        self.riga_pronto.setWordWrap(True)
        self.riga_pronto.setTextFormat(Qt.TextFormat.RichText)
        dentro.addWidget(self.riga_pronto)
        self.scatola_pronto.hide()
        return self.scatola_pronto

    def _grezzo(self) -> QWidget:
        scatola = QWidget()
        colonna = QVBoxLayout(scatola)
        colonna.setContentsMargins(0, 0, 0, 0)
        colonna.setSpacing(6)
        riga = QHBoxLayout()
        self.ascolta_btn = QPushButton(_('Ascolta tutto per trovare il resto'))
        self.ascolta_btn.setToolTip(
            _('Per i dispositivi che non si annunciano e non parlano una lingua nota: Mirto sta a '
              'sentire tutto quello che passa e ti mostra chi parla.'))
        self.ascolta_btn.setCheckable(True)
        self.ascolta_btn.clicked.connect(self._ascolta)
        riga.addWidget(self.ascolta_btn)
        riga.addStretch(1)
        colonna.addLayout(riga)
        self.chi_parla = QLabel()
        self.chi_parla.setWordWrap(True)
        self.chi_parla.setStyleSheet(f"color:{T.FAINT}; font-size:{T.SIZE_SMALL}px;")
        colonna.addWidget(self.chi_parla)
        self.grezzi = QGridLayout()
        colonna.addLayout(self.grezzi)
        return scatola

    # --- il collegamento ------------------------------------------------------
    @property
    def casa(self):
        """La casa è quella del nucleo: la scheda la guarda, non la possiede."""
        return self.domotica.casa

    def _mostra_adesso(self) -> None:
        """All'apertura: mostra quello che il nucleo **sta già** facendo.

        Il difetto del 14/11: la scheda diceva «Non collegato» mentre il riquadro *Sistema*, a due
        centimetri di distanza, elencava i dispositivi trovati. Una delle due mentiva, e chi guarda
        non ha modo di sapere quale.
        """
        self.domotica.ascolta(self._cambiato)
        stato = self.domotica.stato()
        if stato["collegata"]:
            self._mostra_stato(_('Collegato a {host}.', host=stato["host"]), T.ARMED)
        elif stato["attiva"]:
            self._mostra_stato(_('Configurato su {host}, ma il broker non risponde. '
                                 'Premi «Cerca i dispositivi di casa» per riprovare.',
                                 host=stato["host"]), T.ALARM)
        else:
            self._mostra_stato(_('Non collegato. Premi «Cerca i dispositivi di casa».'), T.MUTED)
        self._ridisegna()

    def collega(self) -> bool:
        """Salva e si collega. **Salva prima**, e qui sta il punto.

        Il pulsante fa una cosa vera, quindi «Salva» sembra superfluo: chi lo preme, vede comparire
        le lampadine e chiude la finestra, se l'è persa tutta. È lo stesso difetto corretto sul
        nodo — *niente più «Applica»: ogni gesto arriva quando lo fai*.
        """
        from ..credentials import KEEP, SMARTHOME_PASSWORD  # noqa: PLC0415

        host = self.host.text().strip()
        if not host:
            self._mostra_stato(_("Scrivi prima l'indirizzo del broker."), T.PAUSED)
            self.host.setFocus()
            return False
        parola = self.password.text().strip()
        if parola and parola != KEEP:           # il segnaposto vuol dire «lascia quella che c'è»
            self.secrets.set(SMARTHOME_PASSWORD, parola)
        elif not parola:
            self.secrets.delete(SMARTHOME_PASSWORD)
        self.acceso.setChecked(True)            # collegarsi *è* dire che la vuoi usare
        self.cfg.set("smarthome", self.valori())
        try:
            self.cfg.save()
        except Exception as exc:  # noqa: BLE001
            log.warning("smarthome: configurazione non salvata (%s)", exc)
        if not self.domotica.riapri():
            self._mostra_stato(_('Non riesco a collegarmi: {exc}',
                                 exc=self.domotica.errore or host), T.ALARM)
            self._ridisegna()
            return False
        self._mostra_stato(_('Collegato a {host} e salvato. Sto cercando i dispositivi…', host=host),
                           T.ARMED)
        self.domotica.ascolta(self._cambiato)
        self._ridisegna()
        return True

    def _cambiato(self, _d) -> None:
        self._ponte.cambiato.emit()

    def stacca(self) -> None:
        """Smette di guardare. **Non** chiude il collegamento: quello sopravvive alla finestra."""
        self.domotica.non_ascoltare(self._cambiato)
        self.ascolta_btn.setChecked(False)
        if self.casa is not None:
            self.casa.ascolta_tutto(False)

    def _ascolta(self) -> None:
        if self.casa is None:
            self.ascolta_btn.setChecked(False)
            self._mostra_stato(_('Prima collegati al broker.'), T.PAUSED)
            return
        self.casa.ascolta_tutto(self.ascolta_btn.isChecked())
        self._ridisegna()

    def _mostra_stato(self, testo: str, colore: str) -> None:
        self.stato.setText(testo)
        self.stato.setStyleSheet(f"color:{colore};")

    # --- disegnare ------------------------------------------------------------
    def _ridisegna(self) -> None:
        dispositivi = self.domotica.dispositivi()
        self._scelti = [d.identificativo for d in dispositivi]
        self.schede.set_rows([self._riga(d) for d in dispositivi])
        self._mostra_scelto()
        self._aggiorna_pronto(len(dispositivi))
        self._disegna_grezzi()

    def _riga(self, d) -> dict:
        """Una scheda dell'elenco, con la stessa forma delle schede dei nodi."""
        if not d.vivo:
            colore, parola = T.ALARM, _('non risponde')
        elif d.acceso is None:
            colore, parola = T.MUTED, _('non lo so')
        elif d.acceso:
            colore, parola = T.ARMED, _('acceso')
        else:
            colore, parola = T.DISARMED, _('spento')
        marca = _('preso a orecchio') if d.adottato else (d.marca if d.marca != SCONOSCIUTA else "")
        sub = parola + (f" · {html.escape(marca)}" if marca else "")
        bottoni = []
        if d.comandabile:
            bottoni.append((_('Spegni') if d.acceso else _('Accendi'),
                            lambda i=d.identificativo, a=not d.acceso: self._gira(i, a), False))
        return {"title": d.nome, "sub": sub, "dot": colore, "buttons": bottoni}

    def _scelto(self):
        riga = self.schede.current_row()
        if not 0 <= riga < len(self._scelti):
            return None
        return self.domotica.casa.dispositivo(self._scelti[riga]) if self.domotica.casa else None

    def _mostra_scelto(self) -> None:
        d = self._scelto()
        self.dettaglio.setVisible(d is not None)
        if d is None:
            return
        self.d_nome.setText(d.nome)
        pezzi = []
        if d.marca and d.marca != SCONOSCIUTA:
            pezzi.append(_('Marca: {marca}', marca=html.escape(d.marca)))
        if d.adottato:
            pezzi.append(_('Preso a orecchio: quello che Mirto ne sa è un\'ipotesi, non un annuncio.'))
        pezzi.append(_('Dice il suo stato su <code>{argomento}</code>',
                       argomento=html.escape(d.argomento_stato)))
        if d.comandabile:
            pezzi.append(_('Ascolta i comandi su <code>{argomento}</code>',
                           argomento=html.escape(d.argomento_comando)))
        else:
            pezzi.append(_('Si guarda soltanto: non è una cosa che si accende.'))
        self.d_riga.setText("<br>".join(pezzi))
        self.d_gira.setVisible(bool(d.comandabile))
        if d.comandabile:
            self.d_gira.setText(_('Spegni') if d.acceso else _('Accendi'))
        self.d_togli.setVisible(bool(d.adottato))

    def _aggiorna_pronto(self, quanti: int) -> None:
        if not quanti:
            self.scatola_pronto.hide()
            return
        self.riga_pronto.setText(_(
            '<b>Ci siamo: {quanti} dispositivi.</b> Ora li puoi usare nelle <i>Azioni</i> e da '
            'Telegram. Per esempio: «la camera del vialetto riconosce il cane → accendi la luce '
            'delle scale», oppure «si accende la luce delle scale → fai partire la musica di '
            'benvenuto dalla cassa del nodo».', quanti=quanti))
        self.scatola_pronto.show()

    def _disegna_grezzi(self) -> None:
        while self.grezzi.count():
            arnese = self.grezzi.takeAt(0).widget()
            if arnese is not None:
                arnese.deleteLater()
        if self.casa is None or not self.ascolta_btn.isChecked():
            self.chi_parla.setText("")
            return
        sentiti = self.casa.chi_parla()
        if not sentiti:
            self.chi_parla.setText(_('Sto a sentire… Per farsi sentire, un dispositivo deve dire '
                                     'qualcosa: accendilo o spegnilo dal suo interruttore.'))
            return
        self.chi_parla.setText(_('Sta parlando qualcuno che non si è annunciato. Se riconosci uno '
                                 'di questi, dagli un nome e Mirto lo userà come gli altri.'))
        for r, (argomento, carico) in enumerate(sentiti[:12]):
            riga = QLabel(f"{argomento} = {carico.decode('utf-8', 'replace')[:40]}")
            riga.setStyleSheet(f"color:{T.MUTED}; font-size:{T.SIZE_SMALL}px;")
            self.grezzi.addWidget(riga, r, 0)
            prendi = QPushButton(_('Dagli un nome'))
            prendi.setObjectName("small")
            prendi.clicked.connect(lambda _c=False, a=argomento: self._adotta(a))
            self.grezzi.addWidget(prendi, r, 1)

    # --- quello che si può fare ----------------------------------------------
    def _gira(self, identificativo: str, acceso: bool) -> None:
        if not self.domotica.comanda(identificativo, acceso):
            self._mostra_stato(_('Il comando non è partito.'), T.ALARM)

    def _gira_scelto(self) -> None:
        d = self._scelto()
        if d is not None:
            self._gira(d.identificativo, not d.acceso)

    def _dimentica_scelto(self) -> None:
        d = self._scelto()
        if d is None or self.casa is None:
            return
        self.casa.dimentica(d.identificativo)
        self._adottati = [v for v in self._adottati if v.get("topic") != d.argomento_stato]
        self._salva()
        self._ridisegna()

    def _adotta(self, argomento: str) -> None:
        if self.casa is None:
            return
        nome, va = QInputDialog.getText(self, _('Che cos\'è?'),
                                        _('Un nome per «{argomento}»:', argomento=argomento))
        if not va or not nome.strip():
            return
        d = self.casa.adotta(argomento, nome.strip())
        self._adottati = [v for v in self._adottati if v.get("topic") != argomento]
        self._adottati.append({"topic": argomento, "name": nome.strip(),
                               "command_topic": d.argomento_comando})
        self._salva()
        self._ridisegna()

    def _salva(self) -> None:
        self.cfg.set("smarthome", self.valori())
        try:
            self.cfg.save()
        except Exception as exc:  # noqa: BLE001
            log.warning("smarthome: configurazione non salvata (%s)", exc)

    # --- salvare --------------------------------------------------------------
    def valori(self) -> dict:
        return {
            "enabled": self.acceso.isChecked(),
            "host": self.host.text().strip(),
            "port": int(self.porta.value()),
            "username": self.utente.text().strip(),
            "tls": bool((self.cfg.get("smarthome") or {}).get("tls")),
            "adopted": list(self._adottati),
        }
