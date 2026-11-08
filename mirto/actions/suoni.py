# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""I suoni propri: scegliere un file, convertirlo, mandarlo al nodo.

Chiesto da Marco il 23/10, e con una divisione del lavoro decisa allora: **la conversione la fa il
computer**, che ffmpeg ce l'ha già per le registrazioni; il nodo resta un posto che sa suonare solo
WAV. Un decoder in meno su un dispositivo che vive in giardino è un decoder in meno da aggiornare
quando qualcuno ci trova dentro una falla — e i formati audio sono un posto storicamente fertile.

Qui dentro non c'è niente di interattivo e niente di asincrono: si decide se un file **si può**
caricare, si costruisce il comando per convertirlo, e si legge la risposta. Così la parte in cui si
sbaglia — le estensioni, i limiti, i messaggi — si prova senza un nodo acceso e senza aprire una
finestra.
"""

from __future__ import annotations

from pathlib import Path

from ..i18n import _

# Quello che ffmpeg sa aprire e che la gente ha davvero sul computer. Non è un elenco di gusto: è il
# patto che scriviamo nella finestra prima di far scegliere un file.
ESTENSIONI = (".wav", ".mp3", ".m4a", ".mp4", ".aac", ".ogg", ".flac")
# Il peso del **file di partenza**, prima della conversione. Il limite vero è quello del nodo (il WAV
# convertito), ma un mp3 da cento megabyte non ha senso nemmeno provare a leggerlo.
MAX_SORGENTE_BYTE = 25 * 1024 * 1024


def nome_pulito(nome: str) -> str:
    """Il nome che avrà sul nodo. Stessa regola del nodo, applicata qui per poterla **mostrare prima**.

    Chi carica «Voce Mia.m4a» deve vedere subito che si chiamerà «voce_mia», invece di scoprirlo
    dopo nell'elenco e non capire da dove salta fuori.
    """
    import re  # noqa: PLC0415

    radice = str(nome or "").strip().rsplit(".", 1)[0]
    # I separatori ai bordi si tolgono: un file chiamato «....» diventerebbe «___», che non è un nome,
    # è il rumore della sostituzione. Meglio dire «rinominalo» che ritrovarsi «___» nell'elenco.
    return re.sub(r"[^\w-]", "_", radice.lower())[:40].strip("_-")


def perche_no(percorso: str | Path, nomi_presi=(), massimo_sorgente: int = MAX_SORGENTE_BYTE) -> str:
    """Perché questo file non si può caricare. Stringa vuota = si può.

    I limiti si dicono **prima**: la finestra li scrive accanto al pulsante, e questa funzione li fa
    rispettare sullo stesso testo, così non possono raccontare due cose diverse.
    """
    p = Path(percorso)
    if not p.is_file():
        return _('Il file non c\'è più: {nome}', nome=p.name)
    if p.suffix.lower() not in ESTENSIONI:
        return _('Formato non supportato: servono {elenco}', elenco=", ".join(ESTENSIONI))
    try:
        peso = p.stat().st_size
    except OSError as exc:
        return _('Il file non si riesce a leggere: {exc}', exc=exc)
    if peso > massimo_sorgente:
        return _('Il file pesa troppo: al massimo {mb} MB', mb=massimo_sorgente // (1024 * 1024))
    nome = nome_pulito(p.name)
    if not nome:
        return _('Il nome del file non ha lettere né numeri: rinominalo e riprova.')
    if nome in set(nomi_presi):
        return _('Un suono «{nome}» c\'è già sul nodo: rinomina il file o togli quello vecchio.', nome=nome)
    return ""


def comando_conversione(ffmpeg: str, sorgente: str | Path, destinazione: str | Path,
                        rate: int = 22050, massimo_s: int = 30) -> list[str]:
    """Il comando che trasforma qualunque cosa nel WAV che il nodo sa suonare.

    Mono, sedici bit, alla frequenza che il nodo dichiara: è esattamente quello che
    ``AudioPlayer`` scrive su ALSA, quindi il nodo non deve ricampionare niente a mano mentre suona.
    ``-t`` taglia al limite **dichiarato**: il nodo rifiuta comunque quello che è più lungo, e questo
    evita di mandare in rete trenta megabyte per sentirsi dire di no.
    """
    return [str(ffmpeg), "-nostdin", "-y", "-loglevel", "error",
            "-i", str(sorgente),
            "-vn", "-ac", "1", "-ar", str(int(rate)), "-c:a", "pcm_s16le",
            "-t", str(int(massimo_s)),
            str(destinazione)]


def riassunto(stato: dict) -> str:
    """Una riga che dice i limiti del nodo, da scrivere accanto al pulsante «Aggiungi»."""
    secondi = int(stato.get("massimo_s") or 0)
    kb = int(stato.get("massimo_byte") or 0) // 1024
    if not secondi and not kb:
        return _('Questo nodo non dice i suoi limiti: è una versione vecchia.')
    return _('Al massimo {s} secondi e {kb} KB per suono · formati: {elenco}',
             s=secondi, kb=kb, elenco=", ".join(e.lstrip(".") for e in ESTENSIONI))


def in_uso(nome: str, azioni: list[dict]) -> list[str]:
    """Quali azioni userebbero questo suono: si dice **prima** di toglierlo.

    Cancellare un suono e lasciare un'azione muta è il genere di cosa che si scopre la notte in cui
    serviva. Qui si guarda sia il campo vecchio (``sounds``) sia i pezzi della catena.
    """
    fuori = []
    for a in azioni or []:
        suoi = {str(s) for s in (a.get("sounds") or [])}
        suoi |= {str((p or {}).get("suono") or "") for p in (a.get("passi") or [])}
        if nome in suoi:
            fuori.append(str(a.get("name") or ""))
    return fuori


def righe_suoni(stato: dict, in_corso: str = "") -> list[dict]:
    """L'elenco dei suoni come si legge, con **cosa fa il pulsante adesso**.

    Marco, provando A-02: *«quando esegui l'ascolto non c'è modo di fermare il play»*. Aveva ragione —
    un brano caricato può durare mezzo minuto, e restare a sentirlo fino in fondo perché il pulsante
    dice solo «Ascolta» è una piccola prigione. Il nodo sa già spegnere quello che sta suonando: qui
    si decide soltanto cosa scrivere sul pulsante, e si decide **fuori** dalla finestra, dove si prova.

    Ogni riga: ``{nome, nostro, secondi, azione}``, dove ``azione`` è ``"ferma"`` per il suono in
    corso e ``"ascolta"`` per tutti gli altri.
    """
    fuori = []
    for s in (stato or {}).get("suoni") or []:
        nome = str(s.get("nome") or "")
        if not nome:
            continue
        fuori.append({"nome": nome, "nostro": bool(s.get("nostro")),
                      "secondi": round(float(s.get("secondi") or 0), 2),
                      "azione": "ferma" if nome and nome == in_corso else "ascolta"})
    return fuori
