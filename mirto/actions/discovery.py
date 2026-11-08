# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Scoperta dei nodi in rete locale via mDNS (servizio ``_mirto-node._tcp``).

Richiede la libreria ``zeroconf``. Il nodo deve annunciarsi con il proprio nome
e la porta HTTPS; un eventuale record TXT ``fp`` con l'impronta del certificato
viene mostrato all'utente durante l'abbinamento.
"""

from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)

SERVICE = "_mirto-node._tcp.local."


def nodo_da_annuncio(name: str, info) -> dict | None:
    """Il nodo descritto da un annuncio mDNS, oppure ``None`` con il motivo scritto nel registro.

    Serve un indirizzo **IPv4**: un URL costruito su un IPv6 link-local (``fe80::…``) perderebbe
    l'indice dell'interfaccia, senza il quale non è raggiungibile. Prima si tornava indietro in
    silenzio, e un nodo che non compare senza dire perché fa perdere un'ora a chi installa — tanto
    più che su Windows il nome del nodo si risolve proprio su un ``fe80::…`` (visto durante W-01, il
    19/10: `ping mirto-ingresso.local` rispondeva in IPv6 e l'IPv4 arrivava solo con `ping -4`).
    """
    indirizzi = info.parsed_addresses()
    ipv4 = [a for a in indirizzi if ":" not in a]
    corto = name.replace("." + SERVICE, "")
    if not ipv4:
        log.warning("Nodo «%s» ignorato: si annuncia senza un indirizzo IPv4 (%s)",
                    corto, ", ".join(indirizzi) or "nessun indirizzo")
        return None
    props = {k.decode(): (v.decode() if v else "") for k, v in (info.properties or {}).items()}
    host = ipv4[0]
    server = (info.server or "").rstrip(".")
    return {"name": corto, "url": f"https://{host}:{info.port}",
            "host_url": f"https://{server or host}:{info.port}",   # da salvare: segue l'IP
            "fingerprint": props.get("fp", "")}


def browse(seconds: float = 3.0) -> list[dict]:
    """Restituisce i nodi trovati: [{name, url, host_url, fingerprint}]."""
    try:
        from zeroconf import ServiceBrowser, Zeroconf  # noqa: PLC0415
    except ImportError:
        log.warning("zeroconf non installato: scoperta automatica non disponibile")
        return []

    found: dict[str, dict] = {}

    class Listener:
        def add_service(self, zc, type_, name):
            info = zc.get_service_info(type_, name, timeout=2000)
            if not info:
                return
            nodo = nodo_da_annuncio(name, info)
            if nodo is not None:
                found[name] = nodo

        def update_service(self, zc, type_, name):
            self.add_service(zc, type_, name)

        def remove_service(self, zc, type_, name):
            found.pop(name, None)

    zc = Zeroconf()
    try:
        ServiceBrowser(zc, SERVICE, Listener())
        time.sleep(seconds)
    finally:
        zc.close()
    return list(found.values())
