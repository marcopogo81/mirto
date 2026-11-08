# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Quanto manca, detto a parole.

Esisteva già tre volte, in tre file, con tre arrotondamenti diversi: `_quanto` in
*pannello_abbinamenti* («4 ore»), `_quanto_fa` in *sistema* («5 h fa») e un terzo in *catena_ui*.
Le due forme sono davvero diverse — *quanto manca* e *quanto è passato* non si scrivono uguale, e
quella corta serve a stare in una colonna — ma la prima non ha motivo di esistere in più copie: il
messaggio del budget dei dissuasori è il secondo posto che la chiede, e un secondo posto è il momento
di avere una funzione sola invece di due che diranno «un'ora» e «60 minuti» per lo stesso istante.
"""

from __future__ import annotations

from .i18n import _


def fra_quanto(secondi: float | int | str | None) -> str:
    """«6 giorni», «4 ore», «20 minuti»: quanto manca. Vuoto se non manca niente o non si sa.

    Non dice «fra»: la parola la mette la frase che la usa, perché «riprende fra 12 minuti» e
    «scade in 12 minuti» vogliono preposizioni diverse e in altre lingue non è detto che ce ne sia
    una sola.
    """
    try:
        s = int(float(secondi))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    if s <= 0:
        return ""
    if s >= 172800:
        return _('{giorni} giorni', giorni=s // 86400)
    if s >= 86400:
        return _('un giorno')
    if s >= 7200:
        return _('{ore} ore', ore=s // 3600)
    if s >= 3600:
        return _("un'ora")
    if s < 120:
        # «1 minuti» lo scriveva la funzione di prima, e si vedeva nel pannello degli abbinamenti
        # ogni volta che a una chiave restava meno di due minuti. Mettere insieme tre copie serve
        # anche a questo: un difetto che stava in una si corregge una volta per tutte.
        return _('un minuto')
    return _('{minuti} minuti', minuti=s // 60)
