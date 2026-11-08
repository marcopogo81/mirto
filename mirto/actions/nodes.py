# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Client verso i nodi: HTTPS con certificato fissato e richieste firmate.

**Abbinamento**: al primo collegamento Mirto scarica il certificato del nodo e
ne mostra l'impronta SHA-256; l'utente la confronta con quella mostrata dal
nodo e conferma. Da quel momento Mirto parla SOLO con quel certificato:
l'impronta viene controllata a ogni connessione, prima di inviare qualsiasi dato.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import logging
import ssl
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from ..config import config_dir
from ..credentials import SecretStore, node_key_name
from .signing import sign
from ..i18n import _

log = logging.getLogger(__name__)


class NodeError(RuntimeError):
    """Il nodo ha detto no, o non ha risposto.

    ``dati`` sono i campi che il nodo ha messo accanto al messaggio quando il rifiuto era una regola
    e non un guasto: ``motivo``, e i numeri che servono a dirlo con altre parole. La frase del nodo è
    in italiano sempre — è la sua lingua, come il registro — quindi chi vuole dirla a una persona
    guarda ``motivo`` e la scrive nella lingua di chi legge, invece di inoltrare l'italiano.
    """

    def __init__(self, message: str, dati: dict | None = None):
        super().__init__(message)
        self.dati = dict(dati or {})


@dataclass
class NodeConfig:
    id: str
    name: str
    url: str  # es. https://ingresso.local:8443

    @classmethod
    def from_dict(cls, d: dict) -> "NodeConfig":
        return cls(id=str(d["id"]), name=d.get("name") or str(d["id"]), url=d["url"].rstrip("/"))


def certs_dir() -> Path:
    p = config_dir() / "nodes"
    p.mkdir(parents=True, exist_ok=True)
    return p


def fingerprint(der: bytes) -> str:
    h = hashlib.sha256(der).hexdigest().upper()
    return ":".join(h[i:i + 2] for i in range(0, len(h), 2))


def pem_to_der(pem: str) -> bytes:
    return ssl.PEM_cert_to_DER_cert(pem)


def fetch_certificate(url: str, timeout: float = 10) -> tuple[str, str]:
    """Scarica il certificato presentato dal nodo. Restituisce (PEM, impronta)."""
    u = urllib.parse.urlparse(url)
    pem = ssl.get_server_certificate((u.hostname, u.port or 443), timeout=timeout)
    return pem, fingerprint(pem_to_der(pem))


def save_pinned_certificate(node_id: str, pem: str) -> Path:
    path = certs_dir() / f"{node_id}.pem"
    path.write_text(pem, encoding="ascii")
    return path


def load_pinned_certificate(node_id: str) -> str | None:
    path = certs_dir() / f"{node_id}.pem"
    return path.read_text(encoding="ascii") if path.exists() else None


class NodeClient:
    def __init__(self, node: NodeConfig, pinned_pem: str, key_hex: str, timeout: float = 10):
        self.node = node
        self.pinned_fp = fingerprint(pem_to_der(pinned_pem))
        self.key = key_hex
        self.timeout = timeout
        u = urllib.parse.urlparse(node.url)
        if u.scheme != "https":
            raise NodeError(_('Il nodo deve usare https://'))
        self.host, self.port = u.hostname, u.port or 443
        self.base_path = u.path.rstrip("/")
        # La verifica avviene confrontando l'impronta del certificato (pinning),
        # non tramite autorità di certificazione: il nodo usa un certificato self-signed.
        self._ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self._ctx.check_hostname = False
        self._ctx.verify_mode = ssl.CERT_NONE
        self._ctx.minimum_version = ssl.TLSVersion.TLSv1_2

    @classmethod
    def from_config(cls, d: dict, secrets: SecretStore) -> "NodeClient":
        node = NodeConfig.from_dict(d)
        pem = load_pinned_certificate(node.id)
        key = secrets.get(node_key_name(node.id))
        if not pem:
            raise NodeError(_('Nodo {name}: certificato non abbinato', name=node.name))
        if not key:
            raise NodeError(_('Nodo {name}: chiave condivisa mancante', name=node.name))
        return cls(node, pem, key)

    def request(self, method: str, path: str, payload=None, raw: bytes | None = None,
                content_type: str = "application/json", timeout: float | None = None):
        body = raw if raw is not None else (json.dumps(payload).encode() if payload is not None else b"")
        full_path = self.base_path + path
        headers = sign(self.key, method, full_path, body)
        if body:
            headers["Content-Type"] = content_type
        conn = http.client.HTTPSConnection(self.host, self.port, timeout=timeout or self.timeout, context=self._ctx)
        try:
            conn.connect()
            der = conn.sock.getpeercert(binary_form=True)
            if not der or fingerprint(der) != self.pinned_fp:
                raise NodeError(_('Nodo {name}: certificato diverso da quello abbinato! Connessione interrotta.', name=self.node.name))
            conn.request(method, full_path, body=body or None, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            ctype = resp.getheader("Content-Type", "")
        finally:
            conn.close()
        if resp.status >= 400:
            corpo: dict = {}
            try:
                letto = json.loads(data)
                corpo = letto if isinstance(letto, dict) else {}
                msg = corpo.get("errore", "")
            except Exception:
                msg = data[:200].decode(errors="replace")
            raise NodeError(f"Nodo {self.node.name}: {resp.status} {msg}", corpo)
        if "application/json" in ctype:
            return json.loads(data or b"null")
        return data, ctype

    # --- contratto API ------------------------------------------------------
    def info(self) -> dict:
        return self.request("GET", "/info")

    def actions(self) -> list[dict]:
        return self.request("GET", "/azioni").get("azioni", [])

    def status(self) -> dict:
        return self.request("GET", "/stato")

    def sounds(self) -> dict:
        """I suoni del nodo e i **limiti** per caricarne altri (API 1.9)."""
        return self.request("GET", "/suoni", timeout=20)

    def add_sound(self, nome: str, wav: bytes) -> dict:
        """Manda un WAV già convertito. La conversione la fa il computer: il nodo suona e basta."""
        import base64  # noqa: PLC0415

        return self.request("POST", "/suoni",
                            {"nome": nome, "wav": base64.b64encode(wav).decode()}, timeout=60)

    def aggiorna(self, pacchetto: bytes, versione: str, sha256: str = "") -> dict:
        """Manda al nodo il suo codice nuovo (N-02, API 1.12). Risponde subito: installa dopo.

        Il timeout è largo perché il nodo, prima di rispondere, scompatta qualche centinaio di KB su
        una microSD — su un Pi 3B non è istantaneo. Ma non si aspetta l'installazione: quella riavvia
        il servizio, e la risposta non arriverebbe mai.
        """
        import base64  # noqa: PLC0415

        return self.request("POST", "/aggiorna",
                            {"versione": versione, "sha256": sha256,
                             "pacchetto": base64.b64encode(pacchetto).decode()}, timeout=120)

    def remove_sound(self, nome: str) -> dict:
        return self.request("POST", "/suoni/togli", {"nome": nome}, timeout=20)

    def activate(self, action: str, duration: float, event_id: str = "", camera: str = "", extra=None) -> dict:
        payload = {"durata": duration, "evento_id": event_id, "camera": camera}
        payload.update(extra or {})
        return self.request("POST", f"/azioni/{urllib.parse.quote(action)}/attiva", payload)

    def deactivate(self, action: str) -> dict:
        return self.request("POST", f"/azioni/{urllib.parse.quote(action)}/disattiva", {})

    def stop_all(self) -> dict:
        return self.request("POST", "/azioni/ferma-tutto", {})

    def prank(self, minuti: float) -> dict:
        """Accende la modalità Prank per tanti minuti, o la spegne con ``0`` (API 1.13)."""
        return self.request("POST", "/prank", {"minuti": minuti})

    def volume(self, percento: int) -> dict:
        """Il volume della cassa che il nodo sta usando, in percento (API 1.13).

        Non ``/configura/manopole``: quella chiede *gira questa manopola di questa scheda*, e chi la
        chiama deve sapere nomi e indici. Qui la domanda è *alza il volume della cassa*, e quale
        manopola sia lo sa il nodo.
        """
        return self.request("POST", "/volume", {"percento": int(percento)})

    def wifi_state(self) -> dict:
        """Le reti che vede il nodo, quella in uso e l'esito dell'ultimo collegamento (API 1.5)."""
        return self.request("GET", "/configura/wifi")

    def find_networks(self) -> dict:
        """Avvia la ricerca delle reti Wi-Fi sul nodo: una ventina di secondi (API 1.5)."""
        return self.request("POST", "/configura/wifi", {"azione": "cerca"}, timeout=20)

    def join_wifi(self, ssid: str, password: str = "", nascosta: bool = False) -> dict:
        """Collega il nodo a una rete Wi-Fi. La password non finisce in nessun registro (API 1.5)."""
        return self.request("POST", "/configura/wifi", {"azione": "collega", "ssid": ssid,
                                                        "password": password,
                                                        "nascosta": bool(nascosta)}, timeout=20)

    def disk_state(self) -> dict:
        """Il disco delle registrazioni: quello in uso, quelli collegati, i limiti, lo stato (API 1.5)."""
        return self.request("GET", "/configura/disco")

    def find_disks(self) -> dict:
        """Avvia la ricerca dei dischi USB collegati al nodo: pochi secondi (API 1.5)."""
        return self.request("POST", "/configura/disco", {"azione": "cerca"}, timeout=20)

    def use_disk(self, dispositivo: str) -> dict:
        """Registra su un disco già formattato bene: lo monta e lo mette in fstab (API 1.5)."""
        return self.request("POST", "/configura/disco", {"azione": "usa",
                                                         "dispositivo": dispositivo}, timeout=20)

    def prepare_disk(self, dispositivo: str, conferma: str) -> dict:
        """**Formatta** l'intero disco e lo usa per le registrazioni (API 1.5).

        ``conferma`` deve ripetere il nome del dispositivo: il nodo rifiuta la richiesta senza, e
        accetta solo un disco che compare nella sua ultima ricerca. È l'unica cosa che Mirto chiede
        a un nodo e che cancella dati, quindi la conferma la deve scrivere una persona.
        """
        return self.request("POST", "/configura/disco", {"azione": "prepara", "dispositivo": dispositivo,
                                                         "conferma": conferma}, timeout=20)

    def set_disk_limits(self, attiva: bool, massimo_gb: int, minimo_libero_percento: int) -> dict:
        """Registrazione accesa/spenta e i due limiti dell'anello (API 1.5)."""
        return self.request("POST", "/configura/disco",
                            {"azione": "limiti", "attiva": bool(attiva), "massimo_gb": int(massimo_gb),
                             "minimo_libero_percento": int(minimo_libero_percento)}, timeout=20)

    def set_recording_audio(self, attivo: bool) -> dict:
        """Il microfono **dentro** le registrazioni di questo nodo (API 1.13).

        Il nodo dice no prima se non può farla — microfono spento, o ffmpeg che manca — invece di
        accettare e non farla: chi l'accende lo fa credendo di aver registrato l'audio di quello che
        è successo, e scoprire il contrario dopo è peggio che non poterla accendere.
        """
        return self.request("POST", "/configura/disco", {"azione": "audio", "attivo": bool(attivo)},
                            timeout=20)

    def audio_state(self) -> dict:
        """Dove suona la sirena, le uscite possibili, l'ultima ricerca di casse e il suo esito (API 1.5)."""
        return self.request("GET", "/configura/audio")

    def set_audio(self, uscita: str, scheda: str = "", bluetooth_mac: str = "") -> dict:
        """Sceglie dove suona la sirena (API 1.5)."""
        return self.request("POST", "/configura/audio", {"azione": "imposta", "uscita": uscita,
                                                         "scheda": scheda,
                                                         "bluetooth_mac": bluetooth_mac}, timeout=20)

    def find_speakers(self) -> dict:
        """Avvia la ricerca di casse Bluetooth sul nodo: dura una quindicina di secondi (API 1.5)."""
        return self.request("POST", "/configura/audio", {"azione": "cerca"}, timeout=20)

    def pair_speaker(self, mac: str) -> dict:
        """Avvia l'abbinamento di una cassa Bluetooth (API 1.5)."""
        return self.request("POST", "/configura/audio", {"azione": "abbina", "mac": mac}, timeout=20)

    def test_sound(self) -> dict:
        """Din-don di prova dall'uscita configurata adesso (API 1.5)."""
        return self.request("POST", "/configura/audio", {"azione": "prova"}, timeout=30)

    def set_microphone(self, attivo: bool, scheda: str = "") -> dict:
        """Accende o spegne il microfono del nodo e dice quale usare (API 1.6).

        Il nodo riavvia il proprio servizio, come per la camera: l'ascolto parte all'avvio.
        """
        return self.request("POST", "/configura/audio",
                            {"azione": "microfono", "attivo": bool(attivo), "scheda": scheda}, timeout=20)

    def devices(self) -> dict:
        """I dispositivi audio del nodo (API 1.7): jack, casse USB, casse Bluetooth ricordate.

        Sostituisce la domanda «dove suona la sirena» con «cosa c'è attaccato»: dove suona una cosa è
        una scelta dell'azione, non una proprietà del nodo.
        """
        return self.request("GET", "/configura/dispositivi", timeout=20)

    def set_jack(self, attivo: bool) -> dict:
        """Rende l'uscita jack utilizzabile o no, ripieghi compresi (API 1.7)."""
        return self.request("POST", "/configura/audio",
                            {"azione": "jack", "attivo": bool(attivo)}, timeout=20)

    def forget_speaker(self, mac: str) -> dict:
        """Toglie l'abbinamento di una cassa Bluetooth (API 1.7): lo fa root, risponde in differita."""
        return self.request("POST", "/configura/audio",
                            {"azione": "dimentica", "mac": mac}, timeout=20)

    def knobs(self) -> dict:
        """Le manopole vere di ogni scheda audio del nodo (API 1.6): volume, muto, guadagno, automatismi."""
        return self.request("GET", "/configura/manopole", timeout=20)

    def set_knob(self, scheda: str, manopola: str, valore: str) -> dict:
        """Gira una manopola. ``valore``: ``"70%"`` per un volume, ``"on"``/``"off"`` (API 1.6).

        Si applica subito e il nodo la ricorda: non serve root e non si riavvia niente.
        """
        return self.request("POST", "/configura/manopole",
                            {"scheda": scheda, "manopola": manopola, "valore": valore}, timeout=20)

    def camera_state(self) -> dict:
        """Com'è la camera del nodo e cosa si può scegliere (API 1.5): attiva, noir, risoluzione, sensore."""
        return self.request("GET", "/configura/camera")

    def set_camera(self, attiva: bool, noir: bool = False, larghezza: int = 1280,
                   altezza: int = 720, fps: int = 15) -> dict:
        """Accende/spegne la camera del nodo e ne sceglie modello e risoluzione (API 1.5)."""
        return self.request("POST", "/configura/camera", {"attiva": bool(attiva), "noir": bool(noir),
                                                          "larghezza": larghezza, "altezza": altezza,
                                                          "fps": fps}, timeout=20)

    def set_name(self, name: str) -> dict:
        """Nome del nodo e nome in rete (API 1.5): {"nome": "Garage", "host": "mirto-garage", …}.

        Risponde col nome in rete nuovo perché l'indirizzo salvato qui va aggiornato: dopo il cambio,
        il nodo si fa trovare come ``mirto-garage.local`` e non risponde più al nome di prima.
        """
        return self.request("POST", "/configura/nome", {"nome": name}, timeout=20)

    def pairings(self) -> dict:
        """Chi è abbinato al nodo e com'è la finestra di abbinamento (API 1.5)."""
        return self.request("GET", "/configura/abbinamenti")

    def open_pairing(self) -> dict:
        """Riapre la finestra perché un altro computer possa abbinarsi: torna il PIN (API 1.5)."""
        return self.request("POST", "/configura/abbinamenti", {"azione": "apri"}, timeout=20)

    def close_pairing(self) -> dict:
        """Richiude la finestra: finito di abbinare, non resta aperta per nessuno (API 1.5)."""
        return self.request("POST", "/configura/abbinamenti", {"azione": "chiudi"}, timeout=20)

    def revoke_pairing(self, nome: str) -> dict:
        """Stacca un Mirto abbinato. L'ultimo il nodo lo rifiuta: si fa dal nodo (API 1.5)."""
        return self.request("POST", "/configura/abbinamenti", {"azione": "revoca", "nome": nome},
                            timeout=20)

    def play_audio(self, audio: bytes, mime: str) -> dict:
        return self.request("POST", "/audio/riproduci", raw=audio, content_type=mime, timeout=30)

    def stream_audio(self, stop=None, chunk: int = 3200):
        """Flusso del microfono del nodo (PCM 16 bit mono 16 kHz) a pezzi di ``chunk`` byte, finché il nodo
        lo manda o ``stop`` non è impostato. Stessa firma e stesso certificato fissato delle altre richieste."""
        full_path = self.base_path + "/audio/flusso"
        headers = sign(self.key, "GET", full_path, b"")
        conn = http.client.HTTPSConnection(self.host, self.port, timeout=15, context=self._ctx)
        try:
            conn.connect()
            der = conn.sock.getpeercert(binary_form=True)
            if not der or fingerprint(der) != self.pinned_fp:
                raise NodeError(_('Nodo {name}: certificato diverso da quello abbinato! Connessione interrotta.', name=self.node.name))
            conn.request("GET", full_path, headers=headers)
            resp = conn.getresponse()
            if resp.status >= 400:
                try:
                    msg = json.loads(resp.read()).get("errore", "")
                except Exception:  # noqa: BLE001
                    msg = ""
                raise NodeError(f"Nodo {self.node.name}: {resp.status} {msg}")
            while not (stop is not None and stop.is_set()):
                data = resp.read(chunk)
                if not data:
                    break
                yield data
        finally:
            conn.close()

    def recordings(self, t0: float | None = None, t1: float | None = None) -> dict:
        """Spezzoni registrati sul nodo (API 1.4): {"spezzoni": [{nome, inizio, fine, byte}], "stato": {...}}."""
        q = "&".join(f"{k}={v:.0f}" for k, v in (("da", t0), ("a", t1)) if v is not None)
        return self.request("GET", "/registrazioni" + (f"?{q}" if q else ""), timeout=20)

    def download_recording(self, name: str, dest, stop=None) -> int:
        """Scarica lo spezzone ``name`` dal nodo in ``dest`` (prima in un file temporaneo). Restituisce i byte."""
        import re  # noqa: PLC0415
        from pathlib import Path  # noqa: PLC0415

        if not re.fullmatch(r"\d{8}-\d{6}\.mp4", name or ""):
            raise NodeError(_("Nome di spezzone non valido: {name}", name=name))
        import os  # noqa: PLC0415
        import tempfile  # noqa: PLC0415

        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix=dest.stem + ".", suffix=".part")
        os.close(fd)
        tmp = Path(tmp_name)  # unico per ogni download: due download dello stesso spezzone non si mescolano
        full_path = self.base_path + f"/registrazioni/file?nome={name}"
        headers = sign(self.key, "GET", full_path, b"")
        conn = http.client.HTTPSConnection(self.host, self.port, timeout=30, context=self._ctx)
        total = 0
        try:
            conn.connect()
            der = conn.sock.getpeercert(binary_form=True)
            if not der or fingerprint(der) != self.pinned_fp:
                raise NodeError(_('Nodo {name}: certificato diverso da quello abbinato! Connessione interrotta.', name=self.node.name))
            conn.request("GET", full_path, headers=headers)
            resp = conn.getresponse()
            if resp.status >= 400:
                try:
                    msg = json.loads(resp.read()).get("errore", "")
                except Exception:  # noqa: BLE001
                    msg = ""
                raise NodeError(f"Nodo {self.node.name}: {resp.status} {msg}")
            with open(tmp, "wb") as f:
                while not (stop is not None and stop.is_set()):
                    data = resp.read(256 * 1024)
                    if not data:
                        break
                    f.write(data)
                    total += len(data)
            expected = resp.getheader("Content-Length")
            if expected is not None and int(expected) != total:
                raise NodeError(_("Download dello spezzone {name} interrotto", name=name))
            tmp.replace(dest)
            return total
        finally:
            conn.close()
            tmp.unlink(missing_ok=True)

    def record_audio(self, seconds: int) -> tuple[bytes, str]:
        res = self.request("POST", "/audio/registra", {"durata": seconds}, timeout=seconds + 20)
        if isinstance(res, dict) and "audio_base64" in res:
            return base64.b64decode(res["audio_base64"]), res.get("mime", "audio/wav")
        return res


def pair_node(config, secrets: SecretStore, node_id: str, name: str, url: str, pem: str, key_hex: str) -> None:
    """Registra un nodo dopo che l'utente ha confermato l'impronta del certificato."""
    save_pinned_certificate(node_id, pem)
    secrets.set(node_key_name(node_id), key_hex)
    nodes = [n for n in (config.get("actions.nodes") or []) if str(n.get("id")) != node_id]
    nodes.append({"id": node_id, "name": name, "url": url.rstrip("/")})
    config.set("actions.nodes", nodes)
    config.save()


def url_col_host(url: str, host: str) -> str:
    """Lo stesso indirizzo con il nome in rete nuovo, "" se non cambia niente.

    Cambiare nome a un nodo gli cambia il nome in rete (``mirto-garage.local``): l'indirizzo salvato
    qui va rifatto, altrimenti Mirto continua a cercarlo col nome di prima e lo perde.
    """
    if not host:
        return ""
    parti = urllib.parse.urlsplit(url)
    if parti.hostname in (f"{host}.local", host):
        return ""
    porta = f":{parti.port}" if parti.port else ""
    return f"{parti.scheme}://{host}.local{porta}"


def segui_il_nome(config, node_id: str, vecchio: str, nuovo: str) -> dict:
    """Camere e azioni che portavano il nome vecchio del nodo **solo perché nessuno gliene ha dato uno**.

    La camera di un nodo nasce col nome del nodo («Studio», o «Studio – cam» se ne ha più d'una), e le
    azioni proposte nascono «Sirena … – Studio». Rinominando il nodo in «Ingresso», il 21/10, restavano
    tutte col nome vecchio: in *Tutte le cam* una camera «Studio» su un nodo che si chiama «Ingresso».

    Regola: si cambia solo quello che è ancora il nome di fabbrica. Un nome scelto da una persona non si
    tocca — è suo, e il nodo non ha voce in capitolo. L'identificativo della camera non cambia mai: è la
    chiave delle credenziali nel portachiavi e degli eventi già registrati.
    """
    fatto = {"camere": [], "azioni": []}
    if not vecchio or vecchio == nuovo:
        return fatto
    camere = list(config.get("cameras.network") or [])
    for cam in camere:
        if not isinstance(cam, dict) or str(cam.get("node") or "") != str(node_id):
            continue
        attuale = str(cam.get("name") or "")
        if attuale == vecchio:
            cam["name"] = nuovo
        elif attuale.startswith(f"{vecchio} – "):
            cam["name"] = nuovo + attuale[len(vecchio):]
        else:
            continue            # nome scelto da una persona: non si tocca
        fatto["camere"].append(cam["name"])
    azioni = list(config.get("actions.items") or [])
    for a in azioni:
        if not isinstance(a, dict) or str(a.get("node") or "") != str(node_id):
            continue
        attuale = str(a.get("name") or "")
        if attuale.endswith(f" – {vecchio}"):
            a["name"] = attuale[: len(attuale) - len(vecchio)] + nuovo
            fatto["azioni"].append(a["name"])
    if fatto["camere"]:
        config.set("cameras.network", camere)
    if fatto["azioni"]:
        config.set("actions.items", azioni)
    return fatto


def rinomina_nodo(config, node_id: str, nome: str, client) -> dict:
    """Cambia il nome di un nodo **già abbinato**: sul dispositivo, in Mirto, e l'indirizzo salvato.

    Il nome lo chiede il wizard al primo abbinamento, ma chi scrive «Balocne» al posto di «Balcone» —
    successo — fino a oggi aveva come unico rimedio il terminale, più la correzione a mano
    dell'indirizzo. Qui si fa nell'ordine giusto: prima il dispositivo (se non accetta, non si è
    cambiato niente), poi quello che sta scritto qui.

    Restituisce ``{"nome", "host", "url"}``: ``url`` è vuoto se l'indirizzo non è cambiato.
    """
    host = str(client.set_name(nome).get("host") or "")
    nodes = list(config.get("actions.nodes") or [])
    nuovo_url, vecchio_nome = "", ""
    for nd in nodes:
        if str(nd.get("id")) != node_id:
            continue
        vecchio_nome = str(nd.get("name") or "")
        nd["name"] = nome
        nuovo_url = url_col_host(str(nd.get("url") or ""), host)
        if nuovo_url:
            nd["url"] = nuovo_url
    config.set("actions.nodes", nodes)
    seguito = segui_il_nome(config, node_id, vecchio_nome, nome)
    config.save()
    log.info("Nodo «%s»: nome nuovo%s%s", nome, f", indirizzo salvato {nuovo_url}" if nuovo_url else "",
             f", {len(seguito['camere'])} camere e {len(seguito['azioni'])} azioni rinominate"
             if seguito["camere"] or seguito["azioni"] else "")
    return {"nome": nome, "host": host, "url": nuovo_url, **seguito}


def set_node_url(config, node_id: str, url: str) -> bool:
    """Cambia l'indirizzo salvato di un nodo (serve quando il nodo cambia nome in rete)."""
    nodes = list(config.get("actions.nodes") or [])
    for nd in nodes:
        if str(nd.get("id")) == node_id and str(nd.get("url", "")).rstrip("/") != url.rstrip("/"):
            nd["url"] = url.rstrip("/")
            config.set("actions.nodes", nodes)
            config.save()
            return True
    return False


def unpair_node(config, secrets: SecretStore, node_id: str) -> None:
    (certs_dir() / f"{node_id}.pem").unlink(missing_ok=True)
    secrets.delete(node_key_name(node_id))
    config.set("actions.nodes", [n for n in (config.get("actions.nodes") or []) if str(n.get("id")) != node_id])
    config.save()
