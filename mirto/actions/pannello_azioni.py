# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""La scheda delle azioni: l'elenco di tutte, e la libreria aggregata.

Fino al 24/10 le azioni vivevano dentro la pagina di **un** nodo, ultimo residuo di quando un'azione
era «una cosa che fa un nodo». Dopo il passaggio dall'azione al blocco quella parentela non esiste
più: un'azione può accendere una luce all'ingresso e far suonare il garage, e chiederle a quale dei
due appartiene non ha una risposta.

Qui dentro non c'è niente di Qt: si decide **cosa** mostrare — quali righe, con quali parole, e quali
voci della libreria sono la stessa cosa vista da due nodi — e la finestra si limita a disegnarlo.
"""

from __future__ import annotations

from ..i18n import _
from .blocchi import dispositivi_di, dispositivo_di
from .presets import describe_action, tipologia_di


def nomi_dispositivi(nodi: list[dict]) -> dict[str, str]:
    """``{"nodo:ingresso": "Ingresso"}``: come si chiamano i dispositivi per chi guarda."""
    return {"nodo:" + str(n.get("id")): str(n.get("name") or n.get("id")) for n in (nodi or [])}


def dove_si_fa(azione: dict, nomi: dict) -> str:
    """Dove agisce quest'azione, in parole: è la colonna che prima non poteva esistere."""
    dove = dispositivi_di(azione)
    if not dove:
        return ""
    return ", ".join(nomi.get(d, dispositivo_di(d)[1] or d) for d in dove)


def righe_azioni(azioni: list[dict], nodi: list[dict], salvate=()) -> list[dict]:
    """Una riga per azione: titolo, cosa fa, dove si fa, e se è accesa.

    ``salvate``: i nomi già scritti in configurazione, per distinguere quelle ancora da salvare.
    """
    nomi = nomi_dispositivi(nodi)
    fuori = []
    for i, a in enumerate(azioni or []):
        titolo, _sep, resto = describe_action(a, con_quando=False).partition("  ·  ")
        fuori.append({
            "i": i,
            "nome": str(a.get("name") or ""),
            # Di che tipologia è, dedotta: l'elenco non la diceva, e Marco il 27/10 — *«nella lista non
            # c'è evidenza della tipologia assegnata e quindi non si riesce a percepire come
            # funzionerà»*. È la cosa che decide tutto il resto della riga, e non si vedeva.
            "tipologia": tipologia_di(a),
            "titolo": titolo.replace(_("(spenta) {text}", text=""), ""),
            "cosa": resto.replace("  ·  ", " · "),
            "dove": dove_si_fa(a, nomi),
            "accesa": bool(a.get("enabled", True)),
            "da_salvare": a.get("name") not in set(salvate),
        })
    return fuori


def libreria_aggregata(per_nodo: dict) -> list[dict]:
    """Le azioni pronte di **tutti** i nodi, senza ripetere due volte la stessa cosa.

    Chiesto da Marco il 24/10: la libreria è delle azioni, non di un nodo. Due nodi che propongono la
    stessa cosa — e lo fanno quasi sempre, perché propongono quello che sanno fare — la mostrano una
    volta sola, con scritto su quanti nodi c'è; **quale** nodo lo si sceglie importandola, che è il
    momento in cui la domanda ha senso.

    ``per_nodo``: ``{id nodo: [azioni pronte]}``. Ogni voce che esce porta ``nodi`` (gli id) e
    ``per_nodo`` (l'azione già pronta per ciascuno), perché importarla su un nodo o sull'altro vuol
    dire prendere due azioni diverse con lo stesso significato.
    """
    voci: dict[str, dict] = {}
    for nid, pronte in (per_nodo or {}).items():
        for a in pronte or []:
            # La chiave è **cosa fa**, non come si chiama: il nome di una proposta porta già dentro il
            # nome del nodo («Sirena – Garage»), quindi due nodi non avrebbero mai un nome uguale.
            #
            # Dal 26/10 c'è dentro anche «quando»: con la libreria per scopi due voci possono avere lo
            # stesso comando e lo stesso tipo — un campanello e uno scherzo sullo stesso jingle — e
            # distinguersi solo per quando sono attive. Senza, la seconda si mangiava la prima, e in
            # libreria compariva una voce sola scelta dall'ordine di lettura.
            chiave = f"{a.get('action')}|{a.get('kind')}|{a.get('quando') or ''}"
            voce = voci.setdefault(chiave, {"chiave": chiave, "titolo": "", "nodi": [], "per_nodo": {}})
            voce["nodi"].append(str(nid))
            voce["per_nodo"][str(nid)] = a
            if not voce["titolo"]:
                # Il titolo senza il nome del nodo attaccato: qui l'azione non è ancora di nessuno.
                voce["titolo"] = str(a.get("name") or "").rsplit(" – ", 1)[0]
    return sorted(voci.values(), key=lambda v: v["titolo"].lower())


def quante_volte_presente(voce: dict, nomi: dict) -> str:
    """«presente su 2 nodi» oppure il nome del nodo, quando è uno solo."""
    nodi = voce.get("nodi") or []
    if len(nodi) == 1:
        return nomi.get("nodo:" + nodi[0], nodi[0])
    return _('presente su {n} nodi', n=len(nodi))
