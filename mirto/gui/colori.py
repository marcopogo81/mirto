# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""I colori e le misure del tema, senza Qt.

Stanno in un file loro perché così li può leggere anche chi non ha un'interfaccia: le righe della
colonna *Sistema* sono testo con dentro i colori, e si possono comporre e provare senza aprire una
finestra. ``theme`` li riprende da qui, quindi per il resto del programma non cambia niente.
"""

from __future__ import annotations

# --- colori -------------------------------------------------------------------
BG = "#1B2330"          # fondo finestra
PANEL = "#243042"       # pannelli (colonna sinistra, cronologia)
RAISED = "#2D3B50"      # pulsanti, campi, elementi in rilievo
LINE = "#33435A"        # separatori sottili, quando servono
TEXT = "#E8EDF3"
MUTED = "#9AA8BA"
FAINT = "#6B7A8F"

ARMED = "#2FB383"       # protetta
PAUSED = "#E8A33D"      # in pausa
DISARMED = "#8A97A8"    # non protetta
ALARM = "#E5484D"       # persona rilevata / errori
INFO = "#5B9BF0"        # movimento, informazioni
OBJECT = "#A78BFA"      # animali, veicoli, oggetti rilevati (non persone)

STATE_COLORS = {"active": ARMED, "paused": PAUSED, "disarmed": DISARMED, "alarm": ALARM}

# --- caratteri (quello di sistema: SF Pro su Mac) -------------------------------
SIZE_STATE = 28
SIZE_TITLE = 18
SIZE_BODY = 15
SIZE_SMALL = 13
