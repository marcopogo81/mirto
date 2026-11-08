# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Firma HMAC-SHA256 delle richieste verso i nodi (e verifica, lato nodo).

Testo firmato (righe separate da ``\\n``)::

    METODO
    /percorso?query
    marca temporale (secondi Unix)
    nonce (valore casuale univoco)
    SHA-256 esadecimale del corpo

Intestazioni: ``X-Mirto-Timestamp``, ``X-Mirto-Nonce``, ``X-Mirto-Signature``
(esadecimale). Il nodo rifiuta richieste con orario fuori tolleranza (±30 s)
o con un nonce già visto (protezione dalla ripetizione).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time

TOLERANCE_SECONDS = 30
H_TS, H_NONCE, H_SIG = "X-Mirto-Timestamp", "X-Mirto-Nonce", "X-Mirto-Signature"


def generate_key() -> str:
    """Nuova chiave condivisa (64 caratteri esadecimali = 256 bit)."""
    return secrets.token_hex(32)


def _canonical(method: str, path: str, ts: str, nonce: str, body: bytes) -> bytes:
    return "\n".join([method.upper(), path, ts, nonce, hashlib.sha256(body or b"").hexdigest()]).encode()


def sign(key_hex: str, method: str, path: str, body: bytes = b"", now: float | None = None) -> dict[str, str]:
    ts = str(int(now if now is not None else time.time()))
    nonce = secrets.token_hex(16)
    sig = hmac.new(bytes.fromhex(key_hex), _canonical(method, path, ts, nonce, body), hashlib.sha256).hexdigest()
    return {H_TS: ts, H_NONCE: nonce, H_SIG: sig}


def as_keys(keys) -> list[bytes]:
    """Una chiave esadecimale o un elenco di chiavi → byte, nell'ordine ricevuto."""
    if isinstance(keys, (str, bytes)):
        keys = [keys]
    return [k if isinstance(k, bytes) else bytes.fromhex(k) for k in keys]


class Verifier:
    """Verifica lato nodo, con memoria dei nonce già usati.

    Accetta più chiavi: il nodo ne ha una per ogni abbinamento (più, finché non viene revocata,
    quella scritta dall'installazione). Si provano tutte, dalla più recente alla più vecchia,
    con lo stesso tempo di calcolo per ciascuna.
    """

    def __init__(self, keys, tolerance: int = TOLERANCE_SECONDS):
        self.keys = as_keys(keys)
        self.tolerance = tolerance
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def set_keys(self, keys) -> None:
        """Sostituisce le chiavi accettate (dopo un abbinamento nuovo o una revoca)."""
        with self._lock:
            self.keys = as_keys(keys)

    def precheck(self, headers, now: float | None = None) -> tuple[bool, str]:
        """I controlli che si possono fare con i soli header, **prima** di leggere il corpo.

        Serve al nodo (S-04): senza questo, chi non ha nessuna chiave può far leggere al server
        megabyte di corpo — o tenere aperta una connessione lenta promettendone uno che non manda
        mai — solo per sentirsi rispondere «firma non valida». Qui bastano tre intestazioni per
        dire no, e il corpo non si tocca.
        """
        now = now if now is not None else time.time()
        ts, nonce, sig = headers.get(H_TS), headers.get(H_NONCE), headers.get(H_SIG)
        if not (ts and nonce and sig):
            return False, "firma mancante"
        try:
            age = abs(now - int(ts))
        except ValueError:
            return False, "marca temporale non valida"
        if age > self.tolerance:
            return False, "richiesta scaduta (controlla l'orologio dei dispositivi)"
        return True, ""

    def verify(self, method: str, path: str, headers, body: bytes, now: float | None = None) -> tuple[bool, str]:
        now = now if now is not None else time.time()
        ok, why = self.precheck(headers, now)
        if not ok:
            return False, why
        ts, nonce = headers.get(H_TS), headers.get(H_NONCE)
        sig = headers.get(H_SIG)
        with self._lock:
            keys = list(self.keys)
        canonical = _canonical(method, path, ts, nonce, body)
        ok = False
        for key in keys:
            # niente scorciatoie: ogni chiave costa lo stesso, qualunque sia quella giusta
            ok = hmac.compare_digest(hmac.new(key, canonical, hashlib.sha256).hexdigest(), sig) or ok
        if not ok:
            return False, "firma non valida"
        with self._lock:
            for n, t in list(self._seen.items()):
                if now - t > 2 * self.tolerance:
                    del self._seen[n]
            if nonce in self._seen:
                return False, "richiesta ripetuta"
            self._seen[nonce] = now
        return True, ""
