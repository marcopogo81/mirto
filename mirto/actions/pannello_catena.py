# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""La striscia dei passi: cosa mostrare, e cosa proporre dopo.

Qui dentro non c'è niente di PySide: ci sono le decisioni che la striscia prende, separate da come
viene disegnata. Il motivo è lo stesso di ``passi.py``: la parte in cui si sbaglia — cosa proporre
dopo una luce, cosa nascondere su un nodo senza cassa, cosa scrivere dentro un riquadro — si prova
senza aprire una finestra, e le finestre qui non si possono aprire.

Il principio della schermata, in una riga sola: **i pezzi suggeriscono altri pezzi**. Il «+» non è un
menù di tutto quello che esiste; è l'elenco corto di quello che ha senso proprio lì, su questo nodo,
con quello che a questo nodo è attaccato davvero. Ed è un elenco corto perché è pertinente, non
perché l'abbiamo tagliato: quando una cosa non si può fare, resta visibile e dice **cosa manca** e
dove si aggiusta. Dire «esiste, e ti serve una cassa» insegna cosa può fare Mirto; nasconderla non
insegna niente, e offrirla per poi fallire è peggio di tutte e due.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..i18n import _
from .pannello_audio import nome_corto_uscita
from .passi import (A_TURNO, INSIEME, MAX_PASSI, MAX_RIPETI, Passo, gruppi_di_passi,
                    modo_del_gruppo, quanto_dura, srotola)

# I tipi che un nodo dichiara per i suoi comandi, con cosa sono per chi guarda la striscia.
LUCE, SIRENA, DISSUASORE, GENERICO = "light", "siren", "deterrent", "generic"
ATTESA = "attesa"          # non è un comando del nodo: è la freccia fra due riquadri
# Sirena e dissuasore sono **la stessa cosa**: un suono. Lo ha detto Marco guardando la striscia, e
# ha ragione — sul nodo sono due comandi che mandano audio a un altoparlante, e la differenza fra loro
# non è di natura: è solo quanto a lungo possono suonare. Mostrarli come due famiglie diverse, con due
# icone e due avvisi, faceva credere che l'uno potesse fare cose che l'altro non può. Qui dentro, da
# oggi, sono tutt'e due «suono».
EMOJI = {LUCE: "💡", SIRENA: "🔊", DISSUASORE: "🔊", GENERICO: "⚡", ATTESA: "⏱"}
# Un suono che il nodo **genera** e un file che hai caricato tu non sono la stessa cosa, e avevano la
# stessa icona: «i suoni, identificati dalla stessa icona, cosa che vorrei fosse diversa se audio
# veri o generati» (Marco, 24/10). Il nodo lo sa — nel suo elenco ogni suono dice se è «nostro» —, e
# quello che sa il nodo deve arrivare fin qui.
SUONO_GENERATO, SUONO_TUO = "🔊", "🎵"
# Quanto propone il «+» quando si aggiunge un pezzo: secondi di partenza sensati, non uno per tutti.
# Una luce accesa due secondi non la vedi; una sirena di sessanta la sente il quartiere.
# Quanto dura un pezzo appena aggiunto. La sirena stava a 20 s e il primo pezzo di un'azione
# nuova si prendeva i 60 s della durata dell'azione; Marco il 25/10: *«la sirena parte
# impostata a 60 secondi, io metterei 5»*. Cinque secondi sono quello che si vuole quasi
# sempre, e allungarla è un numero da cambiare — mentre accorciarla è una prova da rifare in
# giardino con i vicini che ascoltano.
DURATA_PROPOSTA = {LUCE: 30.0, SIRENA: 5.0, DISSUASORE: 8.0, GENERICO: 5.0}
PAUSA_PROPOSTA = 5.0       # l'attesa che propone la freccia, quando la si aggiunge
MARGINE = 0.2              # il filo in più oltre le ripetizioni: il nodo non taglia la coda


@dataclass
class Proposta:
    """Una voce del «+»: cosa si può aggiungere qui, e se non si può, perché.

    ``manca`` vuoto = si può aggiungere. Pieno = la voce si vede comunque, spenta, con la frase da
    mostrare e — se c'è — la ``scorciatoia`` che porta dove si aggiusta.
    """

    chiave: str                  # "comando:luci", "attesa"
    testo: str                   # quello che legge chi guarda
    icona: str = ""
    tipo: str = GENERICO
    spiega: str = ""             # cosa fa, in una riga
    avviso: str = ""             # cosa comporta (limiti, regole di sicurezza)
    manca: str = ""              # perché non si può, se non si può
    scorciatoia: str = ""        # dove si aggiusta: "audio_del_nodo"

    @property
    def si_puo(self) -> bool:
        return not self.manca


@dataclass
class Riquadro:
    """Un passo come si vede: titolo, le righe dentro, e l'etichetta della freccia che lo segue."""

    titolo: str
    righe: list[str] = field(default_factory=list)
    icona: str = ""
    avviso: str = ""
    freccia: str = ""
    # Il pezzo dopo parte **insieme** a questo: fra i due non c'è un'attesa, c'è una graffa. Si
    # scrive sulla freccia perché è lì che si legge il rapporto fra due riquadri (tappa 3, 24/10).
    unito: bool = False


def comandi_di(comandi: list[dict], dispositivo: str = "") -> list[dict]:
    """I comandi di **quel** dispositivo. Senza dispositivo, tutti quelli che ci sono.

    Da quando un pezzo sceglie dove farsi (23/10), l'elenco che gira per l'interfaccia è l'unione dei
    comandi di tutti i dispositivi abbinati, e ogni voce porta il suo ``dispositivo``. Filtrare non è
    un vezzo: due nodi possono avere un comando con lo **stesso nome** e due tipi diversi — la sirena
    di uno è un ululato generato, quella dell'altro un dissuasore con i suoni — e senza filtro
    l'interfaccia mostrerebbe i limiti e le domande del nodo sbagliato.

    Un elenco senza la chiave ``dispositivo`` è quello di prima, di un nodo solo: si lascia passare
    tutto, così le chiamate che non sanno niente di dispositivi continuano a funzionare.
    """
    if not dispositivo:
        return list(comandi or [])
    if not any("dispositivo" in (c or {}) for c in (comandi or [])):
        return list(comandi or [])
    return [c for c in comandi if str(c.get("dispositivo") or "") == dispositivo]


def _cerca(comando: str, comandi: list[dict], dispositivo: str = "") -> dict:
    suoi = comandi_di(comandi, dispositivo)
    return next((x for x in suoi if str(x.get("nome")) == str(comando)), None) or {}


def _tipo_di(comando: str, comandi: list[dict], dispositivo: str = "") -> str:
    return str(_cerca(comando, comandi, dispositivo).get("tipo") or GENERICO)


def _suoni_di(comando: str, comandi: list[dict], dispositivo: str = "") -> list[str]:
    return [str(s) for s in (_cerca(comando, comandi, dispositivo).get("suoni") or [])]


def nome_libero(proposto: str, presi) -> str:
    """Un nome che non esiste già, partendo da quello proposto.

    La finestra propone come nome la descrizione del comando («Sirena»), e con due azioni sullo
    stesso comando proponeva due volte lo stesso nome: si scopriva solo al salvataggio, con un
    rifiuto. Proporre un nome che non si può usare è peggio che non proporne nessuno — chi configura
    lo legge, lo dà per buono, e scopre il problema quando ha già finito.
    """
    proposto = str(proposto or "").strip()
    presi = {str(x).strip() for x in (presi or ()) if str(x).strip()}
    if not proposto or proposto not in presi:
        return proposto
    for n in range(2, 100):
        tentativo = f"{proposto} {n}"
        if tentativo not in presi:
            return tentativo
    return proposto


def lunghezza_per(comando: str, suono: str, comandi: list[dict], dispositivo: str = "") -> float:
    """Quanto dura il suono di questo passo, anche quando il suono **non è scelto**.

    Trovato il 23/10 provando: il pezzo di Marco aveva «quello del nodo (a rotazione)», cioè nessun
    suono in particolare. Cercavo la durata di un suono senza nome, non la trovavo, e ripiegavo sui
    secondi — che è esattamente la domanda sbagliata, e il cannoncino sparava due volte.

    Ma per chiedere «tre volte» non serve sapere *quale* suono partirà: basta che il nodo sappia
    contare, e lui lo sa (è l'unico a sapere quanto dura il file che sceglie). Qui serve solo un
    numero per la contabilità, e si prende il **più lungo** fra i suoni di quel comando: il conto dei
    secondi resta prudente, cioè toglie dai limiti un po' più del dovuto, mai meno.
    """
    suoi = comandi_di(comandi, dispositivo)
    if suono:
        esatta = durata_di(suono, suoi)
        if esatta > 0:
            return esatta
    durate = [float(v) for v in (_cerca(comando, suoi).get("durate") or {}).values() if float(v or 0) > 0]
    return max(durate) if durate else 0.0


def durata_di(suono: str, comandi: list[dict]) -> float:
    """Quanto dura questo suono, da come lo dichiara il nodo. 0 = non lo dice (nodo vecchio)."""
    for c in comandi or []:
        durate = c.get("durate") or {}
        if suono in durate:
            try:
                return max(0.0, float(durate[suono]))
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def quante_volte(durata: float, lunghezza: float) -> int:
    """Quante volte si sente un suono lungo ``lunghezza`` in ``durata`` secondi."""
    if lunghezza <= 0:
        return 1
    return max(1, int(round(float(durata or 0) / lunghezza)))


def secondi_per(volte: int, lunghezza: float) -> float:
    """I secondi da chiedere al nodo per sentire un suono **esattamente** ``volte`` volte.

    Il nodo ripete il suono finché il tempo scade (``player.loop``): è nato per i dissuasori, dove
    scacciare un uccello per otto secondi vuol dire ripetere il verso finché se ne va. Ma chi
    configura non chiede «otto secondi di sparo»: chiede *uno* sparo, e ne sentiva tre. Quindi la
    domanda nell'interfaccia diventa «quante volte», e i secondi li calcoliamo qui.

    Questi secondi non decidono più quello che si sente: a deciderlo è «volte», che il nodo esegue
    contando le ripetizioni. Servono alla contabilità — per quanto l'azione risulta in corso, e quanto
    consuma dai limiti — e portano un margine, perché il nodo spegne il dispositivo allo scadere e
    senza quel filo taglierebbe la coda dell'ultima ripetizione.
    """
    volte = max(1, int(volte or 1))
    if lunghezza <= 0:
        return 0.0
    return round(volte * float(lunghezza) + MARGINE, 2)


def a_volte(comando: str, suono: str, comandi: list[dict], dispositivo: str = "") -> bool:
    """La domanda giusta per questo pezzo: «quante volte» (True) o «per quanto» (False)?

    Dipende da una cosa sola: il nodo sa **quanto dura** quello che sta per far sentire? Un colpo o
    un verso durano quanto durano, e l'unica domanda sensata è quante volte ripeterli; una sirena la
    genera il nodo apposta per tenerla accesa, e lì i secondi sono la domanda giusta — parole di
    Marco: *«solo sulla sirena è impostabile quanto dura»*.

    Sta qui, e non dentro la finestra, perché la finestra la decideva **all'aprirsi** e non la
    rifaceva più: aprendo un pezzo «sirena» e scegliendoci dentro uno sparo restavano i 60 secondi
    della sirena addosso a un colpo da nove decimi (23/10, sera).
    """
    return lunghezza_per(comando, suono, comandi, dispositivo) > 0


def comando_e_suono(chiave: str) -> tuple[str, str]:
    """``«suono:dissuasore:sparo»`` → ``("dissuasore", "sparo")``. Chiave sconosciuta: due stringhe vuote."""
    if not chiave.startswith("suono:"):
        return "", ""
    _c, comando, suono = chiave.split(":", 2)
    return comando.strip(), suono.strip()


def passo_da_scelte(chiave: str, volte: int, durata: float, ripeti: int, pausa: float,
                    comandi: list[dict], dispositivo: str = "", predefinito: str = "") -> Passo | None:
    """Il pezzo che esce dalle scelte della finestra, con la regola che nessuno deve dimenticare.

    **«volte» e «ripeti» non vanno mai accese insieme.** Sono due modi di dire la stessa cosa — tre
    spari — e tenerle tutt'e due vuol dire tre per tre, nove. È già successo due volte, e tutt'e due
    le volte se n'è accorto Marco con l'orecchio invece che un test con un'asserzione: un pezzo che
    conta porta ``ripeti = 1``, e la ripetizione sta in ``volte``.

    I secondi di un pezzo che conta non sono quelli scritti nella casella: si ricalcolano sul suono
    **scelto adesso**, perché restano solo contabilità (quanto l'azione risulta in corso, quanto
    consuma dai limiti) e devono combaciare con quello che il nodo eseguirà davvero.
    """
    comando, suono = comando_e_suono(chiave)
    if not comando:
        return None
    # Il dispositivo si scrive **solo se è diverso da quello solito** dell'azione. Non è pignoleria:
    # scriverlo sempre vorrebbe dire che una configurazione vecchia, riaperta e salvata senza toccare
    # niente, guadagna una riga per ogni pezzo — la promessa fatta tre volte e che Marco verifica a
    # mano con la prova 19.
    dove = "" if (not dispositivo or dispositivo == predefinito) else dispositivo
    lunghezza = lunghezza_per(comando, suono, comandi, dispositivo or predefinito)
    pausa = max(0.0, float(pausa or 0))
    if lunghezza > 0:
        volte = max(1, int(volte or 1))
        return Passo(comando=comando, suono=suono, volte=volte, dispositivo=dove,
                     durata=secondi_per(volte, lunghezza), ripeti=1, pausa=pausa)
    return Passo(comando=comando, suono=suono, volte=0, dispositivo=dove,
                 durata=max(0.0, float(durata or 0)), ripeti=max(1, int(ripeti or 1)), pausa=pausa)


def avviso_di(tipo: str) -> str:
    """Cosa comporta questo comando, in una riga. Solo **limiti**, e solo quelli veri.

    Qui, fino al 23/10, un dissuasore diceva «per animali e uccelli: non scatta mai per una persona».
    La regola era già stata tolta dal motore quella mattina, ma la frase era rimasta scritta nel
    riquadro — e una frase che racconta una regola morta è peggio della regola: Marco ha provato a
    far parlare un dissuasore a una persona, non ha sentito niente, e il riquadro gli ha confermato
    che era previsto. L'errore vero stava altrove (l'azione veniva rifiutata al caricamento), ma la
    frase gli ha dato ragione per l'intero pomeriggio.
    """
    if tipo == SIRENA:
        return _("Max 3 min per volta · 3 min ogni mezz'ora")
    if tipo == DISSUASORE:
        return _("Max 30 s per volta · 2 min ogni ora")
    return ""


def vuole_una_cassa(tipo: str) -> bool:
    """Questo comando esce da un altoparlante? Allora senza cassa non fa niente.

    Vale per sirena e dissuasore: sul nodo sono suoni, non relè. Una luce o un'uscita generica no —
    quelle vanno a un morsetto, e un nodo senza cassa le accende benissimo.
    """
    return tipo in (SIRENA, DISSUASORE)


def icona_suono(nome: str, nostri=None) -> str:
    """L'icona di un suono: l'altoparlante se lo fa il nodo, la nota se è un file tuo.

    ``nostri`` è l'elenco dei nomi che il nodo dichiara come suoi (da ``GET /suoni``). Se non lo
    sappiamo — nodo spento, o versione vecchia che non dichiara i suoi suoni — si mostra
    l'altoparlante: dire «è tuo» di un suono che non lo è sarebbe peggio che non dire niente.
    """
    if nostri is None:
        return SUONO_GENERATO
    return SUONO_GENERATO if str(nome) in set(nostri) else SUONO_TUO


def suoni_del_nodo(comandi: list[dict], nostri=None) -> list[dict]:
    """Tutti i suoni che questo nodo sa emettere, **senza dire da quale comando escono**.

    È la richiesta di Marco, detta due volte e la seconda spazientito: *«sirena e dissuasore sono la
    stessa cosa, un suono: fai sparire la differenziazione»*. Ha ragione, e il motivo per cui erano
    due non riguarda lui — sul nodo sono due dispositivi perché hanno due limiti diversi, e quei
    limiti restano. Ma a chi configura non si chiede di scegliere un dispositivo: si chiede **che
    suono vuole far sentire**, e il resto lo sappiamo noi.

    Ogni voce: ``{chiave, nome, comando, suono, icona}``. Un comando che dichiara dei suoni ne porta
    uno per suono; un comando che non ne dichiara (la sirena, che ha un verso solo) porta sé stesso —
    e quello è sempre un suono del nodo, perché un file caricato un comando suo non ce l'ha.
    """
    fuori: list[dict] = []
    for c in comandi:
        nome = str(c.get("nome") or "")
        if not nome or not vuole_una_cassa(str(c.get("tipo") or GENERICO)):
            continue
        suoni = [str(x) for x in (c.get("suoni") or []) if str(x).strip()]
        if suoni:
            fuori.extend({"chiave": f"suono:{nome}:{x}", "nome": x, "comando": nome, "suono": x,
                          "icona": icona_suono(x, nostri)}
                         for x in suoni)
        else:
            fuori.append({"chiave": f"suono:{nome}:", "nome": str(c.get("descrizione") or nome),
                          "comando": nome, "suono": "", "icona": SUONO_GENERATO})
    return fuori


def proposte(passi: list[Passo], comandi: list[dict], casse: list | None = None,
             nostri=None) -> list[Proposta]:
    """Cosa proporre **dopo** la catena che c'è: in ordine di pertinenza, non alfabetico.

    L'ordine è la metà del valore di questa schermata. Dopo una luce la cosa che si vuole quasi
    sempre è un'attesa o un suono; dopo un suono, un'altra cosa che si accende. Mettere per primo
    quello che si cerca è ciò che fa sembrare corta una lista — e questa lista è anche corta davvero,
    perché contiene solo quello che **questo** nodo dichiara di avere.
    """
    casse = list(casse or [])
    ultimo = passi[-1] if passi else None
    tipo_ultimo = _tipo_di(ultimo.comando, comandi) if ultimo else ""
    voci: list[Proposta] = []
    if ultimo:
        voci.append(Proposta(
            chiave=ATTESA, testo=_('Aspetta'), icona=EMOJI[ATTESA], tipo=ATTESA,
            spiega=_("Lascia passare qualche secondo prima del passo dopo")))
    # I suoni: uno per suono, non uno per comando. Da qui in poi «sirena» è un suono come «falco»,
    # e il dispositivo da cui esce non è una domanda da fare a nessuno.
    for v in suoni_del_nodo(comandi, nostri):
        tipo = _tipo_di(v["comando"], comandi)
        tuo = v["icona"] == SUONO_TUO
        p = Proposta(chiave=v["chiave"], testo=v["nome"], icona=v["icona"], tipo=tipo,
                     spiega=_('Un suono che hai caricato tu') if tuo else _('Suono del nodo'),
                     avviso=avviso_di(tipo))
        if not casse:
            p.manca = _('Questo nodo non ha una cassa configurata')
            p.scorciatoia = "audio_del_nodo"
        voci.append(p)
    for c in comandi:
        nome = str(c.get("nome") or "")
        tipo = str(c.get("tipo") or GENERICO)
        if not nome or vuole_una_cassa(tipo):
            continue
        voci.append(Proposta(chiave=f"comando:{nome}", testo=nome, icona=EMOJI.get(tipo, EMOJI[GENERICO]),
                             tipo=tipo, spiega=str(c.get("descrizione") or "")))
    # L'ordine: prima l'attesa se ha senso (c'è già qualcosa da far seguire), poi i comandi, con quelli
    # che suonano più su se l'ultimo passo era una luce — luce e poi suono è la sequenza che chiede
    # chiunque, dal campanello in giù.
    def peso(p: Proposta) -> tuple:
        if p.tipo == ATTESA:
            return (0,)
        suona = vuole_una_cassa(p.tipo)
        if tipo_ultimo == LUCE:
            primo = 0 if suona else 1
        elif vuole_una_cassa(tipo_ultimo):
            primo = 1 if suona else 0
        else:
            primo = 0
        return (1, 0 if p.si_puo else 1, primo, p.testo.lower())

    voci.sort(key=peso)
    return voci


def passo_proposto(chiave: str, comandi: list[dict]) -> Passo | None:
    """Il passo che nasce da una voce del «+», già con una durata sensata addosso.

    Un pezzo che arriva vuoto fa cominciare chi configura da un modulo da riempire; un pezzo che
    arriva con addosso una durata ragionevole fa cominciare da una cosa che **già funziona**, e che
    poi si aggiusta. È la differenza fra chiedere e proporre.
    """
    if chiave.startswith("suono:"):
        _c, comando, suono = chiave.split(":", 2)
        return Passo(comando=comando, suono=suono,
                     durata=DURATA_PROPOSTA.get(_tipo_di(comando, comandi), 5.0))
    if not chiave.startswith("comando:"):
        return None
    nome = chiave.split(":", 1)[1]
    tipo = _tipo_di(nome, comandi)
    suoni = _suoni_di(nome, comandi)
    return Passo(comando=nome, durata=DURATA_PROPOSTA.get(tipo, 5.0),
                 suono=suoni[0] if len(suoni) == 1 else "")


def riquadri(passi: list[Passo], comandi: list[dict], dispositivo: str = "",
             nomi: dict | None = None, in_uso: dict | None = None) -> list[Riquadro]:
    """La striscia come si legge: un riquadro per passo, con l'attesa scritta sulla freccia.

    Il «× N» del passo sta **dentro** il riquadro e quello della catena **attorno** alla striscia: due
    posti diversi perché sono due cose diverse, ed è il disegno a dirlo invece di una spiegazione.

    ``dispositivo`` è il posto solito di questa azione e ``nomi`` dice come si chiamano i dispositivi
    per chi guarda. Un pezzo che si fa **altrove** lo scrive nel riquadro: senza, si legge come tutti
    gli altri e nessuno può accorgersi che quel suono uscirà da un'altra stanza (23/10).
    """
    nomi = dict(nomi or {})
    contese = casse_contese(passi, comandi, dispositivo, in_uso)
    # Il modo del gruppo a cui appartiene ogni passo: lo dice il capogruppo, e qui serve su ogni
    # riquadro perché la freccia sta fra due pezzi e deve dire quello del gruppo di destra.
    modi = {}
    for gruppo in gruppi_di_passi(passi):
        for i in gruppo:
            modi[i] = modo_del_gruppo(passi, gruppo)
    fuori: list[Riquadro] = []
    for i, p in enumerate(passi):
        dove = p.dispositivo or dispositivo
        tipo = _tipo_di(p.comando, comandi, dove)
        suona = vuole_una_cassa(tipo)
        # Un suono si chiama col suo nome: «FALCO», non «DISSUASORE». Il dispositivo da cui esce è
        # una cosa nostra, e chi guarda la striscia non deve impararla.
        titolo = (p.suono or p.comando) if suona else p.comando
        r = Riquadro(titolo=str(titolo).upper(), icona=EMOJI.get(tipo, EMOJI[GENERICO]))
        if suona and p.volte:
            # Un suono registrato si conta in ripetizioni: i secondi sono una cosa nostra, e scriverli
            # qui farebbe credere che si possano scegliere.
            quanto = _('{n} volte', n=p.volte) if p.volte > 1 else _('una volta')
        else:
            quanto = ((_('suona {s} s', s=_numero(p.durata)) if suona
                       else _('accendi {s} s', s=_numero(p.durata))) if p.durata
                      else _('accendi quanto dura l\'azione'))
        # **Da quale cassa**, sul riquadro (Marco, 25/10: *«nel box si deve vedere anche il
        # dispositivo utilizzato: UFO 3x USB, o UFO 1x Bluetooth»*). Da quando un'azione può suonare
        # da casse diverse, un riquadro che dice solo «× 3» non dice dove si sentirà — e due riquadri
        # identici possono essere due stanze diverse. Se non sappiamo qual è la cassa configurata non
        # si scrive niente: un nome inventato è peggio di nessun nome.
        cassa = nome_corto_uscita(p.uscita or str((in_uso or {}).get(dove, "") or "")) if suona else ""
        r.righe.append(f"{quanto} · {cassa}" if cassa else quanto)
        if p.ripeti > 1:
            r.righe.append(_('× {n}', n=p.ripeti))
        if dove and dispositivo and dove != dispositivo:
            r.righe.append(_('su {dove}', dove=nomi.get(dove, dove)))
        # Il conflitto di cassa viene prima dell'avviso del tipo: quello spiega com'è fatto un
        # dissuasore, questo dice che **questo pezzo non suonerà**. Un avviso solo, quello che serve.
        r.avviso = contese.get(i) or avviso_di(tipo)
        # La freccia porta l'attesa, e l'ultima non ne ha: dopo l'ultimo passo non si aspetta niente
        # (vedi «srotola», che l'attesa finale la butta via).
        if i < len(passi) - 1:
            if passi[i + 1].nel_gruppo:
                r.unito = True
                # La freccia dice **come** parte il gruppo, e sono due cose diverse da leggere a
                # occhio: «insieme» vuol dire tutt'e due adesso, «a turno» vuol dire una per scatto.
                r.freccia = _('a turno') if modi[i + 1] == A_TURNO else _('insieme')
            else:
                r.freccia = _('{s} s', s=_numero(p.pausa)) if p.pausa else _('subito')
        fuori.append(r)
    return fuori


def _cassa_del_passo(p: Passo, comandi: list[dict], dispositivo: str, in_uso) -> tuple | None:
    """``(dispositivo, cassa)`` se questo passo suona, altrimenti ``None``.

    ``in_uso`` dice qual è l'uscita configurata di ogni dispositivo. Serve perché un passo senza
    uscita vuol dire «quella configurata», e senza saperne il nome non si può confrontare con un
    passo che la nomina. Due passi senza uscita però si confrontano benissimo: sono la stessa.
    """
    dove = p.dispositivo or dispositivo
    if not vuole_una_cassa(_tipo_di(p.comando, comandi, dove)):
        return None
    return dove, str(p.uscita or (in_uso or {}).get(dove, "") or "")


def casse_contese(passi: list[Passo], comandi: list[dict], dispositivo: str = "",
                  in_uso: dict | None = None) -> dict[int, str]:
    """Quali passi, fra quelli che partono insieme, finirebbero sulla **stessa cassa**.

    Una cassa è una linea sola: ``aplay`` ci scrive dentro, e due suoni insieme sulla stessa cassa
    non fanno due suoni. Il nodo dall'API 1.11 lo dice — rifiuta il secondo invece di risponder «ok»
    e ammazzare il primo — ma scoprirlo quando l'azione scatta è tardi: qui si dice **mentre si
    costruisce**, che è l'unico momento in cui costa niente cambiare cassa.

    Si accusa solo quando si è certi. Un passo che nomina la cassa USB e un passo senza uscita, su un
    dispositivo di cui non sappiamo com'è configurato, **non** sono un conflitto dichiarato: potrebbero
    essere la stessa cassa o due diverse, e dire «è un conflitto» quando non si sa è il modo di farsi
    ignorare la volta che conta.
    """
    fuori: dict[int, str] = {}
    for gruppo in gruppi_di_passi(passi):
        # Un gruppo «a turno» non contende niente: ne parte **una sola** per scatto, e due operazioni
        # che non suonano mai nello stesso momento non si rubano la linea. È anche il rimedio che si
        # suggerisce, quindi accusarlo sarebbe accusare la propria risposta (tappa 5).
        if modo_del_gruppo(passi, gruppo) == A_TURNO:
            continue
        prese: dict[tuple, int] = {}
        for i in gruppo:
            dove = _cassa_del_passo(passi[i], comandi, dispositivo, in_uso)
            if dove is None:
                continue
            if dove in prese:
                primo = passi[prese[dove]]
                fuori[i] = _('Stessa cassa di «{altro}»: il nodo ne suona uno solo. Scegline un\'altra, '
                             'o mettili in fila invece che insieme',
                             altro=str(primo.suono or primo.comando).upper())
            else:
                prese[dove] = i
    return fuori


def _numero(x: float) -> str:
    """Secondi come li scriverebbe una persona: «10», non «10.0»; «0,2», non «0.2»."""
    x = float(x)
    return str(int(x)) if abs(x - int(x)) < 0.05 else f"{x:.1f}".replace(".", ",")


def riordina(passi: list[Passo], da: int, a: int) -> list[Passo]:
    """Sposta un passo: i riquadri si trascinano per **riordinarli**, non per posizionarli.

    Niente coordinate da salvare, niente tela: sotto resta la stessa lista, e questa funzione è tutto
    quello che il trascinamento deve saper fare.
    """
    if not (0 <= da < len(passi)) or da == a:
        return list(passi)
    fuori = list(passi)
    passo = fuori.pop(da)
    fuori.insert(max(0, min(len(fuori), a)), passo)
    return fuori


def si_puo_aggiungere(passi: list[Passo]) -> bool:
    return len(passi) < MAX_PASSI


def riassunto(passi: list[Passo], durata_azione: float = 0.0, ripeti_tutto: int = 1,
              tetti: dict | None = None) -> str:
    """Una riga sotto la striscia: quante chiamate e quanto dura. È il controllo a occhio.

    Serve a vedere **prima** quello che altrimenti si scopre dopo: una catena da tre passi ripetuta
    cinque volte sono quindici telefonate a un nodo e un minuto e mezzo di cose accese, e detto così
    si capisce senza fare il conto.
    """
    catena = srotola(passi, durata_azione, ripeti_tutto, tetti)
    if not catena:
        return _('Niente da fare: aggiungi un pezzo.')
    secondi = quanto_dura(catena)
    return _('{n} passaggi, {s} s in tutto', n=len(catena), s=_numero(secondi))


def suggerimenti(passi: list[Passo], comandi: list[dict], avvisa: bool = False,
                 casse: list | None = None) -> list[str]:
    """«Le funzioni che assomigliano a questa fanno anche…»: suggerimenti, mai spunte già messe.

    Una casella accesa da sola è una cosa che chi configura non ha deciso, e il giorno che fa qualcosa
    che non si aspetta non ha modo di sapere perché. Quindi qui si parla, e si lascia fare.
    """
    tipi = [_tipo_di(p.comando, comandi) for p in passi]
    fuori: list[str] = []
    if tipi and all(t == LUCE for t in tipi) and any(
            vuole_una_cassa(str(c.get("tipo"))) for c in comandi) and casse:
        fuori.append(_('Chi accende una luce di solito ci mette anche un suono.'))
    if SIRENA in tipi and not avvisa:
        fuori.append(_('Un antifurto di solito avvisa anche su Telegram.'))
    if len(passi) == 1 and tipi and vuole_una_cassa(tipi[0]) and passi[0].ripeti == 1:
        fuori.append(_('Un suono solo si sente poco: si può ripetere due o tre volte.'))
    if len(passi) > 1 and all(p.pausa == 0 for p in passi[:-1]):
        fuori.append(_('Fra due pezzi si può lasciare un\'attesa: la freccia si clicca.'))
    return fuori


def cosa_manca_per_provare(passi: list[Passo], nodo_in_linea: bool | None) -> str:
    """Perché il pulsante «Prova» è spento, in parole. Vuoto = si può provare.

    La prova è la cosa che trasforma un modulo in uno strumento — il campanello lo senti prima di
    salvarlo — e la si può fare solo perché la sequenza la eseguiamo noi. Ma un pulsante spento senza
    una ragione è peggio di un pulsante che non c'è: qui la ragione c'è sempre.
    """
    if not passi:
        return _('Prima aggiungi un pezzo.')
    if nodo_in_linea is False:
        return _('Il nodo non risponde.')
    if nodo_in_linea is None:
        return _('Il nodo non è ancora stato letto.')
    return ""


def ascolta_animali(soggetti) -> bool:
    """Questa azione scatta per degli animali? (e non per persone o suoni)

    È la domanda che decide se un'azione è un «dissuasore», con tutto quello che si porta dietro:
    scatta anche a protezione spenta, pausa minima fra due scatti, tetto giornaliero, mai una
    conferma su Telegram — perché un uccello non aspetta che qualcuno prema un pulsante.
    """
    suoi = [str(t) for t in (soggetti or []) if str(t).strip()]
    if not suoi:
        return False
    return all(t != "person" and not t.startswith("sound:") for t in suoi)


def scelta_mista(soggetti) -> bool:
    """Scattare **sia** per le persone **sia** per gli animali: per ora non si può, e va detto.

    Non è una regola di sicurezza, è un limite che abbiamo ancora dentro: i due casi prendono due
    strade diverse nel motore — gli animali fanno scattare un'azione anche a protezione spenta, con
    la pausa minima fra due scatti e il tetto giornaliero; le persone no. Finché quelle due strade
    non diventano una sola, un'azione mischiata farebbe metà di quello che c'è scritto, in silenzio.

    Meglio dirlo in faccia a chi configura che lasciarglielo scoprire fra un mese.
    """
    suoi = [str(t) for t in (soggetti or []) if str(t).strip()]
    animali = [t for t in suoi if t != "person" and not t.startswith("sound:")]
    return bool(animali) and len(animali) != len(suoi)


def tipo_azione_da(passi: list[Passo], comandi: list[dict], se_vuota: str = GENERICO,
                   soggetti=None) -> str:
    """Che tipo di azione è, **dedotto**: il tipo non si sceglie più.

    Era una tendina, ed era la domanda sbagliata: chi configura sa che vuole far suonare la sirena,
    non che «questa azione è di tipo sirena».

    Chi lo decide è cambiato il 23/10, e vale la pena dire perché. Ieri lo deducevo dai **comandi**:
    un passo di dissuasione rendeva l'azione un dissuasore, «perché la regola non scatta per le
    persone non si ammorbidisce». Quella regola non c'è più — Marco vuole poter dire a una persona di
    allontanarsi, con la voce, prima di farle sentire la sirena — e dedurre il tipo dai comandi oggi
    farebbe un danno preciso: salvando quell'azione, «persona» sparirebbe dagli obiettivi, perché un
    dissuasore gli obiettivi persona non li può avere.

    Quindi: **dissuasore è un'azione che scatta per gli animali**, che è poi quello che la parola ha
    sempre voluto dire. Il resto del tipo — sirena, luce, generica — lo portano i comandi, con il più
    severo che vince, e serve solo ai limiti di durata.
    """
    if ascolta_animali(soggetti):
        return DISSUASORE
    tipi = {_tipo_di(p.comando, comandi) for p in passi if p.comando}
    for tipo in (SIRENA, LUCE):
        if tipo in tipi:
            return tipo
    return GENERICO if tipi else se_vuota


def durata_azione_da(passi: list[Passo], scelta: float = 0.0) -> float:
    """La durata da scrivere nell'azione, perché non tagli i pezzi che hanno già la loro.

    ``duration`` dell'azione è anche il **tetto** di ogni passo (vedi ``srotola``). Finché un'azione
    faceva una cosa sola, i due significati coincidevano; con una catena no: salvare «durata 30» con
    dentro un passo da 60 vorrebbe dire salvare un passo che non farà mai quello che c'è scritto. Qui
    la durata dell'azione diventa il più lungo dei due, e i limiti veri — quelli della sirena, quelli
    del dissuasore — restano dove sono, sul tipo e sul comando, che sono gli unici a poterli imporre.
    """
    piu_lungo = max((float(p.durata) for p in passi), default=0.0)
    return max(1.0, float(scelta or 0), piu_lungo)


def limiti_ripetizione() -> tuple[int, int]:
    """Da quanto a quanto può ripetersi una catena: l'interfaccia non deve conoscere i numeri."""
    return 1, MAX_RIPETI


# --- il «+» in tre passi: dove, quale componente, cosa fargli fare (tappa 2, 24/10) ---------------
# Marco: *«quando faccio aggiungi pezzo mi mostra le possibilità di suono/luci ma non i dispositivi;
# devo scegliere prima il dispositivo/nodo e poi da lì vedo cosa c'è di collegato»*. Prima il menu era
# una lista piatta di cose da fare, e **dove** si facessero era una domanda nascosta dentro il pezzo.
# Adesso l'ordine è quello del ragionamento: dove, con cosa, cosa.

# I **generi** di un componente. Non si chiamano LUCE e SUONO: `LUCE` esiste già in questo file, ed
# è il tipo di comando del nodo («light»). Scriverlo due volte non dà nessun errore — il secondo
# vince in silenzio, e una luce smette di essere una luce. Successo il 24/10, trovato perché una
# prova stampava «altro» dove doveva esserci «luce».
GENERE_SUONO, GENERE_LUCE, GENERE_ALTRO = "suono", "luce", "altro"


@dataclass
class Componente:
    """Qualcosa attaccato a un dispositivo, che sa fare una cosa.

    ``chiave`` è quella dell'uscita audio (``usb:UACDemoV10``) per i suoni, il nome del comando per
    tutto il resto: è il pezzo di informazione che il nodo si aspetta indietro.
    """

    chiave: str
    testo: str
    genere: str = GENERE_ALTRO        # GENERE_SUONO, GENERE_LUCE, GENERE_ALTRO
    comando: str = ""          # il comando del nodo che lo fa funzionare
    icona: str = ""
    manca: str = ""            # perché non si può usare, se non si può
    scorciatoia: str = ""


def componenti_di(comandi: list[dict], uscite: list | None = None,
                  casse: list | None = None) -> list[Componente]:
    """Cosa ha attaccato un dispositivo: le uscite audio, le luci, il resto.

    Le uscite audio arrivano dal nodo (``GET /dispositivi``) e sono **una per cassa**: il jack, la
    cassa bluetooth, quella USB. I comandi che suonano non sono componenti — sono quello che una
    cassa sa fare, e si scelgono dopo. I comandi che non suonano (una luce, un'uscita generica) sono
    componenti loro stessi: lì il componente e la cosa da fare coincidono.

    Senza uscite dichiarate resta una voce sola, «la cassa del nodo»: è il nodo prima dell'API 1.10,
    che ha un'uscita sola e non sa dire quale. Sparire sarebbe peggio — quel nodo suona benissimo.

    ``casse`` vuota (non ``None``) vuol dire «gliel'abbiamo chiesto e non ne ha»: allora la voce
    resta, spenta ma cliccabile, e dice **cosa manca**. Sta qui e non sui singoli suoni perché è il
    componente a mancare, non il suono — e messa sul suono finiva dentro il sotto-menu, cioè
    nascosta, che è l'opposto di quello che deve fare (24/10).
    """
    fuori: list[Componente] = []
    suona = [c for c in comandi if vuole_una_cassa(str(c.get("tipo") or GENERICO))]
    if suona:
        for u in (uscite or []):
            fuori.append(Componente(chiave=str(u.get("chiave") or ""), testo=str(u.get("etichetta") or ""),
                                    genere=GENERE_SUONO, icona=EMOJI[SIRENA]))
        if not uscite:
            manca = _('Questo nodo non ha una cassa configurata') if casse is not None and not casse else ""
            fuori.append(Componente(chiave="", testo=_('La cassa del nodo'), genere=GENERE_SUONO,
                                    icona=EMOJI[SIRENA], manca=manca,
                                    scorciatoia="audio_del_nodo" if manca else ""))
    for c in comandi:
        nome = str(c.get("nome") or "")
        tipo = str(c.get("tipo") or GENERICO)
        if not nome or vuole_una_cassa(tipo):
            continue
        fuori.append(Componente(chiave=nome, testo=str(c.get("descrizione") or nome),
                                genere=GENERE_LUCE if tipo == LUCE else GENERE_ALTRO, comando=nome,
                                icona=EMOJI.get(tipo, EMOJI[GENERICO])))
    return fuori


def chiave_pezzo(dispositivo: str, uscita: str, comando: str, suono: str = "") -> str:
    """Il nome di una voce del «+» che dice tutto: dove, da dove, cosa, quale suono."""
    return "pezzo:" + "|".join([dispositivo, uscita, comando, suono])


def pezzo_da_chiave(chiave: str) -> tuple:
    """``("nodo:n1", "usb:X", "dissuasore", "falco")`` da una voce del «+». Vuoto se non è una voce."""
    testo = str(chiave or "")
    if not testo.startswith("pezzo:"):
        return ()
    pezzi = testo[len("pezzo:"):].split("|")
    pezzi += [""] * (4 - len(pezzi))
    return tuple(pezzi[:4])


def cose_da_fare(componente: Componente, comandi: list[dict], nostri=None,
                 dispositivo: str = "") -> list[Proposta]:
    """Cosa può fare **questo** componente: i suoni di una cassa, o l'unica cosa che fa una luce."""
    fuori: list[Proposta] = []
    if componente.genere != GENERE_SUONO:
        tipo = _tipo_di(componente.comando, comandi)
        fuori.append(Proposta(chiave=chiave_pezzo(dispositivo, "", componente.comando),
                              testo=componente.testo, icona=componente.icona, tipo=tipo,
                              spiega=_('Comando del dispositivo'), avviso=avviso_di(tipo)))
        return fuori
    for v in suoni_del_nodo(comandi, nostri):
        tipo = _tipo_di(v["comando"], comandi)
        tuo = v["icona"] == SUONO_TUO
        p = Proposta(chiave=chiave_pezzo(dispositivo, componente.chiave, v["comando"], v["suono"]),
                     testo=v["nome"], icona=v["icona"], tipo=tipo,
                     spiega=_('Un suono che hai caricato tu') if tuo else _('Suono del nodo'),
                     avviso=avviso_di(tipo))
        fuori.append(p)
    return fuori


def passo_da_pezzo(chiave: str, comandi: list[dict]) -> Passo | None:
    """Il passo che nasce da una voce del «+» a tre passi, con la durata sensata già addosso."""
    pezzi = pezzo_da_chiave(chiave)
    if not pezzi:
        return None
    dispositivo, uscita, comando, suono = pezzi
    if not comando:
        return None
    tipo = _tipo_di(comando, comandi)
    suoni = _suoni_di(comando, comandi)
    scelto = suono or (suoni[0] if len(suoni) == 1 else "")
    # Un suono di cui il nodo sa la lunghezza nasce **contato**, non cronometrato: se no il riquadro
    # dice «suona 8 s» e il cog, che la regola la applica, chiede «quante volte» — due domande
    # diverse sulla stessa cosa, e Marco l'ha visto il 25/10 aprendo uno sparo appena aggiunto. La
    # regola è una sola e sta in `passo_da_scelte`: qui si chiede a lei, invece di ricopiarla.
    if a_volte(comando, scelto, comandi, dispositivo):
        passo = passo_da_scelte(f"suono:{comando}:{scelto}", 1, 0.0, 1, 0.0, comandi)
        if passo is not None:
            passo.dispositivo, passo.uscita = dispositivo, uscita
            return passo
    return Passo(comando=comando, suono=scelto,
                 durata=DURATA_PROPOSTA.get(tipo, 5.0), dispositivo=dispositivo, uscita=uscita)
