# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Su quale sistema operativo stiamo girando.

È una riga sola, ma sta in una funzione, e il motivo è una prova che su Windows ha ucciso la suite intera
senza lasciare detto niente. Le prove che devono far finta di essere su un Mac sostituivano
``sys.platform``: e ``sys`` è un modulo solo per tutti, quindi quella bugia non la sentiva soltanto il pezzo
in prova, la sentiva anche ``platformdirs``, che su Windows costruisce i percorsi delle cartelle leggendo il
registro e, vedendosi dire «darwin», si è fermata in mezzo alla prova con un errore che non voleva dire
niente a nessuno.

Chiedendolo qui, una prova può dire «fai conto di essere su un Mac» **solo** al pezzo che sta provando:
sostituisce ``sistema`` dentro quel modulo e il resto del programma continua a sapere dove si trova.
"""

from __future__ import annotations

import sys


def sistema() -> str:
    """Il nome del sistema operativo come lo scrive Python: ``darwin``, ``win32``, ``linux``."""
    return sys.platform
