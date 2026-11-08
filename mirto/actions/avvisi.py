# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Il simbolino accanto al nome: quando una configurazione merita un avviso, non un divieto.

Il 23 ottobre sono cadute, una dopo l'altra, tutte le regole che impedivano certe configurazioni: un
suono di dissuasione non poteva scattare per una persona, un'azione antintrusione non poteva avere
animali fra gli obiettivi, un dissuasore non poteva ascoltare voci o urla. Sono cadute per una ragione
sola e buona — l'esempio di Marco: *uno scavalca la recinzione, una voce gli dice di allontanarsi, e
se dopo dieci secondi è ancora lì parte la sirena*. Avvisare prima di urlare è la cosa più civile che
questo programma possa fare, e quelle regole la vietavano.

Tolto il divieto però non resta il nulla, se no abbiamo solo spostato il problema sulle spalle di chi
configura senza dirglielo. Resta **questo**: un simbolo piccolo accanto al nome dell'azione, e una
volta sola — quando si salva — una spiegazione in chiaro.

La regola per decidere quando compare non è un elenco di suoni «brutti», ed è stato Marco a
inquadrarla: *non ci basiamo sul cannoncino, uno potrebbe usare il suono di una frenata e sarebbe
forse peggio*. Quindi non si guarda il nome del file: si guarda la **categoria**, che i nostri suoni
dichiarano e quelli caricati dall'utente non hanno — e non la indovineremo mai da un file audio.
Sconosciuto vuol dire «va segnalato».
"""

from __future__ import annotations

from ..i18n import _

# Le categorie che i nostri suoni dichiarano. Una voce di avviso — «per favore si allontani» — è
# fatta apposta per essere rivolta a una persona: quella non fa comparire niente. Tutto il resto sì.
VOCE, ANIMALI, SIRENA, SCONOSCIUTA = "voce", "animali", "sirena", "sconosciuta"
# La fascia in cui un suono all'aperto dà fastidio anche se è nei limiti. Non è una legge — le leggi
# cambiano da comune a comune — è il buon senso che ogni regolamento condivide.
NOTTE_DA, NOTTE_A = 22, 7


# La categoria dei suoni che il nodo genera da sé. Sono nostri, quindi la sappiamo: è l'unico elenco
# scritto a mano qui dentro, e serve a non far comparire il simbolino su un dissuasore puntato su un
# gatto. Quelli che caricherà l'utente la categoria se la sceglieranno al caricamento (tappa dei
# suoni): finché non ce l'hanno, sono sconosciuti — che è la verità, non una scortesia.
CATEGORIE_NOSTRE = {"allarme_corvi": ANIMALI, "cannoncino": ANIMALI, "falco": ANIMALI,
                    "rapace": ANIMALI, "sparo": ANIMALI, "gracchio": ANIMALI, "sirena": SIRENA}


def suoni_di(azione: dict, tipi_comandi: dict | None = None) -> list[str]:
    """I suoni che quest'azione può emettere, per decidere se segnalarla.

    Un comando che il nodo dichiara sirena o dissuasore **emette un suono** anche quando nessuno ne
    ha scelto uno: in quel caso suona quello del nodo, che per noi è un suono senza nome — e senza
    nome vuol dire senza categoria, quindi da segnalare. È prudente nel verso giusto.
    """
    from .passi import passi_di  # noqa: PLC0415

    tipi = dict(tipi_comandi or {})
    fuori: list[str] = []
    for passo in passi_di(azione):
        if tipi and tipi.get(passo.comando) not in ("siren", "deterrent"):
            continue
        if not tipi and passo.comando not in ("sirena", "dissuasore"):
            continue
        fuori.extend([passo.suono] if passo.suono else [s for s in (azione.get("sounds") or [])] or [""])
    return fuori


def _e_persona(soggetti) -> bool:
    """Fra quello che fa scattare l'azione c'è una persona (vista o sentita)?

    «sound:speech», «sound:scream», «sound:crying» sono persone quanto «person»: è una voce umana che
    fa partire quel suono, e chi lo sentirà addosso è chi stava parlando.
    """
    umani = {"person", "sound:speech", "sound:scream", "sound:crying"}
    return any(str(t) in umani for t in (soggetti or []))


def categoria_di(suono: str, categorie: dict | None = None) -> str:
    """La categoria dichiarata di un suono. Quello che non è dichiarato è **sconosciuto**."""
    if not suono:
        return SCONOSCIUTA
    return str((categorie or {}).get(str(suono)) or SCONOSCIUTA)


def avvisi(soggetti, suoni, categorie: dict | None = None, fascia_notturna: bool = False) -> list[str]:
    """Cosa c'è da segnalare in questa azione. Elenco vuoto = niente simbolino.

    Non blocca niente e non cambia niente: restituisce frasi da mostrare. Chi configura decide.
    """
    fuori: list[str] = []
    suoni = [s for s in (suoni or [])]
    if suoni and _e_persona(soggetti):
        if any(categoria_di(s, categorie) != VOCE for s in suoni):
            fuori.append(_("Questa azione emette un suono verso le persone."))
    if suoni and fascia_notturna:
        fuori.append(_("Questa azione può suonare di notte."))
    return fuori


def testo_lungo() -> str:
    """La spiegazione per esteso, mostrata **una volta sola** quando si salva.

    Scritta con attenzione a non promettere niente che non possiamo dimostrare. Le parole evitate di
    proposito sono «conforme», «a norma», «certificato» e «garantisce»: dicono una cosa che nessuno
    ha verificato e che si rivolterebbe contro al primo problema. «Progettate tenendo conto dei limiti
    previsti dalla normativa italiana» dice il vero — che ci abbiamo pensato, non che qualcuno ci ha
    certificati.
    """
    return _(
        "L'uso di suoni bruschi o aggressivi verso le persone è sconsigliato. In molti paesi questi "
        "usi sono regolati — limiti di rumore, orari di quiete, obblighi di segnalazione, e regole "
        "sulla privacy quando c'è ripresa o registrazione — e le norme cambiano da paese a paese, "
        "spesso da comune a comune.\n\n"
        "Mirto non verifica la conformità di quello che configuri: la scelta dei suoni, del volume, "
        "degli orari e del luogo in cui li usi, e la responsabilità che ne deriva, restano di chi "
        "configura.\n\n"
        "Le configurazioni fornite con Mirto sono progettate tenendo conto dei limiti previsti "
        "dalla normativa italiana. Modificandole, o usandole in un altro paese, quella verifica non "
        "vale più.")


def tocca_la_notte(fascia: str) -> bool:
    """La fascia oraria dell'azione tocca la notte? Senza fascia, l'azione vale sempre — notte compresa."""
    from .engine import _parse_hours  # noqa: PLC0415

    rng = _parse_hours(fascia) if fascia else None
    if not rng:
        return True
    inizio, fine = rng                      # minuti dall'inizio del giorno
    # La fascia può scavalcare la mezzanotte («20:00-06:00»): in quel caso la notte la tocca di
    # sicuro, e non c'è niente da calcolare.
    if fine <= inizio:
        return True
    ore = range(inizio // 60, (fine + 59) // 60)
    return any(h >= NOTTE_DA or h < NOTTE_A for h in ore)
