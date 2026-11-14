# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""La scheda «SmartHome»: il broker di casa, e i dispositivi che ci si trovano dentro.

**La regola che decide la forma di questa scheda: non si configura, si riconosce.** È lo stesso
patto dei nodi Raspberry. Qui dentro non si scrive mai un «argomento MQTT» come primo passo: si
preme un pulsante, e quello che c'è in casa compare con il suo nome e il suo stato, che si muove
sotto gli occhi mentre guardi.

La scrittura a mano esiste — è l'ultima riga, per chi sa quello che fa — ma sta in fondo, dove va.

**Tre vie, e si vedono tutte e tre.** Chi si annuncia compare da solo; chi parla solo la lingua
della sua marca compare lo stesso; chi non fa né l'una né l'altra si prende con *Ascolta tutto*, che
mostra chi sta parlando e lascia scegliere. L'ultima è brutta, e c'è perché senza di lei per
qualcuno Mirto sarebbe un vicolo cieco — e un vicolo cieco è la sola cosa che non ci si può
permettere a casa di un estraneo.

**Il filo.** I cambiamenti arrivano dal filo che legge dal broker: passano da un segnale Qt, come
gli avvisi dei nodi.
"""

from __future__ import annotations

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
from ..actions.smarthome import SCONOSCIUTA, CasaSmart
from ..i18n import _

log = logging.getLogger(__name__)


class _Ponte(QObject):
    cambiato = Signal()


class SchedaSmartHome(QWidget):
    """La scheda delle impostazioni. ``valori()`` dà quello che va salvato nella configurazione."""

    def __init__(self, cfg, secrets, campo_password: QLineEdit, parent=None, fabbrica=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.secrets = secrets
        self.password = campo_password
        self._fabbrica = fabbrica or CasaSmart
        self.casa: CasaSmart | None = None
        self._ponte = _Ponte()
        self._ponte.cambiato.connect(self._ridisegna)
        dati = cfg.get("smarthome") or {}

        fuori = QVBoxLayout(self)
        fuori.setSpacing(10)
        self.acceso = QCheckBox(_('Usa la domotica di casa (MQTT)'))
        self.acceso.setChecked(bool(dati.get("enabled")))
        fuori.addWidget(self.acceso)
        fuori.addWidget(self._riga_del_broker(dati))
        self.stato = QLabel()
        self.stato.setWordWrap(True)
        fuori.addWidget(self.stato)
        fuori.addWidget(self._elenco())
        fuori.addWidget(self._pronto())
        fuori.addWidget(self._grezzo())
        self._adottati = list(dati.get("adopted") or [])
        self._mostra_stato(_('Non collegato. Premi «Collegati e cerca».'), T.MUTED)

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
        self.collega_btn = QPushButton(_('Collegati e cerca'))
        self.collega_btn.setObjectName("primario")
        self.collega_btn.clicked.connect(self.collega)
        riga.addWidget(self.collega_btn)
        return scatola

    def _elenco(self) -> QWidget:
        rotolo = QScrollArea()
        rotolo.setWidgetResizable(True)
        rotolo.setMinimumHeight(190)
        dentro = QWidget()
        self.griglia = QGridLayout(dentro)
        self.griglia.setHorizontalSpacing(12)
        self.griglia.setVerticalSpacing(6)
        self.griglia.setColumnStretch(0, 1)
        rotolo.setWidget(dentro)
        return rotolo

    def _pronto(self) -> QWidget:
        """La riga «sei pronto», con due esempi scritti.

        Un rimando alla guida qui non serve a niente: chi è arrivato fin qui vuole sapere *adesso*
        cosa può farci, e se non glielo diciamo resta a guardare un elenco di lampadine senza capire
        che cosa ha appena ottenuto.
        """
        self.scatola_pronto = QFrame()
        self.scatola_pronto.setObjectName("pronto")
        self.scatola_pronto.setStyleSheet(
            f"QFrame#pronto {{ background: rgba(47,179,131,0.12); border-radius: 8px; }}")
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
    def collega(self) -> bool:
        """Si collega al broker e comincia a cercare. Dice sempre com'è andata, anche male."""
        from ..credentials import KEEP, SMARTHOME_PASSWORD  # noqa: PLC0415

        self.stacca()
        host = self.host.text().strip()
        if not host:
            self._mostra_stato(_("Scrivi prima l'indirizzo del broker."), T.PAUSED)
            self.host.setFocus()
            return False
        parola = self.password.text().strip()
        if not parola or parola == KEEP:        # il campo mostra il segnaposto: la vera è nel portachiavi
            parola = self.secrets.get(SMARTHOME_PASSWORD) or ""
        try:
            self.casa = self._fabbrica(
                host, int(self.porta.value()), utente=self.utente.text().strip() or None,
                password=parola or None, su_cambio=lambda _d: self._ponte.cambiato.emit())
            self.casa.collega()
        except Exception as exc:  # noqa: BLE001 - qualunque cosa vada storta, va detta qui
            self.casa = None
            self._mostra_stato(_('Non riesco a collegarmi: {exc}', exc=exc), T.ALARM)
            return False
        for voce in self._adottati:
            try:
                self.casa.adotta(str(voce.get("topic") or ""), str(voce.get("name") or ""),
                                 voce.get("command_topic"))
            except Exception:  # noqa: BLE001
                log.exception("smarthome: non ho potuto riprendere %s", voce)
        self._mostra_stato(_('Collegato a {host}. Sto cercando i dispositivi…', host=host), T.INFO)
        self._ridisegna()
        return True

    def stacca(self) -> None:
        if self.casa is not None:
            self.casa.chiudi()
            self.casa = None
        self.ascolta_btn.setChecked(False)

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
        self._svuota(self.griglia)
        dispositivi = self.casa.dispositivi() if self.casa else []
        if not dispositivi:
            vuoto = QLabel(_('Nessun dispositivo, per ora.') if self.casa
                           else _('Collegati per vedere cosa c\'è in casa.'))
            vuoto.setStyleSheet(f"color:{T.FAINT};")
            self.griglia.addWidget(vuoto, 0, 0)
        for r, d in enumerate(dispositivi):
            nome = QLabel(d.nome if d.vivo else _('{nome} (non risponde)', nome=d.nome))
            nome.setStyleSheet(f"color:{T.TEXT if d.vivo else T.FAINT};")
            self.griglia.addWidget(nome, r, 0)
            marca = QLabel(_('preso a orecchio') if d.adottato
                           else (d.marca if d.marca != SCONOSCIUTA else ""))
            marca.setStyleSheet(f"color:{T.MUTED}; font-size:{T.SIZE_SMALL}px;")
            self.griglia.addWidget(marca, r, 1)
            stato = QLabel(_('non lo so') if d.acceso is None
                           else (_('acceso') if d.acceso else _('spento')))
            stato.setStyleSheet(f"color:{T.ARMED if d.acceso else T.FAINT}; font-weight:600;")
            self.griglia.addWidget(stato, r, 2)
            if d.comandabile:
                gira = QPushButton(_('Spegni') if d.acceso else _('Accendi'))
                gira.setObjectName("small")
                gira.clicked.connect(
                    lambda _c=False, i=d.identificativo, a=not d.acceso: self._gira(i, a))
                self.griglia.addWidget(gira, r, 3)
            else:
                solo = QLabel(_('si guarda'))
                solo.setStyleSheet(f"color:{T.FAINT}; font-size:{T.SIZE_SMALL}px;")
                self.griglia.addWidget(solo, r, 3)
        self._aggiorna_pronto(len(dispositivi))
        self._disegna_grezzi()

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
        self._svuota(self.grezzi)
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

    @staticmethod
    def _svuota(griglia) -> None:
        while griglia.count():
            arnese = griglia.takeAt(0).widget()
            if arnese is not None:
                arnese.deleteLater()

    # --- quello che si può fare ----------------------------------------------
    def _gira(self, identificativo: str, acceso: bool) -> None:
        if self.casa is not None and not self.casa.comanda(identificativo, acceso):
            self._mostra_stato(_('Il comando non è partito.'), T.ALARM)

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
        self._ridisegna()

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
