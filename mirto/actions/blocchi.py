# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""I blocchi di un'azione: quello che ascolta, quello che fa, e quello che controlla.

Fino al 23/10 un'azione aveva due metà scritte in posti diversi: *cosa la fa scattare* stava in campi
suoi (obiettivi, camere, confidenza) e *cosa fa* stava nella catena dei passi. Marco ha guardato il
wizard e ha detto la frase che ha rimesso tutto in ordine: **ogni blocco definisce i controlli per
sé, così resta atomico**.

Da qui in poi c'è un tipo solo di mattone. Un blocco «camera» è sempre lo stesso blocco — camera,
soggetto, confidenza — ed è **la posizione a dargli il significato**:

* in **testa** è un trigger: quando riconosce qualcosa, l'azione parte;
* in **mezzo** è un controllo: guarda per un po', e se non vede niente la catena finisce lì.

Una cosa sola da imparare invece di due, e il caso che ha fatto nascere tutto questo — *una persona
scavalca, le si dice di allontanarsi, e se dopo dieci secondi è ancora lì parte la sirena* — si
disegna in riga senza inventare niente.

Qui dentro non c'è nessuna regola su **chi** può essere il bersaglio di un suono. C'era, fino a ieri,
ed era sbagliata: avvisare una persona prima di farle sentire la sirena è la cosa più civile che
questo programma possa fare. Quello che resta sono i limiti che non dipendono da chi configura — la
sirena tre minuti ogni mezz'ora, il dissuasore trenta secondi per volta — e stanno dove stavano, sul
comando e sul nodo.

Resta **una** regola di buon senso, e non parla di tipi di azione: se nell'inquadratura c'è una
persona e questo blocco le persone non le ha chieste, non scatta. Serve al dissuasore per i gatti che
non deve partire in faccia a chi è in giardino col gatto — e vale per qualunque blocco, senza che
nessuno debba sapere cos'è un «tipo dissuasore».
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..i18n import _
from .passi import MAX_PASSI, MAX_PAUSA, MAX_RIPETI, Passo, passi_di

# I mattoni. «camera» e «microfono» ascoltano; gli altri fanno.
CAMERA, MICROFONO = "camera", "microfono"
COMANDO, TELEGRAM, EMAIL, CONFERMA = "comando", "telegram", "email", "conferma"
ASCOLTO = (CAMERA, MICROFONO)
TIPI = (CAMERA, MICROFONO, COMANDO, TELEGRAM, EMAIL, CONFERMA)
# Quanto guarda un blocco di ascolto messo **in mezzo** a una catena, se nessuno lo dice. Non può
# guardare per sempre: guarda per un tempo e poi decide. Venti secondi coprono il caso vero (uno che
# scavalca e resta lì) senza tenere un'azione appesa a un evento che non arriverà.
GUARDA_PROPOSTO = 20.0
MAX_GUARDA = 300.0
# Un suono «sound:...» arriva da un microfono, tutto il resto da una camera: la sorgente la porta
# l'obiettivo, e non serve chiederla a parte (è così dal 22/10, qui si riusa e basta).
PREFISSO_SUONO = "sound:"

# --- dove si fa una cosa ------------------------------------------------------
# Il dispositivo di un blocco, scritto **col prefisso** (scelta di Marco, 23/10 sera). Costa uguale a
# scriverlo nudo e ci risparmia di riconvertire tutte le configurazioni il giorno che arrivano le cam
# esterne o una presa comandata: un identificativo nuovo è un prefisso nuovo, non una migrazione.
NODO, COMPUTER = "nodo", "computer"
PREFISSI = (NODO, COMPUTER)
# «Questo computer» è un dispositivo da subito, non un caso a parte: la camera e il microfono del Mac
# si scelgono in un blocco come tutto il resto, e la scheda Generale deve solo abilitarli. Farne
# un'eccezione vorrebbe dire rifare lo stesso lavoro quando arriveranno le camere esterne.
QUESTO_COMPUTER = "computer:questo"


def dispositivo_di(valore: str) -> tuple[str, str]:
    """``"nodo:ingresso"`` → ``("nodo", "ingresso")``. Sconosciuto o vuoto: due stringhe vuote.

    Un identificativo **senza** prefisso è un nodo: è come sono scritte tutte le configurazioni fino a
    oggi, e devono continuare a leggersi per sempre. Un prefisso che non conosciamo invece non si
    indovina — meglio «non so dove» che mandare un comando al posto sbagliato.
    """
    valore = str(valore or "").strip()
    if not valore:
        return "", ""
    if ":" not in valore:
        return NODO, valore
    tipo, _sep, resto = valore.partition(":")
    tipo, resto = tipo.strip().lower(), resto.strip()
    if tipo not in PREFISSI or not resto:
        return "", ""
    return tipo, resto


@dataclass
class Blocco:
    """Un mattone dell'azione. I campi che non c'entrano col suo tipo restano vuoti.

    Un dataclass largo invece di sei classi diverse: la configurazione resta una riga leggibile a
    occhio, la conversione da e verso il disco è una cosa sola, e chi legge il file capisce cosa ha
    davanti senza sapere niente di gerarchie.
    """

    tipo: str
    # --- ascolto (camera, microfono) ---
    fonti: list = field(default_factory=list)      # nomi di camere o microfoni; vuoto = tutte
    soggetti: list = field(default_factory=list)   # "person", "cat", "sound:glass"…
    confidenza: float = 0.0
    guarda: float = 0.0                            # secondi, solo quando sta **in mezzo**
    # --- comando del nodo ---
    comando: str = ""
    suono: str = ""
    durata: float = 0.0
    ripeti: int = 1
    # --- messaggi ---
    testo: str = ""
    con_foto: bool = True
    destinatari: list = field(default_factory=list)
    # --- comune ---
    pausa: float = 0.0                             # l'attesa dopo: nel disegno è la freccia
    # **Dove**: ``nodo:<id>``, ``computer:questo``. Sta sul blocco e non sull'azione perché un'azione
    # può ascoltare la camera del computer, aspettare, far suonare la sirena di un nodo e mandare
    # un'email: non ha un dispositivo solo. Vuoto = quello dell'azione, finché l'azione ne ha uno.
    dispositivo: str = ""

    @property
    def ascolta(self) -> bool:
        return self.tipo in ASCOLTO

    @classmethod
    def da_dict(cls, d: dict) -> "Blocco":
        d = dict(d or {})
        tipo = str(d.get("tipo") or "").strip().lower()
        return cls(
            tipo=tipo if tipo in TIPI else COMANDO,
            fonti=[str(x) for x in (d.get("fonti") or []) if str(x).strip()],
            soggetti=[str(x) for x in (d.get("soggetti") or []) if str(x).strip()],
            confidenza=max(0.0, min(1.0, float(d.get("confidenza") or 0))),
            guarda=max(0.0, min(MAX_GUARDA, float(d.get("guarda") or 0))),
            comando=str(d.get("comando") or "").strip(),
            suono=str(d.get("suono") or "").strip(),
            durata=max(0.0, float(d.get("durata") or 0)),
            ripeti=max(1, min(MAX_RIPETI, int(d.get("ripeti") or 1))),
            testo=str(d.get("testo") or ""),
            con_foto=bool(d.get("con_foto", True)),
            destinatari=[str(x) for x in (d.get("destinatari") or []) if str(x).strip()],
            pausa=max(0.0, min(MAX_PAUSA, float(d.get("pausa") or 0))),
            dispositivo=str(d.get("dispositivo") or "").strip(),
        )

    def come_dict(self) -> dict:
        """Solo quello che è stato scelto davvero: un blocco semplice resta una riga corta."""
        fuori: dict = {"tipo": self.tipo}
        for chiave, valore, vuoto in (
                ("fonti", self.fonti, []), ("soggetti", self.soggetti, []),
                ("confidenza", self.confidenza, 0.0), ("guarda", self.guarda, 0.0),
                ("comando", self.comando, ""), ("suono", self.suono, ""),
                ("durata", self.durata, 0.0), ("ripeti", self.ripeti, 1),
                ("testo", self.testo, ""), ("con_foto", self.con_foto, True),
                ("destinatari", self.destinatari, []), ("pausa", self.pausa, 0.0),
                ("dispositivo", self.dispositivo, "")):
            if valore != vuoto:
                fuori[chiave] = valore
        return fuori

    def come_passo(self) -> Passo:
        """Il blocco comando, come lo vede chi esegue la catena (vedi ``passi.py``)."""
        return Passo(comando=self.comando, suono=self.suono, durata=self.durata,
                     ripeti=self.ripeti, pausa=self.pausa, dispositivo=self.dispositivo)


def _sorgente_di(soggetto: str) -> str:
    return MICROFONO if str(soggetto).startswith(PREFISSO_SUONO) else CAMERA


def blocchi_di(d: dict) -> list[Blocco]:
    """I blocchi di un'azione, da come è scritta in configurazione.

    Un'azione scritta prima del 23/10 non ha blocchi: si convertono **leggendo**, come già succede
    per i passi. Il trigger diventa uno o due blocchi di ascolto in testa (uno per la camera, uno per
    il microfono, se gli obiettivi sono di tutt'e due i tipi) e il resto diventa blocchi comando.

    Niente da convertire sul disco, e una configurazione vecchia resta valida per sempre: è la stessa
    promessa fatta il 22/10 per la catena, e vale ancora perché è lo stesso meccanismo.
    """
    d = dict(d or {})
    scritti = [b for b in (d.get("blocchi") or []) if isinstance(b, dict)]
    if scritti:
        blocchi = [Blocco.da_dict(b) for b in scritti[:MAX_PASSI + 4]]
        return [b for b in blocchi if b.ascolta or b.comando or b.tipo in (TELEGRAM, EMAIL, CONFERMA)]
    fuori: list[Blocco] = []
    # Il nodo dell'azione diventa il dispositivo di ogni blocco: è la conversione in lettura, la
    # stessa mossa già fatta due volte (i passi il 22/10, i blocchi il 23/10). Sul disco non cambia
    # niente finché nessuno salva, e una configurazione vecchia resta valida per sempre.
    suo = ("nodo:" + str(d.get("node")).strip()) if str(d.get("node") or "").strip() else ""
    obiettivi = [str(t) for t in (d.get("targets") or []) if str(t).strip()]
    fonti = [str(c) for c in (d.get("cameras") or []) if str(c).strip()]
    confidenza = float(d.get("min_confidence") or 0)
    # Le fonti stanno tutte nello stesso campo — camere e microfoni insieme — da quando un evento
    # audio porta il nome del microfono dove un evento video porta quello della camera. Qui si
    # ricopiano in tutt'e due i blocchi: è quello che fa oggi il filtro, e la conversione non deve
    # cambiare il comportamento nemmeno di un caso.
    #
    # Dal 24/10 la soglia è per obiettivo (``confidenze``), e un blocco ne porta una sola: gli
    # obiettivi con la stessa soglia stanno insieme, le soglie diverse sono blocchi diversi. Il
    # motore non cambia — in testa basta che scatti **uno** dei blocchi. Senza ``confidenze`` esce
    # esattamente un blocco per sorgente, come prima.
    from .confidenze import gruppi  # noqa: PLC0415

    confidenze = {str(k): v for k, v in (d.get("confidenze") or {}).items()}
    for tipo in (CAMERA, MICROFONO):
        suoi = [t for t in obiettivi if _sorgente_di(t) == tipo]
        for quanto, insieme in gruppi(suoi, confidenze, confidenza):
            fuori.append(Blocco(tipo=tipo, soggetti=insieme, fonti=list(fonti), confidenza=quanto,
                                dispositivo=suo))
    fuori.extend(Blocco(tipo=COMANDO, comando=p.comando, suono=p.suono, durata=p.durata,
                        ripeti=p.ripeti, pausa=p.pausa, dispositivo=p.dispositivo or suo)
                 for p in passi_di(d))
    return fuori


def dispositivi_di(d: dict) -> list[str]:
    """I dispositivi che quest'azione tocca, in ordine di catena. Il primo è «il posto solito».

    Serve a tutti quelli che prima chiedevano «di che nodo è questa azione?», una domanda che dal
    23/10 non ha più una risposta sola: un'azione può accendere una luce all'ingresso e far suonare il
    garage. Chi elenca le azioni di un nodo deve mostrare quelle che lo **toccano**, non quelle che
    gli appartengono — altrimenti un'azione con due stanze dentro sparisce da tutt'e due le pagine.
    """
    fuori: list[str] = []
    for b in blocchi_di(d):
        if b.dispositivo and b.dispositivo not in fuori:
            fuori.append(b.dispositivo)
    return fuori


def teste(blocchi: list[Blocco]) -> list[Blocco]:
    """I blocchi di ascolto in testa: quelli che fanno partire l'azione.

    Sono **in OR**: scatta se uno qualsiasi riconosce. Serve a tenere quello che oggi si chiama
    «sorgente: tutti e due» — la camera dell'ingresso *o* il microfono che sente il vetro rotto — e a
    essere elastici senza inventare una seconda idea di trigger.
    """
    fuori = []
    for b in blocchi:
        if not b.ascolta:
            break
        fuori.append(b)
    return fuori


def corpo(blocchi: list[Blocco]) -> list[Blocco]:
    """Quello che viene dopo la testa: le cose da fare, e i controlli in mezzo."""
    return blocchi[len(teste(blocchi)):]


def obiettivi_di(ev) -> set:
    """Cosa ha riconosciuto l'evento, come insieme di obiettivi."""
    return set(ev.targets()) if hasattr(ev, "targets") else {getattr(ev, "label", "person")}


def blocco_scatta(b: Blocco, ev) -> bool:
    """Questo blocco di ascolto riconosce questo evento?

    L'ordine dei controlli è quello che costa meno per primo, ma la regola che conta è l'ultima: se
    nell'inquadratura c'è una **persona** e questo blocco le persone non le ha chieste, non scatta.
    Prima era appesa al «tipo dissuasore» e diceva «i dissuasori non scattano per le persone»; adesso
    che un suono si può puntare su chi si vuole, la stessa prudenza si dice meglio così — vale per
    ogni blocco, e nessuno deve sapere cos'è un tipo di azione per capirla.
    """
    if not b.ascolta or not b.soggetti:
        return False
    visti = obiettivi_di(ev)
    if not (visti & set(b.soggetti)):
        return False
    if b.fonti and getattr(ev, "camera_id", None) not in b.fonti \
            and getattr(ev, "camera_name", None) not in b.fonti:
        return False
    if "person" in visti and "person" not in b.soggetti:
        return False
    return float(getattr(ev, "score", 0)) >= float(b.confidenza or 0)


def scatta(blocchi: list[Blocco], ev) -> bool:
    """L'azione parte per questo evento? Basta che lo riconosca **uno** dei blocchi in testa."""
    return any(blocco_scatta(b, ev) for b in teste(blocchi))


def passi_da(blocchi: list[Blocco]) -> list[Passo]:
    """I passi eseguibili del corpo: per ora i comandi del nodo.

    I blocchi di controllo (una camera in mezzo alla catena), Telegram ed email non sono passi: li
    eseguirà il motore quando sapranno farlo (tappe 2 e 3). Finché non sanno, vengono **saltati** e
    non fermano la catena: meglio un'azione che fa un pezzo in meno di una che non parte.
    """
    return [b.come_passo() for b in corpo(blocchi) if b.tipo == COMANDO and b.comando]


def problemi(blocchi: list[Blocco]) -> list[str]:
    """Cosa non va. Elenco vuoto = si può salvare."""
    guai = []
    if not blocchi:
        guai.append(_("L'azione è vuota: aggiungi almeno un blocco."))
        return guai
    if not teste(blocchi):
        guai.append(_("Manca il primo blocco: cosa deve far partire questa azione?"))
    for b in teste(blocchi):
        if not b.soggetti:
            guai.append(_("Il primo blocco non dice cosa deve riconoscere."))
    if not corpo(blocchi):
        guai.append(_("L'azione riconosce qualcosa ma non fa niente: aggiungi cosa deve succedere."))
    for b in corpo(blocchi):
        if b.tipo == COMANDO and not b.comando:
            guai.append(_("Un blocco non dice cosa fare."))
        if b.ascolta and not b.soggetti:
            guai.append(_("Un controllo non dice cosa deve vedere."))
    return guai
