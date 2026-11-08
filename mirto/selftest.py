# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Esecuzione dei test automatici dall'interfaccia (menu Prove)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from .i18n import _

ROOT = Path(__file__).resolve().parents[1]


def tests_available() -> bool:
    return (ROOT / "tests").is_dir()


def run_unit_tests(timeout: float = 600) -> tuple[bool, str]:
    """Esegue ``python -m unittest`` sulla cartella tests. Restituisce (riusciti, testo del risultato)."""
    if not tests_available():
        return False, _('Cartella dei test non trovata (versione impacchettata): i test si eseguono dal codice sorgente.')
    with tempfile.TemporaryDirectory(prefix="mirto-test-") as tmp:
        # cartelle temporanee: i test non toccano mai la configurazione e i dati reali
        env = dict(os.environ, MIRTO_CONFIG_DIR=str(Path(tmp) / "cfg"), MIRTO_DATA_DIR=str(Path(tmp) / "data"))
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
            cwd=ROOT, capture_output=True, text=True, timeout=timeout, env=env,
        )
    out = (proc.stdout + "\n" + proc.stderr).strip()
    # tieni il riepilogo finale e gli eventuali errori, non tutto il log
    lines = [ln for ln in out.splitlines() if not ln.startswith(("20", "Portachiavi"))]
    summary = "\n".join(lines[-60:])
    return proc.returncode == 0, summary
