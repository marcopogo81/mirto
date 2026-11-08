# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Le manopole di una scheda audio: leggerle, girarle, ricordarle.

Perché esiste — 22/10, col primo microfono USB attaccato al nodo. Un C-Media USB PnP nasce col
guadagno a **0 su 16** e l'«Auto Gain Control» **acceso**, e in quello stato una voce arriva all'1%
del fondo scala: il riconoscimento dei suoni non sente niente, e nessuno capisce perché. Misurato sul
ferro, parlando alla stessa distanza:

======================  ==================  =====
manopole                 voce (picco)        media
======================  ==================  =====
guadagno 0, AGC on       1 %                 51
guadagno 16, AGC on      14 %                609
guadagno 16, AGC off     **38 %**            1659
mani a 2 m, AGC off      **50 %**            —
======================  ==================  =====

L'automatismo *limitava*: spegnendolo il livello quasi triplica, e un colpo di mani — che è il tipo di
suono che Mirto deve riconoscere davvero, non una voce — sta a metà fondo scala con margine. Quei
due valori stanno qui dentro, perché nessun utente al mondo troverà mai
``amixer -c Device sset 'Auto Gain Control' off``; e le manopole stanno in Mirto, perché una scheda
nuova ne ha di sue e indovinarle per tutti è impossibile.

Tre cose di ALSA spiegano la forma di questo file:

* ``amixer`` gira **senza privilegi**, se l'utente sta nel gruppo ``audio``, e il servizio del nodo ci
  sta (è lo stesso motivo per cui può lanciare ``aplay``): una manopola girata da Mirto si applica
  subito, senza passare dal servizio da root e senza riavviare niente;
* i valori **non sopravvivono al riavvio**: li salverebbe ``alsa-store`` in ``/var/lib/alsa``, che è
  di root. Quindi li ricorda il nodo, in un file suo, e li rimette lui — anche quando un dispositivo
  USB viene staccato e riattaccato, perché torna ai valori di fabbrica suoi;
* le **capacità dichiarate non sono affidabili**: su quel microfono l'«Auto Gain Control» si dichiara
  ``pswitch``, cioè manopola di *riproduzione*, pur essendo l'automatismo del microfono. Per questo
  l'AGC si riconosce dal nome, e per questo la finestra mostra **tutte** le manopole di una scheda
  invece di sceglierle in base alla direzione: su una scheda vera quella direzione mente.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
from pathlib import Path

log = logging.getLogger(__name__)

# Dove il nodo ricorda le manopole, dentro /etc/mirto-node (che è suo: non serve root).
FILE = "manopole.json"

# Il guadagno di un microfono va al massimo: vedi la tabella in testa al file.
MICROFONO = 100
# La cassa no: su questi chip gli ultimi decibel distorcono, e una sirena distorta si sente peggio di
# una sirena forte. Ma il 30% di fabbrica di un dongle USB è −20 dB, un decimo dell'ampiezza: per un
# allarme è troppo poco, e il 22/10 ci è costato mezz'ora di «non si sente niente».
CASSA = 90
# L'AGC si riconosce dal nome, non dalla direzione (vedi il terzo punto in testa al file).
AGC = ("auto gain control", "automatic gain control", "agc")

TESTA = re.compile(r"^Simple mixer control '(.+)',(\d+)$")
LIMITI = re.compile(r"^\s*Limits:\s*(?:(?:Playback|Capture)\s+)?(-?\d+)\s*-\s*(-?\d+)")
PERCENTO = re.compile(r"\[(\d+)%\]")
DECIBEL = re.compile(r"\[(-?\d+(?:\.\d+)?)dB\]")
ACCESO = re.compile(r"\[(on|off)\]")
VALORE_BUONO = re.compile(r"^(?:100|\d{1,2})%$|^(?:on|off)$")


def leggi(text: str) -> list[dict]:
    """Le manopole come le stampa ``amixer -c NOME``.

    Una voce per manopola: ``nome``, ``indice``, se ha un ``volume`` (con ``percento``, ``db`` e i
    limiti) e se ha un ``interruttore`` (con ``acceso``). Una manopola può avere **entrambi** — il
    «PCM» di una cassa USB ha volume e muto — quindi non c'è un «tipo»: ci sono due cose, e la
    finestra mostra quelle che ci sono.
    """
    fuori: list[dict] = []
    for pezzo in re.split(r"(?m)^(?=Simple mixer control )", text):
        righe = pezzo.splitlines()
        if not righe:
            continue
        t = TESTA.match(righe[0].strip())
        if not t:
            continue
        caps: list[str] = []
        minimo = massimo = 0
        for r in righe[1:]:
            spoglia = r.strip()
            if spoglia.startswith("Capabilities:"):
                caps = spoglia.split(":", 1)[1].split()
            lim = LIMITI.match(r)
            if lim:
                minimo, massimo = int(lim.group(1)), int(lim.group(2))
        # La prima riga che porta un valore: «Front Left: Playback 44 [30%] [-20.16dB] [on]», oppure
        # «Mono: Playback [on]» per un interruttore senza volume. I canali successivi li ignoriamo di
        # proposito: una manopola stereo si gira insieme, e due cursori per la stessa cosa sarebbero
        # un modo di far sbagliare le persone.
        valore = next((r for r in righe[1:] if PERCENTO.search(r) or ACCESO.search(r)), "")
        pc, db, on = PERCENTO.search(valore), DECIBEL.search(valore), ACCESO.search(valore)
        fuori.append({
            "nome": t.group(1), "indice": int(t.group(2)), "capacita": caps,
            "volume": any(c in ("pvolume", "cvolume", "volume") for c in caps),
            "interruttore": any(c in ("pswitch", "cswitch", "switch") for c in caps),
            "direzione": ("cattura" if any(c.startswith("c") for c in caps)
                          else "riproduzione" if any(c.startswith("p") for c in caps) else ""),
            "minimo": minimo, "massimo": massimo,
            "percento": int(pc.group(1)) if pc else 0,
            "db": db.group(1) if db else "",
            "acceso": on.group(1) == "on" if on else True,
        })
    return fuori


def chiave(manopola: dict) -> str:
    """Come si chiama una manopola per ``amixer`` e nel file dei valori: ``nome,indice``."""
    return f"{manopola['nome']},{manopola['indice']}"


def comando(scheda: str, manopola: str, valore: str) -> list[str]:
    """Il comando per girare una manopola. ``valore``: ``"70%"`` per un volume, ``"on"``/``"off"``.

    La scheda si indica **per nome** e non per numero: attaccando un'altra chiavetta i numeri si
    rimescolano, i nomi no.
    """
    return ["amixer", "-c", scheda, "sset", manopola, valore]


def valori_di_fabbrica(manopole: list[dict], verso: str) -> list[tuple[str, str]]:
    """Cosa mettere la **prima volta** che il nodo prende in uso una scheda: [(manopola, valore)].

    ``verso``: ``"cattura"`` per un microfono, ``"riproduzione"`` per una cassa. Una scheda che fa
    entrambi (certe cuffie USB) viene preparata due volte, una per ruolo, e ogni ruolo tocca solo le
    manopole sue.

    Prima di alzare un volume si **accende** la manopola, se ha un interruttore: una scheda muta è una
    sirena muta e un microfono sordo, e il volume giusto su una scheda mutata non serve a niente.
    """
    livello = MICROFONO if verso == "cattura" else CASSA
    opposto = "riproduzione" if verso == "cattura" else "cattura"
    fuori: list[tuple[str, str]] = []
    for m in manopole:
        k = chiave(m)
        if verso == "cattura" and any(p in m["nome"].lower() for p in AGC):
            if m["interruttore"]:
                fuori.append((k, "off"))
            continue
        if not m["volume"] or m["direzione"] == opposto:
            continue
        if m["interruttore"]:
            fuori.append((k, "on"))
        fuori.append((k, f"{livello}%"))
    return fuori


class Manopole:
    """Legge e gira le manopole delle schede, e le ricorda al posto di ALSA.

    ``cartella`` è ``/etc/mirto-node``, che appartiene al nodo: il file dei valori lo scrive lui,
    senza root. ``None`` vuol dire «non ricordare niente» — serve alle prove e a un nodo che gira
    senza cartella di configurazione.
    """

    def __init__(self, cartella: Path | None = None, run=subprocess.run):
        self.cartella = cartella
        self._run = run
        self._lock = threading.Lock()
        self._preparate: set[tuple[str, str]] = set()   # (scheda, verso) già sistemate in questo processo

    # --- leggere ----------------------------------------------------------
    def leggi(self, scheda: str) -> list[dict]:
        """Le manopole della scheda, o elenco vuoto se la scheda non c'è (o ``amixer`` non c'è)."""
        if not scheda:
            return []
        try:
            res = self._run(["amixer", "-c", scheda], capture_output=True, text=True, timeout=5)
        except Exception as exc:  # noqa: BLE001
            log.debug("Manopole di %s non leggibili: %s", scheda, exc)
            return []
        if getattr(res, "returncode", 1) != 0:
            return []
        return leggi(getattr(res, "stdout", "") or "")

    # --- ricordare --------------------------------------------------------
    @property
    def file(self) -> Path | None:
        return None if self.cartella is None else self.cartella / FILE

    def ricordate(self) -> dict:
        """I valori scelti, per scheda: ``{"Device": {"Mic,0": "100%", …}}``."""
        f = self.file
        if f is None:
            return {}
        try:
            dati = json.loads(f.read_text())
        except (OSError, ValueError):
            return {}
        return {str(k): {str(a): str(b) for a, b in (v or {}).items()}
                for k, v in dati.items() if isinstance(v, dict)}

    def _ricorda(self, scheda: str, manopola: str, valore: str) -> None:
        f = self.file
        if f is None:
            return
        dati = self.ricordate()
        dati.setdefault(scheda, {})[manopola] = valore
        # Si scrive di fianco e poi si sposta: un nodo che perde corrente mentre salva non deve
        # lasciare un file mezzo scritto che al prossimo avvio non si rilegge.
        tmp = f.with_suffix(".json.nuovo")
        try:
            tmp.write_text(json.dumps(dati, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
            os.chmod(tmp, 0o600)   # dentro una cartella 700: non serve che lo legga nessun altro
            os.replace(tmp, f)
        except OSError as exc:
            log.error("Manopole non salvate in %s: %s", f, exc)
            tmp.unlink(missing_ok=True)

    # --- girare -----------------------------------------------------------
    def gira(self, scheda: str, manopola: str, valore: str, ricorda: bool = True) -> bool:
        """Gira una manopola e la ricorda. ``valore``: ``"0%"``…``"100%"``, ``"on"``, ``"off"``."""
        if not VALORE_BUONO.match(valore):
            raise ValueError(f"valore non valido per una manopola: {valore!r}")
        try:
            res = self._run(comando(scheda, manopola, valore), capture_output=True, text=True, timeout=5)
        except Exception as exc:  # noqa: BLE001
            log.error("Manopola %s di %s non girata: %s", manopola, scheda, exc)
            return False
        if getattr(res, "returncode", 1) != 0:
            log.error("Manopola %s di %s rifiutata: %s", manopola, scheda,
                      (getattr(res, "stderr", "") or "").strip()[:200])
            return False
        if ricorda:
            with self._lock:
                self._ricorda(scheda, manopola, valore)
        return True

    # --- prepararle -------------------------------------------------------
    def prepara(self, scheda: str, verso: str) -> None:
        """Rimette i valori scelti, o quelli di fabbrica se nessuno ha ancora scelto.

        Una volta per scheda e per ruolo, in questo processo: la si richiama dopo che il dispositivo è
        stato staccato e riattaccato (vedi ``dimentica``), perché torna ai valori suoi.
        """
        if (scheda, verso) in self._preparate:
            return
        manopole = self.leggi(scheda)
        if not manopole:
            return                     # scheda assente: non si marca niente, si riprova quando torna
        salvate = self.ricordate().get(scheda) or {}
        nomi = {chiave(m) for m in manopole}
        da_fare = ([(k, v) for k, v in sorted(salvate.items()) if k in nomi] if salvate
                   else valori_di_fabbrica(manopole, verso))
        for manopola, valore in da_fare:
            self.gira(scheda, manopola, valore, ricorda=not salvate)
        if da_fare:
            log.info("Manopole di %s sistemate (%s): %s", scheda, verso,
                     ", ".join(f"{k} {v}" for k, v in da_fare))
        self._preparate.add((scheda, verso))

    def dimentica(self, scheda: str = "") -> None:
        """Da richiamare quando una scheda sparisce: al ritorno va ripreparata da zero."""
        self._preparate = {x for x in self._preparate if scheda and x[0] != scheda}
