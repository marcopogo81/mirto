# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Modalità Prank: la finestrella che la accende sul nodo, con la durata.

Marco, 25/10: *«una modalità Prank che permetta un uso più goliardico senza budget dei suoni, per chi
vorrà prenderlo per divertirsi»*. La modalità sospende una regola di sicurezza — due minuti di suono
l'ora, che il nodo impone perché nessun programma possa aggirarli — quindi questa finestra ha un
compito oltre a quello di accenderla: **dire cosa cade e cosa resta**. Un interruttore senza quelle
due righe sarebbe un interruttore che nasconde la cosa più importante che fa.

Il testo sta in una funzione sua, senza Qt, perché così si prova dove Qt non c'è — che è il motivo
per cui le prove di questo programma girano anche nel contenitore. Per la stessa ragione i colori si
prendono da ``colori`` e non da ``theme``: il secondo importa PySide6, e questo file deve potersi
leggere senza. Le **icone** invece stanno solo in ``theme``, e si chiedono dentro la funzione che
costruisce la finestra — dove Qt c'è già. Il 26/10 questa differenza è costata una prova di finestra
rossa: `colori` non ha `icon()`, e qui c'era scritto `T.icon(...)`.
"""

from __future__ import annotations

from ..durate import fra_quanto
from ..i18n import _
from . import colori as T

# Le durate offerte. Sono quattro perché una casella dove scrivere i minuti è una domanda a cui
# bisogna pensare; quattro pulsanti sono una scelta che si fa guardandoli. Il nodo ha comunque il suo
# tetto (`PRANK_MAX_MINUTI`): l'ultima è quella, non una in più.
DURATE = (10, 30, 60, 120)


def testo_prank(stato: dict | None, nome: str) -> str:
    """Il testo della finestra: com'è adesso, cosa cade, cosa resta.

    ``stato`` è il campo ``prank`` dello stato del nodo (``{"attiva", "restano_s"}``), o ``None`` se
    quel nodo non la sa fare — e in quel caso si dice **quello**, invece di offrire pulsanti che il
    nodo rifiuterebbe.
    """
    if stato is None:
        return _("<b>{nome}</b> non conosce la modalità Prank: è arrivata con la versione 0.12.1 del "
                 "nodo. Aggiornalo da «Stato e diagnosi» e torna qui.", nome=nome)
    attiva = bool((stato or {}).get("attiva"))
    resta = fra_quanto((stato or {}).get("restano_s"))
    righe = []
    if attiva:
        righe.append(_("<b style='color:{c}'>🎭 Accesa su {nome}</b>{ancora}", c=T.PAUSED, nome=nome,
                       ancora=_(" · ancora {resta}", resta=resta) if resta else ""))
    else:
        righe.append(_("<b>Spenta su {nome}</b>", nome=nome))
    righe.append(_("Finché è accesa i dissuasori <b>non hanno budget</b>: i due minuti di suono "
                   "all'ora non valgono."))
    # Le garanzie che restano, elencate: sono la ragione per cui la modalità si può concedere.
    righe.append(_("Restano: <b>30 secondi per scatto</b>, la modalità <b>si spegne da sé</b> alla "
                   "scadenza, «Ferma tutto» la spegne subito, e ogni avviso che parte dice che è "
                   "accesa."))
    # Il secondo cancello, detto qui perché è qui che si accende la modalità e si crede di aver
    # finito: a casa inserita non parte nulla, e la finestra resta aperta senza farsi sentire.
    righe.append(_("Le azioni di tipo Prank <b>tacciono a casa protetta</b>: se inserisci l'antifurto "
                   "non parte nessuno scherzo, e riprendono se disinserisci prima della scadenza."))
    righe.append(_("<span style='color:{c}'>Il conto riparte da zero quando si spegne: quello che "
                   "suona adesso non toglie niente all'ora dopo.</span>", c=T.FAINT))
    return "<br>".join(righe)


def prank_dialog(parent, nome: str, stato: dict | None, accendi, spegni) -> None:
    """La finestrella: la costruisce e la mostra."""
    costruisci_prank(parent, nome, stato, accendi, spegni).exec()


def costruisci_prank(parent, nome: str, stato: dict | None, accendi, spegni):
    """La finestra, costruita e non mostrata. ``accendi(minuti)`` e ``spegni()`` tornano il messaggio.

    Costruire e mostrare sono due funzioni perché `exec()` si ferma lì finché qualcuno non chiude: una
    prova che apre la finestra non tornerebbe più. Così le prove dei pulsanti girano davvero — sono le
    uniche che possono dire se un nome di classe o un segnale sono sbagliati.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

    from . import theme as tema   # le icone stanno qui, e «theme» importa Qt: si chiede adesso

    dlg = QDialog(parent)
    dlg.setWindowTitle(_('Modalità Prank'))
    dlg.setMinimumWidth(460)
    lay = QVBoxLayout(dlg)
    lay.setSpacing(10)
    testo = QLabel(testo_prank(stato, nome))
    testo.setWordWrap(True)
    testo.setTextFormat(Qt.TextFormat.RichText)
    lay.addWidget(testo)

    esito = QLabel("")
    esito.setWordWrap(True)
    esito.setStyleSheet(f"color:{T.INFO}")
    esito.hide()

    def dopo(messaggio: str) -> None:
        esito.setText(messaggio or "")
        esito.setVisible(bool(messaggio))

    if stato is not None:
        riga = QHBoxLayout()
        riga.setSpacing(6)
        riga.addWidget(QLabel(_('Accendila per:')))
        for minuti in DURATE:
            b = QPushButton(_('{n} min', n=minuti))
            b.clicked.connect(lambda _c=False, m=minuti: dopo(accendi(m)))
            riga.addWidget(b)
        riga.addStretch(1)
        lay.addLayout(riga)
        if (stato or {}).get("attiva"):
            off = QPushButton(_('  Spegni adesso'))
            off.setIcon(tema.icon("octagon-x", T.TEXT, 16))
            off.clicked.connect(lambda: dopo(spegni()))
            lay.addWidget(off)
    lay.addWidget(esito)

    chiudi = QPushButton(_('Chiudi'))
    chiudi.clicked.connect(dlg.accept)
    fondo = QHBoxLayout()
    fondo.addStretch(1)
    fondo.addWidget(chiudi)
    lay.addLayout(fondo)
    dlg.esito = esito          # alle prove serve leggere cosa ha risposto il nodo
    return dlg
