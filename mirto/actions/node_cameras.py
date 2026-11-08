# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Camere dei nodi: arrivano con il nodo, ne seguono l'indirizzo e spariscono con lui.

Un nodo (API 1.1) dichiara in ``GET /info`` le sue camere::

    "camere": [{"id": "cam", "nome": "cam", "rtsp_porta": 8554, "percorso": "/cam",
                "utente": "...", "password": "..."}]

Mirto le salva in ``cameras.network`` con due campi in più: ``node`` (id del nodo) e ``node_cam`` (id della
camera sul nodo). L'indirizzo salvato è solo una riserva: a ogni apertura lo stream si cerca tra gli indirizzi
attuali del nodo (vedi ``node_camera_url``). Utente e password della camera **non** finiscono nell'indirizzo
salvato: vanno nel portachiavi alla voce ``rtsp:<id della camera>`` e si rimettono solo all'apertura del
flusso (vedi ``mirto/netutil.py``).
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlsplit

from .presets import match_node_cameras

log = logging.getLogger(__name__)


def _unique_id(base: str, nets: list[dict]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-") or "camera"
    cid, k = base, 2
    while any(n.get("id") == cid for n in nets):
        cid, k = f"{base}-{k}", k + 1
    return cid


def camera_url(host: str, cam: dict) -> str:
    """rtsp://host:porta/percorso — senza utente e password (quelle stanno nel portachiavi)."""
    path = str(cam.get("percorso") or "/")
    path = path if path.startswith("/") else "/" + path
    return f"rtsp://{host}:{int(cam.get('rtsp_porta') or cam.get('porta') or 554)}{path}"


def camera_credentials(cam: dict) -> str:
    """«utente:password» dichiarati dal nodo per questa camera ("" se non ne ha)."""
    if cam.get("utente") and cam.get("password"):
        return f"{cam['utente']}:{cam['password']}"
    return ""


def node_host(node: dict) -> str:
    try:
        return urlsplit(node.get("url", "")).hostname or ""
    except ValueError:
        return ""


def sync_node_cameras(nets: list[dict], node: dict, info: dict, secrets=None) -> tuple[list[dict], bool]:
    """Allinea ``cameras.network`` alle camere dichiarate dal nodo.

    * una camera del nodo già presente (``node``/``node_cam``) viene aggiornata (credenziali, percorso);
    * una camera aggiunta in passato come "camera di rete" dello stesso Raspberry viene **adottata** dal nodo
      (stesso id e stesso nome: nulla da rifare per l'utente);
    * una camera nuova viene creata, con il nome del nodo (o "nodo – camera" se il nodo ne ha più di una).
    Le camere che il nodo non dichiara più restano (potrebbe essere un riavvio di MediaMTX): si tolgono solo
    rimuovendo il nodo. Con ``secrets`` (SecretStore) utente e password di ogni camera finiscono nel
    portachiavi alla voce ``rtsp:<id>`` invece che nell'indirizzo salvato.
    Restituisce (nuovo elenco, cambiato?).
    """
    from ..credentials import rtsp_name  # noqa: PLC0415

    def remember(net_id: str, creds: str) -> bool:
        """Salva le credenziali della camera nel portachiavi; True se sono cambiate."""
        if secrets is None:
            return False
        name = rtsp_name(str(net_id))
        try:
            if (secrets.get(name) or "") == creds:
                return False
            if creds:
                secrets.set(name, creds)
            else:
                secrets.delete(name)
        except Exception:  # noqa: BLE001
            log.exception("Credenziali della camera %s non salvate nel portachiavi", net_id)
            return False
        return True

    nid = str(node["id"])
    cams = info.get("camere") or []
    if not cams or not isinstance(cams, list) or not isinstance(cams[0], dict):
        return nets, False  # nodo API 1.0: le sue camere restano camere di rete normali
    nets = [dict(n) for n in nets]
    changed = False
    host = node_host(node) or str((info or {}).get("host") or "")
    # camere aggiunte prima del refactor: le si riconosce come la regola di Sistema (indirizzo/porta/percorso)
    status_like = {"id": nid, "name": node.get("name", nid), "url": node.get("url", ""),
                   "info": {"host": info.get("host", ""), "indirizzi": info.get("indirizzi", []),
                            "camere": [{"porta": c.get("rtsp_porta"), "percorso": c.get("percorso"),
                                        "in_linea": True} for c in cams]}}
    _, legacy = match_node_cameras([n for n in nets if not n.get("node")], [status_like])
    legacy_by_path = {r["percorso"]: r["cam_id"] for r in legacy.get(nid, []) if r.get("cam_id")}
    for cam in cams:
        cid = str(cam.get("id") or cam.get("nome") or "cam")
        url = camera_url(host, cam)
        creds = camera_credentials(cam)
        mine = next((n for n in nets if str(n.get("node")) == nid and str(n.get("node_cam")) == cid), None)
        if mine is None and cam.get("percorso") in legacy_by_path:
            old_id = legacy_by_path[cam["percorso"]].split(":", 1)[1]
            mine = next((n for n in nets if n.get("id") == old_id), None)
            if mine is not None:
                mine.update(node=nid, node_cam=cid)
                mine.pop("mdns", None)
                changed = True
        if mine is None:
            label = node.get("name", nid) if len(cams) == 1 else f"{node.get('name', nid)} – {cam.get('nome', cid)}"
            new_id = _unique_id(label, nets)
            nets.append({"id": new_id, "name": label, "url": url, "node": nid, "node_cam": cid})
            remember(new_id, creds)
            changed = True
            continue
        if mine.get("url") != url and _same_except_host(mine.get("url", ""), url) is False:
            mine["url"] = url  # porta o percorso cambiati sul nodo
            changed = True
        if remember(mine.get("id"), creds):  # password della camera cambiata sul nodo: si riapre il flusso
            changed = True
    return nets, changed


def _same_except_host(a: str, b: str) -> bool:
    """True se i due indirizzi differiscono solo per l'host (lì decide ``node_camera_url``)."""
    try:
        ua, ub = urlsplit(a), urlsplit(b)
        return (ua.username, ua.password, ua.port, ua.path) == (ub.username, ub.password, ub.port, ub.path)
    except ValueError:
        return False


def remove_node_cameras(nets: list[dict], node_id: str) -> tuple[list[dict], list[str]]:
    """Toglie le camere del nodo; restituisce (nuovo elenco, id delle camere tolte "net:…")."""
    keep, gone = [], []
    for n in nets:
        if str(n.get("node")) == str(node_id):
            gone.append(f"net:{n['id']}")
        else:
            keep.append(n)
    return keep, gone


def node_camera_url(net: dict, node: dict, info: dict, pick=None) -> str:
    """Indirizzo attuale dello stream: tra gli indirizzi del nodo, il primo che risponde sulla porta."""
    try:
        u = urlsplit(net.get("url", ""))
    except ValueError:
        return net.get("url", "")
    port = u.port or 554
    candidates = [a for a in (info or {}).get("indirizzi") or [] if ":" not in str(a)]
    host = node_host(node)
    if pick is None:
        from ..cameras.network_discovery import pick_address  # noqa: PLC0415
        pick = pick_address
    best = pick(candidates, port, host) if candidates else None
    best = best or host or u.hostname or ""
    cred = ""
    if u.username:
        cred = u.username + (f":{u.password}" if u.password else "") + "@"
    return f"rtsp://{cred}{best}:{port}{u.path or '/'}"
