# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Testi delle finestre che non hanno bisogno di Qt, così si possono provare senza interfaccia.

Un messaggio che l'utente legge in un momento di dubbio — «ho cercato i nodi, cosa mi sta dicendo?» —
vale quanto il codice che lo precede: qui sta separato, e i test lo leggono riga per riga.
"""

from __future__ import annotations

import time

from . import colori
from ..i18n import N_, _

COMANDO_ABBINA = "mirto-nodo abbina"

# Azioni che una prova fa sentire a tutta la casa: queste si chiedono prima.
PROVE_RUMOROSE = ("siren", "deterrent")


def prova_si_sente(kind: str) -> bool:
    """La prova di questa azione si sente? Allora si chiede conferma prima di farla.

    Una luce si prova e basta: chiedere a ogni prova infastidisce e insegna a premere «sì» senza
    leggere. Cinque secondi di sirena, invece, non si danno di nascosto a chi ha la casa intorno.
    """
    return kind in PROVE_RUMOROSE


def nodi_trovati(found: list[dict]) -> str:
    """Il riepilogo dopo «Cerca nodi in rete», quando non c'è nessun nodo nuovo da abbinare.

    Scritto per chi ha appena premuto il pulsante e non sa perché non succede niente: prima quanti
    nodi si sono visti, poi uno per uno con indirizzo e stato, e in fondo — solo se serve davvero —
    il comando da lanciare sul nodo.
    """
    righe = [_('Trovato un nodo in rete.') if len(found) == 1
             else _('Trovati {quanti} nodi in rete.', quanti=len(found)), ""]
    chiusi = False
    for n in found:
        if n.get("gia"):
            stato = _('già abbinato a questo Mirto')
        else:
            stato = _('abbinamento chiuso sul nodo')
            chiusi = True
        righe.append(f"  • {n.get('nome') or n.get('name') or '?'}")
        righe.append("        " + _('indirizzo: {url}', url=n.get('url', '?')))
        righe.append("        " + _('stato: {stato}', stato=stato))
        righe.append("")
    if chiusi:
        righe += [_('Per abbinarlo, sul nodo lancia:'), "",
                  f"        {COMANDO_ABBINA}", "",
                  _('poi ripeti la ricerca.')]
    return "\n".join(righe).rstrip()


def _da_quanto(secondi: float) -> str:
    """«2 min», «3 h», «1 g»: quanto tempo è passato, corto perché sta sopra l'immagine."""
    if secondi >= 86400:
        return _('{quanti} g', quanti=int(secondi // 86400))
    if secondi >= 3600:
        return _('{quanti} h', quanti=int(secondi // 3600))
    return _('{quanti} min', quanti=max(1, int(secondi // 60)))


def immagine_ferma(ultimo: float, adesso: float | None = None) -> str:
    """Avviso sopra l'ultimo fotogramma di una camera che non è più in linea.

    Un programma di sorveglianza che lascia l'ultima immagine nel riquadro dice una bugia a chi
    guarda: il corridoio è vuoto *adesso* o lo era dieci minuti fa? L'immagine vecchia è utile —
    dice cosa c'era — ma solo se porta scritto di quando è. Sotto il minuto l'ora basta; oltre, si
    aggiunge da quanto, perché è quello che cambia il significato di ciò che si sta guardando.
    """
    adesso = time.time() if adesso is None else adesso
    if not ultimo or ultimo <= 0:
        return _('Immagine ferma')          # senza l'ora non si inventa niente
    ora = time.strftime("%H:%M", time.localtime(ultimo))
    eta = adesso - ultimo
    if eta < 60:
        return _('Immagine ferma · {ora}', ora=ora)
    return _('Immagine ferma · {ora} · {quanto} fa', ora=ora, quanto=_da_quanto(eta))


def testo_audio_registrazione(audio: dict | None) -> str:
    """La riga sotto la spunta «Registra anche l'audio»: cosa comporta, e com'è messo questo nodo.

    Una spunta nuda qui sarebbe la cosa sbagliata. Fino al 26/10 Mirto prometteva in tre punti
    dell'interfaccia e in tutt'e due le guide che l'audio non viene mai registrato: questa non è una
    comodità in più, è un **cambio di politica**, e va letto prima di essere fatto. In Italia il
    Garante considera la registrazione audio nella videosorveglianza eccedente nella gran parte dei
    casi, con soglie molto più severe di quelle del video.

    ``audio``: il campo ``audio`` di ``GET /configura/disco`` — ``None`` (o vuoto) se il nodo è più
    vecchio della 0.12.1 e non sa farlo, e allora si dice **quello** invece di offrire una spunta che
    non cambierebbe niente.
    """
    if not audio:
        return _("Questo nodo non sa registrare l'audio: è arrivato con la versione 0.12.1 del nodo. "
                 "Aggiornalo da «Stato e diagnosi».")
    righe = [_("L'audio ha regole più severe del video: in Italia il Garante lo considera eccedente nella "
               "gran parte dei casi. Accendilo solo se sai di poterlo fare, dillo a chi vive in casa e "
               "scrivilo sul cartello.")]
    if audio.get("acceso") and audio.get("attivo"):
        righe.append(_("<b>Acceso e in funzione</b>: da qui in avanti gli spezzoni e le clip di questo "
                       "nodo hanno l'audio. Quelli di prima restano, muti."))
    elif audio.get("acceso"):
        perche = str(audio.get("perche") or "")
        righe.append(_("<b>Acceso ma fermo</b>{perche}: finché è così si registra solo il video.",
                       perche=_(" — {perche}", perche=perche) if perche else ""))
    else:
        righe.append(_("Adesso è spento: si registra solo il video. Serve il microfono del nodo acceso "
                       "(Audio del nodo) e ffmpeg installato sul nodo."))
    return "<br>".join(righe)


def _riquadro(testo: str, colore: str) -> str:
    """Il nome di una tipologia dentro un riquadro del suo colore.

    Marco, 27/10: *«dai enfasi alle tipologie elencate anche in riga con dei box e le icone»*. Il
    riquadro si fa col fondo, non col bordo: il testo ricco di Qt disegna `background-color` su uno
    span ma ignora `border` e `padding`, quindi i due spazi unificatori ai lati **sono** il margine
    interno. Il fondo è una tinta del colore sul pannello, calcolata qui: a piena saturazione una
    riga di quattro riquadri urla, e quello che deve risaltare è la parola, non la vernice.
    """
    # Anche **dentro** il riquadro gli spazi non si spezzano: con lo spazio normale fra l'icona e la
    # parola, in una riga stretta Qt mandava a capo a metà — «Halloween 🔔» e sotto «Campanello», che
    # e' un riquadro tagliato in due. Visto da Marco il 27/10 nell'elenco delle azioni.
    return (f"<span style='background-color:{_tinta(colore)}; color:{colore}'>"
            f"&nbsp;<b>{testo.replace(' ', '&nbsp;')}</b>&nbsp;</span>")


def _tinta(colore: str, quota: float = 0.22, sfondo: str = colori.PANEL) -> str:
    """``quota`` del colore sopra il fondo del pannello, in esadecimale."""
    def pezzi(c):
        c = c.lstrip("#")
        return [int(c[k:k + 2], 16) for k in (0, 2, 4)]

    return "#" + "".join(f"{round(a * quota + b * (1 - quota)):02x}"
                         for a, b in zip(pezzi(colore), pezzi(sfondo)))


# Le quattro tipologie: nome, icona e colore in un posto solo. La fascia in cima, la riga in elenco e
# l'ultima schermata del wizard leggono di qui, così non possono chiamare la stessa cosa in tre modi —
# e il colore è quello che il programma usa già altrove (rosso le persone, blu le informazioni, viola
# gli animali, ambra il prank), così questa schermata non inventa un suo alfabeto.
TIPOLOGIE = {
    "antifurto": (N_("🚨 Antifurto"), colori.ALARM),
    "campanello": (N_("🔔 Campanello e avvisi"), colori.INFO),
    "dissuasore": (N_("🦅 Dissuasore"), colori.OBJECT),
    "prank": (N_("🎭 Prank"), colori.PAUSED),
}


def riquadro_tipologia(chiave: str, corto: bool = False) -> str:
    """Il nome della tipologia dentro un riquadro del suo colore.

    ``corto``: solo la prima parola, per la riga di un elenco dove accanto c'è già il nome
    dell'azione e la larghezza serve a quello.
    """
    nome, colore = TIPOLOGIE.get(chiave, TIPOLOGIE["antifurto"])
    testo = _(nome)
    if corto:
        testo = testo.split(" e ")[0]
    return _riquadro(testo, colore)


def tipologie_azioni(soglia: float) -> list[tuple[str, str]]:
    """Le quattro tipologie da mettere in griglia: ``(chiave, riga HTML)``.

    Marco, 27/10: *«in cima alla pagina c'è la descrizione ed i pulsanti, non stanno bene… rimane
    spazio inutilizzato ed è molto brutta»*. Erano quattro righe lunghe in una colonna stretta, con i
    pulsanti appesi in alto a destra e il vuoto sotto di loro: qui escono separate, così la finestra
    può metterle in due colonne e usare la larghezza che avanzava.
    """
    return [
        ("antifurto",
         _('{box} scatta <b>solo a protezione attiva</b>, per le persone riconosciute con certezza '
           '(soglia {soglia:.0%}), mai per gli animali. Nessun limite d\'uso: se c\'è qualcuno in '
           'casa, deve suonare.', box=riquadro_tipologia("antifurto"), soglia=soglia)),
        ("campanello",
         _('{box} un suono, una luce, un avviso quando arriva qualcuno: scatta <b>anche a casa '
           'aperta</b>. In cambio ha i limiti che scegli tu — pausa, tetto giornaliero, fascia '
           'oraria.', box=riquadro_tipologia("campanello"))),
        ("dissuasore",
         _('{box} uccelli e animali, anche a casa aperta, e <b>mai per le persone</b>. I suoni '
           'cambiano a ogni scatto, perché gli animali si abituano; il nodo ne impone i limiti.',
           box=riquadro_tipologia("dissuasore"))),
        ("prank",
         _('{box} nessun limite d\'uso, ed è l\'<b>unico</b> che può suonare verso una persona — ma '
           'gli servono <b>due</b> cose insieme: la modalità Prank accesa su quel nodo e la casa non '
           'protetta.', box=riquadro_tipologia("prank"))),
    ]


def spiega_tipologia(chiave: str, soglia: float = 0.6, limiti: dict | None = None) -> str:
    """Che tipologia sarà questa azione, e cosa comporta — per l'ultima schermata del wizard.

    Marco, 27/10: *«anche nell'ultima pagina dove si sceglie di attivare o no la regola conviene dire
    all'utente quale tipologia gli verrà associata e quali sono i motivi / budget»*. È il momento
    giusto: si sta per accendere una cosa, e fino a un istante prima si sono guardati campi singoli.
    Il tipo non lo ha scelto lui — lo portano quello che l'azione fa e quello che riconosce — quindi
    è anche l'unico punto in cui può scoprire **cosa gli è stato assegnato** prima di scoprirlo dal
    comportamento.

    ``limiti``: pausa (s), tetto giornaliero e fascia oraria **di questa azione**, quando li ha. I
    numeri veri, non quelli di serie: un budget raccontato in generale è esattamente il problema da
    cui tutta questa storia è partita.
    """
    quattro = dict(tipologie_azioni(soglia))
    riga = quattro.get(chiave, "")
    pezzi = [_('Questa azione sarà: {riga}', riga=riga)]
    l = dict(limiti or {})
    if chiave == "campanello" and l:
        quanto = []
        pausa = float(l.get("cooldown") or 0)
        if pausa >= 120:
            quanto.append(_('una volta ogni {min} minuti', min=int(pausa // 60)))
        elif pausa >= 60:
            quanto.append(_('una volta al minuto'))
        elif pausa > 0:
            quanto.append(_('una volta ogni {sec} secondi', sec=int(pausa)))
        if l.get("max_per_day"):
            quanto.append(_('al massimo {max} volte al giorno', max=int(l["max_per_day"])))
        if l.get("hours"):
            quanto.append(_('solo nella fascia {hours}', hours=l["hours"]))
        if quanto:
            pezzi.append(_('I <b>tuoi</b> limiti, quelli che hai appena impostato: {quali}.',
                           quali=" · ".join(quanto)))
    if chiave in ("dissuasore", "prank"):
        pezzi.append(_('<b>Il nodo</b> le impone comunque 30 secondi per scatto, e li impone lui, non '
                       'Mirto: è per rispetto dei vicini e delle regole sul rumore, ed è il motivo '
                       'per cui ci si può contare.'))
    if chiave == "dissuasore":
        pezzi.append(_('E 2 minuti di suono ogni ora. Quando finiscono, l\'avviso dice fra quanto '
                       'riprendono.'))
    if chiave == "prank":
        pezzi.append(_('Non scatterà finché non accendi la <b>modalità Prank</b> su quel nodo '
                       '(Impostazioni → Nodi → Configurazione → Modalità Prank), e tacerà ogni volta che inserisci la protezione.'))
    if chiave == "antifurto":
        pezzi.append(_('Nessun limite d\'uso, e niente fascia oraria: a casa protetta, se c\'è '
                       'qualcuno, deve suonare.'))
    return "<br>".join(pezzi)


def riassunto_limiti_azioni(soglia: float, tipi=(), prank_accesi=(), armata: bool = False) -> str:
    """La coda della fascia in cima a *Azioni*: i limiti del nodo, il tipo che non si sceglie, la
    libreria, e la modalità Prank quando è accesa.

    Le quattro **tipologie** non stanno più qui: le dà ``tipologie_azioni`` perché la finestra possa
    metterle in griglia (27/10). Qui resta quello che non è di una tipologia sola.

    Una cosa che vale la pena non perdere: dei limiti si dice **chi li impone e perché**, non solo
    quanto valgono. Un numero senza il suo scopo è una regola che la prima volta che dà fastidio si
    cerca di aggirare.

    ``tipi``: i tipi di azione configurati — del budget della sirena non si parla a chi non ne ha.
    ``prank_accesi``: i nodi con la modalità Prank accesa adesso. ``armata``: la casa è inserita, e
    allora i prank tacciono — scritto accanto alla riga che li annuncia, perché «🎭 accesa adesso» con
    l'antifurto attivo lascerebbe aspettare uno scherzo che non arriverà.
    """
    tipi = set(tipi or ())      # i tipi dei dispositivi **e** il «prank» di chi è di quel tipo
    righe = []
    nodo = []
    if "siren" in tipi:
        nodo.append(_('la sirena 3 minuti per volta e 3 ogni mezz\'ora'))
    if "deterrent" in tipi or "prank" in tipi:
        nodo.append(_('un dissuasore 30 secondi per volta e 2 minuti ogni ora'))
    if nodo:
        righe.append(_('<b>Limiti del nodo</b>, che nessun programma aggira: {quali}. Li impone il '
                       'nodo, non Mirto — per rispetto dei vicini e delle regole sul rumore, ed è il '
                       'motivo per cui ci si può contare. Quando finiscono, l\'avviso dice fra quanto '
                       'riprendono.', quali=" · ".join(nodo)))
    righe.append(_('<b>Il tipo non si sceglie</b>: lo portano quello che l\'azione fa e quello che le '
                   'fai riconoscere. Tu scegli <b>quando è attiva</b> e <b>cosa fa</b>. E se non vuoi '
                   'scegliere niente, <b>Libreria azioni</b> le ha già fatte, una per tipologia.'))
    if prank_accesi:
        righe.append(_('<b style="color:#E8A33D">🎭 Prank accesa adesso su {dove}</b>: finché è così, i '
                       'dissuasori di quel nodo non hanno budget.', dove=", ".join(prank_accesi))
                     + (_(' Ma la casa è protetta, quindi gli scherzi <b>tacciono</b>: riprendono se '
                          'disinserisci prima che la finestra scada.') if armata else ""))
    righe.append(_('Si salvano con «Salva»: finché non lo premi restano «da salvare».'))
    return "<br>".join(righe)
