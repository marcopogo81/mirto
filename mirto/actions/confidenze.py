# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Quanto dev'essere sicuro il modello, **per ogni cosa che riconosce**.

Fino al 24/10 un'azione aveva una confidenza sola per tutto quello che la fa scattare. Marco:
«dato che i modelli AI si aggiornano deve essere specifica per tipologia di riconoscimento». Ha
ragione, e non è un vezzo: le tre reti che usiamo non sono nemmeno la stessa rete. Una persona la
giudica YOLOX, la specie di un uccello un modello di iNaturalist, un vetro rotto YAMNet — e 0,60 non
vuol dire la stessa cosa per tutte e tre. Quando una delle tre si aggiorna, i suoi numeri si spostano
e quelli delle altre no.

Due parole per non confonderle:

* **soglia standard**: quella che Mirto usa già oggi per quel tipo di riconoscimento, e che sta
  nelle impostazioni generali (``actions.person_threshold``, ``detection.object_threshold``,
  ``detection.species_threshold``, ``audio.thresholds`` con la soglia propria di quel suono). Non è
  inventata qui: è quella che decide fuori di qui, e lasciarla vuol dire seguirla anche domani.
* **soglia tua**: un numero scritto in quest'azione, che vale **solo** per quest'azione e solo per
  quell'obiettivo. Si salva in ``confidenze``, e quello che non c'è segue lo standard.

Il campo vecchio ``min_confidence`` resta valido per sempre: era una soglia sola per tutti gli
obiettivi dell'azione, e si legge come se fosse scritta su ognuno.
"""

from __future__ import annotations

from ..i18n import _

# Le tre reti, e il nome con cui si presentano a chi guarda.
YOLOX, SPECIE, YAMNET = "yolox", "specie", "yamnet"
SOUND_PREFIX = "sound:"
BIRD_PREFIX = "bird:"


def modello_di(obiettivo: str) -> str:
    """Quale rete giudica quest'obiettivo. Tre reti diverse, tre scale di numeri diverse."""
    chiave = str(obiettivo or "")
    if chiave.startswith(SOUND_PREFIX):
        return YAMNET
    if chiave.startswith(BIRD_PREFIX):
        return SPECIE
    return YOLOX


def modelli_di(obiettivi) -> list[str]:
    """Le reti in gioco, nell'ordine in cui si incontrano: una riga di spiegazione per ognuna."""
    fuori: list[str] = []
    for o in (obiettivi or []):
        m = modello_di(o)
        if m not in fuori:
            fuori.append(m)
    return fuori


def di_serie(chiave: str, se_manca=None):
    """Il valore di serie di una chiave puntata, letto da ``DEFAULTS``: non si inventano numeri.

    Serve a chi apre la finestra senza avere la configurazione sottomano — una prova, per esempio.
    Meglio dire il numero che il programma userebbe appena installato che dire un numero qualunque.
    """
    from ..config import DEFAULTS  # noqa: PLC0415

    dove = DEFAULTS
    for pezzo in str(chiave).split("."):
        if not isinstance(dove, dict) or pezzo not in dove:
            return se_manca
        dove = dove[pezzo]
    return dove


def soglia_standard(obiettivo: str, leggi) -> float:
    """La soglia che vale oggi per quest'obiettivo, da dove la decide chi la decide.

    ``leggi`` è il ``get`` della configurazione. Non si copiano qui i numeri: se domani cambiano in
    «Generale» o nella scheda AI, cambia anche quello che questa finestra chiama «standard».
    """
    chiave = str(obiettivo or "")
    if chiave.startswith(SOUND_PREFIX):
        from ..audio.classes import SOUNDS  # noqa: PLC0415

        tipo = chiave[len(SOUND_PREFIX):]
        sua = SOUNDS[tipo][3] if tipo in SOUNDS else 0.5
        return float((leggi("audio.thresholds") or {}).get(tipo, sua))
    if chiave.startswith(BIRD_PREFIX):
        return float(leggi("detection.species_threshold", 0.35) or 0.35)
    if chiave == "person":
        # Per le azioni la soglia delle persone è la più alta delle due: un'azione parte, non avvisa.
        return float(leggi("actions.person_threshold", 0.60) or 0.60)
    return float(leggi("detection.object_threshold", 0.50) or 0.50)


def soglia_in_uso(obiettivo: str, confidenze, leggi, vecchia: float = 0.0) -> tuple[float, bool]:
    """``(numero, è_tua)``: quanto serve adesso, e se il numero l'hai scritto tu o è lo standard.

    ``vecchia`` è il ``min_confidence`` di prima: valeva per tutti gli obiettivi, e continua a valere
    per quelli su cui non hai scritto niente. Una configurazione vecchia non cambia comportamento.
    """
    mia = float((confidenze or {}).get(str(obiettivo), 0) or 0)
    if mia > 0:
        return mia, True
    if float(vecchia or 0) > 0:
        return float(vecchia), True
    return soglia_standard(obiettivo, leggi), False


def pulisci(confidenze, obiettivi) -> dict:
    """Le soglie da salvare: solo degli obiettivi ancora spuntati, solo quelle davvero scritte.

    Uno zero vuol dire «standard», e «standard» non si scrive: scriverlo congelerebbe il numero di
    oggi dentro l'azione, che è il contrario di quello che serve quando un modello si aggiorna.
    """
    vivi = {str(o) for o in (obiettivi or [])}
    fuori = {}
    for k, v in (confidenze or {}).items():
        try:
            n = round(float(v), 2)
        except (TypeError, ValueError):
            continue
        if str(k) in vivi and 0 < n < 1:
            fuori[str(k)] = n
    return fuori


def gruppi(obiettivi, confidenze, vecchia: float = 0.0) -> list[tuple[float, list[str]]]:
    """Gli obiettivi raccolti per soglia scritta: ``[(soglia, [obiettivi])]``, in ordine di soglia.

    Qui la configurazione non serve, ed è importante che non serva: la soglia di un'azione è una
    richiesta **in più** rispetto a quella generale, non una sua copia. Chi riconosce ha già lasciato
    passare solo quello che supera lo standard; zero vuol dire «niente in più», non «niente».

    Un blocco ha **una** confidenza per tutti i suoi soggetti, e va bene così: due soglie diverse
    sono due blocchi, e un'azione scatta se la riconosce uno qualsiasi dei blocchi in testa. Al
    motore non c'è niente di nuovo da spiegare — la stessa domanda, divisa in due.
    """
    per_soglia: dict[float, list[str]] = {}
    for o in (obiettivi or []):
        mia = float((confidenze or {}).get(str(o), 0) or 0)
        quanto = mia if mia > 0 else float(vecchia or 0)
        per_soglia.setdefault(round(quanto, 4), []).append(str(o))
    return [(s, per_soglia[s]) for s in sorted(per_soglia)]


def righe_confidenza(obiettivi, confidenze, leggi, nome) -> list[dict]:
    """Una riga per ogni obiettivo spuntato: ``{chiave, testo, valore, standard, mia}``.

    ``nome`` dice come si chiama un obiettivo in italiano. ``valore`` è quello che va nel campo (0 =
    «standard»), ``standard`` il numero che si seguirebbe lasciandolo vuoto.
    """
    fuori = []
    for o in (obiettivi or []):
        chiave = str(o)
        mia = float((confidenze or {}).get(chiave, 0) or 0)
        fuori.append({"chiave": chiave, "testo": nome(chiave), "valore": round(mia, 2),
                      "standard": round(soglia_standard(chiave, leggi), 2), "mia": mia > 0,
                      "modello": modello_di(chiave)})
    return fuori


NOMI = {YOLOX: "YOLOX", SPECIE: "iNaturalist Birds", YAMNET: "YAMNet"}
DOVE = {YOLOX: "detection.model", SPECIE: "detection.species_threshold",
        YAMNET: "audio.thresholds"}


def riga_modello(modello: str, leggi, mie: int = 0, quando: str = "") -> str:
    """La riga in piccolo sotto una griglia: chi giudica, con che numero, e **da dove viene**.

    Marco il 24/10 ha chiesto anche «da dove arriva il numero»: senza quello la riga dice solo un
    nome, e chi legge non sa se quel 60% è una cosa che ha scelto lui o una che gli è stata data.
    """
    if modello == YOLOX:
        quale = str(leggi("detection.model", "yolox_nano") or "yolox_nano")
        nome = {"yolox_nano": "YOLOX Nano", "yolox_s": "YOLOX-S"}.get(quale, quale)
        standard = _('persone {p:.0%}, animali e oggetti {o:.0%}',
                     p=float(leggi("actions.person_threshold", 0.60) or 0.60),
                     o=float(leggi("detection.object_threshold", 0.50) or 0.50))
    elif modello == SPECIE:
        nome = _('iNaturalist Birds (965 specie)')
        standard = _('{s:.0%} per il gruppo', s=float(leggi("detection.species_threshold", 0.35) or 0.35))
    else:
        nome = _('YAMNet (521 tipi di suono)')
        standard = _('la soglia propria di ogni suono, da Suoni')
    pezzi = [_('Giudica <b>{nome}</b>', nome=nome), _('standard: {standard}', standard=standard)]
    if mie:
        pezzi.append(_('{n} soglia tua in quest\'azione', n=mie) if mie == 1
                     else _('{n} soglie tue in quest\'azione', n=mie))
    else:
        pezzi.append(_('nessuna soglia tua: segue lo standard anche quando il modello si aggiorna'))
    if quando:
        pezzi.append(_('modello aggiornato il {quando}', quando=quando))
    return " · ".join(pezzi)


def scelte_tendina(obiettivo: str, leggi) -> list[tuple]:
    """Le voci della tendina «AI» dentro un riquadro: ``[(etichetta, valore)]``, zero = standard.

    La prima voce dice il numero che si seguirebbe lasciandola stare — «standard (60%)» — perché è
    l'unica informazione che serve mentre si decide, e metterla altrove vuol dire farla cercare.
    """
    base = soglia_standard(obiettivo, leggi)
    fuori = [(_('standard {s:.0%}', s=base), 0.0)]
    fuori += [(f"{n}%", round(n / 100, 2)) for n in range(30, 100, 5)]
    return fuori
