# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Le righe della colonna *Sistema*: testo con dentro i colori, composto senza aprire una finestra.

*Sistema* è la prima cosa che si guarda: ci sono le camere, il riconoscimento, la registrazione, i
suoni, le notifiche e i nodi, e ogni riga deve dire in poche parole se quella parte è a posto. Finché
stava dentro ``app``, questo testo si poteva provare **solo** dove è installato PySide6 — cioè in CI e
non qui — e si scriveva al buio. Qui dentro non c'è niente di Qt: sono stringhe, e le stringhe si
provano.

Le righe lunghe stanno dietro un **più**: chiuse si legge il titolo e il riassunto, aperte compare
quello che c'era prima. Chi decide cosa è aperto è la finestra, che se lo ricorda nelle impostazioni;
qui si riceve solo ``aperte`` e si compone di conseguenza.
"""

from __future__ import annotations

from urllib.parse import quote

import html
import re
import time

from ..actions.diagnosi import (ASSENTE, ATTENZIONE, BOLLENTE, CALDO, ICONE, OK, ROTTO,
                                rete_debole, rete_in_parole, riassunto_nodo)
from ..durate import fra_quanto
from ..i18n import N_, _, questo_computer
from . import colori as T

# Da dove sta suonando la sirena quando l'uscita scelta non risponde. La frase la compone Mirto
# dalla chiave dell'uscita (`uscita_in_uso`), non dal testo del nodo: i messaggi del nodo restano in
# italiano, questa riga la legge chi usa il programma e va tradotta.
SUONA_DA = {"jack": N_("suona dal jack 3,5 mm"), "usb": N_("suona dalla scheda USB"),
            "hdmi": N_("suona dall'HDMI"), "predefinito": N_("suona dall'uscita di sistema")}
# Come si chiama un'uscita quando la si **nomina**, non quando si racconta da dove sta suonando.
USCITA_NOME = {"jack": N_("Jack 3,5 mm"), "usb": N_("Scheda USB"), "hdmi": N_("HDMI"),
               "predefinito": N_("Uscita di sistema del Raspberry")}

# Sotto quanti secondi di budget il dissuasore diventa una riga da mostrare. Trenta secondi sono il
# tetto per scatto che impone il nodo: sotto, non c'è più niente da far suonare — e *solo* sotto,
# perché un contatore sempre pieno è una riga che non dice mai niente (la lezione della sirena, 24/10).
DISSUASORE_POCO_S = 30

TELEGRAM_HELP = N_("Comandi da scrivere nel gruppo Telegram:\n\n/stato – stato di camere e sistema\n/foto [camera] – foto istantanea\n/attiva – attiva la protezione (avvisi e azioni)\n/disattiva – disattiva la protezione\n  (funzionano anche /inserisci e /disinserisci)\n/pausa [minuti] – sospende gli avvisi (default 60)\n/riprendi – riattiva gli avvisi\n/ferma – ferma tutto e disinserisce\n/programma – orari automatici (/programma sospendi, /programma riattiva)\n/ascolta [secondi] – audio dal nodo\n/id – ID della chat e dell'utente\n/aiuto – elenco dei comandi\n\nPulsanti sotto ogni foto: Falso allarme, Silenzia 1 ora, Ferma tutto.")

STATE_WORDS = {
    "active": (N_("Protetta"), N_('Avvisi attivi: se entra qualcuno ricevi subito la foto su Telegram e via email.')),
    "paused": (N_('In pausa'), ""),
    "disarmed": (N_('Non protetta'), N_('Nessun avviso. Gli eventi restano solo su questo {computer}.')),
}


def _esc(text) -> str:
    return html.escape(str(text))


def telegram_detail(info: dict, has_chat: bool) -> str:
    """Righe sotto "Telegram collegato": gruppo, bot, utenti autorizzati con nome e cognome."""
    if not info:
        return _('Lettura delle informazioni…')
    lines = []
    kinds = {"group": "gruppo", "supergroup": "supergruppo", "private": _('chat privata'), "channel": "canale"}
    if info.get("chat_title"):
        what = kinds.get(info.get("chat_type", ""), "chat")
        members = _(" · {n} membri", n=info['members']) if info.get("members") else ""
        lines.append(_("Gruppo: <b>{title}</b> ({what}{members})", title=_esc(info['chat_title']), what=what,
                       members=members))
    elif info.get("chat_error"):
        lines.append(_("<span style='color:{alarm}'>Gruppo non trovato: controlla l'ID</span>", alarm=T.ALARM))
    elif not has_chat:
        lines.append(_("<span style='color:{paused}'>Manca l'ID del gruppo (scrivi /id nel gruppo)</span>", paused=T.PAUSED))
    if info.get("bot"):
        lines.append(_('Bot: {esc}', esc=_esc(info['bot'])))
    users = info.get("users") or []
    if not users:
        lines.append(_("<span style='color:{paused}'>Nessun utente autorizzato ai comandi</span>", paused=T.PAUSED))
    else:
        lines.append(_("Autorizzati:"))
        for u in users:
            name = _esc(u.get("name", u["id"]))
            if u.get("unknown"):
                lines.append(_("&nbsp;&nbsp;<span style='color:{paused}'>⚠ {name} – utente sconosciuto, controlla l'ID</span>", paused=T.PAUSED, name=name))
            elif u.get("in_group") is False:
                lines.append(_("&nbsp;&nbsp;<span style='color:{alarm}'>⚠ {name} – non è nel gruppo</span>", alarm=T.ALARM, name=name))
            else:
                user = f" (@{_esc(u['username'])})" if u.get("username") else ""
                lines.append(f"&nbsp;&nbsp;✓ {name}{user}")
    return "<br>".join(lines)


def cameras_detail(cams: list[dict], new_in_network: int = 0, cam_nodes: dict | None = None,
                   aperto: bool = False) -> str:
    """Righe sotto "Camere": lo stato di ogni camera **in uso**, e il conto delle altre.

    ``cam_nodes``: {id camera: nome del nodo} per le camere trasmesse da un nodo Mirto.

    Le camere spente e quelle non più collegate non stanno in mezzo a quelle che funzionano: chiuse
    restano un numero («Spente: 2»), e i nomi compaiono solo se si apre la riga. In una casa con sei
    camere, delle quali due vecchie e una spenta, l'elenco diceva più di quello che si voleva sapere —
    e la cosa importante, quante ne stanno guardando adesso, finiva annegata (chiesto il 22/10).
    """
    lines = []
    cam_nodes = cam_nodes or {}
    used = [c for c in cams if c["enabled"] and c["connected"]]
    for c in used:
        kind_text = c.get("kind", "")
        if c["id"] in cam_nodes:
            kind_text = f"nodo {cam_nodes[c['id']]}"
        kind = _(" <span style='color:{faint}'>({esc})</span>", faint=T.FAINT, esc=_esc(kind_text)) if kind_text else ""
        name = _esc(c["name"])
        st = c.get("status")
        if st == "online":
            lines.append(f"&nbsp;&nbsp;✓ {name}{kind}")
        elif st == "offline":
            lines.append(_("&nbsp;&nbsp;<span style='color:{alarm}'>⚠ {name} – non raggiungibile</span>{kind}", alarm=T.ALARM, name=name, kind=kind))
        else:
            lines.append(_("&nbsp;&nbsp;<span style='color:{paused}'>… {name} – collegamento</span>{kind}", paused=T.PAUSED, name=name, kind=kind))
    off = [c for c in cams if not c["enabled"] and c["connected"]]
    if off:
        lines.append(_('Spente: {len}', len=len(off)))
        if aperto:
            lines += [f"&nbsp;&nbsp;○ {_esc(c['name'])}" for c in off]
    gone = [c for c in cams if not c["connected"]]
    if gone:
        lines.append(_('Non collegate: {len}', len=len(gone)))
        if aperto:
            lines += [_("&nbsp;&nbsp;<span style='color:{faint}'>– {esc}</span>", faint=T.FAINT, esc=_esc(c['name'])) for c in gone]
    if new_in_network:
        lines.append(_("<span style='color:{info}'>Nuove in rete da aggiungere: {new_in_network}</span>", info=T.INFO, new_in_network=new_in_network))
    return "<br>".join(lines)


MODEL_NAMES = {"yolox_nano": N_('YOLOX Nano'), "yolox_s": "YOLOX-S"}


def level_bar(level: float) -> str:
    """Livello del microfono come barretta: ▮▮▮▯▯▯▯▯ (scala logaritmica, da silenzio a forte)."""
    import math  # noqa: PLC0415

    db = 20 * math.log10(max(level, 1e-5))          # -100 … 0 dB
    n = max(0, min(8, int((db + 60) / 60 * 8)))      # -60 dB → 0 tacche, 0 dB → 8
    return "▮" * n + "▯" * (8 - n)


def colonne_griglia(larghezza: int, larghezza_box: int, spazio: int = 8, massimo: int = 4) -> int:
    """Quanti box ci stanno in una riga larga ``larghezza``: almeno uno, al massimo ``massimo``.

    Sta qui, fuori dalle finestre, perché è l'unica parte della griglia che si può provare dove
    l'interfaccia non gira — e perché il conto giusto non è ovvio: fra N box ci sono N-1 spazi, non N.
    """
    if larghezza_box <= 0:
        return 1
    quanti = (int(larghezza) + spazio) // (larghezza_box + spazio)
    return max(1, min(massimo, int(quanti)))


def ai_detail(core) -> str:
    """Righe sotto "AI Recognition": dove gira il modello e quanto deve essere sicuro."""
    providers = []
    try:
        providers = core.detector.session.get_providers()
    except Exception:  # noqa: BLE001
        pass
    if "CoreMLExecutionProvider" in providers:
        accel = _('Apple Neural Engine (CoreML)')
    elif any(p.startswith("Dml") or "DirectML" in p for p in providers):
        accel = "GPU (DirectML)"  # nome del provider: non si traduce
    elif any("CUDA" in p for p in providers):
        accel = "GPU (CUDA)"
    else:
        accel = "CPU"
    thr = float(core.config.get("detection.person_threshold", 0.4))
    size = (getattr(core.detector, "size", None) or ("",))[0]
    from ..detection.labels import display_name  # noqa: PLC0415

    watch = core.config.get("detection.watch") or ["person"]
    names = [display_name(k) for k in watch]
    shown = ", ".join(names[:4]) + (_(" e altre {n}", n=len(names) - 4) if len(names) > 4 else "")
    lines = [_('Sorveglia: {escape}', escape=html.escape(shown)), _('In locale su {accel}', accel=accel)]
    lines.append(_('Soglia persone {thr:.0%}', thr=thr) + (_(' · immagine {size} px', size=size) if size else ""))
    lines.append(species_line(core))
    return "<br>".join(lines)


def species_line(core) -> str:
    """Stato del secondo modello (specie degli uccelli): pronto, in caricamento, spento o in errore."""
    from ..detection.pipeline import needs_species  # noqa: PLC0415

    clf = getattr(core, "species", None)
    state = getattr(core, "_species_state", "") or ""
    if clf is not None:
        kind = getattr(getattr(clf, "backend", None), "kind", "") or "?"
        return _('Specie degli uccelli: pronte ({n} specie, {engine})', n=len(clf.labels), engine=kind)
    if state == "loading":
        return _('Specie degli uccelli: in caricamento…')
    if state.startswith("error"):
        msg = state.split(":", 1)[-1].strip().splitlines()[0][:80]
        return _('Specie degli uccelli: non disponibili ({msg})', msg=html.escape(msg))
    if not core.config.get("detection.bird_species", True):
        return _('Specie degli uccelli: disattivate')
    if not needs_species(core.config):
        return _('Specie degli uccelli: spente (si accendono sorvegliando «Uccello» o con un dissuasore)')
    return _('Specie degli uccelli: si caricano alla prima immagine')


MODE_NAMES = {"off": N_("spenta"), "events": N_("solo eventi"), "continuous": N_("continua")}


def _gb(n: float) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e8 else f"{n / 1e6:.0f} MB"


def recording_summary(st: dict, modes: dict, names: dict | None = None) -> tuple[str, str, str]:
    """Livello, titolo e dettagli della riga "Registrazione" (stato da ``RecordingManager.status``)."""
    recs = st.get("recorders") or []
    lines = []
    for r in recs:
        mode = _(MODE_NAMES.get(modes.get(r["id"], ""), "")) if modes.get(r["id"]) else ""
        how = _("copia") if r.get("mode") == "copy" else (r.get("encoder") or "")
        mark = {"recording": "●", "error": "⚠"}.get(r.get("state"), "○")
        state = {"recording": "", "error": _("ferma"), "waiting": _("senza immagini"),
                 "starting": _("avvio…")}.get(r.get("state"), "")
        lines.append(f"&nbsp;&nbsp;{mark} {html.escape(r['name'])} – " + " · ".join(x for x in (mode, how, state) if x))
        if r.get("error") and r.get("state") == "error":
            lines.append("&nbsp;&nbsp;&nbsp;&nbsp;" + html.escape(r["error"]))
    for cid in st.get("waiting_armed") or []:
        lines.append("&nbsp;&nbsp;○ " + html.escape(str((names or {}).get(cid, cid))) + " – "
                     + _("in pausa: registra solo con la protezione attiva"))
    for cid, mode in modes.items():
        if mode == "off":
            lines.append("&nbsp;&nbsp;○ " + html.escape(str((names or {}).get(cid, cid))) + " – " + _(MODE_NAMES["off"]))
    u = st.get("usage") or {}
    if u.get("total"):
        # Lo spazio si dice sempre, anche a registrazione spenta; «dal …» solo se c'è del video.
        if u.get("oldest"):
            lines.append(_("Spazio: {used} usati, {free} liberi · dal {since}", used=_gb(u.get("used", 0)),
                           free=_gb(u.get("free", 0)),
                           since=time.strftime("%d/%m %H:%M", time.localtime(u["oldest"]))))
        else:
            lines.append(_("Spazio: {free} liberi su {total}", free=_gb(u.get("free", 0)),
                           total=_gb(u.get("total", 0))))
    if st.get("pending_clips"):
        lines.append(_("Clip in preparazione: {n}", n=st["pending_clips"]))
    detail = "<br>".join(lines)
    if not st.get("enabled"):
        return T.DISARMED, _("Registrazione spenta"), detail
    if st.get("ffmpeg") is None:
        return T.PAUSED, _("Registrazione: avvio…"), detail
    if st.get("ffmpeg_error"):
        return T.ALARM, _("Registrazione non disponibile: {err}", err=st["ffmpeg_error"]), detail
    if st.get("paused_disk"):
        return T.ALARM, _("Registrazione sospesa: disco pieno"), detail
    bad = [r for r in recs if r.get("state") == "error"]
    if bad:
        return T.ALARM, _("Registrazione: problema con {camera}", camera=bad[0]["name"]), detail
    rec_n = sum(1 for r in recs if r.get("state") == "recording")
    if rec_n:
        # una camera sola non si dice «1 camere»: in italiano e in inglese sono due frasi diverse,
        # e una riga di stato che sbaglia il plurale e' la prima cosa che si nota (W-01, 19/10)
        titolo = _("Registrazione · 1 camera") if rec_n == 1 else _("Registrazione · {n} camere", n=rec_n)
        return T.ARMED, titolo, detail
    if recs:
        return T.PAUSED, _("Registrazione: avvio…"), detail
    if st.get("waiting_armed"):
        return T.DISARMED, _("Registrazione in pausa (protezione disattivata)"), detail
    return T.DISARMED, _("Registrazione: nessuna camera"), detail


def _duration(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60:02d} s" if s % 60 else f"{s // 60} min"
    if s < 86400:
        return f"{s // 3600} h {s % 3600 // 60} min"
    return f"{s // 86400} g {s % 86400 // 3600} h"


def _short_action_name(name: str) -> str:
    """"Sirena dalla cassa (max 3 minuti) – Ingresso" → "Sirena dalla cassa"."""
    name = name.split(" – ")[0]
    return re.sub(r"\s*\([^)]*\)\s*$", "", name).strip() or name


def _pad(text: str, width: int) -> str:
    """Allinea in colonna (il riquadro Sistema usa un carattere a spaziatura fissa)."""
    return _esc(text) + "&nbsp;" * max(1, width - len(text))


def node_recording_text(rec: dict, now: float | None = None) -> str:
    """Registrazione sul nodo, in una riga: dove, da quanto, spazio libero; in rosso se è ferma."""
    if rec.get("errore"):
        return _("<span style='color:{alarm}'>⚠ {err}</span>", alarm=T.ALARM, err=_esc(rec["errore"]))
    where = _("SSD/USB") if rec.get("disco") not in (None, "", "microSD") else _("microSD")
    parts = [where]
    if rec.get("dal"):
        hours = ((now or time.time()) - float(rec["dal"])) / 3600
        parts.append(_("{h:.0f} h di video", h=hours) if hours >= 2 else _("{m:.0f} min di video", m=hours * 60))
    if rec.get("libero") is not None and rec.get("totale"):
        parts.append(_("{gb} liberi", gb=_gb(float(rec["libero"]))))
    state = "✓" if rec.get("registra") else "○"
    color = T.ARMED if rec.get("registra") else T.PAUSED
    return f"<span style='color:{color}'>{state}</span> " + _esc(" · ".join(parts))


COLORI_STATO = {OK: T.ARMED, ATTENZIONE: T.PAUSED, ROTTO: T.ALARM, ASSENTE: T.FAINT}
PEGGIO = {ASSENTE: 0, OK: 1, ATTENZIONE: 2, ROTTO: 3}   # quale stato vince quando le camere sono più d'una


def piu(chiave: str, aperto: bool) -> str:
    """Il **più** che apre una riga di *Sistema* (e il meno che la richiude).

    È un collegamento dentro il testo, non un pulsante: la riga resta una riga di testo, e la finestra
    sente il clic da ``linkActivated``. Il nome dopo ``sistema:`` è la chiave che la finestra si ricorda.
    """
    return f"<a href='sistema:{chiave}' style='text-decoration:none'>{'−' if aperto else '+'}</a>&nbsp;"


def icone_nodo(stato: dict, camera: str = ASSENTE) -> str:
    """Il riassunto di un nodo in cinque segni colorati, da mettere in fondo alla sua riga."""
    return "&nbsp;".join(f"<span style='color:{COLORI_STATO[v['stato']]}'>{v['segno']}</span>"
                         for v in riassunto_nodo(stato, camera))


def legenda_icone() -> str:
    """Cosa vogliono dire i cinque segni: sta nel suggerimento della riga, non a schermo."""
    return _("▣ camera · ♪ cassa · ◈ sirena · ⏺ registra · ⌁ rete\n"
             "verde a posto · ambra da guardare · rosso da sistemare · grigio non c'è\n"
             "Il più a sinistra apre un nodo e lo richiude.")


def _consiglio_sul_guadagno(picco: float) -> str:
    """Perché la barretta è quasi vuota, quando c'è un motivo da dire. Vuoto se non c'è.

    Marco, 26/10: la barretta stava ferma e vuota, e il microfono funzionava — era il **guadagno** al
    minimo, che su un microfono USB è il valore di fabbrica (misurato il 22/10: una voce all'1 % del
    fondo scala, cioè sotto il primo quadratino). Una barra vuota senza una ragione accanto si legge
    come una funzione rotta, ed è lo stesso difetto del budget dei dissuasori: un numero che non porta
    da nessuna parte.

    Il giudizio si dà sul **picco tenuto**, non sull'ultimo istante: fra una parola e l'altra il
    livello va a zero, e un consiglio che cambiasse lì manderebbe ad alzare un guadagno già giusto.
    Il **silenzio** non si commenta: in una casa tranquilla è lo stato normale, e «fai un rumore» in
    una riga di riassunto è un consiglio fuori posto. Si parla solo quando il segnale c'è e **non va
    bene** — troppo piano, o tagliato.
    """
    from ..actions.pannello_audio import giudizio_livello  # noqa: PLC0415

    esito, _consiglio = giudizio_livello(picco)
    if esito == "piano":
        return _(" <span style='color:{paused}'>· troppo piano: alza il guadagno in "
                 "Impostazioni → Nodi → Configurazione → Audio</span>", paused=T.PAUSED)
    if esito == "saturo":
        return _(" <span style='color:{paused}'>· satura: abbassa il guadagno in "
                 "Impostazioni → Nodi → Configurazione → Audio</span>", paused=T.PAUSED)
    return ""


def riga_volume(nid: str, vol: dict | None) -> str:
    """Il volume della cassa come dieci quadratini **cliccabili**, o il motivo per cui non si può.

    Marco, 25/10: *«nella finestra di sistema servirebbe avere sotto la cassa il livello del volume
    modificabile al volo, sempre usando lo stile della finestra di sistema, tipo quadratini
    cliccabili»*. Dieci quadratini e non un cursore: in una colonna di testo un cursore è un pezzo
    d'altra interfaccia, e per alzare il volume di una cassa in giardino dieci passi bastano.

    Su una cassa Bluetooth il volume non c'è — il nodo manda l'audio al profilo A2DP e la manopola
    sta sulla cassa — e si dice **quello**. Un controllo che si muove senza che il suono cambi è
    peggio di nessun controllo: insegna che la finestra racconta storie.
    """
    if not isinstance(vol, dict):
        return ""          # un nodo prima della 0.12.1 non lo dice
    if not vol.get("si_puo"):
        perche = str(vol.get("perche") or "")
        if perche == "bluetooth":
            return _("<span style='color:{faint}'>si regola sulla cassa (Bluetooth)</span>", faint=T.FAINT)
        if perche == "senza-manopola":
            return _("<span style='color:{faint}'>questa scheda non ha un volume da girare</span>",
                     faint=T.FAINT)
        return ""
    quanti = max(0, min(10, int(round(float(vol.get("percento") or 0) / 10))))
    pezzi = []
    for i in range(1, 11):
        pezzi.append("<a href='sistema:volume:{id}:{p}' style='color:{c}; text-decoration:none'>{q}</a>"
                     .format(id=_esc(str(nid)), p=i * 10, q="▮" if i <= quanti else "▯",
                             c=T.ARMED if i <= quanti else T.FAINT))
    return _("{barra} <span style='color:{faint}'>{per} %</span>", barra="".join(pezzi), faint=T.FAINT,
             per=int(vol.get("percento") or 0))


def _riga_microfono(mic: dict | None, ascoltato: bool, misura: dict | None) -> str:
    """Cosa dire del microfono di un nodo. Vuoto: quel nodo non ne ha, e non c'è niente da scrivere.

    Due fonti, due domande diverse: **se c'è** lo dice il nodo (``microfono`` nello stato, API 1.13),
    **quanto sente** lo sa Mirto, che quel flusso lo sta già analizzando per riconoscere i suoni.
    Tenerle separate serve al caso che conta: un microfono attaccato e funzionante che Mirto non
    ascolta. Finché la riga esisteva solo quando Mirto ascoltava, quel caso non si nominava — e non
    sapere di avere un microfono è il modo più rapido per non usarlo mai.
    """
    if ascoltato:
        if misura is None:
            return _("<span style='color:{paused}'>nessun audio: il flusso non arriva</span>", paused=T.PAUSED)
        barra = f"<span style='color:{T.ARMED}'>{level_bar(float(misura.get('livello') or 0.0))}</span>"
        return barra + _consiglio_sul_guadagno(float(misura.get("picco") or 0.0))
    if not mic:
        return ""      # un nodo prima della 0.12.1 non lo dice: meglio tacere che inventare
    if not mic.get("attivo"):
        return _("<span style='color:{faint}'>spento sul nodo · Impostazioni → Nodi → Configurazione → Audio</span>",
                 faint=T.FAINT)
    if not mic.get("acceso_ora"):
        return _("<span style='color:{paused}'>⚠ acceso sul nodo ma la scheda non risponde</span>",
                 paused=T.PAUSED)
    return _("<span style='color:{paused}'>c'è, ma Mirto non lo ascolta · Impostazioni → Nodi → Configurazione → Audio → Microfono</span>",
             paused=T.PAUSED)


def nodes_detail(nodes: list[dict], node_cams: dict | None = None, cams: list[dict] | None = None,
                 aperti=(), da_aggiornare: dict | None = None, livelli: dict | None = None,
                 armata: bool = False) -> str:
    """Righe sotto "Nodi in linea": un nodo è **una riga**, e si apre solo quello che serve.

    Chiusa, la riga dice il nome, se c'è qualcosa di acceso e i cinque segni del riassunto; aperta,
    rimette tutto quello che c'era prima — azioni, rete, camera, cassa, sirena, registrazione, macchina.
    Con cinque nodi in casa la colonna resta leggibile e i segni si confrontano incolonnati, che era il
    motivo per cui Marco l'ha chiesto (22/10).

    ``aperti``: gli id dei nodi aperti. Chi se li ricorda è la finestra, non questa funzione.

    ``da_aggiornare``: ``{id del nodo: versione nuova}``. Arriva da fuori e non si calcola qui di
    proposito — saperlo vuol dire leggere il codice del nodo dal disco, e questo file è testo e
    basta: è la ragione per cui le sue prove girano ovunque, anche dove Qt non c'è.
    """
    lines = []
    by_id = {c["id"]: c for c in (cams or [])}
    for nd in nodes:
        name = _esc(nd["name"])
        if nd["online"] is False:
            lines.append(_("<span style='color:{alarm}'><b>⚠ {name} – non risponde</b></span>", alarm=T.ALARM, name=name))
            if nd.get("error"):
                lines.append(_("&nbsp;&nbsp;<span style='color:{faint}'>{esc}</span>", faint=T.FAINT, esc=_esc(nd['error'][:70])))
            continue
        if nd["online"] is None:
            lines.append(_("<span style='color:{paused}'>… {name} – controllo in corso</span>", paused=T.PAUSED, name=name))
            continue
        info = nd.get("info") or {}
        active = nd.get("active") or {}
        acts = nd.get("actions") or []
        labels = {a["action"]: _short_action_name(a["name"]) for a in acts}

        dentro = []          # le righe che si vedono solo a nodo aperto
        # dispositivi: uno per riga, lo stato prima di tutto
        width = max([len(labels.get(a["action"], "")) for a in acts] + [6]) + 2
        for a in acts:
            short = labels[a["action"]]
            how = (_("a comando") if a["kind"] == "audio"
                   else _('con conferma') if a["mode"] == "confirm" else _("automatica"))
            # Il nome di un'azione e' un **interruttore**: si clicca e si accende o si spegne.
            # Marco, 25/10: *«servirebbe poter cliccare tipo su disattivata per attivarla»*. Finora
            # per riaccenderne una bisognava aprire Impostazioni, trovare la scheda, trovare la riga:
            # sei gesti per una cosa che si decide guardandola.
            tocca = f"sistema:azione:{quote(str(a['name']))}"
            if not a["enabled"]:
                dentro.append(_("&nbsp;&nbsp;<a href='{dove}' style='color:{faint}; text-decoration:none'>"
                                "– {pad}disattivata</a>", dove=tocca, faint=T.FAINT, pad=_pad(short, width)))
            elif a["action"] in active:
                dentro.append(_("&nbsp;&nbsp;<span style='color:{alarm}'><b>● {pad}ACCESA {duration}</b></span>", alarm=T.ALARM, pad=_pad(short, width), duration=_duration(active[a['action']])))
            else:
                # «spenta», qui, voleva dire «non sta suonando adesso». Ma nella scheda Azioni
                # «attiva» vuol dire «abilitata», e Marco il 25/10 ha letto la riga al contrario:
                # *«da sistema la vedo spenta ma in azioni è attiva»*. Due schermate dello stesso
                # programma non possono usare le stesse parole per cose diverse. «A riposo» non ha
                # un contrario che significhi altro: pronta e ferma, e basta.
                dentro.append(_("&nbsp;&nbsp;<a href='{dove}' style='text-decoration:none'>○</a> {pad}"
                                "<span style='color:{faint}'>a riposo · {how}</span>",
                                dove=tocca, pad=_pad(short, width), faint=T.FAINT, how=how))
        if not acts:
            dentro.append(_("&nbsp;&nbsp;<span style='color:{paused}'>Nessuna azione: Impostazioni → Azioni</span>", paused=T.PAUSED))

        # Com'è attaccato il nodo: se fa i capricci, è la riga che dice da dove guardare. Un Wi-Fi
        # debole si segnala già qui, non solo in diagnosi (chiesto il 22/10).
        rete = rete_in_parole(info.get("rete"))
        if rete:
            debole = rete_debole(info.get("rete"))
            stato_rete_html = (_("<span style='color:{paused}'>{esc} ⚠ debole: spostalo o mettilo sul cavo</span>",
                                 paused=T.PAUSED, esc=_esc(rete)) if debole is not None
                               else _("<span style='color:{faint}'>{esc}</span>", faint=T.FAINT, esc=_esc(rete)))
            dentro.append(_("&nbsp;&nbsp;<span style='color:{faint}'>{pad}</span>{state}", faint=T.FAINT,
                            pad=_pad(_('Rete'), 10), state=stato_rete_html))

        # risorse del nodo: prima la camera, poi la cassa
        stati_cam = []
        for nc in (node_cams or {}).get(nd["id"], []):
            cam = by_id.get(nc["cam_id"]) if nc.get("cam_id") else None
            if not nc.get("in_linea"):
                state = _("<span style='color:{alarm}'>⚠ streaming fermo sul nodo</span>", alarm=T.ALARM)
                stati_cam.append(ROTTO)
            elif cam is None:
                state = (_("{esc} <span style='color:{info}'>da aggiungere (Tutte le cam → Trovate in rete)</span>", esc=_esc(nc['percorso']), info=T.INFO))
                stati_cam.append(ATTENZIONE)
            elif not cam["enabled"]:
                state = _("{esc} <span style='color:{faint}'>spenta in Mirto</span>", esc=_esc(cam['name']), faint=T.FAINT)
                stati_cam.append(ATTENZIONE)
            elif cam.get("status") == "online":
                state = _("{esc} <span style='color:{armed}'>✓ in linea</span>", esc=_esc(cam['name']), armed=T.ARMED)
                stati_cam.append(OK)
            else:
                state = _("{esc} <span style='color:{paused}'>… collegamento</span>", esc=_esc(cam['name']), paused=T.PAUSED)
                stati_cam.append(ATTENZIONE)
            dentro.append(_("&nbsp;&nbsp;<span style='color:{faint}'>{pad}</span>{state}", faint=T.FAINT, pad=_pad('Camera', 10), state=state))
        audio = info.get("audio") or {}
        if audio.get("uscita") == "bluetooth":
            # L'interfaccia parla di «Bluetooth», non di una marca: il modello che il nodo riporta si
            # legge dopo, come dettaglio. Chiesto da Marco il 21/10, ed è giusto: la marca è un dato
            # del dispositivo, non il nome di una funzione del prodotto.
            speaker = audio.get("nome") or ""
            modello = (_(" <span style='color:{faint}'>· {esc}</span>", faint=T.FAINT, esc=_esc(speaker))
                       if speaker else "")
            if audio.get("cassa") == "non configurata":
                state = _("Bluetooth <span style='color:{paused}'>⚠ nessuna cassa scelta</span>",
                          paused=T.PAUSED)
            elif audio.get("collegata"):
                state = _("Bluetooth <span style='color:{armed}'>✓ collegata</span>", armed=T.ARMED) + modello
            elif audio.get("abbinata") is False:
                # Aspettare non serve: l'abbinamento lo rifà una persona, dal pannello.
                state = _("Bluetooth <span style='color:{paused}'>⚠ non più abbinata</span> "
                          "<span style='color:{faint}'>· Impostazioni → Nodi → Configurazione → Audio</span>",
                          paused=T.PAUSED, faint=T.FAINT) + modello
            else:
                state = _("Bluetooth <span style='color:{paused}'>⚠ non collegata</span>",
                          paused=T.PAUSED) + modello
            if audio.get("ripiego"):   # la cassa non risponde, ma la sirena suona da un'altra uscita
                frase = SUONA_DA.get(str(audio.get("uscita_in_uso", "")), "")
                if frase:
                    state += _(" <span style='color:{faint}'>· {frase}</span>", faint=T.FAINT, frase=_(frase))
            dentro.append(_("&nbsp;&nbsp;<span style='color:{faint}'>{pad}</span>{state}", faint=T.FAINT, pad=_pad('Cassa', 10), state=state))
        elif audio:
            # Diceva «uscita del Raspberry» qualunque uscita fosse. Marco, 24/10: *«ho sia la cassa
            # USB che la bluetooth collegate, ma dice uscita del Raspberry»* — ed era vero: il nome
            # vero lo sapevamo (il nodo manda uscita, scheda e nome) e non lo scrivevamo. Una riga
            # che non distingue due casse attaccate non serve a niente.
            quale = str(audio.get("uscita") or "predefinito")
            nome = _(USCITA_NOME.get(quale, quale))
            pezzi = [nome]
            if audio.get("nome") and str(audio["nome"]) != nome:
                pezzi.append(_esc(str(audio["nome"])))
            elif audio.get("scheda") and str(audio["scheda"]) != "predefinita":
                pezzi.append(_esc(str(audio["scheda"])))
            stato_cassa = " · ".join(pezzi)
            if audio.get("collegata") is False:
                stato_cassa += _(" <span style='color:{paused}'>⚠ non collegata</span>", paused=T.PAUSED)
            dentro.append(_("&nbsp;&nbsp;<span style='color:{faint}'>{pad}</span>{state}",
                            faint=T.FAINT, pad=_pad('Cassa', 10), state=stato_cassa))
        # Qui c'era «Sirena · 3 min disponibili (max 3 min/30 min)». Tolta il 24/10: il limite è del
        # nodo e vale comunque, ma in una colonna che si guarda per sapere **com'è messo il nodo**
        # un contatore sempre pieno è una riga che non dice mai niente. Chi esaurisce la sirena lo
        # scopre dove conta — nel messaggio che torna quando non parte.
        # Il volume, **sotto** la cassa: è di quella cassa che si parla, e alzarlo è la cosa che
        # si vuole fare subito dopo aver letto da dove suona.
        vol = riga_volume(str(nd["id"]), info.get("volume") if isinstance(info.get("volume"), dict) else None)
        if vol:
            dentro.append(_("&nbsp;&nbsp;<span style='color:{faint}'>{pad}</span>{state}", faint=T.FAINT,
                            pad=_pad(_('Volume'), 10), state=vol))
        # Il microfono. Il **livello** non lo chiede il nodo: lo sa Mirto, che quel flusso lo sta
        # gia' ascoltando per riconoscere i suoni — quindi e' gratis, e arriva da fuori come tutto
        # il resto che questo file non deve andarsi a prendere. Marco, 25/10: *«il gain non serve
        # averlo, ma il livello di quello che il microfono percepisce sarebbe comodo»*.
        #
        # **Che il microfono ci sia** invece lo dice il nodo, dall'API 1.13: prima quel dato viveva
        # solo nella risposta che si chiede aprendo il pannello dell'audio, e la riga c'era soltanto
        # se Mirto stava ascoltando — un nodo col microfono spento in Mirto non lo nominava, e
        # non sapere di avere un microfono e' il modo piu' rapido per non usarlo mai.
        stato_mic = _riga_microfono((info.get("microfono") if isinstance(info.get("microfono"), dict)
                                     else None), nd["id"] in (livelli or {}),
                                    (livelli or {}).get(nd["id"]))
        if stato_mic:
            dentro.append(_("&nbsp;&nbsp;<span style='color:{faint}'>{pad}</span>{state}", faint=T.FAINT,
                            pad=_pad(_('Microfono'), 10), state=stato_mic))
        # Il budget dei dissuasori **solo quando sta per finire**. Un contatore sempre pieno è una
        # riga che non dice mai niente — è il motivo per cui quella della sirena è stata tolta il
        # 24/10 — ma gli ultimi trenta secondi sono la cosa da sapere prima di chiedersi perché
        # un'azione non è partita. Sotto un scatto intero non c'è più niente da far suonare.
        resta_dis = info.get("dissuasore_disponibile_s")
        if isinstance(resta_dis, (int, float)) and resta_dis < DISSUASORE_POCO_S:
            riprende = fra_quanto(info.get("dissuasore_riprende_fra_s"))
            stato_dis = (_("<span style='color:{paused}'>finito per quest'ora · riprende fra {quando}</span>",
                           paused=T.PAUSED, quando=riprende) if riprende else
                         _("<span style='color:{paused}'>restano {n} s di suono quest'ora</span>",
                           paused=T.PAUSED, n=int(resta_dis)))
            dentro.append(_("&nbsp;&nbsp;<span style='color:{faint}'>{pad}</span>{state}", faint=T.FAINT,
                            pad=_pad(_('Dissuasore'), 10), state=stato_dis))
        rec = info.get("registrazione")
        if isinstance(rec, dict) and rec.get("attiva", True):
            dentro.append(_("&nbsp;&nbsp;<span style='color:{faint}'>{pad}</span>{state}", faint=T.FAINT,
                            pad=_pad(_("Registra"), 10), state=node_recording_text(rec)))
        extra = []
        if "temperatura_c" in info:
            extra.append(f"{info['temperatura_c']:.0f} °C")
        if "attivo_da_s" in info:
            extra.append(_('acceso da {duration}', duration=_duration(info['attivo_da_s'])))
        if info.get("versione"):
            extra.append(f"v{info['versione']}")
        if extra:
            dentro.append(_("&nbsp;&nbsp;<span style='color:{faint}'>{pad}{esc}</span>", faint=T.FAINT, pad=_pad('Nodo', 10), esc=_esc(' · '.join(extra))))

        aperto = nd["id"] in (aperti or ())
        camera = max(stati_cam, key=PEGGIO.get) if stati_cam else ASSENTE
        if active:
            what = ", ".join(f"{labels.get(k, k).upper()} · {_duration(v)}" for k, v in sorted(active.items()))
            testa = _("<b>{name}</b> – <span style='color:{alarm}'><b>● ACCESO: {esc}</b></span>", name=name, alarm=T.ALARM, esc=_esc(what))
        else:
            # «tutto spento» si leggeva come «tutte le azioni sono disabilitate». Marco, 25/10:
            # *«nel nodo Ingresso c'è scritto tutto spento, ma è fuorviante: avevo un'azione
            # attiva»*. Qui si parla di **cosa sta suonando adesso**, non di cosa è abilitato.
            testa = _("<b>{name}</b> – <span style='color:{armed}'>tutto a riposo</span>", name=name, armed=T.ARMED)
        # L'aggiornamento sta nella riga **chiusa**: una cosa da fare che si vede solo aprendo il
        # nodo e' una cosa che non si sa. Ed e' un collegamento, non un avviso: da qui si arriva
        # dove si fa, che era meta' della richiesta di Marco il 25/10.
        # La modalità Prank si vede a riga **chiusa**, accanto al nome, perché sospende una regola di
        # sicurezza: due minuti di suono l'ora non valgono più finché è accesa. Una modalità così non
        # può stare dietro un «+», e il collegamento per spegnerla sta dove si legge che è accesa.
        prank = info.get("prank")
        if isinstance(prank, dict) and prank.get("attiva"):
            quanto = fra_quanto(prank.get("restano_s"))
            # A casa inserita gli scherzi tacciono (Marco, 26/10). Senza dirlo qui, «🎭 ancora 20
            # minuti» accanto a un antifurto attivo lascia aspettare uno scherzo che non arriverà:
            # la finestra è ancora aperta — il nodo è davvero senza budget — ma nessuno la usa.
            testa += _("&nbsp;&nbsp;<span style='color:{paused}'>🎭 Prank{per}{muta}</span> "
                       "<a href='sistema:prank:{id}' style='color:{info_c}; text-decoration:none'>spegni</a>",
                       paused=T.PAUSED, id=_esc(str(nd["id"])), info_c=T.INFO,
                       per=_(' · ancora {quanto}', quanto=quanto) if quanto else "",
                       muta=_(' · muta: casa protetta') if armata else "")
        nuova = (da_aggiornare or {}).get(nd["id"])
        if nuova:
            testa += _("&nbsp;&nbsp;<a href='sistema:aggiorna:{id}' style='color:{info_c}; "
                       "text-decoration:none'>⬆ aggiornalo alla {nuova}</a>",
                       id=_esc(str(nd["id"])), info_c=T.INFO, nuova=_esc(str(nuova)))
        lines.append(piu(f"nodo:{nd['id']}", aperto) + testa + "&nbsp;&nbsp;" + icone_nodo(info, camera))
        if aperto:
            lines += dentro
    return "<br>".join(lines)


def notifications_summary(channels: list[tuple[str, str, str]], armed: bool,
                          aperto: bool = False) -> tuple[str, str, str, bool]:
    """Voce "Notifiche" in Sistema: il titolo, i canali e — solo se aperta — i dettagli di ognuno.

    ``channels``: (stato, testo, dettagli HTML) con stato "ok", "off", "error" o "wait".

    Quello che conta è la riga del canale: *Telegram collegato*, *Email spenta*. Il gruppo, il bot e
    l'elenco di chi è autorizzato sono sei righe che si leggono una volta sola, quando si configura:
    stanno dietro il più (chiesto il 22/10). L'ultimo valore dice se c'è qualcosa da aprire.
    """
    colors = {"ok": T.ARMED, "off": T.MUTED, "error": T.ALARM, "wait": T.PAUSED}
    marks = {"ok": "●", "off": "○", "error": "⚠", "wait": "◌"}
    lines = [_("Gli avvisi partono: la protezione è attiva") if armed else
             _("<span style='color:{c}'>Nessun avviso parte: la protezione è disattivata</span>", c=T.PAUSED)]
    espandibile = False
    for state, text, detail in channels:
        lines.append(f"<span style='color:{colors[state]}'>{marks[state]}</span> <b>{_esc(text)}</b>")
        dettagli = detail.split("<br>") if detail else []
        espandibile = espandibile or bool(dettagli)
        if aperto:
            lines += ["&nbsp;&nbsp;&nbsp;" + line for line in dettagli]
    active = [t.split(" ")[0].rstrip(":") for st, t, _d in channels if st in ("ok", "error", "wait")]
    if any(st == "error" for st, _t, _d in channels):
        level = T.ALARM
        title = _("Notifiche: problema con {what}", what=", ".join(
            t.split(" ")[0].rstrip(":") for st, t, _d in channels if st == "error"))
    elif any(st == "wait" for st, _t, _d in channels):
        level, title = T.PAUSED, _("Notifiche: collegamento…")
    elif active:
        level, title = T.ARMED, _("Notifiche · {channels}", channels=", ".join(active))
    else:
        level, title = T.DISARMED, _("Notifiche: nessun canale")
    return level, title, "<br>".join(lines), espandibile


def email_detail(mail: dict) -> str:
    """Righe sotto "Email pronta": chi spedisce e chi riceverà gli avvisi."""
    if not mail.get("enabled"):
        return ""
    lines = []
    sender = mail.get("sender") or mail.get("username")
    if sender:
        provider = {"gmail": "Gmail"}.get(mail.get("provider", ""), mail.get("provider", ""))
        lines.append(f"Da: {_esc(sender)}" + (f" ({_esc(provider)})" if provider else ""))
    rcpts = [r for r in mail.get("recipients", []) if r]
    if rcpts:
        lines.append(_('Riceveranno gli avvisi ({len}):', len=len(rcpts)))
        lines.extend(f"&nbsp;&nbsp;✓ {_esc(r)}" for r in rcpts)
    else:
        lines.append(_("<span style='color:{paused}'>Nessun destinatario</span>", paused=T.PAUSED))
    return "<br>".join(lines)


# --- la vista di flotta: una riga per nodo, otto colonne da confrontare (N-03) ----------------
#
# Con un nodo basta la riga di *Sistema*; con otto, le domande diventano **confronti** — chi non
# risponde, chi è rimasto indietro, a chi sta finendo il disco — e un elenco di schede da aprire una
# per volta non risponde a nessuna delle tre. Marco il 25/10, scegliendo la forma: una pagina
# «Tutti i nodi» nel riquadro principale, densa, con tutto quello che il nodo dice.
#
# Qui si decide **cosa si legge**; il vestito (la tabella Qt) sta altrove e non sa niente. È la
# ragione per cui questa parte si prova dove Qt non c'è, che è il posto dove il codice si scrive.

COLONNE_FLOTTA = (("nome", N_("Nodo")), ("stato", N_("Stato")), ("versione", N_("Versione")),
                  ("spazio", N_("Spazio")), ("temperatura", N_("Temp.")), ("rete", N_("Rete")),
                  ("avvisi", N_("Avvisi")), ("visto", N_("Ultimo evento")))

MAI_VISTO_ORE = 24.0      # oltre, «non vede niente da…» diventa qualcosa da guardare


def _quanto_fa(secondi: float) -> str:
    """«3 min fa», «5 h fa», «2 g fa»: da quanto è successo, corto perché sta in una colonna."""
    if secondi < 90:
        return _('adesso')
    if secondi < 3600:
        return _('{n} min fa', n=int(secondi // 60))
    if secondi < 86400:
        return _('{n} h fa', n=int(secondi // 3600))
    return _('{n} g fa', n=int(secondi // 86400))


def riga_vista_flotta(quanti: int) -> str:
    """Il collegamento, in cima al dettaglio dei nodi, che apre la vista di flotta.

    Sta nel **dettaglio** e non nel titolo per una ragione pratica: il titolo di una riga di Sistema
    è testo semplice, il dettaglio è l'unica parte che sa già gestire un collegamento. Cambiare il
    titolo per farcelo stare vorrebbe dire interpretare come HTML i nomi di tutte le altre righe.
    """
    if quanti <= 0:
        return ""
    return _("<a href='sistema:flotta' style='color:{info}; text-decoration:none'>"
             "▦ Tutti i {quanti} nodi in una tabella →</a><br>", info=T.INFO, quanti=quanti)


def righe_flotta(nodes: list[dict], node_cams: dict | None = None, ultimi: dict | None = None,
                 da_aggiornare: dict | None = None, adesso: float | None = None,
                 armata: bool = False) -> list[dict]:
    """Una riga per nodo: ``{chiave: (testo, stato)}`` più ``id``, ``nome`` e ``aggiornabile``.

    Lo ``stato`` di ogni cella è uno di ``ok``/``attenzione``/``rotto``/``assente``: il colore lo
    mette chi disegna, qui si dice **quanto è grave**, che è una cosa che si può provare.

    ``ultimi``: ``{id della camera: quando}`` dell'ultimo evento — da Mirto, non dal nodo, perché
    a sapere se quella camera ha visto qualcosa è il computer. È la colonna che giustifica tutta la
    schermata: un nodo che non vede niente da tre giorni **non fa rumore** — è in linea, è verde, e
    non succede niente. Le altre sette colonne dicono se il nodo sta bene; questa dice se sta
    **servendo a qualcosa**.
    """
    from ..actions.diagnosi import diagnosi  # noqa: PLC0415

    adesso = time.time() if adesso is None else adesso
    fuori = []
    for nd in nodes:
        nid = str(nd.get("id"))
        info = dict(nd.get("info") or {})          # quello che dice /stato, letto di continuo
        meta = dict(nd.get("meta") or {})          # la carta d'identità, letta quando il nodo cambia
        riga: dict = {"id": nid, "nome": str(nd.get("name") or nid)}

        attive = dict(nd.get("active") or {})
        # Quello che cambia **come questo nodo si comporterà adesso**, in coda alla cella: la
        # modalità Prank (che sospende il budget) e il budget finito (che rende muto il dissuasore).
        # Due parole, non un contatore: una colonna che dice sempre «120 s» non dice mai niente, ed è
        # il motivo per cui la barra della sirena era stata tolta il 24/10.
        extra_stato, grave_stato = [], False
        prank = dict(info.get("prank") or {})
        if prank.get("attiva"):
            # «🎭 Prank muto» e non solo «🎭 Prank»: a casa inserita gli scherzi non partono, e in una
            # tabella di otto nodi la differenza fra «sta per succedere» e «non succederà» è la ragione
            # per cui si guarda questa colonna.
            extra_stato.append(_('🎭 Prank muto') if armata else _('🎭 Prank'))
            grave_stato = True
        resta_dis = info.get("dissuasore_disponibile_s")
        if isinstance(resta_dis, (int, float)) and resta_dis < DISSUASORE_POCO_S and not prank.get("attiva"):
            extra_stato.append(_('dissuasore muto'))
            grave_stato = True
        coda = ("" if not extra_stato else " · " + " · ".join(extra_stato))
        if nd.get("online") is False:
            riga["stato"] = (_('non risponde'), ROTTO)
        elif nd.get("online") is None:
            riga["stato"] = (_('controllo…'), ASSENTE)
        elif attive:
            riga["stato"] = ((_('{n} accesa', n=len(attive)) if len(attive) == 1
                              else _('{n} accese', n=len(attive))) + coda, ATTENZIONE)
        else:
            # Non «tutto spento»: Marco l'ha letto come «tutte le azioni sono disabilitate» (25/10),
            # e qui si parla di cosa sta suonando adesso. La riga di Sistema è stata corretta allora;
            # questa colonna diceva ancora la stessa cosa con le stesse parole.
            riga["stato"] = (_('a riposo') + coda, ATTENZIONE if grave_stato else OK)

        nuova = (da_aggiornare or {}).get(nid)
        versione = str(info.get("versione") or meta.get("versione") or "")
        riga["versione"] = ((_('{v} → {nuova}', v=versione or "?", nuova=nuova), ATTENZIONE) if nuova
                            else (versione or "?", OK if versione else ASSENTE))
        riga["aggiornabile"] = bool(nuova)

        rec = dict(info.get("registrazione") or {})
        if rec.get("errore"):
            riga["spazio"] = (str(rec["errore"]), ROTTO)
        elif rec.get("libero") is not None and rec.get("totale"):
            quota = float(rec["libero"]) / max(1.0, float(rec["totale"]))
            riga["spazio"] = (_('{gb} liberi', gb=_gb(float(rec["libero"]))),
                              ROTTO if quota < 0.05 else ATTENZIONE if quota < 0.15 else OK)
        else:
            riga["spazio"] = ("—", ASSENTE)

        temp = info.get("temperatura_c")
        if temp is None:
            riga["temperatura"] = ("—", ASSENTE)
        else:
            temp = float(temp)
            riga["temperatura"] = (_('{t:.0f} °C', t=temp),
                                   ROTTO if temp >= BOLLENTE else ATTENZIONE if temp >= CALDO else OK)

        rete = rete_in_parole(info.get("rete"))
        debole = rete_debole(info.get("rete"))
        riga["rete"] = ((rete, ATTENZIONE if debole is not None else OK) if rete else ("—", ASSENTE))

        guai = diagnosi(meta, info) if nd.get("online") else []
        if not guai:
            riga["avvisi"] = (_('nessuno'), OK if nd.get("online") else ASSENTE)
        else:
            grave = ROTTO if any(g["livello"] == "errore" for g in guai) else ATTENZIONE
            riga["avvisi"] = (_('{n} da guardare', n=len(guai)), grave)
        # **Quali** sono e **come** si risolvono viaggiano con la riga, non si buttano via.
        # Marco, 25/10, guardando la tabella per la prima volta: *«mostra avvisi che poi non so cosa
        # siano e come risolvere»*. Aveva ragione: la diagnosi ha per ogni problema il testo e il
        # rimedio, e io li avevo ridotti a un numero. Un avviso che non si puo' ne' leggere ne'
        # risolvere e' peggio che non mostrarlo — dice che c'e' qualcosa e lascia soli.
        riga["avvisi_dettaglio"] = [
            " — ".join(x for x in (str(g.get("testo") or ""), str(g.get("rimedio") or "")) if x)
            for g in guai]

        viste = [float((ultimi or {}).get(str(c.get("cam_id")), 0) or 0)
                 for c in (node_cams or {}).get(nid, []) if c.get("cam_id")]
        quando = max(viste) if viste else 0.0
        if not (node_cams or {}).get(nid):
            riga["visto"] = (_('nessuna camera'), ASSENTE)
        elif not quando:
            riga["visto"] = (_('mai'), ATTENZIONE)
        else:
            passate = max(0.0, adesso - quando)
            riga["visto"] = (_quanto_fa(passate), ATTENZIONE if passate > MAI_VISTO_ORE * 3600 else OK)
        fuori.append(riga)
    return fuori
