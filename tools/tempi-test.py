#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Esegue la suite e, alla fine, stampa i test più lenti.

    python tools/tempi-test.py           # come «python -m unittest discover -s tests -t . -v»
    python tools/tempi-test.py --quanti 30
    python tools/tempi-test.py --a-moduli   # un processo per file: dice QUALE modulo muore

Serve a rispondere a una domanda che altrimenti si risponde a indovinelli: *dove* se ne va il
tempo. La CI lo usa al posto di unittest, così ogni esecuzione lascia scritto nel registro
quali test costano, sistema per sistema — e una lentezza che compare solo su una piattaforma
si vede subito invece di restare un numero in fondo alla pagina.

L'esito è lo stesso di unittest: 0 se è tutto verde. Se è rosso, le prove fallite vengono
ripetute anche come *annotazioni* di GitHub (``::error::``): il registro completo di una
esecuzione sta su un archivio che non è sempre raggiungibile, mentre le annotazioni si leggono
dalla pagina del controllo e dall'API. Il 20/10 la CI è rimasta rossa su Windows per mezza
giornata perché il motivo era scritto solo in un registro che non si riusciva a scaricare.
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

RADICE = Path(__file__).resolve().parents[1]


class RisultatoConTempi(unittest.TextTestResult):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.durate: dict[str, float] = {}
        self._inizio = 0.0

    def startTest(self, test):
        self._inizio = time.perf_counter()
        super().startTest(test)

    def stopTest(self, test):
        self.durate[str(test)] = time.perf_counter() - self._inizio
        super().stopTest(test)


def annotazione(testo: str) -> str:
    """Testo dentro un comando ``::error::``: i capoversi e il percento vanno protetti."""
    return testo.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def racconta_i_guai(esito, quante: int = 8, righe: int = 12) -> None:
    """Ripete le prove rosse come annotazioni, così si leggono senza scaricare il registro."""
    guai = [("fallita", t, m) for t, m in esito.failures] + [("errore", t, m) for t, m in esito.errors]
    print(f"\n== {len(esito.failures)} fallite, {len(esito.errors)} in errore")
    for genere, test, messaggio in guai[:quante]:
        titolo = str(test).replace(",", " ")
        ultime = "\n".join(str(messaggio).strip().splitlines()[-righe:])
        print(f"::error title={genere}: {titolo}::{annotazione(ultime)}")
    if len(guai) > quante:
        print(f"::error title=e non solo::altre {len(guai) - quante} prove rosse: "
              f"{annotazione(chr(10).join(str(t) for _g, t, _m in guai[quante:]))}")


def a_moduli(righe: int = 14) -> int:
    """Esegue ogni file di prove in un processo suo, e annota quale muore.

    Serve quando la suite non muore *dentro* una prova ma porta giù il processo: un crash di Qt, un
    interprete che se ne va, una libreria di sistema. In quel caso unittest non ha niente da
    raccontare e la CI resta rossa senza motivo leggibile — successo su Windows il 21/10, con i
    registri completi irraggiungibili da qui. Qui ogni modulo è isolato: quello che non torna indietro
    si vede per nome, con le sue ultime righe.
    """
    import subprocess  # noqa: PLC0415

    morti = 0
    for prova in sorted((RADICE / "tests").glob("test_*.py")):
        modulo = f"tests.{prova.stem}"
        fatto = subprocess.run([sys.executable, "-m", "unittest", modulo, "-v"], cwd=RADICE,
                               capture_output=True, text=True, errors="replace")
        uscita = (fatto.stdout or "") + (fatto.stderr or "")
        stato = "ok" if fatto.returncode == 0 else f"ROSSO (uscita {fatto.returncode})"
        print(f"== {modulo}: {stato}")
        if fatto.returncode != 0:
            morti += 1
            righe_uscita = uscita.strip().splitlines()
            # I nomi delle prove rosse stanno in cima al blocco, le ultime righe in fondo: servono
            # tutt'e due, perché l'annotazione è l'unica finestra sul registro (21/10).
            nomi = [r for r in righe_uscita if r.startswith(("ERROR:", "FAIL:"))][:4]
            ultime = "\n".join(nomi + righe_uscita[-righe:]) or "nessuna uscita"
            print(f"::error title=modulo {modulo} ({stato})::{annotazione(ultime)}")
    if not morti:
        print("::error title=nessun modulo rosso da solo::"
              "ognuno passa isolato: il guaio nasce dall'insieme (ordine, stato condiviso, risorse)")
    return 1 if morti else 0


def main(argv: list[str]) -> int:
    if "--a-moduli" in argv:
        return a_moduli()
    quanti = 15
    if "--quanti" in argv:
        quanti = int(argv[argv.index("--quanti") + 1])
    suite = unittest.defaultTestLoader.discover(str(RADICE / "tests"), top_level_dir=str(RADICE))
    runner = unittest.TextTestRunner(verbosity=2, resultclass=RisultatoConTempi)
    inizio = time.perf_counter()
    esito = runner.run(suite)
    totale = time.perf_counter() - inizio
    lenti = sorted(esito.durate.items(), key=lambda x: -x[1])[:quanti]
    print(f"\n== i {len(lenti)} test più lenti ({totale:.1f}s in tutto, "
          f"{sum(esito.durate.values()):.1f}s dentro i test)")
    for nome, durata in lenti:
        print(f"{durata:7.2f}s  {nome}")
    if not esito.wasSuccessful():
        racconta_i_guai(esito)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
