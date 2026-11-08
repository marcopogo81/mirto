# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Nodo simulato: implementa il contratto API dei nodi per sviluppo e test.

Serve anche come **implementazione di riferimento** per il progetto Raspberry:
stessi endpoint, stessa firma HMAC, stessi limiti di sicurezza.

Avvio:  ``python -m mirto --simulatore-nodo --porta 8443 --nome-nodo ingresso``
(più istanze su porte diverse simulano più nodi).
"""

from __future__ import annotations

import base64
import datetime
import io
import json
import logging
import math
import os
import ssl
import struct
import tempfile
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .signing import Verifier, generate_key

log = logging.getLogger(__name__)

# L'API che questo finto nodo **implementa davvero**, non quella a cui era arrivato il giorno che è
# stato scritto: dichiarava 1.3 mentre eseguiva già «volte» e pubblicava «durate», che sono 1.8. Il
# numero serve a Mirto per dire «questo nodo è indietro, aggiornalo»: tenerlo vecchio voleva dire
# far comparire quell'avviso in ogni dimostrazione fatta col simulatore (trovato il 23/10).
API_VERSION = "1.9"
SIREN_MAX_SECONDS = 180
DETERRENT_MAX_SECONDS = 30

SUONO_FINTO = 2.0   # quanto "dura" un suono del nodo finto: serve a contare le ripetizioni
DEFAULT_ACTIONS = [
    {"nome": "sirena", "tipo": "siren", "descrizione": "Sirena (max 3 minuti)"},
    {"nome": "luci", "tipo": "light", "descrizione": "Luci"},
    {"nome": "uscita_1", "tipo": "generic", "descrizione": "Uscita generica (relè 1)"},
    {"nome": "messaggio", "tipo": "generic", "descrizione": "Messaggio preregistrato"},
    {"nome": "dissuasore", "tipo": "deterrent", "descrizione": "Suoni contro uccelli e animali",
     "suoni": ["allarme_corvi", "cannoncino", "falco", "rapace", "sparo"],
     "durate": {n: SUONO_FINTO for n in ("allarme_corvi", "cannoncino", "falco", "rapace", "sparo")}},
    # L'audio generato (23/10): qui le lunghezze sono quelle vere dei suoni del nodo, non SUONO_FINTO,
    # perche' su questa differenza si vede subito se l'interfaccia chiede «quante volte» come deve.
    {"nome": "audio_generato", "tipo": "deterrent",
     "descrizione": "Audio generato dal nodo (UFO, jingle, Matrix…)",
     "suoni": ["jingle", "matrix", "ufo"],
     "durate": {"jingle": 1.7, "matrix": 8.0, "ufo": 3.5}},
]


def make_self_signed(common_name: str, folder: Path) -> tuple[Path, Path]:
    """Certificato self-signed per il nodo (valido 10 anni)."""
    from cryptography import x509  # noqa: PLC0415
    from cryptography.hazmat.primitives import hashes, serialization  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric import ec  # noqa: PLC0415
    from cryptography.x509.oid import NameOID  # noqa: PLC0415

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(common_name), x509.DNSName("localhost")]), False)
        .sign(key, hashes.SHA256())
    )
    folder.mkdir(parents=True, exist_ok=True)
    cert_path, key_path = folder / "node.crt", folder / "node.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    os.chmod(key_path, 0o600)
    return cert_path, key_path


def tone_wav(seconds: float, freq: float = 440.0, rate: int = 8000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        frames = b"".join(struct.pack("<h", int(3000 * math.sin(2 * math.pi * freq * i / rate)))
                          for i in range(int(seconds * rate)))
        w.writeframes(frames)
    return buf.getvalue()


class SimulatedNode:
    """Stato del nodo simulato (dispositivi accesi, registro delle chiamate)."""

    def __init__(self, name: str, key_hex: str, actions: list[dict] | None = None, cameras: list[dict] | None = None):
        self.name = name
        self.cameras = list(cameras or [])  # camere dichiarate in /info (API 1.1)
        self.verifier = Verifier(key_hex)
        self.actions = {a["nome"]: dict(a) for a in (actions or DEFAULT_ACTIONS)}
        self.active: dict[str, float] = {}       # azione → spegnimento previsto
        self.calls: list[tuple[str, str, dict]] = []
        self.played: list[tuple[str, int]] = []
        # I suoni caricati, qui solo in memoria: il nodo finto non ha una cartella. Servono perché
        # anche una dimostrazione col simulatore deve poter provare «Impostazioni → Suoni» (24/10).
        self.caricati: dict[str, float] = {}
        self.saltate: list[str] = []              # chiamate cadute perché il comando era già in corso
        self.ripetizioni: list[tuple] = []        # (comando, suono, volte) chieste con «volte»
        self._lock = threading.Lock()
        self.started = time.time()

    def _expire(self) -> None:
        now = time.time()
        with self._lock:
            for name, until in list(self.active.items()):
                if until <= now:
                    del self.active[name]
                    log.info("[%s] %s spenta (scadenza)", self.name, name)

    def activate(self, name: str, payload: dict) -> dict:
        if name not in self.actions:
            raise KeyError(name)
        # Come il nodo vero: se quel comando è ancora in corso la chiamata **non fa niente** e lo dice.
        # Il simulatore prima lo riaccendeva, ed era un simulatore più gentile del nodo: una catena che
        # suona una volta invece di tre passava verde qui e sbagliava sul Raspberry.
        with self._lock:
            resta = self.active.get(name, 0.0) - time.time()
        if resta > 0:
            self.saltate.append(name)
            log.info("[%s] %s era già in corso: salta (restano %.1f s)", self.name, name, resta)
            return {"esito": "ok", "azione": name, "gia_attiva": True, "restano_s": round(resta, 1)}
        kind = self.actions[name].get("tipo")
        duration = float(payload.get("durata", 60))
        if kind == "siren":
            duration = min(duration, SIREN_MAX_SECONDS)  # limite invalicabile del nodo
        if kind == "deterrent":
            duration = min(duration, DETERRENT_MAX_SECONDS)
            sound = payload.get("suono") or ""
            if sound and sound not in self.actions[name].get("suoni", []):
                raise KeyError(sound)
        # «volte» (API 1.8): il nodo vero conta le ripetizioni e calcola lui i secondi. Qui si fa lo
        # stesso — un simulatore più gentile del nodo fa passare verdi le cose che sbagliano in
        # giardino, e il 22/10 era già successo.
        volte = int(payload.get("volte") or 0)
        if volte > 0 and kind == "deterrent":
            duration = min(volte * SUONO_FINTO + 0.2, DETERRENT_MAX_SECONDS)
            self.ripetizioni.append((name, payload.get("suono") or "", volte))
        duration = max(0.2 if volte else 1.0, min(duration, 1800.0))
        with self._lock:
            self.active[name] = time.time() + duration
        log.info("[%s] %s ACCESA per %.0f s (evento %s)", self.name, name, duration, payload.get("evento_id"))
        return {"esito": "ok", "azione": name, "durata": duration}

    def deactivate(self, name: str) -> dict:
        with self._lock:
            self.active.pop(name, None)
        return {"esito": "ok", "azione": name}

    def stop_all(self) -> dict:
        with self._lock:
            stopped = list(self.active)
            self.active.clear()
        return {"esito": "ok", "spente": stopped}

    def sounds_state(self) -> dict:
        nostri = {n: SUONO_FINTO for a in self.actions.values() for n in (a.get("durate") or {})}
        suoni = [{"nome": n, "secondi": s, "nostro": True} for n, s in sorted(nostri.items())]
        suoni += [{"nome": n, "secondi": s, "nostro": False} for n, s in sorted(self.caricati.items())]
        return {"suoni": suoni, "massimo_s": 30, "massimo_byte": 2 * 1024 * 1024, "cartella": "(simulato)"}

    def add_sound(self, dati: dict) -> dict:
        import base64  # noqa: PLC0415
        import re  # noqa: PLC0415

        nome = re.sub(r"[^\w-]", "_", str(dati.get("nome") or "").strip().rsplit(".", 1)[0].lower())[:40].strip("_-")
        if not nome:
            raise ValueError("serve un nome per il suono")
        if any(nome in (a.get("durate") or {}) for a in self.actions.values()):
            raise ValueError(f"«{nome}» è un suono nostro: scegline un altro nome")
        crudo = base64.b64decode(str(dati.get("wav") or ""))
        if not crudo:
            raise ValueError("il suono è vuoto")
        # Il nodo finto non decodifica niente: tiene il nome e una durata plausibile, che è tutto
        # quello che serve a far vedere l'elenco e a provare l'interfaccia.
        secondi = round(max(0.3, min(30.0, len(crudo) / (2 * 22050))), 2)
        self.caricati[nome] = secondi
        for a in self.actions.values():
            if a.get("tipo") == "deterrent" and a.get("nome") == "dissuasore":
                a.setdefault("suoni", []).append(nome)
                a.setdefault("durate", {})[nome] = secondi
        return {"esito": "ok", "nome": nome, "secondi": secondi}

    def remove_sound(self, dati: dict) -> dict:
        nome = str(dati.get("nome") or "").strip().lower()
        if nome not in self.caricati:
            raise ValueError(f"suono «{nome}» non presente sul nodo")
        self.caricati.pop(nome)
        for a in self.actions.values():
            if nome in (a.get("suoni") or []):
                a["suoni"].remove(nome)
                (a.get("durate") or {}).pop(nome, None)
        return {"esito": "ok", "nome": nome}

    def status(self) -> dict:
        self._expire()
        with self._lock:
            active = {k: round(v - time.time(), 1) for k, v in self.active.items()}
        st = {"nome": self.name, "attive": active,
              "attivo_da_s": int(time.time() - self.started)}
        if self.cameras:
            st["camere"] = [{"id": c.get("id"), "nome": c.get("nome"), "porta": c.get("rtsp_porta"),
                             "percorso": c.get("percorso"), "in_linea": True} for c in self.cameras]
            st["host"], st["indirizzi"] = "localhost", ["127.0.0.1"]
        return st


def make_handler(node: SimulatedNode):
    class Handler(BaseHTTPRequestHandler):
        server_version = "MirtoNodeSim/" + API_VERSION

        def log_message(self, fmt, *args):  # meno rumore
            log.debug("[%s] " + fmt, node.name, *args)

        def _send(self, code: int, obj) -> None:
            data = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> bytes:
            n = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(n) if n else b""

        def _auth(self, body: bytes) -> bool:
            ok, why = node.verifier.verify(self.command, self.path, self.headers, body)
            if not ok:
                log.warning("[%s] richiesta rifiutata: %s", node.name, why)
                self._send(401, {"errore": why})
            return ok

        def do_GET(self):  # noqa: N802
            if not self._auth(b""):
                return
            node.calls.append(("GET", self.path, {}))
            if self.path == "/info":
                self._send(200, {"nome": node.name, "tipo": "simulato", "modello": "Nodo simulato",
                                 "versione_api": API_VERSION, "audio": {"uscita": "simulata", "microfono": True},
                                 "azioni": list(node.actions), "camere": node.cameras,
                                 "dispositivi": [{"nome": n, "tipo": a.get("tipo"), **({"suoni": a["suoni"]} if a.get("suoni") else {})}
                                                 for n, a in node.actions.items()]})
            elif self.path == "/azioni":
                self._send(200, {"azioni": list(node.actions.values())})
            elif self.path == "/stato":
                self._send(200, node.status())
            elif self.path == "/suoni":
                self._send(200, node.sounds_state())
            elif self.path == "/audio/flusso":  # 2 s di tono a 16 kHz, poi il flusso si chiude
                self.send_response(200)
                self.send_header("Content-Type", "audio/L16; rate=16000; channels=1")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                pcm = b"".join(struct.pack("<h", int(3000 * math.sin(2 * math.pi * 440 * i / 16000)))
                               for i in range(32000))
                try:
                    self.wfile.write(pcm)
                except OSError:
                    pass
            else:
                self._send(404, {"errore": "percorso sconosciuto"})

        def do_POST(self):  # noqa: N802
            body = self._body()
            if not self._auth(body):
                return
            parts = self.path.strip("/").split("/")
            if parts[:1] == ["suoni"]:
                node.calls.append(("POST", self.path, {}))
                dati = json.loads(body or b"{}")
                try:
                    if parts[1:] == ["togli"]:
                        self._send(200, node.remove_sound(dati))
                    else:
                        self._send(200, node.add_sound(dati))
                except ValueError as exc:
                    self._send(400, {"errore": str(exc)})
                return
            if parts[:1] == ["audio"]:
                node.calls.append(("POST", self.path, {}))
                if parts[1:] == ["riproduci"]:
                    node.played.append((self.headers.get("Content-Type", ""), len(body)))
                    self._send(200, {"esito": "ok", "byte": len(body)})
                elif parts[1:] == ["registra"]:
                    secs = min(120, max(1, int(json.loads(body or b"{}").get("durata", 5))))
                    self._send(200, {"mime": "audio/wav", "audio_base64": base64.b64encode(tone_wav(min(secs, 3))).decode()})
                else:
                    self._send(404, {"errore": "percorso sconosciuto"})
                return
            try:
                payload = json.loads(body or b"{}")
            except ValueError:
                self._send(400, {"errore": "JSON non valido"})
                return
            node.calls.append(("POST", self.path, payload))
            try:
                if parts == ["azioni", "ferma-tutto"]:
                    self._send(200, node.stop_all())
                elif len(parts) == 3 and parts[0] == "azioni" and parts[2] == "attiva":
                    self._send(200, node.activate(parts[1], payload))
                elif len(parts) == 3 and parts[0] == "azioni" and parts[2] == "disattiva":
                    self._send(200, node.deactivate(parts[1]))
                else:
                    self._send(404, {"errore": "percorso sconosciuto"})
            except KeyError:
                self._send(404, {"errore": "azione sconosciuta"})

    return Handler


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # connessioni chiuse dal client (es. lettura del solo certificato): non sono errori
        log.debug("Connessione chiusa da %s", client_address)


class SimulatorServer:
    def __init__(self, name: str = "simulato", port: int = 0, key_hex: str | None = None,
                 cert_dir: Path | None = None, host: str = "127.0.0.1", cameras: list[dict] | None = None):
        self.key_hex = key_hex or generate_key()
        self.node = SimulatedNode(name, self.key_hex, cameras=cameras)
        folder = cert_dir or Path(tempfile.mkdtemp(prefix=f"mirto-node-{name}-"))
        self.cert_path, key_path = make_self_signed(name, folder)
        self.httpd = _QuietServer((host, port), make_handler(self.node))
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(self.cert_path, key_path)
        self.httpd.socket = ctx.wrap_socket(self.httpd.socket, server_side=True)
        self.port = self.httpd.server_address[1]
        self.url = f"https://{host}:{self.port}"
        self._thread: threading.Thread | None = None

    def start(self) -> "SimulatorServer":
        self._thread = threading.Thread(target=self.httpd.serve_forever, name=f"sim-{self.node.name}", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def run_simulator(name: str = "simulato", port: int = 8443) -> int:
    from .nodes import fingerprint, pem_to_der  # noqa: PLC0415

    key = os.environ.get("MIRTO_NODE_KEY") or generate_key()
    srv = SimulatorServer(name=name, port=port, key_hex=key, host="0.0.0.0")
    fp = fingerprint(pem_to_der(srv.cert_path.read_text()))
    print(f"Nodo simulato '{name}' in ascolto su https://<questo-computer>:{srv.port}")
    print(f"Impronta del certificato: {fp}")
    print(f"Chiave condivisa (da inserire in Mirto): {key}", flush=True)
    srv.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        srv.stop()
    return 0
