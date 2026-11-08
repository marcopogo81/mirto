# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Abbinamento dei nodi senza chiave copiata ("Nuovo nodo trovato → Abbina").

Il nodo è abbinabile per 5 minuti dopo ``mirto-nodo abbina`` (o dopo il primo avvio della sua immagine).
Mirto ascolta gli annunci ``_mirto-node._tcp``; per ogni nodo non ancora abbinato chiede ``GET /abbina``
e, se la finestra è aperta, lo propone. Premendo **Abbina** l'utente vede l'impronta del certificato e
scrive il PIN di 6 cifre mostrato dal nodo; Mirto chiede la chiave con ``POST /abbina`` sulla connessione
TLS **dopo** aver controllato che il certificato sia proprio quello mostrato, poi salva nodo, certificato e
chiave come nell'abbinamento manuale. La chiave è diversa per ogni abbinamento: abbinare un secondo Mac non
tocca il primo, e sul nodo ``mirto-nodo abbina --revoca-tutti`` li stacca tutti.
"""

from __future__ import annotations

import http.client
import json
import logging
import re
import socket
import ssl
import threading
import time
import urllib.parse

from .nodes import (NodeClient, NodeConfig, NodeError, fingerprint, load_pinned_certificate, pair_node,
                    pem_to_der, set_node_url, url_col_host)
from ..i18n import _

log = logging.getLogger(__name__)

SERVICE = "_mirto-node._tcp.local."
CHECK_SECONDS = 10


def norm_fp(fp: str) -> str:
    return re.sub(r"[^0-9A-F]", "", (fp or "").upper())


def unsigned_request(url: str, method: str, path: str, payload=None, expect_fp: str = "",
                     timeout: float = 6) -> tuple[int, dict, str, str]:
    """Richiesta senza firma (solo ``/abbina``). Restituisce (stato, json, certificato PEM, impronta).

    Con ``expect_fp`` la connessione si interrompe PRIMA di inviare qualsiasi dato se il certificato del nodo
    non è quello atteso.
    """
    u = urllib.parse.urlparse(url)
    if u.scheme != "https":
        raise NodeError(_('Il nodo deve usare https://'))
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # certificato self-signed: si controlla l'impronta
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    conn = http.client.HTTPSConnection(u.hostname, u.port or 443, timeout=timeout, context=ctx)
    try:
        conn.connect()
        der = conn.sock.getpeercert(binary_form=True)
        fp = fingerprint(der) if der else ""
        if expect_fp and norm_fp(fp) != norm_fp(expect_fp):
            raise NodeError(_('Il certificato del nodo è cambiato rispetto a quello mostrato: abbinamento interrotto.'))
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {"Content-Type": "application/json"} if body else {}
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
    finally:
        conn.close()
    try:
        obj = json.loads(data or b"{}")
    except ValueError:
        obj = {}
    return resp.status, obj if isinstance(obj, dict) else {}, ssl.DER_cert_to_PEM_cert(der) if der else "", fp


def pairing_state(url: str, timeout: float = 4) -> dict:
    """{"aperto", "scade_tra_s", "senza_scadenza", "nome", "impronta"}.

    ``aperto`` è False per i nodi vecchi o non raggiungibili. ``senza_scadenza`` è vero quando il nodo
    non è **mai** stato abbinato: il suo PIN è quello stampato e consegnato col dispositivo, e resta
    valido finché qualcuno non lo abbina (quindi ``scade_tra_s`` non è un conto alla rovescia vero).
    """
    try:
        status, obj, _pem, fp = unsigned_request(url, "GET", "/abbina", timeout=timeout)
    except (OSError, NodeError, ssl.SSLError, http.client.HTTPException) as exc:
        log.debug("Stato abbinamento di %s non leggibile: %s", url, exc)
        return {"aperto": False, "scade_tra_s": 0, "senza_scadenza": False, "nome": "", "impronta": ""}
    if status != 200:
        return {"aperto": False, "scade_tra_s": 0, "senza_scadenza": False,
                "nome": obj.get("nome", ""), "impronta": fp}
    return {"aperto": bool(obj.get("aperto")), "scade_tra_s": int(obj.get("scade_tra_s") or 0),
            "senza_scadenza": bool(obj.get("senza_scadenza")),
            "nome": str(obj.get("nome") or ""), "impronta": fp}


def claim(url: str, expect_fp: str, client: str = "Mirto", pin: str = "") -> tuple[str, str, str]:
    """Chiede la chiave al nodo, col PIN mostrato dal nodo. Restituisce (certificato PEM, chiave, nome)."""
    status, obj, pem, _fp = unsigned_request(url, "POST", "/abbina", {"client": client, "pin": pin},
                                             expect_fp=expect_fp)
    if status != 200:
        raise NodeError(obj.get("errore") or _('il nodo ha risposto {status}', status=status))
    key = str(obj.get("chiave", "")).lower()
    if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
        raise NodeError(_('il nodo non ha consegnato una chiave valida'))
    return pem, key, str(obj.get("nome") or "")


def unique_node_id(config, name: str) -> str:
    base = "".join(ch for ch in name.lower() if ch.isalnum()) or "nodo"
    used = {str(n.get("id")) for n in (config.get("actions.nodes") or [])}
    nid, n = base, 2
    while nid in used:
        nid, n = f"{base}{n}", n + 1
    return nid


def paired_fingerprints(config, load=load_pinned_certificate) -> set[str]:
    out = set()
    for nd in config.get("actions.nodes") or []:
        pem = load(str(nd.get("id")))
        if pem:
            try:
                out.add(norm_fp(fingerprint(pem_to_der(pem))))
            except ValueError:
                pass
    return out


NOME_DI_FABBRICA = re.compile(r"^(mirto-)?(node|nodo)(-[0-9a-f]{2,8})?$")   # il suffisso sono cifre esadecimali


def nome_di_fabbrica(nome: str) -> bool:
    """«Node 17a9» sì, «Garage» no: è il nome che un nodo nuovo si è dato da solo al primo avvio.

    Serve alla finestra di abbinamento, per non proporre come nome una sigla che non dice niente a
    nessuno: davanti a cinque nodi identici, «Node 17a9» non aiuta a sapere quale sia quale.
    """
    pulito = re.sub(r"[^a-z0-9]+", "-", str(nome or "").strip().lower()).strip("-")
    return bool(NOME_DI_FABBRICA.match(pulito))


def battezza_nodo(config, node_id: str, name: str, pem: str, key: str, found: dict) -> str:
    """Scrive il nome scelto **sul dispositivo** e aggiorna l'indirizzo salvato se cambia il nome in rete.

    Il nome non è solo un'etichetta di Mirto: chi mette cinque nodi in casa li configura uno dopo
    l'altro, e poi deve riconoscere quale camera è di quale nodo. Prima lo si faceva col terminale.

    Un nodo con la versione vecchia (prima di API 1.5) non sa fare questa richiesta: si tiene il nome
    che ha, e non è un motivo per rovinare un abbinamento riuscito. Restituisce il nome in rete nuovo,
    "" se non è cambiato niente.
    """
    client = NodeClient(NodeConfig(node_id, name, found["contact"]), pem, key)
    try:
        host = str(client.set_name(name).get("host") or "")
    except (NodeError, OSError) as exc:
        log.warning("Nodo «%s»: il nome non è stato scritto sul dispositivo (%s)", name, exc)
        return ""
    nuovo = url_col_host(found["url"], host)   # lo stesso calcolo della rinomina (nodes.py)
    if not nuovo:
        return ""
    set_node_url(config, node_id, nuovo)
    log.info("Nodo «%s»: nome in rete %s.local, indirizzo salvato aggiornato", name, host)
    return nuovo


def pair_found(config, secrets, found: dict, name: str, client: str = "Mirto", pin: str = "") -> str:
    """Abbina un nodo proposto (dopo che l'utente ha visto l'impronta e scritto il PIN). Restituisce l'id."""
    pem, key, node_name = claim(found["contact"], found["impronta"], client, pin)
    name = (name or node_name or found.get("nome") or "Nodo").strip()
    node_id = unique_node_id(config, name)
    pair_node(config, secrets, node_id, name, found["url"], pem, key)
    if found.get("senza_scadenza"):
        # «senza_scadenza» vuol dire che questo nodo non era mai stato abbinato: è il primo abbinamento,
        # quello del wizard, e il nome va dato anche al dispositivo. Al secondo Mac che si abbina a un
        # nodo già in servizio non si cambia il nome sotto i piedi.
        battezza_nodo(config, node_id, name, pem, key, found)
    return node_id


def client_name() -> str:
    host = socket.gethostname().split(".")[0]
    return f"Mirto su {host}"[:60]


class NodeBrowser:
    """Ascolta gli annunci ``_mirto-node._tcp``: {servizio: {nome, url, contact, fp}}."""

    def __init__(self):
        self._found: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._zc = None
        self._browser = None

    def start(self) -> bool:
        try:
            from zeroconf import ServiceBrowser, Zeroconf  # noqa: PLC0415
        except ImportError:
            log.warning("zeroconf non installato: i nodi nuovi non vengono proposti")
            return False
        outer = self

        class Listener:
            def add_service(self, zc, type_, name):
                try:
                    info = zc.get_service_info(type_, name, timeout=2000)
                except Exception:  # noqa: BLE001
                    info = None
                if info:
                    outer._store(name, info)

            def update_service(self, zc, type_, name):
                self.add_service(zc, type_, name)

            def remove_service(self, zc, type_, name):
                with outer._lock:
                    outer._found.pop(name, None)

        try:
            self._zc = Zeroconf()
            self._browser = ServiceBrowser(self._zc, SERVICE, Listener())
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("Ricerca dei nodi non disponibile: %s", exc)
            self.stop()
            return False

    def _store(self, service: str, info) -> None:
        addrs = [a for a in info.parsed_addresses() if ":" not in a]
        if not addrs:
            return
        props = {k.decode(): (v.decode() if v else "") for k, v in (info.properties or {}).items()}
        host = (info.server or "").rstrip(".")
        label = service.replace("." + SERVICE, "")
        with self._lock:
            self._found[service] = {
                "nome": label, "fp": props.get("fp", ""),
                "contact": f"https://{addrs[0]}:{info.port}",                     # per parlarci adesso
                "url": f"https://{host or addrs[0]}:{info.port}",                 # da salvare (segue l'IP)
            }

    def nodes(self) -> list[dict]:
        with self._lock:
            return [dict(v) for v in self._found.values()]

    def stop(self) -> None:
        browser, self._browser = self._browser, None
        zc, self._zc = self._zc, None
        for obj, meth in ((browser, "cancel"), (zc, "close")):
            try:
                if obj is not None:
                    getattr(obj, meth)()
            except Exception:  # noqa: BLE001
                pass


class PairingWatcher:
    """Tiene l'elenco dei nodi nuovi abbinabili adesso (``pairable``), controllando ogni 10 secondi."""

    def __init__(self, config, browser=None, state=pairing_state, paired=paired_fingerprints, clock=time.time):
        self.config = config
        self.browser = browser if browser is not None else NodeBrowser()
        self._state, self._paired, self.clock = state, paired, clock
        self.pairable: list[dict] = []
        self.ignored: set[str] = set()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        if hasattr(self.browser, "start") and not self.browser.start():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="nodi-abbinamento", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        t, self._thread = self._thread, None
        if t is not None and t is not threading.current_thread():
            t.join(timeout=3)
        if hasattr(self.browser, "stop"):
            self.browser.stop()

    def check_now(self) -> None:
        self._wake.set()

    def ignore(self, fp: str) -> None:
        self.ignored.add(norm_fp(fp))
        self.pairable = [p for p in self.pairable if norm_fp(p["impronta"]) not in self.ignored]

    def _loop(self) -> None:
        self._wake.wait(3)  # lascia il tempo agli annunci di arrivare
        while not self._stop.is_set():
            try:
                self.check()
            except Exception:  # noqa: BLE001
                log.exception("Controllo dei nodi nuovi fallito")
            self._wake.wait(CHECK_SECONDS)
            self._wake.clear()

    def check(self) -> list[dict]:
        paired = self._paired(self.config)
        out = []
        for nd in self.browser.nodes():
            fp = norm_fp(nd.get("fp", ""))
            if not fp:
                # S-06: un annuncio senza impronta non si propone. Chiunque sulla rete può annunciare un
                # «_mirto-node._tcp»; se non dichiara l'impronta del suo certificato, non c'è niente da
                # confrontare e l'utente si troverebbe a confermare un nodo che non è quello che ha in
                # mano. Da qui in poi gli si manderebbe anche la password del Wi-Fi di casa.
                log.warning("Nodo «%s»: annuncio senza impronta del certificato, ignorato", nd.get("nome", "?"))
                continue
            if fp in paired or fp in self.ignored:
                continue  # già abbinato: nessuna domanda al nodo
            st = self._state(nd["contact"])
            real = norm_fp(st.get("impronta", ""))
            if not st.get("aperto") or not real or real in paired or real in self.ignored:
                continue
            if real != fp:
                log.warning("Nodo %s: il certificato non corrisponde all'annuncio in rete, ignorato", nd["nome"])
                continue
            out.append({"nome": st.get("nome") or nd["nome"], "url": nd["url"], "contact": nd["contact"],
                        "impronta": st["impronta"], "senza_scadenza": bool(st.get("senza_scadenza")),
                        "scade": self.clock() + int(st.get("scade_tra_s") or 0)})
        self.pairable = out
        return out

    def current(self) -> list[dict]:
        """Nodi abbinabili con i secondi rimasti (quelli scaduti spariscono subito).

        Un nodo mai abbinato non scade: il suo PIN è stampato e viaggia col dispositivo, quindi resta
        nell'elenco anche dopo i cinque minuti, e il tempo rimasto non si mostra (``scade_tra_s`` 0).
        """
        now = self.clock()
        return [dict(p, scade_tra_s=0 if p.get("senza_scadenza") else int(p["scade"] - now))
                for p in self.pairable if p.get("senza_scadenza") or p["scade"] > now]
