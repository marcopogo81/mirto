# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Cosa non va in un nodo, detto a chi non ha un terminale.

Il nodo risponde con una ventina di campi fra ``/stato``, ``/info`` e ``/configura/camera``: sono fatti,
non diagnosi. Un tester che vede «la camera non si vede» non deve mettersi a confrontare numeri — e noi
non dobbiamo chiedergli di incollarci un registro per capire che il cavo piatto è fuori sede.

Qui si trasforma quella roba in **due** cose: un elenco di problemi, ognuno col rimedio che una persona
può eseguire, e un rapporto in testo che si incolla in un messaggio. Il rapporto non contiene segreti:
niente PIN, niente password della camera, niente chiavi.

È tutto codice senza interfaccia, perché le finestre qui non si possono provare: la parte che decide
cosa è un problema deve stare dove le prove girano davvero.
"""

from __future__ import annotations

import time

from ..i18n import _

ERRORE, AVVISO = "errore", "avviso"      # non funziona / funziona ma va sistemato

CALDO, BOLLENTE = 70.0, 80.0             # °C: oltre il secondo il Raspberry si rallenta da solo
API_ATTESA = "1.14"                      # quello che Mirto sa sfruttare adesso
SEGNALE_DEBOLE = 40                      # %: sotto, un nodo in Wi-Fi va spostato, non impostato meglio


def versione(valore) -> tuple:
    """«1.10» come coppia di numeri, non come numero.

    Il 24/10, passando il nodo da API 1.9 a 1.10, questa riga era a un passo dal rompersi in
    silenzio: `float("1.10")` fa **1,1**, cioè meno di `float("1.9")`. Un nodo vecchio sarebbe
    risultato più nuovo di quello che ci aspettiamo, e l'avviso «versione vecchia» sarebbe sparito
    per tutti senza che nessuna prova lo notasse — il decimo numero di una versione non è un decimo.
    """
    pezzi = []
    for p in str(valore or "").strip().split("."):
        try:
            pezzi.append(int(p))
        except ValueError:
            break
    return tuple(pezzi)


def _n(valore, difetto: float = 0.0) -> float:
    try:
        return float(valore)
    except (TypeError, ValueError):
        return difetto


def gb(byte) -> str:
    return _('{gb:.1f} GB', gb=_n(byte) / 1e9)


def da_quanto(secondi) -> str:
    """«3 giorni», «5 ore», «12 minuti»: quanto è acceso il nodo, come lo direbbe una persona."""
    s = int(_n(secondi))
    if s >= 86400:
        return _('{giorni} giorni', giorni=s // 86400) if s >= 172800 else _('un giorno')
    if s >= 3600:
        return _('{ore} ore', ore=s // 3600) if s >= 7200 else _("un'ora")
    if s >= 60:
        return _('{minuti} minuti', minuti=s // 60)
    return _('{secondi} secondi', secondi=s)


OK, ATTENZIONE, ROTTO, ASSENTE = "ok", "attenzione", "rotto", "assente"

# Il riassunto di un nodo in cinque segni, sempre gli stessi e sempre in quest'ordine: chiuso, un nodo
# è una riga sola, e con cinque nodi uno sopra l'altro i segni si incolonnano e si confrontano a colpo
# d'occhio. Sono simboli a spaziatura fissa e non emoji proprio per questo: le emoji hanno larghezze
# diverse e su Windows un disegno diverso, e la colonna si storcerebbe. (Scelto con Marco il 22/10.)
ICONE = (("camera", "▣"), ("cassa", "♪"), ("sirena", "◈"), ("registra", "⏺"), ("rete", "⌁"))


def _stato_cassa(audio: dict) -> str:
    if not audio:
        return ASSENTE
    if audio.get("uscita") != "bluetooth":
        return OK                                    # jack, USB o HDMI: c'è un filo, non si scollega
    if audio.get("cassa") == "non configurata" or audio.get("configurata") is False:
        return ATTENZIONE                            # manca una scelta, non è rotto niente
    if audio.get("collegata"):
        return OK
    return ROTTO                                     # non abbinata o non collegata: la sirena è muta lì


def _stato_registrazione(rec: dict) -> str:
    if not rec:
        return ASSENTE
    if rec.get("errore"):
        return ROTTO
    if not rec.get("attiva"):
        return ASSENTE                               # spenta per scelta: non è un guaio da segnalare
    return OK if rec.get("registra") else ATTENZIONE


def riassunto_nodo(stato: dict, camera: str = ASSENTE) -> list[dict]:
    """Le cinque voci del riassunto di un nodo: ``[{"cosa", "segno", "stato", "parole"}]``.

    Ci sono sempre tutte e cinque, anche quando una parte non c'è (``assente``): così le righe di nodi
    diversi restano incolonnate, ed è la colonna che si legge, non la singola riga. Lo stato della
    camera arriva da fuori, perché a sapere se quella camera è viva è Mirto, non il nodo.
    """
    stato = dict(stato or {})
    sirena = (ASSENTE if "sirena_disponibile_s" not in stato else
              OK if _n(stato.get("sirena_disponibile_s")) >= 180 else ATTENZIONE)
    rete = stato.get("rete")
    quali = {
        "camera": (camera, {OK: _('camera in linea'), ATTENZIONE: _('camera da sistemare'),
                            ROTTO: _('camera ferma'), ASSENTE: _('nessuna camera')}),
        "cassa": (_stato_cassa(dict(stato.get("audio") or {})),
                  {OK: _('sirena collegata'), ATTENZIONE: _('nessuna cassa scelta'),
                   ROTTO: _('la sirena non può suonare'), ASSENTE: _('nessun audio')}),
        "sirena": (sirena, {OK: _('sirena pronta'), ATTENZIONE: _('sirena quasi esaurita'),
                            ROTTO: _('sirena non disponibile'), ASSENTE: _('nessuna sirena')}),
        "registra": (_stato_registrazione(dict(stato.get("registrazione") or {})),
                     {OK: _('sta registrando'), ATTENZIONE: _('non sta registrando adesso'),
                      ROTTO: _('registrazione ferma'), ASSENTE: _('registrazione spenta')}),
        "rete": ((ASSENTE if not rete_in_parole(rete) else
                  ATTENZIONE if rete_debole(rete) is not None else OK),
                 {OK: rete_in_parole(rete) or _('rete sconosciuta'), ATTENZIONE: _('Wi-Fi debole'),
                  ROTTO: _('rete assente'), ASSENTE: _('rete sconosciuta')}),
    }
    fuori = []
    for cosa, segno in ICONE:
        come, parole = quali[cosa]
        fuori.append({"cosa": cosa, "segno": segno, "stato": come, "parole": parole[come]})
    return fuori


def rete_in_parole(rete) -> str:
    """Come è attaccato il nodo: «cavo», «Wi-Fi «CasaMia» · 68 %», «Wi-Fi · 68 %».

    Stringa vuota quando il nodo non lo dice, e succede: i nodi fino alla 0.7.2 non lo sapevano. In quel
    caso è giusto non scrivere niente, perché un «sconosciuto» in mezzo allo stato di un nodo sano si
    legge come un guasto.
    """
    rete = dict(rete or {})
    tipo = str(rete.get("tipo") or "")
    if tipo == "cavo":
        return _('cavo')
    if tipo != "wifi":
        return ""
    nome = str(rete.get("rete") or "")
    pezzi = [_('Wi-Fi «{nome}»', nome=nome) if nome else _('Wi-Fi')]
    if rete.get("segnale") is not None:
        pezzi.append(_('segnale {per_cento} %', per_cento=int(_n(rete["segnale"]))))
    return " · ".join(pezzi)


def rete_debole(rete) -> int | None:
    """La percentuale, se il nodo è in Wi-Fi e il segnale è troppo basso. Altrimenti ``None``."""
    rete = dict(rete or {})
    if rete.get("tipo") != "wifi" or rete.get("segnale") is None:
        return None
    quanto = int(_n(rete["segnale"]))
    return quanto if quanto < SEGNALE_DEBOLE else None


def diagnosi(info: dict, stato: dict, camera: dict | None = None) -> list[dict]:
    """I problemi di un nodo, i più gravi per primi: ``[{"livello", "testo", "rimedio"}]``.

    Elenco vuoto vuol dire che non c'è niente da fare: è un'informazione, e va detta come tale.
    """
    info, stato, camera = dict(info or {}), dict(stato or {}), dict(camera or {})
    guai: list[dict] = []

    def dillo(livello: str, testo: str, rimedio: str = "") -> None:
        guai.append({"livello": livello, "testo": testo, "rimedio": rimedio})

    # --- camera ---------------------------------------------------------------
    if camera:
        if camera.get("attiva") and not camera.get("vista_dal_sistema"):
            dillo(ERRORE, _('La camera è accesa nella configurazione, ma il sistema non la vede.'),
                  _('Spegni il nodo, stacca e rimetti il cavo piatto da tutte e due le parti (il lato della scheda è quello che si sfila più facilmente), poi riaccendi. Il 20/10 era proprio questo.'))
        elif not camera.get("attiva"):
            dillo(AVVISO, _('La camera del nodo è spenta.'),
                  _('Impostazioni → Nodi → Configurazione → Camera per riaccenderla.'))
    camere = [c for c in (stato.get("camere") or []) if isinstance(c, dict)]
    if camera.get("attiva") and camera.get("vista_dal_sistema") and not camere:
        dillo(ERRORE, _('La camera c\'è, ma il nodo non la sta trasmettendo.'),
              _('Riavvia il nodo; se non basta, sul nodo:  systemctl status mediamtx'))
    for c in camere:
        if not c.get("in_linea", True):
            dillo(ERRORE, _('La camera «{nome}» del nodo non risponde.', nome=str(c.get("nome") or "?")),
                  _('Riavvia il nodo. Se succede ancora, è il cavo piatto o l\'alimentatore.'))

    # --- registrazione --------------------------------------------------------
    rec = dict(stato.get("registrazione") or {})
    if rec:
        disco = str(rec.get("disco") or "")
        if rec.get("errore"):
            dillo(ERRORE, _('La registrazione è ferma: {errore}', errore=str(rec["errore"])),
                  _('Se il disco USB è stato staccato, ricollegalo: riparte da sola.'))
        elif rec.get("attiva") and rec.get("registra") is False:
            dillo(AVVISO, _('In questo momento il nodo non sta registrando.'),
                  _('Succede se la camera è spenta o il disco non è montato: guarda qui sopra.'))
        elif not rec.get("attiva"):
            dillo(AVVISO, _('La registrazione sul nodo è spenta.'),
                  _('Impostazioni → Nodi → Configurazione → Disco per riaccenderla.'))
        if rec.get("attiva") and disco in ("", "microSD"):
            dillo(AVVISO, _('Il nodo registra sulla microSD, con un tetto basso (circa 3 ore).'),
                  _('Collega un SSD o una chiavetta USB e vai in Impostazioni → Nodi → Configurazione → Disco: con un disco tiene giorni, e la microSD si consuma meno.'))

    # --- audio ----------------------------------------------------------------
    audio = dict(stato.get("audio") or {})
    if audio:
        if audio.get("configurata") is False:
            # Un nodo appena nato dall'immagine sta qui: l'uscita di serie è il Bluetooth e di casse non
            # ne conosce nessuna. Non è rotto niente, manca una scelta — e il rimedio è diverso.
            dillo(AVVISO, _('La sirena è impostata sul Bluetooth, ma al nodo non è abbinata nessuna cassa.'),
                  _('Impostazioni → Nodi → Configurazione → Audio: scegli il jack, una scheda USB o l\'HDMI, oppure metti la cassa in abbinamento e premi «Cerca casse…».'))
        elif audio.get("abbinata") is False:
            dillo(ERRORE, _('La cassa Bluetooth non è più abbinata al nodo.'),
                  _('Metti la cassa in abbinamento e rifà Impostazioni → Nodi → Configurazione → Audio → «Cerca casse…». Se non basta, sul nodo:  sudo bluetoothctl remove <indirizzo della cassa>  e riprova.'))
        elif audio.get("collegata") is False:
            dillo(ERRORE, _('La sirena non può suonare dove dovrebbe: {dove} non è collegata.',
                            dove=str(audio.get("nome") or audio.get("cassa") or audio.get("scheda") or "?")),
                  _('Accendi la cassa e tienila a portata; il nodo prova a ricollegarla da solo ogni pochi minuti.'))
        if audio.get("ripiego"):
            dillo(AVVISO, _('La sirena sta suonando da un ripiego ({ripiego}), non dall\'uscita scelta.',
                            ripiego=str(audio["ripiego"])),
                  _('Non si perde l\'allarme, ma si sente meno: sistema l\'uscita in Impostazioni → Nodi → Configurazione → Audio.'))

    # --- chi comanda il nodo --------------------------------------------------
    abb = dict(stato.get("abbinamento") or {})
    if abb.get("chiave_installazione"):
        quando = _n(abb.get("ritiro_installazione_tra_s"))
        dillo(AVVISO, _('C\'è ancora la chiave dell\'installazione: chi la conosce comanda questo nodo.'),
              # Il rimedio sta nel pannello: «Altri computer…» la stacca come qualunque altra chiave.
              # Il comando resta scritto per chi e' gia' davanti al nodo (21/10).
              _('Si ritira da sola fra {quanto}; per farlo subito: Impostazioni → Nodi → Configurazione → Altri computer → Stacca (oppure, sul nodo:  sudo mirto-nodo abbina --revoca installazione)', quanto=da_quanto(quando))
              if quando > 0 else
              _('Impostazioni → Nodi → Configurazione → Altri computer → Stacca (oppure, sul nodo:  sudo mirto-nodo abbina --revoca installazione)'))
    if abb.get("aperto") and abb.get("abbinato"):
        dillo(AVVISO, _('L\'abbinamento di questo nodo è aperto: chi ha il PIN può abbinarsi.'),
              _('Se hai finito di aggiungere computer, chiudilo: Impostazioni → Nodi → Configurazione → Altri computer.'))

    # --- la macchina ----------------------------------------------------------
    temp = _n(stato.get("temperatura_c"), -1)
    if temp >= BOLLENTE:
        dillo(ERRORE, _('Il nodo è a {temp:.0f} °C: a questa temperatura si rallenta da solo e le immagini saltano.', temp=temp),
              _('Mettilo dove l\'aria circola, lontano dal sole; se è in una scatola chiusa, aprila o metti un dissipatore.'))
    elif temp >= CALDO:
        dillo(AVVISO, _('Il nodo è a {temp:.0f} °C: caldo, non ancora un problema.', temp=temp),
              _('Un po\' d\'aria intorno basta.'))
    debole = rete_debole(stato.get("rete"))
    if debole is not None:
        # Questo è il motivo per cui la ricerca delle reti la fa il nodo e non il computer: da dove sta
        # il nodo il segnale è un altro. Il rimedio non è un'impostazione, è un posto diverso.
        dillo(AVVISO, _('Il nodo è in Wi-Fi con un segnale debole ({per_cento} %): le immagini possono saltare e il nodo può sparire da Mirto.', per_cento=debole),
              _('Spostalo più vicino al router, o mettilo sul cavo. Non c\'è un\'impostazione che lo risolva: è il posto che va cambiato.'))
    api = versione(info.get("versione_api"))
    if api and api < versione(API_ATTESA):
        # Il rimedio era un comando da lanciare sul nodo, dal terminale. Dal 25/10 (N-02) il
        # programma del nodo lo manda Mirto, quindi il rimedio è un pulsante — e lasciare scritto
        # il comando vecchio manderebbe ad aprire una sessione ssh per una cosa che si fa in un clic.
        dillo(AVVISO, _('Il nodo ha una versione vecchia (API {api}): alcune cose si possono fare solo dal terminale.', api=str(info.get("versione_api") or "?")),
              _('Impostazioni → Nodi: in cima c\'è «Aggiorna il nodo». Gli abbinamenti e le configurazioni restano.'))
    return guai


def righe_stato(info: dict, stato: dict, camera: dict | None = None) -> list[tuple[str, str]]:
    """Com'è il nodo, in coppie (etichetta, valore) da mostrare in fila. Niente segreti."""
    info, stato, camera = dict(info or {}), dict(stato or {}), dict(camera or {})
    rec = dict(stato.get("registrazione") or {})
    audio = dict(stato.get("audio") or {})
    abb = dict(stato.get("abbinamento") or {})
    camere = [c for c in (stato.get("camere") or []) if isinstance(c, dict)]
    righe = [
        (_('Nome'), str(stato.get("nome") or info.get("nome") or "?")),
        (_('Modello'), str(info.get("modello") or "?")),
        (_('Versione'), _('nodo {versione} · API {api}', versione=str(info.get("versione") or "?"),
                          api=str(info.get("versione_api") or "?"))),
        (_('In rete'), " · ".join([str(stato.get("host") or "?")]
                                  + [str(a) for a in (stato.get("indirizzi") or [])]
                                  + [p for p in [rete_in_parole(stato.get("rete"))] if p])),
        (_('Acceso da'), da_quanto(stato.get("attivo_da_s"))),
    ]
    if _n(stato.get("temperatura_c"), -1) >= 0:
        righe.append((_('Temperatura'), _('{temp:.0f} °C', temp=_n(stato.get("temperatura_c")))))
    if camera:
        righe.append((_('Camera'), _('{sensore} · {stato}',
                                     sensore=str(camera.get("sensore") or _('sensore non visto')),
                                     stato=_('accesa') if camera.get("attiva") else _('spenta'))))
    if camere:
        righe.append((_('Trasmette'), ", ".join(
            _('{nome} ({stato})', nome=str(c.get("nome") or "?"),
              stato=_('in linea') if c.get("in_linea", True) else _('non risponde')) for c in camere)))
    if rec:
        dove = str(rec.get("disco") or "") or _('microSD')
        quanto = (_('{usato} di video · {libero} liberi su {totale}', usato=gb(rec.get("usato")),
                    libero=gb(rec.get("libero")), totale=gb(rec.get("totale")))
                  if rec.get("libero") is not None else _('{usato} di video', usato=gb(rec.get("usato"))))
        righe.append((_('Registrazione'), _('{dove} · {quanto} · {adesso}', dove=dove, quanto=quanto,
                                            adesso=_('registra') if rec.get("registra") else _('non registra'))))
    if audio:
        righe.append((_('Sirena'), _('{uscita} · {come}', uscita=str(audio.get("uscita_in_uso") or audio.get("uscita") or "?"),
                                     come=_('collegata') if audio.get("collegata", True) else _('non collegata'))))
    if abb:
        chiavi = int(_n(abb.get("chiavi")))
        righe.append((_('Abbinato a'), _('{quanti} computer', quanti=chiavi) if chiavi != 1 else _('un computer')))
        if abb.get("da"):
            righe.append((_('Primo abbinamento'), str(abb.get("da"))))
    return righe


def testo_console(righe: list[tuple[str, str]]) -> str:
    """Le righe dello stato incolonnate, per un riquadro a caratteri fissi.

    Incolonnare serve a leggerle: quindici righe «etichetta: valore» di lunghezza diversa sono un
    muro, le stesse quindici con i valori allineati si scorrono con l'occhio. L'allineamento regge
    solo a caratteri di larghezza fissa, ed è il motivo per cui il riquadro è in stile console.
    """
    righe = [(str(e), str(v)) for e, v in righe]
    largo = max((len(e) for e, _v in righe), default=0)
    return "\n".join(f"{e.ljust(largo)}   {v}" for e, v in righe)


def rapporto(info: dict, stato: dict, camera: dict | None = None, quando=None) -> str:
    """Il testo da incollare in un messaggio quando si chiede aiuto. Senza segreti.

    Un tester che scrive «non va» ci costa tre messaggi per capire **cosa** non va. Questo risponde a
    tutti e tre in una volta, e lo può copiare senza sapere cos'è un registro di sistema.
    """
    t = time.strftime("%d/%m/%Y %H:%M", time.localtime(quando if quando is not None else time.time()))
    pezzi = [_('Mirto · stato del nodo · {quando}', quando=t), ""]
    pezzi += [f"{etichetta}: {valore}" for etichetta, valore in righe_stato(info, stato, camera)]
    guai = diagnosi(info, stato, camera)
    pezzi += ["", _('Problemi: {quanti}', quanti=len(guai)) if guai else _('Nessun problema rilevato.')]
    for g in guai:
        pezzi.append(f"- [{g['livello']}] {g['testo']}")
        if g.get("rimedio"):
            pezzi.append(f"  → {g['rimedio']}")
    return "\n".join(pezzi) + "\n"
