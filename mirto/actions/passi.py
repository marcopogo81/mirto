# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""I passi di un'azione: una catena invece di un comando solo.

Fino al 22/10 un'azione faceva **una** cosa: un comando sul nodo, per N secondi. Basta per una sirena,
non basta per niente altro. Il campanello che Marco ha descritto è *accendi la luce → aspetta dieci
secondi → suona*, e già quello non ci stava.

Da qui in poi un'azione è una **catena di passi**. Ogni passo dice cosa fare, con che suono, per
quanto, quante volte, e quanto aspettare dopo; la catena intera può ripetersi a sua volta. Le due
ripetizioni sono cose diverse e si vedono in due posti diversi — quella del passo sta **dentro** il
passo, quella della catena **attorno** — ed è il motivo per cui qui ci sono due numeri e non uno solo
che significa due cose.

Tutto questo modulo è **senza thread, senza rete e senza orologio**: ``srotola`` prende una catena e
restituisce l'elenco esatto delle chiamate da fare, nell'ordine, con le attese in mezzo. Chi esegue si
limita a scorrere quell'elenco. Vuol dire che la parte in cui si sbaglia — l'ordine, le ripetizioni,
le attese, i limiti — si prova in un millesimo di secondo e senza un Raspberry acceso.

Le azioni di prima restano valide: un'azione senza passi è una catena di **un** passo, costruita da
``passi_di``. Non c'è niente da convertire sul disco e niente da migrare.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..i18n import _

# I due modi in cui un gruppo di pezzi può partire. Sono valori, non campi: il campo che li porta è
# `a_turno` del capogruppo, e questi due nomi servono a chi deve dire **quale** dei due è.
INSIEME, A_TURNO = "insieme", "a_turno"

# Quanto si lascia costruire. Non sono numeri scelti a caso: sono il punto oltre il quale una catena
# smette di essere una cosa che qualcuno ha pensato e diventa un modo per tenere occupato un nodo.
MAX_PASSI = 12            # passi diversi in una catena
MAX_RIPETI = 20           # ripetizioni di un passo, e della catena
MAX_PAUSA = 600.0         # attesa massima fra due passi: dieci minuti
MAX_CHIAMATE = 120        # chiamate totali dopo lo srotolamento (12 passi x 20 giri sarebbero 240)
# Respiro minimo fra due chiamate **dello stesso comando**, quando chi configura non ne ha chiesto
# nessuno. Senza, la seconda ripetizione parte nell'istante esatto in cui finisce la prima, e il nodo
# la butta via perché quel comando risulta ancora in corso: il campanello suonerebbe una volta sola
# invece di tre. Due decimi non si sentono, e tolgono una corsa fra due orologi che non sono lo
# stesso orologio (il nostro parte quando mandiamo, il suo quando riceve).
RESPIRO = 0.2
# Il nodo tiene accesa una cosa per **almeno un secondo**: ``max(1.0, durata)`` in ``activate``, sia
# sul nodo vero che sul simulatore. Chiedergli mezzo secondo non lo accorcia, lo allunga di nascosto —
# e se qui dentro continuassimo a contare mezzo secondo, la ripetizione successiva arriverebbe mentre
# quel comando è ancora in corso. In quel caso il nodo non protesta: risponde «già attiva» e **non fa
# niente**, che è il modo peggiore di sbagliare, perché la catena va avanti come se avesse suonato.
# Quindi la durata che srotoliamo è quella che il nodo eseguirà davvero, non quella che ci piacerebbe.
MINIMO_NODO = 1.0


@dataclass
class Passo:
    """Una cosa da fare: un comando del nodo, col suo suono, la sua durata e le sue ripetizioni."""

    comando: str
    suono: str = ""
    durata: float = 0.0       # secondi; 0 = quella dell'azione
    ripeti: int = 1           # quante volte di fila questo passo
    pausa: float = 0.0        # attesa dopo ogni esecuzione del passo (fra una ripetizione e l'altra,
                              # e prima del passo successivo): è la freccia del disegno
    volte: int = 0            # suoni: quante ripetizioni esatte chiedere al nodo (0 = vale la durata)
    # **Dove** si fa questa cosa: ``nodo:<id>``, ``computer:questo``… Vuoto vuol dire «quello
    # dell'azione», e il ripiego lo fa chi esegue. Vuoto **deve** restare il valore normale: un'azione
    # scritta prima di oggi non deve guadagnare una riga solo perché il campo è nato (23/10).
    dispositivo: str = ""
    # **Da quale uscita** di quel dispositivo: ``usb:UACDemoV10``, ``bluetooth:AA:BB:…``. Vuoto vuol
    # dire «quella configurata sul nodo», che è come ha sempre funzionato: un'azione scritta prima
    # del 24/10 non guadagna una riga perché il campo è nato (API 1.10, tappa 2).
    uscita: str = ""
    # **Sta nel gruppo del pezzo prima** invece di venire dopo (tappa 3, 24/10). Il caso che l'ha
    # chiesto: la camera vede una persona al cancello, la cassa emette un suono e **nello stesso
    # istante** si accende la luce. Sul primo passo non vuol dire niente e si ignora: non c'è un
    # «prima» a cui attaccarsi.
    #
    # Si chiamava `insieme` fino alla tappa 5, e quel nome è diventato falso appena un gruppo ha
    # potuto partire **a turno**: era vero per il pezzo di un gruppo che non suona insieme a nessuno.
    # Quello che questo campo dice è *dove sta* il pezzo; **come** parte il gruppo lo dice `a_turno`
    # del capogruppo. Il nome vecchio si continua a leggere dal disco per sempre.
    nel_gruppo: bool = False
    # **Del mio gruppo ne parte una per volta**, a rotazione fra gli scatti (tappa 5, 25/10). Sta sul
    # **capogruppo** e non su chi si attacca, e non è un dettaglio: il modo è del gruppo, e messo su
    # ogni pezzo si potrebbe scrivere un gruppo «uno insieme e uno a turno», che non vuol dire
    # niente. Così quel gruppo non è una cosa che l'interfaccia evita: è una cosa che non si può
    # scrivere. Su un gruppo di un pezzo solo non vuol dire niente e si ignora.
    a_turno: bool = False

    @classmethod
    def da_dict(cls, d: dict) -> "Passo":
        d = dict(d or {})
        return cls(comando=str(d.get("comando") or d.get("action") or "").strip(),
                   suono=str(d.get("suono") or "").strip(),
                   durata=max(0.0, float(d.get("durata") or 0)),
                   ripeti=max(1, min(MAX_RIPETI, int(d.get("ripeti") or 1))),
                   pausa=max(0.0, min(MAX_PAUSA, float(d.get("pausa") or 0))),
                   volte=max(0, min(MAX_RIPETI, int(d.get("volte") or 0))),
                   dispositivo=str(d.get("dispositivo") or "").strip(),
                   uscita=str(d.get("uscita") or "").strip(),
                   # `insieme` è il nome che questo campo aveva il 24/10: le azioni salvate quel
                   # giorno ce l'hanno scritto, e si leggono senza convertire niente sul disco.
                   nel_gruppo=bool(d.get("nel_gruppo", d.get("insieme"))),
                   a_turno=bool(d.get("a_turno")))

    def come_dict(self) -> dict:
        """Solo quello che è stato scelto davvero: un passo semplice resta una riga corta."""
        fuori = {"comando": self.comando}
        for chiave, valore, vuoto in (("suono", self.suono, ""), ("durata", self.durata, 0.0),
                                      ("ripeti", self.ripeti, 1), ("pausa", self.pausa, 0.0),
                                      ("volte", self.volte, 0), ("dispositivo", self.dispositivo, ""),
                                      ("uscita", self.uscita, ""), ("nel_gruppo", self.nel_gruppo, False),
                                      ("a_turno", self.a_turno, False)):
            if valore != vuoto:
                fuori[chiave] = valore
        return fuori


def passi_di(d: dict) -> list[Passo]:
    """I passi di un'azione, da come è scritta nella configurazione.

    Un'azione **senza** passi — cioè tutte quelle scritte prima del 22/10 — è una catena di un passo
    solo: il suo comando, la sua durata, il suo suono se ne ha uno. Non c'è niente da convertire sul
    disco: la conversione avviene leggendo, ogni volta, e una configurazione vecchia resta valida per
    sempre.
    """
    d = dict(d or {})
    scritti = [p for p in (d.get("passi") or []) if isinstance(p, dict)]
    if scritti:
        passi = [Passo.da_dict(p) for p in scritti[:MAX_PASSI]]
        return [p for p in passi if p.comando]
    comando = str(d.get("action") or "").strip()
    if not comando:
        return []
    suoni = [s for s in (d.get("sounds") or []) if str(s).strip()]
    return [Passo(comando=comando, durata=float(d.get("duration") or 0),
                  suono=str(suoni[0]) if len(suoni) == 1 else "")]


def gruppi_di_passi(passi: list[Passo]) -> list[list[int]]:
    """Gli indici dei passi raccolti per gruppo: chi si attacca sta nel gruppo di chi lo precede.

    Un gruppo comincia su un passo che aspetta il suo turno e tira dentro quelli dopo che hanno
    ``nel_gruppo``. La catena resta una fila: questo è solo un modo di leggerla, e sta qui — fra i
    dati — perché la stessa domanda la fanno chi disegna la striscia e chi sceglie cosa far partire.
    Due risposte diverse alla stessa domanda sono il genere di doppione che poi si paga.
    """
    fuori: list[list[int]] = []
    for i, p in enumerate(passi):
        if p.nel_gruppo and fuori:
            fuori[-1].append(i)
        else:
            fuori.append([i])
    return fuori


def scegli_a_turno(passi: list[Passo], giro: int = 0) -> list[Passo]:
    """I passi da eseguire **a questo scatto**: di un gruppo «a turno» ne resta uno, a rotazione.

    Marco il 24/10, sul significato di «a turno»: *«a ogni scatto ne parte una sola, e allo scatto
    dopo tocca a un'altra»*. È quello che il dissuasore fa già con i suoi suoni, e per una ragione
    che vale anche qui: gli animali si abituano in fretta a un suono solo, e il gatto non impara che
    il rumore viene sempre da quella parte.

    La scelta si fa **prima** di srotolare, su un giro in ordine e non a caso: dopo tre scatti sai
    cos'ha suonato, e una prova che non si può prevedere non è una prova. Il pezzo scelto diventa il
    capogruppo del momento — perde `nel_gruppo`, perché non ha più nessuno a cui attaccarsi — e
    `a_turno`, perché la scelta è già stata fatta: chi esegue non scelga mai una seconda volta.

    ``giro`` è il numero dello scatto per quell'azione. Uno scatto che **non è avvenuto** — azione
    già in corso, sirena senza budget, nodo che non risponde al primo pezzo — non consuma un turno:
    chi lo conta lo avanza dopo, non prima.
    """
    import dataclasses  # noqa: PLC0415

    fuori: list[Passo] = []
    for gruppo in gruppi_di_passi(passi):
        capo = passi[gruppo[0]]
        if len(gruppo) > 1 and capo.a_turno:
            scelto = passi[gruppo[max(0, int(giro or 0)) % len(gruppo)]]
            fuori.append(dataclasses.replace(scelto, nel_gruppo=False, a_turno=False))
        else:
            fuori += [passi[i] for i in gruppo]
    return fuori


def capogruppo(passi: list[Passo], i: int) -> int | None:
    """Di che gruppo fa parte il passo ``i``: l'indice del suo capo, o ``None`` se ``i`` non esiste.

    Il capo è l'unico posto dove è scritto **come** parte il gruppo, quindi è l'unico da cambiare
    quando si cambia modo. Si ricava dai gruppi e non contando a mano i `nel_gruppo` indietro: la
    regola di cosa sia un gruppo sta in un posto solo.
    """
    for gruppo in gruppi_di_passi(passi):
        if i in gruppo:
            return gruppo[0]
    return None


def modo_del_gruppo(passi: list[Passo], gruppo: list[int]) -> str:
    """``"a_turno"``, ``"insieme"`` o ``""`` (un pezzo solo): come parte questo gruppo.

    Lo dice il **capogruppo**, che è l'unico posto dove è scritto. Un gruppo di un pezzo solo non ha
    un modo: non parte né insieme a qualcuno né a turno con qualcuno.
    """
    if len(gruppo) < 2:
        return ""
    return A_TURNO if passi[gruppo[0]].a_turno else INSIEME


def srotola(passi: list[Passo], durata_azione: float = 0.0, ripeti_tutto: int = 1,
            tetti: dict | None = None, dispositivo: str = "",
            tetti_per=None) -> list[dict]:
    """La catena srotolata: l'elenco esatto delle chiamate da fare, nell'ordine.

    Ogni voce: ``{"comando", "suono", "durata", "attesa_dopo"}``. Chi esegue scorre questa lista e non
    deve sapere niente di ripetizioni né di catene — che è il punto: la parte in cui si sbaglia sta
    tutta qui, dove si prova senza aspettare.

    L'ultima attesa viene **tolta**: aspettare dopo l'ultima cosa vuol dire tenere occupata un'azione
    per niente, e far sembrare più lunga di quello che è una catena già finita.

    ``dispositivo``: dove mandare i passi che non dicono il loro. È qui e non nel passo perché chi
    esegue legge **solo** questa lista: se il dispositivo non arrivasse fin qui, una catena su due
    nodi non si potrebbe eseguire, qualunque cosa ci sia scritto in configurazione. Vuoto resta
    vuoto: non si inventa un nodo a chi non ne ha uno.

    ``tetti_per``: una funzione ``dispositivo → {comando: secondi}``, per quando i pezzi stanno su
    dispositivi diversi. I tetti li dichiara il **nodo**, quindi in una catena che attraversa due nodi
    non possono venire da uno solo: la sirena dell'ingresso non sa niente di quella del garage. Senza
    questa funzione vale ``tetti`` per tutti, che è il caso di sempre.

    ``tetti``: nome del comando → secondi massimi per quel comando, da come il **nodo** lo dichiara
    (una sirena tre minuti, un dissuasore trenta secondi). Serve perché in una catena i comandi si
    mescolano: finché un'azione faceva una cosa sola bastava il limite del *tipo di azione*, ma una
    luce e una sirena nello stesso elenco non possono avere lo stesso tetto. Chi non è elencato non ha
    limiti suoi e risponde solo a ``durata_azione``; il limite più stretto vince sempre.
    """
    ripeti_tutto = max(1, min(MAX_RIPETI, int(ripeti_tutto or 1)))
    # «durata_azione» non è solo il valore da mettere dove manca: è anche il **tetto** di ogni passo.
    # Chi la passa ha già applicato i limiti del tipo di azione (tre minuti per una sirena, trenta
    # secondi per un dissuasore); senza questa riga un passo scritto a mano con «durata: 300» li
    # scavalcherebbe tutti, e Mirto chiederebbe al nodo una cosa che non ha il permesso di chiedere.
    massimo = max(0.0, float(durata_azione or 0))
    tetti = {str(k): float(v) for k, v in (tetti or {}).items() if float(v or 0) > 0}
    fuori: list[dict] = []
    for _giro in range(ripeti_tutto):
        for passo in passi:
            if not passo.comando:
                continue
            # Il tetto di **questo** passo: quello dell'azione e quello del comando, il più stretto dei
            # due. Resta una variabile a parte: schiacciare «massimo» qui vorrebbe dire che una sirena
            # in mezzo alla catena accorcia tutti i passi che vengono dopo.
            dove = passo.dispositivo or dispositivo
            suoi_tetti = tetti
            if tetti_per is not None:
                suoi_tetti = {str(k): float(v) for k, v in (tetti_per(dove) or {}).items()
                              if float(v or 0) > 0}
            suoi = [t for t in (massimo, suoi_tetti.get(passo.comando, 0.0)) if t > 0]
            tetto = min(suoi) if suoi else 0.0
            for _volta in range(max(1, int(passo.ripeti))):
                durata = float(passo.durata or tetto or 0.0)
                # Prima il tetto, poi il pavimento, e il pavimento non scavalca mai il tetto: così la
                # durata srotolata è sempre quella che il nodo eseguirà (vedi MINIMO_NODO) e sempre
                # dentro i limiti del tipo di azione.
                if durata:
                    if tetto:
                        durata = min(durata, tetto)
                    durata = max(durata, min(MINIMO_NODO, tetto) if tetto else MINIMO_NODO)
                attesa = float(passo.pausa)
                if not attesa and fuori and fuori[-1]["comando"] == passo.comando:
                    attesa = RESPIRO          # due volte lo stesso comando: vedi RESPIRO
                if fuori and not fuori[-1]["attesa_dopo"] and fuori[-1]["comando"] == passo.comando:
                    fuori[-1]["attesa_dopo"] = RESPIRO
                # Stare nel gruppo vale solo per la **prima** ripetizione di un passo: le ripetizioni
                # di uno stesso pezzo restano in fila fra loro — «tre volte insieme» non vuol dire
                # niente. `a_turno` non arriva fin qui: la scelta l'ha già fatta `scegli_a_turno`
                # prima di srotolare, e chi esegue trova una catena in cui c'è solo quello che deve
                # partire. Chi esegue non sceglie: esegue.
                fuori.append({"comando": passo.comando, "suono": passo.suono,
                              "durata": durata, "attesa_dopo": attesa, "volte": passo.volte,
                              "dispositivo": dove, "uscita": passo.uscita,
                              "nel_gruppo": bool(passo.nel_gruppo) and _volta == 0 and bool(fuori)})
                if len(fuori) >= MAX_CHIAMATE:
                    fuori[-1]["attesa_dopo"] = 0.0
                    return fuori
    if fuori:
        fuori[-1]["attesa_dopo"] = 0.0
    return fuori


def quanto_dura(catena: list[dict]) -> float:
    """Quanto va avanti la catena, attese comprese. È il tempo in cui l'azione è «in corso».

    Le chiamate che partono **insieme** non si sommano: il gruppo dura quanto la sua più lunga. Un
    suono di un secondo e una luce di trenta accesi nello stesso istante sono trenta secondi, non
    trentuno — ed è il tempo in cui l'azione risulta in corso, quindi sbagliarlo vuol dire o
    dichiararla finita mentre suona, o tenerla occupata quando ha finito.
    """
    totale, gruppo = 0.0, 0.0
    for c in catena:
        quanto = float(c.get("durata") or 0) + float(c.get("attesa_dopo") or 0)
        if c.get("nel_gruppo"):
            gruppo = max(gruppo, quanto)
        else:
            totale += gruppo
            gruppo = quanto
    return totale + gruppo


def inizio_del_gruppo(catena: list[dict], i: int) -> int:
    """L'indice della prima chiamata del gruppo a cui appartiene la ``i``-esima."""
    while i > 0 and catena[i].get("nel_gruppo"):
        i -= 1
    return i


def quanto_dura_il_gruppo(catena: list[dict], i: int) -> float:
    """Quanto va avanti il gruppo della chiamata ``i``: la più lunga delle sue, attesa compresa.

    È la stessa regola di ``quanto_dura``, e sta qui perché chi **esegue** deve usare quella e non
    un'altra. Fino alla tappa 4 chi eseguiva programmava il pezzo dopo un gruppo sulla durata
    dell'**ultima** chiamata del gruppo: con un suono di un secondo e una luce di trenta accesi
    insieme, il pezzo dopo partiva dopo un secondo mentre il conto dell'azione diceva trenta. Due
    verità sullo stesso tempo, e quella che si sente è sempre l'altra.
    """
    j = inizio_del_gruppo(catena, i)
    quanto = 0.0
    k = j
    while k < len(catena) and (k == j or catena[k].get("nel_gruppo")):
        quanto = max(quanto, float(catena[k].get("durata") or 0) + float(catena[k].get("attesa_dopo") or 0))
        k += 1
    return quanto


def quanto_suona(catena: list[dict], comandi: dict) -> float:
    """Secondi di **sirena** in una catena, per i limiti di legge e di buon vicinato.

    Se la chiamata porta già scritto il suo ``tipo`` — ce lo mette chi costruisce la catena, che è
    l'unico a sapere **quale nodo** la eseguirà — vale quello. Serve da quando una catena attraversa
    due nodi: lo stesso nome può essere una sirena di qua e un dissuasore di là, e un dizionario solo
    non può rispondere per tutt'e due (23/10).

    ``comandi``: nome del comando → tipo dichiarato dal nodo (``siren``, ``deterrent``, ``light``…).
    Un comando che il nodo non dichiara non si conta come sirena: non si inventa un allarme a partire
    da un nome sconosciuto, e chi non è dichiarato viene comunque fermato dal nodo.
    """
    return sum(float(c.get("durata") or 0) for c in catena
               if str(c["tipo"] if "tipo" in c else comandi.get(str(c.get("comando")), "")) == "siren")


def problemi(passi: list[Passo], ripeti_tutto: int = 1) -> list[str]:
    """Cosa non va in una catena, in parole. Elenco vuoto = si può salvare."""
    guai = []
    if not passi:
        guai.append(_("La catena è vuota: aggiungi almeno una cosa da fare."))
    if len(passi) > MAX_PASSI:
        guai.append(_("Troppi passi: al massimo {max}.", max=MAX_PASSI))
    for passo in passi:
        if not passo.comando:
            guai.append(_("Un passo non dice cosa fare."))
        if passo.ripeti > MAX_RIPETI:
            guai.append(_("«{comando}» si ripete troppe volte: al massimo {max}.",
                          comando=passo.comando, max=MAX_RIPETI))
        if passo.pausa > MAX_PAUSA:
            guai.append(_("L'attesa dopo «{comando}» è troppo lunga: al massimo {max} minuti.",
                          comando=passo.comando, max=int(MAX_PAUSA // 60)))
    if int(ripeti_tutto or 1) > MAX_RIPETI:
        guai.append(_("La catena si ripete troppe volte: al massimo {max}.", max=MAX_RIPETI))
    return guai


@dataclass
class Catena:
    """Una catena pronta da eseguire: i passi srotolati e quanto dura in tutto."""

    chiamate: list[dict] = field(default_factory=list)
    durata: float = 0.0

    @classmethod
    def da_azione(cls, d: dict, durata_azione: float = 0.0) -> "Catena":
        catena = srotola(passi_di(d), durata_azione, int((d or {}).get("ripeti_tutto") or 1))
        return cls(chiamate=catena, durata=quanto_dura(catena))
