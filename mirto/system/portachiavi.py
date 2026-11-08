# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""La password del Wi-Fi di casa, chiesta al portachiavi di **questo** computer.

Chi mette un nodo in casa quella password l'ha già: sta nel portachiavi del Mac o nei profili di
Windows, perché il computer è sulla stessa rete. Riscriverla a mano in Mirto è il passaggio dove si
sbaglia un carattere e si dà la colpa al nodo.

Qui non si conserva niente: si chiede al sistema, si manda al nodo, si dimentica. La password non
finisce in nessun file di Mirto, in nessun registro, e non viene mai mostrata — nel campo resta
coperta, come se l'avesse scritta una persona.

Il consenso lo chiede il sistema operativo, non noi: su macOS il portachiavi apre la sua finestra (Touch
ID o password dell'utente) a ogni richiesta, su Windows ``netsh`` mostra la chiave solo a un
amministratore. Se l'utente dice no, qui arriva una stringa vuota e la finestra lo dice senza drammi.
"""

from __future__ import annotations

import logging
import subprocess

from ..i18n import _
from ..piattaforma import sistema

log = logging.getLogger(__name__)

TIMEOUT = 30          # il tempo che serve a una persona per rispondere alla finestra del portachiavi


def _senza_finestra() -> dict:
    """Su Windows evita il lampo della finestra nera del terminale."""
    if sistema().startswith("win"):
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


def _esegui(cmd: list[str], timeout: float = TIMEOUT) -> tuple[int, str]:
    """Esegue e restituisce (codice, testo). Non si scrive mai nel registro cosa ha risposto."""
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,  # noqa: S603
                             check=False, **_senza_finestra())
    except (OSError, subprocess.SubprocessError) as exc:
        log.info("Portachiavi: «%s» non eseguibile (%s)", cmd[0], type(exc).__name__)
        return 1, ""
    return res.returncode, res.stdout or ""


# ---------------------------------------------------------------- lettori di testo (provabili ovunque)

def reti_da_networksetup(testo: str) -> list[str]:
    """``networksetup -listpreferredwirelessnetworks en0``: una rete per riga, indentata."""
    reti = []
    for riga in (testo or "").splitlines()[1:]:      # la prima riga è un titolo
        nome = riga.strip()
        if nome and not nome.endswith(":"):
            reti.append(nome)
    return reti


def reti_da_netsh(testo: str) -> list[str]:
    """``netsh wlan show profiles``: «Profilo tutti gli utenti : CasaMia» (in ogni lingua)."""
    reti = []
    for riga in (testo or "").splitlines():
        if ":" not in riga:
            continue
        etichetta, _sep, nome = riga.partition(":")
        if "profil" in etichetta.lower() and nome.strip():
            reti.append(nome.strip())
    return reti


def chiave_da_netsh(testo: str) -> str:
    """``netsh wlan show profile name="X" key=clear``: la riga del contenuto della chiave.

    Il nome della riga cambia con la lingua di Windows («Key Content», «Contenuto chiave»), quindi si
    cerca la parola che non cambia — *key*/*chiave* — nella sezione delle impostazioni di sicurezza.
    """
    for riga in (testo or "").splitlines():
        if ":" not in riga:
            continue
        etichetta, _sep, valore = riga.partition(":")
        e = etichetta.lower()
        if ("key content" in e or "contenuto chiave" in e or "contenido de la clave" in e
                or "contenu de la clé" in e or "schlüsselinhalt" in e) and valore.strip():
            return valore.strip()
    return ""


def reti_da_nmcli(testo: str) -> list[str]:
    """``nmcli -t -f NAME,TYPE connection show``: solo le connessioni Wi-Fi."""
    reti = []
    for riga in (testo or "").splitlines():
        pezzi = riga.split(":")
        if len(pezzi) >= 2 and pezzi[-1].strip() in ("802-11-wireless", "wifi") and pezzi[0].strip():
            reti.append(pezzi[0].strip())
    return reti


# ---------------------------------------------------------------- quello che usa la finestra

def supportato() -> bool:
    return sistema() == "darwin" or sistema().startswith(("win", "linux"))


def perche_no() -> str:
    """Perché su questo sistema non si può chiedere: testo già pronto per la finestra."""
    if sistema().startswith("win"):
        return _('Windows mostra la password di una rete solo a un amministratore: apri Mirto come amministratore, oppure scrivila a mano.')
    if sistema() == "darwin":
        return _('Il portachiavi non ha dato la password: se hai annullato la richiesta riprova, altrimenti scrivila a mano.')
    return _('Su questo sistema la password del Wi-Fi va scritta a mano.')


def reti_conosciute() -> list[str]:
    """Le reti Wi-Fi di cui **questo** computer ha la password. Non le legge, le elenca."""
    if sistema() == "darwin":
        for interfaccia in ("en0", "en1"):
            codice, testo = _esegui(["networksetup", "-listpreferredwirelessnetworks", interfaccia], 10)
            if codice == 0:
                reti = reti_da_networksetup(testo)
                if reti:
                    return reti
        return []
    if sistema().startswith("win"):
        codice, testo = _esegui(["netsh", "wlan", "show", "profiles"], 10)
        return reti_da_netsh(testo) if codice == 0 else []
    if sistema().startswith("linux"):
        codice, testo = _esegui(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"], 10)
        return reti_da_nmcli(testo) if codice == 0 else []
    return []


def password_di(ssid: str) -> str:
    """La password di una rete, chiesta al sistema. "" se non c'è o se l'utente ha detto no.

    Qui il sistema operativo può aprire una finestra e aspettare una persona: su macOS è il portachiavi
    che chiede il permesso, ed è giusto che chieda — è l'unico punto in cui una persona autorizza
    Mirto a leggere quella password.
    """
    ssid = str(ssid or "").strip()
    if not ssid:
        return ""
    if sistema() == "darwin":
        codice, testo = _esegui(["security", "find-generic-password", "-D",
                                 "AirPort network password", "-a", ssid, "-w"])
    elif sistema().startswith("win"):
        codice, testo = _esegui(["netsh", "wlan", "show", "profile", f"name={ssid}", "key=clear"])
        testo = chiave_da_netsh(testo) if codice == 0 else ""
    elif sistema().startswith("linux"):
        codice, testo = _esegui(["nmcli", "-s", "-g", "802-11-wireless-security.psk",
                                 "connection", "show", ssid])
    else:
        return ""
    password = (testo or "").strip("\n").strip()
    log.info("Password del Wi-Fi «%s» chiesta al sistema: %s", ssid, "data" if password else "non data")
    return password
