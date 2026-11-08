# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Nessun test esce dalla macchina: fuori da 127.0.0.1 nessuno risponde e nessun nome si risolve.

Un test che tocca la rete vera non è più un test — cambia risultato con il Wi-Fi acceso o
spento, e sulla macchina di qualcun altro trova indirizzi che qui non esistono. Costa anche
tempo: su macOS un nome ``.local`` che nessuno rivendica tiene ferma la chiamata circa cinque
secondi (attesa mDNS), e nella suite di nomi così ce n'erano tredici.
"""
from mirto.cameras import network_discovery as nd

LOCALI = ("127.", "::1", "localhost")


def locale(nome) -> bool:
    return str(nome).startswith(LOCALI)


def attiva() -> tuple:
    """Mette le finte al posto delle vere; restituisce quelle vere per ``disattiva``."""
    vere = (nd.reachable, nd.risolvi)
    vero_reachable, vera_risolvi = vere
    nd.reachable = lambda addr, port, timeout=None: locale(addr) and vero_reachable(addr, port, timeout)
    nd.risolvi = lambda host, port: vera_risolvi(host, port) if locale(host) else []
    return vere


def disattiva(vere: tuple) -> None:
    nd.reachable, nd.risolvi = vere
