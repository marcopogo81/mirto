# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco

import faulthandler
import os

# Le prove non devono mai contattare il server delle statistiche vero.
os.environ["MIRTO_STATS_URL"] = "off"

# Un tetto di tempo per la suite intera, con la pila di OGNI thread stampata prima di morire.
#
# Il 22/10 un lavoro della CI è rimasto appeso **due ore e dodici minuti** su Linux e Python 3.12 —
# la stessa combinazione che quattro minuti prima era passata in 3'56" — e ha mangiato più di cento
# minuti di Actions, finendo il credito del mese per tutti gli altri lavori: quelli dopo non sono
# nemmeno partiti. Il danno vero però non sono i minuti: di quel blocco **non è rimasto niente**.
# Nessun registro, nessuna prova colpevole, niente da cui ripartire.
#
# Questo non impedisce il blocco: lo rende leggibile. Passato il tetto, ``faulthandler`` stampa dove
# sta ogni thread — compresi quelli che aspettano un socket o una coda, che sono i sospetti di
# sempre — e chiude il processo con un errore. Al prossimo blocco si saprà chi è, invece di
# ricominciare da «chissà cosa c'era dentro».
#
# La suite intera gira in meno di due minuti: un quarto d'ora è tanto largo che non può dare un
# falso rosso su una macchina lenta, e tanto stretto da costare quasi niente. Si cambia con
# MIRTO_PROVE_TETTO=secondi, e 0 lo spegne (per chi deve fermarsi dentro un debugger).
_tetto = float(os.environ.get("MIRTO_PROVE_TETTO", "900"))
if _tetto > 0:
    faulthandler.dump_traceback_later(_tetto, exit=True)
