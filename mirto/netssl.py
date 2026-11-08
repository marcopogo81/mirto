# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Contesto TLS per le connessioni HTTPS e SMTP.

Il Python scaricato da python.org su macOS non usa i certificati del sistema
finché non si esegue "Install Certificates.command": ogni connessione HTTPS
fallirebbe (CERTIFICATE_VERIFY_FAILED). Per questo Mirto usa l'elenco di
autorità del pacchetto ``certifi`` quando è installato.
"""

from __future__ import annotations

import functools
import ssl


@functools.lru_cache(maxsize=1)
def ssl_context() -> ssl.SSLContext:
    try:
        import certifi  # noqa: PLC0415

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()
