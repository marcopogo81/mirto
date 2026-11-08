# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Scoperta delle camere di rete via mDNS/Bonjour (servizio ``_rtsp._tcp``).

Le camere che si annunciano in rete locale (per esempio il Raspberry con
MediaMTX e il file Avahi ``mirto-cam.service``) compaiono in "Tutte le cam"
nella sezione "Trovate in rete", pronte da aggiungere con un clic.

Record TXT riconosciuti (tutti facoltativi):

* ``path``      percorso dello stream (es. ``/cam``); di serie ``/``
* ``mirto=1`` camera preparata per Mirto (nome proposto più leggibile)

Una camera aggiunta da qui viene salvata con il nome del servizio (campo
``mdns``): a ogni riconnessione Mirto rilegge l'indirizzo attuale, quindi la
camera continua a funzionare anche se il router le assegna un IP diverso.
Richiede la libreria ``zeroconf``; senza, la scoperta è semplicemente spenta.
"""

from __future__ import annotations

import dataclasses
import logging
import re
import socket
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from ..netutil import redact_url

log = logging.getLogger(__name__)

SERVICE = "_rtsp._tcp.local."
# Come comincia il nome in rete di un nodo («mirto-ingresso»). Sta in una costante perché il pezzo
# da togliere si misura da qui: il 7/11, rinominando il programma, questa riga era scritta a mano
# come `base[8:]` — otto, la lunghezza di «myhouse-» — e dopo il cambio di nome la camera
# «mirto-ingresso» si chiamava «Gresso».
PREFISSO_NODO = "mirto-"
DEFAULT_RTSP_PORT = 554
PROBE_TIMEOUT = 1.0   # secondi per controllare se un indirizzo risponde
REFRESH_SECONDS = 10  # ogni quanto ricontrollare gli indirizzi delle camere trovate
FAST_REFRESH_SECONDS = 2  # mentre l'utente guarda "Tutte le cam"


@dataclass(frozen=True)
class FoundCamera:
    service: str                  # nome completo, es. "Mirto Cam mirto-ingresso._rtsp._tcp.local."
    name: str                     # nome dell'annuncio, es. "Mirto Cam mirto-ingresso"
    host: str                     # nome in rete, es. "mirto-ingresso.local"
    addresses: tuple[str, ...]    # indirizzi IPv4
    port: int
    path: str = "/"
    mirto: bool = False
    alive: bool = True            # il primo indirizzo ha risposto all'ultimo controllo
    live: tuple[str, ...] = ()    # tutti gli indirizzi che hanno risposto (es. cavo e Wi-Fi insieme)

    def url(self, address: str | None = None) -> str:
        addr = address or (self.addresses[0] if self.addresses else self.host)
        return f"rtsp://{addr}:{self.port}{self.path}"

    @property
    def suggested_name(self) -> str:
        """Nome proposto per la camera: "mirto-ingresso" → "Ingresso"."""
        base = self.host.split(".")[0] if self.host else self.name
        if self.mirto and base.lower().startswith(PREFISSO_NODO) and len(base) > len(PREFISSO_NODO):
            base = base[len(PREFISSO_NODO):]
        elif not self.mirto:
            return self.name
        return base.replace("-", " ").replace("_", " ").strip().capitalize() or self.name


def base_service(service: str) -> str:
    """Nome del servizio senza il suffisso " #2", " (2)"… che Avahi/Bonjour aggiungono quando
    credono di aver trovato un doppione (capita con lo stesso dispositivo su cavo e Wi-Fi)."""
    name = service[: -len("." + SERVICE)] if service.endswith("." + SERVICE) else service
    name = re.sub(r"\s*(#\d+|\(\d+\))$", "", name.strip())
    return name.lower()


def _norm_path(path: str) -> str:
    path = (path or "").strip()
    return path if path.startswith("/") else "/" + path


def parse_service_info(service: str, info) -> FoundCamera | None:
    """Converte un ``ServiceInfo`` di zeroconf (o un oggetto simile) in ``FoundCamera``."""
    if info is None or not getattr(info, "port", None):
        return None
    try:
        addrs = tuple(a for a in info.parsed_addresses() if ":" not in a)  # solo IPv4
    except Exception:
        addrs = ()
    props = {}
    for k, v in (getattr(info, "properties", None) or {}).items():
        key = k.decode(errors="replace") if isinstance(k, bytes) else str(k)
        val = v.decode(errors="replace") if isinstance(v, bytes) else ("" if v is None else str(v))
        props[key.lower()] = val
    host = (getattr(info, "server", "") or "").rstrip(".")
    name = service[: -len("." + SERVICE)] if service.endswith("." + SERVICE) else service
    if not addrs and not host:
        return None
    return FoundCamera(service=service, name=name, host=host, addresses=addrs, port=int(info.port),
                       path=_norm_path(props.get("path", "/")),
                       mirto=props.get("mirto", "") in ("1", "true", "yes"))


def matches(net: dict, cam: FoundCamera) -> bool:
    """La camera di rete configurata ``net`` è quella annunciata ``cam``?"""
    if net.get("mdns") and base_service(net["mdns"]) == base_service(cam.service):
        return True
    try:
        u = urlsplit(net.get("url", ""))
    except ValueError:
        return False
    if (u.scheme or "").lower() not in ("rtsp", "rtsps"):
        return False
    host = (u.hostname or "").rstrip(".").lower()
    port = u.port or DEFAULT_RTSP_PORT
    hosts = {a.lower() for a in cam.addresses} | ({cam.host.lower()} if cam.host else set())
    return host in hosts and port == cam.port and _norm_path(u.path) == cam.path


def risolvi(host: str, port: int) -> list[str]:
    """Indirizzi IPv4 del nome in rete, o lista vuota.

    Una funzione sola perché è il punto lento: su macOS un nome ``.local`` che nessuno
    rivendica tiene ferma la chiamata per ~5 secondi (attesa mDNS), e ``getaddrinfo`` non
    accetta un timeout. I test la sostituiscono per non toccare la rete.
    """
    try:
        infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        return []
    return list(dict.fromkeys(i[4][0] for i in infos))


def reachable(addr: str, port: int, timeout: float | None = None) -> bool:
    try:
        with socket.create_connection((addr, port), timeout=PROBE_TIMEOUT if timeout is None else timeout):
            return True
    except OSError:
        return False


def live_addresses(addresses, port: int, host: str = "", known_alive=None) -> tuple[str, ...]:
    """Tutti gli indirizzi che rispondono sulla porta, compresi quelli del nome in rete.

    ``known_alive(addr, port)``: se dice True l'indirizzo è vivo senza bisogno di provarlo
    (Mirto sta già ricevendo il video da lì). Così non si apre una connessione di prova
    ogni pochi secondi, che riempirebbe il registro della camera di righe "opened/closed".
    """
    candidates = list(dict.fromkeys(addresses))
    if host:
        candidates += [a for a in risolvi(host, port) if a not in candidates]
    def alive(a: str) -> bool:
        if known_alive is not None:
            try:
                if known_alive(a, port):
                    return True
            except Exception:
                pass
        return reachable(a, port)
    return tuple(a for a in candidates if alive(a))


def _probe(cam: FoundCamera, known_alive=None) -> FoundCamera:
    """Mette per primo un indirizzo che risponde e annota tutti quelli che rispondono."""
    live = live_addresses(cam.addresses, cam.port, cam.host, known_alive)
    if live:
        best = cam.addresses[0] if cam.addresses and cam.addresses[0] in live else live[0]
        addrs = (best,) + tuple(a for a in dict.fromkeys(cam.addresses + live) if a != best)
    else:
        addrs = cam.addresses
    new = dataclasses.replace(cam, addresses=addrs, alive=bool(live), live=live)
    return cam if new == cam else new


def pick_address(addresses, port: int, host: str = "") -> str | None:
    """Primo indirizzo che risponde davvero sulla porta.

    Serve quando la camera ha più indirizzi o ne ha appena cambiato uno (es. cavo
    staccato e passaggio al Wi-Fi): gli annunci vecchi possono restare in memoria
    per qualche minuto. Se nessuno risponde si prova il nome in rete (``host``),
    altrimenti si restituisce il primo indirizzo come ultima risorsa.
    """
    addresses = list(dict.fromkeys(addresses))
    for addr in addresses:
        if reachable(addr, port):
            return addr
    if host:
        for addr in risolvi(host, port):
            if addr not in addresses and reachable(addr, port):
                return addr
    return addresses[0] if addresses else None


class NetworkCameraBrowser:
    """Ascolta di continuo gli annunci ``_rtsp._tcp`` e tiene l'elenco aggiornato."""

    def __init__(self):
        self._found: dict[str, FoundCamera] = {}
        self._lock = threading.Lock()
        self._zc = None
        self._browser = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self.interval = REFRESH_SECONDS
        self.last_refresh = 0.0       # ora (time.time) dell'ultimo controllo concluso
        self.refreshing = False
        # (indirizzo, porta) -> True se Mirto sta già ricevendo video da lì (vedi live_addresses)
        self.known_alive = None

    @property
    def running(self) -> bool:
        return self._zc is not None

    def start(self) -> bool:
        if self._zc is not None:
            return True
        try:
            from zeroconf import ServiceBrowser, Zeroconf  # noqa: PLC0415
        except ImportError:
            log.warning("zeroconf non installato: ricerca delle camere di rete non disponibile")
            return False
        try:
            self._zc = Zeroconf()
            self._browser = ServiceBrowser(self._zc, SERVICE, _Listener(self))
            self._stop.clear()
            self._thread = threading.Thread(target=self._refresh_loop, name="mdns-cameras", daemon=True)
            self._thread.start()
            log.info("Ricerca camere di rete (mDNS %s) avviata", SERVICE)
            return True
        except Exception as exc:
            log.warning("Ricerca camere di rete non disponibile: %s", exc)
            self.stop()
            return False

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3)
        zc, self._zc = self._zc, None
        browser, self._browser = self._browser, None
        try:
            if browser is not None:
                browser.cancel()
        except Exception:
            pass
        try:
            if zc is not None:
                zc.close()
        except Exception:
            pass

    # --- aggiornamento periodico ----------------------------------------
    def set_interval(self, seconds: float) -> None:
        """Cambia la frequenza dei controlli (più alta mentre si guarda "Tutte le cam")."""
        if seconds != self.interval:
            self.interval = seconds
            self._wake.set()

    def refresh_now(self) -> None:
        """Chiede un controllo subito, senza aspettare il prossimo giro."""
        self._wake.set()

    def _refresh_loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self.interval)
            self._wake.clear()
            if self._stop.is_set():
                break
            try:
                self.refresh()
            except Exception:
                log.exception("Controllo delle camere di rete fallito")

    def refresh(self) -> None:
        """Rilegge gli indirizzi di ogni camera trovata e mette per primo quello che risponde.

        Il cambio di indirizzo (es. cavo staccato → Wi-Fi) non genera un nuovo annuncio
        e il vecchio indirizzo resta in memoria per qualche minuto: senza questo controllo
        l'elenco mostrerebbe ancora l'indirizzo vecchio.
        """
        self.refreshing = True
        try:
            self._refresh_all()
        finally:
            self.refreshing = False
            self.last_refresh = time.time()

    def _refresh_all(self) -> None:
        for service in [c.service for c in self.found()]:
            if self._stop.is_set():
                return
            info = None
            if self._zc is not None:
                try:
                    info = self._zc.get_service_info(SERVICE, service, timeout=2000)
                except Exception as exc:
                    log.debug("Aggiornamento mDNS di %s fallito: %s", service, exc)
            if info is not None:
                self._store(service, info)
            else:
                with self._lock:
                    cam = self._found.get(service)
                if cam is not None:
                    self._reorder(cam)

    def _reorder(self, cam: FoundCamera) -> FoundCamera:
        """Ricontrolla gli indirizzi di una camera già in elenco (prove di rete fuori dal lucchetto)."""
        new = _probe(cam, self.known_alive)
        if new != cam:
            with self._lock:
                known = self._found.get(cam.service)
                if known is not None and known.addresses == cam.addresses:
                    self._found[cam.service] = new
            if new.addresses[:1] != cam.addresses[:1] and new.alive:
                log.info("Camera di rete %s: indirizzo attuale %s", cam.name, new.addresses[0])
        return new

    # --- dati ---------------------------------------------------------
    def _store(self, service: str, info) -> None:
        cam = parse_service_info(service, info)
        if cam is None:
            return
        cam = _probe(cam, self.known_alive)  # per primo l'indirizzo che risponde, e se risponde
        with self._lock:
            old = self._found.get(service)
            self._found[service] = cam
        if old is None:
            log.info("Camera di rete trovata: %s (%s)", cam.name, redact_url(cam.url()))
        elif old.addresses != cam.addresses:
            log.info("Camera di rete %s: nuovo indirizzo %s", cam.name, ", ".join(cam.addresses))

    def _forget(self, service: str) -> None:
        with self._lock:
            if self._found.pop(service, None):
                log.info("Camera di rete non più annunciata: %s", service)

    def found(self) -> list[FoundCamera]:
        """Camere trovate; a parità di dispositivo (annunci "… #2") prima quella che risponde."""
        with self._lock:
            return sorted(self._found.values(),
                          key=lambda c: (not c.mirto, base_service(c.service), not c.alive, c.name.lower()))

    def get(self, service: str, refresh: bool = False) -> FoundCamera | None:
        """Camera annunciata come ``service``; con ``refresh`` rilegge gli indirizzi attuali."""
        with self._lock:
            cam = self._found.get(service)
            if cam is None or not cam.alive:  # rinominata dalla rete ("… #2"): stesso dispositivo
                base = base_service(service)
                twins = [c for c in self._found.values() if base_service(c.service) == base]
                best = next((c for c in twins if c.alive), None) or cam or (twins[0] if twins else None)
                if best is not None:
                    cam, service = best, best.service
        if (cam is None or refresh) and self._zc is not None:
            try:
                info = self._zc.get_service_info(SERVICE, service, timeout=2000)
                if info:
                    self._store(service, info)
                    with self._lock:
                        cam = self._found.get(service)
            except Exception as exc:
                log.debug("Richiesta mDNS per %s fallita: %s", service, exc)
        return cam

    def resolve_url(self, service: str, fallback: str) -> str:
        """URL attuale della camera annunciata come ``service``; ``fallback`` se non si trova."""
        cam = self.get(service, refresh=True)
        if cam is None:
            return fallback
        addr = pick_address(cam.addresses, cam.port, cam.host)
        if addr is None:
            return fallback
        url = cam.url(addr)
        # conserva eventuali credenziali scritte a mano nell'indirizzo salvato
        try:
            old = urlsplit(fallback)
            if old.username:
                cred = old.username + (f":{old.password}" if old.password else "")
                url = url.replace("rtsp://", f"rtsp://{cred}@", 1)
        except ValueError:
            pass
        return url


class _Listener:
    def __init__(self, owner: NetworkCameraBrowser):
        self.owner = owner

    def add_service(self, zc, type_, name):
        try:
            self.owner._store(name, zc.get_service_info(type_, name, timeout=3000))
        except Exception as exc:
            log.debug("Annuncio mDNS %s non leggibile: %s", name, exc)

    def update_service(self, zc, type_, name):
        self.add_service(zc, type_, name)

    def remove_service(self, zc, type_, name):
        self.owner._forget(name)
