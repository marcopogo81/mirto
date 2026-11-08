# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Log su console e su file a rotazione nella cartella dati."""

from __future__ import annotations

import logging
import logging.handlers
import sys
import threading

from .config import data_dir


def installa_ganci_errori() -> None:
    """Scrive nel registro le eccezioni che nessuno ha catturato, anche quelle dei thread.

    Serve soprattutto nelle versioni pacchettizzate: lì non c'è una console dove il traceback
    possa comparire, quindi senza questi ganci un errore non previsto fa sparire la finestra (o
    morire un thread) senza lasciare niente da leggere — ed è proprio il caso in cui qualcuno ci
    scriverà «si è chiuso da solo» (Q-06).
    """
    log = logging.getLogger("mirto")

    def su_eccezione(tipo, valore, traccia):
        if issubclass(tipo, KeyboardInterrupt):   # Ctrl-C non è un errore da raccontare
            sys.__excepthook__(tipo, valore, traccia)
            return
        log.error("Errore non catturato", exc_info=(tipo, valore, traccia))

    def su_eccezione_thread(args):
        if issubclass(args.exc_type, SystemExit):
            return
        log.error("Errore non catturato nel thread %s", args.thread.name if args.thread else "?",
                  exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = su_eccezione
    threading.excepthook = su_eccezione_thread


class _SenzaIndirizzoSparito(logging.Filter):
    """Toglie dal registro l'avviso di zeroconf quando un indirizzo di rete sparisce.

    `Error with socket N (('169.254.x.y', 5353)): [Errno 49] Can't assign requested address`, con
    tutto il traceback appresso. Succede ogni volta che un'interfaccia cambia o se ne va — un cavo
    staccato, il Wi-Fi che si riattacca, l'indirizzo link-local del cavo diretto al nodo — e zeroconf
    la gestisce da sé: l'ha già scritta come avviso dopo averla risolta.

    Si toglie per la stessa ragione delle righe dell'anello (22/10) e dell'uscita audio (8/11): un
    avviso che compare da solo, non chiede niente a nessuno e si ripete, insegna a non leggere gli
    avvisi — e il prossimo, quello vero, passa inosservato in mezzo. Resta a livello `debug`, per chi
    lo va a cercare.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name.split(".")[0] != "zeroconf" or record.levelno > logging.WARNING:
            return True
        testo = str(record.getMessage())
        sparito = "Can't assign requested address" in testo or "Errno 49" in testo
        if sparito:
            record.levelno, record.levelname = logging.DEBUG, "DEBUG"
            record.exc_info = None          # il traceback di una cosa gestita non serve a nessuno
            return logging.getLogger().isEnabledFor(logging.DEBUG)
        return True


def zittisci_zeroconf() -> None:
    """Mette il filtro qui sopra su zeroconf, una volta sola."""
    log_zc = logging.getLogger("zeroconf")
    if not any(isinstance(f, _SenzaIndirizzoSparito) for f in log_zc.filters):
        log_zc.addFilter(_SenzaIndirizzoSparito())


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)
    log_dir = data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    # Prima di riaprire il registro: via le password rimaste dentro gli indirizzi nei file già
    # scritti. Dopo averlo riaperto non si potrebbe più, perché riscrivere un file che il programma
    # tiene aperto lascia spazzatura in mezzo. Vedi scrub_logs in netutil.py.
    from .netutil import scrub_logs  # noqa: PLC0415

    ripulite = scrub_logs(log_dir)
    file_h = logging.handlers.RotatingFileHandler(
        log_dir / "mirto.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    file_h.setFormatter(fmt)
    root.addHandler(file_h)
    zittisci_zeroconf()
    installa_ganci_errori()   # da qui un errore non catturato finisce nel registro, non nel nulla
    if ripulite:
        # Detto a voce alta: un registro già mandato a qualcuno resta mandato, quindi la password
        # va cambiata, non solo cancellata da qui.
        logging.getLogger(__name__).warning(
            "Ripulite %d righe dei registri: contenevano la password di una camera, scritta da una "
            "versione precedente. Se hai già condiviso un registro, cambia la password della camera "
            "(sul nodo: «sudo rm /etc/mirto-node/rtsp» e reinstalla).", ripulite)
