# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Audio del nodo: suono di sirena e riproduzione sulla cassa (Bluetooth o uscita cuffie/USB).

Usa solo programmi già presenti sul Raspberry: ``aplay`` (alsa-utils), ``bluetoothctl`` e, per le
casse Bluetooth, il servizio ``bluealsa`` (pacchetto bluez-alsa-utils).
"""

from __future__ import annotations

import array
import logging
import math
import re
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

RATE = 22050


def siren_pcm(seconds: float = 4.0, volume: int = 80, rate: int = RATE) -> bytes:
    """Un giro di sirena "a ululato": tono che sale e scende (600 → 1300 → 600 Hz in 2 s).

    Il tono è leggermente "saturato" (forma quasi quadra) per sembrare più forte a parità di volume.
    """
    amp = 32767 * 0.92 * max(0, min(100, volume)) / 100
    out = array.array("h")
    phase = 0.0
    for i in range(int(seconds * rate)):
        t = i / rate
        freq = 600 + 700 * (0.5 - 0.5 * math.cos(2 * math.pi * t / 2.0))
        phase += 2 * math.pi * freq / rate
        out.append(int(amp * math.tanh(2.2 * math.sin(phase)) / math.tanh(2.2)))
    return out.tobytes()


def quiet_pcm(seconds: float = 1.0, rate: int = RATE) -> bytes:
    """Suono a 30 Hz quasi impercettibile: tiene sveglie le casse che si spengono nel silenzio."""
    out = array.array("h", (int(120 * math.sin(2 * math.pi * 30 * i / rate)) for i in range(int(seconds * rate))))
    return out.tobytes()


def chime_pcm(rate: int = RATE) -> bytes:
    """Due note gentili (din-don) per provare l'uscita audio senza far suonare la sirena."""
    out = array.array("h")
    for freq, dur in ((880, 0.35), (660, 0.55)):
        n = int(dur * rate)
        for i in range(n):
            env = min(1.0, i / (0.01 * rate)) * (1 - i / n) ** 1.5
            out.append(int(9000 * env * math.sin(2 * math.pi * freq * i / rate)))
    return out.tobytes()


# --- suoni dei dissuasori ----------------------------------------------------
# Generati dal nodo stesso (nessun file da scaricare, nessun problema di licenza). Ogni suono è una
# sequenza completa di pochi secondi; si possono aggiungere file WAV propri in ``cartella_suoni``.

def _noise(seed: int = 1):
    """Rumore bianco deterministico (-1..1), senza dipendenze."""
    x = seed or 1
    while True:
        x ^= (x << 13) & 0xFFFFFFFF
        x ^= x >> 17
        x ^= (x << 5) & 0xFFFFFFFF
        yield (x / 0x7FFFFFFF) - 1.0


def _render(samples: list[float], volume: int) -> bytes:
    peak = max(1e-6, max(abs(v) for v in samples))
    amp = 32767 * 0.95 * max(0, min(100, volume)) / 100 / peak
    return array.array("h", (int(v * amp) for v in samples)).tobytes()


def bang_pcm(shots: int = 2, gap: float = 1.3, volume: int = 90, rate: int = RATE, deep: bool = False) -> bytes:
    """Colpi secchi (sparo) o, con ``deep``, un unico scoppio basso e lungo (cannoncino a gas)."""
    total = shots * gap + (1.8 if deep else 1.0)
    out = [0.0] * int(total * rate)
    noise = _noise(7)
    low = 0.0
    for k in range(shots):
        start = int(k * gap * rate)
        tail = 1.6 if deep else 0.8
        n_tail = int(tail * rate)
        for i in range(n_tail):
            t = i / rate
            fade = min(1.0, (n_tail - i) / (0.2 * rate))  # coda che si spegne piano: niente "clic" finale
            n = next(noise)
            low += (n - low) * (0.05 if deep else 0.15)          # rumore filtrato: il "corpo" dello scoppio
            crack = n * math.exp(-t / (0.02 if not deep else 0.05))
            body = low * 3.0 * math.exp(-t / (0.18 if deep else 0.09))
            thump = math.sin(2 * math.pi * (55 if deep else 80) * t) * math.exp(-t / (0.25 if deep else 0.12))
            echo = n * 0.12 * math.exp(-t / 0.5) * (1 if t > 0.05 else 0)
            if start + i < len(out):
                out[start + i] += (crack + body + 1.4 * thump + echo) * fade
    return _render(out, volume)


def raptor_pcm(calls: int = 3, volume: int = 90, rate: int = RATE) -> bytes:
    """Grido di rapace "kiii-aah" (tipo poiana/falco): fischio discendente, rauco, ripetuto."""
    call, pause = 0.9, 0.45
    out = []
    noise = _noise(3)
    for _c in range(calls):
        phase = 0.0
        for i in range(int(call * rate)):
            t = i / call / rate                       # 0..1 lungo il grido
            f = 2900 - 900 * t ** 1.5                 # scende da 2,9 a 2 kHz
            phase += 2 * math.pi * f / rate
            env = min(1.0, i / (0.04 * rate)) * (1 - t) ** 0.6
            rasp = 0.75 + 0.25 * math.sin(2 * math.pi * 70 * i / rate) + 0.15 * next(noise)
            tone = math.sin(phase) + 0.45 * math.sin(2 * phase) + 0.2 * math.sin(3 * phase)
            out.append(env * rasp * tone)
        out.extend([0.0] * int(pause * rate))
    return _render(out, volume)


def falcon_pcm(volume: int = 90, rate: int = RATE) -> bytes:
    """Allarme di falco "kek-kek-kek": note brevi e acute in rapida successione."""
    out = []
    for _n in range(10):
        for i in range(int(0.07 * rate)):
            t = i / rate
            env = math.sin(math.pi * i / (0.07 * rate))
            f = 2600 - 300 * t / 0.07
            out.append(env * (math.sin(2 * math.pi * f * t) + 0.3 * math.sin(4 * math.pi * f * t)))
        out.extend([0.0] * int(0.075 * rate))
    out.extend([0.0] * int(0.4 * rate))
    return _render(out * 2, volume)


def crow_alarm_pcm(volume: int = 90, rate: int = RATE) -> bytes:
    """Gracchio d'allarme di corvide "kraa-kraa-kraa": suono roco e nasale, ripetuto."""
    out = []
    noise = _noise(11)
    for _c in range(4):
        n = int(0.32 * rate)
        phase = 0.0
        for i in range(n):
            t = i / n
            f = 520 - 120 * t
            phase += 2 * math.pi * f / rate
            saw = 2 * ((phase / (2 * math.pi)) % 1.0) - 1          # dente di sega: timbro roco
            env = min(1.0, i / (0.02 * rate)) * (1 - t) ** 0.4
            out.append(env * (0.8 * saw + 0.35 * next(noise)) * (0.8 + 0.2 * math.sin(2 * math.pi * 35 * i / rate)))
        out.extend([0.0] * int(0.22 * rate))
    return _render(out, volume)


BUILTIN_SOUNDS = {
    "rapace": raptor_pcm,
    "falco": falcon_pcm,
    "sparo": bang_pcm,
    "cannoncino": lambda volume=90, rate=RATE: bang_pcm(1, 1.0, volume, rate, deep=True),
    "allarme_corvi": crow_alarm_pcm,
}


def _render_forte(samples: list[float], volume: int, rms_obiettivo: float = 0.70) -> bytes:
    """Come ``_render``, ma pareggiando il livello **percepito** invece del picco.

    ``_render`` divide per il picco: due suoni con lo stesso picco escono con lo stesso numero, non
    con lo stesso volume. Il Matrix, che è fatto di colpi di basso, aveva lo stesso picco dell'UFO e
    metà dell'energia — Marco, 23/10: *«matrix è di volume molto più basso delle 3»*.

    Qui si normalizza sull'energia media (RMS) e si lasciano sporgere i picchi dentro un limitatore
    morbido (``tanh``): invece di tagliare, li arrotonda, che su una cassetta da esterno si sente come
    un filo di grinta e non come distorsione.
    """
    n = max(1, len(samples))
    rms = math.sqrt(sum(v * v for v in samples) / n) or 1e-6
    guadagno = rms_obiettivo / rms
    amp = 32767 * 0.95 * max(0, min(100, volume)) / 100
    return array.array("h", (int(amp * math.tanh(v * guadagno)) for v in samples)).tobytes()


# --- audio generato ----------------------------------------------------------
# Suoni che il nodo **compone**, e che non sono dissuasori: servono a farsi sentire senza essere un
# allarme. Nati il 23/10 da un'idea di Marco, mentre si correggeva il difetto della sirena: la sirena
# era l'unico comando che suonava *senza avere suoni* — un ululato generato — ed era proprio da lì che
# nasceva il difetto. Trasformarla in «un comando con dei suoni» toglie il caso speciale invece di
# tapparlo, e la strada scelta (A, decisa da Marco) tiene la sirena un comando a sé con il suo limite
# di legge: questi stanno accanto, con il limite dei dissuasori.
#
# Durano quanto durano — il nodo lo dichiara — quindi nell'interfaccia la domanda è «quante volte»,
# non «per quanto». La sirena resta l'unica a cui si chiedono i secondi, perché è l'unica fatta per
# restare accesa.

def ufo_pcm(volume: int = 90, rate: int = RATE) -> bytes:
    """Disco volante: due toni quasi uguali che scivolano su e giù e battono fra loro."""
    secondi = 3.5
    out, ph1, ph2 = [], 0.0, 0.0
    for i in range(int(secondi * rate)):
        t = i / rate
        base = 520 + 300 * math.sin(2 * math.pi * 0.45 * t) + 40 * math.sin(2 * math.pi * 6.5 * t)
        ph1 += 2 * math.pi * base / rate
        ph2 += 2 * math.pi * base * 1.012 / rate     # stonato apposta: i battimenti fanno il «disco»
        env = min(1.0, t / 0.25) * min(1.0, (secondi - t) / 0.4)
        out.append(env * (math.sin(ph1) + 0.7 * math.sin(ph2) + 0.2 * math.sin(3 * ph1)))
    return _render_forte(out, volume)


def jingle_pcm(volume: int = 90, rate: int = RATE) -> bytes:
    """Jingle da luna park: arpeggio maggiore che sale, timbro da organetto, chiusura in alto."""
    note = [(523.25, 0.16), (659.25, 0.16), (783.99, 0.16), (1046.50, 0.28),
            (783.99, 0.12), (1046.50, 0.12), (1318.51, 0.60)]
    out = []
    for freq, dur in note:
        n = int(dur * rate)
        for i in range(n):
            t = i / rate
            env = min(1.0, i / (0.008 * rate)) * (1 - i / n) ** 1.2
            tremolo = 1.0 + 0.12 * math.sin(2 * math.pi * 7 * t)
            out.append(env * tremolo * (math.sin(2 * math.pi * freq * t)
                                        + 0.5 * math.sin(4 * math.pi * freq * t)
                                        + 0.25 * math.sin(6 * math.pi * freq * t)))
        out.extend([0.0] * int(0.012 * rate))
    return _render_forte(out, volume)


def matrix_pcm(volume: int = 90, rate: int = RATE) -> bytes:
    """Un pezzo **nostro** nel gusto di quei film — non la loro colonna sonora, che è di altri.

    Tre strati: un basso in do minore che pulsa a ogni tempo, un ostinato di quattro note sopra, e la
    «pioggia digitale» — note acutissime e brevissime che cadono fuori tempo. Otto secondi, che è
    quanto basta per riconoscerlo senza diventare un disco.
    """
    secondi, battuta = 8.0, 0.6
    out = [0.0] * int(secondi * rate)

    def somma(da: float, campioni: list[float], guadagno: float) -> None:
        inizio = int(da * rate)
        for i, v in enumerate(campioni):
            if 0 <= inizio + i < len(out):
                out[inizio + i] += v * guadagno

    # Il basso: do1 con un filo di quinta sotto, che si spegne prima del battito dopo.
    for k in range(int(secondi / battuta)):
        n = int(0.45 * rate)
        colpo = []
        for i in range(n):
            t = i / rate
            env = min(1.0, i / (0.004 * rate)) * math.exp(-t / 0.16)
            colpo.append(env * (math.sin(2 * math.pi * 65.41 * t) + 0.5 * math.sin(2 * math.pi * 49.0 * t)
                                + 0.2 * math.sin(4 * math.pi * 65.41 * t)))
        somma(k * battuta, colpo, 1.0)

    # L'ostinato: do - mi bemolle - sol - si bemolle, in croma, timbro stretto.
    giro = [261.63, 311.13, 392.00, 311.13, 466.16, 392.00, 311.13, 261.63]
    passo = battuta / 2
    for k in range(int(secondi / passo)):
        freq = giro[k % len(giro)]
        n = int(passo * 0.9 * rate)
        nota = []
        for i in range(n):
            t = i / rate
            env = min(1.0, i / (0.01 * rate)) * (1 - i / n) ** 0.7
            saw = 2 * ((freq * t) % 1.0) - 1
            nota.append(env * (0.6 * saw + 0.5 * math.sin(2 * math.pi * freq * t)))
        somma(k * passo, nota, 0.33)

    # La pioggia: gocce acute in posizioni irregolari, prese dal rumore deterministico.
    gocce = _noise(29)
    quando = 0.3
    while quando < secondi - 0.3:
        freq = 1400 + 1600 * abs(next(gocce))
        n = int(0.05 * rate)
        goccia = [math.sin(2 * math.pi * freq * i / rate) * (1 - i / n) ** 2 for i in range(n)]
        somma(quando, goccia, 0.22)
        quando += 0.12 + 0.3 * abs(next(gocce))

    return _render_forte(out, volume)


GENERATED_SOUNDS = {
    "ufo": ufo_pcm,
    "jingle": jingle_pcm,
    "matrix": matrix_pcm,
}


def wav_to_pcm(data: bytes, rate: int = RATE, max_seconds: float = 30.0) -> bytes:
    """WAV qualsiasi (8/16 bit, mono o stereo, qualsiasi frequenza) -> PCM 16 bit mono a ``rate``."""
    import io  # noqa: PLC0415
    import wave  # noqa: PLC0415

    with wave.open(io.BytesIO(data)) as w:
        ch, width, src_rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
        frames = w.readframes(min(w.getnframes(), int(max_seconds * src_rate)))
    if width == 2:
        vals = array.array("h", frames)
    elif width == 1:
        vals = array.array("h", ((b - 128) << 8 for b in frames))
    else:
        raise ValueError(f"WAV a {width * 8} bit non supportato (servono 8 o 16 bit)")
    if ch > 1:
        vals = array.array("h", (sum(vals[i:i + ch]) // ch for i in range(0, len(vals) - ch + 1, ch)))
    if src_rate != rate and len(vals) > 1:  # ricampionamento lineare, sufficiente per questi suoni
        n = int(len(vals) * rate / src_rate)
        step = (len(vals) - 1) / max(1, n - 1)
        out = array.array("h")
        for i in range(n):
            x = i * step
            j = int(x)
            fr = x - j
            nxt = vals[j + 1] if j + 1 < len(vals) else vals[j]
            out.append(int(vals[j] * (1 - fr) + nxt * fr))
        vals = out
    return vals.tobytes()


def load_sounds(folder: Path | None, volume: int = 90, rate: int = RATE) -> dict[str, bytes]:
    """Suoni dei dissuasori: quelli generati dal nodo più i WAV della cartella (stesso nome = sostituisce)."""
    sounds = {name: fn(volume=volume, rate=rate) for name, fn in BUILTIN_SOUNDS.items()}
    if folder and Path(folder).is_dir():
        for p in sorted(Path(folder).glob("*.wav")):
            try:
                sounds[re.sub(r"[^\w-]", "_", p.stem.lower())[:40]] = wav_to_pcm(p.read_bytes(), rate)
            except Exception as exc:  # noqa: BLE001
                log.warning("Suono %s ignorato: %s", p.name, exc)
    return sounds


def load_generated(volume: int = 90, rate: int = RATE) -> dict[str, bytes]:
    """I suoni che il nodo compone: nessuna cartella, nessun file, nessuna licenza da verificare.

    Costano un paio di decimi di secondo all'avvio su questo computer, qualche decimo su un Pi 3B, e
    si pagano una volta sola: in cambio il nodo sa dichiarare **quanto dura** ognuno, che è quello che
    serve a Mirto per chiedere «fallo tre volte» invece di «fallo per otto secondi».
    """
    return {name: fn(volume=volume, rate=rate) for name, fn in GENERATED_SOUNDS.items()}


class AudioError(RuntimeError):
    pass


def parse_bt_devices(text: str) -> list[tuple[str, str]]:
    """Righe ``Device AA:BB:… Nome`` di bluetoothctl → [(mac, nome)], senza doppioni."""
    seen, out = set(), []
    for m in re.finditer(r"Device\s+((?:[0-9A-F]{2}:){5}[0-9A-F]{2})\s*(.*)", text, re.I):
        mac = m.group(1).upper()
        if mac not in seen:
            seen.add(mac)
            name = m.group(2).strip()
            out.append((mac, "" if name.replace("-", ":").upper() == mac else name))
    return out


def casse_abbinate(run=subprocess.run) -> list[dict]:
    """Le casse Bluetooth che questo nodo ricorda: [{mac, nome, collegata}].

    **Non le salviamo noi.** L'elenco ce l'ha gia' BlueZ, che e' chi le ha abbinate: tenerne una copia
    in un file del nodo vorrebbe dire due verita' che prima o poi divergono — una cassa tolta con
    ``bluetoothctl remove`` sparirebbe di la' e resterebbe di qua. Qui si legge e basta.

    Elenco vuoto anche quando ``bluetoothctl`` non c'e' o non risponde: su un nodo senza Bluetooth non
    e' un guasto, e' l'assenza di un pezzo di ferro.
    """
    def chiedi(*argv) -> str:
        try:
            res = run(["bluetoothctl", *argv], capture_output=True, text=True, timeout=5)
        except Exception:  # noqa: BLE001
            return ""
        return getattr(res, "stdout", "") or ""

    note = parse_bt_devices(chiedi("devices", "Paired")) or parse_bt_devices(chiedi("paired-devices"))
    fuori = []
    for mac, nome in note:
        info = chiedi("info", mac)
        fuori.append({"mac": mac, "nome": nome or "(senza nome)",
                      "collegata": bool(re.search(r"^\s*Connected:\s*yes", info, re.M | re.I))})
    return fuori


def schermo_attaccato(radice: Path | None = None) -> bool:
    """C'e' un monitor o una TV collegata al Raspberry?

    Serve a non mostrare una scheda HDMI a chi non ha niente attaccato: un Raspberry dichiara **due**
    uscite HDMI sempre, accese o spente, e proporle a tutti sarebbe rumore. Il sistema invece lo sa:
    ``/sys/class/drm/card*/status`` dice «connected» solo se dall'altra parte c'e' davvero qualcosa.
    """
    radice = radice or Path("/sys/class/drm")
    try:
        porte = sorted(radice.glob("card*-HDMI*/status"))
    except OSError:
        return False
    for porta in porte:
        try:
            if porta.read_text().strip().lower() == "connected":
                return True
        except OSError:
            continue
    return False


OUTPUTS = ("bluetooth", "jack", "usb", "hdmi", "predefinito")
# Se l'uscita scelta non risponde, la sirena suona da qui, in quest'ordine. Una sirena muta perché
# la cassa Bluetooth era spenta non è una sirena: meglio un allarme meno bello che nessun allarme.
RIPIEGHI = ("jack", "usb", "predefinito")
# Cassa abbinata ma scollegata: si riprova entro un minuto, non ogni «tieni_sveglia_minuti».
# Quattro minuti di silenzio sono troppi per una sirena che deve suonare durante un'intrusione.
RIPROVA_SCOLLEGATA = 60
# Quando il collegamento muore con questo motivo, il legame Bluetooth e' rotto da una delle due
# parti (molte casse, rimesse in abbinamento, sostituiscono la chiave del dispositivo precedente):
# insistere non serve, lo rifa' il pannello «Audio del nodo…».
LEGAME_PERSO = "br-connection-unknown"
# Da dove sta suonando, pronto da infilare in una frase: «la sirena suona dal jack 3,5 mm».
# Con la preposizione giusta, perché queste righe le legge una persona, nel registro e in Mirto.
OUTPUT_DA = {"bluetooth": "dalla cassa Bluetooth", "jack": "dal jack 3,5 mm", "usb": "dalla scheda USB",
             "hdmi": "dall'HDMI", "predefinito": "dall'uscita di sistema"}


PROC_ASOUND = Path("/proc/asound")


def _versi(radice: Path | None, n: int) -> tuple[bool, bool]:
    """Se la scheda ``n`` sa suonare e se sa registrare.

    ALSA tiene una cartella per dispositivo dentro ``/proc/asound/cardN``: ``pcm0p`` è una
    riproduzione, ``pcm0c`` una cattura. Una scheda può avere **solo** l'una o **solo** l'altra — un
    microfono USB non suona, una cassa USB non registra — e ``/proc/asound/cards`` non lo dice: lì
    stanno tutte uguali. Il 22/10, col microfono e la cassa USB attaccati insieme per la prima volta,
    il microfono compariva fra le uscite proponibili per la sirena: scegliendolo, la sirena avrebbe
    suonato in un posto che non sa suonare.

    Senza la cartella (``radice`` non data: si sta leggendo un testo, in una prova) la risposta è
    «entrambi»: non saperlo non è un buon motivo per nascondere una scheda a chi la sta cercando.
    """
    if radice is None:
        return True, True
    try:
        pcm = [p.name for p in (radice / f"card{n}").iterdir() if p.name.startswith("pcm")]
    except OSError:
        return True, True
    if not pcm:
        return True, True
    return any(x.endswith("p") for x in pcm), any(x.endswith("c") for x in pcm)


def alsa_cards(text: str | None = None, radice: Path | None = None) -> list[dict]:
    """Schede audio del sistema da ``/proc/asound/cards``: [{n, id, nome, tipo, suona, registra}].

    ``tipo``: "jack" (uscita cuffie del Raspberry), "hdmi", "usb" o "altro".
    ``suona`` e ``registra``: la direzione, letta dai dispositivi della scheda (vedi ``_versi``).
    """
    if text is None:
        radice = radice or PROC_ASOUND
        try:
            text = (radice / "cards").read_text()
        except OSError:
            return []
    out = []
    for m in re.finditer(r"^\s*(\d+)\s+\[([^\]]+?)\s*\]:\s*(\S+)\s*-\s*(.+)$", text, re.M):
        n, cid, driver, name = int(m.group(1)), m.group(2).strip(), m.group(3), m.group(4).strip()
        low = f"{cid} {driver} {name}".lower()
        kind = ("jack" if "headphones" in low or "bcm2835" in low and "hdmi" not in low
                else "hdmi" if "hdmi" in low
                else "usb" if "usb" in low else "altro")
        suona, registra = _versi(radice, n)
        out.append({"n": n, "id": cid, "nome": name, "tipo": kind, "suona": suona, "registra": registra})
    return out


OUTPUT_LABEL = {"bluetooth": "cassa Bluetooth", "jack": "jack 3,5 mm (cuffie)", "usb": "scheda o cassa USB",
                "hdmi": "HDMI (TV o monitor)", "predefinito": "uscita predefinita del sistema",
                "altro": "altra scheda"}


def audio_choices(cards: list[dict]) -> list[dict]:
    """Uscite proponibili: Bluetooth, poi ogni scheda che **sa suonare**, poi quella predefinita.

    Sta qui, e non in ``cli.py``, perché l'elenco lo propone anche Mirto: il pannello dell'audio
    mostra le stesse voci del menu sul Raspberry, senza inventarsene di sue.

    Una scheda di sola cattura non compare: è un microfono, e una sirena non suona da un microfono.
    """
    out = [{"uscita": "bluetooth", "scheda": "", "etichetta": "Cassa Bluetooth"}]
    order = {"jack": 0, "usb": 1, "hdmi": 2, "altro": 3}
    for c in sorted((c for c in cards if c.get("suona", True)), key=lambda c: (order.get(c["tipo"], 9), c["n"])):
        kind = c["tipo"] if c["tipo"] in OUTPUTS else "predefinito"
        label = OUTPUT_LABEL.get(c["tipo"], c["tipo"])
        out.append({"uscita": kind, "scheda": c["id"] if kind != "predefinito" else "",
                    "etichetta": f"{label[0].upper()}{label[1:]} — {c['nome']} [{c['id']}]"})
    out.append({"uscita": "predefinito", "scheda": "", "etichetta": "Uscita predefinita del sistema"})
    return out


def mic_choices(cards: list[dict]) -> list[dict]:
    """Microfoni proponibili: le schede che **sanno registrare**, USB per prime.

    Qui, al contrario delle uscite, non c'è la voce «predefinito»: su un Raspberry la scheda
    predefinita è quella delle cuffie, che non registra, e offrire «il microfono di sistema»
    vorrebbe dire offrire un microfono che non esiste. Elenco vuoto = nessun microfono collegato, ed
    è una frase che il pannello può dire con sicurezza.
    """
    order = {"usb": 0, "altro": 1, "jack": 2, "hdmi": 3}
    return [{"scheda": c["id"], "etichetta": f"{c['nome']} [{c['id']}]"}
            for c in sorted((c for c in cards if c.get("registra", True)),
                            key=lambda c: (order.get(c["tipo"], 9), c["n"]))]


def uscita_da_chiave(chiave: str) -> tuple[str, str, str]:
    """``"usb:UACDemoV10"`` -> ``("usb", "UACDemoV10", "")``; ``"bluetooth:AA:BB:.."`` -> il MAC.

    È lo stesso nome che Mirto usa per elencare le uscite (``chiave_uscita``): un posto solo dove
    si decide come si chiama una cassa, perché due nomi per la stessa cosa diventano due bug.
    Una chiave vuota, o senza i due punti, vuol dire «l'uscita configurata»: chi chiama se ne accorge
    dal primo elemento vuoto.
    """
    testo = str(chiave or "").strip()
    if ":" not in testo:
        return "", "", ""
    uscita, resto = testo.split(":", 1)
    uscita = uscita.strip().lower()
    if uscita not in OUTPUTS:
        return "", "", ""
    if uscita == "bluetooth":
        return uscita, "", resto.strip().upper()
    return uscita, resto.strip(), ""


def quale_cassa(chiave: str, output: str = "", card: str = "", bt_mac: str = "") -> str:
    """Il nome unico della cassa su cui si suonerà: due richieste con lo stesso nome sono la stessa linea.

    Serve a una domanda sola, ma che prima nessuno poteva fare: *questa seconda operazione finisce
    sulla stessa cassa di quella che sta già suonando?* Due chiavi scritte in modo diverso possono
    voler dire la stessa uscita — una vuota vuol dire «quella configurata», e `bluetooth:aa:bb` e
    `bluetooth:AA:BB` sono la stessa cassa — e confrontare le chiavi come arrivano direbbe di no.

    I tre argomenti dopo sono l'uscita configurata, cioè quello che una chiave vuota significa.
    """
    uscita, scheda, mac = uscita_da_chiave(chiave)
    if not uscita:
        uscita, scheda, mac = output, card, bt_mac
    if uscita == "bluetooth":
        return f"bluetooth:{str(mac or '').strip().upper()}"
    return f"{uscita}:{str(scheda or '').strip()}"


def alsa_device(output: str, card: str, bt_mac: str = "") -> str:
    """Nome ALSA dell'uscita scelta (la scheda si indica per nome: non cambia se si aggiunge una scheda USB)."""
    if output == "bluetooth":
        return f"bluealsa:DEV={bt_mac},PROFILE=a2dp"
    if output == "hdmi" and card:
        return f"hdmi:CARD={card},DEV=0"
    if output in ("jack", "usb") and card:
        return f"plughw:CARD={card},DEV=0"
    return "default"


class AudioPlayer:
    """Riproduce audio su una cassa Bluetooth (via bluealsa) o sull'uscita predefinita."""

    def __init__(self, output: str = "bluetooth", bt_mac: str = "", rate: int = RATE,
                 keep_alive_minutes: float = 4.0, popen=subprocess.Popen, run=subprocess.run, card: str = "",
                 cards=alsa_cards, manopole=None, jack: bool = True):
        self.output = output if output in OUTPUTS else "predefinito"
        self.card = card  # nome della scheda ALSA per jack, USB, HDMI (es. "Headphones")
        self.in_uso = self.output   # da dove sta suonando davvero: cambia se l'uscita scelta non risponde
        self.card_in_uso = card
        self.ripiego = ""           # descrizione dell'uscita di ripiego in uso (vuota: sta suonando dove deve)
        self._cards = cards
        # mixer.Manopole, o None: chi sistema volume e muto della scheda. Una scheda mutata o a un
        # terzo di volume e' una sirena che non si sente, e il 22/10 e' costata mezz'ora di prove.
        self.manopole = manopole
        # Il jack si puo' spegnere dal pannello. Non e' un capriccio: su un nodo che non ha niente
        # attaccato al jack, ripiegarci sopra vuol dire una sirena che suona in un buco e nessuno che
        # se ne accorga - meglio sapere che non ha suonato.
        self.jack_attivo = jack
        self.bt_mac = bt_mac.strip().upper()
        self.rate = rate
        self.keep_alive = keep_alive_minutes
        self._popen = popen
        self._run = run
        self._proc = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.last_error = ""
        # L'ultimo ripiego gia' raccontato nel diario. Senza, la riga «uscita non disponibile» esce a
        # **ogni** controllo: su un nodo appena nato, che una cassa non ce l'ha, erano quattro righe
        # identiche ogni quarto d'ora, per sempre (visto l'8/11). E' lo stesso difetto dell'anello del
        # 22/10: righe identiche che riempiono il diario e nascondono quelle che contano. Un avviso
        # che si ripete uguale insegna a non leggere gli avvisi.
        self._ripiego_detto: tuple = ()
        self.legame_perso = False   # il collegamento è morto con «br-connection-unknown»
        self.last_played = 0.0
        self._ka_thread: threading.Thread | None = None
        self._ka_stop = threading.Event()
        self.owner = ""  # chi sta suonando ("sirena", "dissuasore"…): un dissuasore non spegne mai la sirena
        # Le sorelle: un player per ogni altra cassa, creato la prima volta che la si chiede.
        # Un player ha una linea sola — `_proc`, `_thread`, `in_uso`, `ripiego` sono di quel suono —
        # e due casse sono due linee, non una da dividere in due. Vedi `per()`.
        self._sorelle: dict[str, AudioPlayer] = {}
        self.madre: AudioPlayer | None = None   # una sorella non fa sorelle: le chiede a lei

    @property
    def cassa(self) -> str:
        """Il nome unico della cassa configurata su questo player: con cosa confrontare una chiave."""
        return quale_cassa("", self.output, self.card, self.bt_mac)

    def per(self, chiave: str = "") -> AudioPlayer:
        """Il player che suona da quell'uscita: questo, o una sorella sua.

        Fino al 24/10 il nodo aveva un player solo, e due suoni insieme erano una bugia: `loop()`
        comincia con `self.stop()`, quindi il secondo ammazzava il primo — mentre il nodo rispondeva
        «ok» a tutt'e due e metteva due timer. Con due uscite diverse era peggio: l'uscita del player
        cambiava sotto i piedi del suono che stava già andando.

        Una cassa è una linea: `aplay` ci scrive dentro, e due aplay sulla stessa linea non fanno due
        suoni, fanno un pasticcio. Quindi un player per cassa, e **chi suona dalla stessa cassa di un
        altro lo scopre dal nodo che gli dice no** (in `activate`), non dal silenzio.

        La sorella nasce già configurata su quella cassa, ma il suono le arriva comunque con la
        chiave: così resta in piedi la regola del 1.10 — un'uscita **chiesta** non ripiega.
        """
        if self.madre is not None:
            return self.madre.per(chiave)
        voluta = quale_cassa(chiave, self.output, self.card, self.bt_mac)
        if voluta == self.cassa:
            return self
        if voluta not in self._sorelle:
            uscita, scheda, mac = uscita_da_chiave(chiave)
            self._sorelle[voluta] = self._crea_sorella(uscita, scheda, mac)
            self._sorelle[voluta].madre = self
        return self._sorelle[voluta]

    def _crea_sorella(self, uscita: str, scheda: str, mac: str) -> AudioPlayer:
        """Una sorella come questo player, ma su un'altra cassa. Niente sveglia: è di chi è configurato."""
        return AudioPlayer(output=uscita, bt_mac=mac, card=scheda, rate=self.rate, keep_alive_minutes=0.0,
                           popen=self._popen, run=self._run, cards=self._cards,
                           manopole=self.manopole, jack=self.jack_attivo)

    def sorelle(self) -> list[AudioPlayer]:
        """Le sorelle nate finora: serve a chi deve fermare o chiudere tutto."""
        return list(self._sorelle.values())

    # --- dove suona ------------------------------------------------------
    @property
    def device(self) -> str:
        return alsa_device(self.in_uso, self.card_in_uso, self.bt_mac)

    def bt_info(self) -> str:
        """Risposta di ``bluetoothctl info`` per la cassa (vuota se non disponibile)."""
        if self.output != "bluetooth" or not self.bt_mac:
            return ""
        try:
            res = self._run(["bluetoothctl", "info", self.bt_mac], capture_output=True, text=True, timeout=5)
            return res.stdout or ""
        except Exception:  # noqa: BLE001
            return ""

    def bt_stato(self) -> dict:
        """Nome, abbinamento, fiducia e collegamento della cassa. Vuoto se lo stato non si legge.

        Vuoto vuol dire «non lo so» (``bluetoothctl`` assente o muto), e non si accusa nessuno: si
        prova a collegare e si vede. Distinguere ``paired`` da ``connected`` conta, perché il primo
        non si aggiusta aspettando.
        """
        righe = [r.strip() for r in self.bt_info().splitlines()]
        campi = {r.split(":", 1)[0].strip().lower(): r.split(":", 1)[1].strip()
                 for r in righe if ":" in r and not r.startswith("Device ")}
        if not any(k in campi for k in ("paired", "connected")):
            return {}
        return {"nome": campi.get("name", ""),
                "paired": campi.get("paired") == "yes",
                "trusted": campi.get("trusted") == "yes",
                "connected": campi.get("connected") == "yes"}

    def bt_connected(self) -> bool:
        return bool(self.bt_stato().get("connected"))

    def ready(self, chiave: str = "") -> bool:
        """Prepara l'uscita scelta e, se non risponde, un'uscita di ripiego.

        Il ripiego non nasconde il guasto: ``last_error`` resta con il motivo e ``status()`` continua
        a dire che la cassa non è collegata. Dice solo che la sirena ha suonato lo stesso.

        **Con ``chiave`` il ripiego non c'è** (API 1.10). Chiedere «suona dalla cassa del portico» e
        sentirla dalla cucina non è un ripiego, è un'altra cosa: se quell'uscita non risponde si dice
        che non risponde. Il ripiego resta per l'uscita configurata, dove «suona da dove puoi» è
        esattamente quello che si vuole da una sirena.
        """
        if chiave:
            uscita, scheda, mac = uscita_da_chiave(chiave)
            if not uscita:
                self.last_error = f"uscita «{chiave}» non riconosciuta"
                return False
            vecchio_mac = self.bt_mac
            if uscita == "bluetooth" and mac:
                self.bt_mac = mac       # il controllo del collegamento guarda qui
            pronta = self._uscita_pronta(uscita, scheda)
            if pronta:
                self.in_uso, self.card_in_uso, self.ripiego = uscita, scheda, ""
                self._sistema_manopole()
            else:
                self.bt_mac = vecchio_mac
            return pronta
        if self._uscita_pronta(self.output, self.card):
            self.in_uso, self.card_in_uso, self.ripiego = self.output, self.card, ""
            # Tornata a posto: il prossimo ripiego e' una notizia nuova e va detto di nuovo.
            self._ripiego_detto = ()
            self._sistema_manopole()
            return True
        motivo = self.last_error
        for alt, card in self._ripieghi():
            if self._uscita_pronta(alt, card):
                self.in_uso, self.card_in_uso = alt, card
                self._sistema_manopole()
                self.ripiego = OUTPUT_DA.get(alt, alt)
                self.last_error = motivo
                # Si racconta quando **cambia**, non a ogni controllo: la prima volta, e poi solo se
                # cambia l'uscita, il motivo o il ripiego. Il caso che si ripete uguale resta nel
                # diario a livello debug, per chi lo va a cercare.
                adesso = (self.output, motivo or "", self.ripiego)
                dillo = log.warning if adesso != self._ripiego_detto else log.debug
                self._ripiego_detto = adesso
                dillo("Uscita audio «%s» non disponibile (%s): suono %s",
                      self.output, motivo or "motivo sconosciuto", self.ripiego)
                return True
        self.in_uso, self.card_in_uso, self.ripiego = self.output, self.card, ""
        self.last_error = motivo
        return False

    def _sistema_manopole(self) -> None:
        """Volume e muto della scheda su cui si sta per suonare, una volta sola.

        Serve anche sul ripiego, che e\' il caso peggiore: si ripiega sul jack proprio quando la cassa
        non risponde, cioe\' durante un allarme, e trovare quella scheda mutata vorrebbe dire scoprirlo
        in quel momento. ``prepara`` non rifa\' niente se la scheda e\' gia\' sistemata.
        """
        if self.manopole is not None and self.card_in_uso:
            self.manopole.prepara(self.card_in_uso, "riproduzione")

    def _ripieghi(self):
        """Uscite da provare quando quella scelta non risponde: (uscita, scheda).

        Due cose che sembrano dettagli e non lo sono:

        * si guarda solo chi **sa suonare**. Il 22/10, col microfono e la cassa USB attaccati insieme,
          qui si prendeva la prima scheda «usb» dell'elenco — che era il microfono: la sirena sarebbe
          ripiegata su un dispositivo che non ha nemmeno un'uscita, e nessuno se ne sarebbe accorto;
        * il jack si salta se e' stato spento dal pannello. Chi l'ha spento sa che li' non c'e'
          attaccato niente, e una sirena che «suona» in un jack vuoto e' peggio di una che si ferma e
          lo dice.
        """
        for tipo in RIPIEGHI:
            if tipo == self.output:
                continue          # quella l'abbiamo appena provata
            if tipo == "jack" and not self.jack_attivo:
                continue
            if tipo == "predefinito":
                yield tipo, ""
                continue
            scheda = next((c for c in self._cards() if c["tipo"] == tipo and c.get("suona", True)), None)
            if scheda:
                yield tipo, scheda["id"]

    def _uscita_pronta(self, output: str, card: str) -> bool:
        if output in ("jack", "usb", "hdmi"):
            if card and not any(c["id"] == card for c in self._cards()):
                self.last_error = f"scheda audio «{card}» non trovata (scollegata?)"
                return False
            return True
        if output != "bluetooth":
            return True
        if not self.bt_mac:
            self.last_error = "cassa Bluetooth non configurata (bluetooth_mac in node.toml)"
            return False
        stato = self.bt_stato()
        if stato.get("connected"):
            return True
        if stato and not stato["paired"] and not stato["trusted"]:
            # Mai scelta da qui: nessun tentativo la farebbe comparire, e il rimedio è una scelta di
            # una persona nel pannello.
            nome = stato.get("nome") or self.bt_mac
            self.last_error = (f"cassa «{nome}» non ancora abbinata a questo nodo: Mirto → "
                               "Impostazioni → Nodi → «Audio del nodo…»")
            return False
        # «Paired: no» NON vuol dire che non funzioni: il 21/10, su hardware, la JBL si è collegata e ha
        # suonato con «Paired: no, Trusted: yes» — le casse A2DP spesso non lasciano un legame salvato.
        # Chi decide è l'errore del collegamento, non un campo dello stato.
        testo = self._bt_connect()
        if LEGAME_PERSO in testo:
            nome = stato.get("nome") or self.bt_mac
            self.last_error = (f"cassa «{nome}» non più abbinata al nodo: Mirto → Impostazioni → "
                               "Nodi → «Audio del nodo…» → «Abbina» la rifà da sola")
            self.legame_perso = True
            return False
        if "not available" in testo.lower():
            # BlueZ tiene una cassa non abbinata solo finché si annuncia: senza una ricerca, il
            # collegamento parte verso un dispositivo che per lui non esiste (21/10).
            try:
                self._run(["bluetoothctl", "--timeout", "10", "scan", "on"], capture_output=True,
                          text=True, timeout=14)
            except Exception:  # noqa: BLE001
                pass
            testo = self._bt_connect()
        for _ in range(3):
            if self.bt_connected():
                time.sleep(0.8)  # bluealsa ha bisogno di un attimo per aprire il canale audio
                self.legame_perso = False
                return True
            time.sleep(0.5)
        self.last_error = "cassa Bluetooth spenta o lontana"
        return False

    def _bt_connect(self) -> str:
        """Un tentativo di collegamento; restituisce quello che ha detto bluetoothctl ("" se è esploso)."""
        try:
            res = self._run(["bluetoothctl", "connect", self.bt_mac], capture_output=True, text=True,
                            timeout=6)
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"collegamento Bluetooth fallito: {exc}"
            return ""
        return (getattr(res, "stdout", "") or "") + (getattr(res, "stderr", "") or "")

    def status(self) -> dict:
        st = {"uscita": self.output, "in_riproduzione": self.playing}
        if self.output in ("jack", "usb", "hdmi"):
            card = next((c for c in self._cards() if c["id"] == self.card), None)
            st["scheda"] = self.card or "predefinita"
            st["collegata"] = card is not None or not self.card
            if card:
                st["nome"] = card["nome"]
        if self.output == "bluetooth":
            st["cassa"] = self.bt_mac or "non configurata"
            # «non configurata» e «configurata ma non collegata» sono due guai diversi, con due rimedi
            # diversi: il primo si risolve scegliendo l'uscita, il secondo accendendo la cassa.
            st["configurata"] = bool(self.bt_mac)
            stato = self.bt_stato()
            st["collegata"] = bool(stato.get("connected"))
            if stato:
                # Conosciuta da questo nodo: «Trusted» basta, perché una cassa A2DP spesso si collega
                # senza lasciare un legame salvato (JBL Flip 6, 21/10: Paired: no e suona).
                st["abbinata"] = bool(stato["paired"] or stato["trusted"])
            if stato.get("nome"):
                st["nome"] = stato["nome"]
        st["uscita_in_uso"] = self.in_uso
        if self.ripiego:
            st["ripiego"] = self.ripiego
        if self.last_error:
            st["ultimo_errore"] = self.last_error
        return st

    # --- riproduzione ----------------------------------------------------
    @property
    def playing(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _aplay_raw(self):
        return self._popen(["aplay", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(self.rate), "-c", "1",
                            "-D", self.device], stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    def loop(self, pcm: bytes, seconds: float, owner: str = "", uscita: str = "") -> None:
        """Ripete ``pcm`` senza pause per ``seconds`` secondi (o fino a ``stop``). Non blocca."""
        self.stop()
        self.owner = owner
        if not self.ready(uscita):
            raise AudioError(self.last_error)
        self._stop.clear()
        if not self.ripiego:
            self.last_error = ""   # col ripiego il motivo resta: è l'unica traccia che la cassa non risponde

        def work():
            deadline = time.monotonic() + seconds
            try:
                proc = self._aplay_raw()
                with self._lock:
                    self._proc = proc
                chunk = self.rate * 2 // 4  # un quarto di secondo per volta: si ferma in fretta
                pos = 0
                while not self._stop.is_set() and time.monotonic() < deadline:
                    part = pcm[pos:pos + chunk] or pcm[:chunk]
                    pos = (pos + chunk) % len(pcm)
                    proc.stdin.write(part)
                    proc.stdin.flush()
                    # aplay legge più in fretta del tempo reale: si scrive al ritmo dell'audio
                    time.sleep(len(part) / (self.rate * 2) * 0.9)
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"riproduzione interrotta: {exc}"
                log.error("Audio: %s", self.last_error)
            finally:
                self._close()
                self.last_played = time.time()

        self._thread = threading.Thread(target=work, name="audio", daemon=True)
        self._thread.start()

    def ripeti(self, pcm: bytes, volte: int, owner: str = "", uscita: str = "") -> float:
        """Riproduce il suono **esattamente** ``volte`` volte. Restituisce quanto durerà.

        Esiste perché «per quanti secondi» non sa dire «una volta», e il 23/10 Marco l'ha detto in
        una riga: *«se metti × 1 su un suono che emetti, non decidi tu quanto dura»*. La durata di uno
        sparo la decide lo sparo.

        ``loop`` contava il tempo di **scrittura**, e scriveva l'11% più veloce del tempo reale perché
        aplay non restasse a secco: alla scadenza aveva in pancia più audio di quanto chiesto, e quello
        si sentiva comunque. Chiedendo 1,9 s di uno sparo da 2 se ne sentivano due. Qui invece si
        conta in **byte**: tanti quanti ne servono per ``volte`` ripetizioni, né uno di più. Poi si
        chiude l'ingresso di aplay e lo si aspetta, invece di ucciderlo — se no si taglia la coda.
        """
        self.stop()
        self.owner = owner
        if not self.ready(uscita):
            raise AudioError(self.last_error)
        self._stop.clear()
        if not self.ripiego:
            self.last_error = ""
        volte = max(1, int(volte))
        totale = len(pcm) * volte
        durata = totale / (self.rate * 2) if self.rate else 0.0

        def work():
            try:
                proc = self._aplay_raw()
                with self._lock:
                    self._proc = proc
                chunk = self.rate * 2 // 4     # un quarto di secondo: si ferma in fretta
                scritti = 0
                while not self._stop.is_set() and scritti < totale:
                    dentro = scritti % len(pcm)
                    quanto = min(chunk, totale - scritti, len(pcm) - dentro)
                    part = pcm[dentro:dentro + quanto]
                    proc.stdin.write(part)
                    proc.stdin.flush()
                    scritti += len(part)
                    # si scrive al ritmo dell'audio, un filo più in fretta: aplay non resta a secco
                    time.sleep(len(part) / (self.rate * 2) * 0.9)
                if not self._stop.is_set():
                    try:
                        proc.stdin.close()     # fa finire quello che ha in pancia, non lo taglia
                        proc.wait(timeout=durata + 5)
                    except Exception:  # noqa: BLE001, S110
                        pass
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"riproduzione interrotta: {exc}"
                log.error("Audio: %s", self.last_error)
            finally:
                self._close()
                self.last_played = time.time()

        self._thread = threading.Thread(target=work, name="audio", daemon=True)
        self._thread.start()
        return durata

    def play_wav(self, data: bytes, timeout: float = 120, uscita: str = "") -> None:
        """Riproduce un file WAV (es. un messaggio) e aspetta la fine."""
        self.stop()
        if not self.ready(uscita):
            raise AudioError(self.last_error)
        proc = self._popen(["aplay", "-q", "-D", self.device, "-"], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        with self._lock:
            self._proc = proc
        try:
            proc.communicate(data, timeout=timeout)
        finally:
            self._close()
            self.last_played = time.time()

    def _close(self) -> None:
        with self._lock:
            proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            proc.wait(timeout=3)
        except Exception:  # noqa: BLE001
            proc.kill()

    def stop(self, owner: str | None = None) -> None:
        """Ferma subito il suono in corso (con ``owner``: solo se è quello a suonare)."""
        if owner is not None and (self.owner != owner or not self.playing):
            return
        self._stop.set()
        with self._lock:
            proc = self._proc
        if proc is not None:
            try:
                proc.terminate()
            except Exception:  # noqa: BLE001
                pass
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)
        self._thread = None

    # --- cassa sempre sveglia --------------------------------------------
    def _pausa_sveglia(self) -> float:
        """Quanto aspettare prima del prossimo tentativo.

        Un minuto se la cassa è abbinata ma scollegata (riprovare la riporta, e costa un tentativo);
        ``tieni_sveglia_minuti`` se è collegata — lì si tocca solo per non farla addormentare — e
        anche se l'abbinamento è perso, perché insistere non lo rifà e riempirebbe solo il diario.
        """
        lungo = self.keep_alive * 60
        if self.output != "bluetooth":
            return lungo
        if self.legame_perso:
            return lungo          # insistere non rifà un legame: serve che una persona lo rifaccia
        stato = self.bt_stato()
        if stato and (stato["paired"] or stato["trusted"]) and not stato["connected"]:
            return min(RIPROVA_SCOLLEGATA, lungo) if lungo else RIPROVA_SCOLLEGATA
        return lungo

    def start_keep_alive(self) -> None:
        if self.output != "bluetooth" or self.keep_alive <= 0 or self._ka_thread:
            return
        self._ka_stop.clear()

        def work():
            quiet = quiet_pcm(1.0, self.rate)
            while not self._ka_stop.wait(30):
                if self.playing or time.time() - self.last_played < self._pausa_sveglia():
                    continue
                try:
                    self.loop(quiet, 1.0)
                except AudioError:
                    self.last_played = time.time()  # riprova al prossimo giro, senza insistere

        self._ka_thread = threading.Thread(target=work, name="audio-sveglia", daemon=True)
        self._ka_thread.start()

    def close(self) -> None:
        self._ka_stop.set()
        self.stop()
        for s in self.sorelle():   # chiudere il nodo spegne tutte le casse, non solo quella configurata
            s.close()
