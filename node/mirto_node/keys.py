# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Chiavi HMAC del nodo: una per ogni abbinamento.

Fino alla versione 0.5 il nodo aveva **una** chiave, scritta dall'installazione in
``/etc/mirto-node/chiave`` e consegnata a chi arrivava primo all'abbinamento: chiunque fosse sulla
rete di casa nei minuti giusti teneva per sempre la chiave di tutto il nodo.

Adesso ogni abbinamento riceve una chiave sua in ``/etc/mirto-node/chiavi/<cliente>.key``
(cartella 0700, file 0600), con accanto una scheda ``<cliente>.json`` (nome del cliente, indirizzo da
cui ha chiesto l'abbinamento, data): ``mirto-nodo abbina --elenco`` le mostra una per riga, così un
Mirto che non si riconosce salta all'occhio, e ``--revoca <cliente>`` ne stacca uno solo.

La chiave dell'installazione resta valida solo per il tempo del passaggio, perché i nodi abbinati con
quella continuino a funzionare: dal primo abbinamento con chiave propria il nodo chiede di ritirarla e,
se nessuno lo fa, la ritira da solo dopo ``INSTALL_RETIRE_DAYS`` giorni. Ritirarla significa cancellare
il file e lasciare ``chiave-revocata``, così nemmeno una reinstallazione la fa tornare.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

from .signing import generate_key

from . import fs

log = logging.getLogger(__name__)

KEY_LENGTH = 64          # 32 byte in esadecimale
INSTALL_RETIRE_DAYS = 7  # dal primo abbinamento con chiave propria
INSTALL_NAME = "installazione"


def slug(client: str) -> str:
    """«Mirto su mac-di-marco» → «mirto-su-mac-di-marco» (nome di file)."""
    out = re.sub(r"[^a-z0-9]+", "-", (client or "").lower()).strip("-")
    return out[:60] or "mirto"


class KeyStore:
    """Le chiavi che il nodo accetta: quelle degli abbinamenti e, durante il passaggio, quella dell'installazione."""

    def __init__(self, folder, install_key: Path | None = None, clock=time.time):
        self.folder = Path(folder)
        self.dir = self.folder / "chiavi"
        self.install_key = Path(install_key) if install_key is not None else self.folder / "chiave"
        self.revoked_flag = self.folder / "chiave-revocata"
        self.first_claim_file = self.dir / "primo-claim"
        self.clock = clock

    # --- lettura -------------------------------------------------------------
    def files(self) -> list[Path]:
        """Le chiavi degli abbinamenti, la più recente per prima."""
        try:
            return sorted(self.dir.glob("*.key"), key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            return []

    def entries(self) -> list[dict]:
        """Una scheda per abbinamento: ``{"nome", "cliente", "indirizzo", "quando"}``, la più recente per prima."""
        out = []
        for path in self.files():
            info = {}
            try:
                info = json.loads(path.with_suffix(".json").read_text())
            except (OSError, ValueError):
                pass
            try:
                when = int(info.get("quando") or path.stat().st_mtime)
            except OSError:
                when = 0
            out.append({"nome": path.stem, "cliente": str(info.get("cliente") or path.stem),
                        "indirizzo": str(info.get("indirizzo") or ""), "quando": when})
        return out

    def first_claim(self) -> int:
        """Quando è stato fatto il primo abbinamento con chiave propria (0 se mai)."""
        try:
            return int(self.first_claim_file.read_text().strip())
        except (OSError, ValueError):
            return 0

    def retire_at(self) -> int:
        """Quando la chiave dell'installazione si ritira da sola (0 se non c'è più o non è ancora partito il conto)."""
        first = self.first_claim()
        if not first or self.revoked_flag.exists() or not self.install_key.exists():
            return 0
        return first + INSTALL_RETIRE_DAYS * 86400

    def retire_in(self) -> int:
        """Secondi che mancano al ritiro automatico (0 se non è in programma)."""
        due = self.retire_at()
        return max(0, int(due - self.clock())) if due else 0

    def install_key_hex(self) -> str:
        """La chiave dell'installazione, se è ancora in uso. Scaduto il periodo di passaggio, la ritira."""
        if self.revoked_flag.exists():
            return ""
        due = self.retire_at()
        if due and self.clock() >= due:
            log.warning("Chiave dell'installazione ritirata da sola: sono passati %d giorni dal primo "
                        "abbinamento con chiave propria", INSTALL_RETIRE_DAYS)
            self.revoke_install()
            return ""
        try:
            key = self.install_key.read_text().strip()
        except OSError:
            return ""
        return key if len(key) == KEY_LENGTH else ""

    def keys(self) -> list[str]:
        """Tutte le chiavi accettate: prima le più recenti, in fondo quella dell'installazione."""
        out = []
        for path in self.files():
            try:
                key = path.read_text().strip()
            except OSError as exc:
                log.warning("Chiave %s non leggibile: %s", path.name, exc)
                continue
            if len(key) == KEY_LENGTH:
                out.append(key)
            else:
                log.warning("Chiave %s ignorata: non è lunga %d caratteri", path.name, KEY_LENGTH)
        old = self.install_key_hex()
        if old:
            out.append(old)
        return out

    def stamp(self) -> tuple:
        """Istantanea dello stato: cambia quando una chiave viene aggiunta, revocata o scade il passaggio."""
        def when(path: Path) -> int:
            try:
                return path.stat().st_mtime_ns
            except OSError:
                return 0
        due = self.retire_at()
        return (when(self.dir), when(self.install_key), self.revoked_flag.exists(),
                bool(due and self.clock() >= due))

    # --- scrittura -----------------------------------------------------------
    def create(self, client: str, address: str = "") -> str:
        """Nuova chiave solo per questo abbinamento (la restituisce in chiaro: va consegnata una volta sola)."""
        self.dir.mkdir(parents=True, exist_ok=True)
        self._chmod(self.dir, 0o700)
        path = self.dir / f"{slug(client)}.key"
        if path.exists():
            path = self.dir / f"{slug(client)}-{int(self.clock())}.key"
        key = generate_key()
        path.write_text(key + "\n")
        self._chmod(path, 0o600)
        info = {"cliente": str(client)[:80], "indirizzo": str(address)[:60], "quando": int(self.clock())}
        path.with_suffix(".json").write_text(json.dumps(info, ensure_ascii=False) + "\n")
        self._chmod(path.with_suffix(".json"), 0o600)
        if not self.first_claim_file.exists():
            # da qui parte il conto alla rovescia per il ritiro della chiave dell'installazione
            self.first_claim_file.write_text(str(int(self.clock())) + "\n")
            self._chmod(self.first_claim_file, 0o600)
        log.info("Chiave nuova per l'abbinamento «%s» da %s (%s)", client, address or "?", path.name)
        return key

    def revoke(self, name: str) -> int:
        """Stacca un abbinamento solo, per nome del file o del cliente. ``installazione`` ritira la chiave vecchia."""
        wanted = (name or "").strip()
        if slug(wanted) in (INSTALL_NAME, "chiave-installazione", "chiave"):
            return 1 if self.revoke_install() else 0
        count = 0
        for path in self.files():
            entry = next((e for e in self.entries() if e["nome"] == path.stem), {})
            if path.stem not in (wanted, slug(wanted)) and slug(entry.get("cliente", "")) != slug(wanted):
                continue
            count += self._remove(path)
        if count:
            log.warning("Abbinamento «%s» revocato: quel Mirto dovrà rifare l'abbinamento", wanted)
        return count

    def revoke_install(self) -> bool:
        """Ritira la chiave dell'installazione: la cancella e segna che non deve tornare."""
        had = self.install_key.exists()
        try:
            self.install_key.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("Chiave dell'installazione non cancellata: %s", exc)
            return False
        self.revoked_flag.write_text(f"ritirata il {time.strftime('%d/%m/%Y %H:%M')}\n")
        self._chmod(self.revoked_flag, 0o600)
        if had:
            log.warning("Chiave dell'installazione ritirata: il nodo non la accetta più")
        return had

    def revoke_all(self) -> int:
        """Stacca tutti gli abbinamenti e ritira la chiave dell'installazione."""
        count = sum(self._remove(path) for path in self.files())
        try:
            self.first_claim_file.unlink(missing_ok=True)
        except OSError:
            pass
        if self.revoke_install():
            count += 1
        log.warning("Revocate %d chiavi: i Mirto abbinati dovranno rifare l'abbinamento", count)
        return count

    # --- utilità -------------------------------------------------------------
    def _remove(self, path: Path) -> int:
        try:
            path.unlink()
        except OSError as exc:
            log.warning("Chiave %s non rimossa: %s", path.name, exc)
            return 0
        try:
            path.with_suffix(".json").unlink(missing_ok=True)
        except OSError:
            pass
        return 1

    @staticmethod
    def _chmod(path: Path, mode: int) -> None:
        """Permessi stretti e proprietario del servizio.

        «mirto-nodo» gira da root: senza questo, una chiave creata dalla riga di comando
        resterebbe di root e il servizio — che root non è — non riuscirebbe a leggerla.
        """
        try:
            path.chmod(mode)
        except OSError as exc:
            log.warning("Permessi di %s non impostati: %s", path.name, exc)
        fs.al_servizio(path)
