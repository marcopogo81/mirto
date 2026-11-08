# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Finestra «Audio del nodo»: i dispositivi audio del nodo, uno per tab, senza aprire un terminale.

Fino al 22/10 questa finestra chiedeva «dove deve suonare la sirena», e sbagliava domanda. La sirena è
un'**azione** — scatta quando il video o l'audio la fanno scattare e i filtri dell'AI confermano — e
una casa può volerla dalla cassa di fuori mentre un dissuasore suona da quella del giardino. Il nodo
quindi ha dei **dispositivi**; chi sceglie dove suonare è l'azione.

Qui ci sono i dispositivi, uno per tab: **Jack**, **Bluetooth**, **USB**, e il **Microfono** (che è un
dispositivo anche lui, solo che registra invece di suonare). In fondo, fuori dalle tab, resta una riga
sola — «le azioni suonano da» — perché finché la scelta non sta dentro l'azione il nodo deve pur
sapere da dove suonare una sirena. Quella riga è destinata a sparire.

Una velocità sola, dall'8/11: **ogni gesto arriva al nodo quando lo si fa.** Prima erano due — le
manopole e il jack subito, il dispositivo delle azioni e il microfono con «Applica» — e quella metà
differenza era la causa di quasi tutto quello che confondeva: si abbinava una cassa e sembrava non
succedere niente, perché l'abbinamento partiva e la scelta di usarla no. Chi cambia qualcosa qui lo
vede succedere; chi gira fra le schede per guardare non cambia niente.

Il prezzo, detto dove serve: cambiare il dispositivo delle azioni o il microfono fa **riavviare il
servizio del nodo**, cioè mezzo minuto in cui la camera non c'è. Per questo si manda solo quello che
è davvero diverso da com'è adesso, e la finestra lo dice prima di farlo.

La ricerca e l'abbinamento di una cassa Bluetooth li fa il nodo da root e durano secondi: qui si
chiedono, e la finestra ripassa a vedere com'è andata invece di restare bloccata. Il «ponte» è
l'oggetto che parla col nodo — così questa finestra si costruisce e si prova anche senza un Raspberry
acceso, che è l'unico modo che ho di provarla.
"""

from __future__ import annotations

import html
import threading
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSlider,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import theme as T
from .attesa import Attesa
from .sistema import level_bar
from ..actions.pannello_audio import (attesa_scaduta, casse_ricordate, casse_trovate, chiave_manopola,
                                      chiave_uscita,
                                      esito_arrivato, etichetta_manopola, firma_esito, giudizio_livello,
                                      livello_da_pcm, microfono_da_mandare, picco_tenuto,
                                      quale_cassa_tenere, si_puo_provare, uscita_in_uso,
                                      uscite_per_le_azioni)
from ..i18n import _


# Il picco tenuto e le soglie stanno in `pannello_audio`: le usa anche la riga di *Sistema*.

COLORE_LIVELLO = {"bene": "ARMED", "piano": "PAUSED", "forte": "PAUSED",
                  "saturo": "ALARM", "silenzio": "MUTED"}


class AudioDialog(QDialog):
    """I dispositivi audio del nodo: jack, casse Bluetooth, casse USB, microfono."""

    # Il livello arriva da un thread che legge il flusso del nodo: un segnale è l'unico modo onesto di
    # portarlo all'interfaccia, che vive in un thread suo e non si tocca da fuori.
    livello_letto = Signal(float, float)

    def __init__(self, nome: str, ponte, parent=None):
        super().__init__(parent)
        self.ponte = ponte
        self.setWindowTitle(_('Audio del nodo'))
        self.setModal(True)
        self.stato = dict(ponte.stato() or {})
        # «getattr»: un nodo più vecchio non ha queste strade, e il ponte nemmeno. Chiedere
        # «ponte.dispositivi» romperebbe **qui**, prima ancora di provare a chiamarlo, e la finestra
        # non si aprirebbe affatto — per un nodo che va benissimo per tutto il resto.
        self.disp = self._chiedi_se_c_e("dispositivi") or {}
        self.manopole_dati = self._chiedi_se_c_e("manopole") or {}
        self.attesa = ""          # "cerca", "abbina" o "dimentica" mentre il nodo lavora
        # Il nodo tiene l'ultima ricerca in un file, e quel file sopravvive a tutto: senza questo,
        # aprendo la finestra si rivedeva l'elenco di giorni prima con dentro casse spente marcate
        # «da abbinare» (8/11). Si mostrano solo le ricerche fatte **dopo** che la finestra e' aperta,
        # cosi' l'elenco nasce vuoto, che e' la verita': nessuno ha ancora cercato.
        self.aperta_il = time.time()
        self.in_corso = ("", "")  # (indirizzo, nome) della cassa su cui si sta lavorando
        self.firma_prima = ()     # com'era l'esito del nodo quando si è cominciato ad aspettare
        self.attesa_s, self.inizio = 0, 0.0
        self.micro_prima = dict(self.stato.get("microfono") or {})
        self.uscita_prima = uscita_in_uso(self.disp)
        self._ascolto: threading.Thread | None = None
        self._ascolto_stop = threading.Event()
        self._picco_tenuto, self._picco_quando = 0.0, 0.0

        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.addWidget(QLabel(_("<b style='font-size:15px'>{escape}</b>",
                               escape=html.escape(_('Nodo {nome}', nome=nome)))))

        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)

        self.nota = Attesa()      # riga di note che sa anche dire «sto aspettando» e com'è andata
        lay.addWidget(self.nota)

        # --- l'uscita di questo nodo ------------------------------------------
        # Era «Le azioni suonano da», e dal 24/10 non è più vero: ogni azione sceglie la sua cassa
        # (API 1.10). Qui resta quello che questa finestra ha sempre configurato — l'uscita **del
        # nodo** — che serve ancora per due cose: le azioni che non dicono niente (tutte quelle
        # scritte finora) e il pulsante di prova qui accanto. Marco, 24/10: *«è rimasta la scritta da
        # rimuovere: lì serve solo a configurare l'audio del nodo»*.
        riga = QHBoxLayout()
        riga.addWidget(QLabel(_('Uscita predefinita del nodo:')))
        self.scelta_uscita = QComboBox()
        riga.addWidget(self.scelta_uscita, 1)
        self.prova_btn = QPushButton(_('  Suono di prova'))
        self.prova_btn.setIcon(T.icon("bell", T.MUTED, 18))
        riga.addWidget(self.prova_btn)
        lay.addLayout(riga)
        self.nota_uscita = QLabel(_('Ogni azione può scegliere da quale cassa suonare, pezzo per '
                                    'pezzo. Questa è quella che il nodo usa quando l\'azione non lo '
                                    'dice, ed è quella che prova il pulsante qui accanto.'))
        self.nota_uscita.setWordWrap(True)
        self.nota_uscita.setStyleSheet(f"color:{T.MUTED}; font-size:11px")
        lay.addWidget(self.nota_uscita)

        # Niente «Applica», niente «Annulla» (8/11). Ogni gesto di questa finestra arriva al nodo
        # **subito**: il jack, le manopole e l'abbinamento lo facevano gia', l'uscita e il microfono
        # no — e quella meta' differenza era la causa di tutto. Marco, provando: «sembrava si fosse
        # bloccato fino all'applica», e poi: «tutte le funzionalita' sono atomiche, non aspetti
        # applica sotto nella finestra altrimenti l'utente viene confuso».
        # Un «Annulla» qui sarebbe peggio di non averlo: prometterebbe di disfare cose che il nodo ha
        # gia' fatto — una cassa abbinata, un servizio riavviato — e non puo' mantenerlo.
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText(_("Chiudi"))
        buttons.rejected.connect(self.accept)
        lay.addWidget(buttons)

        self.scelta_uscita.currentIndexChanged.connect(lambda _i: self._uscita_cambiata())
        self.prova_btn.clicked.connect(self._prova)
        self.livello_letto.connect(self._livello_arrivato)
        self.tabs.currentChanged.connect(lambda _i: self._ascolto_forse())
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._ripassa)
        self._costruisci()
        self.resize(560, max(520, self.sizeHint().height()))

    # --- costruzione delle tab ----------------------------------------------
    def _costruisci(self) -> None:
        """Rifà le tab da com'è il nodo adesso, tenendo aperta quella che si stava guardando."""
        aperta = self.tabs.currentIndex()
        # Chi sceglie una cassa la deve ritrovare scelta. L'elenco si rifà dopo ogni ricerca e dopo
        # ogni tentativo, e il 21/10, su hardware, un abbinamento fallito aveva lasciato selezionato un
        # dispositivo BLE qualsiasi al posto della JBL scelta — e il tentativo dopo sarebbe andato su
        # quello.
        self._mac_da_tenere = self.mac_scelto()
        # Stessa cosa per il microfono: cambiarlo ricostruisce le tab (le manopole sono altre), e senza
        # ricordarsene la scelta tornerebbe indietro da sola sotto le dita di chi l'ha appena fatta.
        self._micro_da_tenere = self.micro_scelto()
        while self.tabs.count():
            vecchia = self.tabs.widget(0)
            self.tabs.removeTab(0)
            vecchia.deleteLater()
        self.tabs.addTab(self._tab_jack(), _('Jack'))
        self.tabs.addTab(self._tab_bluetooth(), _('Bluetooth'))
        self.tabs.addTab(self._tab_usb(), _('USB'))
        self.tabs.addTab(self._tab_microfono(), _('Microfono'))
        if 0 <= aperta < self.tabs.count():
            self.tabs.setCurrentIndex(aperta)
        self._riempi_uscite()
        self._mostra()
        self._ascolto_forse()

    def _pagina(self) -> tuple[QWidget, QVBoxLayout]:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(8)
        return w, lay

    def _nota_piccola(self, testo: str) -> QLabel:
        e = QLabel(testo)
        e.setWordWrap(True)
        e.setStyleSheet(f"color:{T.MUTED}; font-size:11px")
        return e

    def _tab_jack(self) -> QWidget:
        w, lay = self._pagina()
        j = dict(self.disp.get("jack") or {})
        if not j.get("presente"):
            lay.addWidget(QLabel(_('Questo nodo non ha un\'uscita jack.')))
            lay.addStretch(1)
            return w
        self.jack_on = QCheckBox(_('Usa l\'uscita jack 3,5 mm'))
        self.jack_on.setChecked(bool(j.get("attivo", True)))
        self.jack_on.toggled.connect(self._jack)
        lay.addWidget(self.jack_on)
        lay.addWidget(self._nota_piccola(
            _('Spenta, il jack sparisce dai dispositivi proponibili e la sirena non ci ripiega più '
              'sopra. Su un nodo che non ha niente attaccato al jack, ripiegarci vuol dire suonare in '
              'un buco senza che nessuno lo sappia.')))
        self._manopole_di(lay, str(j.get("scheda") or ""))
        lay.addStretch(1)
        return w

    def _tab_bluetooth(self) -> QWidget:
        """Due elenchi, non uno (8/11).

        Prima stavano insieme, «ricordate» e «viste passare», distinte da una scritta in coda. Marco,
        provando: *«l'elenco deve essere vuoto»*, e aveva ragione due volte. Sono due cose diverse —
        una cassa che il nodo **ha**, e una che si **sta cercando** — e su ciascuna si può fare una
        cosa sola: la prima si dimentica, la seconda si abbina. Mescolarle voleva dire due pulsanti
        accesi a turno sulla stessa riga, e un elenco che all'apertura mostrava la ricerca di giorni
        prima come se fosse adesso.
        """
        w, lay = self._pagina()

        lay.addWidget(QLabel(_('Casse che il nodo ricorda:')))
        self.casse = QListWidget()
        for c in casse_ricordate(self.disp.get("bluetooth")):
            voce = QListWidgetItem(f"{c['nome']} · {c['mac']}" + (_(' · collegata') if c["collegata"] else ""))
            voce.setData(Qt.ItemDataRole.UserRole, c)
            self.casse.addItem(voce)
        if not self.casse.count():
            lay.addWidget(self._nota_piccola(_('Nessuna: il nodo non ha ancora nessuna cassa Bluetooth.')))
        tenere = quale_cassa_tenere(self.in_corso[0], getattr(self, "_mac_da_tenere", ""),
                                    str((self.disp.get("predefinito") or {}).get("bluetooth_mac") or ""),
                                    [self.casse.item(i).data(Qt.ItemDataRole.UserRole)["mac"]
                                     for i in range(self.casse.count())])
        for i in range(self.casse.count()):
            if self.casse.item(i).data(Qt.ItemDataRole.UserRole)["mac"] == tenere:
                self.casse.setCurrentRow(i)
                break
        self.casse.currentRowChanged.connect(lambda _i: self._mostra())
        lay.addWidget(self.casse, 1)
        self.dimentica_btn = QPushButton(_('Dimentica'))
        self.dimentica_btn.clicked.connect(self._dimentica)
        riga = QHBoxLayout()
        riga.addStretch(1)
        riga.addWidget(self.dimentica_btn)
        lay.addLayout(riga)

        lay.addWidget(QLabel(_('Cerca una cassa nuova:')))
        self.trovate = QListWidget()
        for c in casse_trovate(self.disp.get("bluetooth"), self.aperta_il):
            voce = QListWidgetItem(f"{c['nome']} · {c['mac']}")
            voce.setData(Qt.ItemDataRole.UserRole, c)
            self.trovate.addItem(voce)
        self.trovate.currentRowChanged.connect(lambda _i: self._mostra())
        lay.addWidget(self.trovate, 1)
        self.cerca_btn = QPushButton(_('Cerca casse…'))
        self.abbina_btn = QPushButton(_('Abbina'))
        self.cerca_btn.clicked.connect(self._cerca)
        self.abbina_btn.clicked.connect(self._abbina)
        riga2 = QHBoxLayout()
        riga2.addWidget(self.cerca_btn)
        riga2.addStretch(1)
        riga2.addWidget(self.abbina_btn)
        lay.addLayout(riga2)
        lay.addWidget(self._nota_piccola(
            _('Accendi la cassa, mettila in abbinamento (il tasto Bluetooth finché lampeggia veloce) '
              'e premi «Cerca casse…». Abbinarla non la fa suonare: quale cassa usa il nodo si '
              'sceglie qui sotto, e per le sirene conviene il filo.')))
        return w

    def _tab_usb(self) -> QWidget:
        w, lay = self._pagina()
        casse = [c for c in (self.disp.get("usb") or []) if isinstance(c, dict)]
        hdmi = [c for c in (self.disp.get("hdmi") or []) if isinstance(c, dict)]
        if not casse and not hdmi:
            lay.addWidget(QLabel(_('Nessuna cassa USB collegata al nodo.')))
            lay.addWidget(self._nota_piccola(
                _('Attaccane una alla porta USB del Raspberry: compare da sola, senza abbinamenti.')))
            lay.addStretch(1)
            return w
        for c in casse + hdmi:
            lay.addWidget(QLabel(_("<b>{escape}</b>", escape=html.escape(str(c.get("nome") or c["scheda"])))))
            self._manopole_di(lay, str(c["scheda"]))
        lay.addWidget(self._nota_piccola(
            _('Se non si sente niente, controlla l\'interruttore della cassa: una cassa USB spenta resta '
              'collegata al nodo, e da qui non si distingue da una accesa.')))
        lay.addStretch(1)
        return w

    def _tab_microfono(self) -> QWidget:
        w, lay = self._pagina()
        self.microfoni = [m for m in (self.stato.get("microfoni") or []) if isinstance(m, dict)]
        self.scelta_micro = QComboBox()
        for m in self.microfoni:
            self.scelta_micro.addItem(str(m.get("etichetta") or m.get("scheda") or ""), m.get("scheda"))
        voluto = getattr(self, "_micro_da_tenere", "") or str(self.micro_prima.get("scheda") or "")
        for i, m in enumerate(self.microfoni):
            if str(m.get("scheda") or "") == voluto:
                self.scelta_micro.setCurrentIndex(i)
                break
        # L'etichetta dice **cosa fa questa spunta**: accende il microfono scelto, sul nodo. Diceva
        # «Ascolta i suoni (vetro rotto, allarme fumo, cane…)», che è quello che Mirto riconosce —
        # un'altra domanda, in un'altra scheda. Marco, 24/10: «qui l'impostazione è quale microfono
        # vuoi usare, se è attivo o spento e i suoi controlli».
        self.micro_on = QCheckBox(_('Usa questo microfono'))
        self.micro_on.setChecked(bool(self.micro_prima.get("attivo")))
        # La seconda metà della storia, che prima stava in «Suoni» ed era una sola per tutti i nodi:
        # il nodo accende il suo microfono, **Mirto** decide se ascoltarlo. Averle separate in due
        # schede diverse voleva dire un microfono acceso che non arrivava a nessuno, senza un motivo
        # scritto da nessuna parte (24/10).
        self.micro_ascolto = QCheckBox(_('Mirto ascolta questo microfono'))
        self._ascolto_prima = True
        if hasattr(self.ponte, "ascolto"):
            try:
                self._ascolto_prima = bool(self.ponte.ascolto())
            except Exception:  # noqa: BLE001
                self._ascolto_prima = True
        self.micro_ascolto.setChecked(self._ascolto_prima)
        self.micro_ascolto.setVisible(hasattr(self.ponte, "ascolta"))
        if not self.microfoni:
            self.scelta_micro.addItem(_('Nessun microfono collegato al nodo'), "")
            self.scelta_micro.setEnabled(False)
            self.micro_on.setEnabled(False)
        lay.addWidget(self.scelta_micro)
        lay.addWidget(self.micro_on)
        lay.addWidget(self.micro_ascolto)
        lay.addWidget(self._nota_piccola(
            _('L\'audio non viene salvato sul nodo: arriva a Mirto solo mentre lo sta ascoltando.')))
        self.scelta_micro.currentIndexChanged.connect(lambda _i: self._microfono_cambiato())
        self.micro_on.toggled.connect(lambda _b: self._microfono_cambiato())
        self.micro_ascolto.toggled.connect(self._ascolto_cambiato)
        self._manopole_di(lay, self.micro_scelto())

        # Il misuratore. Regolare un guadagno senza vedere il livello è regolare un volume a orecchie
        # tappate: la barretta è la stessa della colonna «Sistema», così le due cose dicono lo stesso.
        ascolta = bool(self.micro_prima.get("acceso_ora"))
        self.riga_livello = QLabel(_('Livello adesso:'))
        self.barra = QLabel(level_bar(0.0))
        self.barra.setStyleSheet("font-size:16px; letter-spacing:3px")
        self.giudizio = QLabel("")
        self.giudizio.setWordWrap(True)
        self.spento = self._nota_piccola(
            _('Il livello si vede quando il nodo sta ascoltando: spunta «Usa questo microfono» e '
              'riapri questa finestra.'))
        for widget in (self.riga_livello, self.barra, self.giudizio, self.spento):
            lay.addWidget(widget)
            widget.setVisible(ascolta if widget is not self.spento else not ascolta)
        lay.addStretch(1)
        return w

    # --- manopole -------------------------------------------------------------
    def _manopole_di(self, lay: QVBoxLayout, scheda: str) -> None:
        """Le manopole della scheda, se il nodo le sa dire (prima di API 1.6 non le sa)."""
        dati = dict((self.manopole_dati.get("schede") or {}).get(scheda) or {})
        for manopola in (dati.get("manopole") or []):
            if isinstance(manopola, dict):
                lay.addLayout(self._riga_manopola(scheda, manopola))

    def _riga_manopola(self, scheda: str, manopola: dict) -> QHBoxLayout:
        """Una manopola: nome, cursore se ha un volume, interruttore se ne ha uno."""
        chiave = chiave_manopola(manopola)
        riga = QHBoxLayout()
        nome = QLabel(etichetta_manopola(manopola))
        nome.setMinimumWidth(190)
        riga.addWidget(nome)
        if manopola.get("volume"):
            cursore = QSlider(Qt.Orientation.Horizontal)
            cursore.setRange(0, 100)
            cursore.setValue(int(manopola.get("percento") or 0))
            quanto = QLabel(f"{int(manopola.get('percento') or 0)} %")
            quanto.setMinimumWidth(42)
            quanto.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            cursore.valueChanged.connect(lambda v, e=quanto: e.setText(f"{v} %"))
            # Si manda al nodo quando si lascia il cursore, non a ogni pixel: un «amixer» per pixel
            # vorrebbe dire cento processi per una trascinata.
            cursore.sliderReleased.connect(lambda s=scheda, k=chiave, c=cursore: self._gira(s, k, f"{c.value()}%"))
            riga.addWidget(cursore, 1)
            riga.addWidget(quanto)
        else:
            riga.addStretch(1)
        if manopola.get("interruttore"):
            acceso = QCheckBox(_('acceso'))
            acceso.setChecked(bool(manopola.get("acceso", True)))
            acceso.toggled.connect(lambda b, s=scheda, k=chiave: self._gira(s, k, "on" if b else "off"))
            riga.addWidget(acceso)
        return riga

    def _gira(self, scheda: str, manopola: str, valore: str) -> None:
        nuove = self._chiedi(self.ponte.gira, scheda, manopola, valore)
        if nuove is not None:
            self.manopole_dati = dict(nuove)

    # --- stato della finestra ------------------------------------------------
    def cassa_scelta(self) -> dict:
        """La cassa scelta fra quelle **ricordate**: quella su cui si può premere «Dimentica»."""
        voce = self.casse.currentItem() if hasattr(self, "casse") else None
        return dict(voce.data(Qt.ItemDataRole.UserRole) or {}) if voce is not None else {}

    def cassa_trovata(self) -> dict:
        """La cassa scelta fra quelle **appena trovate**: quella da abbinare."""
        voce = self.trovate.currentItem() if hasattr(self, "trovate") else None
        return dict(voce.data(Qt.ItemDataRole.UserRole) or {}) if voce is not None else {}

    def mac_scelto(self) -> str:
        return str(self.cassa_scelta().get("mac") or "")

    def micro_scelto(self) -> str:
        return str(self.scelta_micro.currentData() or "") if hasattr(self, "scelta_micro") else ""

    def scelta_microfono(self) -> dict | None:
        """Cosa mandare al nodo per il microfono, o None se nessuno l'ha toccato."""
        return microfono_da_mandare(self.micro_prima, self.micro_on.isChecked(), self.micro_scelto())

    def scelta(self) -> dict:
        """Il dispositivo su cui il nodo deve suonare le azioni."""
        u = self.scelta_uscita.currentData() or {}
        return {"uscita": str(u.get("uscita", "")), "scheda": str(u.get("scheda", "")),
                "bluetooth_mac": str(u.get("mac", ""))}

    def scelta_uscita_cambiata(self) -> dict | None:
        """La riga «le azioni suonano da», solo se è cambiata: applicarla riavvia il servizio del nodo."""
        s = self.scelta()
        if not s["uscita"]:
            return None
        if chiave_uscita(s["uscita"], s["scheda"], s["bluetooth_mac"]) == self.uscita_prima:
            return None
        return s

    def _riempi_uscite(self) -> None:
        """L'elenco dei dispositivi su cui si può suonare, tenendo scelto quello che era scelto."""
        # Riempire la tendina **muove** l'indice, e da quando la scelta si applica da sola quel
        # movimento sarebbe indistinguibile da una scelta di chi guarda: il nodo si riavvierebbe da
        # solo ogni volta che la finestra si ridisegna. Qui si dice che stiamo riempiendo, non
        # scegliendo.
        self._sto_riempiendo = True
        try:
            prima = self.scelta_uscita.currentData() if self.scelta_uscita.count() else None
            voluta = chiave_uscita(**{k: str(prima.get(k, "")) for k in ("uscita", "scheda", "mac")}) if prima \
                else uscita_in_uso(self.disp)
            self.scelta_uscita.clear()
            for u in uscite_per_le_azioni(self.disp):
                self.scelta_uscita.addItem(str(u["etichetta"]), u)
            for i in range(self.scelta_uscita.count()):
                if str((self.scelta_uscita.itemData(i) or {}).get("chiave")) == voluta:
                    self.scelta_uscita.setCurrentIndex(i)
                    break
        finally:
            self._sto_riempiendo = False
        self._mostra()

    def _uscita_cambiata(self) -> None:
        """Scegliere un'uscita la manda al nodo, subito. Il nodo riavvia il suo servizio."""
        if getattr(self, "_sto_riempiendo", False):
            return
        s = self.scelta_uscita_cambiata()
        self._mostra()
        if s is None:
            return      # e' gia' quella: un riavvio per niente e' mezzo minuto di casa senza camera
        self.nota.inizia(_('Sto dicendo al nodo da dove suonare: riavvia il suo servizio, '
                           'la camera torna da sé.'), 30)
        nuovo = self._chiedi(self.ponte.imposta, s["uscita"], s["scheda"], s["bluetooth_mac"])
        if nuovo is None:
            self.nota.esito(_('Il nodo non ha accettato l\'uscita.'), False)
            self._riempi_uscite()        # torna a mostrare quella vera
            return
        self.uscita_prima = chiave_uscita(s["uscita"], s["scheda"], s["bluetooth_mac"])
        self.nota.esito(_('Fatto: il nodo suona da «{dove}».',
                          dove=str((self.scelta_uscita.currentData() or {}).get("etichetta") or "")), True)
        self._aggiorna_dispositivi()

    def _microfono_cambiato(self) -> None:
        """Accendere, spegnere o cambiare microfono arriva al nodo subito."""
        if getattr(self, "_sto_riempiendo", False):
            return
        m = self.scelta_microfono()
        if m is None:
            self._costruisci()   # non e' cambiato niente per il nodo: restano le manopole da rifare
            return
        if m["attivo"] and not m["scheda"]:
            QMessageBox.warning(self, _('Audio del nodo'),
                                _('Scegli quale microfono usare, oppure togli la spunta.'))
            return
        self.nota.inizia(_('Sto dicendo al nodo quale microfono usare: riavvia il suo servizio, '
                           'la camera torna da sé.'), 30)
        nuovo = self._chiedi(self.ponte.microfono, m["attivo"], m["scheda"])
        if nuovo is None:
            self.nota.esito(_('Il nodo non ha accettato il microfono.'), False)
            return
        self.micro_prima = dict((nuovo or {}).get("microfono") or self.micro_prima)
        self.nota.esito(_('Fatto.'), True)
        self._aggiorna_dispositivi()

    def _ascolto_cambiato(self, acceso: bool) -> None:
        """L'ascolto è una scelta di **questo computer**, non del nodo: non riavvia niente."""
        if getattr(self, "_sto_riempiendo", False) or not hasattr(self.ponte, "ascolta"):
            return
        try:
            self.ponte.ascolta(bool(acceso))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, _('Audio del nodo'),
                                _('Non sono riuscito a salvare l\'ascolto: {exc}', exc=exc))
            return
        self._ascolto_prima = bool(acceso)
        self._ascolto_forse()

    def _mostra(self) -> None:
        """Cosa si può premere, adesso. Unico posto che accende e spegne i pulsanti."""
        lavora = bool(self.attesa)
        if hasattr(self, "cerca_btn"):
            # Ogni pulsante guarda **il suo** elenco: niente più un pulsante acceso o spento a
            # seconda di cosa fosse selezionato in una lista mescolata.
            self.cerca_btn.setEnabled(not lavora)
            self.abbina_btn.setEnabled(not lavora and bool(self.cassa_trovata().get("mac")))
            self.dimentica_btn.setEnabled(not lavora and bool(self.cassa_scelta().get("mac")))
        # Il din-don esce da com'è configurato **il nodo**. Da quando la scelta si applica da sola le
        # due cose coincidono quasi sempre, ma non sempre: un'uscita scelta che il nodo ha rifiutato,
        # o una cassa che si è scollegata, restano casi in cui premere non farebbe succedere niente.
        # La regola sta in `si_puo_provare` e non qui: due copie, e una resta indietro.
        provabile = self.scelta_uscita.count() > 0 and si_puo_provare(self.stato, self.scelta())
        self.prova_btn.setEnabled(provabile and not lavora)
        self.prova_btn.setToolTip("" if provabile else
                                  _('Il nodo non sta ancora suonando da qui: scegli un\'uscita che '
                                    'abbia davvero un dispositivo collegato.'))

    # --- cose che chiedono al nodo ------------------------------------------
    def _chiedi(self, fn, *args, muto: bool = False):
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            return fn(*args)
        except Exception as exc:  # noqa: BLE001
            # «muto»: un nodo più vecchio non conosce questa strada, e non è un guasto — la finestra
            # deve restare utile per tutto il resto.
            if not muto:
                QMessageBox.warning(self, _('Audio del nodo'),
                                    _('Il nodo non ha accettato la richiesta: {exc}', exc=exc))
            return None
        finally:
            QApplication.restoreOverrideCursor()

    # --- il misuratore del microfono ----------------------------------------
    def _ascolto_forse(self) -> None:
        """Ascolta solo quando serve: tab del microfono aperta e nodo che sta ascoltando davvero.

        Un misuratore che resta attaccato al flusso mentre nessuno lo guarda occupa uno dei tre posti
        che il nodo concede, e tiene acceso un «arecord» per niente.
        """
        micro = self.tabs.count() > 3 and self.tabs.currentIndex() == 3
        puo = getattr(self.ponte, "flusso", None) is not None
        if micro and puo and self.micro_prima.get("acceso_ora"):
            if self._ascolto is None:
                self._ascolto_stop.clear()
                self._picco_tenuto, self._picco_quando = 0.0, 0.0
                self._ascolto = threading.Thread(target=self._ascolta, name="livello-microfono", daemon=True)
                self._ascolto.start()
        else:
            self._ferma_ascolto()

    def _ascolta(self) -> None:
        """Nel suo thread: legge il flusso del nodo e manda il livello all'interfaccia."""
        try:
            for pezzo in self.ponte.flusso(self._ascolto_stop):
                if self._ascolto_stop.is_set():
                    break
                media, picco = livello_da_pcm(pezzo)
                self.livello_letto.emit(media, picco)
        except Exception:  # noqa: BLE001
            # Il nodo ha chiuso, o la rete è caduta: si dice, non si fa finta che sia silenzio. Ma
            # non se siamo stati noi a staccare: lì la finestra sta chiudendo, e parlare a una
            # finestra che non c'è più non serve a nessuno.
            if not self._ascolto_stop.is_set():
                self.livello_letto.emit(-1.0, -1.0)

    def _ferma_ascolto(self) -> None:
        thread, self._ascolto = self._ascolto, None
        self._ascolto_stop.set()
        if thread is not None:
            thread.join(timeout=0.5)   # mezzo secondo: i pezzi sono da un decimo, non si resta appesi

    def _livello_arrivato(self, media: float, picco: float) -> None:
        if not hasattr(self, "barra"):
            return
        if picco < 0:
            self.giudizio.setText(_('Non riesco a leggere il livello: il nodo ha chiuso l\'ascolto.'))
            self.giudizio.setStyleSheet(f"color:{T.MUTED}; font-size:11px")
            return
        adesso = time.monotonic()
        self._picco_tenuto, self._picco_quando = picco_tenuto(picco, self._picco_tenuto,
                                                              self._picco_quando, adesso)
        self.barra.setText(level_bar(media))
        esito, consiglio = giudizio_livello(self._picco_tenuto)
        self.giudizio.setText(consiglio)
        self.giudizio.setStyleSheet(f"color:{getattr(T, COLORE_LIVELLO.get(esito, 'MUTED'))}; font-size:11px")

    def done(self, r: int) -> None:
        """Chiudendo si stacca dal flusso: un posto in meno occupato sul nodo."""
        self._ferma_ascolto()
        super().done(r)

    def _chiedi_se_c_e(self, nome: str):
        """Chiede al nodo una cosa che i nodi vecchi non sanno fare, in silenzio se non la sanno."""
        fn = getattr(self.ponte, nome, None)
        return self._chiedi(fn, muto=True) if fn is not None else None

    def _aspetta(self, azione: str, risposta: dict | None, testo: str, quanto: int) -> None:
        """Comincia ad aspettare il nodo: da qui in poi la finestra ripassa, con una fine."""
        self.attesa = azione
        self.firma_prima = firma_esito(self.stato.get("esito"))
        self.attesa_s = int((risposta or {}).get("attesa_s") or quanto)
        self.inizio = time.monotonic()
        self.nota.inizia(testo, self.attesa_s)
        self._mostra()
        self.timer.start(3000)

    def _jack(self, attivo: bool) -> None:
        nuovo = self._chiedi(self.ponte.jack, bool(attivo))
        if nuovo is None:
            return
        self.disp = dict(nuovo)
        self._riempi_uscite()
        self._mostra()

    def _cerca(self) -> None:
        risposta = self._chiedi(self.ponte.cerca)
        if risposta is None:
            return
        self._aspetta("cerca", risposta, _('Sto cercando: tieni la cassa accesa e in abbinamento.'), 20)

    def _abbina(self) -> None:
        cassa = self.cassa_trovata()
        if not cassa.get("mac"):
            return
        # Il nome si prende adesso: l'elenco può cambiare mentre il nodo lavora, e il risultato deve
        # parlare della cassa che è stata scelta, non di quella selezionata alla fine.
        self.in_corso = (str(cassa["mac"]), str(cassa.get("nome") or ""))
        risposta = self._chiedi(self.ponte.abbina, cassa["mac"])
        if risposta is None:
            self.in_corso = ("", "")
            return
        self._aspetta("abbina", risposta,
                      _('Sto abbinando la cassa: tienila vicina e in abbinamento (il tasto Bluetooth lampeggia veloce).'), 60)

    def _dimentica(self) -> None:
        cassa = self.cassa_scelta()
        if not cassa.get("mac"):
            return
        nome = str(cassa.get("nome") or cassa["mac"])
        if QMessageBox.question(self, _('Audio del nodo'),
                                _('Il nodo dimentica «{nome}». Per riusarla andrà abbinata di nuovo. Procedo?',
                                  nome=nome)) != QMessageBox.StandardButton.Yes:
            return
        self.in_corso = (str(cassa["mac"]), nome)
        risposta = self._chiedi(self.ponte.dimentica, cassa["mac"])
        if risposta is None:
            self.in_corso = ("", "")
            return
        self._aspetta("dimentica", risposta, _('Sto togliendo l\'abbinamento…'), 20)

    def _prova(self) -> None:
        if self._chiedi(self.ponte.prova) is not None:
            self.nota.setText(_('Din-don inviato: se non lo senti, controlla volume e cassa.'))

    def _ripassa(self) -> None:
        """Ogni tre secondi, finché il nodo sta lavorando: ha finito? Com'è andata?"""
        try:
            nuovo = dict(self.ponte.stato() or {})
        except Exception:  # noqa: BLE001
            return            # il nodo non risponde adesso: si riprova al giro dopo, senza allarmare
        self.stato = nuovo
        if not self.attesa:
            return
        if esito_arrivato(self.stato.get("esito"), self.firma_prima, self.attesa):
            self._finito(dict(self.stato.get("esito") or {}))
            return
        if attesa_scaduta(self.inizio, self.attesa_s, time.monotonic()):
            self.timer.stop()
            azione, self.attesa = self.attesa, ""
            self.nota.esito(_('Il nodo non ha risposto in tempo ({azione}). Riprova, o guarda se è acceso.',
                              azione=azione), False)
            self._aggiorna_dispositivi()

    def _finito(self, esito: dict) -> None:
        """Il nodo ha risposto: si smette di aspettare e si dice com'è andata."""
        self.timer.stop()
        azione, self.attesa = self.attesa, ""
        andata = str(esito.get("esito") or "")
        messaggio = str(esito.get("messaggio") or "")
        mac_in_corso, nome = self.in_corso[0], self.in_corso[1] or _('la cassa')
        self.in_corso = ("", "")
        self._aggiorna_dispositivi()
        if azione == "cerca":
            self.nota.esito(_('Ricerca finita: {messaggio}', messaggio=messaggio) if andata == "ok"
                            else _('Ricerca non riuscita: {messaggio}', messaggio=messaggio), andata == "ok")
        elif azione == "dimentica":
            self.nota.esito(_('«{nome}» non è più abbinata.', nome=nome) if andata == "ok"
                            else _('«{nome}» non si è potuta dimenticare: {messaggio}', nome=nome,
                                   messaggio=messaggio), andata == "ok")
        elif andata != "ok":
            self.nota.esito(_('«{nome}» non si è abbinata: {messaggio}', nome=nome,
                              messaggio=messaggio), False)
        else:
            # Abbinare non vuol dire usare, e dall'8/11 non lo fa nemmeno di nascosto: l'abbinamento
            # è un fatto di sistema, *quale* cassa suona è una scelta a parte. Marco: «la cassa
            # principale deve prediligere connessioni sicure come jack/usb» — quindi una cassa appena
            # abbinata non diventa l'uscita del nodo da sola. Il messaggio dice cos'è successo e dove
            # si sceglie, senza fare il passo al posto di chi guarda.
            self.nota.esito(_('«{nome}» è abbinata. Il nodo la ricorda: per farla suonare, sceglila '
                              'come uscita qui sotto.', nome=nome), True)

    def _aggiorna_dispositivi(self) -> None:
        """Ricarica i dispositivi dal nodo e rifà le tab: l'elenco delle casse è appena cambiato."""
        nuovo = self._chiedi_se_c_e("dispositivi")
        if nuovo is not None:
            self.disp = dict(nuovo)
        nuove = self._chiedi_se_c_e("manopole")
        if nuove is not None:
            self.manopole_dati = dict(nuove)
        self._costruisci()


def audio_dialog(parent, ponte, nome: str) -> dict | None:
    """Apre la finestra. Non manda piu' niente alla chiusura: l'ha gia' mandato tutto lei.

    Fino all'8/11 qui dentro c'era meta' del pannello: l'uscita delle azioni, il microfono e
    l'ascolto si raccoglievano nella finestra e partivano al momento di «Applica». Era il motivo per
    cui abbinare una cassa sembrava non fare niente — l'abbinamento partiva subito, la scelta di
    usarla no — e per cui chi girava fra le schede per guardare si ritrovava il nodo cambiato alla
    fine. Adesso ogni gesto arriva al nodo quando lo si fa, e qui resta solo l'ultimo stato
    conosciuto, per chi ha aperto la finestra e vuole sapere com'e' il nodo adesso.
    """
    try:
        dlg = AudioDialog(nome, ponte, parent)
    except Exception as exc:  # noqa: BLE001
        QMessageBox.warning(parent, _('Audio del nodo'), _('Il nodo non risponde: {exc}', exc=exc))
        return None
    dlg.exec()
    return dlg.disp or dlg.stato or None
