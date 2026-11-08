# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Il pacchetto del nodo: quello che Mirto manda a un nodo per aggiornarlo (N-02).

Fino al 25/10 un nodo si aggiornava da Terminale, con `privato/aggiorna-nodo.sh`: rsync del codice,
`install.sh`, password sudo. Va bene per un nodo in casa di chi l'ha scritto; con cinque nodi è un
pomeriggio, con cinquanta in case altrui non si fa — e in due giorni è già successo tre volte, perché
l'API del nodo è passata dalla 1.10 alla 1.12.

**Il pacchetto lo manda Mirto**, non lo scarica il nodo. La ragione non è la comodità: fra i due la
fiducia esiste già e non è da inventare. L'abbinamento ha lasciato una chiave, ogni richiesta è
firmata, e la firma copre l'impronta del corpo — quindi quei byte arrivano da chi ha la chiave di quel
nodo e da nessun altro. Scaricarli da internet vorrebbe dire una chiave di rilascio nuova da creare e
custodire (la stessa decisione sospesa di W-05) e internet sul nodo, che non è detto che ci sia.

E c'è un secondo motivo, che si vede solo usandolo: il codice che Mirto manda è **quello che ha
dentro**, quindi dopo l'aggiornamento il nodo parla esattamente l'API che quell'app sa usare. Un nodo
che si aggiorna per conto suo può ritrovarsi più avanti dell'app che lo comanda, e il programma
dovrebbe sapersela cavare con un nodo del futuro — un problema che qui non esiste.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import sys
import tarfile
from pathlib import Path

# Quello che serve al nodo per funzionare. Il resto della cartella (prove, appunti, immagini) non si
# manda: un pacchetto è anche quello che qualcuno leggerà per sapere cosa gira sul suo Raspberry.
# Quello che `install.sh` cerca **accanto a sé** più il programma: se ne manca uno, l'installazione
# muore al passo 2 con `set -e` e il nodo resta com'era. Il 26/10 mancavano `prepare_image.sh` e
# `node.toml.example`, e non si era mai visto perché fino a quel giorno nessun `/aggiorna` aveva mai
# installato davvero: la 0.12.0 era stata messa a mano con rsync, che manda la cartella intera. Il
# primo aggiornamento vero è fallito con «l'installazione non è andata a buon fine» e nient'altro.
# Adesso l'elenco lo controlla una prova, che lo rilegge da `install.sh`.
DA_MANDARE = ("mirto_node", "install.sh", "boot-segnali.sh", "firstboot.sh", "prepare_image.sh",
              "node.toml.example", "99-mirto-leds.rules", "README.md", "mirto-nodo")
SALTA = ("__pycache__", ".pyc", ".pyo", ".DS_Store")
MAX_PACCHETTO = 12 * 1024 * 1024      # lo stesso tetto che ha il nodo: meglio scoprirlo prima


def cartella_del_nodo() -> Path | None:
    """Dove sta il codice del nodo: nel progetto, o dentro il pacchetto dell'app.

    Se non c'è, questa copia di Mirto non può aggiornare nessun nodo — e lo deve **dire**, invece
    di offrire un pulsante che non può funzionare.
    """
    radici = [Path(__file__).resolve().parents[2]]
    if getattr(sys, "_MEIPASS", None):
        radici.insert(0, Path(sys._MEIPASS))
    for radice in radici:
        dove = radice / "node"
        if (dove / "mirto_node" / "__init__.py").is_file():
            return dove
    return None


def versione_che_abbiamo(dove: Path | None = None) -> str:
    """La versione del nodo che questa copia di Mirto porta con sé. Vuota se non ce l'ha.

    Si legge dal file, non si importa: importare `mirto_node` dentro Mirto vorrebbe dire avere in
    memoria due programmi che si chiamano quasi uguale, e prima o poi uno prende il posto dell'altro.
    """
    dove = dove or cartella_del_nodo()
    if dove is None:
        return ""
    try:
        testo = (dove / "mirto_node" / "__init__.py").read_text(encoding="utf-8")
    except OSError:
        return ""
    for riga in testo.splitlines():
        if riga.startswith("__version__"):
            return riga.split("=", 1)[1].strip().strip('"\'')
    return ""


def _da_saltare(p: Path) -> bool:
    return any(s in p.parts or p.name.endswith(s) for s in SALTA)


def fai_pacchetto(dove: Path | None = None) -> tuple[bytes, str, str]:
    """``(byte, versione, sha256)``: il codice del nodo pronto da mandare.

    L'archivio è **deterministico** — stessi file, stesso risultato — perché le date e i proprietari
    si azzerano: così due computer che mandano lo stesso codice producono la stessa impronta, e
    l'impronta diventa un modo per dire «questi due nodi hanno la stessa roba dentro».
    """
    dove = dove or cartella_del_nodo()
    if dove is None:
        raise FileNotFoundError("questa copia di Mirto non ha il codice del nodo")
    pezzi: list[tuple[str, Path]] = []
    for nome in DA_MANDARE:
        p = dove / nome
        if p.is_dir():
            pezzi += [(str(f.relative_to(dove)), f) for f in sorted(p.rglob("*"))
                      if f.is_file() and not _da_saltare(f)]
        elif p.is_file():
            pezzi.append((nome, p))
    if not any(n == "install.sh" for n, _ in pezzi):
        raise FileNotFoundError("manca install.sh: non è il codice di un nodo")
    buf = io.BytesIO()
    # Il tar **senza** compressione, e la compressione dopo con `mtime=0`. Scritto `w:gz`, tarfile
    # lascia che gzip si metta nell'intestazione **l'ora di adesso**: due pacchetti fatti dallo
    # stesso codice in due secondi diversi avevano byte diversi e quindi impronte diverse, cioè
    # esattamente il contrario di quello che questa funzione promette. Si vedeva come una prova
    # capricciosa — rossa una volta nella suite, verde da sola — perché due chiamate di fila
    # cadono quasi sempre nello stesso secondo. Non era un capriccio: era l'impronta che non valeva
    # niente, e l'unica cosa che la prova sapeva dire era «a volte».
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.GNU_FORMAT) as tar:
        for nome, p in sorted(pezzi):
            info = tarfile.TarInfo(nome)
            try:
                dati = p.read_bytes()
            except OSError:
                # Sparito fra l'elenco e la lettura. Si impacchetta una cartella **viva**: può
                # succedere (un editor che salva, un `__pycache__` che nasce e muore), e un
                # aggiornamento del nodo non può morire per questo. Il file semplicemente non c'è.
                continue
            info.size = len(dati)
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            # Il bit di esecuzione conta: `install.sh` e `boot-segnali.sh` li lancia root, e un file
            # arrivato senza quel bit è un aggiornamento che fallisce a metà.
            info.mode = 0o755 if nome.endswith(".sh") or nome == "mirto-nodo" else 0o644
            tar.addfile(info, io.BytesIO(dati))
    crudo = gzip.compress(buf.getvalue(), compresslevel=9, mtime=0)
    if len(crudo) > MAX_PACCHETTO:
        raise ValueError(f"il pacchetto pesa {len(crudo) // 1024} KB: il nodo non lo accetterebbe")
    return crudo, versione_che_abbiamo(dove), hashlib.sha256(crudo).hexdigest()


def aggiornamento_del_nodo(info: dict | None) -> tuple[str, str] | None:
    """``(versione sul nodo, versione che abbiamo)`` se c'è un aggiornamento, altrimenti ``None``.

    Una domanda sola, con una risposta sola, per i tre posti che la fanno: la riga del nodo in
    *Sistema*, la scheda del nodo in Impostazioni e il pulsante dentro «Stato e diagnosi». Finché
    ognuno se la rispondeva per conto suo — e per un giorno è stato così — bastava che uno dei tre
    confrontasse le versioni in un altro modo per dire il contrario degli altri due.

    Marco, 25/10: *«se c'è un aggiornamento disponibile per il nodo devo saperlo sia da NODI che da
    Sistema»*. Saperlo in due posti vuol dire **chiederlo in un posto solo**.
    """
    sul_nodo = str((dict(info or {})).get("versione") or "")
    da_noi = versione_che_abbiamo()
    if not da_noi or not piu_vecchio(sul_nodo, da_noi):
        return None
    return sul_nodo, da_noi


def piu_vecchio(sul_nodo: str, da_noi: str) -> bool:
    """Il nodo ha una versione più vecchia della nostra? Confronto numero per numero.

    `float("0.10")` fa 0,1 — meno di `float("0.9")`. È lo stesso scivolone che il 24/10 aveva fatto
    sparire l'avviso «nodo vecchio» per tutti, e si evita contando i numeri uno per uno.
    """
    def pezzi(v: str) -> tuple:
        fuori = []
        for p in str(v or "").split("."):
            try:
                fuori.append(int(p))
            except ValueError:
                break
        return tuple(fuori)

    a, b = pezzi(sul_nodo), pezzi(da_noi)
    return bool(a and b and a < b)
