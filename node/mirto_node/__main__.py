# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Avvio del nodo: ``python3 -m mirto_node --config /etc/mirto-node/node.toml``.

Altri comandi utili sul Raspberry:

* ``--impronta``   mostra l'impronta del certificato (da confrontare in Mirto)
* ``--prova-sirena 3``  fa suonare la sirena per 3 secondi, senza passare da Mirto
* ``--prova-suono rapace``  fa sentire un suono del dissuasore (``--prova-suono elenco`` per vederli)
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import signal
import ssl
import sys
import threading
from pathlib import Path

from .audio import AudioPlayer, load_generated, load_sounds, siren_pcm
from .keys import KeyStore
from . import mixer
from .server import (DeterrentDevice, GeneratedAudioDevice, LedDevice, Node, NodeServer,
                     PairingWindow, SirenDevice)

DEFAULT_DIR = Path("/etc/mirto-node")


def load_config(path: Path) -> dict:
    import tomllib  # noqa: PLC0415  (Python 3.11+)

    with open(path, "rb") as f:
        return tomllib.load(f)


def fingerprint(cert: Path) -> str:
    der = ssl.PEM_cert_to_DER_cert(cert.read_text())
    h = hashlib.sha256(der).hexdigest().upper()
    return ":".join(h[i:i + 2] for i in range(0, len(h), 2))


def make_player(cfg: dict) -> AudioPlayer:
    a = cfg.get("audio", {})
    return AudioPlayer(output=a.get("uscita", "bluetooth"), bt_mac=a.get("bluetooth_mac", ""),
                       card=a.get("scheda", ""), keep_alive_minutes=float(a.get("tieni_sveglia_minuti", 4)),
                       jack=bool(a.get("jack", True)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="mirto_node")
    ap.add_argument("--config", type=Path, default=DEFAULT_DIR / "node.toml")
    ap.add_argument("--impronta", action="store_true", help="mostra l'impronta del certificato ed esce")
    ap.add_argument("--prova-sirena", type=float, metavar="SECONDI", help="suona la sirena ed esce")
    ap.add_argument("--prova-suono", metavar="NOME", help="fa sentire un suono del dissuasore ed esce")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=sys.stdout)
    folder = args.config.parent
    cert, key_file = folder / "node.crt", folder / "node.key"
    if args.impronta:
        print(fingerprint(cert))
        return 0
    cfg = load_config(args.config)
    player = make_player(cfg)
    volume = int(cfg.get("audio", {}).get("volume", 80))
    if args.prova_sirena:
        secs = min(float(args.prova_sirena), 10.0)
        print(f"Sirena di prova per {secs:.0f} s su {player.device}…", flush=True)
        player.loop(siren_pcm(4.0, volume, player.rate), secs)
        player._thread.join(secs + 5)
        print("Fatto." if not player.last_error else f"Errore: {player.last_error}")
        return 0 if not player.last_error else 1

    det = cfg.get("dissuasore", {})
    sounds_dir = Path(det.get("cartella_suoni", str(folder / "suoni")))
    if args.prova_suono:
        sounds = load_sounds(sounds_dir, int(det.get("volume", 90)), player.rate)
        if args.prova_suono not in sounds:
            print("Suoni disponibili: " + ", ".join(sorted(sounds)))
            return 0 if args.prova_suono == "elenco" else 1
        secs = len(sounds[args.prova_suono]) / (2 * player.rate)
        print(f"Suono «{args.prova_suono}» ({secs:.1f} s) su {player.device}…", flush=True)
        player.loop(sounds[args.prova_suono], secs)
        player._thread.join(secs + 5)
        print("Fatto." if not player.last_error else f"Errore: {player.last_error}")
        return 0 if not player.last_error else 1

    devices = {"sirena": SirenDevice(player, volume)}
    if det.get("attivo", True):
        devices["dissuasore"] = DeterrentDevice(player, load_sounds(sounds_dir, int(det.get("volume", 90)), player.rate))
    # L'audio generato sta accanto alla sirena, non dentro: la sirena porta un limite di legge (tre
    # minuti per volta) e un jingle da due secondi non deve mangiare il budget di un allarme. Due
    # comandi, due limiti; davanti agli occhi di chi configura restano un elenco solo di suoni.
    if cfg.get("audio_generato", True):
        devices["audio_generato"] = GeneratedAudioDevice(
            player, load_generated(int(det.get("volume", 90)), player.rate))
    if cfg.get("led_prova", True):
        devices["luce_prova"] = LedDevice()
    # Le manopole delle schede audio: le gira il nodo (senza root) e le ricorda lui, perche' ALSA al
    # riavvio le dimentica. Si preparano qui, all'avvio, cosi' la prima sirena non aspetta amixer; e di
    # nuovo da sole se una scheda USB viene staccata e riattaccata (vedi AudioPlayer.ready e
    # Microphone._start).
    manopole = mixer.Manopole(cartella=folder)
    player.manopole = manopole
    if player.card:
        manopole.prepara(player.card, "riproduzione")
    mic = None
    if cfg.get("microfono", {}).get("attivo", False):
        from .mic import Microphone  # noqa: PLC0415

        mic = Microphone(cfg["microfono"].get("scheda", ""), manopole=manopole)
        if mic.card:
            manopole.prepara(mic.card, "cattura")
    recorder = None
    from .install_tools import camera_cfg  # noqa: PLC0415
    from .recording import NodeRecorder, recording_cfg  # noqa: PLC0415

    rec = recording_cfg(cfg)
    if rec["attiva"] and camera_cfg(cfg)["attiva"]:
        from .install_tools import read_credentials  # noqa: PLC0415
        from .recording import MediaMTXApi  # noqa: PLC0415

        try:
            creds = read_credentials(folder / "mtx-api")
        except (OSError, ValueError) as exc:
            # Senza password l'API di MediaMTX risponde 401 e la registrazione non parte mai. Prima
            # questo errore spariva in silenzio: il nodo diceva soltanto «NON registra adesso».
            creds = None
            logging.warning("Password dell'API di MediaMTX non leggibile (%s): la registrazione "
                            "non partirà. Controlla con  mirto-nodo stato", exc)
        # registra anche senza Mirto; anello per non riempire il disco
        recorder = NodeRecorder(rec, api=MediaMTXApi(credentials=creds)).start()
        logging.info("Registrazione sul nodo: %s", recorder.folder)
    # Il microfono dentro il flusso registrato: **spento di serie**, acceso per nodo. Il muxer e' il
    # terzo ascoltatore dell'unico `arecord` del nodo, non un secondo lettore di ALSA: e' quello che
    # permette al riconoscimento dei suoni di continuare a lavorare mentre l'audio finisce nelle
    # registrazioni (vedi flusso_audio.py).
    flusso = None
    if rec.get("audio") and mic is not None and camera_cfg(cfg)["attiva"]:
        from .flusso_audio import FlussoConAudio  # noqa: PLC0415
        from .install_tools import publish_credentials  # noqa: PLC0415
        from .recording import AUDIO_PATH, CAMERA_PATH, RTSP_PORT  # noqa: PLC0415

        try:
            pu, pp = publish_credentials(folder / "mtx-pub")
        except (OSError, ValueError) as exc:
            logging.warning("Password per pubblicare il flusso con l'audio non leggibile (%s): "
                            "l'audio non finira' nelle registrazioni", exc)
        else:
            dentro = f"rtsp://{pu}:{pp}@127.0.0.1:{RTSP_PORT}"
            flusso = FlussoConAudio(f"{dentro}/{CAMERA_PATH}", f"{dentro}/{AUDIO_PATH}", mic).start()
            if recorder is not None:
                # Il registratore nasce prima del muxer e glielo si collega qui: al prossimo
                # controllo (un minuto) passa a registrare il percorso con l'audio, e torna al video
                # da se' se quel muxer cade.
                recorder.flusso = flusso
            logging.warning("Audio nelle registrazioni ACCESO: il microfono finisce nelle clip e "
                            "negli spezzoni di questo nodo")
    # Una chiave per ogni abbinamento (chiavi/), più quella dell'installazione finché non viene
    # ritirata. Il file «chiave» può non esistere più — è proprio quello che fa
    # «mirto-nodo abbina --revoca installazione» — e il servizio deve partire lo stesso:
    # chiederlo senza riserve significava che il nodo si spegneva al primo riavvio dopo il ritiro.
    chiavi = KeyStore(folder)
    def scrivi_nome(nome: str) -> None:
        """Il nome scelto nel wizard di Mirto finisce in node.toml, come quello di «mirto-nodo nome»."""
        from .cli import set_toml_values  # noqa: PLC0415  (cli importa server: import tardivo)

        args.config.write_text(set_toml_values(args.config.read_text(), None, {"nome": nome}))

    def leggi_camera() -> dict:
        from .install_tools import camera_cfg as _camera_cfg, read_toml  # noqa: PLC0415

        return _camera_cfg(read_toml(args.config))

    def scrivi_camera(valori: dict) -> None:
        """La sezione [camera] scelta nel wizard di Mirto: la applica poi il servizio da root."""
        from .cli import set_toml_values  # noqa: PLC0415  (cli importa server: import tardivo)

        args.config.write_text(set_toml_values(args.config.read_text(), "camera", valori))

    def leggi_audio() -> dict:
        from .install_tools import read_toml  # noqa: PLC0415

        return dict(read_toml(args.config).get("audio") or {})

    def scrivi_audio(valori: dict) -> None:
        """La sezione [audio] scelta nel wizard: la applica poi il servizio da root."""
        from .cli import set_toml_values  # noqa: PLC0415

        args.config.write_text(set_toml_values(args.config.read_text(), "audio", valori))

    def leggi_microfono() -> dict:
        from .install_tools import read_toml  # noqa: PLC0415

        return dict(read_toml(args.config).get("microfono") or {})

    def scrivi_microfono(valori: dict) -> None:
        """La sezione [microfono] scelta dal pannello: il servizio da root riavvia e l'ascolto parte."""
        from .cli import set_toml_values  # noqa: PLC0415

        args.config.write_text(set_toml_values(args.config.read_text(), "microfono", valori))

    def leggi_registrazione() -> dict:
        from .install_tools import read_toml  # noqa: PLC0415
        from .recording import recording_cfg as _rec  # noqa: PLC0415

        return _rec(read_toml(args.config))

    def scrivi_registrazione(valori: dict) -> None:
        """La sezione [registrazione] scelta nel wizard: il disco e i limiti li applica root."""
        from .cli import set_toml_values  # noqa: PLC0415

        args.config.write_text(set_toml_values(args.config.read_text(), "registrazione", valori))

    node = Node(cfg.get("nome", "nodo"), chiavi.install_key_hex(), devices, player,
                cartella_suoni=sounds_dir,
                pairing=PairingWindow(folder), mic=mic, recorder=recorder, keys=chiavi,
                flusso=flusso,
                folder=folder, rename=scrivi_nome,
                camera_cfg=leggi_camera, set_camera_cfg=scrivi_camera,
                audio_cfg=leggi_audio, set_audio_cfg=scrivi_audio,
                rec_cfg=leggi_registrazione, set_rec_cfg=scrivi_registrazione,
                mic_cfg=leggi_microfono, set_mic_cfg=scrivi_microfono, manopole=manopole)
    if not node.all_keys():
        logging.warning("Nessuna chiave: il nodo non risponde a nessun Mirto finché non lo abbini "
                        "(sul nodo:  mirto-nodo abbina)")
    left = node.pairing.remaining()
    if left:
        # il PIN non finisce nel registro: si legge sul nodo (mirto-nodo stato) o nella partizione di avvio
        quanto = ("finché non viene abbinato" if left == node.pairing.SENZA_SCADENZA
                  else f"per {left // 60} minuti")
        logging.info("Abbinamento aperto %s: in Mirto comparirà \"Nuovo nodo trovato\". "
                     "Il PIN da scrivere in Mirto è in %s oppure in  mirto-nodo stato",
                     quanto, node.pairing.boot_pin)
    srv = NodeServer(node, cert, key_file, port=int(cfg.get("porta", 8443))).start()
    player.start_keep_alive()
    logging.info("Mirto Node '%s' in ascolto sulla porta %d · impronta %s", node.name, srv.port, fingerprint(cert))
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    stop.wait()
    logging.info("Arresto: spengo tutto")
    srv.stop()
    if recorder is not None:
        recorder.stop()
    # Prima il muxer, poi il microfono: fermato il microfono, il muxer resterebbe a scrivere su una
    # pipe che non riceve più niente e ffmpeg aspetterebbe il suo tempo prima di accorgersene.
    if flusso is not None:
        flusso.stop()
    player.close()
    if mic is not None:
        mic.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
