# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Credenziali nel portachiavi del sistema (Portachiavi su macOS, Credential Manager su Windows).

Nessuna password o token viene mai scritto nel file di configurazione.
Se la libreria ``keyring`` non è disponibile (es. nei test), si usa una memoria
temporanea che non viene salvata su disco.

Ogni segreto si legge dal portachiavi **una volta sola** e poi resta in memoria
finché il programma è vivo: su macOS, finché l'app non è firmata, ogni lettura
fa comparire la richiesta di consenso del Portachiavi, e il token riletto a ogni
evento metteva una finestra davanti a un avviso di intrusione.
"""

from __future__ import annotations

import logging
import time

from . import APP_NAME

log = logging.getLogger(__name__)

# Quanto si aspetta prima di ridomandare, dopo che il portachiavi ha detto di no.
#
# Il 22/10, aprendo per la prima volta l'app impacchettata: la richiesta di consenso compariva una
# decina di volte e non smetteva piu'. Il segreto letto si ricordava, il **rifiuto** no, quindi chi
# rileggeva - il registratore, ogni due secondi - ridomandava per sempre. Dire di no a una finestra e
# vedersela ricomparire due secondi dopo, all'infinito, e' il modo piu' veloce per far chiudere un
# programma e non riaprirlo. Un no vale cinque minuti; «set» lo cancella, perche' chi ha appena
# scritto quel segreto ha evidentemente il permesso.
ATTESA_DOPO_UN_NO = 300.0

# Nomi delle credenziali usate dal programma
TELEGRAM_TOKEN = "telegram_bot_token"
EMAIL_PASSWORD = "email_password"          # per Gmail: la password per app
SMS_TWILIO_TOKEN = "sms_twilio_auth_token"
SMS_HTTP_API_KEY = "sms_http_api_key"


def node_key_name(node_id: str) -> str:
    return f"node_hmac_key:{node_id}"


def rtsp_name(net_id: str) -> str:
    """Voce con «utente:password» della camera di rete ``net_id`` (vedi ``mirto/netutil.py``)."""
    return f"rtsp:{net_id}"


class SecretStore:
    def __init__(self, service: str = APP_NAME, backend=None, clock=time.monotonic):
        self.service = service
        self._memory: dict[str, str] = {}
        self._noti: dict[str, str | None] = {}  # segreti già letti dal portachiavi
        self._rifiutati: dict[str, float] = {}  # e quelli che il portachiavi non ha voluto dare
        self.clock = clock
        self._kr = backend
        if self._kr is None:
            try:
                import keyring  # noqa: PLC0415

                self._kr = keyring
            except Exception:  # pragma: no cover - dipende dall'ambiente
                log.warning("Portachiavi non disponibile: le credenziali restano solo in memoria")
                self._kr = None

    @property
    def persistent(self) -> bool:
        return self._kr is not None

    def get(self, name: str) -> str | None:
        if self._kr is None:
            return self._memory.get(name)
        if name in self._noti:
            return self._noti[name]
        quando = self._rifiutati.get(name)
        if quando is not None and self.clock() - quando < ATTESA_DOPO_UN_NO:
            return None            # ha gia' detto di no da poco: non glielo si richiede ogni due secondi
        try:
            valore = self._kr.get_password(self.service, name)
        except Exception as exc:
            # Un errore non e' una risposta e fra cinque minuti si riprova - ma fino ad allora si tace,
            # altrimenti chi rilegge in un giro corto trasforma un «no» in una fila di finestre.
            if quando is None:
                log.error("Lettura dal portachiavi fallita per %s: %s", name, exc)
            self._rifiutati[name] = self.clock()
            return None
        self._rifiutati.pop(name, None)
        self._noti[name] = valore
        return valore

    def set(self, name: str, value: str) -> None:
        if self._kr is None:
            self._memory[name] = value
            return
        self._kr.set_password(self.service, name, value)
        self._noti[name] = value
        self._rifiutati.pop(name, None)   # chi ha appena scritto questo segreto il permesso ce l'ha

    def delete(self, name: str) -> None:
        if self._kr is None:
            self._memory.pop(name, None)
            return
        try:
            self._kr.delete_password(self.service, name)
        except Exception:
            pass
        self._noti[name] = None

    def forget(self) -> None:
        """Dimentica i segreti già letti: la prossima lettura torna al portachiavi.

        Serve quando un segreto può essere cambiato da fuori — dal Portachiavi del
        sistema, o da un altro Mirto sullo stesso computer. Dimentica anche i rifiuti: se qualcuno
        chiede apposta di rileggere, è perché qualcosa è cambiato, e vale la pena ridomandare.
        """
        self._noti.clear()
        self._rifiutati.clear()
