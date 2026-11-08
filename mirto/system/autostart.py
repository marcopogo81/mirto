# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Avvio automatico all'accesso dell'utente, con riavvio in caso di arresto anomalo.

* macOS: LaunchAgent ``~/Library/LaunchAgents/it.mirto.app.plist`` con
  ``KeepAlive`` (launchd riavvia Mirto se si chiude in modo anomalo).
* Windows: valore nel registro ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``.
"""

from __future__ import annotations

import logging
import plistlib
import subprocess
import sys
from pathlib import Path

from ..config import data_dir
from ..piattaforma import sistema
from ..i18n import _

log = logging.getLogger(__name__)

LABEL = "it.mirto.app"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def launch_command() -> list[str]:
    """Comando per avviare Mirto: l'eseguibile impacchettato oppure ``python -m mirto``."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "mirto"]


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def is_enabled() -> bool:
    if sistema() == "darwin":
        return _plist_path().exists()
    if sistema().startswith("win"):
        import winreg  # noqa: PLC0415

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                winreg.QueryValueEx(k, "Mirto")
                return True
        except OSError:
            return False
    return False


def set_enabled(enable: bool) -> str:
    cmd = launch_command()
    if sistema() == "darwin":
        path = _plist_path()
        if enable:
            logs = data_dir() / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            plist = {
                "Label": LABEL,
                "ProgramArguments": cmd,
                "RunAtLoad": True,
                "KeepAlive": {"SuccessfulExit": False},  # riavvia solo dopo un arresto anomalo
                "ThrottleInterval": 30,
                "WorkingDirectory": str(Path.cwd()),
                "StandardOutPath": str(logs / "launchd.out.log"),
                "StandardErrorPath": str(logs / "launchd.err.log"),
                "ProcessType": "Interactive",
            }
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "wb") as f:
                plistlib.dump(plist, f)
            subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
            subprocess.run(["launchctl", "load", str(path)], capture_output=True)
            return _('Avvio automatico attivato.')
        if path.exists():
            subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
            path.unlink()
        return _('Avvio automatico disattivato.')
    if sistema().startswith("win"):
        import winreg  # noqa: PLC0415

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if enable:
                value = " ".join(f'"{c}"' for c in cmd)
                winreg.SetValueEx(k, "Mirto", 0, winreg.REG_SZ, value)
                return _('Avvio automatico attivato.')
            try:
                winreg.DeleteValue(k, "Mirto")
            except OSError:
                pass
            return _('Avvio automatico disattivato.')
    return _('Avvio automatico non supportato su questo sistema.')
