# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Controlla le traduzioni di Mirto.

Raccoglie i testi passati a ``_()`` e ``N_()`` nel codice e li confronta con
``mirto/locales/<lingua>.json``:

* testi senza traduzione;
* traduzioni con segnaposto diversi dall'originale (``{camera}``, ``{score:.0%}``…);
* traduzioni non più usate.

Uso::

    python tools/i18n_check.py                 # controlla tutte le lingue (esce con 1 se manca qualcosa)
    python tools/i18n_check.py --missing en    # stampa in JSON i testi da tradurre in inglese
    python tools/i18n_check.py --prune en      # toglie dal catalogo le traduzioni non più usate
"""

from __future__ import annotations

import ast
import json
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "mirto"
LOCALES = PKG / "locales"


def collect() -> dict[str, list[str]]:
    """Testo da tradurre -> dove compare (file:riga)."""
    out: dict[str, list[str]] = {}
    for path in sorted(PKG.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("_", "N_")
                    and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                out.setdefault(node.args[0].value, []).append(f"{path.relative_to(ROOT)}:{node.lineno}")
    return out


def fields(text: str) -> set[str]:
    try:
        return {f"{name}{'!' + conv if conv else ''}{':' + spec if spec else ''}"
                for _lit, name, spec, conv in string.Formatter().parse(text) if name is not None}
    except ValueError:
        return {"<formato non valido>"}


def check(lang: str, texts: dict[str, list[str]]) -> list[str]:
    path = LOCALES / f"{lang}.json"
    cat = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    problems = []
    for t, where in texts.items():
        tr = cat.get(t)
        if not tr:
            problems.append(f"[{lang}] manca: {t[:70]!r} ({where[0]})")
        elif fields(t) != fields(tr):
            problems.append(f"[{lang}] segnaposto diversi: {t[:50]!r} {sorted(fields(t))} -> {sorted(fields(tr))}")
    for t in cat:
        if t not in texts:
            problems.append(f"[{lang}] non più usata: {t[:70]!r}")
    return problems


def main(argv: list[str]) -> int:
    texts = collect()
    if len(argv) == 2 and argv[0] == "--missing":
        path = LOCALES / f"{argv[1]}.json"
        cat = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        print(json.dumps({t: "" for t in texts if not cat.get(t)}, ensure_ascii=False, indent=1))
        return 0
    if len(argv) == 2 and argv[0] == "--prune":
        path = LOCALES / f"{argv[1]}.json"
        cat = json.loads(path.read_text(encoding="utf-8"))
        kept = {t: v for t, v in cat.items() if t in texts}
        path.write_text(json.dumps(kept, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"tolte {len(cat) - len(kept)} traduzioni non più usate")
        return 0
    problems = []
    for p in sorted(LOCALES.glob("*.json")):
        problems += check(p.stem, texts)
    for line in problems:
        print(line)
    print(f"{len(texts)} testi, {len(problems)} problemi")
    return 1 if any("manca" in x or "segnaposto" in x for x in problems) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
