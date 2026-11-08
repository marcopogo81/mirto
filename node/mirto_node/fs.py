# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Scritture nella cartella di configurazione, con il proprietario giusto.

Il servizio gira come utente ``mirto-node`` (azione S-03), ma ``mirto-nodo`` — la riga di
comando — gira da root, perché deve poter installare e riparare. Un file di stato scritto da root
resta di root e, con i permessi 0600 che questi file devono avere, il servizio non riesce più a
leggerlo: un PIN che non si legge è un PIN che non c'è, e l'abbinamento risponde «scaduto».

Qui si scrive e, se chi scrive è root, si restituisce il file al servizio.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

UTENTE = "mirto-node"
chown = shutil.chown          # sostituibile nei test


def da_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def al_servizio(percorso) -> None:
    """Dà il file all'utente del servizio. Senza privilegi non serve: è già suo."""
    if not da_root():
        return
    try:
        chown(percorso, UTENTE, UTENTE)
    except (LookupError, OSError):
        pass  # utente assente (prove sul Mac) o file system senza proprietari


def scrivi(percorso, testo: str, modo: int = 0o600) -> None:
    """Scrive un file di stato del nodo: permessi stretti, proprietario del servizio, **e su disco davvero**.

    Il 20/10, staccando la corrente a un nodo nel minuto dopo l'accensione, «pin» e «abbina_fino» sono
    rimasti da **zero byte**: il nome del file era registrato, il contenuto no. Un PIN vuoto è peggio di
    un PIN assente, perché la finestra sembra aperta e l'abbinamento risponde «PIN scaduto»: su un nodo
    di un tester, senza terminale, vuol dire riscrivere la microSD.

    Quindi: si scrive in un file accanto, si forza la scrittura (``fsync``), si rinomina — la rinomina è
    atomica, il file o è quello vecchio o è quello nuovo, mai mezzo — e si forza anche la cartella,
    altrimenti il nome nuovo può non essere ancora sul disco.
    """
    p = Path(percorso)
    tmp = p.with_name(p.name + ".in-corso")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, modo)
        with os.fdopen(fd, "w") as f:
            f.write(testo)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:  # la rinomina sta nella cartella: anche quella va spinta sul disco
        cartella_fd = os.open(p.parent, os.O_RDONLY)
        try:
            os.fsync(cartella_fd)
        finally:
            os.close(cartella_fd)
    except OSError:
        pass
    try:
        os.chmod(p, modo)
    except OSError:
        pass
    al_servizio(p)


def cartella(percorso, modo: int = 0o700) -> Path:
    """Crea una cartella di stato del nodo, con lo stesso criterio."""
    p = Path(percorso)
    p.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p, modo)
    except OSError:
        pass
    al_servizio(p)
    return p


def svuota(percorso) -> Path:
    """Cartella vuota, creata se manca: il posto dove scompattare qualcosa che arriva da fuori.

    Si svuota invece di sovrascrivere perché un pacchetto nuovo con meno file dell'altro lascerebbe
    in giro i file di prima, e quello che verrebbe installato non sarebbe né il vecchio né il nuovo.
    """
    p = Path(percorso)
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)
    return cartella(p)


def scompatta(crudo: bytes, dove, max_file: int = 4 * 1024 * 1024, max_totale: int = 64 * 1024 * 1024) -> int:
    """Apre un ``.tar.gz`` arrivato da fuori dentro ``dove``, e **solo** dentro. Torna quanti file.

    Un archivio è un elenco di nomi scelti da chi l'ha fatto: può contenere ``../../etc/passwd``, un
    collegamento che punta fuori, un file da dieci gigabyte che riempie la microSD, o un dispositivo.
    Qui passano soltanto file normali e cartelle, con un nome relativo che resta sotto ``dove`` una
    volta risolto. Tutto il resto si rifiuta per nome, **prima** di scrivere un byte.
    """
    import io  # noqa: PLC0415
    import tarfile  # noqa: PLC0415

    radice = Path(dove).resolve()
    totale = 0
    quanti = 0
    with tarfile.open(fileobj=io.BytesIO(crudo), mode="r:gz") as tar:
        buoni = []
        for m in tar.getmembers():
            if not (m.isfile() or m.isdir()):
                raise ValueError(f"«{m.name}» non è un file né una cartella")
            meta = (radice / m.name).resolve()
            if meta != radice and radice not in meta.parents:
                raise ValueError(f"«{m.name}» finirebbe fuori dalla cartella")
            if m.isfile():
                if m.size > max_file:
                    raise ValueError(f"«{m.name}» è troppo grande")
                totale += m.size
                quanti += 1
                if totale > max_totale:
                    raise ValueError("l'archivio aperto è troppo grande")
            buoni.append(m)
        for m in buoni:
            tar.extract(m, radice, filter="data")
    al_servizio(radice)
    return quanti
