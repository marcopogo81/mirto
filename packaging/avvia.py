# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Lo script da cui parte l'app impacchettata (PyInstaller).

Esiste per una ragione sola, e ci è costata la prima apertura del `.dmg` (22/10): lo spec dava a
PyInstaller `mirto/__main__.py` come script di partenza, e PyInstaller uno script lo esegue **come
programma a sé**, chiamandolo `__main__`. Quel file però è il punto di ingresso di un pacchetto e usa
importazioni relative (``from . import __version__``): eseguito fuori dal pacchetto, alla prima riga
utile diceva *«attempted relative import with no known parent package»* e l'app si chiudeva prima di
mostrare qualcosa.

Qui si entra dalla porta giusta: un'importazione **assoluta**, che lascia `mirto` un pacchetto vero
e `mirto.__main__` un suo modulo. `python -m mirto` continua a funzionare esattamente come prima —
questo file lo usa soltanto chi impacchetta.
"""

from __future__ import annotations

import multiprocessing
import sys

if __name__ == "__main__":
    # Senza, su macOS e Windows ogni processo figlio riapre l'app invece del lavoro che gli tocca.
    multiprocessing.freeze_support()
    from mirto.__main__ import main

    sys.exit(main())
