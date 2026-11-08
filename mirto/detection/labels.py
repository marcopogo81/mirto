# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Le 80 categorie COCO che il modello YOLOX sa riconoscere, con nome italiano e gruppo.

In configurazione (``detection.watch``) si usano le chiavi inglesi, es. ``["person", "car", "dog"]``.
"""

from __future__ import annotations

from ..i18n import N_, _, get_language

# (chiave COCO, nome italiano) nell'ordine delle uscite del modello
COCO: list[tuple[str, str]] = [
    ("person", "Persona"), ("bicycle", "Bicicletta"), ("car", "Auto"), ("motorcycle", "Moto"),
    ("airplane", "Aereo"), ("bus", "Autobus"), ("train", "Treno"), ("truck", "Camion"), ("boat", "Barca"),
    ("traffic light", "Semaforo"), ("fire hydrant", "Idrante"), ("stop sign", "Segnale di stop"),
    ("parking meter", "Parchimetro"), ("bench", "Panchina"), ("bird", "Uccello"), ("cat", "Gatto"),
    ("dog", "Cane"), ("horse", "Cavallo"), ("sheep", "Pecora"), ("cow", "Mucca"), ("elephant", "Elefante"),
    ("bear", "Orso"), ("zebra", "Zebra"), ("giraffe", "Giraffa"), ("backpack", "Zaino"),
    ("umbrella", "Ombrello"), ("handbag", "Borsa"), ("tie", "Cravatta"), ("suitcase", "Valigia"),
    ("frisbee", "Frisbee"), ("skis", "Sci"), ("snowboard", "Snowboard"), ("sports ball", "Pallone"),
    ("kite", "Aquilone"), ("baseball bat", "Mazza da baseball"), ("baseball glove", "Guanto da baseball"),
    ("skateboard", "Skateboard"), ("surfboard", "Tavola da surf"), ("tennis racket", "Racchetta"),
    ("bottle", "Bottiglia"), ("wine glass", "Bicchiere da vino"), ("cup", "Tazza"), ("fork", "Forchetta"),
    ("knife", "Coltello"), ("spoon", "Cucchiaio"), ("bowl", "Ciotola"), ("banana", "Banana"),
    ("apple", "Mela"), ("sandwich", "Panino"), ("orange", "Arancia"), ("broccoli", "Broccoli"),
    ("carrot", "Carota"), ("hot dog", "Hot dog"), ("pizza", "Pizza"), ("donut", "Ciambella"),
    ("cake", "Torta"), ("chair", "Sedia"), ("couch", "Divano"), ("potted plant", "Pianta in vaso"),
    ("bed", "Letto"), ("dining table", "Tavolo"), ("toilet", "WC"), ("tv", "TV"), ("laptop", "Portatile"),
    ("mouse", "Mouse"), ("remote", "Telecomando"), ("keyboard", "Tastiera"), ("cell phone", "Cellulare"),
    ("microwave", "Microonde"), ("oven", "Forno"), ("toaster", "Tostapane"), ("sink", "Lavandino"),
    ("refrigerator", "Frigorifero"), ("book", "Libro"), ("clock", "Orologio"), ("vase", "Vaso"),
    ("scissors", "Forbici"), ("teddy bear", "Peluche"), ("hair drier", "Asciugacapelli"),
    ("toothbrush", "Spazzolino"),
]

KEYS = [k for k, _name in COCO]
INDEX = {k: i for i, k in enumerate(KEYS)}
ITALIAN = dict(COCO)
PERSON = "person"

# Gruppi mostrati nelle Impostazioni (il resto finisce in "Oggetti")
GROUPS: list[tuple[str, str, list[str]]] = [
    ("persone", N_("Persone"), ["person"]),
    ("animali", N_("Animali"), ["cat", "dog", "bird", "horse", "sheep", "cow", "bear"]),
    ("veicoli", N_("Veicoli"), ["car", "motorcycle", "bicycle", "truck", "bus", "boat"]),
    ("oggetti", N_("Oggetti"), ["backpack", "handbag", "suitcase", "umbrella", "knife", "scissors",
                            "cell phone", "laptop", "bottle", "sports ball"]),
]
# Categorie da interni/esterni poco utili per la sorveglianza: disponibili ma non mostrate per prime
OTHER = [k for k in KEYS if not any(k in g[2] for g in GROUPS)]

EMOJI = {"persone": "🚨", "animali": "🐾", "veicoli": "🚗", "oggetti": "📦"}

# Un'emoji per specie. La zampa andava bene per un gruppo, non per una fila di scelte: il 22/10, in
# una colonna di sette animali, «🐾 Gatto / 🐾 Cane / 🐾 Cavallo» erano sette glifi identici e scuri —
# si leggevano i nomi, uno per uno, perché l'icona non distingueva niente. Queste si distinguono a
# colpo d'occhio e hanno un colore loro.
EMOJI_SPECIE = {"cat": "🐈", "dog": "🐕", "bird": "🐦", "horse": "🐎", "sheep": "🐑", "cow": "🐄",
                "bear": "🐻", "person": "🚨", "car": "🚗", "motorcycle": "🏍", "bicycle": "🚲",
                "truck": "🚚", "bus": "🚌", "boat": "⛵"}


def emoji_di(key: str) -> str:
    """L'emoji di una cosa riconoscibile: quella della specie se c'è, altrimenti quella del gruppo.

    Per gli uccelli per specie (``bird:corvid``) vale quella degli uccelli: sono tutti uccelli, e una
    emoji diversa per corvidi e rapaci non esiste.
    """
    from ..audio.classes import is_sound, sound_emoji  # noqa: PLC0415

    if is_sound(key):
        return sound_emoji(key)
    if key.startswith("bird:"):
        return EMOJI_SPECIE["bird"]
    return EMOJI_SPECIE.get(key) or EMOJI.get(group_of(key), "📦")


def group_of(key: str) -> str:
    if key.startswith("bird:"):
        return "animali"
    for gid, _name, keys in GROUPS:
        if key in keys:
            return gid
    return "oggetti"


def display_name(key: str) -> str:
    """Nome della categoria nella lingua dell'interfaccia (in inglese è la chiave COCO stessa).

    Vale anche per i gruppi di uccelli (``bird:corvid``) e per i suoni (``sound:glass``).
    """
    if key.startswith("sound:"):
        from ..audio.classes import sound_name  # noqa: PLC0415

        return sound_name(key)
    if key.startswith("bird:"):
        from .species import group_name  # noqa: PLC0415

        return group_name(key[5:])
    if get_language() == "it":
        return ITALIAN.get(key, key)
    return key[:1].upper() + key[1:]


def group_name(gid: str) -> str:
    for g, name, _keys in GROUPS:
        if g == gid:
            return _(name)
    return gid


def headline(key: str) -> str:
    """Titolo dell'avviso: "🚨 Persona rilevata", "🐾 Rilevato: Cane"…"""
    if key == PERSON:
        return "🚨 " + _("Persona rilevata")
    if key.startswith("sound:"):
        from ..audio.classes import sound_emoji  # noqa: PLC0415

        return f"{sound_emoji(key)} " + _("Suono: {name}", name=display_name(key))
    return f"{EMOJI.get(group_of(key), '📦')} " + _("Rilevato: {name}", name=display_name(key))


def short(key: str) -> str:
    """Testo senza emoji per email, SMS, titoli: "Persona rilevata", "Rilevato: Cane"."""
    if key.startswith("sound:"):
        return _("Suono: {name}", name=display_name(key))
    return _("Persona rilevata") if key == PERSON else _("Rilevato: {name}", name=display_name(key))


def watched_indices(watch: list | None) -> list[int]:
    """Chiavi in configurazione → indici del modello (sconosciute ignorate; vuoto = solo persone)."""
    idx = sorted({INDEX[k] for k in (watch or []) if k in INDEX})
    return idx or [INDEX[PERSON]]
