# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Finestra Impostazioni: generale, cosa sorvegliare, programmazione, presenza, notifiche (Telegram, email,
SMS) e nodi con le loro azioni.

Le credenziali inserite qui vanno direttamente nel portachiavi del sistema.
I pulsanti "di prova" girano in un thread separato per non bloccare l'interfaccia.
"""

from __future__ import annotations

import json
import threading

from PySide6.QtCore import QDate, QObject, Qt, QTime, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QFrame,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from .. import DISCUSSIONI_URL
from ..actions.aggiorna_nodo import aggiornamento_del_nodo
from ..actions.presets import (
    ACTION_KINDS,
    MANUAL_KINDS,
    describe_action,
    kind_from_node,
    load_actions,
)
from ..credentials import (
    EMAIL_PASSWORD,
    SMS_HTTP_API_KEY,
    SMS_TWILIO_TOKEN,
    TELEGRAM_TOKEN,
)
from ..notify.email import PRESETS
from . import theme as T
from .cards import settings_style
from .guide import aiuto_in_fondo, guide_link, help_box
from .testi import nodi_trovati, prova_si_sente
from .widgets import ElencoDiSpunte, GrigliaDiBox, GrigliaDiSpunte
from ..i18n import N_, _, questo_computer

KEEP = "••••••••"  # segnaposto: credenziale già salvata, lasciare invariata


def _split(text: str) -> list[str]:
    return [x.strip() for x in text.replace(";", ",").replace("\n", ",").split(",") if x.strip()]


def _pairing_parts(info: dict, nd: dict) -> list[str]:
    """«abbinato da Mirto su mac · il 18/10/2026 14:03», più l'avviso sulla chiave dell'installazione."""
    import html as _html  # noqa: PLC0415
    import time as _time  # noqa: PLC0415

    st = info.get("abbinamento") or {}
    who = str(st.get("da") or nd.get("abbinato_da") or "")
    out = []
    if who:
        when = st.get("quando")
        quando = _time.strftime("%d/%m/%Y %H:%M", _time.localtime(when)) if when else ""
        out.append(_('abbinato da {who}', who=_html.escape(who)) + (f" · {quando}" if quando else ""))
    if st.get("chiave_installazione"):
        left = int(st.get("ritiro_installazione_tra_s") or 0)
        when = (_('si ritira da sola tra {days} g', days=max(1, left // 86400)) if left
                else _('si ritira dopo il primo abbinamento con chiave propria'))
        # Il rimedio sta nel pannello, non in un terminale: «Altri computer…» stacca la chiave
        # dell'installazione come qualunque altra. Il comando resta scritto per chi è già sul nodo.
        out.append(_("<span style='color:{c}'>accetta ancora la chiave di installazione: toglila in "
                     "<a href='altri-computer' style='color:{c}'><b>Altri computer…</b></a> ({when})</span>",
                     c="#E8A33D", when=when))
    return out


class _Runner(QObject):
    done = Signal(object)  # (callback, risultato)


class SettingsDialog(QDialog):
    def __init__(self, core, parent=None, tests: list | None = None, tab: str = "", node_name: str = "",
                 aggiorna_nodo: bool = False, stato_nodo: bool = False):
        super().__init__(parent)
        self.core = core
        self._tests = tests or []  # [(testo, icona, funzione, spiegazione) | None]
        self.cfg = core.config
        self.secrets = core.secrets
        self.setWindowTitle(_('Impostazioni Mirto'))
        # Alla chiusura la finestra va distrutta: il parente è la finestra principale, quindi
        # altrimenti resta in vita (con i suoi thread e i suoi riferimenti) per tutta la sessione,
        # e ogni apertura ne lascia una in più (Q-06).
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setStyleSheet(settings_style())  # selezioni ben visibili in tutte le schede
        self._restore_size()
        self._runner = _Runner()
        self._runner.done.connect(lambda pair: pair[0](pair[1]))

        # Le azioni si leggono **prima** delle schede, non dentro una di esse: le guardano la scheda
        # «Azioni» e il conteggio sulla card di ogni nodo, e nessuna delle due deve dipendere da quale
        # scheda si costruisce per prima (24/10, tappa B).
        self._actions, self._azioni_scartate = load_actions(
            self.cfg.get("actions.items") or [],
            {str(n["id"]) for n in (self.cfg.get("actions.nodes") or [])})

        # Tenuta da parte: serve a chi deve sapere **quali schede ci sono** senza frugare fra i figli
        # del layout — le prove che controllano dove e' finita una cosa dopo un riordino (25/10).
        self.tabs = tabs = QTabWidget()
        tabs.addTab(aiuto_in_fondo(self._tab_general()), _("Generale"))
        # Una scheda sola per tutto quello che il programma **riconosce**, con dentro due
        # sottoschede. L'aiuto sta dentro ognuna delle due, non qui: sono due argomenti diversi.
        tabs.addTab(self._tab_ai(), _("AI"))
        tabs.addTab(aiuto_in_fondo(self._tab_recording()), _("Registrazione"))
        tabs.addTab(aiuto_in_fondo(self._tab_schedule()), _("Programmazione"))
        tabs.addTab(aiuto_in_fondo(self._tab_presence()), _("Presenza"))
        tabs.addTab(aiuto_in_fondo(self._tab_notifications()), _("Notifiche"))
        tabs.addTab(aiuto_in_fondo(self._tab_nodes_actions()), _("Nodi"))
        tabs.addTab(aiuto_in_fondo(self._tab_actions()), _("Azioni"))
        if self._tests:
            tabs.addTab(aiuto_in_fondo(self._tab_tests()), _("Prove"))
        for i in range(tabs.count()):  # apertura su una scheda precisa (es. dopo l'abbinamento di un nodo)
            if tab and tabs.tabText(i) in (tab, _(tab)):
                tabs.setCurrentIndex(i)
        for i, nd in enumerate(self.cfg.get("actions.nodes") or []):
            if node_name and nd.get("name") == node_name:
                self.nodes_cards.set_current_row(i)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(_("Salva"))
        buttons.button(QDialogButtonBox.StandardButton.Close).setText(_("Chiudi"))
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        self.salvato = QLabel("")
        self.salvato.setStyleSheet(f"color:{T.ARMED}; font-size:{T.SIZE_SMALL}px;")
        buttons.layout().insertWidget(0, self.salvato)
        lay = QVBoxLayout(self)
        lay.addWidget(tabs)
        lay.addWidget(buttons)
        # **Dopo** che le schede sono dentro la finestra, non prima. Qui stava il difetto vero dei
        # campi «enormi»: la misura cercava i campi fra i figli di `self`, e fino a `lay.addWidget`
        # il QTabWidget non è figlio di nessuno — quindi non ne trovava **neanche uno** e il tetto di
        # 200 pixel non è mai stato messo a niente. Era scritto giusto e non lo usava nessuno (25/10).
        self._misura_i_campi()
        if stato_nodo:
            # Da «Avvisi» della vista di flotta: si arriva qui per sapere **cosa** non va, e la
            # schermata che lo dice con il rimedio accanto e' questa. Aprirla da se' evita di far
            # ricominciare la ricerca da capo a chi ha gia' detto cosa vuole vedere.
            QTimer.singleShot(0, self._open_stato_dialog)
        if aggiorna_nodo:
            # Chi e' arrivato qui dal pulsante di un'altra schermata ha gia' detto cosa vuole: si
            # parte dalla domanda, invece di fargli cercare lo stesso pulsante una seconda volta.
            # Dopo che la finestra e' in piedi, non durante: una domanda modale dentro `__init__`
            # lascia a meta' la finestra che la fa.
            QTimer.singleShot(0, self._aggiorna_il_nodo_scelto)

    # --- dimensioni: calcolate sullo schermo la prima volta, poi ricordate ----------
    def _restore_size(self) -> None:
        from PySide6.QtCore import QSettings, QSize  # noqa: PLC0415

        screen = self.screen() or (self.parent().screen() if self.parent() else None)
        avail = screen.availableGeometry() if screen else None
        saved = QSettings("Mirto", "Mirto").value("settings_dialog/size")
        if isinstance(saved, QSize) and saved.isValid():
            w, h = saved.width(), saved.height()
        elif avail is not None:
            w, h = min(1100, int(avail.width() * 0.7)), min(920, int(avail.height() * 0.85))
        else:
            w, h = 1000, 800
        if avail is not None:  # mai più grande dello schermo (es. Mac collegato a un monitor diverso)
            w, h = min(w, avail.width() - 40), min(h, avail.height() - 40)
        self.setMinimumSize(760, 560)
        self.resize(max(w, 760), max(h, 560))

    def done(self, result):  # noqa: D401 - chiamato sia da Salva sia da Annulla
        from PySide6.QtCore import QSettings  # noqa: PLC0415

        QSettings("Mirto", "Mirto").setValue("settings_dialog/size", self.size())
        # Ogni timer va fermato: la finestra ha come parente quella principale, quindi non muore da
        # sola alla chiusura. Senza, aprire e chiudere Impostazioni dieci volte lascia dieci timer
        # che chiedono lo stato della registrazione ogni due secondi, per sempre (Q-06).
        for nome in ("_nodes_timer", "_rec_timer"):
            timer = getattr(self, nome, None)
            if timer is not None:
                timer.stop()
        super().done(result)

    # --- utilità ------------------------------------------------------------
    def _bg(self, fn, on_done):
        def work():
            try:
                res = fn()
            except Exception as exc:  # noqa: BLE001
                res = _('Errore: {exc}', exc=exc)
            self._runner.done.emit((on_done, res))
        threading.Thread(target=work, daemon=True).start()

    def _misura_i_campi(self) -> None:
        """Ogni campo numerico largo quanto il numero più lungo che può contenere, non di più.

        Prima il tetto era 200 pixel per tutti, e con `AllNonFixedFieldsGrow` tutti ci arrivavano:
        «0.95» in un campo da 200 pixel. Marco, 25/10: *«i tipi di input sono sempre enormi, ci si
        attende 3 caratteri e hanno lunghezza immensa»*. Un campo lungo è una promessa — dice «qui
        dentro ci va qualcosa di lungo» — e quando dentro ci va un numero di tre cifre la promessa
        è falsa, e l'occhio deve comunque attraversare il vuoto per leggere la cifra.

        La misura si prende dal campo stesso: il testo del valore massimo, più prefisso e suffisso,
        nel suo carattere. Così un campo che arriva a 3600 è più largo di uno che arriva a 0,95
        senza che nessuno scriva due numeri a mano, e resta giusto anche se domani cambia il font o
        la lingua (un suffisso tradotto può essere più lungo dell'italiano).
        """
        for kind in (QSpinBox, QDoubleSpinBox, QTimeEdit, QDateEdit):
            for campo in self.findChildren(kind):
                if campo.maximumWidth() <= 2000:
                    continue           # qualcuno l'ha già misurato a mano: non si tocca
                # Due misure, e vince la più larga: quello che c'è scritto adesso (è l'unica cosa che
                # un'ora o una data sappiano dire) e il valore massimo con prefisso e suffisso. Due e
                # non una perché se un domani `textFromValue` non fosse raggiungibile, la misura
                # ripiegherebbe sul valore di adesso — e un campo tarato su «120» taglia «3600».
                misure = [campo.text()]
                try:
                    misure.append(f"{campo.prefix()}{campo.textFromValue(campo.maximum())}{campo.suffix()}")
                except (AttributeError, TypeError):
                    pass
                largo = max(campo.fontMetrics().horizontalAdvance(m) for m in misure)
                # 46 = le frecce (30 di spazio riservato nel tema) più i bordi e il respiro ai lati.
                campo.setMaximumWidth(max(78, min(200, largo + 46)))

    def _info(self, text):
        QMessageBox.information(self, "Mirto", str(text))

    def _secret_field(self, name: str) -> QLineEdit:
        w = QLineEdit()
        w.setEchoMode(QLineEdit.EchoMode.Password)
        if self.secrets.get(name):
            w.setText(KEEP)
        return w

    def _store_secret(self, name: str, field: QLineEdit) -> None:
        val = field.text().strip()
        if val and val != KEEP:
            self.secrets.set(name, val)
        elif not val:
            self.secrets.delete(name)

    # --- schede ---------------------------------------------------------------
    def _tab_tests(self) -> QWidget:
        """Prove di invio, foto, nodi e test automatici (prima erano un pulsante nella colonna)."""
        from .theme import icon  # noqa: PLC0415

        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(help_box([
            _('<b>Avviso di prova completo</b>: controlla in un colpo Telegram, email ed SMS (parte anche a protezione disattivata).'),
            _('<b>Foto da tutte le camere</b>: per verificare le inquadrature.'),
            _('<b>Test automatici</b>: dopo un aggiornamento, per controllare che tutto funzioni.'),
        ], _("15. Prove e diagnostica"), self))
        for item in self._tests:
            if item is None:
                lay.addSpacing(8)
                continue
            text, icon_name, fn, tip = item
            row = QHBoxLayout()
            b = QPushButton("  " + text)
            b.setIcon(icon(icon_name, "#E8EDF3", 18))
            b.setMinimumWidth(320)
            b.clicked.connect(fn)
            row.addWidget(b)
            hint = QLabel(tip)
            hint.setWordWrap(True)
            hint.setStyleSheet("color:#6B7A8F;")
            row.addWidget(hint, 1)
            lay.addLayout(row)
        lay.addStretch(1)
        return w

    def _tab_general(self) -> QWidget:
        from ..system import autostart  # noqa: PLC0415

        w = QWidget()
        f = QFormLayout(w)
        f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        # **Prima la domanda, poi l'interruttore.** Le spunte avevano il testo attaccato a destra del
        # quadratino, mentre tutte le altre righe hanno l'etichetta a sinistra e il campo a destra:
        # due allineamenti diversi nella stessa colonna, che Marco ha chiamato «disturbante» (25/10).
        # Adesso la spunta e' nuda e sta nella colonna dei campi, incolonnata con le altre; quello
        # che non ci sta in una riga breve e' finito nel suggerimento, che si legge passandoci sopra.
        self.autostart = QCheckBox()
        self.autostart.setToolTip(_('Riparte da solo quando accendi il computer, e si riavvia se si chiude'))
        self.autostart.setChecked(autostart.is_enabled())
        self.prevent_sleep = QCheckBox()
        self.prevent_sleep.setToolTip(_('Un computer sospeso non sorveglia niente'))
        self.prevent_sleep.setChecked(bool(self.cfg.get("general.prevent_sleep", True)))
        from PySide6.QtCore import QSettings  # noqa: PLC0415

        self.close_background = QCheckBox()
        self.close_background.setToolTip(_('Per uscire davvero: Mirto → Esci da Mirto (⌘Q)'))
        self.close_background.setChecked(QSettings("Mirto", "Mirto").value("close/background", False, type=bool))
        from ..i18n import LANGUAGES, available_languages  # noqa: PLC0415

        self.language = QComboBox()
        for code in ["auto"] + available_languages():
            self.language.addItem(_(LANGUAGES.get(code, code)) if code == "auto" else LANGUAGES.get(code, code), code)
        self.language.setCurrentIndex(max(0, self.language.findData(self.cfg.get("general.language", "auto"))))
        f.addRow(_("Lingua / Language"), self.language)
        f.addRow(_("Avvia Mirto all'accesso"), self.autostart)
        f.addRow(_('Impedisci la sospensione del computer'), self.prevent_sleep)
        f.addRow(_('Chiudendo la finestra resta acceso in background'), self.close_background)
        # Qui sono rimaste le cose che valgono per **tutto il programma**. Se ne sono andate, il
        # 25/10: il modello e la soglia delle persone in AI (un modello e la sua soglia sono la
        # stessa decisione); la pausa fra due avvisi in AI → Camere, perche' riguarda una camera che
        # riconosce; e «conserva foto ed eventi» in Registrazione, che e' dove si decide quanto
        # tenere le cose. Una scheda «Generale» che contiene un po' di tutto non e' generale: e' il
        # posto dove finisce quello che non si e' deciso dove mettere.
        tel = getattr(self.core, "telemetry", None)
        self.tm_stats = QCheckBox()
        self.tm_stats.setToolTip(_("Una volta al giorno, senza foto, nomi né indirizzi"))
        self.tm_stats.setChecked(bool(self.cfg.get("telemetry.stats")))
        self.tm_updates = QCheckBox()
        self.tm_updates.setChecked(bool(self.cfg.get("telemetry.update_check", True)))
        if tel is None or not tel.available():  # nessun server configurato: non parte niente
            for cb in (self.tm_stats, self.tm_updates):
                cb.setEnabled(False)
                cb.setToolTip(_("Server delle statistiche non ancora configurato: Mirto non invia niente"))
        tm_row = QHBoxLayout()
        b_prev = QPushButton(_("Cosa viene inviato…"))
        b_new = QPushButton(_("Nuovo ID casuale"))
        b_new.setToolTip(_("Il numero vecchio viene cancellato dal server; da qui in poi conta come una installazione nuova"))
        b_new.setEnabled(bool(self.cfg.get("telemetry.stats")) and tel is not None)
        self.tm_stats.toggled.connect(lambda on: b_new.setEnabled(on and bool(self.cfg.get("telemetry.stats"))))

        def _preview():
            from .telemetry_ui import PreviewDialog  # noqa: PLC0415

            PreviewDialog(self.core, self).exec()

        def _new_id():
            tel.reset_id()
            QMessageBox.information(self, _("Statistiche d'uso"), _("Creato un nuovo ID casuale; quello vecchio sarà cancellato dal server."))

        b_prev.clicked.connect(_preview)
        b_new.clicked.connect(_new_id)
        tm_row.addWidget(b_prev)
        tm_row.addWidget(b_new)
        tm_row.addStretch(1)
        f.addRow(_("Invia statistiche d'uso"), self.tm_stats)
        f.addRow(_('Controlla se esce una nuova versione di Mirto'), self.tm_updates)
        f.addRow(tm_row)
        f.addRow(help_box([
            _("<b>Avvia Mirto all'accesso</b>: riparte da solo quando accendi il {computer} o se si chiude.",
              computer=questo_computer()),
            _('Lascia attivo <b>Impedisci la sospensione</b> e tieni il coperchio aperto: un computer sospeso non sorveglia.'),
            _("<b>Statistiche d'uso</b>: aiutano a sapere quante installazioni sono attive; «Cosa viene inviato…» mostra il messaggio esatto. Non partono finché non le spunti."),
            _('Il <b>modello di riconoscimento</b> e le <b>soglie</b> stanno in <b>AI</b>; <b>per quanto tenere foto ed eventi</b> in <b>Registrazione</b>.'),
        ], _('14. Impostazioni generali e funzionamento continuo'), self))
        return w

    def _tab_telegram(self) -> QWidget:
        tg = self.cfg.get("notifications.telegram") or {}
        w = QWidget()
        f = QFormLayout(w)
        f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.tg_enabled = QCheckBox(_('Invia gli avvisi su Telegram (canale principale)'))
        self.tg_enabled.setChecked(bool(tg.get("enabled")))
        self.tg_token = self._secret_field(TELEGRAM_TOKEN)
        self.tg_chat = QLineEdit(str(tg.get("chat_id", "")))
        self.tg_chat.setPlaceholderText(_("es. -1001234567890 (supergruppo)"))
        self.tg_users = QLineEdit(", ".join(str(u) for u in tg.get("allowed_user_ids", [])))
        self.tg_users.setPlaceholderText(_('ID utenti autorizzati, separati da virgola'))
        test = QPushButton(_('Invia messaggio di prova'))
        test.clicked.connect(self._test_telegram)
        help_ = help_box([
            _('Su Telegram apri <b>@BotFather</b> → <b>/newbot</b>, scegli nome e username e copia il <b>token</b> qui sopra.'),
            _('Crea il gruppo <b>Mirto Admin</b> e aggiungi il bot e i familiari.'),
            _('Nel gruppo: Modifica → <i>Cronologia chat per i nuovi membri</i> → <b>Visibile</b> (diventa supergruppo, serve per il link nelle email).'),
            _("Scrivi <b>/id</b> nel gruppo: l'ID chat (inizia con -100) va in <i>ID del gruppo</i>, il tuo ID utente in <i>Utenti autorizzati</i>."),
            _('Premi <b>Invia messaggio di prova</b>, poi <b>Salva</b>.'),
        ], _('7.1 Telegram'), self)
        f.addRow(self.tg_enabled)
        f.addRow(_('Token del bot'), self.tg_token)
        f.addRow(_('ID del gruppo'), self.tg_chat)
        f.addRow(_('Utenti autorizzati'), self.tg_users)
        f.addRow(test)
        f.addRow(help_)
        return w

    def _tab_email(self) -> QWidget:
        em = self.cfg.get("notifications.email") or {}
        w = QWidget()
        f = QFormLayout(w)
        f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.em_enabled = QCheckBox(_('Invia email con foto allegata e link a Telegram'))
        self.em_enabled.setChecked(bool(em.get("enabled")))
        self.em_provider = QComboBox()
        for key, p in PRESETS.items():
            self.em_provider.addItem(_(p["label"]), key)
        self.em_provider.setCurrentIndex(max(0, self.em_provider.findData(em.get("provider", "gmail"))))
        self.em_host = QLineEdit(em.get("host", "")); self.em_host.setPlaceholderText(_('dal preset'))
        self.em_port = QSpinBox(); self.em_port.setRange(0, 65535); self.em_port.setValue(int(em.get("port") or 0))
        self.em_user = QLineEdit(em.get("username", "")); self.em_user.setPlaceholderText("tuoindirizzo@gmail.com")
        self.em_pass = self._secret_field(EMAIL_PASSWORD)
        self.em_pass.setPlaceholderText(_('per Gmail: password per app di 16 caratteri'))
        self.em_to = QLineEdit(", ".join(em.get("recipients", [])))
        self.em_to.setPlaceholderText(_('indirizzi dei Mirto Admin'))
        test = QPushButton(_('Invia email di prova'))
        test.clicked.connect(self._test_email)
        help_ = help_box([
            _('Attiva la <b>verifica in due passaggi</b> sul tuo account Google.'),
            _('Apri <b>myaccount.google.com/apppasswords</b>, crea la password <b>Mirto</b> e copia le 16 lettere.'),
            _('Qui: provider <b>Gmail</b>, utente = il tuo indirizzo, password = le 16 lettere <b>senza spazi</b>.'),
            _('Scrivi i destinatari separati da virgola, premi <b>Invia email di prova</b> e poi <b>Salva</b>.'),
        ], _('7.2 Email'), self)
        f.addRow(self.em_enabled)
        f.addRow(_("Provider"), self.em_provider)
        f.addRow(_('Server SMTP (solo personalizzato)'), self.em_host)
        f.addRow(_('Porta (0 = dal preset)'), self.em_port)
        f.addRow(_("Utente"), self.em_user)
        f.addRow(_("Password"), self.em_pass)
        f.addRow(_("Destinatari"), self.em_to)
        f.addRow(test)
        f.addRow(help_)
        return w

    def _tab_sms(self) -> QWidget:
        sms = self.cfg.get("notifications.sms") or {}
        http = sms.get("http", {})
        w = QWidget()
        f = QFormLayout(w)
        f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.sms_enabled = QCheckBox(_('Attiva gli SMS (canale di riserva, a pagamento)'))
        self.sms_enabled.setChecked(bool(sms.get("enabled")))
        self.sms_provider = QComboBox()
        for label, key in ((_('Provider HTTP generico'), "http"), ("Twilio", "twilio"), ("Email-to-SMS", "email2sms")):
            self.sms_provider.addItem(label, key)
        self.sms_provider.setCurrentIndex(max(0, self.sms_provider.findData(sms.get("provider", "http"))))
        self.sms_mode = QComboBox()
        self.sms_mode.addItem(_('Per ogni evento'), "always")
        self.sms_mode.addItem(_('Solo se Telegram non risponde'), "fallback")
        self.sms_mode.setCurrentIndex(max(0, self.sms_mode.findData(sms.get("mode", "always"))))
        self.sms_limit = QSpinBox(); self.sms_limit.setRange(1, 500); self.sms_limit.setValue(int(sms.get("daily_limit", 20)))
        self.sms_to = QLineEdit(", ".join(sms.get("recipients", []))); self.sms_to.setPlaceholderText("+39333…")
        self.sms_url = QLineEdit(http.get("url", "")); self.sms_url.setPlaceholderText("https://… {numero} {testo} {api_key}")
        self.sms_method = QComboBox(); self.sms_method.addItems(["POST", "GET"])
        self.sms_method.setCurrentText(http.get("method", "POST"))
        self.sms_ctype = QLineEdit(http.get("content_type", "application/json"))
        self.sms_headers = QLineEdit(json.dumps(http.get("headers", {}), ensure_ascii=False))
        self.sms_body = QPlainTextEdit(http.get("body", "")); self.sms_body.setFixedHeight(60)
        self.sms_success = QLineEdit(http.get("success_contains", ""))
        self.sms_key = self._secret_field(SMS_HTTP_API_KEY)
        self.tw_sid = QLineEdit(sms.get("twilio", {}).get("account_sid", ""))
        self.tw_from = QLineEdit(sms.get("twilio", {}).get("from_number", ""))
        self.tw_token = self._secret_field(SMS_TWILIO_TOKEN)
        self.e2s = QLineEdit(sms.get("email2sms", {}).get("address_template", ""))
        test = QPushButton(_('Invia SMS di prova (al primo destinatario)'))
        test.clicked.connect(self._test_sms)
        for label, wid in (
            ("", self.sms_enabled), ("Provider", self.sms_provider), (_("Quando"), self.sms_mode),
            (_('Limite al giorno'), self.sms_limit), (_("Destinatari"), self.sms_to),
            ("HTTP – URL", self.sms_url), (_("HTTP – metodo"), self.sms_method), ("HTTP – Content-Type", self.sms_ctype),
            (_("HTTP – intestazioni (JSON)"), self.sms_headers), (_("HTTP – corpo"), self.sms_body),
            (_('HTTP – risposta deve contenere'), self.sms_success), (_('HTTP – chiave API'), self.sms_key),
            (_('Twilio – Account SID'), self.tw_sid), (_('Twilio – numero mittente'), self.tw_from),
            ("Twilio – token", self.tw_token), (_("Email-to-SMS – indirizzo"), self.e2s),
        ):
            f.addRow(label, wid) if label else f.addRow(wid)
        f.addRow(test)
        f.addRow(help_box([
            _('Scegli il <b>provider</b>: HTTP generico (Skebby, Aruba, Brevo…), Twilio o email-to-SMS.'),
            _('Compila i campi del provider: compaiono solo quelli che servono.'),
            _('Destinatari in formato internazionale (<b>+39…</b>) e un <b>limite al giorno</b> per i costi.'),
            _('Con <b>Solo se Telegram non risponde</b> gli SMS fanno da riserva. Poi <b>Invia SMS di prova</b>.'),
        ], _('7.3 SMS'), self))
        self._sms_form = f
        self._sms_rows = {
            "http": [self.sms_url, self.sms_method, self.sms_ctype, self.sms_headers, self.sms_body,
                     self.sms_success, self.sms_key],
            "twilio": [self.tw_sid, self.tw_from, self.tw_token],
            "email2sms": [self.e2s],
        }
        self.sms_provider.currentIndexChanged.connect(lambda _i: self._sms_provider_changed())
        self._sms_provider_changed()
        return w

    def _sms_provider_changed(self):
        """Mostra solo i campi del provider scelto."""
        current = self.sms_provider.currentData()
        for key, widgets in self._sms_rows.items():
            for wid in widgets:
                try:
                    self._sms_form.setRowVisible(wid, key == current)
                except AttributeError:  # Qt < 6.4
                    wid.setVisible(key == current)

    def _tab_notifications(self) -> QWidget:
        """Un'unica scheda per i canali di avviso: elenco a sinistra, impostazioni del canale a destra."""
        from PySide6.QtWidgets import QScrollArea  # noqa: PLC0415

        w = QWidget()
        lay = QHBoxLayout(w)
        self.channel_list = QListWidget()
        self.channel_list.setFixedWidth(190)
        stack = QStackedWidget()
        pages = (("Telegram", self._tab_telegram), ("Email", self._tab_email), ("SMS", self._tab_sms))
        for _name, build in pages:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(build())
            stack.addWidget(scroll)
            self.channel_list.addItem("")
        self._channel_checks = [self.tg_enabled, self.em_enabled, self.sms_enabled]
        self._channel_names = [n for n, _b in pages]
        for cb in self._channel_checks:
            cb.toggled.connect(lambda _on: self._refresh_channels())
        self.channel_list.currentRowChanged.connect(stack.setCurrentIndex)
        self.channel_list.setCurrentRow(0)
        self._refresh_channels()
        left = QVBoxLayout()
        left.addWidget(QLabel(_('Dove arrivano gli avvisi')))
        left.addWidget(self.channel_list, 1)
        note = QLabel(_('Ordine di invio: Telegram, poi email con il link al messaggio, poi SMS.'))
        note.setWordWrap(True)
        note.setStyleSheet("color:#9AA8BA; font-size:12px;")
        note.setFixedWidth(190)
        left.addWidget(note)
        lay.addLayout(left)
        lay.addWidget(stack, 1)
        return w

    def _refresh_channels(self):
        for i, (name, cb) in enumerate(zip(self._channel_names, self._channel_checks)):
            self.channel_list.item(i).setText(f"{'●' if cb.isChecked() else '○'}  {name}  –  "
                                              f"{_('attivo') if cb.isChecked() else _('spento')}")

    def _tab_ai(self) -> QWidget:
        """**AI**: cosa il programma riconosce, con che modello e con che soglia.

        Erano due schede — «Cosa sorvegliare» e «Suoni» — più il modello, che stava in «Generale» in
        mezzo alla lingua e all'avvio automatico. Marco, 25/10: *«devono andare in un tab solo
        chiamato AI, dove sposti anche il modello di riconoscimento e le sue impostazioni»*.

        Dentro, **due sottoschede**: quello che si vede e quello che si sente. Non è un vezzo di
        ordine: sono due modelli diversi, con due soglie diverse e due elenchi di cose diverse, e
        messi nella stessa pagina si confondevano — una soglia alzata per i gatti sembrava valere
        anche per i vetri rotti.
        """
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        self.ai_tabs = QTabWidget()
        self.ai_tabs.addTab(aiuto_in_fondo(self._ai_cam()), T.icon("video", T.TEXT, 16), _("Camere"))
        self.ai_tabs.addTab(aiuto_in_fondo(self._ai_audio()), T.icon("bell", T.TEXT, 16), _("Microfoni"))
        lay.addWidget(self.ai_tabs)
        return w

    def _ai_cam(self) -> QWidget:
        """Quali categorie fanno partire un avviso (persone, animali, veicoli, oggetti), e con che modello."""
        from ..detection.labels import GROUPS, OTHER, display_name  # noqa: PLC0415
        from ..detection.models import MODELS  # noqa: PLC0415

        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(help_box([
            _('<b>Persona</b> è spuntata di serie: fa lampeggiare la fascia rossa e può avviare azioni ed SMS.'),
            _('Spunta <b>animali, veicoli od oggetti</b> per ricevere anche per loro foto su Telegram ed email.'),
            _('Se arrivano falsi avvisi per animali e oggetti, alza la loro <b>soglia</b> qui sotto.'),
            _('Le soglie qui sono quelle <b>di serie</b>: una singola azione può chiederne una sua, '
              'e in quel caso scrive accanto qual è lo standard che sta scavalcando.'),
        ], _('8.1 Camere: cosa riconoscere'), self))
        # Il modello e le sue soglie **in cima**: è la cosa che decide tutto quello che c'è sotto, e
        # chi apre questa scheda per alzare una soglia deve vedere subito chi è che giudica.
        testa = QFormLayout()
        self.model = QComboBox()
        for name, spec in MODELS.items():
            self.model.addItem(f"{name} – {_(spec.description)}", name)
        self.model.setCurrentIndex(max(0, self.model.findData(self.cfg.get("detection.model"))))
        self.n_thr = QDoubleSpinBox(); self.n_thr.setRange(0.1, 0.95); self.n_thr.setSingleStep(0.05)
        self.n_thr.setValue(float(self.cfg.get("detection.person_threshold", 0.4)))
        self.cooldown = QSpinBox(); self.cooldown.setRange(10, 3600)
        self.cooldown.setValue(int(self.cfg.get("notifications.cooldown_seconds", 120)))
        self.cooldown.setToolTip(_('Evita raffiche di messaggi quando qualcuno resta davanti alla camera'))
        testa.addRow(_('Modello che guarda le immagini'), self.model)
        testa.addRow(_('Soglia persona per gli avvisi'), self.n_thr)
        # La pausa fra due avvisi stava in Generale. Riguarda una camera che riconosce qualcosa, e
        # sta accanto alla soglia perche' sono le due manopole con cui si litiga quando gli avvisi
        # sono troppi: una decide **se** avvisare, l'altra **quanto spesso** (25/10).
        testa.addRow(_('Pausa tra due avvisi della stessa camera (s)'), self.cooldown)
        lay.addLayout(testa)
        watched = set(self.cfg.get("detection.watch") or ["person"])
        self.watch_boxes: dict[str, QCheckBox] = {}
        # **Sei per riga, e incolonnate a sinistra.** Erano quattro, sparse su tutta la larghezza: in
        # una griglia di spunte lo spazio fra una voce e l'altra non aiuta a leggere, allontana — si
        # scorre l'occhio per mezzo schermo per andare da «Gatto» a «Cane». L'ultima colonna si
        # prende tutto quello che avanza, cosi' le altre restano larghe quanto il loro testo (25/10).
        # Quante colonne e quale griglia restano a portata di mano: e' quello che una prova guarda
        # per sapere se le spunte sono ancora incolonnate strette, senza frugare nei figli del layout.
        self.watch_colonne = colonne = 6
        self.watch_grid = grid = QGridLayout()
        grid.setHorizontalSpacing(22)
        grid.setVerticalSpacing(4)
        row = 0
        for _gid, gname, keys in GROUPS:
            title = QLabel(_(gname))
            title.setStyleSheet("font-weight:700; margin-top:6px;")
            grid.addWidget(title, row, 0, 1, colonne)
            row += 1
            for i, key in enumerate(keys):
                cb = QCheckBox(display_name(key))
                cb.setChecked(key in watched)
                self.watch_boxes[key] = cb
                grid.addWidget(cb, row + i // colonne, i % colonne)
            row += (len(keys) + colonne - 1) // colonne
        grid.setColumnStretch(colonne, 1)
        lay.addLayout(grid)
        # Le altre 56 categorie stanno **dietro un pulsante**, non in un elenco alto tre righe che
        # ne mostra una per volta: un elenco cosi' non si usa, si subisce. Marco, 25/10: *«fallo
        # diventare "mostra tutte le categorie": inutile averlo li', e' inusabile»*.
        #
        # Il posto dove vivono le spunte resta questo elenco, che pero' non si vede mai: cosi' chi
        # salva continua a leggere da dov'e' sempre stato, e la finestra e' solo un modo di toccarlo.
        self.watch_other = QListWidget()
        for key in OTHER:
            it = QListWidgetItem(display_name(key))
            it.setData(Qt.ItemDataRole.UserRole, key)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if key in watched else Qt.CheckState.Unchecked)
            self.watch_other.addItem(it)
        lay.addWidget(self.watch_other)
        self.watch_other.hide()   # dopo `addWidget`: nascondere prima di avere un genitore è un augurio
        riga_altre = QHBoxLayout()
        self.btn_altre = QPushButton(_('Mostra tutte le categorie…'))
        self.btn_altre.setToolTip(_('Le altre {n} cose che il modello sa riconoscere', n=len(OTHER)))
        self.btn_altre.clicked.connect(self._scegli_altre_categorie)
        riga_altre.addWidget(self.btn_altre, 0)
        self.altre_conto = QLabel("")
        self.altre_conto.setStyleSheet(f"color:{T.MUTED}; font-size:12px;")
        riga_altre.addWidget(self.altre_conto, 1)
        lay.addLayout(riga_altre)
        self._conta_altre()
        f = QFormLayout()
        self.o_thr = QDoubleSpinBox(); self.o_thr.setRange(0.2, 0.95); self.o_thr.setSingleStep(0.05)
        self.o_thr.setValue(float(self.cfg.get("detection.object_threshold", 0.5)))
        f.addRow(_('Soglia per animali, veicoli e oggetti'), self.o_thr)
        self.bird_species = QCheckBox(_("Per gli uccelli riconosci anche il gruppo (piccioni, corvi, gabbiani…)"))
        self.bird_species.setChecked(bool(self.cfg.get("detection.bird_species", True)))
        self.bird_species.setToolTip(_("Usa un secondo modello (~3,5 MB, scaricato al primo uso) solo quando "
                                       "compare un uccello"))
        f.addRow(self.bird_species)
        lay.addLayout(f)
        lay.addStretch(1)
        return w

    def _ai_audio(self) -> QWidget:
        """Microfoni da ascoltare e suoni che fanno partire un avviso, col modello che li giudica.

        La libreria dei suoni del **nodo** non sta più qui (25/10): quelli non sono cose che Mirto
        riconosce, sono cose che un nodo suona, e appartengono al nodo. Stanno in Nodi → Suoni, una
        per nodo — e così sparisce da sé il titolo «I suoni del nodo» su una casa con cinque nodi.
        """
        from ..audio.classes import SOUNDS  # noqa: PLC0415

        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(help_box([
            _("Spunta <b>Ascolta il microfono del {computer}</b> (il sistema chiede il permesso la prima volta).",
              computer=questo_computer()),
            _("Il microfono di un nodo si accende e si ascolta da <b>Nodi → Configurazione → Audio → "
              "Microfono</b>."),
            _("Scegli quali suoni mandano un <b>avviso</b>: arriva la foto della camera vicina al microfono."),
            _("Per far <b>reagire</b> un nodo a un suono (es. vetro rotto → sirena) aggiungilo tra gli obiettivi "
              "dell'azione in <b>Azioni</b>."),
        ], _('8.2 Microfoni: i suoni'), self))
        f = QFormLayout()
        self.mac_mic = QCheckBox(_("Ascolta il microfono del {computer}", computer=questo_computer()))
        self.mac_mic.setChecked(bool(self.cfg.get("audio.mac_mic", False)))
        self.mac_cam = QComboBox()
        self.mac_cam.addItem(_("La prima camera del {computer}", computer=questo_computer()), "")
        for c in self.core.cameras.all_cameras():
            self.mac_cam.addItem(c["name"], c["id"])
        self.mac_cam.setCurrentIndex(max(0, self.mac_cam.findData(self.cfg.get("audio.mac_camera", ""))))
        f.addRow(self.mac_mic)
        f.addRow(_("Foto dalla camera"), self.mac_cam)
        lay.addLayout(f)
        title = QLabel(_("Suoni che mandano un avviso"))
        title.setStyleSheet("font-weight:700; margin-top:8px;")
        lay.addWidget(title)
        watched = set(self.cfg.get("audio.watch") or [])
        always = set(self.cfg.get("audio.always_notify") or [])
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        self.sound_boxes: dict[str, QCheckBox] = {}
        for i, (kind, (name, emoji, _classes, _thr, _frames)) in enumerate(SOUNDS.items()):
            cb = QCheckBox(f"{emoji} {_(name)}")
            cb.setChecked(kind in watched)
            self.sound_boxes[kind] = cb
            grid.addWidget(cb, i // 2, i % 2)
        lay.addLayout(grid)
        self.smoke_always = QCheckBox(_("Allarme fumo: avvisa anche con la protezione disattivata"))
        self.smoke_always.setChecked("smoke_alarm" in always)
        lay.addWidget(self.smoke_always)
        # «non viene registrato» era vero per tutto il programma fino al 26/10. Oggi lo e' ancora
        # **qui**: questo e' l'ascolto per riconoscere i suoni, e quell'audio resta in memoria pochi
        # secondi. Dirlo al presente senza distinguere, adesso che un nodo puo' registrarlo, vorrebbe
        # dire una promessa smentita da un'altra schermata — e una promessa smentita vale meno di
        # nessuna promessa.
        note = QLabel(_("Questo ascolto non viene registrato né inviato: l'audio resta in memoria pochi "
                        "secondi, giusto il tempo di riconoscere il suono. Un nodo può invece registrare il "
                        "suo microfono dentro il video, ma solo se lo accendi tu "
                        "(Nodi → Configurazione → Disco → «Registra anche l'audio»). Avvisa chi vive in casa che Mirto ascolta."))
        note.setWordWrap(True)
        note.setStyleSheet("color:#9AA8BA; font-size:12px; margin-top:6px;")
        lay.addWidget(note)
        lay.addStretch(1)
        return w

    # --- la libreria dei suoni (24/10) ----------------------------------------

    def _libreria_suoni(self) -> QWidget:
        """I suoni che il nodo sa emettere, e il modo di aggiungerne di propri.

        Chiesto da Marco il 23/10: caricare un suono non deve succedere **dentro** un'azione, mentre
        la stai costruendo. Sono due momenti diversi — «sto preparando la mia libreria» e «sto
        costruendo questa azione» — e chi prepara la libreria non vuole passare da un'azione per
        farlo.
        """
        from .cards import CardList  # noqa: PLC0415

        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(2, 10, 2, 2)
        # Niente titolo «I suoni del nodo» e niente tendina per scegliere quale: questa pagina sta
        # **dentro** il nodo, che e' gia' scelto a sinistra e scritto in cima. Prima la tendina
        # compariva solo con piu' di un nodo, e il titolo diceva «il nodo» anche con cinque — Marco,
        # 25/10: *«il titolo e' sbagliato in modalita' multi nodo»*. Il modo di non sbagliare un
        # titolo e' non doverlo scrivere.
        testa = QHBoxLayout()
        self.suoni_titolo = QLabel("")
        self.suoni_titolo.setStyleSheet(f"color:{T.MUTED}; font-size:12px;")
        testa.addWidget(self.suoni_titolo, 1)
        self.btn_suono_nuovo = QPushButton(_("Aggiungi un suono…"))
        self.btn_suono_nuovo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_suono_nuovo.clicked.connect(self._aggiungi_suono)
        testa.addWidget(self.btn_suono_nuovo, 0)
        lay.addLayout(testa)
        # I limiti **prima** di far scegliere un file, non dopo aver aspettato la conversione.
        self.suoni_limiti = QLabel("")
        self.suoni_limiti.setWordWrap(True)
        self.suoni_limiti.setStyleSheet("color:#9AA8BA; font-size:12px;")
        lay.addWidget(self.suoni_limiti)
        # Righe compatte: una decina di suoni per nodo, e con l'altezza doppia se ne vedevano cinque
        # per pagina (Marco, 25/10).
        self.suoni_cards = CardList(_("Nessun nodo abbinato: i suoni vivono sul nodo che li emette."),
                                    selectable=False, compatta=True)
        lay.addWidget(self.suoni_cards)
        lay.addStretch(1)
        self._suoni_stato: dict = {}
        self._suono_in_corso = ""
        self._suoni_letti_da = ""      # per quale nodo abbiamo gia' chiesto: si rilegge se cambia
        return w

    def _nodo_dei_suoni(self) -> str:
        """Il nodo di cui si stanno guardando i suoni: quello aperto in «Nodi», non una scelta a parte."""
        nd = self._current_node()
        return str(nd["id"]) if nd else ""

    def _client_suoni(self):
        eng = self.core.actions
        return eng.clients.get(self._nodo_dei_suoni()) if eng else None

    def _leggi_suoni(self) -> None:
        """Chiede al nodo i suoi suoni. In disparte: se il nodo non risponde la scheda resta viva."""
        client = self._client_suoni()
        if not client:
            self.suoni_cards.set_rows([{"title": _("Il nodo non risponde: riprova fra poco."), "style": "add"}])
            return

        def arrivati(stato):
            if isinstance(stato, dict):
                self._suoni_stato = stato
                self._mostra_suoni()
        self._bg(client.sounds, arrivati)

    def _mostra_suoni(self) -> None:
        from ..actions.suoni import riassunto, righe_suoni  # noqa: PLC0415

        self.suoni_limiti.setText(riassunto(self._suoni_stato))
        righe = []
        for r in righe_suoni(self._suoni_stato, self._suono_in_corso):
            nome, nostro = r["nome"], r["nostro"]
            if r["azione"] == "ferma":
                bottoni = [(_("Ferma"), lambda _c=False: self._ferma_suono(), True)]
            else:
                bottoni = [(_("Ascolta"), lambda _c=False, n=nome: self._ascolta_suono(n), False)]
            if not nostro:
                bottoni.append((_("Togli"), lambda _c=False, n=nome: self._togli_suono(n), True))
            righe.append({"title": nome,
                          "sub": (_("nostro · {s} s", s=f"{r['secondi']:.1f}") if nostro
                                  else _("tuo · {s} s", s=f"{r['secondi']:.1f}")),
                          "dot": "#6B7A8F" if nostro else "#2FB383",
                          "buttons": bottoni})
        if not righe:
            righe = [{"title": _("Questo nodo non dichiara i suoi suoni: è una versione vecchia."),
                      "style": "add"}]
        self.suoni_cards.set_rows(righe)

    def _ascolta_suono(self, nome: str) -> None:
        """L'unica prova che conta: la cassa in giardino non suona come il computer.

        Mentre suona, il pulsante di quella riga diventa «Ferma»: un brano caricato può durare mezzo
        minuto, e doverlo sentire fino in fondo perché non c'è modo di spegnerlo è una piccola
        prigione (detto da Marco provando A-02).
        """
        from PySide6.QtCore import QTimer  # noqa: PLC0415

        client = self._client_suoni()
        if not client:
            self._info(_('Il nodo non risponde.'))
            return

        def partito(res):
            self._suono_in_corso = nome
            self._mostra_suoni()
            # Quando il nodo avrà finito, il pulsante torna «Ascolta» da solo: i secondi li dice lui
            # nella risposta, perché è l'unico a sapere quanto dura davvero quel file.
            durata = float((res or {}).get("durata") or 0) if isinstance(res, dict) else 0.0
            QTimer.singleShot(int(max(1.0, durata) * 1000) + 200, self,
                              lambda: self._suono_finito(nome))
        self._bg(lambda: client.activate("dissuasore", 30, extra={"suono": nome, "volte": 1}), partito)

    def _suono_finito(self, nome: str) -> None:
        """Il suono è arrivato in fondo da sé. La finestra può essere stata chiusa nel frattempo."""
        try:
            if self._suono_in_corso == nome:
                self._suono_in_corso = ""
                self._mostra_suoni()
        except RuntimeError:
            pass        # finestra chiusa mentre suonava: non c'è più niente da aggiornare

    def _ferma_suono(self) -> None:
        client = self._client_suoni()
        self._suono_in_corso = ""
        self._mostra_suoni()
        if client:
            self._bg(lambda: client.deactivate("dissuasore"), lambda _r: None)

    def _aggiungi_suono(self) -> None:
        from pathlib import Path  # noqa: PLC0415

        from PySide6.QtWidgets import QFileDialog  # noqa: PLC0415

        from ..actions.suoni import ESTENSIONI, comando_conversione, nome_pulito, perche_no  # noqa: PLC0415
        from ..recording.ffmpeg import find_ffmpeg  # noqa: PLC0415

        client = self._client_suoni()
        if not client:
            self._info(_('Il nodo non risponde: i suoni si caricano sul nodo che li emette.'))
            return
        ffmpeg = find_ffmpeg(self.cfg.get("recording.ffmpeg", "") or "")
        if not ffmpeg:
            self._info(_('Serve ffmpeg per convertire i suoni, e non lo trovo. È lo stesso che usa la '
                         'registrazione: configuralo in Registrazione.'))
            return
        filtro = _("Suoni") + " (" + " ".join("*" + e for e in ESTENSIONI) + ")"
        percorso, _scelto = QFileDialog.getOpenFileName(self, _("Scegli un suono"), "", filtro)
        if not percorso:
            return
        presi = {str(s.get("nome")) for s in (self._suoni_stato.get("suoni") or [])}
        guaio = perche_no(percorso, presi)
        if guaio:
            self._info(guaio)
            return
        nome = nome_pulito(Path(percorso).name)
        secondi = int(self._suoni_stato.get("massimo_s") or 30)

        def lavora():
            import subprocess  # noqa: PLC0415
            import tempfile  # noqa: PLC0415

            rate = int(self._suoni_stato.get("rate") or 22050)
            with tempfile.TemporaryDirectory() as d:
                fuori = Path(d, nome + ".wav")
                res = subprocess.run(comando_conversione(ffmpeg, percorso, fuori, rate, secondi),
                                     capture_output=True, text=True, timeout=120)
                if res.returncode != 0 or not fuori.exists():
                    raise RuntimeError((res.stderr or "").strip()[:200] or "conversione fallita")
                dati = fuori.read_bytes()
            massimo = int(self._suoni_stato.get("massimo_byte") or 0)
            if massimo and len(dati) > massimo:
                raise RuntimeError(_('Convertito pesa {kb} KB: al massimo {max} KB. Accorcialo.',
                                     kb=len(dati) // 1024, max=massimo // 1024))
            return client.add_sound(nome, dati)

        def finito(res):
            if isinstance(res, dict) and res.get("nome"):
                self._info(_('«{nome}» caricato sul nodo.', nome=res["nome"]))
            self._leggi_suoni()
        self._bg(lavora, finito)

    def _togli_suono(self, nome: str) -> None:
        from ..actions.suoni import in_uso  # noqa: PLC0415

        client = self._client_suoni()
        if not client:
            return
        usato = in_uso(nome, [a for a in self._actions if str(a.get("node")) == self._nodo_dei_suoni()])
        if usato:
            # Cancellarlo e lasciare un'azione muta è il genere di cosa che si scopre la notte in cui
            # serviva: si dice prima, e si lascia decidere.
            risposta = QMessageBox.question(
                self, _("Togli il suono"),
                _('«{nome}» è usato da: {elenco}. Quelle azioni resteranno senza quel suono. Procedo?',
                  nome=nome, elenco=", ".join(usato)))
            if risposta != QMessageBox.StandardButton.Yes:
                return
        self._bg(lambda: client.remove_sound(nome), lambda _r: self._leggi_suoni())

    def _tab_recording(self) -> QWidget:
        """Registrazione video: cartella, modalità per camera, conservazione, clip degli eventi."""
        from .sistema import MODE_NAMES  # noqa: PLC0415

        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(help_box([
            _("<b>Solo eventi</b> (consigliato): per ogni avviso Mirto salva un video da 10 secondi prima a 20 "
              "secondi dopo, accanto alla foto."),
            _("<b>Continua</b>: registra tutto, giorno e notte (circa 10 GB al giorno per camera); scegline una "
              "camera alla volta."),
            _("Le registrazioni più vecchie si cancellano da sole: il disco non si riempie mai."),
            _("La clip dell'evento arriva anche su <b>Telegram</b>, subito dopo la foto."),
            _("<b>Conserva foto ed eventi</b> dice per quanti giorni restano la foto e la riga "
              "dell'evento; i video invece se ne vanno quando serve spazio."),
        ], _('9. Registrazione e Rivedi'), self))
        rec = getattr(self.core, "recording", None)
        f = QFormLayout()
        self.rec_enabled = QCheckBox()
        self.rec_enabled.setChecked(bool(self.cfg.get("recording.enabled", True)))
        f.addRow(_("Registra le camere"), self.rec_enabled)
        # «Per quanto tenere foto ed eventi» stava in Generale. Sta qui dal 25/10, perche' questa e'
        # la scheda dove si decide **quanto tenere le cose**: averlo altrove voleva dire cercare in
        # due posti la stessa domanda. Vale per le foto e per gli eventi, non solo per i video.
        self.retention = QSpinBox(); self.retention.setRange(1, 365)
        self.retention.setValue(int(self.cfg.get("general.retention_days", 30)))
        self.retention.setToolTip(_('Vale per le foto e per gli eventi; i video seguono lo spazio del disco'))
        f.addRow(_('Conserva foto ed eventi (giorni)'), self.retention)
        folder_row = QHBoxLayout()
        self.rec_folder = QLineEdit(self.cfg.get("recording.folder", "") or "")
        default_root = str(rec.data_root / "recordings") if rec else ""
        self.rec_folder.setPlaceholderText(default_root)
        pick = QPushButton(_("Scegli…"))

        def choose():
            from PySide6.QtWidgets import QFileDialog  # noqa: PLC0415

            d = QFileDialog.getExistingDirectory(self, _("Cartella delle registrazioni"),
                                                 self.rec_folder.text() or default_root)
            if d:
                self.rec_folder.setText(d)

        pick.clicked.connect(choose)
        folder_row.addWidget(self.rec_folder, 1)
        folder_row.addWidget(pick)
        f.addRow(_("Cartella"), folder_row)
        self.rec_mode = QComboBox()
        for key in ("events", "continuous", "off"):
            self.rec_mode.addItem(_(MODE_NAMES[key]), key)
        self.rec_mode.setCurrentIndex(max(0, self.rec_mode.findData(self.cfg.get("recording.mode", "events"))))
        self.rec_mode.setMaximumWidth(260)
        f.addRow(_("Di serie"), self.rec_mode)
        lay.addLayout(f)
        title = QLabel(_("Per camera"))
        title.setStyleSheet("font-weight:700; margin-top:8px;")
        lay.addWidget(title)
        per = QGridLayout()
        per.setHorizontalSpacing(14)
        per.setColumnStretch(0, 1)
        per_cam = dict(self.cfg.get("recording.cameras") or {})
        armed_only = dict(self.cfg.get("recording.armed_only") or {})
        self.rec_cams: dict[str, QComboBox] = {}
        self.rec_armed: dict[str, QCheckBox] = {}
        self.rec_state: dict[str, tuple[QLabel, dict]] = {}
        for col, text in enumerate((_("Camera"), _("Registrazione"), "", _("Adesso"))):
            h = QLabel(text)
            h.setStyleSheet("color:#9AA8BA; font-size:12px; font-weight:700;")
            per.addWidget(h, 0, col)
        row, group = 1, None
        cams = sorted(self.core.cameras.all_cameras(), key=lambda c: (bool(c.get("node")),
                                                                     (c.get("node_name") or "").lower()))
        for c in cams:
            node = c.get("node_name") or ""
            if node != group:  # le camere di un nodo stanno sotto il nome del nodo
                group = node
                head = QLabel(_("Nodo {name}", name=node) if node else _("Camere di questo computer e di rete"))
                head.setStyleSheet("color:#9AA8BA; font-size:12px; margin-top:6px;")
                per.addWidget(head, row, 0, 1, 4)
                row += 1
            box = QComboBox()
            box.addItem("", "")  # "di serie: …", testo scritto da _rec_default_texts
            for key in ("events", "continuous", "off"):
                box.addItem(_(MODE_NAMES[key]), key)
            box.setCurrentIndex(max(0, box.findData(per_cam.get(c["id"], ""))))
            only = QCheckBox(_("solo con protezione attiva"))
            only.setChecked(bool(armed_only.get(c["id"], False)))
            only.setToolTip(_("Con la protezione disattivata questa camera non registra niente: nessun video, "
                              "nemmeno degli eventi."))
            self.rec_cams[c["id"]] = box
            self.rec_armed[c["id"]] = only
            name = QLabel(c["name"])
            name.setTextFormat(Qt.TextFormat.PlainText)
            if node:
                name.setContentsMargins(16, 0, 0, 0)  # rientro sotto il nome del nodo
            state = QLabel()
            state.setTextFormat(Qt.TextFormat.PlainText)
            self.rec_state[c["id"]] = (state, c)
            per.addWidget(name, row, 0)
            per.addWidget(box, row, 1)
            per.addWidget(only, row, 2)
            per.addWidget(state, row, 3)
            row += 1
        self.rec_mode.currentIndexChanged.connect(lambda _i: self._rec_default_texts())
        self._rec_default_texts()
        self._rec_refresh_state()
        from PySide6.QtCore import QTimer  # noqa: PLC0415

        self._rec_timer = QTimer(self)  # stato dal vivo mentre la finestra è aperta
        self._rec_timer.timeout.connect(self._rec_refresh_state)
        self._rec_timer.start(2000)
        hint = QLabel(_("«Adesso» mostra cosa succede con le impostazioni salvate; dopo «Salva» si aggiorna."))
        hint.setStyleSheet("color:#9AA8BA; font-size:12px;")
        per.addWidget(hint, row, 0, 1, 4)
        self._rec_other_armed = {k: v for k, v in armed_only.items() if k not in self.rec_armed}
        self._rec_other_cams = {k: v for k, v in per_cam.items() if k not in self.rec_cams}
        lay.addLayout(per)
        g = QFormLayout()
        self.rec_days = QSpinBox()
        self.rec_days.setRange(1, 365)
        self.rec_days.setSuffix(_(" giorni"))
        self.rec_days.setValue(int(self.cfg.get("recording.retention_days", 7)))
        g.addRow(_("Conserva la registrazione continua"), self.rec_days)
        self.rec_share = QSpinBox()
        self.rec_share.setRange(5, 90)
        self.rec_share.setSuffix(" %")
        self.rec_share.setValue(int(round(float(self.cfg.get("recording.max_disk_fraction", 0.5)) * 100)))
        g.addRow(_("Spazio massimo (dello spazio disponibile)"), self.rec_share)
        self.rec_pre = QSpinBox()
        self.rec_pre.setRange(0, 60)
        self.rec_pre.setSuffix(" s")
        self.rec_pre.setValue(int(self.cfg.get("recording.pre_seconds", 10)))
        self.rec_post = QSpinBox()
        self.rec_post.setRange(5, 120)
        self.rec_post.setSuffix(" s")
        self.rec_post.setValue(int(self.cfg.get("recording.post_seconds", 20)))
        clip_row = QHBoxLayout()
        clip_row.addWidget(QLabel(_("prima")))
        clip_row.addWidget(self.rec_pre)
        clip_row.addWidget(QLabel(_("dopo")))
        clip_row.addWidget(self.rec_post)
        clip_row.addStretch(1)
        g.addRow(_("Clip dell'evento"), clip_row)
        self.rec_fps = QSpinBox()
        self.rec_fps.setRange(2, 30)
        self.rec_fps.setSuffix(_(" fotogrammi/s"))
        self.rec_fps.setValue(int(self.cfg.get("recording.local_fps", 10)))
        g.addRow(_("Camere del {computer} e USB", computer=questo_computer()), self.rec_fps)
        self.rec_tg = QCheckBox(_("Manda la clip su Telegram dopo la foto"))
        self.rec_tg.setChecked(bool(self.cfg.get("recording.telegram_clip", True)))
        g.addRow(self.rec_tg)
        lay.addLayout(g)
        st = rec.status() if rec else {}
        info = (_("ffmpeg {v} · codificatore {enc}", v=st.get("ffmpeg_version") or "?", enc=st.get("encoder") or "-")
                if st.get("ffmpeg") else (st.get("ffmpeg_error") or _("ffmpeg: ricerca in corso…")))
        note = QLabel(info + "<br>" + _(
            "Se una camera riprende la strada o spazi condominiali valgono le regole del Garante Privacy: "
            "cartello, inquadratura limitata e conservazione breve (meglio <b>Solo eventi</b>). "
            "<b>L'audio non viene registrato</b>, a meno che tu non lo accenda su un nodo "
            "(Nodi → Configurazione → Disco → «Registra anche l'audio»): l'audio ha regole più severe del video, e in Italia il "
            "Garante lo considera eccedente nella gran parte dei casi."))
        note.setWordWrap(True)
        note.setStyleSheet("color:#9AA8BA; font-size:12px; margin-top:6px;")
        lay.addWidget(note)
        lay.addStretch(1)
        from PySide6.QtWidgets import QFrame, QScrollArea  # noqa: PLC0415

        scroll = QScrollArea()  # molte camere: la scheda scorre invece di allungare la finestra
        scroll.setWidget(w)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        return scroll

    def _rec_default_texts(self) -> None:
        """Prima voce di ogni camera: "di serie: <modalità>", così si vede cosa vale davvero."""
        from .sistema import MODE_NAMES  # noqa: PLC0415

        text = _("di serie: {mode}", mode=_(MODE_NAMES.get(self.rec_mode.currentData() or "events", "")))
        for box in self.rec_cams.values():
            box.setItemText(0, text)

    def _rec_refresh_state(self) -> None:
        rec = getattr(self.core, "recording", None)
        if rec is None:
            return
        try:  # stato attuale delle camere (collegata, accesa)
            fresh = {c["id"]: c for c in self.core.cameras.all_cameras()}
        except Exception:  # noqa: BLE001
            fresh = {}
        colors = {"ok": T.ARMED, "paused": T.PAUSED, "off": T.MUTED, "error": T.ALARM}
        marks = {"ok": "●", "paused": "⏸", "off": "○", "error": "⚠"}
        for cid, (label, cam) in self.rec_state.items():
            try:
                kind, text = rec.camera_state(fresh.get(cid, cam))
            except Exception:  # noqa: BLE001
                continue
            label.setText(f"{marks[kind]} {text}")
            label.setStyleSheet(f"color:{colors[kind]}; font-weight:{'700' if kind == 'ok' else '400'};")

    def _watched(self) -> list[str]:
        keys = [k for k, cb in self.watch_boxes.items() if cb.isChecked()]
        for i in range(self.watch_other.count()):
            it = self.watch_other.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                keys.append(it.data(Qt.ItemDataRole.UserRole))
        return keys

    def _altre_spuntate(self) -> list[str]:
        """Le categorie «altre» accese adesso, nell'ordine dell'elenco nascosto."""
        fuori = []
        for i in range(self.watch_other.count()):
            it = self.watch_other.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                fuori.append(it.text())
        return fuori

    def _conta_altre(self) -> None:
        """Scrive accanto al pulsante quali altre categorie sono accese.

        Senza questa riga il pulsante nasconderebbe anche il **fatto** che qualcosa è acceso: chi
        ha spuntato «semaforo» un mese fa non ha più modo di accorgersene, e gli avvisi che arrivano
        diventano inspiegabili. Si scrivono per nome finché stanno in una riga, poi si contano.
        """
        accese = self._altre_spuntate()
        if not accese:
            self.altre_conto.setText(_('nessuna delle altre categorie'))
        elif len(accese) <= 3:
            self.altre_conto.setText(", ".join(accese))
        else:
            self.altre_conto.setText(_('{n} categorie accese: {elenco}…',
                                       n=len(accese), elenco=", ".join(accese[:3])))

    def _scegli_altre_categorie(self) -> None:
        """La finestra con tutte le categorie che il modello sa riconoscere.

        Le spunte vere restano in `self.watch_other`, che non si vede: qui si lavora su una **copia**
        e si riscrive l'originale solo premendo Ok. Così «Annulla» annulla davvero, e chi salva legge
        da dove ha sempre letto.
        """
        dlg = QDialog(self)
        dlg.setWindowTitle(_('Tutte le categorie'))
        lay = QVBoxLayout(dlg)
        lay.addWidget(help_box([
            _('Queste sono le altre cose che il modello sa riconoscere: di solito non servono alla '
              'sorveglianza, ma a volte una è esattamente quello che cerchi.'),
            _('Valgono la <b>soglia</b> e la pausa degli oggetti, non quelle delle persone.'),
        ], _('8.1 Camere: cosa riconoscere'), dlg, title=_('Altre categorie')))
        cerca = QLineEdit()
        cerca.setPlaceholderText(_('Cerca una categoria…'))
        lay.addWidget(cerca)
        elenco = ElencoDiSpunte()
        for i in range(self.watch_other.count()):
            orig = self.watch_other.item(i)
            it = QListWidgetItem(orig.text())
            it.setData(Qt.ItemDataRole.UserRole, orig.data(Qt.ItemDataRole.UserRole))
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(orig.checkState())
            elenco.addItem(it)
        lay.addWidget(elenco, 1)

        def filtra(testo: str) -> None:
            cosa = testo.strip().casefold()
            for i in range(elenco.count()):
                it = elenco.item(i)
                it.setHidden(bool(cosa) and cosa not in it.text().casefold())

        cerca.textChanged.connect(filtra)
        bottoni = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bottoni.button(QDialogButtonBox.StandardButton.Ok).setText(_("Ok"))
        bottoni.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        bottoni.accepted.connect(dlg.accept)
        bottoni.rejected.connect(dlg.reject)
        lay.addWidget(bottoni)
        dlg.resize(460, 520)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        for i in range(elenco.count()):
            self.watch_other.item(i).setCheckState(elenco.item(i).checkState())
        self._conta_altre()

    def _tab_presence(self) -> QWidget:
        """Telefoni di casa sul Wi-Fi: protezione attivata quando escono tutti, disattivata al rientro."""
        w = QWidget()
        lay = QVBoxLayout(w)
        p = self.cfg.get("presence") or {}
        self.pr_enabled = QCheckBox(_('Riconosci chi è in casa dai telefoni collegati al Wi-Fi'))
        self.pr_enabled.setChecked(bool(p.get("enabled")))
        lay.addWidget(self.pr_enabled)
        lay.addWidget(help_box([
            _("Su ogni iPhone: Impostazioni → Wi-Fi → <b>(i)</b> → <b>Indirizzo Wi-Fi privato: Fisso</b> (riguarda solo il MAC, l'IP resta automatico)."),
            _('Spunta la casella qui sopra e premi <b>Cerca dispositivi in rete…</b>'),
            _('Riconosci il telefono dal suo <b>indirizzo MAC</b> (lo stesso che vedi sul telefono), premi <b>Aggiungi</b> e scrivi di chi è.'),
            _('Scegli dopo quanti minuti senza risposta una persona è <b>fuori</b>, poi <b>Salva</b>.'),
        ], _('11. Presenza'), self))
        self._devices = [dict(d) for d in (p.get("devices") or [])]
        self.dev_list = QListWidget()
        lay.addWidget(self.dev_list, 1)
        row = QHBoxLayout()
        for text, slot in ((_('Cerca dispositivi in rete…'), self._scan_devices), (_('Aggiungi a mano…'), self._add_device),
                           (_("Rinomina"), self._rename_device), (_("Rimuovi"), self._remove_device)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
            if slot == self._scan_devices:
                self.scan_button = b
        lay.addLayout(row)
        f = QFormLayout()
        self.pr_away = QSpinBox(); self.pr_away.setRange(3, 120)
        self.pr_away.setValue(int(p.get("away_minutes", 15)))
        self.pr_arm = QCheckBox(_('Quando escono tutti, attiva la protezione'))
        self.pr_arm.setChecked(bool(p.get("arm_when_all_away", True)))
        self.pr_disarm = QCheckBox(_('Quando rientra qualcuno, disattiva la protezione'))
        self.pr_disarm.setChecked(bool(p.get("disarm_on_arrival", True)))
        f.addRow(_('Considera «fuori» dopo (minuti senza risposta)'), self.pr_away)
        f.addRow(self.pr_arm)
        f.addRow(self.pr_disarm)
        lay.addLayout(f)
        self._refresh_devices()
        return w

    def _refresh_devices(self):
        from ..presence import normalize_mac  # noqa: PLC0415

        state = {pp["mac"]: pp for pp in self.core.presence.people()} if self.core.presence.enabled else {}
        self.dev_list.clear()
        for d in self._devices:
            mac = normalize_mac(d["mac"])
            st = state.get(mac)
            where = "" if not st or not self.core.presence.last_check else (_(' – in casa') if st["home"] else _(" – fuori"))
            self.dev_list.addItem(f"{d.get('name') or _('Senza nome')}  ·  MAC {mac.upper()}{where}")

    def _ask_name(self, default: str = "") -> str | None:
        name, ok = QInputDialog.getText(self, _("Persona"), _('Di chi è questo telefono? (es. Marco)'),
                                        QLineEdit.EchoMode.Normal, default)
        return name.strip() if ok and name.strip() else None

    def _add_device(self):
        from ..presence import normalize_mac  # noqa: PLC0415

        mac, ok = QInputDialog.getText(self, _('Aggiungi telefono'),
                                       _('Indirizzo MAC del telefono («Indirizzo Wi-Fi», es. 3A:7F:12:34:56:78):'))
        if not ok or not mac.strip():
            return
        try:
            mac = normalize_mac(mac)
            if len(mac.split(":")) != 6 or any(len(x) != 2 for x in mac.split(":")):
                raise ValueError
            int(mac.replace(":", ""), 16)
        except ValueError:
            self._info(_('Indirizzo non valido: servono 6 coppie di cifre separate da «:».'))
            return
        name = self._ask_name()
        if name:
            self._devices.append({"name": name, "mac": mac})
            self._refresh_devices()

    def _rename_device(self):
        row = self.dev_list.currentRow()
        if not 0 <= row < len(self._devices):
            self._info(_('Seleziona prima un telefono.'))
            return
        name = self._ask_name(self._devices[row].get("name", ""))
        if name:
            self._devices[row]["name"] = name
            self._refresh_devices()

    def _remove_device(self):
        row = self.dev_list.currentRow()
        if 0 <= row < len(self._devices):
            del self._devices[row]
            self._refresh_devices()

    def _scan_devices(self):
        from ..presence import discover_devices, normalize_mac  # noqa: PLC0415

        self.dev_list.addItem(_('Ricerca in corso (circa 10 secondi)…'))
        self.scan_button.setEnabled(False)

        def done(found):
            self.scan_button.setEnabled(True)
            self._refresh_devices()
            if isinstance(found, str):
                self._info(found)
                return
            if not found:
                self._info(_('Nessun dispositivo trovato. Il {computer} è collegato al Wi-Fi di casa?', computer=questo_computer()))
                return
            known = {normalize_mac(d["mac"]) for d in self._devices}
            dlg = QDialog(self)
            dlg.setWindowTitle(_('Dispositivi in rete'))
            dlg.resize(620, 460)
            v = QVBoxLayout(dlg)
            hint = QLabel(_("Ogni dispositivo è riconosciuto dal suo indirizzo MAC (prima colonna), che resta sempre lo stesso; l'indirizzo IP lo assegna il router e può cambiare, Mirto non lo usa per riconoscere nessuno. Confronta il MAC con l'«Indirizzo Wi-Fi» mostrato nelle impostazioni Wi-Fi del telefono."))
            hint.setWordWrap(True)
            v.addWidget(hint)
            lst = QListWidget()
            for r in found:
                tag = _(' · indirizzo privato (probabile telefono)') if r["private"] else ""
                already = _('  ✓ già aggiunto') if r["mac"] in known else ""
                it = QListWidgetItem(f"{r['mac'].upper()}  ·  {r['hostname'] or _('senza nome')}{tag}{already}\n"
                                     + _("      ora all'indirizzo IP {ip} (solo informativo: può cambiare)", ip=r['ip']))
                it.setData(Qt.ItemDataRole.UserRole, r)
                lst.addItem(it)
            lst.itemDoubleClicked.connect(lambda _i: dlg.accept())
            v.addWidget(lst, 1)
            bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            bb.button(QDialogButtonBox.StandardButton.Ok).setText(_("Aggiungi"))
            bb.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Chiudi"))
            bb.accepted.connect(dlg.accept)
            bb.rejected.connect(dlg.reject)
            v.addWidget(bb)
            if not dlg.exec() or not lst.currentItem():
                return
            r = lst.currentItem().data(Qt.ItemDataRole.UserRole)
            if r["mac"] in known:
                return
            name = self._ask_name((r.get("hostname") or "").split(".")[0])
            if name:
                self._devices.append({"name": name, "mac": r["mac"]})
                self._refresh_devices()
        self._bg(discover_devices, done)

    def _tab_schedule(self) -> QWidget:
        from ..schedule import load_rules  # noqa: PLC0415

        w = QWidget()
        lay = QVBoxLayout(w)
        self.sched_enabled = QCheckBox(_('Programmazione automatica attiva'))
        self.sched_enabled.setChecked(bool(self.cfg.get("schedule.enabled", True)))
        lay.addWidget(self.sched_enabled)
        lay.addWidget(help_box([
            _("Premi <b>Aggiungi…</b> e scegli <b>Attiva</b> o <b>Disattiva protezione</b> e l'ora."),
            _('Scegli i <b>giorni</b>, oppure un periodo <b>tra due date</b> (es. le vacanze).'),
            _('<b>Esempio notte + mattina</b> crea due regole pronte (23:00 attiva, 07:00 disattiva).'),
            _('Un cambio fatto a mano vale fino al prossimo orario. Da Telegram: <b>/programma sospendi</b> e <b>/programma riattiva</b>.'),
        ], _('10. Programmazione'), self))
        rules, errors = load_rules(self.cfg.get("schedule.rules") or [])
        self._rules = [r.to_dict() for r in rules]
        self.rules_list = QListWidget()
        self.rules_list.itemDoubleClicked.connect(lambda _it: self._edit_rule())
        lay.addWidget(self.rules_list, 1)
        if errors:
            warn = QLabel(_('Regole non valide ignorate: ') + "; ".join(errors))
            warn.setWordWrap(True)
            warn.setStyleSheet("color:#b91c1c;")
            lay.addWidget(warn)
        row = QHBoxLayout()
        for text, slot in ((_('Aggiungi…'), self._add_rule), (_('Modifica…'), self._edit_rule),
                           (_("Elimina"), self._delete_rule), (_('Esempio notte + mattina'), self._example_rules)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        lay.addLayout(row)
        self._refresh_rules()
        return w

    def _refresh_rules(self):
        from ..schedule import ScheduleRule  # noqa: PLC0415

        self.rules_list.clear()
        self._rules.sort(key=lambda d: d.get("ora", ""))
        for d in self._rules:
            self.rules_list.addItem(ScheduleRule.from_dict(d).describe())

    def _add_rule(self):
        d = RuleDialog(None, self)
        if d.exec():
            self._rules.append(d.result_rule)
            self._refresh_rules()

    def _edit_rule(self):
        row = self.rules_list.currentRow()
        if row < 0:
            self._info(_('Seleziona prima una regola.'))
            return
        d = RuleDialog(self._rules[row], self)
        if d.exec():
            self._rules[row] = d.result_rule
            self._refresh_rules()

    def _delete_rule(self):
        row = self.rules_list.currentRow()
        if row >= 0:
            del self._rules[row]
            self._refresh_rules()

    def _example_rules(self):
        self._rules.extend([
            {"nome": _("Notte"), "azione": "inserisci", "ora": "23:00", "giorni": [], "dal": "", "al": "", "attiva": True},
            {"nome": _("Mattina"), "azione": "disinserisci", "ora": "07:00", "giorni": [], "dal": "", "al": "", "attiva": True},
        ])
        self._refresh_rules()

    def _tab_nodes_actions(self) -> QWidget:
        """Un pulsantone per trovare i nodi; i nodi come schede (con Rimuovi sulla riga); sotto, solo per il nodo
        selezionato, le sue azioni (Prova, Modifica, Elimina sulla riga) e le sue camere."""
        from .cards import CardList, primary_button  # noqa: PLC0415

        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(8)
        lay.addWidget(help_box([
            _('Un <b>nodo</b> è un dispositivo Mirto (per esempio un Raspberry) con tutto ciò che ha: <b>camere</b>, sirena e audio, luci. Le sue camere compaiono da sole in Mirto.'),
            _('Accendi il nodo e premi <b>Cerca nodi in rete</b> (o usa "Nuovo nodo trovato" nella colonna Sistema): controlla l\'impronta e premi Abbina. Un nodo <b>mai abbinato</b> resta abbinabile finché non lo abbini, col PIN stampato che viaggia con lui; dopo il primo abbinamento ogni riapertura dura <b>5 minuti</b> e si chiede in <b>Altri computer…</b> (o sul nodo: <code>mirto-nodo abbina</code>).'),
            _('Clicca un nodo: sotto compaiono le sue <b>Camere</b>, la sua <b>Configurazione</b> (Wi-Fi, audio, camera, disco, altri computer, stato e diagnosi, modalità Prank) e i suoi <b>Suoni</b>. Le azioni si creano nella scheda <b>Azioni</b>.'),
        ], _('12. Nodi'), self))

        self.search_btn = primary_button(_('  Cerca nodi in rete'))
        self.search_btn.setIcon(T.icon("wifi", "#FFFFFF", 20))
        self.search_btn.clicked.connect(self._discover_nodes)
        lay.addWidget(self.search_btn)

        # Il nastro dell'aggiornamento. Stava solo dentro «Stato e diagnosi», cioe' a tre clic di
        # distanza e dentro una finestra che si apre quando qualcosa non va: Marco, 25/10,
        # *«e' troppo nascosto»*. Qui e' in cima alla scheda dove i nodi si guardano, e il pulsante
        # fa la cosa invece di portare dove si fa.
        self.agg_nastro = QWidget()
        self.agg_nastro.setStyleSheet(f"background:rgba(91,155,240,0.14); border:1px solid {T.INFO};"
                                      " border-radius:8px;")
        nastro = QHBoxLayout(self.agg_nastro)
        nastro.setContentsMargins(12, 8, 10, 8)
        self.agg_testo = QLabel("")
        self.agg_testo.setWordWrap(True)
        self.agg_testo.setStyleSheet("border:none; background:transparent;")
        nastro.addWidget(self.agg_testo, 1)
        self.agg_btn = QPushButton(_('Aggiorna il nodo'))
        self.agg_btn.setToolTip(_('Manda al nodo il programma che questo computer ha dentro'))
        self.agg_btn.clicked.connect(self._aggiorna_il_nodo_scelto)
        nastro.addWidget(self.agg_btn, 0)
        self.agg_nastro.hide()
        lay.addWidget(self.agg_nastro)

        # Elenco a sinistra, dettaglio a destra: con tre o cinque nodi, uno sotto l'altro diventa un
        # rotolo in cui non si capisce più dove si è. Chiesto da Marco il 21/10, guardandone uno solo
        # e immaginandone cinque.
        corpo = QHBoxLayout()
        corpo.setSpacing(14)
        colonna = QVBoxLayout()
        colonna.setSpacing(6)
        # Titolo di colonna, non titolo di paragrafo: maiuscoletto spaziato e una riga sotto, così si
        # legge come l'intestazione dell'elenco che apre (21/10).
        head = QLabel(_('NODI ABBINATI'))
        head.setStyleSheet(f"color:{T.MUTED}; font-weight:800; font-size:11px; letter-spacing:1.4px; "
                           f"padding:2px 0 6px 2px; border-bottom:1px solid {T.LINE}; margin-top:6px;")
        colonna.addWidget(head)
        self.nodes_cards = CardList(_('Nessun nodo abbinato. Accendi il nodo e premi «Cerca nodi in rete».'))
        self.nodes_cards.current_changed.connect(lambda _r: self._refresh_nodo())
        colonna.addWidget(self.nodes_cards)
        colonna.addStretch(1)
        sinistra = QWidget()
        sinistra.setLayout(colonna)
        sinistra.setFixedWidth(300)
        corpo.addWidget(sinistra)

        # tutto ciò che segue riguarda il nodo selezionato: nascosto se non ce n'è uno
        from PySide6.QtWidgets import QTabWidget  # noqa: PLC0415

        self.node_box = QWidget()
        box = QVBoxLayout(self.node_box)
        box.setContentsMargins(0, 0, 0, 4)
        box.setSpacing(4)
        # In cima al pannello di destra: nome del nodo e la riga lunga (indirizzo, abbinamenti,
        # avvisi) che prima stava stretta dentro la scheda.
        self.node_title = QLabel("")
        self.node_title.setStyleSheet("font-weight:700; font-size:15px;")
        self.node_detail = QLabel("")
        self.node_detail.setWordWrap(True)
        self.node_detail.setTextFormat(Qt.TextFormat.RichText)
        self.node_detail.setStyleSheet(f"color:{T.MUTED};")
        # Un avviso che dice «toglila in Altri computer…» deve portarci: leggerlo e poi cercare il
        # pulsante è lavoro che possiamo risparmiare a chi legge.
        self.node_detail.linkActivated.connect(
            lambda href: self._open_abbinamenti_dialog() if href == "altri-computer" else None)
        box.addWidget(self.node_title)
        box.addWidget(self.node_detail)
        box.addSpacing(6)        # le linguette non si appoggiano sulla riga dell'indirizzo
        self._node_cmds: dict[str, list] = {}   # comandi dichiarati da ogni nodo (GET /azioni)
        self._reading_cmds: set[str] = set()

        # Tre schede invece di tre rami uno sotto l'altro: ognuna ha una sola cosa da far vedere, e
        # non si scorre per trovarla. Chiesto da Marco il 21/10.
        self.node_tabs = QTabWidget()   # niente documentMode: il pannello deve avere il suo bordo

        pagina_camere = QWidget()
        camere_lay = QVBoxLayout(pagina_camere)
        camere_lay.setContentsMargins(2, 10, 2, 2)
        camere_lay.setSpacing(6)
        self.cams_cards = CardList(_('Questo nodo non trasmette camere.'), selectable=False)
        self.cams_cards.activated.connect(self._rinomina_camera_del_nodo)
        camere_lay.addWidget(self.cams_cards)
        suggerimento = QLabel(_('Clicca una camera per cambiarle il nome in Mirto.'))
        suggerimento.setStyleSheet(f"color:{T.MUTED}; font-size:12px;")
        camere_lay.addWidget(suggerimento)
        camere_lay.addStretch(1)
        self.node_tabs.addTab(pagina_camere, T.icon("video", T.TEXT, 16), _("Camere"))

        # Tutta la configurazione del nodo, che prima voleva una sessione ssh per pezzo. Sta in un ramo
        # suo perché erano quattro pulsanti sotto «Camere», che non è il posto dove uno cerca il Wi-Fi.
        pagina_conf = QWidget()
        conf_lay = QVBoxLayout(pagina_conf)
        conf_lay.setContentsMargins(2, 10, 2, 2)
        conf_lay.setSpacing(6)
        # Box invece di un elenco di pulsanti lunghi una riga, **in ordine alfabetico**: un elenco si
        # legge dall'alto una voce per volta, dei box uguali si guardano tutti insieme. E «rinomina»
        # non è qui: sta accanto al nome, che è dove uno va a cercarlo (22/10).
        self.conf_griglia = GrigliaDiBox()
        # Niente riferimenti ai singoli bottoni: la griglia li **ricrea** a ogni cambio di larghezza, e
        # un attributo che punta a un pulsante buttato via è un difetto che aspetta solo una finestra
        # ridimensionata.
        voci = [(_('Altri computer'), "link", self._open_abbinamenti_dialog),
                (_('Audio'), "bell", self._open_audio_dialog),
                (_('Camera'), "video", self._open_camera_dialog),
                (_('Disco'), "folder-open", self._open_disco_dialog),
                (_('Modalità Prank'), "flask-conical", self._open_prank_dialog),
                (_('Stato e diagnosi'), "shield-check", self._open_stato_dialog),
                (_('Wi-Fi'), "wifi", self._open_wifi_dialog)]
        voci.sort(key=lambda v: v[0].lower())      # alfabetico nella lingua di chi guarda, non nella mia
        self.conf_griglia.set_voci(voci)
        conf_lay.addWidget(self.conf_griglia)
        conf_lay.addStretch(1)
        # Stesso colore per tutte e tre, e chiaro: una era azzurra e due grigie, e quella grigia
        # spariva del tutto sulla tab selezionata, che ha lo sfondo azzurro (22/10).
        self.node_tabs.addTab(pagina_conf, T.icon("settings", T.TEXT, 16), _("Configurazione"))
        # I suoni che **questo** nodo sa emettere. Stavano nella scheda «Suoni» delle impostazioni,
        # insieme ai suoni che Mirto riconosce: due cose che si chiamano uguale e non c'entrano
        # niente — una e' quello che il programma sente, l'altra quello che una cassa suona.
        self.node_tabs.addTab(self._libreria_suoni(), T.icon("bell", T.TEXT, 16), _("Suoni"))
        box.addWidget(self.node_tabs)
        self.nessun_nodo = QLabel(_('Scegli un nodo a sinistra per vedere le sue camere e la sua configurazione.'))
        self.nessun_nodo.setWordWrap(True)
        self.nessun_nodo.setStyleSheet(f"color:{T.MUTED}; padding:14px; border:1px dashed {T.LINE}; border-radius:10px;")
        destra = QVBoxLayout()
        destra.setSpacing(6)
        destra.addWidget(self.nessun_nodo)
        destra.addWidget(self.node_box)
        destra.addStretch(1)
        pannello = QWidget()
        pannello.setLayout(destra)
        corpo.addWidget(pannello, 1)
        lay.addLayout(corpo)
        lay.addStretch(1)

        self._refresh_nodes()
        self._refresh_nodo()
        # stato dei nodi aggiornato da solo (sostituisce "Controlla ora")
        from PySide6.QtCore import QTimer  # noqa: PLC0415

        if self.core.actions:
            self.core.actions.refresh_nodes_soon()
        self._nodes_timer = QTimer(self)
        self._nodes_timer.timeout.connect(self._update_node_counts)
        self._nodes_timer.start(2000)

        # la scheda può essere lunga: scorre
        from PySide6.QtWidgets import QScrollArea  # noqa: PLC0415

        scroll = QScrollArea()
        scroll.setWidget(w)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        return scroll

    # --- azioni del nodo ------------------------------------------------------------
    def _current_node(self) -> dict | None:
        nodes = self.cfg.get("actions.nodes") or []
        row = self.nodes_cards.current_row() if hasattr(self, "nodes_cards") else -1
        return nodes[row] if 0 <= row < len(nodes) else None

    def _microfoni_scegliibili(self) -> list[dict]:
        """I microfoni che si possono mettere in un'azione: **solo quelli accesi davvero**.

        Prima erano quelli che il monitor stava ascoltando in quel momento, ed era una lista che
        diceva mezza verità. Marco, 24/10: *«il microfono del Mac se è disattivato non si deve vedere
        come scelta possibile; perché non vedo il microfono USB dell'ingresso?»*.

        Adesso la lista si costruisce da quello che è **configurato**: il microfono del computer se è
        acceso in Generale, e quello di ogni nodo che dichiara di averlo acceso. Un nodo con una
        scheda audio attaccata ma il microfono spento non compare: va acceso in Nodi → Audio del nodo,
        ed è lì che si vede.

        Sul computer il microfono è **uno**: Mirto ascolta l'ingresso predefinito del sistema. Se hai
        un microfono USB ed è quello predefinito, «Microfono del computer» è lui; scegliere *quale*
        ingresso usare non si può ancora, ed è scritto nel piano.
        """
        fuori = []
        if self.cfg.get("audio.mac_mic", False):
            fuori.append({"id": "mac", "name": _("Microfono del {computer}", computer=questo_computer())})
        eng = self.core.actions
        for nd in (self.cfg.get("actions.nodes") or []):
            nid = str(nd["id"])
            meta = (eng.node_meta.get(nid) or {}) if eng else {}
            if (meta.get("audio") or {}).get("microfono"):
                fuori.append({"id": f"node:{nid}",
                              "name": _("{name} (microfono)", name=nd.get("name") or nid)})
        return fuori

    def _ascolta_mic_di(self, nid: str) -> bool:
        from ..actions.pannello_audio import ascolta_microfono  # noqa: PLC0415

        return ascolta_microfono(self.cfg.get("audio.node_mics", True), nid)

    def _imposta_ascolto_mic(self, nid: str, acceso: bool) -> None:
        from ..actions.pannello_audio import con_ascolto  # noqa: PLC0415

        self.cfg.set("audio.node_mics", con_ascolto(self.cfg.get("audio.node_mics", True), nid, acceso))
        self.cfg.save()
        if self.core.actions:
            self.core.sync_audio_sources()

    def _nodo_di(self, action: dict | None) -> dict | None:
        """Il nodo su cui vive quest'azione: il primo dei suoi dispositivi."""
        from ..actions.blocchi import dispositivi_di, dispositivo_di  # noqa: PLC0415

        dove = dispositivi_di(action or {})
        if not dove:
            return None
        _tipo, quale = dispositivo_di(dove[0])
        return next((n for n in (self.cfg.get("actions.nodes") or []) if str(n.get("id")) == quale), None)

    # --- la scheda delle azioni (24/10) ----------------------------------------

    def _tab_actions(self) -> QWidget:
        """Tutte le azioni, in una scheda loro.

        Finché un'azione doveva avere un nodo, doveva anche vivere dentro la pagina di quel nodo. Da
        ieri non è più così: un'azione può accendere una luce all'ingresso e far suonare il garage, e
        chiederle a quale dei due appartiene non ha una risposta. Qui ci sono tutte, con scritto
        **dove** si fanno — la colonna che prima non poteva esistere.
        """
        from .cards import CardList  # noqa: PLC0415

        w = QWidget()
        lay = QVBoxLayout(w)

        if self._azioni_scartate:
            warn = QLabel(_('Queste azioni appartengono a nodi rimossi o non sono valide e verranno eliminate al salvataggio: ') + ", ".join(self._azioni_scartate))
            warn.setWordWrap(True)
            warn.setStyleSheet("color:#E8A33D; font-size:12px;")
            lay.addWidget(warn)
        # Qui c'era un paragrafo che diceva «partono solo con la protezione attiva, solo per le
        # persone»: vero finche' un'azione era solo un antifurto, e smentito dal primo dissuasore.
        # Marco, 26/10: *«e' indubbiamente un casino far capire all'utente questa cosa dei budget
        # senza che lui ne sia a conoscenza»*. Adesso i limiti si leggono **qui**, dove si creano le
        # azioni, divisi per tipologia e con scritto chi li impone.
        #
        # Il 27/10: *«in cima alla pagina c'e' la descrizione ed i pulsanti, non stanno bene… rimane
        # spazio inutilizzato ed e' molto brutta»*. Era un testo lungo in una colonna stretta con i
        # due pulsanti appesi in alto a destra e il vuoto sotto di loro. Adesso e' un pannello: i
        # pulsanti in testa, le quattro tipologie in **due colonne** — e' quella la larghezza che
        # avanzava — e il resto sotto, in piccolo.
        from PySide6.QtWidgets import QGridLayout  # noqa: PLC0415

        pannello = QFrame()
        pannello.setObjectName("bandaAzioni")
        # La seconda riga **non** è un f-string, quindi `}}` resterebbe `}}` e il foglio di stile
        # sarebbe invalido: Qt lo scarta in silenzio e scrive «Could not parse stylesheet» sei volte
        # a ogni apertura. Le graffe si raddoppiano solo dove c'è la f davanti alle virgolette.
        pannello.setStyleSheet(f"#bandaAzioni {{ background:{T.PANEL}; border:1px solid {T.LINE}; "
                               "border-radius:10px; }")
        dentro = QVBoxLayout(pannello)
        dentro.setContentsMargins(14, 10, 14, 10)
        dentro.setSpacing(8)
        riga_testa = QHBoxLayout()
        titolo = QLabel(_('Le quattro tipologie di azione'))
        titolo.setStyleSheet(f"font-weight:700; color:{T.TEXT}; background:transparent; border:none;")
        riga_testa.addWidget(titolo, 1)
        self.btn_azione_nuova = QPushButton(_('Nuova azione'))
        self.btn_azione_nuova.clicked.connect(lambda: self._nuova_azione_globale())
        self.btn_azione_libreria = QPushButton(_('Libreria azioni'))
        self.btn_azione_libreria.clicked.connect(self._apri_libreria_globale)
        for b in (self.btn_azione_nuova, self.btn_azione_libreria):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            riga_testa.addWidget(b, 0)
        dentro.addLayout(riga_testa)
        griglia = QGridLayout()
        griglia.setHorizontalSpacing(18)
        griglia.setVerticalSpacing(6)
        self._tipologie_lab: list[QLabel] = []
        for k in range(4):
            lb = QLabel("")
            lb.setWordWrap(True)
            lb.setTextFormat(Qt.TextFormat.RichText)
            lb.setStyleSheet(f"color:{T.MUTED}; font-size:{T.SIZE_SMALL}px; background:transparent; "
                             "border:none;")
            lb.setAlignment(Qt.AlignmentFlag.AlignTop)
            griglia.addWidget(lb, k // 2, k % 2)
            self._tipologie_lab.append(lb)
        griglia.setColumnStretch(0, 1)
        griglia.setColumnStretch(1, 1)
        dentro.addLayout(griglia)
        self.azioni_limiti = QLabel("")
        self.azioni_limiti.setWordWrap(True)
        self.azioni_limiti.setStyleSheet(f"color:{T.FAINT}; font-size:{T.SIZE_SMALL}px; "
                                         "background:transparent; border:none;")
        dentro.addWidget(self.azioni_limiti)
        lay.addWidget(pannello)
        # Righe basse: con una sola azione la scheda si allungava a riempire tutta la pagina, col
        # titolo in alto e i pulsanti a meta' altezza. Marco, 27/10: *«c'e' solo un'azione ma occupa
        # tutta la schermata»*. Il modo compatto esiste dal 25/10, da *«fai piu' corti gli item della
        # lista»*: qui non era stato usato.
        self.azioni_cards = CardList("", selectable=False, compatta=True)
        # Il doppio clic dà la **posizione** nell'elenco, non l'azione: finché l'elenco mostra tutte le
        # azioni in ordine le due coincidono, ma è una coincidenza, e il giorno che si filtra qualcosa
        # si aprirebbe l'azione sbagliata senza dirlo. La corrispondenza si tiene scritta.
        self._azioni_in_elenco: list[int] = []
        self.azioni_cards.activated.connect(self._azione_in_riga)
        # Altezza naturale, e lo spazio che avanza sotto: senza questo le schede si allungano a
        # riempire la pagina, che e' il difetto di sopra visto dall'altra parte.
        lay.addWidget(self.azioni_cards, 0)
        lay.addStretch(1)
        # Le proposte di **tutti** i nodi, lette in disparte: la libreria è delle azioni, non di un
        # nodo, e deve essere già popolata quando qualcuno la apre.
        for n in (self.cfg.get("actions.nodes") or []):
            if str(n["id"]) not in self._node_cmds:
                self._read_node_commands(str(n["id"]))
        # Niente riquadro «Come fare» qui: da quando in cima ci sono le quattro tipologie con le loro
        # regole, i suoi tre passi ripetevano con altre parole quello che si era appena letto. Resta
        # il collegamento alla guida, per chi vuole il capitolo intero.
        lay.addWidget(guide_link(_('13. Azioni'), self), 0, Qt.AlignmentFlag.AlignLeft)
        self._refresh_azioni()
        from PySide6.QtWidgets import QScrollArea  # noqa: PLC0415

        scorre = QScrollArea()   # con venti azioni l'altezza naturale non ci sta nella finestra
        scorre.setWidget(w)
        scorre.setWidgetResizable(True)
        scorre.setFrameShape(QScrollArea.Shape.NoFrame)
        scorre.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        return scorre

    def _refresh_azioni(self) -> None:
        import html as _html  # noqa: PLC0415

        from ..actions.pannello_azioni import righe_azioni  # noqa: PLC0415
        from .testi import riquadro_tipologia  # noqa: PLC0415

        if not hasattr(self, "azioni_cards"):
            return
        salvate = {a.get("name") for a in (self.cfg.get("actions.items") or [])}
        nodi = self.cfg.get("actions.nodes") or []

        def badge(text, color):
            return _("&nbsp;&nbsp;<span style='color:{color}; font-size:11px; font-weight:800; "
                     "letter-spacing:1px'>{text}</span>", color=color, text=text)

        righe, self._azioni_in_elenco = [], []
        for r in righe_azioni(self._actions, nodi, salvate):
            i = r["i"]
            tag = (badge(_("DA SALVARE"), "#E8A33D") if r["da_salvare"]
                   else badge(_("ATTIVA"), "#2FB383") if r["accesa"] else badge(_("SPENTA"), "#6B7A8F"))
            nd = self._nodo_di(self._actions[i])
            if nd and self._avvisi_azione(self._actions[i], str(nd["id"])):
                tag = badge("⚠", "#E8A33D") + tag
            sotto = r["cosa"]
            if r["dove"]:
                sotto = _('{cosa} · su {dove}', cosa=sotto, dove=r["dove"]) if sotto else _('su {dove}', dove=r["dove"])
            self._azioni_in_elenco.append(i)
            righe.append({"title": _html.escape(r["titolo"]) + "&nbsp;&nbsp;"
                                   + riquadro_tipologia(r["tipologia"], corto=True) + tag,
                          "sub": _html.escape(sotto),
                          "dot": "#2FB383" if r["accesa"] else "#6B7A8F",
                          "buttons": [(_("Prova"), lambda k=i: self._test_azione(k), False),
                                      (_('Modifica…'), lambda k=i: self._modifica_azione_globale(k), False),
                                      (_("Elimina"), lambda k=i: self._delete_action(k), True)]})
        if not righe:
            righe = [{"title": _('Nessuna azione.'), "style": "add",
                      "sub": _('«Libreria azioni» per prenderne una già fatta — antifurto, campanello, '
                               'dissuasore, scherzo — oppure «Nuova azione» per farla da zero.')}]
        self.azioni_cards.set_rows(righe)
        self._mostra_limiti_azioni()

    def _mostra_limiti_azioni(self) -> None:
        """La fascia in cima a «Azioni»: i limiti, divisi per scopo. Si rifà con l'elenco, perché
        dipende da quali azioni ci sono e dalla modalità Prank, che scade da sé."""
        from .testi import riassunto_limiti_azioni, tipologie_azioni  # noqa: PLC0415

        if not hasattr(self, "azioni_limiti"):
            return
        soglia = float(self.cfg.get("actions.person_threshold", 0.6))
        for lb, (_k, riga) in zip(self._tipologie_lab, tipologie_azioni(soglia)):
            lb.setText(riga)
        tipi = {str(a.get("kind") or "") for a in (self._actions or [])}
        tipi |= {"prank" for a in (self._actions or []) if str(a.get("quando") or "") == "prank"}
        eng = self.core.actions
        accesi = []
        for nd in (self.cfg.get("actions.nodes") or []):
            info = (eng.node_info.get(str(nd.get("id"))) if eng else None) or {}
            if (info.get("prank") or {}).get("attiva"):
                accesi.append(str(nd.get("name") or nd.get("id")))
        self.azioni_limiti.setText(riassunto_limiti_azioni(
            soglia, tipi, accesi, armata=bool(eng.armed) if eng else False))

    def _azione_in_riga(self, riga: int) -> None:
        """Doppio clic sulla riga numero ``riga`` dell'elenco: apre **quell'**azione."""
        if 0 <= riga < len(self._azioni_in_elenco):
            self._modifica_azione_globale(self._azioni_in_elenco[riga])

    def _modifica_azione_globale(self, i: int) -> None:
        if not (0 <= i < len(self._actions)):
            return
        a = self._open_action_dialog(self._actions[i])
        if a:
            self._actions[i] = a
            self._refresh_tutte_le_azioni()

    def _refresh_tutte_le_azioni(self) -> None:
        """Qualcosa è cambiato nelle azioni: l'elenco e i conteggi sulle card dei nodi."""
        self._refresh_azioni()
        self._update_node_counts()

    def _nuova_azione_globale(self) -> None:
        """Nuova azione dalla scheda: prima **dove**, poi cosa.

        Marco il 24/10: «Nuova azione deve far selezionare i dispositivi sui quali vuole agire». Con
        un nodo solo la domanda ha una risposta sola, quindi non si fa: si dice qual è, e si dice che
        aggiungendo nodi si potranno scegliere da lì.
        """
        nodi = self.cfg.get("actions.nodes") or []
        if not nodi:
            self._info(_('Prima abbina un nodo: un\'azione ha bisogno di qualcosa che la esegua.'))
            return
        # Niente finestra prima del wizard: la scelta dei dispositivi **è** la seconda schermata, e
        # da quello che si spunta lì discende cosa si può riconoscere. Una finestra separata ha vissuto
        # un'ora, il 24/10: chiedeva una cosa che poi la schermata dopo richiedeva un'altra volta.
        a = self._open_action_dialog(None, new=True, nodo=nodi[0])
        if a:
            self._actions.append(a)
            self._refresh_tutte_le_azioni()

    def _apri_libreria_globale(self) -> None:
        """La libreria di **tutte** le azioni pronte, senza ripetere la stessa cosa per ogni nodo."""
        from ..actions.pannello_azioni import libreria_aggregata, nomi_dispositivi  # noqa: PLC0415
        from ..actions.presets import proposte_per_scopo  # noqa: PLC0415

        nodi = self.cfg.get("actions.nodes") or []
        per_nodo = {}
        for n in nodi:
            nid = str(n["id"])
            cmds = self._node_cmds.get(nid)
            if cmds is None:
                self._read_node_commands(nid)
                continue
            # «Già importata» guarda il comando **e** lo scopo: chi ha il campanello non per questo
            # ha già lo scherzo, anche se suonano lo stesso jingle.
            gia = {(a.get("action"), str(a.get("quando") or "")) for a in self._actions
                   if str(a.get("node")) == nid}
            taken = {a.get("name") for a in self._actions}
            per_nodo[nid] = [a for a in proposte_per_scopo(nid, str(n.get("name") or nid), cmds, taken)
                             if (a.get("action"), str(a.get("quando") or "")) not in gia]
        voci = libreria_aggregata(per_nodo)
        if not voci:
            self._info(_('Sto leggendo le azioni pronte dei nodi: riprova fra un istante.')
                       if any(self._node_cmds.get(str(n["id"])) is None for n in nodi)
                       else _('Hai già importato tutte le azioni pronte.'))
            return
        dlg = LibreriaDialog(voci, self, nomi=nomi_dispositivi(nodi))
        if dlg.exec() and dlg.scelte:
            self._actions.extend(dlg.scelte)
            self._refresh_tutte_le_azioni()

    def _azioni_sul_nodo(self, nid: str) -> list[int]:
        """Indici in ``self._actions`` delle azioni che **toccano** quel nodo.

        Non «le azioni di quel nodo»: un'azione può accendere una luce all'ingresso e far suonare il
        garage, e chiederle a quale dei due appartiene non ha una risposta. Serve per il conteggio
        sulla card del nodo, che è l'ultimo posto dove un numero del genere ha ancora senso.
        """
        from ..actions.blocchi import dispositivi_di  # noqa: PLC0415

        chiave = "nodo:" + str(nid)
        return [i for i, a in enumerate(self._actions) if chiave in dispositivi_di(a)]

    def _avvisi_azione(self, a: dict, node_id: str) -> list:
        """Cosa c'è da segnalare in quest'azione: il simbolino accanto al nome (vedi «avvisi.py»).

        Non blocca e non cambia niente. È quello che resta dopo aver tolto i divieti il 23/10, ed è
        l'unica cosa che dice «attento» a chi punta un suono su una persona.
        """
        from ..actions.avvisi import CATEGORIE_NOSTRE, avvisi, suoni_di, tocca_la_notte  # noqa: PLC0415

        eng = self.core.actions
        tipi = eng.tipi_comandi(node_id) if eng else {}
        return avvisi(a.get("targets") or [], suoni_di(a, tipi), CATEGORIE_NOSTRE,
                      fascia_notturna=tocca_la_notte(a.get("hours") or ""))

    def _refresh_nodo(self):
        """Ramo del nodo selezionato: il suo nome, le sue camere, la sua configurazione.

        Le azioni non ci sono più (tappa B, 24/10): stavano qui quando un'azione doveva avere un nodo,
        e si vedevano in due posti da quando hanno una scheda loro. Quello che resta e che serve
        davvero e' la lettura in disparte dei comandi del nodo, che e' quello che popola la libreria.
        """
        import html as _html  # noqa: PLC0415

        nd = self._current_node()
        self.node_box.setVisible(bool(nd))
        self.nessun_nodo.setVisible(not nd)
        if not nd:
            return
        riga = self._node_row(nd)
        self.node_title.setText(_html.escape(str(riga["title"])))
        self.node_detail.setText(riga["dettaglio"])
        nid = str(nd["id"])
        if self._node_cmds.get(nid) is None:
            self._read_node_commands(nid)
        cam_rows = self._node_cam_rows(nid)
        self.cams_cards.set_rows(cam_rows)
        self.node_tabs.setTabText(0, _("Camere · {len}", len=len(cam_rows)))
        # I suoni sono di **questo** nodo: cambiando nodo si richiedono, una volta sola per nodo —
        # chiederli a ogni passaggio vorrebbe dire una richiesta in rete per ogni clic.
        if self._suoni_letti_da != nid:
            self._suoni_letti_da = nid
            self.suoni_titolo.setText(_('I suoni che questo nodo sa emettere, e quelli che hai caricato tu.'))
            self._leggi_suoni()
        self._update_node_counts()

    def _node_cam_rows(self, nid: str) -> list[dict]:
        import html as _html  # noqa: PLC0415

        rows = []
        for r in self._node_cameras(nid):
            cam = r.get("cam")
            if not r.get("in_linea"):
                state, dot = _('streaming fermo sul nodo'), "#E5484D"
            elif cam is None:
                state, dot = _('in arrivo in Mirto'), "#5B9BF0"
            elif not cam.get("enabled"):
                state, dot = _('spenta in Mirto'), "#6B7A8F"
            elif cam.get("status") == "online":
                state, dot = _('in linea'), "#2FB383"
            else:
                state, dot = _('collegamento…'), "#E8A33D"
            rows.append({"title": _html.escape(cam["name"] if cam else r.get("nome") or _("Camera")),
                         "sub": _("<span style='color:{dot}'><b>{state}</b></span> · porta {get} · {escape}", dot=dot, state=state, get=r.get('porta'), escape=_html.escape(str(r.get('percorso')))),
                         "dot": dot})
        return rows

    def _rinomina_camera_del_nodo(self, i: int) -> None:
        """Il nome con cui una camera del nodo si vede in Mirto.

        Di fabbrica è il nome del nodo, e quando il nodo cambia nome lo segue (``segui_il_nome``). Ma
        chi ha due camere su un nodo vuole chiamarle «Porta» e «Giardino», non «Ingresso – cam» e
        «Ingresso – cam2»: il nome si può cambiare qui, e da quel momento è suo e non segue più niente.
        Svuotando il campo si torna al nome che arriva dal nodo.
        """
        nd = self._current_node()
        righe = self._node_cameras(str(nd["id"])) if nd else []
        riga = righe[i] if 0 <= i < len(righe) else {}
        cam = riga.get("cam")
        if cam is None:
            self._info(_('Questa camera non è ancora arrivata in Mirto: riprova fra qualche secondo.'))
            return
        proposto = str(cam.get("name") or "")
        nome, ok = QInputDialog.getText(
            self, _('Nome della camera'),
            _("Nome da usare nell'app e negli avvisi\n(vuoto = quello che arriva dal nodo):"),
            QLineEdit.EchoMode.Normal, proposto)
        if not ok:
            return
        try:
            self.core.cameras.rename(str(cam["id"]), str(nome))
        except Exception as exc:  # noqa: BLE001
            self._info(_('Nome non salvato: {exc}', exc=exc))
            return
        self._refresh_nodes()

    def _open_camera_dialog(self) -> None:
        """Camera del nodo (accesa, NoIR, risoluzione) senza aprire un terminale sul Raspberry."""
        client, nd = self._node_client()
        if client is None:
            self._info(_('Il nodo non risponde: controlla che sia acceso e collegato alla rete.'))
            return
        from .camera_ui import camera_dialog  # noqa: PLC0415

        if camera_dialog(self, client, nd.get("name") or str(nd["id"])):
            self._info(_('Fatto: il nodo riavvia la trasmissione, e in Mirto la camera si aggiorna in pochi secondi.'))
            self._refresh_nodo()

    def _node_client(self):
        """Il cliente del nodo selezionato, o None se il nodo non risponde."""
        nd = self._current_node()
        eng = self.core.actions
        return (eng.clients.get(str(nd["id"])) if (nd and eng) else None), nd

    def _open_prank_dialog(self) -> None:
        """Modalità Prank su questo nodo: si accende qui, perché qui si sceglie per quanto.

        Lo stato lo si legge da quello periodico che Mirto tiene già (``node_info``): un nodo che
        non lo manda è un nodo più vecchio della 0.12.1, e la finestra lo dice invece di offrire
        pulsanti che quel nodo rifiuterebbe.
        """
        nd = self._current_node()
        if not nd:
            return
        nid = str(nd["id"])
        eng = self.core.actions
        if not eng or not eng.clients.get(nid):
            self._info(_('Il nodo non risponde: controlla che sia acceso e collegato alla rete.'))
            return
        from .prank_ui import prank_dialog  # noqa: PLC0415

        stato = (eng.node_info.get(nid) or {}).get("prank")
        nome = nd.get("name") or nid
        prank_dialog(self, nome, stato if isinstance(stato, dict) else None,
                     lambda minuti: self.core.prank(nid, minuti), lambda: self.core.prank(nid, 0))

    def _open_audio_dialog(self) -> None:
        """Uscita della sirena e cassa Bluetooth del nodo, senza aprire un terminale."""
        client, nd = self._node_client()
        if client is None:
            self._info(_('Il nodo non risponde: controlla che sia acceso e collegato alla rete.'))
            return
        from .audio_ui import audio_dialog  # noqa: PLC0415

        class Ponte:
            """Quello che la finestra può chiedere al nodo: niente di più."""

            stato = staticmethod(client.audio_state)
            cerca = staticmethod(client.find_speakers)
            abbina = staticmethod(client.pair_speaker)
            prova = staticmethod(client.test_sound)
            imposta = staticmethod(client.set_audio)
            microfono = staticmethod(client.set_microphone)
            manopole = staticmethod(client.knobs)
            gira = staticmethod(client.set_knob)
            dispositivi = staticmethod(client.devices)
            jack = staticmethod(client.set_jack)
            # Le due cose che non si chiedono al nodo: se **Mirto** ascolta quel microfono. Sta qui
            # perché è una scelta di questo computer su quel nodo, e si guarda dov'è il microfono.
            ascolto = staticmethod(lambda: self._ascolta_mic_di(str(nd["id"])))
            ascolta = staticmethod(lambda acceso: self._imposta_ascolto_mic(str(nd["id"]), acceso))
            dimentica = staticmethod(client.forget_speaker)
            flusso = staticmethod(client.stream_audio)

        if audio_dialog(self, Ponte(), nd.get("name") or str(nd["id"])):
            self._info(_('Fatto: il nodo riavvia il servizio e la sirena suonerà da lì.'))

    def _open_wifi_dialog(self) -> None:
        """Rete Wi-Fi del nodo, scelta fra quelle che vede lui, senza aprire un terminale."""
        client, nd = self._node_client()
        if client is None:
            self._info(_('Il nodo non risponde: controlla che sia acceso e collegato alla rete.'))
            return
        from .wifi_ui import ponte_del_nodo, wifi_dialog  # noqa: PLC0415

        wifi_dialog(self, ponte_del_nodo(client), nd.get("name") or str(nd["id"]))

    def _open_disco_dialog(self) -> None:
        """Disco delle registrazioni del nodo: quale, i limiti dell'anello, e prepararne uno nuovo."""
        client, nd = self._node_client()
        if client is None:
            self._info(_('Il nodo non risponde: controlla che sia acceso e collegato alla rete.'))
            return
        from .disco_ui import disco_dialog  # noqa: PLC0415

        class Ponte:
            """Quello che la finestra può chiedere al nodo: niente di più."""

            stato = staticmethod(client.disk_state)
            cerca = staticmethod(client.find_disks)
            usa = staticmethod(client.use_disk)
            prepara = staticmethod(client.prepare_disk)
            limiti = staticmethod(client.set_disk_limits)
            audio = staticmethod(client.set_recording_audio)

        disco_dialog(self, Ponte(), nd.get("name") or str(nd["id"]))

    def _rinomina_nodo(self) -> None:
        """Il nome di un nodo già abbinato: qui, sul dispositivo, e nell'indirizzo salvato.

        Il wizard il nome lo chiede una volta sola, al primo abbinamento. Chi scrive «Balocne» al posto
        di «Balcone» finora aveva come unico rimedio il terminale sul Raspberry più la correzione a mano
        dell'indirizzo salvato: cioè il terminale che il wizard doveva far sparire.
        """
        client, nd = self._node_client()
        if client is None:
            self._info(_('Il nodo non risponde: controlla che sia acceso e collegato alla rete.'))
            return
        vecchio = str(nd.get("name") or "")
        nome, ok = QInputDialog.getText(self, _('Rinomina il nodo'),
                                        _('Nome nuovo del nodo (quello vecchio era «{vecchio}»):',
                                          vecchio=vecchio), text=vecchio)
        nome = " ".join(str(nome or "").split())
        if not ok or not nome or nome == vecchio:
            return
        from ..actions.nodes import rinomina_nodo  # noqa: PLC0415

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            esito = rinomina_nodo(self.cfg, str(nd["id"]), nome, client)
        except Exception as exc:  # noqa: BLE001
            QApplication.restoreOverrideCursor()
            self._info(_('Il nodo non ha accettato il nome: {exc}', exc=exc))
            return
        QApplication.restoreOverrideCursor()
        # le camere del nodo possono aver cambiato nome: la griglia le rilegge dalla configurazione
        self._actions, _scartate = load_actions(self.cfg.get("actions.items") or [],
                                                {str(n["id"]) for n in (self.cfg.get("actions.nodes") or [])})
        cams = getattr(self.core, "cameras", None)
        ricaricate = True
        if esito.get("camere") and cams is not None:
            try:
                cams.reload()
            except Exception:  # noqa: BLE001
                ricaricate = False
        self._refresh_nodes()
        self._refresh_nodo()
        self._refresh_tutte_le_azioni()
        seguiti = len(esito.get("camere") or []) + len(esito.get("azioni") or [])
        pezzi = [_('Fatto: il nodo si chiama «{nome}».', nome=nome)]
        if esito.get("url"):
            pezzi.append(_('In rete è {host}.local, e l\'indirizzo salvato è stato aggiornato da solo.',
                           host=str(esito.get("host") or "")))
        if seguiti:
            pezzi.append(_('Hanno seguito il nome anche {quanti} fra camere e azioni che non ne avevano uno loro.',
                           quanti=seguiti))
        if not ricaricate:
            pezzi.append(_('La griglia delle camere si aggiorna alla prossima apertura di Mirto.'))
        self._info(" ".join(pezzi))

    def _open_abbinamenti_dialog(self) -> None:
        """Chi è abbinato a questo nodo: aggiungere un altro computer, o staccarne uno."""
        client, nd = self._node_client()
        if client is None:
            self._info(_('Il nodo non risponde: controlla che sia acceso e collegato alla rete.'))
            return
        from ..actions.pairing import client_name  # noqa: PLC0415
        from .abbinamenti_ui import abbinamenti_dialog  # noqa: PLC0415

        class Ponte:
            """Quello che la finestra può chiedere al nodo: niente di più."""

            stato = staticmethod(client.pairings)
            apri = staticmethod(client.open_pairing)
            chiudi = staticmethod(client.close_pairing)
            stacca = staticmethod(client.revoke_pairing)

        abbinamenti_dialog(self, Ponte(), nd.get("name") or str(nd["id"]), client_name())

    def _open_stato_dialog(self, aggiorna_subito: bool = False) -> None:
        """Stato e diagnosi del nodo: cosa non va, com'è fatto, e il rapporto da incollare."""
        client, nd = self._node_client()
        if client is None:
            self._info(_('Il nodo non risponde: controlla che sia acceso e collegato alla rete.'))
            return
        from .stato_ui import stato_dialog  # noqa: PLC0415

        class Ponte:
            """Tre domande al nodo in una: un nodo vecchio non conosce la terza, e non è un problema."""

            @staticmethod
            def leggi() -> dict:
                letto = {"info": client.info(), "stato": client.status(), "camera": {}}
                try:
                    letto["camera"] = client.camera_state()
                except Exception:  # noqa: BLE001  (nodo prima di API 1.5: si fa senza)
                    pass
                return letto

            @staticmethod
            def aggiorna(pacchetto: bytes, versione: str, sha256: str) -> dict:
                """Manda al nodo il programma che questo computer ha dentro (N-02, API 1.12)."""
                return client.aggiorna(pacchetto, versione, sha256)

        stato_dialog(self, Ponte(), nd.get("name") or str(nd["id"]), aggiorna_subito=aggiorna_subito)

    def _read_node_commands(self, nid: str) -> None:
        """Legge (in background) le azioni che il nodo dichiara: diventano le proposte nell'albero."""
        if nid in self._reading_cmds:
            return
        self._reading_cmds.add(nid)

        def done(cmds):
            self._reading_cmds.discard(nid)
            # Niente da ridisegnare: le proposte del nodo non stanno in nessun elenco, le legge la
            # «Libreria azioni» quando la si apre (tappa B, 24/10).
            self._node_cmds[nid] = cmds if isinstance(cmds, list) else []
        self._bg(lambda: self._node_commands(nid), done)

    def _node_commands(self, node_id: str) -> list[dict]:
        """Comandi esposti dal nodo (GET /azioni), se il nodo risponde."""
        eng = self.core.actions
        client = eng.clients.get(str(node_id)) if eng else None
        if not client:
            return []
        try:
            return client.actions()
        except Exception:  # noqa: BLE001
            return []

    def _open_action_dialog(self, action: dict | None, new: bool = False, nodo: dict | None = None):
        """``nodo``: su quale nodo nasce (o vive) l'azione. Senza, quello della pagina dei nodi.

        Dalla scheda Azioni una pagina del nodo non c'è: l'azione porta il suo (o lo si sceglie
        aprendo), e questa finestra deve poterlo ricevere invece di indovinarlo (24/10).
        """
        nd = nodo or self._nodo_di(action) or self._current_node()
        if not nd:
            return None
        # Le camere **com'è fatta la griglia**, accese e spente: quali proporre lo decide la finestra,
        # che sa anche quali erano già scelte da questa azione.
        camere = [{"name": c["name"], "enabled": c.get("enabled", True)}
                  for c in self.core.cameras.all_cameras()]
        microfoni = self._microfoni_scegliibili()
        taken = {a.get("name") for a in self._actions if new or a is not action}
        # Una strada sola da imparare: il wizard vale sempre, anche per modificare. Il modulo unico
        # da diciotto campi non si apre più da nessuna parte — e il prezzo (tre schermate per cambiare
        # un numero) si paga lasciando «Salva» acceso in tutte.
        dlg = ActionWizard(action, str(nd["id"]), camere, self, taken_names=taken, microfoni=microfoni,
                           nuova=new, soglie=self.cfg)
        if action and not new:
            dlg.setWindowTitle(_('Modifica azione'))

        def fill(cmds):
            if isinstance(cmds, list) and cmds:
                dlg.set_node_commands(cmds)
        self._bg(lambda: self._node_commands(nd["id"]), fill)

        # Gli **altri** nodi: servono al «Dove» del pezzo, perché una catena può attraversare due
        # stanze. Si chiedono in disparte come tutto il resto — se un nodo non risponde, nella tendina
        # c'è lo stesso e semplicemente non offre comandi, invece di far sparire la scelta.
        # I pezzi scelgono fra **tutti** i nodi abbinati, non fra un sottoinsieme deciso prima: dopo
        # una camera si può voler accendere una cassa di un altro nodo, e quella scelta è di ogni
        # pezzo (24/10).
        altri = [n for n in (self.cfg.get("actions.nodes") or []) if str(n.get("id")) != str(nd["id"])]
        dlg.set_dispositivi([{"chiave": "nodo:" + str(n["id"]), "nome": str(n.get("name") or n["id"])}
                             for n in [nd, *altri]])

        def altri_comandi(mappa):
            for chiave, cmds in (mappa or {}).items():
                if cmds:
                    dlg.set_comandi_di(chiave, cmds)
        if altri:
            self._bg(lambda: {"nodo:" + str(n["id"]): self._node_commands(n["id"]) for n in altri},
                     altri_comandi)

        # Cosa ha attaccato questo nodo: serve alla striscia per non proporre un suono a un nodo senza
        # cassa — e per dirlo, invece di nasconderlo. Si chiede in disparte: se il nodo non risponde la
        # finestra si apre comunque, come si apriva prima che le casse esistessero.
        def in_uso_di(dispositivi) -> str:
            """La cassa configurata sul nodo, o vuoto se non si riesce a saperlo."""
            from ..actions.pannello_audio import uscita_in_uso  # noqa: PLC0415

            try:
                return uscita_in_uso(dispositivi) if isinstance(dispositivi, dict) else ""
            except Exception:  # noqa: BLE001
                return ""

        def casse(dispositivi):
            from ..actions.pannello_audio import uscite_per_le_azioni  # noqa: PLC0415

            eng = self.core.actions
            vive = bool(eng and eng.node_online.get(str(nd["id"])))
            try:
                elenco = uscite_per_le_azioni(dispositivi) if isinstance(dispositivi, dict) else []
            except Exception:  # noqa: BLE001
                elenco = []
            dlg.set_stato_nodo(elenco, vive if dispositivi is not None else None)
            dlg.set_uscite_di("nodo:" + str(nd["id"]), elenco, in_uso=in_uso_di(dispositivi))
        self._bg(lambda: self._node_devices(nd["id"]), casse)

        # Le casse **degli altri** nodi: una catena può attraversare due stanze, e il «+» deve poter
        # offrire la cassa del garage anche quando l'azione nasce all'ingresso.
        def casse_altrui(mappa):
            from ..actions.pannello_audio import uscite_per_le_azioni  # noqa: PLC0415

            for chiave, dispositivi in (mappa or {}).items():
                try:
                    dlg.set_uscite_di(chiave, uscite_per_le_azioni(dispositivi)
                                      if isinstance(dispositivi, dict) else [],
                                      in_uso=in_uso_di(dispositivi))
                except Exception:  # noqa: BLE001
                    pass
        if altri:
            self._bg(lambda: {"nodo:" + str(n["id"]): self._node_devices(n["id"]) for n in altri},
                     casse_altrui)

        # Quali suoni fa il nodo e quali hai caricato tu: il nodo lo dichiara nel suo elenco, e senza
        # chiederglielo il «+» dava la stessa icona a un verso di falco e a un brano caricato
        # (Marco, 24/10). Si chiede in disparte come tutto il resto: se non risponde, restano tutti
        # con l'altoparlante, che è il meno sbagliato dei due.
        def suoi_suoni(stato):
            if isinstance(stato, dict):
                dlg.set_suoni_del_nodo([str(s.get("nome")) for s in (stato.get("suoni") or [])
                                        if s.get("nostro")])
        self._bg(lambda: self._node_sounds(nd["id"]), suoi_suoni)
        dlg.catena.prova_chiesta.connect(lambda: self._prova_la_catena(dlg, str(nd["id"])))
        dlg.catena.prova_un_pezzo.connect(lambda k: self._prova_la_catena(dlg, str(nd["id"]), k))
        dlg.catena.ferma_catena.connect(lambda: self._ferma_la_prova(dlg, str(nd["id"])))
        dlg.catena.ferma_pezzo.connect(lambda k: self._ferma_il_pezzo(dlg, str(nd["id"]), k))
        dlg.catena.scorciatoia.connect(lambda nome: self._scorciatoia_della_catena(nome))
        fatta = dlg.result_action if dlg.exec() else None
        if fatta:
            self._avvisa_una_volta(fatta, str(nd["id"]))
        return fatta

    def _avvisa_una_volta(self, a: dict, node_id: str) -> None:
        """La spiegazione sulle normative, **una volta sola**, quando un'azione viene salvata.

        Dopo resta solo il simbolino accanto al nome: un avviso che torna a ogni salvataggio diventa
        una cosa da chiudere senza leggere, e allora tanto vale non scriverlo.
        """
        from ..actions.avvisi import testo_lungo  # noqa: PLC0415

        segnalata = self._avvisi_azione(a, node_id)
        if segnalata:
            QMessageBox.information(self, _("Azione"), segnalata[0] + "\n\n" + testo_lungo())

    def _node_devices(self, node_id: str) -> dict | None:
        """Cosa ha attaccato il nodo (casse, microfoni), se risponde."""
        eng = self.core.actions
        client = eng.clients.get(str(node_id)) if eng else None
        if not client:
            return None
        try:
            return client.devices()
        except Exception:  # noqa: BLE001
            return None

    def _node_sounds(self, node_id: str) -> dict | None:
        """L'elenco dei suoni del nodo, se risponde: da lì si sa quali fa lui e quali hai caricato."""
        eng = self.core.actions
        client = eng.clients.get(str(node_id)) if eng else None
        if not client:
            return None
        try:
            return client.sounds()
        except Exception:  # noqa: BLE001
            return None

    def _prova_la_catena(self, dlg, node_id: str, solo: int | None = None) -> None:
        """Esegue la catena che si sta costruendo, adesso, sul nodo: la senti prima di salvarla.

        Con ``solo`` fa sentire **un pezzo**: serve quando si cerca un suono fra cinque, e sentire
        ogni volta anche la luce e l'attesa sarebbe un modo per non provarlo (23/10).
        """
        eng = self.core.actions
        if not eng:
            self._info(_('Il motore delle azioni non è attivo.'))
            return
        passi = dlg.catena.passi_salvabili()
        if solo is not None:
            passi = passi[solo:solo + 1]
            giri = 1
        else:
            giri = dlg.catena.ripeti_tutto.value()
        if not passi:
            return
        # Quanto durerà davvero: lo sa la striscia, perché con «quante volte» il nodo suona il file
        # intero e non i cinque secondi che la prova gli chiede (24/10).
        if solo is not None:
            secondi = dlg.catena.secondi_del_pezzo(solo)
        else:
            secondi = sum(dlg.catena.secondi_del_pezzo(k) + p.pausa
                          for k, p in enumerate(dlg.catena.passi())) * max(1, giri)
        dlg.catena.in_prova(secondi, solo)
        self._bg(lambda: eng.prova_catena(node_id, passi, giri),
                 lambda testo: self._info(str(testo)) if testo else None)

    def _ferma_la_prova(self, dlg, node_id: str) -> None:
        """Il flusso intero: si spegne il nodo **e** si butta via la coda dei passi."""
        eng = self.core.actions
        if eng:
            self._bg(lambda: eng.ferma_prova(node_id), lambda _r: None)

    def _ferma_il_pezzo(self, dlg, node_id: str, i: int) -> None:
        """Un pezzo solo non ha una coda dietro: basta spegnere quel comando."""
        eng = self.core.actions
        passi = dlg.catena.passi()
        client = eng.clients.get(str(node_id)) if eng else None
        if not client or not (0 <= i < len(passi)):
            return
        comando = passi[i].comando
        self._bg(lambda: client.deactivate(comando), lambda _r: None)

    def _scorciatoia_della_catena(self, nome: str) -> None:
        """«Ti serve una cassa» → la finestra dove si configura. Dire cosa manca e portarci."""
        if nome == "audio_del_nodo":
            self._open_audio_dialog()

    def _delete_action(self, i: int):
        if QMessageBox.question(self, _('Elimina azione'), _("Eliminare «{name}»?", name=self._actions[i].get('name'))) \
                == QMessageBox.StandardButton.Yes:
            del self._actions[i]
            self._refresh_tutte_le_azioni()

    # --- nodi -------------------------------------------------------------------
    def _node_cameras(self, node_id: str) -> list[dict]:
        """Camere trasmesse dal nodo, con il nome e lo stato che hanno in Mirto."""
        from ..actions.presets import match_node_cameras  # noqa: PLC0415

        eng = self.core.actions
        if not eng:
            return []
        overview = [n for n in eng.node_overview() if n["id"] == node_id]
        _matched, node_cams = match_node_cameras(self.cfg.get("cameras.network") or [], overview)
        try:
            by_id = {c["id"]: c for c in self.core.cameras.all_cameras()}
        except Exception:  # noqa: BLE001
            by_id = {}
        rows = []
        for nc in node_cams.get(node_id, []):
            rows.append(dict(nc, cam=by_id.get(nc["cam_id"]) if nc.get("cam_id") else None))
        return rows

    def _node_cameras_text(self, node_id: str) -> str:
        rows = self._node_cameras(node_id)
        eng = self.core.actions
        if not rows:
            if eng and eng.node_online.get(node_id) is None:
                return _("<span style='color:#9AA8BA'>Lettura dal nodo in corso…</span>")
            return (_("<span style='color:#9AA8BA'>Questo nodo non trasmette camere. Se sullo stesso Raspberry c'è MediaMTX con l'annuncio in rete, aggiorna il nodo col pulsante in cima a questa scheda.</span>"))
        lines = []
        for r in rows:
            where = f"porta {r.get('porta')} · {r['percorso']}"
            cam = r.get("cam")
            if not r.get("in_linea"):
                state = _("<span style='color:#E5484D'>⚠ streaming fermo sul nodo</span>")
            elif cam is None:
                state = (_("<span style='color:#5B9BF0'>da aggiungere: Tutte le cam → Trovate in rete</span>"))
            elif not cam.get("enabled"):
                state = _("<span style='color:#9AA8BA'>spenta in Mirto</span>")
            elif cam.get("status") == "online":
                state = _("<span style='color:#2FB383'>✓ in linea in Mirto</span>")
            else:
                state = _("<span style='color:#E8A33D'>… collegamento</span>")
            import html as _html  # noqa: PLC0415

            name = f"<b>{_html.escape(cam['name'])}</b> · " if cam else ""
            lines.append(f"📷 {name}{where} · {state}")
        return "<br>".join(lines)

    def _node_row(self, nd: dict) -> dict:
        import html as _html  # noqa: PLC0415

        nid = str(nd["id"])
        n_act = len(self._azioni_sul_nodo(nid)) if hasattr(self, "_actions") else 0
        n_cam = sum(1 for n in self.cfg.get("cameras.network") or [] if str(n.get("node")) == nid)
        eng = self.core.actions
        if not n_cam and eng:
            n_cam = len(self._node_cameras(nid))
        meta = (eng.node_meta.get(nid) if eng else None) or {}
        ok = eng.node_online.get(nid) if eng else None
        state, dot = ((_('in linea'), "#2FB383") if ok else (_('non risponde'), "#E5484D") if ok is False
                      else (_('controllo…'), "#E8A33D"))
        stato = _("<span style='color:{dot}'><b>{state}</b></span>", dot=dot, state=state)
        # Nella colonna di sinistra ci sta poco e deve bastare un'occhiata: stato, modello, quanto ha.
        corte = [stato]
        agg = aggiornamento_del_nodo(meta) if ok else None
        if agg:
            # Sulla scheda, in azzurro, prima di tutto il resto: e' l'unica cosa in quella colonna
            # che chieda di fare qualcosa, e il resto descrive soltanto com'e' messo il nodo.
            corte.append(_("<span style='color:{c}'><b>⬆ da aggiornare</b></span>", c=T.INFO))
        if meta.get("modello"):
            corte.append(_html.escape(meta["modello"]))
        corte.append(f"{n_act} azion{'e' if n_act == 1 else 'i'}")
        if n_cam:
            corte.append(f"{n_cam} camer{'a' if n_cam == 1 else 'e'}")
        # Nel pannello di destra ci sta tutto: indirizzo, chi è abbinato, avvisi.
        lunghe = [stato]
        if agg:
            lunghe.append(_("<span style='color:{c}'><b>aggiornamento: {vecchia} → {nuova}</b></span>",
                            c=T.INFO, vecchia=_html.escape(agg[0] or "?"), nuova=_html.escape(agg[1])))
        if meta.get("modello"):
            lunghe.append(_html.escape(meta["modello"]))
        lunghe.append(_html.escape(nd["url"]))
        lunghe += corte[2 if meta.get("modello") else 1:]
        lunghe += _pairing_parts((eng.node_info.get(nid) if eng else None) or {}, nd)
        return {"title": nd.get("name") or nid, "sub": " · ".join(corte), "dot": dot,
                "dettaglio": " · ".join(lunghe)}

    def _nodi_da_aggiornare(self) -> list[dict]:
        """I nodi in linea che hanno una versione più vecchia di quella che questo Mirto porta."""
        eng = self.core.actions
        fuori = []
        for nd in (self.cfg.get("actions.nodes") or []):
            nid = str(nd.get("id"))
            if eng and eng.node_online.get(nid) and aggiornamento_del_nodo(eng.node_meta.get(nid) or {}):
                fuori.append(nd)
        return fuori

    def _mostra_nastro_aggiornamento(self) -> None:
        """Il nastro in cima a «Nodi»: c'è solo quando c'è qualcosa da fare, e dice cosa."""
        if not hasattr(self, "agg_nastro"):
            return
        da_fare = self._nodi_da_aggiornare()
        self.agg_nastro.setVisible(bool(da_fare))
        if not da_fare:
            return
        eng = self.core.actions
        if len(da_fare) == 1:
            nd = da_fare[0]
            agg = aggiornamento_del_nodo((eng.node_meta.get(str(nd["id"])) if eng else None) or {})
            self.agg_testo.setText(_('<b>{nome}</b> ha un aggiornamento: {vecchia} → {nuova}.',
                                     nome=nd.get("name") or str(nd["id"]),
                                     vecchia=(agg or ("?", ""))[0] or "?", nuova=(agg or ("", "?"))[1]))
            self.agg_btn.setText(_('Aggiorna il nodo'))
        else:
            self.agg_testo.setText(_('{n} nodi hanno un aggiornamento: {nomi}.', n=len(da_fare),
                                     nomi=", ".join(str(x.get("name") or x.get("id")) for x in da_fare)))
            # Con più nodi il pulsante ne fa **uno**, quello scelto nell'elenco, e lo dice: un
            # pulsante che ne aggiorna cinque di fila fa aspettare cinque riavvii senza che si
            # capisca a che punto è. Si ripete, ed è chiaro ogni volta.
            self.agg_btn.setText(_('Aggiorna quello scelto'))

    def _aggiorna_il_nodo_scelto(self) -> None:
        """Il pulsante del nastro: apre «Stato e diagnosi» del nodo da aggiornare, già sulla domanda."""
        da_fare = self._nodi_da_aggiornare()
        if not da_fare:
            return
        scelto = self._current_node()
        ids = {str(x.get("id")) for x in da_fare}
        if scelto is None or str(scelto.get("id")) not in ids:
            scelto = da_fare[0]
            for i, nd in enumerate(self.cfg.get("actions.nodes") or []):
                if str(nd.get("id")) == str(scelto.get("id")):
                    self.nodes_cards.set_current_row(i)   # si vede **quale** nodo si sta aggiornando
        self._open_stato_dialog(aggiorna_subito=True)
        self._mostra_nastro_aggiornamento()

    def _update_node_counts(self):
        """Aggiorna ogni 2 s lo stato dei nodi e delle camere del nodo aperto, senza ricostruire nulla."""
        if not hasattr(self, "nodes_cards"):
            return
        self._mostra_nastro_aggiornamento()
        scelto = self._current_node()
        for i, nd in enumerate(self.cfg.get("actions.nodes") or []):
            r = self._node_row(nd)
            self.nodes_cards.update_row(i, r["sub"], r["dot"])
            if scelto and str(nd.get("id")) == str(scelto.get("id")):
                self.node_detail.setText(r["dettaglio"])
        nd = self._current_node()
        if nd is not None and hasattr(self, "cams_cards"):
            rows = self._node_cam_rows(str(nd["id"]))
            if len(rows) != self.cams_cards.count():
                self.cams_cards.set_rows(rows)
            for i, r in enumerate(rows):
                self.cams_cards.update_row(i, r["sub"], r["dot"])

    def _refresh_nodes(self):
        rows = []
        for k, nd in enumerate(self.cfg.get("actions.nodes") or []):
            r = self._node_row(nd)
            r["buttons"] = [(_("Rinomina il nodo"), lambda i=k: self._rinomina_nodo(), False, "pencil"),
                            (_("Rimuovi"), lambda i=k: self._remove_node(i), True)]
            rows.append(r)
        self.nodes_cards.set_rows(rows)

    def _discover_nodes(self):
        """Cerca i nodi in rete: quelli nuovi con l'abbinamento aperto si abbinano senza chiave."""
        from ..actions.discovery import browse  # noqa: PLC0415
        from ..actions.pairing import norm_fp, paired_fingerprints, pairing_state  # noqa: PLC0415

        def work():
            paired = paired_fingerprints(self.cfg)
            out = []
            for n in browse():
                st = pairing_state(n["url"])
                fp = norm_fp(st["impronta"] or n.get("fingerprint", ""))
                out.append(dict(n, gia=fp in paired, **st))
            return out

        def done(found):
            self.search_btn.setEnabled(True)
            self.search_btn.setText(_('  Cerca nodi in rete'))
            if isinstance(found, str):
                self._info(found); return
            if not found:
                self._info(_('Nessun nodo trovato in rete. Controlla che il nodo sia acceso e sulla stessa rete del {computer}, poi riprova.',
                           computer=questo_computer()))
                return
            new = [n for n in found if not n["gia"] and n["aperto"]]
            if new:
                from .pairing_ui import pair_node_dialog  # noqa: PLC0415

                pick = new[0]
                if len(new) > 1:
                    label, ok = QInputDialog.getItem(self, _('Nodi nuovi'), _('Quale nodo abbinare?'),
                                                     [f"{n['nome'] or n['name']} – {n['url']}" for n in new], 0, False)
                    if not ok:
                        return
                    pick = new[[f"{n['nome'] or n['name']} – {n['url']}" for n in new].index(label)]
                name = pair_node_dialog(self, self.core, {
                    "nome": pick["nome"] or pick["name"], "url": pick.get("host_url") or pick["url"],
                    "contact": pick["url"], "impronta": pick["impronta"], "scade_tra_s": pick["scade_tra_s"],
                    "senza_scadenza": bool(pick.get("senza_scadenza"))})
                if name:
                    self._refresh_nodes()
                    self.nodes_cards.set_current_row(self.nodes_cards.count() - 1)
                    self._info(_('{name} abbinato. Per creare le sue azioni apri la scheda Azioni → «Libreria azioni», poi Salva.', name=name))
                return
            self._info(nodi_trovati(found))
        self.search_btn.setEnabled(False)
        self.search_btn.setText(_('  Ricerca in corso…'))
        self._bg(work, done)

    def _remove_node(self, row: int):
        from ..actions.nodes import unpair_node  # noqa: PLC0415

        nodes = self.cfg.get("actions.nodes") or []
        if row < 0 or row >= len(nodes):
            return
        nd = nodes[row]
        own = [a for a in self._actions if str(a.get("node")) == str(nd["id"])]
        own_cams = [n for n in self.cfg.get("cameras.network") or [] if str(n.get("node")) == str(nd["id"])]
        parts = []
        if own:
            parts.append(_('le sue {len} azioni', len=len(own)))
        if own_cams:
            parts.append(_('le sue {len} camere ({join})', len=len(own_cams), join=', '.join(n.get('name', n['id']) for n in own_cams)))
        extra = _('\nVerranno eliminate anche {join}.', join=' e '.join(parts)) if parts else ""
        if QMessageBox.question(self, _('Rimuovi nodo'), _('Rimuovere il nodo {get}?{extra}', get=nd.get('name'), extra=extra)) \
                == QMessageBox.StandardButton.Yes:
            unpair_node(self.cfg, self.secrets, str(nd["id"]))
            self._actions = [a for a in self._actions if str(a.get("node")) != str(nd["id"])]
            # dal salvato si toglie solo ciò che riguarda il nodo: le altre modifiche restano da salvare
            self.cfg.set("actions.items", [a for a in (self.cfg.get("actions.items") or [])
                                           if str(a.get("node")) != str(nd["id"])])
            from ..actions.node_cameras import remove_node_cameras  # noqa: PLC0415

            nets, gone = remove_node_cameras(self.cfg.get("cameras.network") or [], str(nd["id"]))
            if gone:  # le camere del nodo se ne vanno con il nodo
                from ..credentials import rtsp_name  # noqa: PLC0415

                self.cfg.set("cameras.network", nets)
                ov = self.cfg.get("cameras.overrides") or {}
                self.cfg.set("cameras.overrides", {k: v for k, v in ov.items() if k not in gone})
                for cam_id in gone:  # e le loro password dal portachiavi
                    self.core.secrets.delete(rtsp_name(cam_id.split(":", 1)[1]))
            self.cfg.save()
            if gone:
                self.core.cameras.reload()
            if self.core.actions:
                self.core.actions.reload()
            self._refresh_nodes()
            self._refresh_nodo()
            self._refresh_tutte_le_azioni()

    def _test_azione(self, i: int):
        """«Prova» accende davvero l'azione, per cinque secondi.

        Prima chiamava la prova *a vuoto*, che controlla solo che il nodo risponda e che l'azione
        esista nel suo elenco: rispondeva «riuscita» senza aver acceso niente. Un pulsante che
        dice di aver provato quando non ha provato è peggio di un errore — fa credere che tutta la
        catena funzioni, e il difetto vero si scopre la notte in cui serve.
        """
        if not self.core.actions:
            return
        azione = self._actions[i]
        name = azione.get("name")
        if name not in {a.get("name") for a in (self.cfg.get("actions.items") or [])}:
            self._info(_('Questa azione non è ancora salvata: premi prima Salva.'))
            return
        if prova_si_sente(str(azione.get("kind", ""))) and QMessageBox.question(
                self, _('Prova azione'),
                _('«{name}» si accende adesso per 5 secondi, e si sente. Procedere?', name=name)) \
                != QMessageBox.StandardButton.Yes:
            return
        self._bg(lambda: self.core.actions.test_action(name, dry_run=False), self._info)

    # --- prove ---------------------------------------------------------------------
    def _test_telegram(self):
        self._save(close=False)
        self.core.notifier.setup_bot()
        self._bg(lambda: _('Messaggio inviato.') if self.core.notifier.send_text_now(_('✅ Mirto: messaggio di prova'))
                 else _('Invio non riuscito: controlla token, ID del gruppo e che il bot sia nel gruppo.'), self._info)

    def _test_email(self):
        from ..notify.email import EmailNotifier  # noqa: PLC0415

        self._save(close=False)
        n = EmailNotifier(self.cfg.get("notifications.email") or {}, self.secrets)

        def go():
            n.send(n.build_text_message(_('Mirto: email di prova'), _('Se leggi questa email, Mirto può inviarti gli avvisi.')))
            self.core.notifier.last_error.pop("email", None)
            return _('Email di prova inviata.')
        self._bg(go, self._info)

    def _test_sms(self):
        from ..notify.email import EmailNotifier  # noqa: PLC0415
        from ..notify.sms import make_provider  # noqa: PLC0415

        self._save(close=False)
        cfg = self.cfg.get("notifications.sms") or {}
        if not cfg.get("recipients"):
            self._info(_('Inserisci almeno un destinatario.'))
            return
        email = EmailNotifier(self.cfg.get("notifications.email") or {}, self.secrets)
        prov = make_provider(cfg, self.secrets, email)

        def go():
            prov.send(cfg["recipients"][0], _('Mirto: SMS di prova'))
            self.core.notifier.last_error.pop("sms", None)
            return _('SMS di prova inviato.')
        self._bg(go, self._info)

    # --- salvataggio -----------------------------------------------------------------
    def _salvato(self) -> None:
        """Dice che il salvataggio è andato, visto che la finestra non si chiude più a dirlo da sé."""
        from PySide6.QtCore import QTimer  # noqa: PLC0415

        self.salvato.setText(_('✓ Salvato'))
        # Il contesto è **l'etichetta**, non la finestra: se Qt la distrugge nei due secondi e mezzo
        # (succede quando la scheda si ricostruisce), il timer non parte invece di schiantarsi su un
        # oggetto C++ che non c'è più. L'8/11 questo errore è comparso tre volte in un minuto.
        QTimer.singleShot(2500, self.salvato, lambda: self.salvato.setText(""))

    def _save(self, close: bool = True):
        from ..system import autostart  # noqa: PLC0415

        c = self.cfg
        from ..actions.engine import ActionSpec  # noqa: PLC0415

        try:
            headers = json.loads(self.sms_headers.text() or "{}")
        except Exception as exc:
            QMessageBox.warning(self, _("Impostazioni"), _('Intestazioni SMS non valide (JSON): {exc}', exc=exc))
            return
        items = list(self._actions)
        seen = set()
        for a in items:
            if a.get("name") in seen:
                QMessageBox.warning(self, _("Azioni"), _('Due azioni si chiamano «{get}»: rinominane una.', get=a.get('name')))
                return
            seen.add(a.get("name"))
        for a in items:
            try:
                ActionSpec.from_dict(a)
            except Exception as exc:
                QMessageBox.warning(self, _("Azioni"), _('Azione «{get}» non valida: {exc}', get=a.get('name'), exc=exc))
                return
        watch = self._watched()  # controlli prima di cambiare qualsiasi cosa
        if not watch:
            QMessageBox.warning(self, _('AI → Camere'), _('Spunta almeno una categoria (di solito «Persona»).'))
            return
        if self.pr_enabled.isChecked() and not self._devices:
            QMessageBox.warning(self, _("Presenza"), _('Aggiungi almeno un telefono oppure spegni la presenza.'))
            return
        c.set("general.prevent_sleep", self.prevent_sleep.isChecked())
        from PySide6.QtCore import QSettings  # noqa: PLC0415

        QSettings("Mirto", "Mirto").setValue("close/background", self.close_background.isChecked())
        c.set("general.retention_days", self.retention.value())
        c.set("telemetry.update_check", self.tm_updates.isChecked())
        tel = getattr(self.core, "telemetry", None)
        if tel is not None and tel.available() and bool(c.get("telemetry.stats")) != self.tm_stats.isChecked():
            tel.set_consent(self.tm_stats.isChecked())  # dalle Impostazioni: nessun «no» anonimo, solo la prima risposta conta
        lang_changed = c.get("general.language", "auto") != self.language.currentData()
        c.set("general.language", self.language.currentData())
        c.set("detection.model", self.model.currentData())
        c.set("detection.person_threshold", round(self.n_thr.value(), 2))
        c.set("detection.watch", watch)
        c.set("detection.object_threshold", round(self.o_thr.value(), 2))
        c.set("detection.bird_species", self.bird_species.isChecked())
        always = [k for k in (c.get("audio.always_notify") or []) if k != "smoke_alarm"]
        if self.smoke_always.isChecked():
            always.append("smoke_alarm")
        c.set("audio", {**(c.get("audio") or {}), "mac_mic": self.mac_mic.isChecked(),
                        "mac_camera": self.mac_cam.currentData() or "",
                        "watch": [k for k, cb in self.sound_boxes.items() if cb.isChecked()],
                        "always_notify": always})
        folder = self.rec_folder.text().strip()
        if folder:
            from pathlib import Path  # noqa: PLC0415

            if not Path(folder).expanduser().is_absolute():
                QMessageBox.warning(self, _("Registrazione"), _("La cartella delle registrazioni deve essere un "
                                                               "percorso completo (usa «Scegli…»). Resta quella "
                                                               "di serie."))
                folder = ""
                self.rec_folder.setText("")
        per_cam = dict(self._rec_other_cams)
        per_cam.update({cid: box.currentData() for cid, box in self.rec_cams.items() if box.currentData()})
        armed_only = dict(self._rec_other_armed)
        armed_only.update({cid: True for cid, cb in self.rec_armed.items() if cb.isChecked()})
        for cid, cb in self.rec_armed.items():
            if not cb.isChecked():
                armed_only.pop(cid, None)
        c.set("recording", {**(c.get("recording") or {}), "enabled": self.rec_enabled.isChecked(),
                            "folder": folder, "mode": self.rec_mode.currentData(),
                            "cameras": per_cam, "armed_only": armed_only, "retention_days": self.rec_days.value(),
                            "max_disk_fraction": self.rec_share.value() / 100,
                            "pre_seconds": self.rec_pre.value(), "post_seconds": self.rec_post.value(),
                            "local_fps": self.rec_fps.value(), "telegram_clip": self.rec_tg.isChecked()})
        c.set("presence", {**(c.get("presence") or {}), "enabled": self.pr_enabled.isChecked(),
                           "devices": list(self._devices), "away_minutes": self.pr_away.value(),
                           "arm_when_all_away": self.pr_arm.isChecked(),
                           "disarm_on_arrival": self.pr_disarm.isChecked()})
        c.set("notifications.cooldown_seconds", self.cooldown.value())
        users = []
        for u in _split(self.tg_users.text()):
            try:
                users.append(int(u))
            except ValueError:
                pass
        c.set("notifications.telegram", {"enabled": self.tg_enabled.isChecked(),
                                         "chat_id": self.tg_chat.text().strip(), "allowed_user_ids": users})
        self._store_secret(TELEGRAM_TOKEN, self.tg_token)
        c.set("notifications.email", {
            "enabled": self.em_enabled.isChecked(), "provider": self.em_provider.currentData(),
            "host": self.em_host.text().strip(), "port": self.em_port.value(), "security": "",
            "username": self.em_user.text().strip(), "sender": self.em_user.text().strip(),
            "recipients": _split(self.em_to.text()),
        })
        self._store_secret(EMAIL_PASSWORD, self.em_pass)
        c.set("notifications.sms", {
            "enabled": self.sms_enabled.isChecked(), "provider": self.sms_provider.currentData(),
            "mode": self.sms_mode.currentData(), "daily_limit": self.sms_limit.value(),
            "recipients": _split(self.sms_to.text()),
            "http": {"url": self.sms_url.text().strip(), "method": self.sms_method.currentText(),
                     "headers": headers, "body": self.sms_body.toPlainText(),
                     "content_type": self.sms_ctype.text().strip() or "application/json",
                     "success_status": [200, 201, 202], "success_contains": self.sms_success.text()},
            "twilio": {"account_sid": self.tw_sid.text().strip(), "from_number": self.tw_from.text().strip()},
            "email2sms": {"address_template": self.e2s.text().strip()},
        })
        self._store_secret(SMS_HTTP_API_KEY, self.sms_key)
        self._store_secret(SMS_TWILIO_TOKEN, self.tw_token)
        c.set("actions.items", items)
        c.set("schedule.enabled", self.sched_enabled.isChecked())
        c.set("schedule.rules", list(self._rules))
        c.save()
        if self.autostart.isChecked() != autostart.is_enabled():
            try:
                autostart.set_enabled(self.autostart.isChecked())
            except Exception as exc:
                QMessageBox.warning(self, _('Avvio automatico'), str(exc))
        if lang_changed:
            from ..i18n import set_language  # noqa: PLC0415

            set_language(c.get("general.language", "auto"))  # messaggi e avvisi subito nella nuova lingua
            QMessageBox.information(self, "Mirto", _("La nuova lingua vale per avvisi e messaggi da subito; "
                                                        "per tutta la finestra chiudi e riapri Mirto."))
        # Salvare non chiude. Chiesto da Marco il 23/10 — «è fastidioso» — e ha ragione: la finestra
        # delle impostazioni è un posto dove si fanno dieci cose di fila, e chiuderla a ogni salvataggio
        # obbliga a riaprirla e a ritrovare il punto in cui si era. Si chiude con Chiudi.
        self.core.reload_config()
        # L'elenco delle azioni dice «DA SALVARE» confrontando i nomi con quelli **in configurazione**:
        # scritta la configurazione, quel confronto e' cambiato, e finche' nessuno lo rifaceva la
        # scritta ambra restava li' dopo aver salvato. Marco, 27/10: *«rimane il messaggio nella lista
        # "da salvare" anche se ho gia' salvato»*. Succedeva solo premendo Salva senza chiudere, che e'
        # il modo normale di usare questa finestra dal 23/10 («salvare non chiude»).
        if hasattr(self, "azioni_cards"):
            self._refresh_tutte_le_azioni()
        if close:
            self._salvato()


class ActionDialog(QDialog):
    """Modulo per un'azione di un nodo: niente YAML da scrivere a mano."""

    def __init__(self, action: dict | None, node_id: str, camere: list[dict], parent=None,
                 taken_names: set | None = None, microfoni: list[dict] | None = None,
                 nuova: bool = False, soglie=None):
        super().__init__(parent)
        # Le soglie generali servono per dire «standard: 60%» accanto a ogni riconoscimento. Questa
        # finestra **non ha** la configurazione (ce l'ha quella che la apre, ed e' un'altra classe):
        # gliela si passa, e senza si leggono i valori di serie invece di inventare un numero. Il
        # 24/10 avevo scritto `self.cfg` qui dentro, e sono andate in errore quaranta prove — la
        # seconda volta in un giorno che chiamo una cosa che in questa classe non c'e'.
        from ..actions.confidenze import di_serie  # noqa: PLC0415

        self._soglia_generale = getattr(soglie, "get", None) or di_serie
        a = dict(action or {})
        # Creare o modificare: cambia quando «Salva» si accende. Lo **dice chi apre**, invece di
        # indovinarlo dalla presenza di un'azione: dal 24/10 una nuova può arrivare già con dentro le
        # fonti scelte nel flusso, e indovinare la farebbe passare per una modifica (24/10).
        self._apriva_una_azione = bool(action) and not nuova
        self.taken_names = set(taken_names or ())
        self.node_id = node_id
        self.result_action: dict | None = None
        self.setWindowTitle(_('Azione del nodo'))
        # Larga: dentro ci sono due griglie di riquadri, e quattro per riga vogliono spazio. Stretta,
        # le righe diventano sei e la finestra un rotolo.
        self.resize(920, 760)
        self.name = QLineEdit(a.get("name", ""))
        self.name.setPlaceholderText(_('es. Sirena ingresso'))
        self.kind = QComboBox()
        for key, label in ACTION_KINDS.items():
            self.kind.addItem(_(label), key)
        self.kind.setCurrentIndex(max(0, self.kind.findData(a.get("kind", "light"))))
        from ..actions.passi import passi_di  # noqa: PLC0415
        from .catena_ui import StrisciaDiPassi  # noqa: PLC0415

        # La catena: un'azione vecchia e' una catena di **un** passo, costruita leggendo, quindi questa
        # finestra non ha un «prima» e un «dopo» — mostra sempre una striscia.
        self._casse: list = []            # le uscite audio del nodo: senza, un suono non si propone
        self._nodo_in_linea: bool | None = None
        self.catena = StrisciaDiPassi(self)
        self.catena.set_passi(passi_di(a))
        self.catena.ripeti_tutto.setValue(max(1, int(a.get("ripeti_tutto") or 1)))
        # Il comando resta, ma non si sceglie piu' da una tendina: lo porta il primo pezzo della
        # striscia. Serve ancora perche' da lui si ricava il tipo dell'azione, e perche' un'azione
        # senza «action» non e' valida — le configurazioni vecchie continuano a leggersi.
        self.cmd = QComboBox()
        self.cmd.setEditable(True)
        self.cmd.setCurrentText(a.get("action", ""))
        self.cmd.setToolTip(_("Il nome del comando sul nodo (l'elenco arriva dal nodo, se risponde)"))
        # Quando è attiva: era nascosto dentro il tipo — «dissuasore» voleva dire «sempre» — ed era
        # una cosa importante che nessuno poteva vedere né cambiare. Adesso è una domanda.
        self.quando = QComboBox()
        self.quando.addItem(_('Solo quando la casa è protetta (antifurto)'), "protetta")
        self.quando.addItem(_('Sempre, anche a protezione spenta'), "sempre")
        # Il terzo, dal 26/10 (Marco: «la scelta la metterei nel primo step»). Si chiama per quello
        # che è, non «senza limiti»: chi lo sceglie deve sapere che sta scegliendo uno scherzo, e
        # l'aiuto accanto dice cosa salta e cosa no.
        self.quando.addItem(_('Prank: scherzo, solo a modalità accesa sul nodo'), "prank")
        self.quando.setCurrentIndex(max(0, self.quando.findData(
            a.get("quando") or ("sempre" if a.get("kind") == "deterrent" else "protetta"))))
        self.mode = QComboBox()
        self.mode.addItem(_('Subito, in automatico'), "auto")
        self.mode.addItem(_('Chiedi conferma su Telegram'), "confirm")
        self.mode.setCurrentIndex(max(0, self.mode.findData(a.get("mode", "auto"))))
        self.delay = QSpinBox(); self.delay.setRange(0, 600); self.delay.setSuffix(" s")
        self.delay.setValue(int(float(a.get("delay", 0) or 0)))
        self.duration = QSpinBox(); self.duration.setRange(1, 1800); self.duration.setSuffix(" s")
        self.duration.setValue(int(float(a.get("duration", 60) or 60)))
        self.confirm_timeout = QSpinBox(); self.confirm_timeout.setRange(10, 600); self.confirm_timeout.setSuffix(" s")
        self.confirm_timeout.setValue(int(float(a.get("confirm_timeout", 60) or 60)))
        self.on_timeout = QComboBox()
        self.on_timeout.addItem(_('Non fare nulla'), "skip")
        self.on_timeout.addItem(_('Esegui comunque (solo con persona certa)'), "run")
        self.on_timeout.setCurrentIndex(max(0, self.on_timeout.findData(a.get("on_timeout", "skip"))))
        from ..actions.pannello_azione import fonti_da_mostrare  # noqa: PLC0415

        chosen = set(a.get("cameras") or [])
        viste, sentite = fonti_da_mostrare(camere, microfoni or [], chosen)
        # Due liste, un campo solo: un evento audio porta il nome del microfono nello stesso posto in
        # cui un evento video porta quello della camera, e il filtro dell'azione guarda il nome.
        self.cams = self._elenco_fonti(viste, chosen)
        self.mics = self._elenco_fonti(sentite, chosen)
        # Quando non c'è nessun microfono acceso, la riga non sparisce in silenzio: dice **dove** si
        # accende. Nascondere e basta fa cercare per dieci minuti una cosa che non c'è (24/10).
        self.mics_vuoti = QLabel("")
        self.mics_vuoti.setWordWrap(True)
        self.mics_vuoti.setStyleSheet(f"color:{T.MUTED}; font-size:{T.SIZE_SMALL}px;")
        # Da dove arriva quello che fa scattare l'azione. Non è un campo salvato: lo portano con sé gli
        # obiettivi (un obiettivo che comincia per «sound:» è un suono), e qui serve a filtrare quello
        # che si vede — compreso il far **sparire** le scelte che quella sorgente non usa.
        self.cams.itemChanged.connect(lambda _i: self._fonti_cambiate())
        self.mics.itemChanged.connect(lambda _i: self._fonti_cambiate())
        # Quello che la camera deve riconoscere sta **sotto le camere**, quello che il microfono deve
        # riconoscere **sotto i microfoni**. Erano in una griglia sola, mescolati, con un'iconcina a
        # distinguerli: Marco il 24/10, «anche se c'è l'icona non è chiaro». E aveva ragione due
        # volte, perché scegliendo una camera la griglia proponeva lo stesso un microfono.
        self._targets_chosen = set(a.get("targets") or (["person"] if a.get("kind") != "deterrent" else []))
        self._confidenze = dict(a.get("confidenze") or {})
        # Una soglia sola, scritta prima del 24/10, vale per tutti gli obiettivi: si apre su ognuno,
        # e al primo salvataggio diventa quello che è sempre stata, scritta per esteso.
        vecchia = float(a.get("min_confidence", 0) or 0)
        if vecchia > 0 and not self._confidenze:
            self._confidenze = {str(o): round(vecchia, 2) for o in self._targets_chosen}
        # Riquadri piu' piccoli e fino a sei per riga: le specie di uccelli da sole sono dieci, e a
        # quattro per riga la schermata diventava piu' alta dello schermo. L'altezza non si dichiara
        # piu' a mano: la griglia sa quante righe le servono e la chiede lei (vedi «_disponi»).
        # Alti abbastanza da contenere la tendina «AI», che compare dentro il riquadro quando lo
        # accendi: dieci cose spuntate facevano dieci righe sotto la griglia, e la soglia è una
        # proprietà di quella cosa lì, non una riga che le sta sotto (Marco, 24/10).
        self.targets_cam = GrigliaDiSpunte(massimo=6, largo=122, alto=92)
        self.targets_cam.cambiata.connect(self._target_toggled)
        self.targets_mic = GrigliaDiSpunte(massimo=6, largo=122, alto=92)
        self.targets_mic.cambiata.connect(self._target_toggled)
        self.ai_cam, self.ai_mic = QLabel(""), QLabel("")
        for etichetta in (self.ai_cam, self.ai_mic):
            etichetta.setWordWrap(True)
            etichetta.setStyleSheet(f"color:{T.MUTED}; font-size:{T.SIZE_SMALL}px;")
        # dissuasori: suoni, pause, limiti
        self._sounds_chosen = set(a.get("sounds") or [])
        self.sounds = GrigliaDiSpunte()
        self.sounds.setMinimumHeight(90)
        self.sounds.cambiata.connect(self._sound_toggled)
        self.cooldown = QSpinBox(); self.cooldown.setRange(1, 240); self.cooldown.setSuffix(" min")
        self.cooldown.setValue(max(1, int(float(a.get("cooldown", 300) or 300) // 60)))
        self.max_day = QSpinBox(); self.max_day.setRange(1, 200)
        self.max_day.setValue(int(a.get("max_per_day", 40) or 40))
        self.hours = QLineEdit(a.get("hours", ""))
        self.hours.setPlaceholderText(_("es. 06:00-20:30 (vuoto = sempre)"))
        self.notify = QCheckBox(_("Avvisa su Telegram quando scatta"))
        self.notify.setChecked(bool(a.get("notify", True)))
        self.enabled = QCheckBox(_('Azione attiva'))
        self.enabled.setChecked(bool(a.get("enabled", True)))
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color:#9AA8BA; font-size:12px;")
        # L'ultima schermata chiede «la accendo adesso?»: e' il momento in cui si sta per accendere
        # una cosa, e fino a un istante prima si sono guardati campi singoli. Marco, 27/10: *«conviene
        # dire all'utente quale tipologia gli verra' associata e quali sono i motivi / budget»*. Il
        # tipo non l'ha scelto lui — lo portano quello che l'azione fa e quello che riconosce — quindi
        # questo e' anche l'unico punto in cui puo' scoprirlo prima di scoprirlo dal comportamento.
        self.riassunto_tipo = QLabel()
        self.riassunto_tipo.setWordWrap(True)
        self.riassunto_tipo.setTextFormat(Qt.TextFormat.RichText)
        self.riassunto_tipo.setStyleSheet(
            f"color:{T.TEXT}; font-size:{T.SIZE_SMALL}px; background:{T.PANEL}; "
            f"border:1px solid {T.LINE}; border-radius:10px; padding:10px;")

        # L'ordine è quello in cui si ragiona: come si chiama, se è accesa, che tipo è, **cosa la fa
        # scattare** — che è la domanda vera e stava a metà pagina — e solo dopo il resto.
        # L'interruttore sta in cima perché è la cosa più importante della finestra: un'azione spenta
        # non fa niente, e scoprirlo in fondo alla pagina è tardi (22/10).
        # Le righe si **dichiarano**, non si montano: la finestra le mette tutte in un modulo solo, il
        # wizard le divide in schermate. Una dichiarazione sola vuol dire che un campo aggiunto domani
        # compare in tutte e due, e che le regole di «_update» valgono in tutte e due senza copiarle.
        # La «Durata» dell'azione non si chiede più. Era il tempo di quando un'azione faceva una cosa
        # sola; con la striscia ogni pezzo porta il suo (secondi per i suoni generati, ripetizioni per
        # quelli registrati), e quel campo restava lì senza decidere niente di visibile — Marco l'ha
        # chiesto il 23/10: «duration nella prima finestra non serve a nulla, dove viene usato?».
        # Non è sparito dal dato: si calcola dal pezzo più lungo (vedi «durata_azione_da»), perché
        # serve ancora come tetto e per i limiti. Ma non è una domanda da fare.
        self.duration.setVisible(False)
        self.riga_tempi = self._riga_doppia(self.delay, "", "", None)
        self.riga_limiti = self._riga_doppia(self.cooldown, _("Scatti massimi al giorno"), "massimi",
                                             self.max_day)
        # Il «Tipo» non c'è più: lo portano i comandi della striscia (vedi «tipo_azione_da»). La
        # tendina resta, invisibile, perché da lei dipendono le regole — ma non è più una domanda.
        self.kind.setVisible(False)
        self._righe = [
            ("nome", _("Nome"), self.name),
            ("tipologia", "", self.riassunto_tipo),
            ("acceso", "", self.enabled),
            ("camere", _('Da quali camere'), self.cams),
            ("obiettivi_camera", _('Cosa far riconoscere al modello AI'), self.targets_cam),
            ("ai_camera", "", self.ai_cam),
            ("microfoni", _('Da quali microfoni'), self.mics),
            ("microfoni_mancanti", "", self.mics_vuoti),
            ("obiettivi_microfono", _('Cosa far riconoscere al modello AI'), self.targets_mic),
            ("ai_microfono", "", self.ai_mic),
            ("cosa_fa", _('Cosa fa'), self.catena),
            ("quando_attiva", _('Quando è attiva'), self.quando),
            ("quando_parte", _('Quando parte'), self.mode),
            ("ritardo", _("Ritardo"), self.riga_tempi),
            ("attesa_conferma", _('Attesa della conferma'), self.confirm_timeout),
            ("se_nessuno_conferma", _('Se nessuno conferma'), self.on_timeout),
            ("suoni_emessi", _("Suoni da emettere"), self.sounds),
            ("pausa", _("Pausa tra due scatti"), self.riga_limiti),
            ("fascia", _("Fascia oraria"), self.hours),
            ("avvisa", "", self.notify),
            ("aiuto", "", self.hint),
        ]
        self._dove: dict[int, QFormLayout] = {}
        self._monta_tutto()
        self.kind.currentIndexChanged.connect(lambda _i: self._update())
        self.mode.currentIndexChanged.connect(lambda _i: self._update())
        # «Quando è attiva» cambia le regole, quindi cambia la finestra: scegliendo Prank si
        # riaprono «persona» fra gli obiettivi e l'aiuto giusto. Senza questa riga la scelta non
        # avrebbe effetto finché non si toccava qualcos'altro — un campo che sembra non funzionare.
        self.quando.currentIndexChanged.connect(lambda _i: self._update())
        self.cmd.currentIndexChanged.connect(lambda _i: self._command_picked())
        self.catena.cambiata.connect(self._catena_cambiata)
        self.targets_cam.cambiata.connect(self._catena_cambiata)
        self.targets_mic.cambiata.connect(self._catena_cambiata)
        self._commands: list[dict] = []
        # I comandi non sono più di «un nodo»: sono di ogni dispositivo. Un pezzo può farsi altrove
        # (23/10, tappa 3), e due nodi possono avere un comando con lo stesso nome e due tipi
        # diversi — quindi l'elenco che gira porta il dispositivo scritto su ogni voce.
        self._comandi_per: dict = {}
        self._dispositivi: list = []
        self._uscite_per: dict = {}
        self._uscita_in_uso: dict = {}   # la cassa configurata di ogni dispositivo (API 1.11)
        self._suoni_propri: list | None = None
        self._fill_sounds([])
        self._update()

    def _monta_tutto(self) -> None:
        """Tutte le righe in un modulo solo. Il wizard sovrascrive questo, e nient'altro."""
        f = QFormLayout(self)
        f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self._form = f
        self._monta(f, [chiave for chiave, _t, _w in self._righe])
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("OK")
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    SENZA_ETICHETTA: set = set()     # righe la cui etichetta ripeterebbe il titolo della schermata

    def _monta(self, form: QFormLayout, chiavi: list[str]) -> None:
        """Mette nel modulo le righe chieste, e ricorda **dove** sono finite.

        Serve a «_set_row»: una riga si nasconde nel modulo che la contiene, e col wizard i moduli
        sono quattro. Senza questa mappa, nascondere una riga della terza schermata dalla prima
        sarebbe un errore silenzioso — la riga resterebbe lì, visibile, e nessuno direbbe niente.
        """
        for chiave in chiavi:
            voce = next((r for r in self._righe if r[0] == chiave), None)
            if voce is None:
                continue
            _c, testo, widget = voce
            if chiave in self.SENZA_ETICHETTA:
                # Il titolo della schermata lo dice già: scriverlo due volte nella stessa pagina è
                # rumore, e ruba la larghezza alla striscia (23/10).
                form.addRow(widget)
            else:
                form.addRow(self._etichetta(testo, chiave), widget) if testo else form.addRow(widget)
            self._dove[id(widget)] = form

    def set_dispositivi(self, elenco: list) -> None:
        """I dispositivi fra cui un pezzo può scegliere: oggi i nodi abbinati.

        «Questo computer» non è qui: sa ascoltare e avvisare, non ha morsetti. Comparirà quando anche
        i blocchi di ascolto si sceglieranno nella striscia — metterlo adesso vorrebbe dire lasciar
        costruire un pezzo che non partirà mai (23/10).
        """
        self._dispositivi = list(elenco or [])
        self._aggiorna_contesto()

    def set_comandi_di(self, dispositivo: str, cmds: list[dict]) -> None:
        """I comandi di un dispositivo diverso da quello dell'azione: servono al «Dove» del pezzo."""
        self._comandi_per[str(dispositivo)] = [dict(c, dispositivo=str(dispositivo)) for c in (cmds or [])]
        self._aggiorna_contesto()

    def _il_mio_dispositivo(self) -> str:
        return ("nodo:" + str(self.node_id)) if self.node_id else ""

    def _tutti_i_comandi(self) -> list[dict]:
        """L'unione dei comandi di tutti i dispositivi, ognuno con scritto da dove viene."""
        fuori: list[dict] = []
        for cmds in self._comandi_per.values():
            fuori.extend(cmds)
        return fuori

    def _aggiorna_contesto(self) -> None:
        self.catena.set_contesto(self._tutti_i_comandi(), casse=self._casse,
                                 nodo_in_linea=self._nodo_in_linea,
                                 avvisa=self.notify.isChecked(),
                                 durata_azione=float(self.duration.value()),
                                 dispositivi=self._dispositivi,
                                 dispositivo=self._il_mio_dispositivo(),
                                 suoni_propri=self._suoni_propri,
                                 uscite_per=self._uscite_per,
                                 uscita_in_uso=self._uscita_in_uso)

    def set_node_commands(self, cmds: list[dict]) -> None:
        """Riempie l'elenco dei comandi con quelli dichiarati dal nodo."""
        current = self.cmd.currentText()
        self._commands = cmds
        self.cmd.blockSignals(True)
        self.cmd.clear()
        for c in cmds:
            self.cmd.addItem(f"{c.get('nome')}", c.get("nome"))
        self.cmd.setCurrentText(current or (cmds[0].get("nome", "") if cmds else ""))
        self.cmd.blockSignals(False)
        # Un'azione nuova nasce **con un pezzo dentro**, non con una striscia vuota. Prima la tendina
        # arrivava già col primo comando scelto, e il nome e il tipo si riempivano da lì: lasciare la
        # striscia vuota faceva una finestra che si contraddice — «tipo: sirena, durata 60 s» sopra, e
        # sotto «non c'è niente da fare».
        if not self.catena.passi():
            nome = self.cmd.currentText().strip() or (cmds[0].get("nome", "") if cmds else "")
            if nome:
                from ..actions.pannello_catena import passo_proposto  # noqa: PLC0415

                passo = passo_proposto(f"comando:{nome}", cmds)
                if passo is not None:
                    # La durata la porta il **pezzo**, non la casella «durata» dell'azione: quella è
                    # il tetto e la finestra in cui l'azione risulta in corso, e vale 60 s di suo.
                    # Prendendola da lì, una sirena appena creata nasceva da un minuto — Marco,
                    # 25/10: *«la sirena parte impostata a 60 secondi, io metterei 5»*. Adesso i
                    # cinque secondi sono la proposta del pezzo (DURATA_PROPOSTA), e il minuto
                    # dell'azione resta a fare il tetto, che è il suo mestiere.
                    self.catena.set_passi([passo])
        self._comandi_per[self._il_mio_dispositivo()] = [
            dict(c, dispositivo=self._il_mio_dispositivo()) for c in cmds]
        self._aggiorna_contesto()
        if not current:
            self._command_picked()
        else:
            c = next((x for x in cmds if x.get("nome") == current), {})
            self._fill_sounds(c.get("suoni") or [])

    def set_uscite_di(self, dispositivo: str, uscite: list, in_uso: str = "") -> None:
        """Le casse di un dispositivo: da lì il «+» fa scegliere **da quale** suonare (API 1.10).

        ``in_uso`` è quella configurata sul nodo, cioè cosa vuol dire un pezzo che non nomina la
        cassa. Serve a riconoscere due pezzi che finirebbero sulla stessa linea quando uno la nomina
        e l'altro no: senza, quel caso resterebbe un «non si sa» e lo scoprirebbe il nodo quando
        l'azione scatta.
        """
        self._uscite_per[str(dispositivo)] = list(uscite or [])
        if in_uso:
            self._uscita_in_uso[str(dispositivo)] = str(in_uso)
        self._aggiorna_contesto()

    def set_suoni_del_nodo(self, nomi: list | None) -> None:
        """Quali suoni il nodo fa da sé: da qui viene l'icona del «+» (altoparlante o nota)."""
        self._suoni_propri = list(nomi) if nomi is not None else None
        self._aggiorna_contesto()

    def set_stato_nodo(self, casse: list | None = None, in_linea: bool | None = None) -> None:
        """Quello che solo la finestra grande sa: se il nodo risponde e che casse ha attaccate.

        Serve alla striscia per due cose, e sono le due che rendono onesto il «+»: non proporre un
        suono a un nodo senza cassa (dicendo **cosa manca**, non nascondendolo), e non offrire una
        prova che fallirebbe perche' il nodo non c'e'.
        """
        self._casse = list(casse or [])
        self._nodo_in_linea = in_linea
        self._aggiorna_contesto()

    def _catena_cambiata(self) -> None:
        """Il primo pezzo della striscia e' il comando dell'azione: da lui si ricava il tipo.

        Prima il tipo si scopriva scegliendo un comando in una tendina; ora si scopre costruendo la
        catena, che e' la stessa cosa detta con la mano invece che con un elenco.
        """
        from ..actions.pannello_catena import nome_libero, tipo_azione_da  # noqa: PLC0415

        if self.kind.currentData() in MANUAL_KINDS:
            # «Ascolta da Telegram» non fa niente al nodo: non ha comandi, non ha una striscia, e non
            # deve diventare una sirena perché in configurazione c'era rimasto scritto un comando.
            return
        passi = self.catena.passi()
        primo = passi[0].comando if passi else ""
        if primo and primo != self.cmd.currentText():
            self.cmd.blockSignals(True)
            self.cmd.setCurrentText(primo)
            self.cmd.blockSignals(False)
            if not self.name.text().strip():
                c = next((x for x in self._commands if x.get("nome") == primo), None)
                if c:
                    self.name.setText(nome_libero(c.get("descrizione") or c["nome"], self.taken_names))
            self._fill_sounds(next((x.get("suoni") or [] for x in self._commands
                                    if x.get("nome") == primo), []))
        # Il tipo non lo decide il primo pezzo: lo decide **tutta** la catena, con la regola più
        # stretta che vince. Un dissuasore in fondo rende dissuasore anche un'azione che comincia
        # con una luce, perché «non scatta per le persone» non si ammorbidisce aggiungendo pezzi.
        # I soggetti contano: la stessa voce puntata su un gatto fa un dissuasore (che scatta a
        # protezione spenta, con pausa e tetto giornaliero), puntata su una persona fa un'azione
        # antintrusione. Il comando da solo non lo sa (23/10).
        tipo = tipo_azione_da(passi, self._commands, se_vuota=self.kind.currentData() or "generic",
                              soggetti=sorted(self._scelte_obiettivi()))
        if tipo != self.kind.currentData():
            self.kind.setCurrentIndex(max(0, self.kind.findData(tipo)))

    def _command_picked(self):
        """Il comando scelto porta il tipo dell'azione e i suoi suoni. **Non porta il nome.**

        Fino al 23/10 qui dentro il nome si proponeva da solo, leggendo la descrizione del primo
        comando che il nodo dichiarava: aprendo «Nuova azione» ci si trovava scritto «Sirena dalla
        cassa (max 3 minuti) 3» — con il contatore, perche' le prime due esistevano gia'. Aveva senso
        quando un'azione nasceva **da** un comando del nodo; da quando le proposte stanno nella
        libreria e' solo un campo da svuotare a mano ogni volta, e per giunta un nome che descrive il
        dispositivo invece di dire a cosa serve l'azione. Una cosa nuova nasce senza nome.
        """
        c = next((x for x in self._commands if x.get("nome") == self.cmd.currentText()), None)
        if c:
            self.kind.setCurrentIndex(max(0, self.kind.findData(kind_from_node(c.get("tipo")))))
        self._fill_sounds((c or {}).get("suoni") or [])

    def _sound_toggled(self) -> None:
        self._sounds_chosen = set(self.sounds.scelte())

    def _etichetta(self, testo: str, chiave: str) -> QWidget:
        """L'etichetta di una riga con la (i) accanto: il testo spiega **cosa**, la (i) spiega a cosa serve.

        Una finestra con diciotto campi è una finestra in cui metà delle scelte si lasciano come sono
        perché non si sa cosa facciano. La spiegazione non sta in un manuale: sta accanto al campo.
        """
        from ..actions.pannello_azione import aiuto  # noqa: PLC0415

        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(QLabel(testo))
        spiega = QLabel("ⓘ")
        spiega.setToolTip(aiuto(chiave))
        spiega.setStyleSheet(f"color:{T.MUTED}; font-size:13px;")
        spiega.setCursor(Qt.CursorShape.WhatsThisCursor)
        lay.addWidget(spiega)
        lay.addStretch(1)
        return w

    def _elenco_fonti(self, fonti: list[dict], scelte: set) -> QListWidget:
        """Un elenco di camere o di microfoni, con la spunta su quelli già scelti.

        Alto quanto serve, non 90 pixel sempre: una camera sola occupava lo spazio di tre, e ogni
        riga era alta il doppio del suo testo — «l'elenco è inutile stia su due righe con i controlli
        così lunghi» (Marco, 24/10). Oltre le quattro voci scorre, che è il punto in cui un elenco
        comincia a valere più di una riga alta.
        """
        from PySide6.QtCore import QSize  # noqa: PLC0415

        elenco = ElencoDiSpunte()
        elenco.setUniformItemSizes(True)
        quante = max(1, min(len(fonti), 4))
        elenco.setFixedHeight(quante * 26 + 6)
        # Largo quanto un campo, non quanto la finestra: una riga lunga mille pixel per scriverci
        # «Ingresso» e' una barra, non un controllo. E un po' d'aria fra il bordo e la scritta.
        elenco.setMaximumWidth(460)
        elenco.setStyleSheet("QListWidget::item { padding: 2px 10px; }")
        for c in fonti:
            it = QListWidgetItem(c["nome"] if c["accesa"] else _('{nome} · spenta', nome=c["nome"]))
            it.setData(Qt.ItemDataRole.UserRole, c["nome"])
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if c["nome"] in scelte else Qt.CheckState.Unchecked)
            it.setSizeHint(QSize(0, 24))
            elenco.addItem(it)
        return elenco

    def _riga_doppia(self, primo, testo: str, chiave: str, secondo) -> QWidget:
        """Due campi sulla stessa riga, con l'etichetta del secondo in mezzo.

        Ritardo e durata sono la stessa domanda («da quando, e per quanto»), e così pausa e scatti
        massimi: su due righe separate costringono l'occhio a scendere per leggere una cosa sola. Il
        contenitore serve anche a nasconderli **insieme**, che è sempre quello che si vuole.
        """
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(primo, 1)
        # Con un campo solo resta un campo solo: serve ancora il contenitore, perché è lui che si
        # nasconde quando la riga non ha senso.
        if secondo is not None:
            lay.addWidget(self._etichetta(testo, chiave))
            lay.addWidget(secondo, 1)
        return w

    @staticmethod
    def _fonti_spuntate(elenco) -> list[str]:
        """I nomi spuntati in un elenco di fonti. Il nome vero sta nel dato, non nel testo."""
        return [elenco.item(i).data(Qt.ItemDataRole.UserRole) for i in range(elenco.count())
                if elenco.item(i).checkState() == Qt.CheckState.Checked]

    def _nota_microfoni(self) -> str:
        """Cosa manca perché un microfono si possa usare. Vuoto = non manca niente.

        Due interruttori diversi, e nessuno dei due lo diceva: il nodo accende **il suo** microfono
        (Nodi → Audio del nodo), e lì accanto Mirto decide se ascoltarlo. Marco il
        24/10 aveva il primo acceso, il secondo spento, e in elenco non compariva niente — senza un
        motivo da nessuna parte.
        """
        if self.mics.count() == 0:
            return _('Nessun microfono acceso: accendilo in AI → Microfoni per questo computer, o in '
                     'Nodi → Configurazione → Audio per un nodo.')
        return ""

    def _fonti_cambiate(self) -> None:
        """Spuntata una camera o un microfono, compare sotto **la sua** griglia di riconoscimenti.

        Marco, 24/10: *«anche avendo scelto camera ingresso al primo step, la combo mi propone il
        microfono»*. C'era una tendina «sorgente» che chiedeva una cosa a cui la risposta stava due
        righe sopra, e una domanda del genere fa dubitare di aver capito. Non c'è più: lo dicono le
        spunte.

        Più camere spuntate vogliono dire **oppure** — scatta quella che vede per prima. Due persone
        davanti a due porte nello stesso istante non è il caso per cui vale la pena complicare la
        finestra, e il riconoscimento da cercare resta lo stesso per tutte (Marco, 24/10).
        """
        self._update()

    def _fill_sounds(self, names: list[str]) -> None:
        """Suoni offerti dal nodo per il comando scelto (più quelli già scelti, anche se il nodo è spento)."""
        self.sounds.blockSignals(True)
        self.sounds.set_voci([(n, n, "🔊", n in self._sounds_chosen, False)
                              for n in sorted(set(names) | self._sounds_chosen)])
        self.sounds.blockSignals(False)

    def _target_options(self, kind: str = "") -> list[tuple[str, str]]:
        """(chiave, testo) di tutto quello che un'azione può riconoscere.

        Fino al 23/10 dipendeva dal tipo: gli animali si vedevano **solo** scegliendo «dissuasore»
        nella tendina. Tolta la tendina — il tipo adesso si deduce da quello che l'azione fa scattare
        — quell'elenco diventava una trappola: gli animali non erano più raggiungibili da nessuna
        parte, e un'azione per i gatti non si poteva più creare. L'ha scoperto una prova delle
        finestre, non io.

        Adesso si vede tutto, in quest'ordine: le persone, i suoni, gli animali. Chi configura sceglie
        cosa deve riconoscere la camera, che è la domanda vera; il resto (i limiti, la pausa fra due
        scatti, se serve la protezione inserita) viene da sé.
        """
        from ..actions.engine import INTRUSION_SOUND_TARGETS  # noqa: PLC0415
        from ..audio.classes import SOUNDS, sound_key  # noqa: PLC0415
        from ..detection.labels import GROUPS, display_name  # noqa: PLC0415
        from ..detection.species import GROUPS as BIRDS  # noqa: PLC0415

        out = [("person", "🚨 " + _("Persona rilevata"))]
        out += [(t, "🔊 " + display_name(t)) for t in sorted(INTRUSION_SOUND_TARGETS)]
        out += [("bird", "🐦 " + _("Qualunque uccello"))]
        out += [(f"bird:{g}", "🐦 " + display_name(f"bird:{g}")) for g in BIRDS]
        animali = next(keys for gid, _n, keys in GROUPS if gid == "animali")
        out += [(k, "🐾 " + display_name(k)) for k in animali if k != "bird"]
        out += [(sound_key(k), "🔊 " + display_name(sound_key(k)))
                for k in ("bird", "dog") if k in SOUNDS and sound_key(k) not in INTRUSION_SOUND_TARGETS]
        return out

    def _rebuild_targets(self, kind: str) -> None:
        """Le due griglie: quello che una camera può riconoscere, quello che può riconoscere un
        microfono. Niente filtro da scegliere — la sorgente di un obiettivo è scritta nella sua
        chiave, e ognuno finisce sotto il suo input."""
        from ..actions.confidenze import scelte_tendina  # noqa: PLC0415
        from ..actions.pannello_azione import AUDIO, VIDEO, obiettivi_per_sorgente  # noqa: PLC0415
        from ..detection.labels import emoji_di  # noqa: PLC0415

        opzioni = self._target_options(kind)
        for griglia, sorgente in ((self.targets_cam, VIDEO), (self.targets_mic, AUDIO)):
            griglia.blockSignals(True)
            # «Persona» non è più bloccata: lo era perché un'antintrusione senza persone non ha
            # senso, ma con la scelta libera quel lucchetto vieterebbe un'azione per i soli gatti.
            # Al suo posto c'è un controllo: un'azione deve riconoscere almeno una cosa.
            # Ogni riquadro si porta dentro la sua tendina «AI»: le scelte dipendono da lui, perché
            # lo «standard» di una persona non è quello di un gatto né quello di un gruppo di uccelli.
            griglia.set_voci([(key, text, emoji_di(key), key in self._targets_chosen, False,
                               scelte_tendina(key, self._soglia_generale),
                               float(self._confidenze.get(key, 0) or 0))
                              for key, text in obiettivi_per_sorgente(opzioni, sorgente)])
            griglia.blockSignals(False)
        self._targets_kind = kind
        self._rifai_righe_ai()

    def _scelte_obiettivi(self) -> set:
        """Tutto quello che è spuntato, in tutt'e due le griglie."""
        return set(self.targets_cam.scelte()) | set(self.targets_mic.scelte())

    def _rifai_righe_ai(self) -> None:
        """Sotto ogni griglia, una riga per ogni rete in gioco: chi giudica, e con che numeri."""
        from ..actions.confidenze import modelli_di, riga_modello  # noqa: PLC0415

        leggi = self._soglia_generale
        for griglia, etichetta in ((self.targets_cam, self.ai_cam), (self.targets_mic, self.ai_mic)):
            scelti = sorted(griglia.scelte())
            mie = griglia.valori()
            quali = [riga_modello(m, leggi, mie=sum(1 for o in scelti
                                                    if o in mie and modelli_di([o]) == [m]))
                     for m in modelli_di(scelti)]
            etichetta.setText("<br>".join(quali))

    def _target_toggled(self) -> None:
        self._targets_chosen = self._scelte_obiettivi()
        # Le soglie si tengono da parte: la griglia si rifà a ogni cambio di larghezza, e quello che
        # hai scritto in una tendina non deve dipendere da quanto è larga la finestra.
        self._confidenze = {**self._confidenze, **self.targets_cam.valori(), **self.targets_mic.valori()}
        self._rifai_righe_ai()

    def _set_row(self, widget, visible: bool) -> None:
        form = self._dove.get(id(widget))
        if form is None:
            # Non è in nessun modulo: succede ai campi che una disposizione non mostra — il wizard non
            # chiede più il ritardo, ma «_update» continua a ragionarci sopra. Chiedere a un modulo di
            # nascondere una riga che non ha fa stampare a Qt «Invalid widget» a ogni aggiornamento.
            widget.setVisible(visible)
            return
        try:
            form.setRowVisible(widget, visible)
        except AttributeError:  # Qt < 6.4
            widget.setVisible(visible)

    def _update(self):
        kind = self.kind.currentData()
        manual = kind in MANUAL_KINDS
        deterrent = kind == "deterrent"
        prank = self.quando.currentData() == "prank"
        confirm = self.mode.currentData() == "confirm" and not deterrent
        # La striscia sparisce con le azioni manuali: «ascolta da Telegram» non fa niente al nodo, e
        # una striscia vuota su una cosa che non ha passi fa dubitare di aver capito.
        for wid in (self.riga_tempi, self.catena):
            self._set_row(wid, not manual)
        # Le camere e i microfoni sono **la** scelta di questa schermata, non un filtro che compare
        # se la sorgente lo permette: si vedono sempre, quando ce n'è almeno uno.
        from ..actions.pannello_azione import si_vede_la_griglia  # noqa: PLC0415

        c_è_camera = not manual and self.cams.count() > 0
        c_è_microfono = not manual and self.mics.count() > 0
        self._set_row(self.cams, c_è_camera)
        self._set_row(self.mics, c_è_microfono)
        # I riconoscimenti si vedono solo per un input che hai scelto: chiedere cosa deve riconoscere
        # una camera che non hai spuntato e' la solita domanda fuori posto (24/10).
        nuova = not self._apriva_una_azione
        chiede_cam = c_è_camera and si_vede_la_griglia(self._fonti_spuntate(self.cams),
                                                       self.targets_cam.scelte(), nuova)
        chiede_mic = c_è_microfono and si_vede_la_griglia(self._fonti_spuntate(self.mics),
                                                          self.targets_mic.scelte(), nuova)
        self._set_row(self.mics_vuoti, not manual and bool(self._nota_microfoni()))
        self.mics_vuoti.setText(self._nota_microfoni())
        # Sotto ogni input, quello che quell'input deve riconoscere — e sotto ancora, quanto sicuro
        # dev'essere il modello e chi è quel modello. Le soglie e la riga dell'AI compaiono quando
        # c'è almeno una cosa spuntata: righe vuote non dicono niente a nessuno.
        self._set_row(self.targets_cam, chiede_cam)
        self._set_row(self.ai_cam, chiede_cam and bool(self.targets_cam.scelte()))
        self._set_row(self.targets_mic, chiede_mic)
        self._set_row(self.ai_mic, chiede_mic and bool(self.targets_mic.scelte()))
        self._set_row(self.mode, not manual and not deterrent)
        self._set_row(self.confirm_timeout, not manual and confirm)
        self._set_row(self.on_timeout, not manual and confirm)
        # I suoni e l'avviso su Telegram sono del dissuasore; **pausa, tetto giornaliero e fascia
        # oraria no**: il motore li applica a ogni azione «sempre», e fino al 26/10 le caselle si
        # vedevano solo per i dissuasori. Un campanello si prendeva 5 minuti di pausa e 40 scatti al
        # giorno senza che nessuno potesse vederli né cambiarli — suoni, risuoni dopo un minuto, non
        # succede niente, e sembra un difetto del campanello. Un limite che si subisce senza poterlo
        # vedere e' lo stesso difetto dei budget dei dissuasori, un piano piu' sotto.
        for wid in (self.sounds, self.notify):
            self._set_row(wid, deterrent)
        con_limiti = not manual and (deterrent or self.quando.currentData() == "sempre")
        for wid in (self.riga_limiti, self.hours):
            self._set_row(wid, con_limiti)
        if not manual and getattr(self, "_targets_kind", None) != ("deterrent" if deterrent else "intrusion"):
            # «persona» si toglie da sé quando l'azione diventa un dissuasore — tranne se è un prank,
            # che è l'unico caso in cui quel bersaglio è ammesso. Il motore l'eccezione la aveva dal
            # 26/10, questa riga no: togliere la spunta qui voleva dire che l'unica cosa per cui il
            # tipo Prank esiste non si poteva nemmeno scegliere.
            if deterrent and not prank and getattr(self, "_targets_kind", None) is not None:
                self._targets_chosen.discard("person")
            self._rebuild_targets(kind)
            self._targets_kind = "deterrent" if deterrent else "intrusion"
        self.duration.setMaximum(180 if kind == "siren" else 30 if deterrent else 1800)
        if hasattr(self, "riassunto_tipo"):
            from ..actions.presets import tipologia_di  # noqa: PLC0415
            from .testi import spiega_tipologia  # noqa: PLC0415

            quale = tipologia_di({"quando": self.quando.currentData(), "kind": kind})
            # `_soglia_generale` è un lettore, non un numero: qui serve quello delle persone, che è
            # la soglia di cui parla l'antifurto.
            soglia = float(self._soglia_generale("actions.person_threshold", 0.6) or 0.6)
            self.riassunto_tipo.setText(spiega_tipologia(
                quale, soglia,
                {"cooldown": self.cooldown.value() * 60, "max_per_day": self.max_day.value(),
                 "hours": self.hours.text().strip()} if con_limiti else None))
        if deterrent and prank:
            # Lo stesso tipo, regole diverse: dirgli «mai per le persone» mentre gli si lascia
            # spuntare «Persona rilevata» sarebbe la finestra che si contraddice da sola.
            self.hint.setText(_("Scherzo: scatta solo mentre la modalità Prank è accesa su quel nodo e la casa "
                                "non è protetta, e può puntare un suono su una persona — è l'unico caso in cui "
                                "si può. Niente pausa, niente tetto giornaliero, niente fascia oraria; restano i "
                                "30 secondi per scatto del nodo e la conferma forte della persona. Rispetta gli "
                                "orari di quiete e chi ti sta intorno."))
        elif deterrent:
            self.hint.setText(_("Scatta sempre, anche con la protezione disattivata, ma mai per le persone. "
                                "I suoni cambiano a ogni scatto perché gli animali si abituano; il nodo suona al "
                                "massimo 30 secondi per volta e 2 minuti ogni ora. Rispetta gli orari di quiete "
                                "e il regolamento del tuo Comune."))
        elif manual:
            self.hint.setText(_("L'audio non parte mai da solo: si usa da Telegram (/ascolta, messaggi vocali)."))
        elif kind == "siren":
            self.hint.setText(_("La sirena suona al massimo 3 minuti (e 3 minuti ogni mezz'ora in totale). Consigliato: chiedi conferma, oppure un ritardo per poter disattivare in tempo."))
        elif kind == "generic":
            self.hint.setText(_("Accende un'uscita del nodo (relè, presa, servizio HTTP) e la spegne da sola allo scadere. "
                                "Che cosa colleghi è una tua scelta e responsabilità: nessun dispositivo deve poter "
                                "fare del male. Consigliato: chiedi conferma su Telegram."))
        else:
            self.hint.setText(_('Parte solo con la protezione attiva, quando una persona è rilevata con certezza.'))

    def _accept(self):
        name = self.name.text().strip()
        cmd = self.cmd.currentText().strip()
        if not name or not cmd:
            QMessageBox.warning(self, _("Azione"),
                                _('Serve il nome, e almeno un pezzo in «Cosa fa».'))
            return
        if name in self.taken_names:
            QMessageBox.warning(self, _("Azione"), _("Esiste già un'azione «{name}»: scegli un altro nome (es. aggiungendo il nome del nodo).", name=name))
            return
        kind = self.kind.currentData()
        a = {"name": name, "node": self.node_id, "action": cmd, "kind": kind,
             "enabled": self.enabled.isChecked()}
        targets = sorted(self._scelte_obiettivi())

        if kind == "deterrent":
            # Un prank tiene «persona»: era il difetto che Marco ha trovato il 26/10 provando la sua
            # azione «halloween» — si scegliva Prank, si spuntava Persona, e il salvataggio rispondeva
            # «scegli almeno un animale o un uccello». Cioè l'unica cosa per cui quel tipo esiste.
            if self.quando.currentData() != "prank":
                targets = [t for t in targets if t != "person"]
            if not targets:
                QMessageBox.warning(self, _("Azione"), _("Scegli almeno un animale o un uccello che fa scattare il dissuasore.")
                                    if self.quando.currentData() != "prank" else
                                    _("Scegli almeno una cosa che fa scattare lo scherzo."))
                return
            hours = self.hours.text().strip()
            from ..actions.engine import _parse_hours  # noqa: PLC0415

            if hours and not _parse_hours(hours):
                QMessageBox.warning(self, _("Azione"), _("Fascia oraria non valida: scrivi per esempio 06:00-20:30."))
                return
            sounds = sorted(self.sounds.scelte())
            a.update(targets=targets, sounds=sounds, cooldown=self.cooldown.value() * 60,
                     max_per_day=self.max_day.value(), hours=hours, notify=self.notify.isChecked())
        elif kind not in MANUAL_KINDS:
            a["targets"] = targets or ["person"]
            if self.quando.currentData() == "sempre":
                # Salvati solo se le caselle si vedono: scriverli su un'antifurto vorrebbe dire
                # mettere un tetto giornaliero su una sirena, che è un difetto, non una cautela.
                ore = self.hours.text().strip()
                from ..actions.engine import _parse_hours  # noqa: PLC0415

                if ore and not _parse_hours(ore):
                    QMessageBox.warning(self, _("Azione"), _("Fascia oraria non valida: scrivi per esempio 06:00-20:30."))
                    return
                a.update(cooldown=self.cooldown.value() * 60, max_per_day=self.max_day.value(), hours=ore)
        if kind not in MANUAL_KINDS:
            from ..actions.pannello_catena import durata_azione_da  # noqa: PLC0415

            passi = self.catena.passi()
            a["quando"] = self.quando.currentData()
            a.update(mode="auto" if kind == "deterrent" else self.mode.currentData(), delay=self.delay.value(),
                     duration=durata_azione_da(passi, self.duration.value()))
            # Una catena di un passo solo non si scrive: resta «action» + «duration», come e' sempre
            # stata. Cosi' una configurazione che non usa le catene non cambia di una riga, e un
            # giorno in cui si torna indietro non c'e' niente da convertire.
            if len(passi) > 1 or any(p.ripeti > 1 or p.pausa or p.suono for p in passi):
                a["passi"] = self.catena.passi_salvabili()
                if self.catena.ripeti_tutto.value() > 1:
                    a["ripeti_tutto"] = self.catena.ripeti_tutto.value()
            if a["mode"] == "confirm":
                a.update(confirm_timeout=self.confirm_timeout.value(), on_timeout=self.on_timeout.currentData())
            # Il nome vero sta nel dato, non nel testo: una camera spenta si mostra come «nome ·
            # spenta», e salvare quella scritta vorrebbe dire salvare una camera che non esiste.
            cams = self._fonti_spuntate(self.cams) + self._fonti_spuntate(self.mics)
            if cams:
                a["cameras"] = cams
            # Una soglia per obiettivo. Quelle lasciate su «standard» non si scrivono: seguono la
            # soglia generale anche quando il modello si aggiorna, che e' il motivo per cui Marco le
            # voleva separate (24/10). «min_confidence» non si scrive piu': se c'era, al primo
            # salvataggio e' gia' diventata una soglia per ogni obiettivo.
            from ..actions.confidenze import pulisci  # noqa: PLC0415

            soglie = pulisci({**self._confidenze, **self.targets_cam.valori(),
                              **self.targets_mic.valori()}, targets)
            if soglie:
                a["confidenze"] = soglie
        # L'avviso sulle normative **non si mostra da qui**. Una finestra modale dentro «_accept»
        # vuol dire che chiunque salvi un'azione senza qualcuno davanti — una prova automatica, per
        # esempio — resta ad aspettare un clic che non arriva mai: la suite di Marco si è piantata
        # così. Ma soprattutto non è compito di questa finestra: lei produce l'azione, chi l'ha
        # aperta decide cosa dire. Vedi «_open_action_dialog».
        self.result_action = a
        self.accept()


class LibreriaDialog(QDialog):
    """Le azioni già pronte di **tutti** i nodi: si spuntano e si importano, anche più d'una.

    Non è un elenco di cose da leggere: è una spesa. Ogni voce dice cosa fa in una riga, la si accende
    col riquadro, e si porta a casa tutto insieme — perché chi apre questa finestra sa già cosa vuole,
    e farglielo fare una alla volta sarebbe solo un modo di contare i clic.

    Dal 24/10 la libreria è **delle azioni**, non di un nodo: due nodi che propongono la stessa cosa
    la mostrano una volta sola, con scritto su quanti nodi c'è, e il nodo si sceglie qui accanto —
    che è il momento in cui quella domanda ha senso.
    """

    def __init__(self, voci: list[dict], parent=None, nomi: dict | None = None):
        super().__init__(parent)
        from ..actions.pannello_azioni import quante_volte_presente  # noqa: PLC0415

        self.setWindowTitle(_('Libreria azioni'))
        self.resize(680, 480)
        self.voci = list(voci)
        self.nomi = dict(nomi or {})
        self.scelte: list[dict] = []
        self.dove: dict[int, QComboBox] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(10)
        testa = QLabel(_('Azioni già pronte sui tuoi nodi. Scegli quelle che vuoi e importale: '
                         'restano modificabili come tutte le altre.'))
        testa.setWordWrap(True)
        testa.setStyleSheet(f"color:{T.MUTED}; font-size:{T.SIZE_SMALL}px;")
        lay.addWidget(testa)
        self.elenco = QListWidget()
        for i, v in enumerate(self.voci):
            prima = next(iter(v["per_nodo"].values()))
            voce = QListWidgetItem(f"{v['titolo']}  ·  {quante_volte_presente(v, self.nomi)}\n"
                                   f"{describe_action(prima).partition('  ·  ')[2]}")
            voce.setFlags(voce.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            voce.setCheckState(Qt.CheckState.Unchecked)
            self.elenco.addItem(voce)
            if len(v["nodi"]) > 1:
                # La stessa azione su più nodi: **dove** crearla si sceglie qui, riga per riga.
                combo = QComboBox()
                for nid in v["nodi"]:
                    combo.addItem(self.nomi.get("nodo:" + nid, nid), nid)
                riga = QWidget()
                dentro = QHBoxLayout(riga)
                dentro.setContentsMargins(0, 0, 8, 0)
                dentro.addStretch(1)
                dentro.addWidget(combo, 0)
                voce.setSizeHint(riga.sizeHint().expandedTo(voce.sizeHint()))
                self.elenco.setItemWidget(voce, riga)
                self.dove[i] = combo
        lay.addWidget(self.elenco, 1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        self.importa = QPushButton(_("Importa"))
        self.importa.setDefault(True)
        self.importa.clicked.connect(self._importa)
        bb.addButton(self.importa, QDialogButtonBox.ButtonRole.AcceptRole)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.elenco.itemChanged.connect(lambda _i: self._aggiorna())
        self._aggiorna()

    def _aggiorna(self) -> None:
        quante = len(self._spuntate())
        self.importa.setEnabled(bool(quante))
        self.importa.setText(_("Importa ({n})", n=quante) if quante else _("Importa"))

    def _spuntate(self) -> list[dict]:
        fuori = []
        for i, v in enumerate(self.voci):
            if self.elenco.item(i).checkState() != Qt.CheckState.Checked:
                continue
            nid = self.dove[i].currentData() if i in self.dove else v["nodi"][0]
            fuori.append(v["per_nodo"][nid])
        return fuori

    def _importa(self) -> None:
        self.scelte = self._spuntate()
        self.accept()


class ActionWizard(ActionDialog):
    """La stessa azione, chiesta in quattro schermate invece che in un modulo da diciotto campi.

    Non è una seconda finestra: è la **stessa** — stessi campi, stesse regole, stesso salvataggio —
    disposta in pagine. Il modulo unico è morto il 22 ottobre sera, dopo che Marco l'ha visto e ha
    detto che era un casino: aveva ragione, e la cosa giusta non era sistemarlo, era smettere di
    chiedere diciotto cose insieme.

    L'ordine delle schermate è quello in cui si racconta una cosa a voce: *come si chiama*, *cosa la
    fa scattare*, *cosa fa*, e alla fine *la accendo adesso?*. La prova del nove, a ogni schermata:
    «quello che vedo qui dipende da quello che ho scelto prima?» Se no, quella roba è nella schermata
    sbagliata.

    Il **tipo** non è una schermata e non è una domanda: lo portano i comandi che si mettono nella
    striscia (vedi ``tipo_azione_da``). Chi configura sa che vuole far suonare la sirena, non che
    quell'azione «è di tipo sirena».
    """

    SENZA_ETICHETTA = {"cosa_fa", "obiettivi_camera", "obiettivi_microfono"}

    PAGINE = (
        (N_('Informazioni generali'), N_('Il nome lo leggerai su Telegram e nell\'elenco delle azioni.'),
         ["nome", "quando_attiva", "quando_parte", "attesa_conferma",
          "se_nessuno_conferma", "fascia"]),
        (N_('Cosa la fa scattare'), N_('Da quali dispositivi, e che cosa devono riconoscere.'),
         ["camere", "obiettivi_camera", "ai_camera",
          "microfoni", "microfoni_mancanti", "obiettivi_microfono", "ai_microfono"]),
        (N_('Imposta le azioni'),
         N_('Una serie di operazioni in sequenza, su qualsiasi dispositivo rilevato: il «+» propone '
            'quello che sanno fare i dispositivi collegati — i nodi, e le casse abbinate a loro. '
            'Per chiedere qualcosa che qui non c\'è, apri una '
            '<a href="{discussioni}">discussione sul progetto</a>.'),
         ["cosa_fa", "suoni_emessi", "pausa", "avvisa"]),
        (N_('La accendo adesso?'), N_('Si può lasciare spenta e accenderla quando vuoi.'),
         ["tipologia", "acceso", "aiuto"]),
    )

    def _monta_tutto(self) -> None:
        self.setWindowTitle(_('Nuova azione'))
        self._e_nuova = not self._apriva_una_azione
        # Larga abbastanza da far stare sei riquadri per riga: la larghezza si paga una volta, e
        # in cambio la schermata «cosa la fa scattare» passa da cinque righe di riquadri a tre.
        self.resize(1000, 620)
        fuori = QVBoxLayout(self)
        fuori.setContentsMargins(18, 16, 18, 14)
        fuori.setSpacing(10)
        self.passo_titolo = QLabel("")
        # Maiuscolo e più grande: è l'unica cosa che dice a che punto sei, e a 18 px in minuscolo si
        # leggeva come una riga qualsiasi della pagina (23/10). Il maiuscolo si fa sul testo, non col
        # foglio di stile: Qt non ha «text-transform».
        self.passo_titolo.setStyleSheet(f"font-size:{T.SIZE_TITLE + 6}px; font-weight:700; "
                                        f"letter-spacing:1px;")
        self.passo_sotto = QLabel("")
        self.passo_sotto.setWordWrap(True)
        # Testo ricco e collegamenti che si aprono: nella terza schermata c'è l'indirizzo a cui
        # scrivere per chiedere una cosa che il programma non sa ancora fare.
        self.passo_sotto.setTextFormat(Qt.TextFormat.RichText)
        self.passo_sotto.setOpenExternalLinks(True)
        self.passo_sotto.setStyleSheet(f"font-size:{T.SIZE_SMALL}px; color:{T.MUTED};")
        self.passo_quanti = QLabel("")
        self.passo_quanti.setStyleSheet(f"font-size:{T.SIZE_BODY}px; color:{T.MUTED}; "
                                        f"letter-spacing:1px;")
        testa = QHBoxLayout()
        testa.addWidget(self.passo_titolo, 1)
        testa.addWidget(self.passo_quanti, 0, Qt.AlignmentFlag.AlignRight)
        fuori.addLayout(testa)
        fuori.addWidget(self.passo_sotto)
        self.pile = QStackedWidget()
        self._moduli: list[QFormLayout] = []
        for _titolo, _sotto, chiavi in self.PAGINE:
            pagina = QWidget()
            # Il modulo in alto e lo spazio che avanza **sotto**, dentro la pagina. Senza questo, lo
            # spazio in più si infilava fra il titolo e il sottotitolo e li allontanava come una
            # fisarmonica: nella terza schermata erano duecento pixel di niente (23/10).
            dentro = QVBoxLayout(pagina)
            dentro.setContentsMargins(0, 0, 0, 0)
            f = QFormLayout()
            f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            self._monta(f, chiavi)
            dentro.addLayout(f)
            dentro.addStretch(1)
            self._moduli.append(f)
            self.pile.addWidget(pagina)
        # «_form» resta il primo modulo: è quello che le prove vecchie interrogano, ed è anche il
        # ripiego di «_set_row» per una riga che non fosse finita in nessuna pagina.
        self._form = self._moduli[0]
        fuori.addWidget(self.pile, 1)
        # La banda dei pulsanti si vede: fondo suo e una riga sopra. Prima galleggiava in fondo a una
        # pagina alta e vuota, e non si capiva che fosse la barra di navigazione (23/10).
        banda = QFrame()
        banda.setObjectName("banda")
        banda.setStyleSheet(f"""QFrame#banda {{ background: rgba(255,255,255,0.04);
                                                border-top: 1px solid {T.LINE}; }}""")
        righello = QHBoxLayout(banda)
        righello.setContentsMargins(14, 10, 14, 10)
        righello.setSpacing(8)
        self.indietro = QPushButton(_('Indietro'))
        self.indietro.clicked.connect(lambda: self.vai(self.pile.currentIndex() - 1))
        self.avanti = QPushButton(_('Avanti'))
        self.avanti.setObjectName("primario")
        self.avanti.setDefault(True)
        self.avanti.clicked.connect(self._avanti)
        self.fine = QPushButton(_('Salva'))
        self.fine.setObjectName("primario")   # l'ultimo passo e' un'azione principale come «Avanti»
        self.fine.clicked.connect(self._accept)
        annulla = QPushButton(_("Annulla"))
        annulla.clicked.connect(self.reject)
        righello.addWidget(annulla)
        righello.addStretch(1)
        righello.addWidget(self.fine)          # staccato: salvare non è «andare avanti»
        righello.addSpacing(28)
        righello.addWidget(self.indietro)
        righello.addWidget(self.avanti)
        self._banda = banda
        fuori.addWidget(banda)
        self.vai(0)

    def vai(self, i: int) -> None:
        """Alla schermata «i», senza uscire dai bordi."""
        i = max(0, min(len(self.PAGINE) - 1, int(i)))
        self.pile.setCurrentIndex(i)
        titolo, sotto, _chiavi = self.PAGINE[i]
        self.passo_titolo.setText(_(titolo).upper())
        # Solo una delle descrizioni ha un indirizzo dentro: si passa il valore soltanto a quella,
        # perché `_()` con argomenti chiama `format()` e una graffa in un'altra frase diventerebbe
        # un errore in faccia all'utente a metà della procedura.
        self.passo_sotto.setText(_(sotto, discussioni=DISCUSSIONI_URL) if '{discussioni}' in sotto
                                 else _(sotto))
        self.passo_quanti.setText(_('{i} di {n}', i=i + 1, n=len(self.PAGINE)))
        self.indietro.setEnabled(i > 0)
        self.avanti.setVisible(i < len(self.PAGINE) - 1)
        self._adatta_altezza(i)
        # Creando, «Salva» si accende solo in fondo: un'azione nuova a metà non è un'azione, e un
        # pulsante acceso che poi ti rimanda indietro è una presa in giro. Modificando invece è sempre
        # acceso — per cambiare un numero non si passano quattro schermate (23/10).
        ultima = i == len(self.PAGINE) - 1
        self.fine.setEnabled(ultima or not self._e_nuova)
        self.fine.setDefault(ultima)

    def _adatta_altezza(self, i: int) -> None:
        """La finestra si stringe sulla pagina che mostra, invece di stare alta come la più alta.

        Un QStackedWidget chiede lo spazio della pagina **più grande**, sempre: quattro schermate di
        cui una lunga fanno quattro finestre lunghe, e tre su quattro restano mezze vuote. Marco le
        ha viste tutte: «la pagina è molto alta», «anche lo step 3», «anche la 4 è enorme».

        Al primo tentativo mi ero affidato a ``sizeHint`` e non è bastato — restavano grandi lo stesso.
        Qui l'altezza si **conta**: la pagina, il titolo, la riga sotto, la banda dei pulsanti e i
        margini. Fare il conto a mano è meno elegante e molto più prevedibile, e qui conta la seconda.
        La larghezza non si tocca: se cambiasse a ogni passo la finestra ballerebbe.
        """
        for k in range(self.pile.count()):
            pagina = self.pile.widget(k)
            pagina.setSizePolicy(QSizePolicy.Policy.Preferred,
                                 QSizePolicy.Policy.Preferred if k == i else QSizePolicy.Policy.Ignored)
            # «Ignored» riguarda il desiderio, non l'obbligo: l'altezza **minima** di una pagina
            # nascosta continua a tenere su la finestra, e la griglia dei riconoscimenti ne chiede
            # duecento pixel. Risultato: la prima schermata, tre campi in croce, restava alta come la
            # seconda — Marco il 24/10, «troppo spazio tra i campi e la barra dei pulsanti».
            pagina.setMaximumHeight(16777215 if k == i else 0)
        modulo = self._moduli[i]
        alta = modulo.sizeHint().height()      # il modulo, non la pagina: la pagina ha lo stretch
        intorno = (self.passo_titolo.sizeHint().height() + self.passo_sotto.sizeHint().height()
                   + self._banda.sizeHint().height() + 54)
        self.resize(self.width(), max(280, min(820, alta + intorno)))

    def _avanti(self) -> None:
        guaio = self._cosa_manca(self.pile.currentIndex())
        if guaio:
            QMessageBox.warning(self, _("Azione"), guaio)
            return
        self.vai(self.pile.currentIndex() + 1)

    def _cosa_manca(self, i: int) -> str:
        """Cosa impedisce di andare avanti da questa schermata. Vuoto = si passa.

        Si controlla **uscendo** da una schermata, non salvando: scoprire alla fine che mancava una
        cosa della prima pagina vuol dire rifare il giro, ed è esattamente quello che fa odiare i
        wizard.
        """
        if i == 0:
            nome = self.name.text().strip()
            if not nome:
                return _('Dai un nome a questa azione.')
            if nome in self.taken_names:
                return _("Esiste già un\'azione «{name}»: scegli un altro nome (es. aggiungendo il nome del nodo).", name=nome)
        if i == 1:
            # Una per input, non una in tutto: con una camera e un microfono spuntati bastava dire
            # cosa doveva **vedere** la camera, e il microfono restava scelto e muto. Un'azione che
            # sembra ascoltare e non ascolta (Marco, 24/10).
            from ..actions.pannello_azione import manca_negli_input  # noqa: PLC0415

            guaio = manca_negli_input(self._fonti_spuntate(self.cams), self._fonti_spuntate(self.mics),
                                      sorted(self.targets_cam.scelte()), sorted(self.targets_mic.scelte()),
                                      scegliibili=bool(self.cams.count() or self.mics.count()),
                                      nuova=not self._apriva_una_azione)
            if guaio:
                return guaio
            # Qui stava il divieto «persone e animali insieme non si può»: è durato un'ora, il tempo
            # di unificare le due strade del motore. Adesso si può, e quello che prima era implicito
            # nel tipo è una domanda in chiaro: «quando è attiva».
        if i == 2 and not self.catena.passi():
            return _('Serve il nome, e almeno un pezzo in «Cosa fa».')
        return ""

    def _accept(self) -> None:
        """Salvando da una schermata qualsiasi, si controlla **tutto**: niente azioni a metà."""
        for i in range(len(self.PAGINE)):
            guaio = self._cosa_manca(i)
            if guaio:
                self.vai(i)
                QMessageBox.warning(self, _("Azione"), guaio)
                return
        super()._accept()


class RuleDialog(QDialog):
    """Crea o modifica una regola di programmazione, senza scrivere codice."""

    def __init__(self, rule: dict | None, parent=None):
        from ..schedule import DAY_NAMES, DAYS  # noqa: PLC0415

        super().__init__(parent)
        self.setWindowTitle(_('Regola di programmazione'))
        self.result_rule: dict = {}
        rule = rule or {"nome": "", "azione": "inserisci", "ora": "22:00", "giorni": [], "dal": "", "al": "",
                        "attiva": True}
        f = QFormLayout(self)
        f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.name = QLineEdit(rule.get("nome", ""))
        self.name.setPlaceholderText(_('es. Notte, Uscita al lavoro, Vacanze'))
        self.action = QComboBox()
        self.action.addItem(_('🔒 Attiva protezione (avvisi attivi)'), "inserisci")
        self.action.addItem(_('🔓 Disattiva protezione (nessun avviso)'), "disinserisci")
        self.action.setCurrentIndex(max(0, self.action.findData(rule.get("azione", "inserisci"))))
        self.time = QTimeEdit()
        self.time.setDisplayFormat("HH:mm")
        hh, mm = (int(x) for x in (rule.get("ora") or "22:00").split(":"))
        self.time.setTime(QTime(hh, mm))
        self.every_day = QCheckBox(_("Tutti i giorni"))
        self.days = {}
        days_row = QHBoxLayout()
        for key, label in zip(DAYS, DAY_NAMES):
            cb = QCheckBox(_(label)[:3].capitalize())
            cb.setChecked(key in (rule.get("giorni") or []))
            self.days[key] = cb
            days_row.addWidget(cb)
        self.every_day.setChecked(not rule.get("giorni"))
        self.every_day.toggled.connect(lambda on: [cb.setEnabled(not on) for cb in self.days.values()])
        for cb in self.days.values():
            cb.setEnabled(not self.every_day.isChecked())
        self.use_period = QCheckBox(_('Solo tra due date'))
        self.date_from = QDateEdit()
        self.date_to = QDateEdit()
        for de in (self.date_from, self.date_to):
            de.setCalendarPopup(True)
            de.setDisplayFormat("dd/MM/yyyy")
        today = QDate.currentDate()
        self.date_from.setDate(QDate.fromString(rule["dal"], "yyyy-MM-dd") if rule.get("dal") else today)
        self.date_to.setDate(QDate.fromString(rule["al"], "yyyy-MM-dd") if rule.get("al") else today.addDays(7))
        self.use_period.setChecked(bool(rule.get("dal") or rule.get("al")))
        self.use_period.toggled.connect(lambda on: (self.date_from.setEnabled(on), self.date_to.setEnabled(on)))
        self.date_from.setEnabled(self.use_period.isChecked())
        self.date_to.setEnabled(self.use_period.isChecked())
        period_row = QHBoxLayout()
        period_row.addWidget(QLabel("dal"))
        period_row.addWidget(self.date_from)
        period_row.addWidget(QLabel("al"))
        period_row.addWidget(self.date_to)
        self.active = QCheckBox(_('Regola attiva'))
        self.active.setChecked(bool(rule.get("attiva", True)))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        f.addRow(_("Nome"), self.name)
        f.addRow(_('Cosa fare'), self.action)
        f.addRow(_('A che ora'), self.time)
        f.addRow(self.every_day)
        f.addRow(_("Giorni"), days_row)
        f.addRow(self.use_period)
        f.addRow(_("Periodo"), period_row)
        f.addRow(self.active)
        f.addRow(buttons)

    def _accept(self):
        from ..schedule import ScheduleRule  # noqa: PLC0415

        days = [] if self.every_day.isChecked() else [k for k, cb in self.days.items() if cb.isChecked()]
        if not self.every_day.isChecked() and not days:
            QMessageBox.warning(self, _("Regola"), _('Scegli almeno un giorno, oppure "Tutti i giorni".'))
            return
        d = {
            "nome": self.name.text().strip() or self.action.currentText().split(" ")[1],
            "azione": self.action.currentData(),
            "ora": self.time.time().toString("HH:mm"),
            "giorni": days,
            "dal": self.date_from.date().toString("yyyy-MM-dd") if self.use_period.isChecked() else "",
            "al": self.date_to.date().toString("yyyy-MM-dd") if self.use_period.isChecked() else "",
            "attiva": self.active.isChecked(),
        }
        try:
            ScheduleRule.from_dict(d)
        except ValueError as exc:
            QMessageBox.warning(self, _("Regola"), str(exc))
            return
        self.result_rule = d
        self.accept()

