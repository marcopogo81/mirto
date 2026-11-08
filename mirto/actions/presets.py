# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Azioni dei nodi per l'interfaccia: nomi in italiano, proposte prudenti, descrizioni leggibili.

Separato dalla finestra Impostazioni così si può provare senza interfaccia grafica.
"""

from __future__ import annotations
from ..i18n import N_, _

ACTION_KINDS = {"siren": N_("Sirena"), "light": N_("Luci"), "audio": N_("Audio"),
                # «Uscita generica (relè, presa, servizio HTTP)» fino al 26/10. Con la libreria per
                # scopi un campanello è un'azione «generica» — un dissuasore con «persona» fra gli
                # obiettivi non è un dissuasore — e chiamarla «relè, presa, servizio HTTP» diceva la
                # cosa sbagliata a chi aveva appena importato un campanello che suona un jingle.
                "generic": N_("Uscita o suono del nodo"),
                "deterrent": N_("Dissuasore (uccelli e animali)")}
MANUAL_KINDS = {"audio"}  # mai in automatico


def kind_from_node(tipo: str) -> str:
    """Tipo dichiarato dal nodo → tipo di Mirto; quelli che Mirto non conosce diventano uscite generiche."""
    tipo = str(tipo or "").strip().lower()
    return tipo if tipo in ACTION_KINDS else "generic"


def unique_name(name: str, taken: set) -> str:
    out, k = name, 2
    while out in taken:
        out, k = f"{name} ({k})", k + 1
    return out


def load_actions(items: list, node_ids: set) -> tuple[list[dict], list[str]]:
    """Azioni salvate pronte per il modulo: tipo e modalità in minuscolo; scarta quelle di nodi rimossi
    o non valide (restituite per avvisare l'utente)."""
    from .engine import ActionSpec  # noqa: PLC0415

    keep, dropped = [], []
    for raw in items:
        a = dict(raw)
        for k in ("kind", "mode", "on_timeout"):
            if isinstance(a.get(k), str):
                a[k] = a[k].strip().lower()
        from .engine import LEGACY_KINDS  # noqa: PLC0415

        if a.get("kind") in LEGACY_KINDS:  # tipi delle versioni precedenti: uscite generiche con conferma
            a.update(kind=LEGACY_KINDS[a["kind"]], mode="confirm")
            a.pop("close_action", None)
        try:
            ActionSpec.from_dict(a)
            ok = str(a.get("node")) in node_ids
        except Exception:  # noqa: BLE001
            ok = False
        (keep.append(a) if ok else dropped.append(str(a.get("name") or _('senza nome'))))
    return keep, dropped


def default_action(node: str, cmd: str, kind: str, label: str) -> dict:
    """Azione proposta per un comando del nodo, con impostazioni prudenti."""
    a = {"name": label, "node": node, "action": cmd, "kind": kind, "enabled": True}
    if kind == "light":
        a.update(mode="auto", duration=120)
    elif kind == "siren":
        a.update(mode="confirm", duration=60, delay=10, confirm_timeout=60, on_timeout="skip")
    elif kind == "generic":
        # un'uscita generica può comandare qualunque cosa: di serie chiede conferma su Telegram
        a.update(mode="confirm", duration=30, confirm_timeout=60, on_timeout="skip")
    elif kind == "deterrent":
        # proposta: piccioni e corvidi, suoni a rotazione, al massimo 40 volte al giorno
        a.update(mode="auto", duration=8, targets=["bird:pigeon", "bird:corvid"], sounds=[], cooldown=300,
                 max_per_day=40, notify=True)
    return a


# --- la libreria per SCOPI, non per comandi (26/10) ----------------------------------------------
#
# Marco: *«possiamo preimpostare nella libreria queste azioni e spiegarlo nella pagina azioni in
# cima»*, subito dopo aver chiesto *«come faccio a creare un'azione campanello sempre attiva se non
# posso scegliere la tipologia di azione? in base al suono che uso?»*
#
# La libreria proponeva **un'azione per ogni comando del nodo** — «Sirena – Ingresso», «Luci – Garage»
# — cioè era una libreria di *dispositivi*. Chi la apriva sapeva cosa fa il nodo, non a cosa gli serve,
# e per fare un campanello doveva capire da sé che il tipo non si sceglie, che lo porta il pezzo in
# «Cosa fa», e che la differenza fra campanello e antifurto sta nel campo «Quando è attiva». Tre cose
# da sapere per un'azione che il programma sa fare già.
#
# Qui la libreria gira di novanta gradi: le voci sono gli **scopi**, e il comando con cui si fanno lo
# scegliamo noi fra quelli che quel nodo dichiara. Chi importa sceglie «campanello», non «audio
# generato».
# Il nome è corto perché è un **nome**: lo si leggerà su Telegram e in elenco. A cosa serve lo dice
# la riga sotto, in libreria, e quella riga non è un'etichetta salvata — la scrive `describe_action`
# leggendo i campi, così dopo una modifica non può restare a dire una cosa che non è più vera.
SCOPI = (
    # (chiave, nome, comandi che vanno bene in ordine di preferenza)
    ("antifurto", N_("Antifurto"), ("sirena",)),
    ("campanello", N_("Campanello"), ("audio_generato", "dissuasore")),
    ("dissuasore", N_("Dissuasore uccelli e animali"), ("dissuasore",)),
    ("prank", N_("Prank (scherzo)"), ("audio_generato", "dissuasore")),
)


def _comando_per(scopo: str, comandi: list[dict]) -> dict | None:
    """Il primo comando del nodo che serve a quello scopo, o ``None`` se quel nodo non può farlo.

    L'ordine dentro ``SCOPI`` è una preferenza, non un vincolo: un campanello si fa meglio con l'audio
    generato (un jingle) che con lo sparo del dissuasore, ma su un nodo senza audio generato lo sparo
    è meglio di niente — e chi importa lo cambia, perché il suono si scegle nel modulo.
    """
    voluti = next((c for k, _n, c in SCOPI if k == scopo), ())
    per_nome = {str(c.get("nome")): c for c in (comandi or []) if c.get("nome")}
    return next((per_nome[n] for n in voluti if n in per_nome), None)


def proposte_per_scopo(node: str, nome_nodo: str, comandi: list[dict], taken: set) -> list[dict]:
    """Le azioni pronte di un nodo, una per scopo che quel nodo sa fare.

    ``taken``: i nomi già usati, perché due nodi non devono proporre due «Campanello» uguali.
    """
    fuori = []
    presi = set(taken)
    for chiave, etichetta, _c in SCOPI:
        c = _comando_per(chiave, comandi)
        if c is None:
            continue
        nome = unique_name(f"{_(etichetta)} – {nome_nodo}", presi)
        presi.add(nome)
        a = azione_per_scopo(chiave, node, str(c.get("nome")), nome)
        if a is not None:
            fuori.append(a)
    return fuori


def azione_per_scopo(scopo: str, node: str, cmd: str, label: str) -> dict | None:
    """L'azione pronta di uno scopo: è qui che vive la differenza fra campanello e antifurto.

    Quello che cambia non è il comando — un campanello e uno scherzo possono suonare lo stesso jingle
    — ma **quando è attiva** e **cosa la fa scattare**. Era la cosa che si doveva capire da soli.
    """
    if scopo == "antifurto":
        a = default_action(node, cmd, "siren", label)
        a.update(quando="protetta", targets=["person"])
        return a
    if scopo == "dissuasore":
        a = default_action(node, cmd, "deterrent", label)
        a.update(quando="sempre")
        return a
    if scopo == "campanello":
        # Un suono per una persona a casa aperta. Il tipo lo decide il motore e diventa «generica»:
        # un dissuasore con «persona» fra gli obiettivi non è un dissuasore, ed è giusto così.
        return {"name": label, "node": node, "action": cmd, "kind": "generic", "enabled": True,
                "quando": "sempre", "mode": "auto", "targets": ["person"], "duration": 4,
                "cooldown": 60, "max_per_day": 200, "notify": False}
    if scopo == "prank":
        # Arriva **spenta**, con «persona» spuntata: scelta di Marco il 26/10. Senza «persona» non
        # servirebbe a niente — è l'unica azione a cui è permessa — ma un'azione puntata su una
        # persona non si accende da sola a un clic di distanza: la si legge, e poi la si accende.
        # (Suonare le servono comunque due cose: la modalità Prank accesa sul nodo e la casa aperta.)
        return {"name": label, "node": node, "action": cmd, "kind": "deterrent", "enabled": False,
                "quando": "prank", "mode": "auto", "targets": ["person"], "sounds": [],
                "duration": 6, "notify": True}
    return None


QUANDO_IN_PAROLE = {
    "protetta": N_("solo a casa protetta"),
    "sempre": N_("anche a casa aperta"),
    "prank": N_("🎭 solo a modalità Prank accesa"),
}


def tipologia_di(a: dict) -> str:
    """Di che tipologia è un'azione **già fatta**: una delle quattro chiavi di ``SCOPI``.

    Non si legge da un campo salvato, si **deduce** — come il tipo. Un'etichetta scritta dentro
    l'azione resterebbe a dire «campanello» il giorno che qualcuno la trasforma in un antifurto, e la
    riga in elenco direbbe il contrario di quello che l'azione fa. Qui le due cose non possono
    scollarsi, perché l'una è calcolata dall'altra.

    Serve in tre posti che prima dicevano tre cose diverse: la fascia in cima, la riga nell'elenco, e
    l'ultima schermata del wizard — Marco, 27/10: *«nella lista non c'è evidenza della tipologia
    assegnata e quindi non si riesce a percepire come funzionerà»*.
    """
    quando = str(a.get("quando") or "").strip().lower()
    if quando == "prank":
        return "prank"
    if str(a.get("kind") or "") == "deterrent":
        return "dissuasore"
    if quando == "sempre":
        return "campanello"
    return "antifurto"


def describe_action(a: dict, con_quando: bool = True) -> str:
    """Riga leggibile: "Sirena · sirena · solo a casa protetta · chiede conferma · 60 s dopo 10 s".

    «Quando è attiva» è entrato qui il 26/10, con la libreria per scopi: due voci con lo stesso
    comando — un campanello e uno scherzo sullo stesso jingle — si distinguono **solo** per quello, e
    senza scriverlo la libreria mostrerebbe due righe identiche. Ma serve anche nell'elenco di tutti i
    giorni: fino a oggi, guardando le azioni, non si leggeva da nessuna parte quali aspettano
    l'antifurto e quali no.
    """
    kind = a.get("kind", "generic")
    parts = [a.get("name", "?"), _(ACTION_KINDS.get(kind, kind))]
    quando = str(a.get("quando") or "").strip().lower()
    # ``con_quando=False`` dove accanto c'è già il riquadro della tipologia, che quella cosa la dice
    # da sé: nell'elenco delle azioni leggere «🎭 Prank» e due parole dopo «🎭 solo a modalità Prank
    # accesa» è la stessa frase due volte su una riga che deve stare corta (27/10).
    if con_quando and quando in QUANDO_IN_PAROLE and kind not in MANUAL_KINDS:
        parts.append(_(QUANDO_IN_PAROLE[quando]))
    if kind in MANUAL_KINDS:
        parts.append(_("solo a comando (Telegram)"))
    elif kind == "deterrent":
        from ..detection.labels import display_name  # noqa: PLC0415

        parts.append(_("per: {targets}", targets=", ".join(display_name(t) for t in a.get("targets") or [])))
        # Un prank non ha pausa né tetto giornaliero: scriverglieli accanto sarebbe la riga che dice
        # il contrario di quello che fa. Il suo freno è la finestra sul nodo, e quella è già scritta
        # qui sopra («solo a modalità Prank accesa»).
        if quando != "prank":
            parts.append(_("ogni {min} min al massimo, {max} volte al giorno",
                           min=max(1, int(float(a.get("cooldown", 300)) // 60)), max=int(a.get("max_per_day", 40))))
        if a.get("hours"):
            parts.append(_("solo {hours}", hours=a["hours"]))
    else:
        parts.append(_('chiede conferma su Telegram') if a.get("mode") == "confirm" else _("automatica"))
        timing = f"{int(float(a.get('duration', 60)))} s"
        if float(a.get("delay", 0) or 0):
            timing = _("{timing} dopo {delay} s", timing=timing, delay=int(float(a['delay'])))
        parts.append(timing)
        extra = [t for t in (a.get("targets") or []) if t != "person"]
        if extra:
            from ..detection.labels import display_name  # noqa: PLC0415

            parts.append(_("anche per: {targets}", targets=", ".join(display_name(t) for t in extra)))
        # La pausa vale per **ogni** azione «sempre», non solo per i dissuasori: un campanello che non
        # risuona entro un minuto lo fa per questo, e finché non era scritto da nessuna parte sembrava
        # un difetto del campanello.
        if quando == "sempre":
            pausa = float(a.get("cooldown", 300) or 0)
            if pausa >= 120:
                parts.append(_("non più di una volta ogni {min} min", min=int(pausa // 60)))
            elif pausa >= 60:
                parts.append(_("non più di una volta al minuto"))
            elif pausa > 0:
                parts.append(_("non più di una volta ogni {sec} s", sec=int(pausa)))
    if a.get("cameras"):
        parts.append(_("solo {cameras}", cameras=", ".join(a["cameras"])))
    text = "  ·  ".join(parts)
    return text if a.get("enabled", True) else _("(spenta) {text}", text=text)


def _mdns_host(service: str) -> str:
    """"Mirto Cam mirto-ingresso #2._rtsp._tcp.local." → "mirto-ingresso"."""
    import re  # noqa: PLC0415

    name = service.split("._rtsp._tcp")[0]
    name = re.sub(r"\s*(#\d+|\(\d+\))$", "", name).strip()
    return name.split()[-1].lower() if name else ""


def match_node_cameras(nets: list[dict], nodes: list[dict]) -> tuple[dict, dict]:
    """Collega le camere di rete di Mirto ai nodi che le trasmettono.

    ``nets``: ``cameras.network`` della configurazione; ``nodes``: ``ActionEngine.node_overview()``.
    Restituisce ``(cam_to_node, node_cams)``:

    * ``cam_to_node``: {"net:<id>": node_id}
    * ``node_cams``: {node_id: [{percorso, porta, in_linea, cam_id}]} con ``cam_id`` None se la camera
      del nodo non è (ancora) in Mirto.
    """
    from urllib.parse import urlsplit  # noqa: PLC0415

    cam_to_node: dict[str, str] = {}
    node_cams: dict[str, list[dict]] = {}
    parsed = []
    for net in nets:
        try:
            u = urlsplit(net.get("url", ""))
            parsed.append((f"net:{net['id']}", (u.hostname or "").lower(), u.port or 554, u.path or "/",
                           _mdns_host(net.get("mdns", ""))))
        except (ValueError, KeyError):
            continue
    for nd in nodes:
        info = nd.get("info") or {}
        cams = info.get("camere") or []
        if not cams:
            continue
        host = str(info.get("host") or "").lower()
        hosts = {host, host.split(".")[0]} | {str(a).lower() for a in info.get("indirizzi") or []}
        try:
            hosts.add((urlsplit(nd.get("url", "")).hostname or "").lower())
        except ValueError:
            pass
        hosts.discard("")
        rows = []
        owned = {str(n.get("node_cam") or ""): f"net:{n['id']}" for n in nets
                 if str(n.get("node") or "") == str(nd["id"]) and n.get("id")}
        owned_paths = {}
        for n in nets:
            if str(n.get("node") or "") == str(nd["id"]):
                try:
                    owned_paths[(urlsplit(n.get("url", "")).path or "/").rstrip("/")] = f"net:{n['id']}"
                except ValueError:
                    pass
        for c in cams:
            cam_id = owned.get(str(c.get("id") or "")) or owned_paths.get(str(c.get("percorso", "/")).rstrip("/"))
            if cam_id:  # camera già "del nodo" (refactor camere/nodi)
                cam_to_node[cam_id] = nd["id"]
                rows.append({"percorso": c.get("percorso", "/"), "porta": c.get("porta"),
                             "in_linea": bool(c.get("in_linea")), "cam_id": cam_id})
                continue
            for cid, h, port, path, mdns_host in parsed:
                if (h in hosts or (mdns_host and mdns_host in hosts)) and port == int(c.get("porta", 554)) \
                        and path.rstrip("/") == str(c.get("percorso", "/")).rstrip("/"):
                    cam_id = cid
                    cam_to_node[cid] = nd["id"]
                    break
            rows.append({"percorso": c.get("percorso", "/"), "porta": c.get("porta"),
                         "in_linea": bool(c.get("in_linea")), "cam_id": cam_id})
        node_cams[nd["id"]] = rows
    return cam_to_node, node_cams
