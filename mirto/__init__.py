# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Mirto: videosorveglianza desktop locale (Windows/macOS).

Rileva il movimento sulle camere visibili dal computer, verifica se si tratta
di una persona e avvisa i Mirto Admin (Telegram, email, SMS opzionale).
Può inoltre comandare dispositivi di deterrenza su nodi esterni via HTTP.
"""

__version__ = "0.2.0"
APP_NAME = "Mirto"

# Il nome di prima sta qui perche' lo guardano in due: ``config`` per le cartelle dei dati e
# ``credentials`` per il portachiavi. Scritto a mano in due file diventa due verita' diverse il
# giorno che una delle due si dimentica di cambiarlo.
NOME_DI_PRIMA = "MyHouse"   # il programma si e' chiamato cosi' fino al 7/11/2026

# Gli indirizzi del progetto stanno qui e solo qui. Erano scritti a mano in tre file diversi
# (i rilasci in telemetry_ui, le donazioni in app, e per chiedere qualcosa l'indirizzo di posta
# personale di Marco dentro la procedura delle azioni): il repository cambia nome prima del lancio
# pubblico, e tre indirizzi scritti a mano in tre posti diventano tre bugie diverse. Qui si cambia
# una riga. Nel programma non c'è nessun indirizzo di posta personale: chi vuole chiedere qualcosa
# apre una discussione, che resta leggibile a tutti e non finisce in una casella privata.
REPO_URL = "https://github.com/marcopogo81/mirto"
RILASCI_URL = f"{REPO_URL}/releases"
DISCUSSIONI_URL = f"{REPO_URL}/discussions"
SOSTIENI_URL = "https://github.com/sponsors/marcopogo81"
