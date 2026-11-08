# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Strumenti usati da ``install.sh``: configurazione della camera (MediaMTX) a partire da ``node.toml``.

Comandi (eseguiti dall'installazione, come amministratore)::

    python3 -m mirto_node.install_tools prepara-config  /etc/mirto-node/node.toml [vecchio mediamtx.yml]
    python3 -m mirto_node.install_tools mediamtx         /etc/mirto-node/node.toml /etc/mirto-node/rtsp
    python3 -m mirto_node.install_tools camera-attiva    /etc/mirto-node/node.toml
    python3 -m mirto_node.install_tools avahi-camera     (annuncio in rete della camera)
    python3 -m mirto_node.install_tools registrazioni    /etc/mirto-node/node.toml
        (stampa: cartella delle registrazioni e disco USB, separati da una tabulazione; vuoto se spenta)

Tutto ciò che decide il comportamento (risoluzione, NoIR, credenziali) sta in ``node.toml`` e nel file delle
credenziali: MediaMTX si rigenera a ogni installazione, quindi è sempre allineato.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

CAMERA_DEFAULTS = {"attiva": True, "noir": False, "larghezza": 1280, "altezza": 720, "fps": 15, "bitrate": 1500}
CAMERA_PATH = "cam"
# Il percorso del flusso che porta anche l'audio (vedi `flusso_audio.py`). Scritto qui e non
# importato da `recording` perche' questo modulo gira anche dentro l'installazione, dove `recording`
# non e' ancora al suo posto.
AUDIO_PATH = CAMERA_PATH + "-audio"


CAM_AVAHI = """<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">Mirto Cam %h</name>
  <service>
    <type>_rtsp._tcp</type>
    <port>8554</port>
    <txt-record>path=/cam</txt-record>
    <txt-record>transport=tcp</txt-record>
    <txt-record>mirto=1</txt-record>
  </service>
</service-group>
"""


def read_toml(path: Path) -> dict:
    import tomllib  # noqa: PLC0415 (Python 3.11+, presente sul Raspberry)

    with open(path, "rb") as f:
        return tomllib.load(f)


def camera_section(noir: bool) -> str:
    return f"""
[camera]
# Camera collegata al Raspberry, trasmessa da MediaMTX (rtsp://<nodo>:8554/cam, con password)
attiva = true
# true per la Camera Module NoIR (senza filtro infrarossi): usa le regolazioni di colore adatte
noir = {"true" if noir else "false"}
larghezza = 1280
altezza = 720
fps = 15
# kbit/s del video: 1500 bastano per 720p (circa 16 GB al giorno se Mirto registra di continuo)
bitrate = 1500
"""


def was_noir(old_mediamtx: Path | None) -> bool:
    """La configurazione fatta a mano usava già le regolazioni NoIR?"""
    try:
        return bool(old_mediamtx) and "noir" in old_mediamtx.read_text()
    except OSError:
        return False


def prepare_config(toml_path: Path, old_mediamtx: Path | None = None) -> bool:
    """Aggiunge a ``node.toml`` la sezione [camera] se manca (riprendendo NoIR dalla vecchia configurazione)."""
    text = toml_path.read_text()
    if re.search(r"^\[camera\]", text, re.M):
        return False
    toml_path.write_text(text.rstrip() + "\n" + camera_section(was_noir(old_mediamtx)))
    return True


def camera_cfg(cfg: dict) -> dict:
    out = dict(CAMERA_DEFAULTS)
    out.update({k: v for k, v in (cfg.get("camera") or {}).items() if k in CAMERA_DEFAULTS})
    return out


def detect_sensor(run=subprocess.run) -> str:
    """Sensore della camera collegata (es. "imx219"), dall'elenco di rpicam-hello; vuoto se nessuna."""
    for cmd in (["rpicam-hello", "--list-cameras"], ["libcamera-hello", "--list-cameras"]):
        try:
            res = run(cmd, capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            continue
        m = re.search(r"^\s*\d+\s*:\s*(\w+)", (res.stdout or "") + (res.stderr or ""), re.M)
        if m:
            return m.group(1).lower()
    return ""


CAMERA_NON_VISTA = "la camera è attiva in configurazione ma il sistema NON la vede"
CAMERA_NON_VISTA_RIMEDIO = (
    "MediaMTX espone rtsp://…/cam senza nessuno che trasmetta, e in Mirto la camera resta «non\n"
    "raggiungibile» per sempre. Nove volte su dieci è il cavo flat: spegni il nodo, stacca e rimetti\n"
    "il flat (contatti verso il connettore HDMI, fermo premuto a fondo fino allo scatto), riaccendi e\n"
    "controlla con  rpicam-hello --list-cameras .\n"
    "Se su questo nodo non c'è nessuna camera, spegnila:  mirto-nodo camera  → camera spenta.")


# Formati su cui si può registrare senza formattare: ext4 è il migliore (resiste allo
# spegnimento improvviso), exfat e vfat si accettano perché arrivano già così dal negozio.
FORMATI_BUONI = ("ext4", "exfat", "vfat")

RESOLUTIONS = [  # (etichetta, larghezza, altezza, fps) — le propone sia mirto-nodo sia Mirto
    ("640×480 · 15 fps — leggera, per Wi-Fi debole", 640, 480, 15),
    ("1280×720 · 15 fps — consigliata", 1280, 720, 15),
    ("1920×1080 · 15 fps — dettagliata, più banda", 1920, 1080, 15),
    ("1280×720 · 25 fps — movimenti più fluidi", 1280, 720, 25),
]


def risoluzione_valida(larghezza, altezza, fps) -> tuple[int, int, int] | None:
    """La terna chiesta è fra quelle proposte? Il nodo esegue, non interpreta: fuori elenco si rifiuta."""
    try:
        terna = (int(larghezza), int(altezza), int(fps))
    except (TypeError, ValueError):
        return None
    return terna if any(terna == (w, h, f) for _e, w, h, f in RESOLUTIONS) else None


def riga_camera(sensor: str, noir: bool = False, tuning: str = "") -> str:
    """La riga che l'installazione stampa sulla camera: il sensore, o l'avviso con il rimedio."""
    if sensor:
        return f"Camera: {sensor}{' · NoIR' if noir else ''}{' · ' + tuning if tuning else ''}"
    return f"ATTENZIONE: {CAMERA_NON_VISTA}.\n{CAMERA_NON_VISTA_RIMEDIO}"


def host_slug(name: str) -> str:
    """"Garage Nord" → "garage-nord" (nome in rete: lettere, cifre e trattini).

    Sta qui, e non in ``cli.py``, perché serve anche al servizio: il wizard di Mirto manda il nome
    scelto a ``POST /configura/nome`` e il nodo deve ricavarne lo stesso nome in rete di sempre.
    """
    import unicodedata  # noqa: PLC0415

    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:40]


def board_model() -> str:
    try:
        return Path("/proc/device-tree/model").read_text(errors="replace").strip("\x00 \n")
    except OSError:
        return ""


def posix(percorso) -> str:
    """Percorso con le barre in avanti: i file generati (mediamtx.yml, fstab) sono per il Raspberry,
    anche quando a generarli è un Windows che userebbe le barre rovesciate."""
    return str(percorso).replace("\\", "/")


def tuning_file(sensor: str, noir: bool, model: str, root: Path = Path("/usr/share/libcamera/ipa/rpi")) -> str:
    """Regolazioni di colore per i sensori NoIR (vc4 = Pi 4 e precedenti, pisp = Pi 5)."""
    if not (noir and sensor):
        return ""
    family = "pisp" if "Raspberry Pi 5" in model else "vc4"
    path = root / family / f"{sensor}_noir.json"
    return str(path) if path.exists() else ""


def api_credentials(path: Path, utente: str = "mirto-api") -> tuple[str, str]:
    """Utente e password locali di MediaMTX (``/etc/mirto-node/mtx-api``); creati la prima volta.

    Serve due volte: per l'**API** (accendere e spegnere la registrazione) e per **pubblicare** il
    flusso con l'audio. Sono due utenti distinti e non uno con due permessi: entrambi vivono in un
    file 0600 su localhost, ma un segreto che apre l'API *e* permette di pubblicare sulla camera è
    una cosa sola che fa due danni.
    """
    if not path.exists():
        import secrets  # noqa: PLC0415

        path.write_text(f"{utente}:{secrets.token_hex(16)}\n")
        path.chmod(0o600)
    return read_credentials(path)


def publish_credentials(path: Path) -> tuple[str, str]:
    """Utente e password con cui il nodo **pubblica** il flusso con l'audio (``mtx-pub``)."""
    return api_credentials(path, "mirto-pub")


def delete_after_seconds(cam: dict, max_bytes: int) -> int:
    """Freno di emergenza per MediaMTX: se il servizio del nodo è fermo nessuno pulisce, quindi MediaMTX cancella
    da sé i file più vecchi del tempo che occupa ``max_bytes`` (×1,2). 0 = nessun limite (disco USB senza massimo)."""
    if max_bytes <= 0:
        return 0
    bits_per_s = max(200, int(cam.get("bitrate", 1500))) * 1000
    return max(3600, int(max_bytes * 8 / bits_per_s * 1.2))


def mediamtx_for(cfg: dict, user: str, pwd: str, tuning: str, conf_dir: Path) -> str:
    """mediamtx.yml completo per questa configurazione (camera, registrazione, API con password)."""
    from .recording import NodeRecorder, rec_folder, recording_cfg  # noqa: PLC0415

    cam = camera_cfg(cfg)
    rec = recording_cfg(cfg)
    # Il percorso con l'audio esiste solo se quel nodo l'ha acceso: un percorso pubblicabile che
    # nessuno pubblica è una porta aperta che non serve a niente.
    pub = publish_credentials(conf_dir / "mtx-pub") if rec.get("audio") else None
    if not rec["attiva"]:
        return mediamtx_yaml(cam, user, pwd, tuning, "", api_credentials(conf_dir / "mtx-api"), pub=pub)
    max_bytes = NodeRecorder(rec, api=object()).max_bytes
    return mediamtx_yaml(cam, user, pwd, tuning, str(rec_folder(rec)), api_credentials(conf_dir / "mtx-api"),
                         delete_after_seconds(cam, max_bytes), pub=pub)


def mediamtx_yaml(cam: dict, user: str, password: str, tuning: str = "", rec_root: str = "",
                  api: tuple[str, str] | None = None, delete_after: int = 0,
                  pub: tuple[str, str] | None = None) -> str:
    """Configurazione completa di MediaMTX: solo RTSP (TCP) e vista nel browser, con password."""
    lines = [
        "# Generato da Mirto Node: NON modificare a mano (si rigenera a ogni installazione).",
        "# Le impostazioni della camera stanno in /etc/mirto-node/node.toml, sezione [camera].",
        "logLevel: info",
        "rtspTransports: [tcp]",
        # API solo locale e con password: il servizio del nodo accende e spegne la registrazione
        f"api: {'yes' if api else 'no'}",
        "apiAddress: 127.0.0.1:9997",
        "rtmp: no",
        "hls: no",
        "srt: no",
        "moq: no",
        "authInternalUsers:",
        f"  - user: {user}",
        f"    pass: {password}",
        "    ips: []",
        "    permissions:",
        "      - action: read",
        "        path:",
        *([f"  - user: {api[0]}", f"    pass: {api[1]}", "    ips: [\"127.0.0.1\", \"::1\"]", "    permissions:",
            "      - action: api"] if api else []),
        # Chi pubblica il flusso con l'audio: **solo** da localhost e **solo** su quel percorso. È il
        # servizio del nodo, che gira su questa macchina: non c'è nessun motivo perché quella
        # password valga da fuori, né su un altro percorso.
        *([f"  - user: {pub[0]}", f"    pass: {pub[1]}", "    ips: [\"127.0.0.1\", \"::1\"]", "    permissions:",
            "      - action: publish", f"        path: {AUDIO_PATH}",
            "      - action: read", f"        path: {CAMERA_PATH}"] if pub else []),
        "  - user: any",
        "    pass:",
        "    ips: [\"127.0.0.1\", \"::1\"]",
        "    permissions:",
        "      - action: metrics",
        "paths:",
        f"  {CAMERA_PATH}:",
        "    source: rpiCamera",
        f"    rpiCameraWidth: {int(cam['larghezza'])}",
        f"    rpiCameraHeight: {int(cam['altezza'])}",
        f"    rpiCameraFPS: {int(cam['fps'])}",
        f"    rpiCameraBitrate: {max(200, int(cam['bitrate'])) * 1000}",
        # un fotogramma chiave ogni 2 s: clip degli eventi che iniziano e finiscono più vicino all'istante giusto
        f"    rpiCameraIDRPeriod: {max(1, int(cam['fps']) * 2)}",
    ]
    if tuning:
        lines.append(f"    rpiCameraTuningFile: {tuning}")
    coda: list[str] = []
    if pub:
        # Nessun «source»: lo pubblica il servizio del nodo con ffmpeg (`flusso_audio.py`). Il percorso
        # c'è anche quando il muxer non sta pubblicando — altrimenti ffmpeg non avrebbe dove bussare.
        coda = [f"  {AUDIO_PATH}:"]
    if rec_root:
        # registrazione: spezzoni da 1 minuto, ora UTC nel nome (il servizio MediaMTX gira con TZ=UTC0);
        # parte spenta: la accende il servizio del nodo quando il disco c'è; la pulizia ad anello la fa il nodo
        lines += [
            "    record: no",
            f"    recordPath: {posix(rec_root)}/%path/%Y%m%d-%H%M%S",
            "    recordFormat: fmp4",
            "    recordSegmentDuration: 60s",
            "    recordPartDuration: 1s",
            f"    recordDeleteAfter: {int(delete_after)}s",
        ]
        # Le stesse regole sul percorso con l'audio: chi registra e' uno solo alla volta (lo decide il
        # servizio del nodo accendendo `record` via API), ma la configurazione deve esserci su
        # entrambi — altrimenti accendere la registrazione sul percorso sbagliato non scriverebbe
        # niente e nessuno saprebbe perche'.
        coda += ["    record: no",
                 f"    recordPath: {posix(rec_root)}/%path/%Y%m%d-%H%M%S",
                 "    recordFormat: fmp4",
                 "    recordSegmentDuration: 60s",
                 "    recordPartDuration: 1s",
                 f"    recordDeleteAfter: {int(delete_after)}s"] if coda else []
    return "\n".join(lines + coda) + "\n"


def read_credentials(path: Path) -> tuple[str, str]:
    user, _, pwd = path.read_text().strip().partition(":")
    if not (user and pwd) or not re.fullmatch(r"[A-Za-z0-9._-]+", user + pwd):
        raise ValueError(f"credenziali non valide in {path} (serve una riga utente:password, solo lettere e cifre)")
    return user, pwd


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd, args = argv[0], [Path(a) for a in argv[1:]]
    if cmd == "avahi-camera":
        sys.stdout.write(CAM_AVAHI)
        return 0
    if cmd == "prepara-config":
        changed = prepare_config(args[0], args[1] if len(args) > 1 else None)
        print("sezione [camera] aggiunta" if changed else "sezione [camera] già presente")
        return 0
    cfg = read_toml(args[0])
    cam = camera_cfg(cfg)
    from .recording import rec_folder, recording_cfg  # noqa: PLC0415

    rec = recording_cfg(cfg)
    if cmd == "registrazioni":
        if rec["attiva"] and cam["attiva"]:
            print(f"{rec_folder(rec)}\t{str(rec.get('disco') or '').strip()}")
        return 0
    if cmd == "camera-attiva":
        return 0 if cam["attiva"] else 1
    if cmd == "mediamtx":
        user, pwd = read_credentials(args[1])
        sensor = detect_sensor()
        tuning = tuning_file(sensor, bool(cam["noir"]), board_model())
        # Prima diceva «Camera: non rilevata» in una riga fra cento: provando il 20/10 quel silenzio
        # è costato mezz'ora, con l'errore vero due livelli sotto (Mirto «can't open the camera»,
        # MediaMTX «selected camera is not available», e solo in fondo «libcamera interfaces=0»).
        sys.stderr.write(riga_camera(sensor, bool(cam["noir"]), tuning) + "\n")
        sys.stdout.write(mediamtx_for(cfg, user, pwd, tuning, args[1].parent))
        return 0
    print(f"comando sconosciuto: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
