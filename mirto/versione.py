# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Quale codice sta girando davvero, e da quanto.

Nato la sera del 23 ottobre, dopo mezz'ora buttata in due. Marco stava provando una finestra e mi
diceva che c'era scritta una cosa; io leggevo nel codice che ce n'era un'altra, e tutt'e due avevamo
ragione: l'app che aveva davanti era stata **avviata a metà pomeriggio**, e conteneva metà delle
modifiche di quel pomeriggio. Sul disco il file era giusto, in memoria no, e **non c'era modo di
accorgersene guardando**.

La parte che conta non è il numero di versione — quello cambia una volta al mese — e nemmeno il
commit: leggerlo adesso dice cosa c'è *sul disco*, non cosa il programma ha caricato all'avvio, che è
proprio la differenza che ci è costata il pomeriggio. La domanda vera è:

    **il codice sul disco è più nuovo di quello che sto eseguendo?**

Si risponde con due numeri: quando il programma ha letto i suoi file (all'importazione di questo
modulo, cioè all'avvio) e qual era allora il file più recente. Più tardi basta riguardare il disco: se
è comparso qualcosa di più nuovo, il programma lo dice invece di lasciar perdere mezz'ora a qualcun
altro.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

from . import __version__
from .i18n import _

log = logging.getLogger(__name__)

RADICE = Path(__file__).resolve().parent
AVVIATO = time.time()


def _ultima_modifica(radice: Path | None = None) -> float:
    """Il file di codice più recente sotto ``mirto/``. 0 se non si può sapere (eseguibile congelato).

    Costa qualche millisecondo (un centinaio di file) e si paga una volta all'avvio.
    """
    radice = radice or RADICE
    recente = 0.0
    try:
        for cartella, _sub, nomi in os.walk(radice):
            if "__pycache__" in cartella:
                continue
            for nome in nomi:
                if nome.endswith(".py"):
                    recente = max(recente, os.path.getmtime(Path(cartella) / nome))
    except OSError:
        return 0.0
    return recente


CARICATO = _ultima_modifica()      # com'era il codice quando il programma l'ha letto


def commit() -> str:
    """Il commit da cui gira, se è una copia di lavoro con ``git``. Vuoto altrimenti."""
    try:
        res = subprocess.run(["git", "-C", str(RADICE.parent), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return ""
    return res.stdout.strip() if res.returncode == 0 else ""


def codice_piu_nuovo_sul_disco(adesso: float | None = None) -> float:
    """Di quanti secondi il codice sul disco è più nuovo di quello in esecuzione. 0 = pari.

    Un secondo di tolleranza: il momento in cui un file viene scritto e quello in cui viene letto non
    sono mai lo stesso, e non si vuole un avviso ogni volta che si salva senza riavviare... ma dopo
    un secondo, sì: è esattamente la situazione che si vuole far vedere.
    """
    disco = adesso if adesso is not None else _ultima_modifica()
    if not disco or not CARICATO:
        return 0.0
    return max(0.0, disco - CARICATO - 1.0)


def riga() -> str:
    """Una riga sola da mettere nel registro e in «Informazioni»."""
    pezzi = [f"Mirto {__version__}"]
    sha = commit()
    if sha:
        pezzi.append(_('commit {sha}', sha=sha))
    if CARICATO:
        pezzi.append(_('codice del {quando}', quando=datetime.fromtimestamp(CARICATO).strftime("%d/%m %H:%M")))
    pezzi.append(_('avviato alle {ora}', ora=datetime.fromtimestamp(AVVIATO).strftime("%H:%M")))
    return " · ".join(pezzi)


def avviso_codice_vecchio(disco: float | None = None) -> str:
    """Cosa dire quando sul disco c'è del codice più nuovo di quello in esecuzione. Vuoto se è pari."""
    disco = disco if disco is not None else _ultima_modifica()
    if not codice_piu_nuovo_sul_disco(disco):
        return ""
    return _("Stai usando una versione avviata prima dell'ultima modifica al programma: sul disco "
             "c'è codice del {disco}, questa finestra esegue quello del {caricato}. Chiudi e riapri "
             "Mirto prima di provare qualcosa: quello che vedi potrebbe essere già stato corretto.",
             disco=datetime.fromtimestamp(disco).strftime("%d/%m %H:%M"),
             caricato=datetime.fromtimestamp(CARICATO).strftime("%d/%m %H:%M"))


def scrivi_nel_registro() -> None:
    """La prima riga del registro a ogni avvio: dice con cosa abbiamo a che fare."""
    log.info("%s", riga())
