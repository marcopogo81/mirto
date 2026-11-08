# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Registrazione (NVR): spezzoni, conservazione, spazio, clip degli eventi, registratori."""

from __future__ import annotations

import calendar
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as N
from unittest import mock

import numpy as np

from mirto.recording import manager as M
from mirto.recording import recorder as R
from mirto.recording.ffmpeg import FFmpegInfo, candidate_encoders, detect
from mirto.recording.store import RecordingStore, parse_start, safe_dir_name


class Cfg:
    def __init__(self, **values):
        self.d = {f"recording.{k}": v for k, v in values.items()}

    def get(self, key, default=None):
        return self.d.get(key, default)


def name_at(ts: float) -> str:
    return time.strftime("%Y%m%d-%H%M%S.mp4", time.gmtime(ts))


def touch(folder: Path, ts: float, size: int = 4096) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name_at(ts)
    p.write_bytes(b"x" * size)
    import os

    os.utime(p, (ts + 60, ts + 60))
    return p


class StoreTest(unittest.TestCase):
    def test_parse_start_is_utc(self):
        self.assertEqual(parse_start("20261025-013000.mp4"), calendar.timegm((2026, 10, 25, 1, 30, 0)))
        self.assertIsNone(parse_start("camera.json"))
        self.assertIsNone(parse_start("20261399-999999.mp4"))

    def test_safe_dir_name(self):
        a, b = safe_dir_name("net:rtsp://x/y"), safe_dir_name("net:rtsp://x/z")
        self.assertNotEqual(a, b)
        self.assertRegex(a, r"^[A-Za-z0-9_-]+$")

    def test_segments_end_and_between(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = RecordingStore(Path(tmp))
            d = st.cam_dir("cam", "Camera")
            t = 1_800_000_000.0
            for i in range(3):
                touch(d, t + 60 * i)
            segs = st.segments("cam")
            self.assertEqual([s.start for s in segs], [t, t + 60, t + 120])
            self.assertEqual(segs[0].end, t + 60)
            self.assertEqual(segs[2].end, t + 180)  # ultimo: ora dell'ultima scrittura
            self.assertEqual(len(st.between(segs, t + 70, t + 130)), 2)
            # scelti dal nome, con un margine di 15 minuti prima (uno spezzone può essere iniziato prima)
            self.assertEqual(st.segments_in(d, t0=t + 2000, t1=t + 3000), [])
            self.assertEqual(len(st.segments_in(d, t0=t, t1=t + 30)), 1)
            self.assertEqual(st.camera_name("cam"), "Camera")
            self.assertIn("cam", st.cameras())


class FakeRecorder:
    def __init__(self, cam_id, name, source, store, ffmpeg, **kw):
        self.cam_id, self.name, self.source, self.store = cam_id, name, source, store
        self.folder = store.cam_dir(cam_id, name)
        self.state, self.error, self.started = R.STATE_RECORDING, "", False

    def start(self):
        self.started = True
        return self

    def stop(self):
        self.started = False

    def open_path(self):
        return None

    def status(self):
        return {"id": self.cam_id, "state": self.state}


def make_manager(tmp, clock, **cfg):
    sources = {}
    mgr = M.RecordingManager(Cfg(**cfg), N(sources=sources), Path(tmp), notify=lambda t: msgs.append(t),
                             clock=clock, ffmpeg_detect=lambda *a: FFmpegInfo(path="/bin/ffmpeg", encoder="libx264"))
    msgs: list[str] = []
    mgr.msgs = msgs
    mgr.notify = msgs.append
    return mgr, sources


class ManagerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = 1_800_000_000.0
        self.patch = mock.patch.object(M, "CameraRecorder", FakeRecorder)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def clock(self):
        return self.now

    def test_modes_and_folders(self):
        mgr, _src = make_manager(self.tmp.name, self.clock, mode="events", cameras={"b": "continuous", "c": "boh"})
        self.assertEqual(mgr.mode_for("a"), "events")
        self.assertEqual(mgr.mode_for("b"), "continuous")
        self.assertEqual(mgr.mode_for("c"), "events")
        self.assertEqual(mgr.folder_for("a", "A").name, M.BUFFER_DIR)
        self.assertEqual(mgr.folder_for("b", "B"), mgr.store.cam_dir("b"))
        self.assertEqual(mgr.root, Path(self.tmp.name) / "recordings")

    def test_sync_starts_and_stops(self):
        mgr, sources = make_manager(self.tmp.name, self.clock, mode="events", cameras={"off": "off"})
        sources["a"] = N(info=N(id="a", name="A", kind="local", url=None))
        sources["off"] = N(info=N(id="off", name="Spenta", kind="local", url=None))
        mgr.sync()
        self.assertEqual(set(mgr.recorders), {"a"})
        rec = mgr.recorders["a"]
        self.assertTrue(rec.started)
        mgr.config.d["recording.cameras"] = {"a": "continuous", "off": "off"}  # cambio di modalità → nuova cartella, riavvio
        mgr.sync()
        self.assertFalse(rec.started)
        self.assertEqual(mgr.recorders["a"].folder, mgr.store.cam_dir("a"))
        del sources["a"]
        mgr.sync()
        self.assertEqual(mgr.recorders, {})

    def test_no_ffmpeg_no_recorders(self):
        mgr, sources = make_manager(self.tmp.name, self.clock)
        mgr._detect = lambda *a: FFmpegInfo(error="ffmpeg non trovato")
        sources["a"] = N(info=N(id="a", name="A", kind="local", url=None))
        mgr.sync()
        self.assertEqual(mgr.recorders, {})
        self.assertEqual(mgr.status()["ffmpeg_error"], "ffmpeg non trovato")

    def test_disabled(self):
        mgr, sources = make_manager(self.tmp.name, self.clock, enabled=False)
        sources["a"] = N(info=N(id="a", name="A", kind="local", url=None))
        mgr.sync()
        self.assertEqual(mgr.recorders, {})

    def test_cleanup_buffer_and_retention(self):
        mgr, _ = make_manager(self.tmp.name, self.clock, mode="events", cameras={"c": "continuous"},
                              retention_days=1, max_gb=100, min_free_gb=0)
        buf = mgr.store.cam_dir("e", "E") / M.BUFFER_DIR
        old_buf = touch(buf, self.now - 600)
        new_buf = touch(buf, self.now - 60)
        main = mgr.store.cam_dir("c", "C")
        old_main = touch(main, self.now - 2 * 86400)
        new_main = touch(main, self.now - 3600)
        mgr.cleanup()
        self.assertFalse(old_buf.exists())
        self.assertTrue(new_buf.exists())
        self.assertFalse(old_main.exists())
        self.assertTrue(new_main.exists())

    def test_cleanup_keeps_segments_of_pending_clips(self):
        mgr, _ = make_manager(self.tmp.name, self.clock, mode="events", max_gb=100, min_free_gb=0)
        buf = mgr.store.cam_dir("e", "E") / M.BUFFER_DIR
        seg = touch(buf, self.now - 600)
        img = Path(self.tmp.name) / "ev.jpg"
        self.assertTrue(mgr.request_clip(N(id="1", camera_id="e", ts=self.now - 570, image_path=str(img))))
        mgr.cleanup()
        self.assertTrue(seg.exists())

    def test_quota_deletes_oldest_first(self):
        mgr, _ = make_manager(self.tmp.name, self.clock, mode="continuous", retention_days=30,
                              max_gb=10_000 / 1e9, min_free_gb=0)
        d = mgr.store.cam_dir("c", "C")
        files = [touch(d, self.now - 3600 * (5 - i), size=4000) for i in range(5)]  # 20 kB, limite 10 kB
        usage = mgr.cleanup()
        self.assertEqual([f.exists() for f in files], [False, False, False, True, True])
        self.assertLessEqual(usage["used"], 10_000)

    def test_free_space_is_told_even_with_recording_off(self):
        """Chiesto da Marco il 21/10: lo spazio del computer è un'informazione, non un dettaglio della
        registrazione. Lo misurava solo il giro di pulizia, quindi a registrazione spenta spariva."""
        mgr, _ = make_manager(self.tmp.name, self.clock, enabled=False)
        mgr.last_usage = {}                     # nessun giro di pulizia: è quello che succede da spenta
        uso = mgr.status()["usage"]
        self.assertFalse(mgr.enabled, "registrazione spenta")
        self.assertTrue(uso.get("total"), "lo spazio si sa lo stesso")
        self.assertGreater(uso.get("free", 0), 0)

    def test_disk_full_pauses_and_notifies(self):
        mgr, sources = make_manager(self.tmp.name, self.clock, min_free_gb=10)
        with mock.patch.object(RecordingStore, "usage", return_value={"used": 0, "free": 1e9, "total": 1e12,
                                                                     "oldest": None}):
            mgr.cleanup()
        self.assertTrue(mgr.paused_disk)
        self.assertEqual(len(mgr.msgs), 1)
        sources["a"] = N(info=N(id="a", name="A", kind="local", url=None))
        mgr.sync()
        self.assertEqual(mgr.recorders, {})
        with mock.patch.object(RecordingStore, "usage", return_value={"used": 0, "free": 50e9, "total": 1e12,
                                                                     "oldest": None}):
            mgr.cleanup()
        self.assertFalse(mgr.paused_disk)
        self.assertEqual(len(mgr.msgs), 2)

    def test_request_clip_rules(self):
        mgr, _ = make_manager(self.tmp.name, self.clock, mode="events", cameras={"off": "off"})
        ev = N(id="1", camera_id="sconosciuta", ts=self.now, image_path="/x/ev.jpg")
        self.assertFalse(mgr.request_clip(ev))  # nessuna registrazione di quella camera
        mgr.store.cam_dir("off", "Spenta")
        self.assertFalse(mgr.request_clip(N(id="2", camera_id="off", ts=self.now, image_path="/x/a.jpg")))
        mgr.store.cam_dir("c", "C")
        self.assertFalse(mgr.request_clip(N(id="3", camera_id="c", ts=self.now, image_path="")))
        self.assertTrue(mgr.request_clip(N(id="4", camera_id="c", ts=self.now, image_path="/x/b.jpg")))
        job = mgr.jobs[0]
        self.assertEqual((job.t0, job.t1, job.out.name), (self.now - 10, self.now + 20, "b.mp4"))

    def test_job_gives_up_without_segments(self):
        mgr, _ = make_manager(self.tmp.name, self.clock)
        mgr.ensure_ffmpeg()
        mgr.store.cam_dir("c", "C")
        mgr.request_clip(N(id="4", camera_id="c", ts=self.now, image_path=str(Path(self.tmp.name) / "b.jpg")))
        self.now += 30
        mgr._process_jobs()
        self.assertEqual(len(mgr.jobs), 1)  # si riprova più tardi
        self.now += M.CLIP_GIVE_UP_SECONDS
        mgr._process_jobs()
        self.assertEqual(mgr.jobs, [])

    def test_alerts_after_two_minutes(self):
        mgr, sources = make_manager(self.tmp.name, self.clock)
        sources["a"] = N(info=N(id="a", name="A", kind="local", url=None))
        mgr.sync()
        rec = mgr.recorders["a"]
        rec.state, rec.error = R.STATE_ERROR, "boom"
        mgr._check_alerts()
        self.now += M.ALERT_AFTER_SECONDS + 1
        mgr._check_alerts()
        mgr._check_alerts()
        self.assertEqual(len(mgr.msgs), 1)
        rec.state = R.STATE_RECORDING
        mgr._check_alerts()
        self.assertEqual(len(mgr.msgs), 2)


class HelpersTest(unittest.TestCase):
    def test_output_size_and_bitrate(self):
        self.assertEqual(R.output_size(1920, 1080, 1280), (1280, 720))
        self.assertEqual(R.output_size(641, 361, 1280), (640, 360))
        self.assertTrue(800 <= R.bitrate_kbps(1280, 720, 10) <= 1500)

    def test_copy_source(self):
        self.assertTrue(R.is_copy_source(N(kind="network", url="rtsp://a/b")))
        self.assertFalse(R.is_copy_source(N(kind="network", url="http://a/mjpeg")))
        self.assertFalse(R.is_copy_source(N(kind="local", url=None)))

    def test_audio_is_dropped_unless_the_node_publishes_it(self):
        """«-an» era una promessa, non una dimenticanza: fino al 26/10 l'interfaccia e le due guide
        dicevano che l'audio non viene mai registrato. Dal 26/10 un nodo puo' pubblicare il microfono
        accanto al video, e quando lo fa quel flusso si registra com'e'. Qui non si decide niente: si
        usa quello che il nodo dice di stare pubblicando."""
        rec = R.CameraRecorder.__new__(R.CameraRecorder)
        rec.cam_id, rec.name = "net:1", "Ingresso"
        rec.audio_resolver = None
        self.assertEqual(rec._url_con_audio(), "", "senza nessuno a cui chiedere, si registra come sempre")
        rec.audio_resolver = lambda cam: "rtsp://u:p@1.2.3.4:8554/cam-audio"
        self.assertEqual(rec._url_con_audio(), "rtsp://u:p@1.2.3.4:8554/cam-audio")

        def esplode(cam):
            raise RuntimeError("il nodo non risponde")

        rec.audio_resolver = esplode
        self.assertEqual(rec._url_con_audio(), "",
                         "una registrazione muta e' un peccato; una che non parte e' una casa non sorvegliata")

    def test_errors_hide_credentials(self):
        rec = R.CameraRecorder.__new__(R.CameraRecorder)
        from collections import deque

        rec._stderr = deque(["Connection refused", "rtsp://utente:segreta@nodo:8554/cam: 401 Unauthorized"])
        self.assertEqual(rec._last_error("x"), "Connection refused")

    def test_encoder_order(self):
        self.assertEqual(candidate_encoders("darwin")[0], "h264_videotoolbox")
        self.assertEqual(candidate_encoders("win32")[0], "h264_mf")
        self.assertEqual(candidate_encoders("linux")[-1], "mpeg4")
        self.assertEqual(FFmpegInfo(encoder="h264_mf").encoder_args(900), ["-c:v", "h264_mf", "-b:v", "900k"])


FF = detect()


@unittest.skipUnless(FF.ok and FF.encoder, "ffmpeg non disponibile")
class LiveTest(unittest.TestCase):
    """Con ffmpeg vero: camera "locale" finta → spezzoni → clip."""

    def test_frames_recording_and_clip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = RecordingStore(Path(tmp) / "rec")
            stop = threading.Event()
            src = N(info=N(id="mac", name="Mac", kind="local", url=None), frame=None, ts=0.0)
            src.latest = lambda: (src.frame, src.ts, 0)

            def feed():
                i = 0
                while not stop.is_set():
                    f = np.zeros((360, 640, 3), np.uint8)
                    f[:, (i * 16) % 600:(i * 16) % 600 + 40] = 255
                    src.frame, src.ts = f, time.time()
                    i += 1
                    time.sleep(1 / 15)

            threading.Thread(target=feed, daemon=True).start()
            rec = R.CameraRecorder("mac", "Mac", src, store, FF, segment_seconds=2, local_fps=10)
            t0 = time.time()
            rec.start()
            try:
                deadline = time.time() + 20
                while time.time() < deadline and len(store.segments("mac")) < 3:
                    time.sleep(0.5)
                self.assertEqual(rec.state, R.STATE_RECORDING, rec.error)
            finally:
                rec.stop()
                stop.set()
            segs = store.segments("mac")
            self.assertGreaterEqual(len(segs), 3)
            self.assertLess(abs(segs[0].start - t0), 5)
            out = Path(tmp) / "ev.mp4"
            err = M.ffmpeg_concat_clip(FF.path, segs, segs[0].start + 1, segs[-1].end - 0.5, out, Path(tmp) / ".tmp")
            self.assertEqual(err, "")
            self.assertGreater(out.stat().st_size, M.MIN_READABLE_BYTES)


@unittest.skipUnless(FF.ok, "ffmpeg non disponibile")
class ClipDalMomentoGiustoTest(unittest.TestCase):
    """Q-05: la clip deve contenere **quel** momento, non un pezzo qualunque della registrazione.

    La promessa è «10 secondi prima e 20 dopo l'evento». Finora il test controllava solo che il file
    fosse più grande di 2 KB: un video dell'ora sbagliata lo supera benissimo. Qui i tre spezzoni
    sono di tre colori diversi — rosso, verde, blu, cinque secondi l'uno — e si chiede la finestra
    che cade **dentro il secondo**: se la clip comincia col verde è partita dal punto giusto, se
    comincia col rosso ha sbagliato spezzone.
    """

    DURATA = 5.0
    COLORI = {"rosso": (0, 0, 255), "verde": (0, 255, 0), "blu": (255, 0, 0)}   # BGR, come li legge OpenCV

    def spezzoni(self, cartella: Path, inizio: float) -> list:
        """Tre spezzoni consecutivi, uno per colore, con l'ora di inizio nel nome come quelli veri."""
        from mirto.recording.store import Segment

        segs = []
        for i, nome in enumerate(("rosso", "verde", "blu")):
            percorso = cartella / f"{int(inizio) + i * int(self.DURATA)}-{nome}.mp4"
            subprocess.run([FF.path, "-v", "error", "-y", "-f", "lavfi",
                            "-i", f"color=c={nome.replace('rosso', 'red').replace('verde', 'green').replace('blu', 'blue')}:"
                                  f"s=320x240:r=10:d={self.DURATA:.0f}",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "10",
                            # frammentati come li scrive il registratore: la durata nell'intestazione è 0
                            "-movflags", "+frag_keyframe+empty_moov+default_base_moof", str(percorso)],
                           check=True, capture_output=True)
            segs.append(Segment(path=percorso, start=inizio + i * self.DURATA,
                                end=inizio + (i + 1) * self.DURATA, size=percorso.stat().st_size))
        return segs

    def primo_colore(self, video: Path) -> str:
        """Il colore del primo fotogramma della clip, letto davvero dal file."""
        import cv2

        cap = cv2.VideoCapture(str(video))
        try:
            ok, frame = cap.read()
        finally:
            cap.release()
        self.assertTrue(ok, f"clip illeggibile: {video}")
        b, g, r = (int(x) for x in frame[frame.shape[0] // 2, frame.shape[1] // 2])
        vicino = min(self.COLORI, key=lambda n: sum(abs(a - b2) for a, b2 in zip(self.COLORI[n], (b, g, r))))
        return vicino

    def test_the_clip_starts_in_the_right_segment(self):
        with tempfile.TemporaryDirectory() as tmp:
            cartella = Path(tmp)
            inizio = 1_760_000_000.0
            segs = self.spezzoni(cartella, inizio)
            out = cartella / "clip.mp4"
            # finestra tutta dentro il secondo spezzone (verde): da +6 s a +9 s dall'inizio
            err = M.ffmpeg_concat_clip(FF.path, segs, inizio + 6.0, inizio + 9.0, out, cartella / ".tmp")
            self.assertEqual(err, "")
            self.assertEqual(self.primo_colore(out), "verde",
                             "la clip è partita dallo spezzone sbagliato: è questo che un file da 2 KB non vede")

    def test_the_clip_lasts_what_was_asked(self):
        with tempfile.TemporaryDirectory() as tmp:
            cartella = Path(tmp)
            inizio = 1_760_000_000.0
            segs = self.spezzoni(cartella, inizio)
            out = cartella / "clip.mp4"
            err = M.ffmpeg_concat_clip(FF.path, segs, inizio + 3.0, inizio + 11.0, out, cartella / ".tmp")
            self.assertEqual(err, "")
            misurata = durata_vera(out)
            self.assertAlmostEqual(misurata, 8.0, delta=2.0,
                                   msg=f"chiesti 8 secondi, il file ne dura {misurata:.1f}")

    def test_the_list_declares_how_long_each_piece_lasts(self):
        """Il file lista dichiara la durata di ogni pezzo, tranne l'ultimo.

        Senza «duration», il concat crede alla durata scritta nell'intestazione del file, che in un MP4
        frammentato può essere 0: allora il salto a «dieci secondi prima dell'evento» cade nello
        spezzone sbagliato. La durata si conosce dai nomi, che sono orari.

        **Onestà sul perché questo test guarda il file lista e non il risultato**: con ffmpeg 7.0.2 su
        questa macchina il salto finisce comunque nel punto giusto anche senza le durate — il difetto
        che il piano temeva non si riproduce qui. Ma la correttezza non deve dipendere da quanto bene
        una certa versione di ffmpeg indovina: dichiarare la durata la rende indipendente
        dall'intestazione, e questo si verifica solo dove la scelta viene fatta.
        """
        righe = []
        with tempfile.TemporaryDirectory() as tmp:
            cartella = Path(tmp)
            segs = self.spezzoni(cartella, 1_760_000_000.0)
            vera = M.subprocess.run

            def spia(cmd, *a, **k):     # intercetta la chiamata e legge il file lista prima che sparisca
                lista = Path(cmd[cmd.index("-i") + 1])
                righe.extend(lista.read_text(encoding="utf-8").splitlines())
                return vera(cmd, *a, **k)

            M.subprocess.run = spia
            try:
                M.ffmpeg_concat_clip(FF.path, segs, segs[0].start + 1, segs[-1].end, cartella / "c.mp4",
                                     cartella / ".tmp")
            finally:
                M.subprocess.run = vera
        durate = [r for r in righe if r.startswith("duration ")]
        self.assertEqual(len(durate), 2, f"una durata per spezzone tranne l'ultimo, trovate {len(durate)}: {righe}")
        self.assertEqual(durate[0], f"duration {self.DURATA:.3f}")
        self.assertTrue(righe[-1].startswith("file "),
                        "l'ultimo spezzone resta senza durata: può essere ancora in scrittura")


def durata_vera(video: Path) -> float:
    """Quanto dura davvero il file, misurato decodificandolo con ffmpeg.

    Prima si chiedeva a ``ffprobe``, e dove ffprobe manca la prova si saltava: ``imageio-ffmpeg``
    porta solo ``ffmpeg``, quindi su Windows — il sistema che il piano vuole poter dichiarare
    provato — la promessa «la clip dura quanto chiesto» non la verificava nessuno (W-01, 19/10).

    ``-progress`` stampa ``out_time=HH:MM:SS.ffffff`` mentre decodifica: l'ultimo valore è quanto
    video è davvero uscito, che è più di quanto direbbe l'intestazione — in un MP4 frammentato la
    durata dichiarata può essere 0. I primi valori possono essere ``N/A``, per questo si scorre
    all'indietro. Il formato ``out_time`` esiste in tutte le versioni di ffmpeg, a differenza di
    ``out_time_us``.
    """
    res = subprocess.run([FF.path, "-v", "error", "-i", str(video), "-f", "null",
                          "-progress", "pipe:1", "-"], capture_output=True, text=True)
    tempi = [r.split("=", 1)[1].strip() for r in res.stdout.splitlines() if r.startswith("out_time=")]
    for valore in reversed(tempi):
        try:
            ore, minuti, secondi = valore.split(":")
            return int(ore) * 3600 + int(minuti) * 60 + float(secondi)
        except ValueError:
            continue
    return -1.0


class EventClipTest(unittest.TestCase):
    def test_delete_removes_clip(self):
        from mirto.events import Event, EventStore

        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp))
            ev = store.save(Event(camera_id="c", camera_name="C", score=0.9, image=np.zeros((10, 10, 3), np.uint8)))
            clip = Path(ev.image_path).with_suffix(".mp4")
            clip.write_bytes(b"x")
            self.assertEqual(ev.clip_path, clip)
            store.delete([ev.id])
            self.assertFalse(clip.exists())
            store.close()


if __name__ == "__main__":
    unittest.main()


class TelegramClipTest(unittest.TestCase):
    def _bot(self):
        from mirto.notify.telegram import BotHandlers, TelegramBot
        from tests.test_notify import FakeAPI

        class H(BotHandlers):
            def __init__(self):
                self.asked = []

            def clip(self, camera_query, seconds):
                self.asked.append((camera_query, seconds))
                return (b"MP4", "🎞 Ingresso", "c.mp4") if camera_query != "garage" else "Camera non trovata."

        api, h = FakeAPI(), H()
        return api, h, TelegramBot(api, "-100", [111], h)

    def test_clip_replies_to_event_photo(self):
        from mirto.events import Event

        api, _h, bot = self._bot()
        with tempfile.TemporaryDirectory() as tmp:
            clip = Path(tmp) / "a.mp4"
            clip.write_bytes(b"VIDEO")
            self.assertFalse(bot.send_event_clip("ev1", clip, "x"))  # foto non ancora inviata
            ev = Event(camera_id="c", camera_name="C", score=0.9, image=np.zeros((10, 10, 3), np.uint8))
            ev.id = "ev1"
            bot.send_event(ev)
            photo_id = api.next_id
            self.assertTrue(bot.send_event_clip("ev1", clip, "🎞 C"))
        method, params, files = api.calls[-1]
        self.assertEqual(method, "sendVideo")
        self.assertEqual(params["reply_parameters"]["message_id"], photo_id)
        self.assertEqual(files["video"][1], b"VIDEO")

    def test_clip_command(self):
        api, h, bot = self._bot()
        bot.handle_update({"message": {"chat": {"id": 5}, "from": {"id": 111}, "text": "/clip ingresso 45"}})
        bot.handle_update({"message": {"chat": {"id": 5}, "from": {"id": 111}, "text": "/video 500"}})
        bot.handle_update({"message": {"chat": {"id": 5}, "from": {"id": 111}, "text": "/clip garage"}})
        self.assertEqual(h.asked, [("ingresso", 45), ("", 120), ("garage", 30)])
        self.assertEqual([c[0] for c in api.calls], ["sendVideo", "sendVideo", "sendMessage"])

    def test_dispatcher_waits_for_photo(self):
        from mirto.notify.dispatcher import NotificationDispatcher

        d = NotificationDispatcher.__new__(NotificationDispatcher)
        d.config = N(get=lambda k, default=None: default)
        sent = {"ok": False}
        d.bot = N(chat_id="-100", send_event_clip=lambda eid, p, c: sent["ok"])
        item = ("ev1", "/x.mp4", "c", time.time())
        self.assertFalse(d._send_clip(item))           # foto non ancora partita: si aspetta
        sent["ok"] = True
        self.assertTrue(d._send_clip(item))
        self.assertTrue(d._send_clip(("ev1", "/x.mp4", "c", time.time() - 3600)))  # troppo vecchia: si lascia


class ReviewFixesTest(unittest.TestCase):
    """Correzioni dopo la revisione: niente cancellazioni alla cieca, arresti che non si bloccano."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = time.time()
        self.patch = mock.patch.object(M, "CameraRecorder", FakeRecorder)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def clock(self):
        return self.now

    def test_unknown_disk_space_deletes_nothing(self):
        mgr, _ = make_manager(self.tmp.name, self.clock, mode="continuous", retention_days=30)
        d = mgr.store.cam_dir("c", "C")
        files = [touch(d, self.now - 3600 * i) for i in range(1, 4)]
        with mock.patch.object(RecordingStore, "usage", return_value={"used": 12288, "free": 0, "total": 0,
                                                                     "oldest": None}):
            mgr.cleanup()
        self.assertTrue(all(f.exists() for f in files))
        self.assertFalse(mgr.paused_disk)

    def test_disk_filled_by_others_keeps_last_day(self):
        mgr, _ = make_manager(self.tmp.name, self.clock, mode="continuous", retention_days=30, max_gb=1000,
                              min_free_gb=10)
        d = mgr.store.cam_dir("c", "C")
        old = touch(d, self.now - 3 * 86400)
        recent = touch(d, self.now - 3600)
        with mock.patch.object(RecordingStore, "usage", return_value={"used": 8192, "free": 1e9, "total": 1e12,
                                                                     "oldest": None}):
            mgr.cleanup()
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())
        self.assertTrue(mgr.paused_disk)

    def test_relative_folder_ignored(self):
        mgr, _ = make_manager(self.tmp.name, self.clock, folder="registrazioni")
        self.assertEqual(mgr.root, Path(self.tmp.name) / "recordings")

    def test_no_new_recorders_after_stop(self):
        mgr, sources = make_manager(self.tmp.name, self.clock)
        sources["a"] = N(info=N(id="a", name="A", kind="local", url=None))
        mgr._stop.set()
        mgr.sync()
        self.assertEqual(mgr.recorders, {})

    def test_covers(self):
        from mirto.recording.store import Segment

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "x.mp4"
            p.write_bytes(b"x")
            import os

            os.utime(p, (1000, 1000))
            seg = Segment(p, 900, 1000, 1)
            self.assertTrue(M.RecordingManager._covers([seg], 998))
            self.assertFalse(M.RecordingManager._covers([seg], 1000))
            self.assertTrue(M.RecordingManager._covers([seg, Segment(p, 1001, 1002, 1)], 1000))
            self.assertFalse(M.RecordingManager._covers([], 1))

    def test_kill_only_ffmpeg(self):
        import os

        self.assertFalse(M.kill_if_ffmpeg(os.getpid()))

    def test_percent_in_folder(self):
        import os as _os

        cartella = Path("/dati/100%/cam")
        atteso = str(cartella).replace("%", "%%") + _os.sep  # su Windows il separatore è un altro
        out = R.segment_output(cartella, 60)
        self.assertTrue(out[-1].startswith(atteso), out[-1])
        self.assertTrue(out[-1].endswith("%Y%m%d-%H%M%S.mp4"))


@unittest.skipIf(__import__("sys").platform == "win32", "script di shell")
class StuckFfmpegTest(unittest.TestCase):
    def test_stop_does_not_hang_when_ffmpeg_stops_reading(self):
        import os

        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "ffmpeg"
            fake.write_text("#!/bin/sh\nexec sleep 1000\n")
            fake.chmod(0o755)
            ff = FFmpegInfo(path=str(fake), encoder="libx264")
            src = N(info=N(id="mac", name="Mac", kind="local", url=None))
            frame = np.zeros((360, 640, 3), np.uint8)
            src.latest = lambda: (frame, time.time(), 0)
            store = RecordingStore(Path(tmp) / "rec")
            rec = R.CameraRecorder("mac", "Mac", src, store, ff, local_fps=10)
            rec.start()
            time.sleep(1.5)  # il registratore è bloccato a scrivere: sleep non legge
            pid = rec._proc.pid
            t0 = time.monotonic()
            rec.stop()
            self.assertLess(time.monotonic() - t0, 8)
            self.assertIsNotNone(rec._proc.poll())
            self.assertEqual(store.stale_pids(), [])
            self.assertTrue(pid)


class ArmedOnlyTest(unittest.TestCase):
    """Camere che registrano solo con la protezione attiva."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(M, "CameraRecorder", FakeRecorder)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_follows_protection(self):
        mgr, sources = make_manager(self.tmp.name, time.time, armed_only={"a": True})
        mgr.config.d["actions.armed"] = False
        sources["a"] = N(info=N(id="a", name="A", kind="local", url=None))
        sources["b"] = N(info=N(id="b", name="B", kind="local", url=None))
        mgr.sync()
        self.assertEqual(set(mgr.recorders), {"b"})
        self.assertEqual(mgr.status()["waiting_armed"], ["a"])
        mgr.store.cam_dir("a", "A")
        self.assertFalse(mgr.request_clip(N(id="1", camera_id="a", ts=time.time(), image_path="/x/a.jpg")))
        self.assertIsNone(mgr.live_clip("a", 30)[0])
        mgr.config.d["actions.armed"] = True
        mgr.sync()
        self.assertEqual(set(mgr.recorders), {"a", "b"})
        self.assertEqual(mgr.status()["waiting_armed"], [])
        mgr.config.d["actions.armed"] = False
        mgr.sync()
        self.assertEqual(set(mgr.recorders), {"b"})


class CameraStateTest(unittest.TestCase):
    """Colonna «Adesso» delle Impostazioni: cosa fa la registrazione di ogni camera."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(M, "CameraRecorder", FakeRecorder)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_states(self):
        mgr, sources = make_manager(self.tmp.name, time.time, mode="events",
                                    cameras={"c": "continuous", "o": "off"}, armed_only={"p": True})
        mgr.config.d["actions.armed"] = False
        for cid in ("a", "c", "p"):
            sources[cid] = N(info=N(id=cid, name=cid.upper(), kind="local", url=None))
        mgr.sync()
        cam = lambda cid, **kw: dict({"id": cid, "enabled": True, "connected": True}, **kw)  # noqa: E731
        self.assertEqual(mgr.camera_state(cam("a"))[0], "ok")
        self.assertIn("eventi", mgr.camera_state(cam("a"))[1])
        self.assertEqual(mgr.camera_state(cam("c")), ("ok", "registra tutto"))
        self.assertEqual(mgr.camera_state(cam("o"))[0], "off")
        self.assertEqual(mgr.camera_state(cam("p"))[0], "paused")
        self.assertEqual(mgr.camera_state(cam("x", enabled=False))[0], "off")
        self.assertEqual(mgr.camera_state(cam("x", connected=False))[0], "off")
        mgr.recorders["a"].state, mgr.recorders["a"].error = R.STATE_ERROR, "boom"
        self.assertEqual(mgr.camera_state(cam("a")), ("error", "ferma: boom"))
        mgr.paused_disk = True
        self.assertEqual(mgr.camera_state(cam("c"))[0], "error")


class TimelineTest(unittest.TestCase):
    """Logica della vista Rivedi."""

    def seg(self, a, b):
        from mirto.recording.store import Segment

        return Segment(Path(f"/x/{int(a)}.mp4"), a, b, 1)

    @unittest.skipUnless(hasattr(time, "tzset"), "time.tzset non esiste su Windows")
    def test_day_bounds_dst(self):
        import datetime as dt
        import os

        from mirto.recording import timeline as TL

        old = os.environ.get("TZ")
        os.environ["TZ"] = "Europe/Rome"
        time.tzset()
        try:
            a, b = TL.day_bounds(dt.date(2026, 10, 25))  # cambio dell'ora: la giornata dura 25 ore
            self.assertEqual(b - a, 25 * 3600)
            a, b = TL.day_bounds(dt.date(2026, 10, 17))
            self.assertEqual(b - a, 24 * 3600)
        finally:
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old
            time.tzset()

    def test_spans_merge_small_gaps(self):
        from mirto.recording import timeline as TL

        segs = [self.seg(0, 60), self.seg(61, 120), self.seg(300, 360)]
        self.assertEqual(TL.spans(segs, 0, 1000), [(0, 120), (300, 360)])
        self.assertEqual(TL.spans(segs, 30, 330), [(30, 120), (300, 330)])

    def test_covered(self):
        from mirto.recording import timeline as TL

        segs = [self.seg(0, 60), self.seg(30, 90), self.seg(200, 260)]
        self.assertEqual(TL.covered(segs, 0, 120), 90)  # sovrapposti contati una volta sola
        self.assertEqual(TL.covered(segs, 100, 190), 0)
        self.assertEqual(TL.covered(segs, 230, 300), 30)

    def test_locate_and_next(self):
        from mirto.recording import timeline as TL

        segs = [self.seg(0, 60), self.seg(60, 120), self.seg(300, 360)]
        p = TL.locate(segs, 75)
        self.assertEqual((p.segment.start, p.offset, p.exact), (60, 15, True))
        p = TL.locate(segs, 200)  # buco: si parte dal tratto dopo
        self.assertEqual((p.segment.start, p.offset, p.exact), (300, 0, False))
        self.assertIsNone(TL.locate(segs, 500))
        self.assertEqual(TL.next_segment(segs, segs[1]).start, 300)
        self.assertIsNone(TL.next_segment(segs, segs[2]))

    def test_events(self):
        from mirto.recording import timeline as TL

        evs = [{"ts": 10, "label": "person"}, {"ts": 50, "label": "sound:glass"}, {"ts": 90, "label": "bird:pigeon"}]
        self.assertEqual(TL.neighbor_event(evs, 10, True)["ts"], 50)
        self.assertEqual(TL.neighbor_event(evs, 50, False)["ts"], 10)
        self.assertIsNone(TL.neighbor_event(evs, 95, True))
        self.assertEqual([TL.event_color_kind(e["label"]) for e in evs], ["person", "sound", "other"])
        segs = [self.seg(40, 60)]  # solo l'evento delle 50 ha un video: gli altri hanno solo la foto
        self.assertEqual([e["ts"] for e in TL.playable_events(evs, segs)], [50])

    def test_store_between(self):
        from mirto.events import Event, EventStore

        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp))
            for i, cam in enumerate(("a", "b", "a")):
                ev = Event(camera_id=cam, camera_name=cam, score=0.9, image=np.zeros((4, 4, 3), np.uint8))
                ev.ts = 1000 + i
                store.save(ev)
            self.assertEqual([e["ts"] for e in store.between(0, 2000, "a")], [1000, 1002])
            self.assertEqual(len(store.between(1001, 2000)), 2)
            store.close()


@unittest.skipUnless(FF.ok and FF.encoder, "ffmpeg non disponibile")
class ExportTest(unittest.TestCase):
    def test_export_and_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = M.RecordingManager(Cfg(), N(sources={}), Path(tmp))
            d = mgr.store.cam_dir("c", "C")
            t0 = float(int(time.time()) - 100)
            # due spezzoni veri da 3 s ciascuno
            import subprocess

            for i in range(2):
                name = time.strftime("%Y%m%d-%H%M%S.mp4", time.gmtime(t0 + 3 * i))
                subprocess.run([FF.path, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10",
                                "-t", "3", "-c:v", FF.encoder, "-pix_fmt", "yuv420p", "-g", "10", str(d / name)],
                               check=True)
            out = Path(tmp) / "export.mp4"
            self.assertEqual(mgr.export("c", t0 + 1, t0 + 5, out), "")
            self.assertGreater(out.stat().st_size, 1000)
            self.assertIn("Nessuna", mgr.export("c", t0 + 1000, t0 + 1005, Path(tmp) / "x.mp4"))
            seg = mgr.segments("c")[0]
            jpg = Path(tmp) / "f.jpg"
            self.assertEqual(mgr.frame_at(seg, 1.5, jpg), "")
            self.assertGreater(jpg.stat().st_size, 500)
            self.assertEqual([c for c, _n in mgr.recorded_cameras()], ["c"])


class ClipSegmentsTest(unittest.TestCase):
    def test_clips_become_playable_spans(self):
        from mirto.recording import timeline as TL
        from mirto.recording.store import Segment

        with tempfile.TemporaryDirectory() as tmp:
            img1, img2, img3 = (Path(tmp) / f"{i}.jpg" for i in range(3))
            img1.with_suffix(".mp4").write_bytes(b"x" * 10)
            img2.with_suffix(".mp4").write_bytes(b"x" * 10)  # coperta dalla registrazione continua
            events = [{"ts": 100.0, "image_path": str(img1)}, {"ts": 1000.0, "image_path": str(img2)},
                      {"ts": 2000.0, "image_path": str(img3)}]  # senza clip
            segs = [Segment(Path("/x/a.mp4"), 900, 1200, 1)]
            clips = TL.clip_segments(events, segs, 10, 20)
            self.assertEqual([(c.start, c.end, c.path.name) for c in clips], [(90.0, 120.0, "0.mp4")])


class TimelineZoomTest(unittest.TestCase):
    """Zoom della barra di Rivedi (serve PySide6: sul Mac sì, nell'ambiente dei test automatici forse no)."""

    def test_zoom_history(self):
        import importlib.util

        if importlib.util.find_spec("PySide6") is None:
            self.skipTest("PySide6 non disponibile")
        from PySide6.QtWidgets import QApplication

        from mirto.gui.review import MIN_WINDOW, TimelineBar

        _app = QApplication.instance() or QApplication([])
        b = TimelineBar()
        b.set_day(0.0, 86400.0)
        self.assertFalse(b.zoomed)
        b.zoom_to(3600, 7200)
        b.zoom_to(3600, 3610)  # troppo stretto: si allarga al minimo
        self.assertEqual(b.t1 - b.t0, MIN_WINDOW)
        b.set_cursor(5000)  # la riproduzione esce dalla finestra: la finestra la segue
        self.assertTrue(b.t0 <= 5000 <= b.t1)
        b.zoom_back()
        self.assertEqual((b.t0, b.t1), (3600, 7200))
        b.zoom_back()
        self.assertFalse(b.zoomed)


class DimensioneDuranteLaScritturaTest(unittest.TestCase):
    """Il registratore capisce che il flusso è vivo guardando se l'ultimo spezzone cresce.

    Su Windows i dati della voce di cartella non si aggiornano finché il file è aperto: ``os.scandir``
    continua a dichiarare la dimensione di quando il file è nato. Il risultato, su un PC vero (W-01,
    19/10): «Registrazione ferma: nessun dato dalla camera da 30 s» ogni trenta secondi, ffmpeg
    ammazzato e riavviato all'infinito, mentre nello stesso minuto il registro diceva «cancellati 2
    spezzoni» — cioè i file c'erano e si rinnovavano.

    Qui la voce di cartella mente come su Windows, e la dimensione deve venire lo stesso dal file.
    """

    def test_the_size_comes_from_the_file_not_from_the_folder(self):
        from mirto.recording import store as S

        with tempfile.TemporaryDirectory() as tmp:
            cartella = Path(tmp)
            spezzone = cartella / "20261019-182352.mp4"
            spezzone.write_bytes(b"x" * 4096)

            class VoceBugiarda:          # os.scandir su Windows, con lo spezzone ancora aperto
                name = spezzone.name
                path = str(spezzone)

                def stat(self, *a, **k):
                    return os.stat_result((0,) * 10)     # dimensione 0: per la cartella il file è fermo

            class Scansione:
                def __enter__(self): return [VoceBugiarda()]
                def __exit__(self, *a): return False

            vero = S.os.scandir
            S.os.scandir = lambda _cartella: Scansione()
            try:
                percorso, dimensione = S.RecordingStore.newest(cartella)
            finally:
                S.os.scandir = vero

        self.assertEqual(percorso, spezzone)
        self.assertEqual(dimensione, 4096,
                         "la dimensione deve arrivare dal file aperto, non dalla voce di cartella")

    def test_a_file_that_cannot_be_opened_falls_back_to_the_folder(self):
        """Se il file sparisce fra una riga e l'altra non si alza un'eccezione: zero e si va avanti."""
        from mirto.recording.store import dimensione_vera

        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(dimensione_vera(Path(tmp) / "non-esiste.mp4"), 0)


class PuntiniDegliEventiTest(unittest.TestCase):
    """I puntini della barra di Rivedi: raggruppati per posizione, e calcolati una volta sola.

    Con una giornata piena di eventi la barra diventava lenta: il raggruppamento si rifaceva a ogni
    ridisegno, cioè dieci volte al secondo mentre il video scorre (segnalato sul PC Windows il
    19/10). Qui si prova la regola, che adesso vive fuori dal disegno e si può misurare.
    """

    @staticmethod
    def x_of(ts):
        return (ts - 1_000_000) / 10          # dieci secondi = un pixel

    def eventi(self, *tempi, label="person"):
        return [{"ts": 1_000_000 + t, "label": label} for t in tempi]

    def test_events_closer_than_four_pixels_become_one_dot(self):
        from mirto.recording import timeline as TL

        # 0 s, 10 s e 20 s stanno in tre pixel: un puntino solo, che dice «qui ce ne sono tre»
        gruppi = TL.group_events(self.eventi(0, 10, 20), 1_000_000, 1_000_100, self.x_of)
        self.assertEqual(len(gruppi), 1)
        x, genere, quanti = gruppi[0]
        self.assertEqual((genere, quanti), ("person", 3))

    def test_events_far_apart_stay_separate(self):
        from mirto.recording import timeline as TL

        gruppi = TL.group_events(self.eventi(0, 60, 120), 1_000_000, 1_000_200, self.x_of)
        self.assertEqual([q for _x, _g, q in gruppi], [1, 1, 1])

    def test_a_person_wins_over_an_animal_and_a_sound_in_the_same_dot(self):
        from mirto.recording import timeline as TL

        misti = (self.eventi(0, label="sound:dog") + self.eventi(10, label="cat")
                 + self.eventi(20, label="person"))
        gruppi = TL.group_events(misti, 1_000_000, 1_000_100, self.x_of)
        self.assertEqual(len(gruppi), 1)
        self.assertEqual(gruppi[0][1], "person",
                         "il puntino deve dire che lì c'è una persona, non fare una media")

    def test_events_outside_the_visible_window_are_not_drawn(self):
        from mirto.recording import timeline as TL

        gruppi = TL.group_events(self.eventi(-500, 50, 5000), 1_000_000, 1_000_100, self.x_of)
        self.assertEqual(len(gruppi), 1, "solo quello dentro la finestra visibile")

