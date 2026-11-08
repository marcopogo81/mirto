#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Fissa le action dei workflow allo SHA del commit, lasciando il nome della versione nel commento.

    python3 tools/fissa-action.py            # mostra cosa cambierebbe
    python3 tools/fissa-action.py --scrivi   # riscrive i file

``uses: actions/checkout@v7`` diventa ``uses: actions/checkout@3d3c42e… # v7``: un tag si può spostare,
un commit no, quindi nessuno può cambiare sotto i piedi il codice che gira nella CI. Dependabot continua ad
aggiornarli (legge il commento) e questo script è anche il modo di rinnovarli a mano.

Gli SHA si leggono con ``git ls-remote``: serve una connessione a GitHub, ma non un token e non ci sono
limiti di richieste.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
USA = re.compile(r"^(?P<prima>\s*-?\s*uses:\s*)(?P<repo>[\w.-]+/[\w.-]+)@(?P<rif>\S+)(?P<dopo>.*)$")
SHA = re.compile(r"^[0-9a-f]{40}$")


def ls_remote(repo: str, rif: str) -> str:
    """Le righe «sha<tab>riferimento» che GitHub conosce per quel tag o ramo."""
    fatto = subprocess.run(
        ["git", "ls-remote", f"https://github.com/{repo}",
         f"refs/tags/{rif}", f"refs/tags/{rif}^{{}}", f"refs/heads/{rif}"],
        capture_output=True, text=True, timeout=120)
    if fatto.returncode != 0:
        raise RuntimeError((fatto.stderr or "").strip().splitlines()[-1] if fatto.stderr else "git ls-remote")
    return fatto.stdout


def sha_del_riferimento(repo: str, rif: str, elenca=ls_remote) -> str:
    """Lo SHA del commit a cui punta il tag.

    Un tag annotato ha due righe: l'oggetto-tag e, con ``^{}``, il commit che contiene davvero il codice.
    Quella che conta è la seconda — GitHub Actions vuole un commit — e la differenza non si vede finché
    non si prova a far girare il workflow.
    """
    trovati = {}
    for riga in elenca(repo, rif).splitlines():
        parti = riga.split()
        if len(parti) == 2 and SHA.match(parti[0]):
            trovati[parti[1]] = parti[0]
    for nome in (f"refs/tags/{rif}^{{}}", f"refs/tags/{rif}", f"refs/heads/{rif}"):
        if nome in trovati:
            return trovati[nome]
    raise LookupError(f"{repo}: nessun tag o ramo «{rif}»")


def versione(rif: str, dopo: str) -> str:
    """Il nome della versione: il riferimento stesso, o quello lasciato nel commento se è già uno SHA."""
    if not SHA.match(rif):
        return rif
    trovato = re.search(r"#\s*(v[\w.]+)", dopo)
    return trovato.group(1) if trovato else ""


def riscrivi_riga(riga: str, risolvi=sha_del_riferimento) -> tuple[str, str]:
    """La riga con lo SHA al posto del tag, e il nome della versione fissata (vuoto se non c'era nulla da fare)."""
    trovato = USA.match(riga.rstrip("\n"))
    if not trovato:
        return riga, ""
    tag = versione(trovato.group("rif"), trovato.group("dopo"))
    if not tag:
        return riga, ""
    repo = trovato.group("repo")
    sha = risolvi(repo, tag)
    return f"{trovato.group('prima')}{repo}@{sha}  # {tag}\n", tag


def main(argv: list[str]) -> int:
    scrivi = "--scrivi" in argv
    cambi = errori = 0
    for percorso in sorted(WORKFLOWS.glob("*.yml")):
        nuove = []
        for riga in percorso.read_text(encoding="utf-8").splitlines(keepends=True):
            try:
                nuova, tag = riscrivi_riga(riga)
            except Exception as exc:  # noqa: BLE001
                print(f"  ! {riga.strip()}: {exc}")
                errori += 1
                nuove.append(riga)
                continue
            if tag and nuova != riga:
                print(f"  {percorso.name}: {nuova.split('@')[0].split()[-1]}@{tag} → {nuova.split('@')[1].split()[0]}")
                cambi += 1
            nuove.append(nuova)
        if scrivi:
            percorso.write_text("".join(nuove), encoding="utf-8")
    if errori:
        print(f"\n{errori} action non risolte: serve una connessione a GitHub.")
        return 1
    if not cambi:
        print("Tutte le action sono già fissate all'ultimo commit del loro tag.")
    elif not scrivi:
        print(f"\n{cambi} da aggiornare. Rilancia con --scrivi per applicare.")
    else:
        print(f"\n{cambi} aggiornate. Controlla con  git diff  e committa.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
