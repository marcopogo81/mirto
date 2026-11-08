# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Chi comanda un nodo: le righe di «Altri computer…», e cosa si può staccare.

Le regole stanno qui e non nella finestra perché qui si provano: la finestra gira solo dove c'è
PySide6, e questa è roba che non si vuole sbagliare — ogni riga di quell'elenco è qualcuno che può
accendere una sirena in casa d'altri.

**Il difetto da cui nasce questo file** (22/10): la chiave dell'installazione non compariva in elenco.
*Sistema* diceva «toglila in Altri computer…», la diagnosi diceva lo stesso, e arrivati lì non c'era
niente da selezionare: l'unico pulsante utile, «Stacca…», lavorava sulla riga scelta, e la sola riga
era *questo computer*. Chi ha provato si è fermato un attimo prima di staccarsi il proprio Mac — e ha
fatto bene. In più il testo in fondo suggeriva ``--revoca-tutti``, che stacca **tutto**, Mac compreso,
cioè l'esatto contrario di quello che si voleva fare.

Adesso la chiave dell'installazione è una riga come le altre, e si stacca come le altre.
"""

from __future__ import annotations

import time

from ..durate import fra_quanto
from ..i18n import _

INSTALLAZIONE = "installazione"      # il nome con cui il nodo la conosce


def _quando(epoca) -> str:
    try:
        return time.strftime("%d/%m/%Y %H:%M", time.localtime(float(epoca)))
    except (TypeError, ValueError):
        return "?"


def _quanto(secondi) -> str:
    """Quanto manca al ritiro automatico. Una funzione sola per tutto il programma (``durate.py``)."""
    return fra_quanto(secondi)


def righe(stato: dict, io: str = "") -> list[dict]:
    """L'elenco di «Altri computer…»: ``[{"nome", "testo", "installazione", "questo"}]``.

    ``io`` è il nome con cui questo Mirto si è abbinato: la sua riga si segna, perché staccarla è
    l'unica mossa da cui non si torna indietro senza un PIN nuovo.

    La chiave dell'installazione sta **in fondo**: è l'unica che non è un computer, e si legge come
    quello che è — un residuo da togliere, non un abbinamento.
    """
    stato = dict(stato or {})
    fuori = []
    for k in (stato.get("chiavi") or []):
        if not isinstance(k, dict):
            continue
        cliente = str(k.get("cliente") or k.get("nome") or "?")
        testo = _('{cliente} · abbinato il {quando}', cliente=cliente, quando=_quando(k.get("quando")))
        if k.get("indirizzo"):
            testo += _(' · da {indirizzo}', indirizzo=str(k["indirizzo"]))
        questo = bool(io) and cliente == io
        if questo:
            testo += _(' · questo computer')
        fuori.append({"nome": str(k.get("nome") or ""), "testo": testo,
                      "installazione": False, "questo": questo})
    if stato.get("chiave_installazione"):
        manca = _quanto(stato.get("ritiro_installazione_tra_s"))
        testo = _('Chiave dell\'installazione · non è un computer: chi la conosce comanda il nodo')
        testo += (_(' · si ritira da sola fra {quanto}', quanto=manca) if manca
                  else _(' · si ritira da sola'))
        fuori.append({"nome": INSTALLAZIONE, "testo": testo, "installazione": True, "questo": False})
    return fuori


def si_puo_staccare(elenco: list[dict], nome: str) -> bool:
    """La chiave dell'installazione si stacca sempre; un computer solo che resta, no.

    Il nodo deve restare comandabile da **qualcuno**: se l'ultimo Mirto si staccasse, per rientrare
    servirebbe il terminale. La chiave dell'installazione non è nessuno, quindi non conta nel numero.
    """
    riga = next((r for r in (elenco or []) if r["nome"] == nome), None)
    if riga is None:
        return False
    if riga["installazione"]:
        return True
    return sum(1 for r in elenco if not r["installazione"]) > 1


def domanda(riga: dict) -> str:
    """Cosa si chiede prima di staccare. Tre situazioni diverse, tre frasi diverse."""
    riga = dict(riga or {})
    if riga.get("installazione"):
        return _('Ritiro la chiave dell\'installazione? Da quel momento il nodo non la accetta più, '
                 'nemmeno dopo una reinstallazione — ed è il punto: chi la conosceva non comanda più '
                 'niente. Gli abbinamenti qui sopra non si toccano.')
    cliente = str(riga.get("testo") or "").split(" · ")[0]
    if riga.get("questo"):
        return _('Stai staccando <b>questo</b> computer: Mirto perderebbe il nodo e per rientrare ti '
                 'servirebbe un PIN nuovo. Procedo?')
    return _('Stacco «{cliente}»? Quel computer non comanderà più questo nodo, e per rientrare gli '
             'servirà un PIN nuovo.', cliente=cliente)


def nota(stato: dict, elenco: list[dict] | None = None) -> str:
    """La riga in fondo alla finestra: cosa c'è da sapere adesso."""
    stato = dict(stato or {})
    if stato.get("chiave_installazione"):
        return _('In elenco c\'è ancora la chiave dell\'installazione: sceglila e premi «Stacca…» per '
                 'ritirarla. Si ritira anche da sola, o sul nodo:  mirto-nodo abbina --revoca installazione')
    quanti = sum(1 for r in (elenco or []) if not r["installazione"])
    if quanti == 1:
        return _('Un solo computer abbinato: non si stacca da qui, perché il nodo resterebbe senza nessun Mirto.')
    return ""
