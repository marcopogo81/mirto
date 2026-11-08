# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Traduzioni dell'interfaccia e dei messaggi.

Il testo di partenza è l'italiano scritto nel codice: ``_("Camera spenta")``
restituisce la traduzione nella lingua scelta, oppure il testo stesso se manca.
I segnaposto usano la sintassi di ``str.format``:
``_("Foto salvata in {f}", f=path)``.

Le stringhe definite a livello di modulo (che verrebbero tradotte una sola
volta, all'importazione) si marcano con ``N_()`` e si traducono dove si usano.

I cataloghi sono file JSON in ``mirto/locales/<lingua>.json``
(``{"testo italiano": "traduzione"}``). ``tools/i18n_check.py`` controlla che
ogni testo abbia la sua traduzione e gli stessi segnaposto.
"""

from __future__ import annotations

import json
import locale
import logging
import os
import subprocess
import threading
from pathlib import Path

from .piattaforma import sistema

log = logging.getLogger(__name__)

SOURCE_LANGUAGE = "it"
LOCALES_DIR = Path(__file__).with_name("locales")
# Lingue proposte nelle Impostazioni: codice -> nome nella lingua stessa
LANGUAGES = {"auto": "Automatica", "it": "Italiano", "en": "English"}

_lock = threading.Lock()
_current = SOURCE_LANGUAGE
_catalogs: dict[str, dict[str, str]] = {}


def available_languages() -> list[str]:
    """Lingue con un catalogo (più l'italiano, che è il testo di partenza)."""
    found = {p.stem for p in LOCALES_DIR.glob("*.json")} if LOCALES_DIR.exists() else set()
    return [SOURCE_LANGUAGE] + sorted(found - {SOURCE_LANGUAGE})


def _load(lang: str) -> dict[str, str]:
    with _lock:
        if lang not in _catalogs:
            path = LOCALES_DIR / f"{lang}.json"
            try:
                _catalogs[lang] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            except (OSError, ValueError) as exc:  # un catalogo rovinato non deve fermare l'app
                log.error("Catalogo %s illeggibile: %s", path, exc)
                _catalogs[lang] = {}
        return _catalogs[lang]


def system_language() -> str:
    """Lingua del sistema operativo, ridotta a due lettere (``it``, ``en``…)."""
    candidates: list[str] = []
    if sistema() == "darwin":  # il Terminale spesso ha LANG=en_US anche su un Mac in italiano
        try:
            out = subprocess.run(["defaults", "read", "-g", "AppleLanguages"], capture_output=True,
                                 text=True, timeout=2).stdout
            candidates += [x.strip(' "\n,()') for x in out.split("\n") if x.strip(' "\n,()')]
        except (OSError, subprocess.SubprocessError):
            pass
    if sistema() == "win32":  # Windows: lingua dell'interfaccia utente ("en_US", non "English_United States")
        try:
            import ctypes  # noqa: PLC0415

            candidates.append(locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage(), ""))
        except Exception:  # noqa: BLE001
            pass
    candidates += [os.environ.get(k, "") for k in ("LC_ALL", "LC_MESSAGES", "LANG")]
    try:
        candidates.append(locale.getlocale()[0] or "")
    except ValueError:
        pass
    known = available_languages()
    names = {"english": "en", "italian": "it", "italiano": "it"}  # nomi lunghi (es. locale di Windows)
    for c in candidates:
        code = c.replace("-", "_").split("_")[0].split(".")[0].lower()
        code = names.get(code, code)
        if code in known:
            return code
    return SOURCE_LANGUAGE


def resolve(lang: str | None) -> str:
    lang = (lang or "auto").strip().lower()
    if lang == "auto":
        return system_language()
    return lang if lang in available_languages() else SOURCE_LANGUAGE


def set_language(lang: str | None) -> str:
    """Imposta la lingua (``auto`` = quella del sistema) e restituisce il codice scelto."""
    global _current
    code = resolve(lang)
    if code != SOURCE_LANGUAGE:
        _load(code)
    _current = code
    return code


def get_language() -> str:
    return _current


def _(text: str, /, **kwargs) -> str:
    """Traduce ``text`` nella lingua corrente e, se servono, inserisce i valori."""
    out = text
    if _current != SOURCE_LANGUAGE:
        out = _load(_current).get(text) or text
    if kwargs:
        try:
            return out.format(**kwargs)
        except (KeyError, IndexError, ValueError):  # traduzione con segnaposto sbagliati: si usa l'originale
            return text.format(**kwargs)
    return out


def N_(text: str, /) -> str:
    """Marca un testo da tradurre più tardi (costanti di modulo): lo restituisce invariato."""
    return text


def questo_computer() -> str:
    """Come chiamare, in un messaggio, il computer su cui Mirto sta girando.

    Fino al 19 ottobre l'interfaccia diceva «Mac» dappertutto — «gli eventi restano su questo Mac»,
    «il microfono del Mac», «il Mac è collegato al Wi-Fi?» — perché era stata scritta e provata solo
    lì. Al primo avvio su un PC Windows vero (W-01) il programma parlava all'utente di un computer
    che non possiede: una dozzina di frasi che dicono «questo non è per te» nei primi dieci secondi.

    Il nome del sistema resta dov'è utile («macOS chiede il permesso»), ma la parola che indica *il
    computer di chi legge* ora segue il sistema. Fuori da macOS e Windows resta «computer», che è
    vero ovunque.
    """
    if sistema() == "darwin":
        return _("Mac")
    if sistema().startswith("win"):
        return _("PC")
    return _("computer")
