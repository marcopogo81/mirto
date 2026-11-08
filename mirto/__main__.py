# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Punto di ingresso: ``python -m mirto``.

Senza opzioni avvia l'interfaccia grafica. Opzioni utili:

  --elenca-camere           mostra le camere trovate con il loro ID stabile
  --prova-camera ID         apre una camera e salva una foto di prova
  --diagnosi-camere         prova tutti gli indici e salva una foto per ciascuno
  --senza-interfaccia       avvia il sistema senza finestra (servizio)
  --prova-registrazione [S] registra S secondi (60) da ogni camera attiva e misura spazio e CPU
"""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .i18n import _


def cmd_check_models() -> int:
    """Scarica e prova i modelli delle specie e dei suoni; stampa le impronte da fissare nel codice."""
    import numpy as np  # noqa: PLC0415

    from .detection.models import ASSETS, asset_digests, ensure_asset  # noqa: PLC0415

    ok = True
    for key in ASSETS:
        try:
            path = ensure_asset(key)
            print(f"✓ {key:14} {path}")
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"✗ {key:14} {exc}")
    print("\n" + _("Impronte SHA-256 (da copiare in ASSETS, detection/models.py):"))
    for key, digest in asset_digests().items():
        print(f"  {key:14} {digest or '-'}")
    try:
        from .detection.species import SpeciesClassifier  # noqa: PLC0415

        clf = SpeciesClassifier(ensure_asset("birds_model"), ensure_asset("birds_labels"))
        res = clf.classify(np.full((224, 224, 3), 127, np.uint8))
        print("\n" + _("Specie: motore {kind}, {n} specie, prova su immagine grigia → {top}",
                        kind=clf.backend.kind, n=len(clf.labels), top=res.top[0] if res else "-"))
    except Exception as exc:  # noqa: BLE001
        ok = False
        print("\n" + _("Specie: NON funziona ({exc})", exc=exc))
    try:
        from .audio.classifier import RATE, SoundClassifier  # noqa: PLC0415

        snd = SoundClassifier(ensure_asset("yamnet_model"), ensure_asset("yamnet_labels"))
        t = np.arange(RATE) / RATE
        beep = (0.5 * np.sin(2 * np.pi * 3100 * t) * (np.sin(2 * np.pi * 2 * t) > 0)).astype(np.float32)
        print(_("Suoni: {n} classi, {k} tipi; prova con un bip intermittente → {top}", n=len(snd.names),
                k=len(snd.groups), top=", ".join(f"{a} {b:.0%}" for a, b in snd.top(beep))))
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(_("Suoni: NON funziona ({exc})", exc=exc))
    return 0 if ok else 1


def cmd_list_cameras() -> int:
    from .cameras.discovery import discover_local_cameras  # noqa: PLC0415

    cams = discover_local_cameras()
    if not cams:
        print(_('Nessuna camera trovata. Su macOS controlla il permesso Camera in Impostazioni di Sistema → Privacy e sicurezza.'))
        return 1
    print(_('{v:<7} {v2:<35} ID stabile', v='Indice', v2='Nome'))
    for c in cams:
        print(f"{c.index!s:<7} {c.name:<35} {c.id}")
    return 0


def _read_frames(cap, seconds: float = 8.0):
    """Legge per qualche secondo: la camera del Mac impiega 1-2 s a "scaldarsi" (prime immagini nere)."""
    import time as _t  # noqa: PLC0415

    best, best_mean, got = None, -1.0, 0
    deadline = _t.time() + seconds
    while _t.time() < deadline:
        ok, frame = cap.read()
        if ok and frame is not None:
            got += 1
            m = float(frame.mean())
            if m > best_mean:
                best, best_mean = frame, m
            if m > 8 and got > 5:
                break
        else:
            _t.sleep(0.05)
    return best, best_mean, got


def _check_permission() -> bool:
    from .cameras.permissions import ensure_camera_permission  # noqa: PLC0415

    ok, msg = ensure_camera_permission()
    if msg:
        print(msg)
    return ok


def cmd_test_camera(camera_id: str, out: str) -> int:
    import cv2  # noqa: PLC0415

    from .cameras.discovery import discover_local_cameras  # noqa: PLC0415

    if not _check_permission():
        return 1
    cams = {c.id: c for c in discover_local_cameras()}
    cam = cams.get(camera_id)
    if cam is None:
        print(_('Camera {camera_id!r} non trovata. Usa --elenca-camere.', camera_id=camera_id))
        return 1
    print(_('Apro {name} (indice OpenCV {index})…', name=cam.name, index=cam.index))
    cap = cv2.VideoCapture(cam.index, cam.api) if cam.api is not None else cv2.VideoCapture(cam.index)
    try:
        if not cap.isOpened():
            print(_('La camera non si apre. Prova: python -m mirto --diagnosi-camere'))
            return 1
        frame, mean, got = _read_frames(cap)
        if frame is None:
            print(_("Nessuna immagine ricevuta (camera occupata da un'altra app?). Prova --diagnosi-camere."))
            return 1
        cv2.imwrite(out, frame)
        if mean < 8:
            print(_("ATTENZIONE: immagine nera (luminosità {mean:.1f}) salvata in {out}.\nPossibili cause: stanza buia, camera coperta, oppure l'indice non corrisponde a questa camera.\nEsegui: python -m mirto --diagnosi-camere", mean=mean, out=out))
            return 2
        print(_('Foto di prova salvata in {out} ({v}x{v2}, luminosità {mean:.0f}).', out=out, v=frame.shape[1], v2=frame.shape[0], mean=mean))
        return 0
    finally:
        cap.release()


def cmd_diagnose_cameras(max_index: int = 6) -> int:
    """Prova ogni indice OpenCV e salva una foto per ciascuno, per capire quale corrisponde a quale camera."""
    import sys as _sys  # noqa: PLC0415

    import cv2  # noqa: PLC0415

    from .cameras.discovery import discover_local_cameras  # noqa: PLC0415

    if not _check_permission():
        return 1
    cams = discover_local_cameras()
    print(_('Camere trovate dal sistema:'))
    for c in cams:
        print(_('  indice previsto {index!s:<4} {name:<35} {id}', index=c.index, name=c.name, id=c.id))
    api = cv2.CAP_AVFOUNDATION if _sys.platform == "darwin" else cv2.CAP_MSMF if _sys.platform.startswith("win") else cv2.CAP_ANY
    print(_('\nProva degli indici OpenCV (guarda se si accende il bollino verde):'))
    for i in range(max(max_index, len(cams))):
        cap = cv2.VideoCapture(i, api)
        try:
            if not cap.isOpened():
                print(_('  indice {i}: non disponibile', i=i))
                continue
            frame, mean, got = _read_frames(cap, seconds=5)
            if frame is None:
                print(_('  indice {i}: aperto ma nessuna immagine', i=i))
                continue
            name = f"prova_indice_{i}.jpg"
            cv2.imwrite(name, frame)
            note = "NERA" if mean < 8 else "ok"
            print(f"  indice {i}: {frame.shape[1]}x{frame.shape[0]}, luminosità {mean:.0f} ({note}) → {name}")
        finally:
            cap.release()
    from .config import config_dir  # noqa: PLC0415

    print(_('\nSe la camera giusta ha un indice diverso da quello previsto, aggiungi in {v}:\ncameras:\n  overrides:\n    "<ID stabile>": {{opencv_index: <indice giusto>}}', v=config_dir() / 'config.yaml'))
    return 0


def _cpu_seconds(pid: int) -> float | None:
    """Tempo di CPU usato finora da un processo (macOS e Linux, con ``ps``)."""
    import subprocess  # noqa: PLC0415

    try:
        out = subprocess.run(["ps", "-o", "time=", "-p", str(pid)], capture_output=True, text=True,
                             timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if not out:
        return None
    days, _sep, rest = out.rpartition("-")
    total = 0.0
    for part in rest.split(":"):
        total = total * 60 + float(part.replace(",", "."))
    return total + (int(days) * 86400 if days else 0)


def cmd_test_recording(seconds: int = 60) -> int:
    """Prova R0 dell'NVR: ffmpeg, codificatore, registrazione da ogni camera attiva, spazio, CPU, clip."""
    import tempfile  # noqa: PLC0415
    import time  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    from .cameras.manager import CameraManager  # noqa: PLC0415
    from .config import Config, data_dir  # noqa: PLC0415
    from .recording.ffmpeg import detect  # noqa: PLC0415
    from .recording.manager import ffmpeg_concat_clip  # noqa: PLC0415
    from .recording.recorder import CameraRecorder  # noqa: PLC0415
    from .recording.store import RecordingStore  # noqa: PLC0415

    cfg = Config()
    ff = detect(cfg.get("recording.ffmpeg", "") or "", cfg.get("recording.encoder", "") or "", refresh=True)
    print(_("ffmpeg: {path}", path=ff.path or _("NON TROVATO")))
    if not ff.ok:
        print(_("Installa le dipendenze: pip install -r requirements.txt (contiene imageio-ffmpeg)"))
        return 1
    print(_("Versione: {v}", v=ff.version))
    print(_("Codificatore per le camere locali: {enc}", enc=ff.encoder or _("nessuno")))
    for enc, err in ff.tried.items():
        print(f"  ✗ {enc}: {err}")
    cams = CameraManager(cfg)
    cams.start(threaded_discovery=False)
    print("\n" + _("Attendo le camere (10 s)…"))
    time.sleep(10)
    sources = dict(cams.sources)
    if not sources:
        print(_("Nessuna camera attiva."))
        cams.stop()
        return 1
    root = Path(tempfile.mkdtemp(prefix="prova-registrazione-", dir=data_dir()))
    store = RecordingStore(root)
    recs = [CameraRecorder(cid, src.info.name, src, store, ff, segment_seconds=30,
                           local_fps=float(cfg.get("recording.local_fps", 10)),
                           local_max_width=int(cfg.get("recording.local_max_width", 1280)))
            for cid, src in sources.items()]
    print(_("Registro {n} camere per {s} s in {root}", n=len(recs), s=seconds, root=root))
    cpu0 = time.process_time()
    t0 = time.time()
    for r in recs:
        r.start()
    time.sleep(seconds)
    elapsed = time.time() - t0
    cpu_py = time.process_time() - cpu0
    cpu = {r.cam_id: (_cpu_seconds(r._proc.pid) if r.running else None) for r in recs}
    for r in recs:
        r.stop()
    cams.stop()
    ok = True
    print()
    for r in recs:
        segs = store.segments_in(r.folder)
        size = sum(s.size for s in segs)
        gb_day = size / max(elapsed, 1) * 86400 / 1e9
        c = cpu.get(r.cam_id)
        cpu_txt = f"{c / elapsed:.0%}" if c is not None else "n/d"
        clip_txt = ""
        if segs:
            clip = root / f"clip-{segs[0].path.parent.name}.mp4"
            err = ffmpeg_concat_clip(ff.path, segs, segs[0].start + 5, segs[0].start + 25, clip, root / ".tmp")
            clip_txt = _("clip di prova ✓") if not err else _("clip ✗ {err}", err=err)
        mark = "✓" if segs and r.state != "error" else "✗"
        ok = ok and mark == "✓"
        print(f"{mark} {r.name}: " + _("{mode} ({enc}), {n} spezzoni, {mb:.1f} MB → {gb:.1f} GB al giorno, "
                                        "CPU di ffmpeg {cpu}", mode=_("copia") if r.copy_mode else _("codifica"),
                                        enc=r.encoder, n=len(segs), mb=size / 1e6, gb=gb_day, cpu=cpu_txt))
        if clip_txt:
            print("    " + clip_txt)
        if r.error:
            print("    " + _("errore: {e}", e=r.error))
        if r.clock_warning:
            print("    ⚠ " + r.clock_warning)
    print(_("CPU di Mirto durante la prova: {p:.0%} (tutti i thread)", p=cpu_py / elapsed))
    print("\n" + _("Apri la cartella e guarda i video con QuickTime:") + f'\n  open "{root}"')
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    from .config import Config  # noqa: PLC0415
    from .i18n import set_language  # noqa: PLC0415

    try:
        set_language(Config().get("general.language", "auto"))
    except Exception:  # noqa: BLE001  (configurazione illeggibile: si parte in italiano)
        set_language("it")
    parser = argparse.ArgumentParser(prog="mirto", description=_('Mirto – videosorveglianza locale'))
    parser.add_argument("--version", action="version", version=f"Mirto {__version__}")
    parser.add_argument("--elenca-camere", "--list-cameras", action="store_true", help=_('elenca le camere con ID stabile'))
    parser.add_argument("--prova-camera", "--test-camera", metavar="ID", help=_('salva una foto di prova dalla camera indicata'))
    parser.add_argument("--diagnosi-camere", "--diagnose-cameras", action="store_true", help=_('prova tutti gli indici e salva una foto per ciascuno'))
    parser.add_argument("--output", default="prova_camera.jpg", help=_('file della foto di prova'))
    parser.add_argument("--senza-interfaccia", "--headless", action="store_true", help=_('avvia senza finestra'))
    parser.add_argument("--simulatore-nodo", "--node-simulator", action="store_true", help=_('avvia un nodo simulato per i test'))
    parser.add_argument("--porta", "--port", type=int, default=8443, help=_('porta del nodo simulato'))
    parser.add_argument("--nome-nodo", "--node-name", default="simulato", help=_('nome del nodo simulato'))
    parser.add_argument("--verifica-modelli", "--check-models", action="store_true",
                        help=_('scarica e prova i modelli delle specie e dei suoni'))
    parser.add_argument("--prova-registrazione", "--test-recording", type=int, nargs="?", const=60, metavar="S",
                        help=_('registra S secondi (60) da ogni camera attiva e misura spazio e CPU'))
    parser.add_argument("--diagnostica", "--diagnostics", action="store_true",
                        help=_('stampa un rapporto da allegare a una segnalazione (senza dati personali)'))
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    from .cameras.permissions import prepare_opencv  # noqa: PLC0415
    from .logging_setup import setup_logging  # noqa: PLC0415

    prepare_opencv()
    setup_logging(args.verbose)
    # Prima riga del registro: versione, commit, **data del codice caricato** e ora di avvio. Serve a
    # rispondere alla domanda che il 23/10 ci è costata mezz'ora: «quello che hai davanti, è il codice
    # di adesso?» (vedi versione.py).
    from .versione import scrivi_nel_registro  # noqa: PLC0415

    scrivi_nel_registro()

    if args.diagnostica:
        from .diagnostics import cmd_diagnostics  # noqa: PLC0415

        return cmd_diagnostics()
    if args.elenca_camere:
        return cmd_list_cameras()
    if args.prova_camera:
        return cmd_test_camera(args.prova_camera, args.output)
    if args.verifica_modelli:
        return cmd_check_models()
    if args.prova_registrazione:
        return cmd_test_recording(args.prova_registrazione)
    if args.diagnosi_camere:
        return cmd_diagnose_cameras()
    if args.simulatore_nodo:
        from .actions.simulator import run_simulator  # noqa: PLC0415

        return run_simulator(name=args.nome_nodo, port=args.porta)
    if args.senza_interfaccia:
        from .core import run_headless  # noqa: PLC0415

        return run_headless()
    from .gui.app import run_gui  # noqa: PLC0415

    return run_gui()


if __name__ == "__main__":
    sys.exit(main())
