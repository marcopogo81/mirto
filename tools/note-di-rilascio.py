#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Le note di una versione, prese dal CHANGELOG.

    python3 tools/note-di-rilascio.py            # la versione corrente di Mirto
    python3 tools/note-di-rilascio.py 0.2.0      # una versione qualsiasi

Il workflow di release le usa come testo della Release su GitHub: così le note non si scrivono
due volte e non possono divergere dal CHANGELOG.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

RADICE = Path(__file__).resolve().parents[1]


def sezione(changelog: str, versione: str) -> str:
    """Il corpo di ``## [versione]``, senza l'intestazione, fino alla versione successiva."""
    inizio = re.search(rf"^##\s*\[{re.escape(versione)}\].*$", changelog, re.MULTILINE)
    if not inizio:
        raise LookupError(f"nel CHANGELOG non c'è una sezione per la versione {versione}")
    resto = changelog[inizio.end():]
    dopo = re.search(r"^##\s", resto, re.MULTILINE)
    return (resto[:dopo.start()] if dopo else resto).strip("\n")


def main(argv: list[str]) -> int:
    if argv:
        versione = argv[0].lstrip("v")
    else:
        sys.path.insert(0, str(RADICE))
        from mirto import __version__ as versione  # noqa: PLC0415
    try:
        print(sezione((RADICE / "CHANGELOG.md").read_text(encoding="utf-8"), versione))
    except LookupError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
