# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Com'è attaccato alla rete il nodo: cavo o Wi-Fi, e quanto arriva.

Serve a Mirto per una domanda sola, ma è la prima che si fa quando un nodo smette di rispondere: **da
dove arrivava?** Un nodo sul cavo che non risponde è un nodo spento o un cavo staccato. Un nodo in Wi-Fi
che non risponde, nove volte su dieci, è un nodo messo troppo lontano — e il segnale lo dice *prima* che
succeda, che è l'unico momento in cui l'informazione serve ancora a qualcosa.

Si legge dai file di sistema invece di chiederlo a un programma: ``/sys/class/net`` dice quali schede ci
sono, quali sono wireless e quali sono su, ``/proc/net/wireless`` dice la qualità del collegamento.
Nessun processo da avviare, quindi lo stato del nodo non può restare appeso per colpa della rete — e lo
stato lo chiede Mirto a ogni battito. Il nome della rete Wi-Fi è l'unica cosa che i file non hanno:
per quello c'è ``iwgetid``, chiamato **solo** se una scheda wireless è davvero su.

Quando ci sono sia il cavo che il Wi-Fi, il traffico passa dal cavo: si dice «cavo», e il Wi-Fi si
segna come riserva. È la situazione di chi sta impostando il Wi-Fi dal pannello col cavo ancora
attaccato, ed è giusto che si veda che la riserva c'è.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

SCHEDE = Path("/sys/class/net")
WIRELESS = Path("/proc/net/wireless")

# Schede che non sono «la rete di casa»: il loopback e i ponti che si tirano su da sé (Docker, VPN,
# reti virtuali). Dire «sei su wg0» a chi cerca il nodo non aiuta nessuno.
FINTE = ("lo", "docker", "veth", "br-", "virbr", "tailscale", "wg", "zt", "tun", "tap", "ap0")

PIENO = 70.0           # il massimo della colonna «link» di /proc/net/wireless, da cui la percentuale


def _vera(nome: str) -> bool:
    return bool(nome) and not nome.startswith(FINTE)


def schede_da_sistema(radice: Path | None = None) -> list[dict]:
    """Le schede di rete vere: ``[{"nome", "su", "wireless"}]``, in ordine di nome.

    «su» è ``operstate`` a *up*: una scheda con il cavo staccato resta lì, scritta *down*, e non deve
    far dire che il nodo è sul cavo.
    """
    radice = radice or SCHEDE
    trovate = []
    try:
        voci = sorted(p for p in radice.iterdir() if _vera(p.name))
    except OSError:
        return []
    for scheda in voci:
        try:
            su = (scheda / "operstate").read_text().strip() == "up"
        except OSError:
            su = False
        trovate.append({"nome": scheda.name, "su": su,
                        "wireless": (scheda / "wireless").exists() or (scheda / "phy80211").exists()})
    return trovate


def segnale_da_proc(testo: str) -> dict[str, int]:
    """``/proc/net/wireless`` → ``{scheda: percentuale}``.

    La colonna che conta è *link*, da 0 a 70: è quella che i programmi mostrano come «qualità». Le due
    righe di intestazione non hanno i due punti dopo il nome della scheda, e così si saltano.
    """
    fuori: dict[str, int] = {}
    for riga in (testo or "").splitlines():
        if ":" not in riga:
            continue
        nome, _, resto = riga.partition(":")
        nome = nome.strip()
        pezzi = resto.split()
        if not _vera(nome) or len(pezzi) < 2:
            continue
        try:
            link = float(pezzi[1].rstrip("."))
        except ValueError:
            continue
        fuori[nome] = max(0, min(100, round(link * 100 / PIENO)))
    return fuori


def come_e_attaccato(schede: list[dict], segnali: dict[str, int] | None = None,
                     nome_rete: str = "") -> dict:
    """Cavo o Wi-Fi, e quale delle due regge il traffico quando ci sono entrambe.

    ``{"tipo": "cavo"|"wifi"|"", "scheda", "rete", "segnale", "riserva"}``. ``tipo`` vuoto vuol dire che
    nessuna scheda è su: succede davvero, per mezzo secondo, mentre il Wi-Fi sale dopo un riavvio.
    """
    segnali = segnali or {}
    su = [s for s in (schede or []) if s.get("su")]
    cavo = next((s for s in su if not s.get("wireless")), None)
    wifi = next((s for s in su if s.get("wireless")), None)
    if cavo is not None:
        return {"tipo": "cavo", "scheda": cavo["nome"], "rete": "", "segnale": None,
                "riserva": "wifi" if wifi is not None else ""}
    if wifi is not None:
        return {"tipo": "wifi", "scheda": wifi["nome"], "rete": nome_rete,
                "segnale": segnali.get(wifi["nome"]), "riserva": ""}
    return {"tipo": "", "scheda": "", "rete": "", "segnale": None, "riserva": ""}


def in_parole(rete: dict) -> str:
    """«cavo (eth0)», «Wi-Fi «CasaMia» · 68 % (wlan0)»: per il comando ``stato``, letto da una persona."""
    rete = dict(rete or {})
    tipo, scheda = str(rete.get("tipo") or ""), str(rete.get("scheda") or "")
    dove = f" ({scheda})" if scheda else ""
    if tipo == "cavo":
        riserva = " · Wi-Fi di riserva" if rete.get("riserva") == "wifi" else ""
        return f"cavo{dove}{riserva}"
    if tipo != "wifi":
        return "nessuna scheda di rete è su"
    pezzi = [f"Wi-Fi «{rete['rete']}»" if rete.get("rete") else "Wi-Fi"]
    if rete.get("segnale") is not None:
        pezzi.append(f"{int(rete['segnale'])} %" + ("  (debole: spostalo)" if int(rete["segnale"]) < 40 else ""))
    return " · ".join(pezzi) + dove


def _nome_rete(scheda: str) -> str:
    """Il nome della rete Wi-Fi. Un processo piccolo, chiamato solo quando c'è un Wi-Fi su."""
    try:
        res = subprocess.run(["iwgetid", "-r", scheda], capture_output=True, text=True, timeout=3)
        return res.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def stato_rete(radice: Path | None = None, wireless: Path | None = None, nome_rete=None) -> dict:
    """Com'è attaccato il nodo, pronto da mettere nello stato. Non solleva mai: al peggio dice "" ."""
    try:
        schede = schede_da_sistema(radice)
        try:
            segnali = segnale_da_proc((wireless or WIRELESS).read_text())
        except OSError:
            segnali = {}
        prima = come_e_attaccato(schede, segnali)
        if prima["tipo"] != "wifi":
            return prima
        cerca = nome_rete or _nome_rete
        return come_e_attaccato(schede, segnali, cerca(prima["scheda"]))
    except Exception:  # noqa: BLE001
        return {"tipo": "", "scheda": "", "rete": "", "segnale": None, "riserva": ""}
