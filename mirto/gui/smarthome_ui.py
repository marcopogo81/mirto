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
    QScrollArea,
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
        fuori.addLayout(self._corpo(), 1)
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
        """A sinistra il broker, a destra quello che ci si trova dentro: la forma della scheda Nodi.

        Ed è la forma *giusta*, non una somiglianza: nei Nodi a sinistra c'è il nodo — la scatola a
        cui ti sei collegato — e a destra quello che quella scatola ha dentro, le sue camere e le
        sue azioni. Qui il broker **è** il nodo, e i dispositivi di casa sono il suo contenuto.
        Il 14/11 li avevo messi al contrario, e Marco: *«fai finta che il broker MQTT sia il nodo e
        i device che trova sono a destra — perché hai messo i controlli a sinistra?»*.
        """
        corpo = QHBoxLayout()
        corpo.setSpacing(14)
        colonna = QVBoxLayout()
        colonna.setSpacing(6)
        colonna.addWidget(self._titolo_colonna(_('IL BROKER DI CASA')))
        self.broker_cards = CardList(_('Nessun broker. Scrivi l\'indirizzo qui sopra e premi '
                                       '«Cerca i dispositivi di casa».'), selectable=False)
        colonna.addWidget(self.broker_cards)
        colonna.addStretch(1)
        sinistra = QWidget()
        sinistra.setLayout(colonna)
        sinistra.setFixedWidth(300)
        corpo.addWidget(sinistra)

        self.dettaglio = QWidget()
        destra = QVBoxLayout(self.dettaglio)
        destra.setContentsMargins(0, 0, 0, 4)
        destra.setSpacing(6)
        destra.addWidget(self._titolo_colonna(_('QUELLO CHE CI HA TROVATO')))
        # «compatta»: il dettaglio accanto al nome invece che sotto. Righe basse, e sei dispositivi
        # stanno in una pagina invece di tre. È la stessa forma dei suoni di un nodo.
        self.schede = CardList(_('Niente, per ora.'), selectable=False, compatta=True)
        destra.addWidget(self._in_un_rotolo(self.schede), 1)
        self.ascolta_btn = QPushButton(_('Cerca anche quelli che non si presentano'))
        self.ascolta_btn.setToolTip(
            _('Certi dispositivi non dicono chi sono: non compaiono da soli. Con questo Mirto resta '
              'a sentire tutto quello che passa sul broker e ti mostra chi parla, così li puoi '
              'riconoscere e dargli un nome.'))
        self.ascolta_btn.setCheckable(True)
        self.ascolta_btn.clicked.connect(self._ascolta)
        destra.addWidget(self.ascolta_btn)
        self.chi_parla = QLabel()
        self.chi_parla.setWordWrap(True)
        self.chi_parla.setStyleSheet(f"color:{T.FAINT}; font-size:{T.SIZE_SMALL}px;")
        destra.addWidget(self.chi_parla)
        self.grezzi = QGridLayout()
        destra.addLayout(self.grezzi)
        destra.addStretch(1)
        corpo.addWidget(self.dettaglio, 1)
        return corpo

    @staticmethod
    def _in_un_rotolo(dentro: QWidget, minimo: int = 230) -> QWidget:
        """Un elenco dentro un rotolo, con un'altezza minima sua.

        Senza, succede quello che Marco ha visto il 14/11: la scheda non ci sta in altezza — c'è
        anche il riquadro «Come fare» in fondo — e Qt comprime l'elenco sotto l'altezza delle sue
        righe, che si accavallano l'una sull'altra. Con un'altezza minima e un rotolo, le righe
        restano alte quanto devono e, se sono tante, si scorre.
        """
        rotolo = QScrollArea()
        rotolo.setWidgetResizable(True)
        rotolo.setFrameShape(QFrame.Shape.NoFrame)
        rotolo.setMinimumHeight(minimo)
        rotolo.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        rotolo.setStyleSheet("QScrollArea { background: transparent; }")
        ospite = QWidget()
        ospite.setStyleSheet("background: transparent;")
        dentro_lay = QVBoxLayout(ospite)
        dentro_lay.setContentsMargins(0, 0, 6, 0)
        dentro_lay.setSpacing(0)
        dentro_lay.addWidget(dentro)
        dentro_lay.addStretch(1)
        rotolo.setWidget(ospite)
        return rotolo

    @staticmethod
    def _titolo_colonna(testo: str) -> QLabel:
        riga = QLabel(testo)
        riga.setStyleSheet(f"color:{T.MUTED}; font-weight:800; font-size:11px; letter-spacing:1.4px; "
                           f"padding:2px 0 6px 2px; border-bottom:1px solid {T.LINE}; margin-top:6px;")
        return riga

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
        stato = self.domotica.stato()
        self.broker_cards.set_rows(self._riga_del_broker_card(stato))
        dispositivi = self.domotica.dispositivi()
        self._scelti = [d.identificativo for d in dispositivi]
        self.schede.set_rows([self._riga(d) for d in dispositivi])
        self._aggiorna_pronto(len(dispositivi))
        self._disegna_grezzi()

    def _riga_del_broker_card(self, stato: dict) -> list[dict]:
        """La scheda del broker: una sola, ed è il gemello della scheda di un nodo abbinato."""
        if not stato["attiva"]:
            return []
        dove = f"{stato['host']}:{self.porta.value()}"
        if not stato["collegata"]:
            return [{"title": dove, "sub": _('non risponde'), "dot": T.ALARM}]
        quanti = stato["quanti"]
        sub = (_('collegato · {quanti} dispositivi', quanti=quanti) if quanti
               else _('collegato · nessun dispositivo'))
        return [{"title": dove, "sub": sub, "dot": T.ARMED}]

    def _riga(self, d) -> dict:
        """Una scheda dell'elenco, con la stessa forma delle camere di un nodo."""
        if not d.vivo:
            colore, parola = T.ALARM, _('non risponde')
        elif d.acceso is None:
            colore, parola = T.MUTED, _('non lo so')
        elif d.acceso:
            colore, parola = T.ARMED, _('acceso')
        else:
            colore, parola = T.DISARMED, _('spento')
        pezzi = [parola]
        if d.adottato:
            pezzi.append(_('preso a orecchio'))
        elif d.marca and d.marca != SCONOSCIUTA:
            pezzi.append(html.escape(d.marca))
        if not d.comandabile:
            pezzi.append(_('si guarda soltanto'))
        bottoni = []
        if d.comandabile:
            bottoni.append((_('Spegni') if d.acceso else _('Accendi'),
                            lambda i=d.identificativo, a=not d.acceso: self._gira(i, a), False))
        if d.adottato:
            bottoni.append((_('Dimentica'), lambda i=d.identificativo: self._dimentica(i), True))
        return {"title": d.nome, "sub": " · ".join(pezzi), "dot": colore, "buttons": bottoni}

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
            self.chi_parla.setText(_('Sto a sentire… Per farsi sentire, un dispositivo deve dire qualcosa: '
                                     'accendilo o spegnilo dal suo interruttore.'))
            return
        self.chi_parla.setText(_('Sta parlando qualcuno che non si è presentato. Se riconosci uno di questi, '
                                 'dagli un nome e Mirto lo userà come gli altri.'))
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

    def _dimentica(self, identificativo: str) -> None:
        d = self.casa.dispositivo(identificativo) if self.casa else None
        if d is None:
            return
        self.casa.dimentica(identificativo)
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
