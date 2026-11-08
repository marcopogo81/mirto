# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Richieste HTTP con la sola libreria standard (nessuna dipendenza esterna)."""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass

from ..netssl import ssl_context


@dataclass
class Response:
    status: int
    body: bytes

    def json(self):
        return json.loads(self.body.decode("utf-8") or "null")

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


def request(method: str, url: str, data: bytes | None = None, headers: dict | None = None,
            timeout: float = 30, context: ssl.SSLContext | None = None) -> Response:
    req = urllib.request.Request(url, data=data, method=method.upper(), headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=context or ssl_context()) as resp:  # noqa: S310
            return Response(resp.status, resp.read())
    except urllib.error.HTTPError as exc:  # risposta HTTP con codice di errore: la restituiamo
        return Response(exc.code, exc.read() or b"")


def multipart(fields: dict[str, str], files: dict[str, tuple[str, bytes, str]]) -> tuple[bytes, str]:
    """Corpo ``multipart/form-data``. ``files``: nome → (nome file, contenuto, tipo MIME)."""
    boundary = "----mirto" + uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    for name, (filename, content, mime) in files.items():
        parts.append(
            (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
             f"Content-Type: {mime}\r\n\r\n").encode() + content + b"\r\n"
        )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"
