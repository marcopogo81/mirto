# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Impedisce la sospensione del computer mentre Mirto è attivo.

* macOS: ``caffeinate -i -m -s -w <pid>`` (si chiude da solo quando Mirto termina).
  Attenzione: con il coperchio chiuso il Mac va comunque in stop, a meno che
  non sia collegato un monitor esterno.
* Windows: ``SetThreadExecutionState`` (va chiamato dal thread principale).
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys

log = logging.getLogger(__name__)

_proc: subprocess.Popen | None = None


def prevent_sleep(enable: bool) -> bool:
    global _proc
    try:
        if sys.platform == "darwin":
            if enable and _proc is None:
                _proc = subprocess.Popen(["caffeinate", "-i", "-m", "-s", "-w", str(os.getpid())])
                log.info("Sospensione del Mac disattivata finché Mirto è attivo")
            elif not enable and _proc is not None:
                _proc.terminate()
                _proc = None
            return True
        if sys.platform.startswith("win"):
            import ctypes  # noqa: PLC0415

            es_continuous, es_system_required = 0x80000000, 0x00000001
            flags = es_continuous | (es_system_required if enable else 0)
            ctypes.windll.kernel32.SetThreadExecutionState(flags)
            return True
    except Exception as exc:
        log.warning("Impossibile gestire la sospensione: %s", exc)
    return False
