# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Le regole del pannello «Audio del nodo», scritte fuori dalla finestra.

Stanno qui e non in ``gui/audio_ui.py`` per lo stesso motivo di ``actions/diagnosi.py``: le finestre
non si possono eseguire dove questo codice viene scritto (PySide6 non si installa), e tutte e tre
queste regole sono nate da un guaio visto su hardware la sera del 21/10, col Pi 4 e una JBL Flip 6.

* ``quale_cassa_tenere`` — la cassa scelta deve restare scelta quando l'elenco si rifà;
* ``esito_arrivato`` — la risposta del nodo è quella di **adesso**, non quella del tentativo di prima;
* ``attesa_scaduta`` — un'attesa senza fine è un programma bloccato: dopo il tempo dichiarato dal nodo,
  più mezzo minuto di grazia, la finestra smette di aspettare e lo dice;
* ``uscita_della_scheda`` — di quale dispositivo parla il riquadro in basso del pannello: lo decide
  la scheda aperta, non una tendina che elenca tutto sotto a tutto.

Qui c'era anche ``si_puo_provare``, che diceva quando il din-don aveva senso: il nodo suonava solo
dall'uscita sua, quindi provare una cassa voleva dire prima renderla l'uscita del nodo — cioè
riavviare il servizio per un din-don. Dall'API 1.14 la prova dice **da dove**, e quella regola non
ha più niente da regolare: è sparita invece di restare lì a mentire.

Dal 22/10, con la prima cassa USB e il primo microfono USB attaccati a un nodo, ci sono anche le
**manopole**: quelle vere della scheda audio, lette dal nodo con ``amixer``. Non è un vezzo da
smanettoni — quel microfono nasce col guadagno a 0 su 16 e l\'automatismo acceso, e in quello stato
una voce arriva all\'1% del fondo scala: il riconoscimento dei suoni non sente niente e nessuno può
capire perché.

* ``manopole_da_mostrare`` — si mostrano le manopole delle **due schede che si stanno usando**, la
  cassa e il microfono, e non di tutte le schede del nodo: un HDMI e due uscite che nessuno usa
  sarebbero sei cursori che non servono a niente;
* ``etichetta_manopola`` — il nome ALSA («PCM», «Mic») col nome in chiaro davanti, quando lo
  conosciamo. Il nome vero resta scritto, perché è quello che si trova nelle guide e in ``amixer``.
"""

from __future__ import annotations

from ..i18n import N_, _

# Le uscite che hanno una scheda audio vera, e quindi delle manopole. Il Bluetooth no: il volume di
# una cassa Bluetooth sta sulla cassa, e bluealsa non espone niente da girare.
USCITE_CON_SCHEDA = ("jack", "usb", "hdmi")


def quale_cassa_tenere(in_corso: str, scelta: str, configurata: str, disponibili: list[str]) -> str:
    """Quale cassa deve restare selezionata quando l'elenco si rifà.

    Il 21/10 l'elenco si rifaceva dopo ogni tentativo e la scelta tornava alla prima riga: dopo un
    abbinamento fallito risultava selezionato un dispositivo BLE qualsiasi al posto della JBL scelta,
    e il tentativo seguente sarebbe andato su quello.

    Nell'ordine: la cassa del tentativo in corso (così l'errore parla di lei e si riprova su di lei),
    poi quella scelta da chi guarda, poi quella configurata sul nodo. Se nessuna delle tre è
    nell'elenco, la prima — che è quello che fa un menù a tendina da solo.
    """
    for candidata in (in_corso, scelta, configurata):
        if candidata and candidata in disponibili:
            return candidata
    return disponibili[0] if disponibili else ""


def firma_esito(esito: dict | None) -> tuple:
    """Come riconoscere «questa risposta del nodo» da un'altra uguale di prima."""
    e = esito or {}
    return (str(e.get("azione") or ""), int(e.get("quando") or 0), str(e.get("esito") or ""),
            str(e.get("messaggio") or ""))


def esito_arrivato(esito: dict | None, prima: tuple, azione: str) -> bool:
    """È arrivata la risposta a quello che stiamo aspettando?

    Il nodo lascia l'esito in un file, e quel file contiene ancora la risposta del tentativo
    precedente. Guardare solo il nome dell'azione voleva dire prendere per buona quella vecchia: si
    premeva «Cerca casse…» e la finestra diceva subito «ricerca finita», con l'elenco di prima.
    """
    e = esito or {}
    if str(e.get("azione") or "") != azione:
        return False
    return firma_esito(e) != prima


def attesa_scaduta(inizio: float, attesa_s: float, adesso: float) -> bool:
    """Quando smettere di aspettare una risposta che non arriva.

    Senza questo, se il nodo non scrive l'esito (servizio da root fermo, biglietto perso, nodo
    riavviato) la finestra resta a girare per sempre: barra che si muove, pulsanti spenti, e niente
    che dica cos'è successo. Mezzo minuto di grazia oltre il tempo che il nodo stesso ha dichiarato.
    """
    return (adesso - inizio) > max(float(attesa_s or 0), 20.0) + 30.0


def etichetta_manopola(manopola: dict) -> str:
    """Come si chiama una manopola per una persona, col nome vero accanto.

    Il nome ALSA non si butta: è quello che si trova nelle guide, nei forum e in ``amixer -c``, e chi
    arriva qui da una ricerca in rete deve ritrovarlo. Ma «PCM» da solo non dice niente a nessuno.
    """
    nome = str(manopola.get("nome") or "")
    chiaro = {
        "pcm": _('Volume'),
        "master": _('Volume'),
        "speaker": _('Volume della cassa'),
        "headphone": _('Volume delle cuffie'),
        "mic": _('Guadagno del microfono'),
        "capture": _('Guadagno del microfono'),
        "mic boost": _('Amplificazione del microfono'),
        "auto gain control": _('Automatismo del guadagno'),
        "automatic gain control": _('Automatismo del guadagno'),
    }.get(nome.strip().lower())
    return _('{chiaro} ({nome})', chiaro=chiaro, nome=nome) if chiaro else nome


def manopole_da_mostrare(manopole: dict | None, stato: dict | None) -> list[dict]:
    """I gruppi di manopole da mostrare nel pannello: la scheda della sirena e quella del microfono.

    Solo quelle due, e solo se esistono davvero sul nodo: le altre schede di un Raspberry — due HDMI e
    il jack che nessuno usa — sarebbero una fila di cursori che non servono. Se la cassa e il
    microfono sono la **stessa** scheda (certe cuffie USB) il gruppo è uno, col titolo che lo dice.
    """
    schede = dict((manopole or {}).get("schede") or {})
    s = dict(stato or {})
    micro = dict(s.get("microfono") or {})
    ruoli: dict[str, list[str]] = {}
    if str(s.get("uscita") or "") in USCITE_CON_SCHEDA and str(s.get("scheda") or ""):
        ruoli.setdefault(str(s["scheda"]), []).append(_('Cassa'))
    if str(micro.get("scheda") or "") and micro.get("attivo"):
        ruoli.setdefault(str(micro["scheda"]), []).append(_('Microfono'))
    fuori = []
    for scheda, nomi in ruoli.items():
        dati = schede.get(scheda)
        if not isinstance(dati, dict) or not dati.get("manopole"):
            continue      # la scheda non risponde ad amixer: non si inventa un cursore che non c'è
        fuori.append({"scheda": scheda,
                      "titolo": _('{ruoli} — {nome}', ruoli=" e ".join(nomi),
                                  nome=str(dati.get("nome") or scheda)),
                      "manopole": [m for m in dati["manopole"] if isinstance(m, dict)]})
    return fuori


def chiave_manopola(manopola: dict) -> str:
    """Come si chiama una manopola per il nodo: ``nome,indice``. Deve combaciare con ``mixer.chiave``."""
    return f"{manopola.get('nome', '')},{int(manopola.get('indice', 0) or 0)}"


def microfono_da_mandare(prima: dict | None, attivo: bool, scheda: str) -> dict | None:
    """Cosa mandare al nodo per il microfono, o ``None`` se non è cambiato niente.

    Conta perché accendere il microfono fa **riavviare il servizio del nodo**: mandarlo quando nessuno
    ha toccato niente vorrebbe dire un nodo che si riavvia ogni volta che si apre il pannello e si
    premono «Applica» — e un riavvio è mezzo minuto senza camera.
    """
    p = dict(prima or {})
    if bool(p.get("attivo")) == bool(attivo) and str(p.get("scheda") or "") == str(scheda or ""):
        return None
    return {"attivo": bool(attivo), "scheda": str(scheda or "")}


def casse_ricordate(bluetooth: dict | None) -> list[dict]:
    """Le casse che il nodo **ricorda**: quelle abbinate, su cui si può già suonare.

    Le tiene BlueZ, non noi. Sono una cosa diversa da quelle che si vedono passare in una ricerca, e
    dall'8/11 stanno in un elenco loro: su queste l'unica azione è dimenticarle.
    """
    fuori, visti = [], set()
    for c in (dict(bluetooth or {}).get("casse") or []):
        if isinstance(c, dict) and c.get("mac"):
            mac = str(c["mac"]).upper()
            if mac in visti:
                continue
            visti.add(mac)
            fuori.append({"mac": mac, "nome": str(c.get("nome") or "(senza nome)"),
                          "abbinata": True, "collegata": bool(c.get("collegata"))})
    return fuori


def casse_trovate(bluetooth: dict | None, da_quando: float) -> list[dict]:
    """Le casse viste da una ricerca fatta **dopo** ``da_quando``, tolte quelle già ricordate.

    Il nodo tiene l'ultima ricerca in un file, e quel file sopravvive a tutto: riaprendo la finestra
    si rivedeva l'elenco di giorni prima, con dentro casse spente da un pezzo marcate «da abbinare».
    L'8/11 Marco apre il programma e trova la sua cassa «da abbinare» mentre quella non è nemmeno in
    abbinamento; poi preme «Cerca casse…» e per qualche secondo non cambia niente, perché sta ancora
    guardando il file vecchio. Due sintomi, una causa: quell'elenco non diceva *cosa c'è adesso*,
    diceva *cosa si è visto l'ultima volta*.

    Si passa il momento in cui la finestra si è aperta: una ricerca più vecchia non si mostra. Così
    l'elenco nasce **vuoto** — che è la verità, perché nessuno ha ancora cercato — e si riempie solo
    con quello che la ricerca di adesso ha davvero visto. Il filtro si fa sul dato (il nodo scrive
    `quando` insieme ai risultati) e non su una bandierina della finestra: una bandierina si dimentica
    di cambiare, una data no.
    """
    bt = dict(bluetooth or {})
    ricerca = dict(bt.get("trovate") or {})
    if float(ricerca.get("quando") or 0) < float(da_quando):
        return []
    gia = {c["mac"] for c in casse_ricordate(bt)}
    fuori, visti = [], set()
    for c in (ricerca.get("casse") or []):
        if not (isinstance(c, dict) and c.get("mac")):
            continue
        mac = str(c["mac"]).upper()
        # Una cassa già ricordata non si ripropone: vederla anche qui farebbe premere «Abbina» su
        # una che è già a posto, ed è il gesto che il 21/10 incantava il controller.
        if mac in gia or mac in visti:
            continue
        visti.add(mac)
        fuori.append({"mac": mac, "nome": str(c.get("nome") or "(senza nome)"),
                      "abbinata": False, "collegata": False})
    return fuori


def chiave_uscita(uscita: str, scheda: str = "", mac: str = "") -> str:
    """Come si nomina un dispositivo di uscita in un posto solo: ``usb:UACDemoV10``, ``bluetooth:AA:BB:…``."""
    return f"{uscita}:{(mac if uscita == 'bluetooth' else scheda) or ''}"


# Come si chiama un'uscita **in due parole**, per scriverla dentro un riquadro della catena. È più
# corto dei nomi di `sistema.USCITA_NOME` («Scheda USB», «Uscita di sistema del Raspberry») perché
# lì c'è una colonna e qui c'è un riquadro di centodieci pixel, e un nome che non ci sta è un nome
# che non si legge. Il bluetooth si chiama «Bluetooth» e non col nome della cassa: il nome di una
# cassa è una marca, e una marca nell'interfaccia non ci va.
NOME_CORTO = {"jack": N_("Jack"), "usb": N_("USB"), "bluetooth": N_("Bluetooth"), "hdmi": N_("HDMI"),
              "predefinito": N_("Uscita di sistema")}


def nome_corto_uscita(chiave: str) -> str:
    """``"usb:UACDemoV10"`` → ``«USB»``. Chiave vuota o sconosciuta: stringa vuota, e non si inventa.

    Vuoto vuol dire «non si sa da quale cassa uscirà», ed è il caso di un pezzo che segue l'uscita
    configurata su un nodo che non ci ha ancora detto quale sia. Scrivere lì un nome a caso sarebbe
    peggio che non scrivere niente: chi legge il riquadro crederebbe di sapere da dove suona.
    """
    testo = str(chiave or "").strip()
    if ":" not in testo:
        return ""
    return _(NOME_CORTO.get(testo.split(":", 1)[0].strip().lower(), ""))


def uscite_per_le_azioni(dispositivi: dict | None) -> list[dict]:
    """I dispositivi su cui il nodo può suonare, nell'ordine delle tab: [{chiave, uscita, scheda, mac, etichetta}].

    Serve finché la scelta del dispositivo non sta dentro l'azione (tappa 2): il nodo deve pur sapere
    da dove suonare una sirena, e qui si dice quale usare. Il jack spento non compare — spegnerlo vuol
    dire «lì non c'è attaccato niente», e proporlo lo stesso sarebbe offrire un posto muto.
    """
    d = dict(dispositivi or {})
    fuori: list[dict] = []
    jack = dict(d.get("jack") or {})
    if jack.get("presente") and jack.get("attivo", True):
        fuori.append({"uscita": "jack", "scheda": str(jack.get("scheda") or ""), "mac": "",
                      "etichetta": _('Jack 3,5 mm')})
    # Prima il filo, poi la radio. Deciso da Marco l'8/11: «la cassa principale deve prediligere
    # connessioni sicure come jack/usb». Non è un gusto: una cassa Bluetooth si scollega da sola, si
    # dimentica l'abbinamento quando qualcuno la rimette in abbinamento, e il 21/10 ha tenuto fermo
    # un nodo per cinque ore. L'uscita di una sirena è l'ultimo posto dove mettere la cosa che cade
    # più spesso — e questa lista è anche l'ordine in cui le uscite si propongono.
    for gruppo, come in (("usb", _('Cassa USB')), ("hdmi", _('HDMI'))):
        for c in (d.get(gruppo) or []):
            if isinstance(c, dict) and c.get("scheda"):
                fuori.append({"uscita": gruppo, "scheda": str(c["scheda"]), "mac": "",
                              "etichetta": _('{come} — {nome}', come=come, nome=str(c.get("nome") or c["scheda"]))})
    for c in casse_ricordate(d.get("bluetooth")):
        # Col tipo davanti, come le altre. Marco, 8/11: *«nelle descrizioni c'è il tipo jack, usb +
        # modello, ma manca bluetooth: viene indicato solo il nome del dispositivo»* — e in un elenco
        # dove le altre voci dicono come sono attaccate, una che dice solo un nome di marca sembra
        # una cosa d'altro tipo.
        fuori.append({"uscita": "bluetooth", "scheda": "", "mac": c["mac"],
                      "etichetta": _('Cassa Bluetooth — {nome}', nome=c["nome"])})
    for u in fuori:
        u["chiave"] = chiave_uscita(u["uscita"], u["scheda"], u["mac"])
    return fuori


def uscita_della_scheda(scheda: str, dispositivi: dict | None, mac: str = "", card: str = "") -> dict:
    """Di quale dispositivo parla il riquadro in basso, guardando quella scheda. ``{}`` se nessuno.

    Marco, 8/11: *«vorrei fosse statica»*, della tendina che elencava tutte le uscite sotto a tutte
    le schede. Aveva già detto il perché il giorno prima: *«è fuorviante vedere 3 scelte sui 3 tab in
    ogni scheda dei tab»*. Una tendina che sotto «Bluetooth» propone anche il jack e l'USB fa sembrare
    che la scheda aperta non conti — e il pulsante di prova lì accanto diventa indecifrabile: suona
    dalla cassa che sto guardando o da quella scelta nella tendina?

    Qui si risponde una volta sola, e fuori dall'interfaccia perché si possa provare: **la scheda
    aperta decide il dispositivo**. Il jack è uno solo; il Bluetooth è la cassa scelta fra quelle
    ricordate; l'USB è il dispositivo scelto fra quelli attaccati. La scheda del microfono non è
    un'uscita e non ha niente da dire: ``{}``, e il riquadro sparisce.
    """
    uscite = uscite_per_le_azioni(dispositivi)
    if scheda == "jack":
        return next((u for u in uscite if u["uscita"] == "jack"), {})
    if scheda == "bluetooth":
        return next((u for u in uscite if u["uscita"] == "bluetooth" and u["mac"] == mac), {}) if mac else {}
    if scheda == "usb":
        return next((u for u in uscite if u["uscita"] in ("usb", "hdmi") and u["scheda"] == card), {}) if card else {}
    return {}


def uscita_in_uso(dispositivi: dict | None) -> str:
    """La chiave del dispositivo che il nodo sta usando adesso per le azioni, o stringa vuota."""
    p = dict((dispositivi or {}).get("predefinito") or {})
    if not p.get("uscita"):
        return ""
    return chiave_uscita(str(p["uscita"]), str(p.get("scheda") or ""), str(p.get("bluetooth_mac") or ""))


# --- il livello del microfono, per capire se il guadagno è giusto ------------
#
# Chiesto da Marco il 22/10, guardando il pannello: «metti i sensori che leggono l'audio e fanno
# capire se le regolazioni vanno bene». Ha ragione, e il motivo è semplice: regolare un guadagno senza
# vedere il livello è regolare un volume a orecchie tappate. Le soglie qui sotto non sono inventate,
# vengono dalle misure fatte sul Raspberry con quel microfono USB (vedi node/mirto_node/mixer.py):
# col guadagno di fabbrica una voce stava all'1 % del fondo scala, con guadagno al massimo e
# automatismo spento al 38 %, e un colpo di mani a due metri al 50 %.
SILENZIO = 0.005     # sotto: non sta succedendo niente, e non si può dire se il livello sia giusto
PIANO = 0.05         # sotto: il riconoscimento dei suoni non sentirà quasi nulla
FORTE = 0.85         # sopra: manca margine, i suoni forti si schiacciano
SATURO = 0.98        # sopra: il segnale è tagliato, e tagliato non torna indietro

# Quanto si tiene fermo il picco prima di lasciarlo scendere. Fra una parola e l'altra il livello va a
# zero, e un giudizio che cambiasse lì manderebbe ad alzare un guadagno già giusto, tre volte al
# secondo. Stava solo nel pannello dell'audio; dal 26/10 serve anche alla riga di *Sistema*, e una
# costante sola è l'unico modo perché le due schermate non diano due giudizi diversi dello stesso
# microfono.
PICCO_TENUTO = 3.0


def picco_tenuto(picco_ora: float, tenuto: float, quando: float, adesso: float) -> tuple[float, float]:
    """Il picco da usare per il giudizio, e da quando è quello: ``(tenuto, quando)``.

    Sale subito e scende solo dopo ``PICCO_TENUTO`` secondi. È la stessa regola per chiunque misuri
    quel microfono: il pannello che serve a regolare il guadagno e la riga di *Sistema* che dice
    com'è messo.
    """
    if picco_ora >= tenuto or adesso - quando > PICCO_TENUTO:
        return float(picco_ora), float(adesso)
    return float(tenuto), float(quando)


def livello_da_pcm(pcm: bytes) -> tuple[float, float]:
    """Livello di un pezzo di audio PCM 16 bit mono: ``(media, picco)``, da 0 a 1.

    La **media** è la stessa grandezza che la colonna *Sistema* mostra come barretta (RMS), così le
    due cose dicono lo stesso; il **picco** serve a dire se il segnale sta per essere tagliato, che è
    la cosa che la media non vede: un colpo di mani alza il picco e quasi non muove la media.
    """
    import array  # noqa: PLC0415

    campioni = array.array("h")
    campioni.frombytes(pcm[:len(pcm) - len(pcm) % 2])
    if not campioni:
        return 0.0, 0.0
    picco = max(max(campioni), -min(campioni)) / 32768.0
    media = (sum(c * c for c in campioni) / len(campioni)) ** 0.5 / 32768.0
    return media, picco


def giudizio_livello(picco: float) -> tuple[str, str]:
    """Come va il livello, in due parole e un consiglio: ``(esito, consiglio)``.

    ``esito``: "silenzio", "piano", "bene", "forte" o "saturo". Il giudizio si dà sul **picco** del
    periodo appena passato, non sull'ultimo istante: fra una parola e l'altra il livello scende a zero
    e un misuratore che per questo dicesse «troppo piano» manderebbe ad alzare un guadagno che è già
    giusto.
    """
    if picco < SILENZIO:
        return "silenzio", _('Non sento niente: fai un rumore vicino al microfono — parla, o batti le mani.')
    if picco < PIANO:
        return "piano", _('Troppo piano: alza il guadagno del microfono. Sotto questo livello il '
                          'riconoscimento dei suoni non sente quasi nulla.')
    if picco > SATURO:
        return "saturo", _('Satura: abbassa il guadagno. Il segnale viene tagliato, e tagliato non '
                           'torna più indietro.')
    if picco > FORTE:
        return "forte", _('Forte: abbassa un po\' il guadagno, così resta margine per i suoni forti.')
    return "bene", _('Livello giusto: i suoni forti ci stanno dentro con margine.')


# --- chi ascolta il microfono di un nodo (24/10) ------------------------------
# Due interruttori diversi e nessuno dei due lo diceva: il **nodo** accende il suo microfono, e
# **Mirto** decide se ascoltarlo. Marco aveva il primo acceso e il secondo spento, e nell'elenco di
# un'azione non compariva niente, senza un motivo da nessuna parte. Il secondo stava in «Suoni», che
# è il posto sbagliato due volte: non è una proprietà dei suoni, ed era uno solo per tutti i nodi.
# Adesso sta accanto al microfono di quel nodo, dove si guarda quando si configura quel nodo.

def ascolta_microfono(valore, nodo: str) -> bool:
    """Mirto ascolta il microfono di questo nodo?

    ``valore`` è ``audio.node_mics``: un dizionario ``{nodo: acceso}`` da oggi, un ``bool`` nelle
    configurazioni scritte prima — e in quel caso vale per tutti, che è quello che voleva dire.
    """
    if isinstance(valore, dict):
        return bool(valore.get(str(nodo), True))
    return True if valore is None else bool(valore)


def con_ascolto(valore, nodo: str, acceso: bool) -> dict:
    """Il nuovo ``audio.node_mics`` con la scelta di questo nodo dentro, senza perdere gli altri."""
    fuori = dict(valore) if isinstance(valore, dict) else {}
    fuori[str(nodo)] = bool(acceso)
    return fuori
