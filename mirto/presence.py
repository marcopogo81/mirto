# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Presenza in casa dai telefoni collegati al Wi-Fi.

Come funziona (senza permessi di amministratore):

1. **ricerca**: un pacchetto UDP a ogni indirizzo della rete di casa (/24) fa sì che il
   computer chieda "chi ha questo indirizzo?" (ARP); la tabella ARP del sistema
   (``arp -a``) dice poi quale indirizzo MAC ha ogni indirizzo IP;
2. **verifica**: la tabella ARP ricorda i dispositivi anche per 20 minuti dopo che sono
   andati via, quindi per ogni telefono registrato si prova una connessione TCP
   (porta 62078, che "sveglia" gli iPhone in standby): qualsiasi risposta, anche un
   rifiuto, vuol dire che il telefono è lì;
3. **decisione**: una persona è "fuori" se il suo telefono non risponde da
   ``away_minutes`` minuti. Quando **escono tutti** Mirto attiva la protezione;
   quando **rientra qualcuno** la disattiva (entrambe le cose si possono spegnere).

Si agisce solo sui **cambi** (tutti fuori ↔ qualcuno in casa), non sullo stato: una
scelta fatta a mano o dalla programmazione oraria resta valida fino al cambio successivo.

iPhone: in Impostazioni → Wi-Fi → (i) della rete di casa, "Indirizzo Wi-Fi privato" deve
essere **Fisso** (non "A rotazione"), altrimenti l'indirizzo MAC cambia nel tempo.
"""

from __future__ import annotations

import errno
import ipaddress
import logging
import re
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable
from .i18n import _

log = logging.getLogger(__name__)

PROBE_PORTS = (62078, 80, 443, 7000)  # 62078: servizio di sincronizzazione degli iPhone
_MAC_RE = re.compile(r"([0-9a-fA-F]{1,2}(?:[:-][0-9a-fA-F]{1,2}){5})")
_IP_RE = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3})")


def normalize_mac(mac: str) -> str:
    """``A:B:C:1:2:3`` / ``aa-bb-…`` → ``0a:0b:0c:01:02:03`` (macOS toglie gli zeri iniziali)."""
    parts = re.split(r"[:-]", mac.strip())
    return ":".join(p.zfill(2).lower() for p in parts)


def is_private_mac(mac: str) -> bool:
    """Indirizzo "privato" (generato dal telefono): secondo bit del primo byte a 1."""
    try:
        return bool(int(normalize_mac(mac).split(":")[0], 16) & 0x02)
    except ValueError:
        return False


def parse_arp(text: str) -> dict[str, str]:
    """Uscita di ``arp -a`` (macOS, Windows, Linux) → {mac: ip}. Ignora voci incomplete e broadcast."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        ip_m, mac_m = _IP_RE.search(line), _MAC_RE.search(line)
        if not ip_m or not mac_m:
            continue
        mac = normalize_mac(mac_m.group(1))
        if mac in ("ff:ff:ff:ff:ff:ff", "00:00:00:00:00:00") or mac.startswith("01:00:5e"):
            continue
        out[mac] = ip_m.group(1)
    return out


def read_arp_table() -> dict[str, str]:
    from pathlib import Path  # noqa: PLC0415

    proc = Path("/proc/net/arp")  # Linux senza il comando arp
    if sys.platform.startswith("linux") and proc.exists():
        return parse_arp(proc.read_text())
    try:
        cmd = ["arp", "-a"] if sys.platform.startswith("win") else ["arp", "-an"]
        extra = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform.startswith("win") else {}
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False, **extra)  # noqa: S603
        return parse_arp(res.stdout)
    except Exception as exc:  # noqa: BLE001
        log.warning("Tabella ARP non leggibile: %s", exc)
        return {}


def local_network() -> ipaddress.IPv4Network | None:
    """Rete di casa (/24) dedotta dall'indirizzo del computer. Nessun pacchetto esce davvero."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # indirizzo di documentazione: serve solo a scegliere l'interfaccia
            ip = s.getsockname()[0]
        if ip.startswith("127."):
            return None
        return ipaddress.ip_network(f"{ip}/24", strict=False)
    except OSError:
        return None


def sweep(network: ipaddress.IPv4Network | None = None) -> None:
    """Un pacchetto UDP a ogni indirizzo: il sistema risolve gli indirizzi MAC (tabella ARP)."""
    net = network or local_network()
    if net is None:
        return
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setblocking(False)
        for host in net.hosts():
            try:
                s.sendto(b"", (str(host), 9))  # porta "discard"
            except OSError:
                pass
    time.sleep(1.5)  # tempo per le risposte ARP


def _codici(*nomi: str) -> set[int]:
    """Codici di errore per nome, saltando quelli che non esistono su questo sistema (Windows ha i suoi)."""
    return {getattr(errno, nome) for nome in nomi if hasattr(errno, nome)}


PRESENTE = _codici("ECONNREFUSED", "ECONNRESET", "WSAECONNREFUSED", "WSAECONNRESET")
ASSENTE = _codici("EHOSTDOWN", "EHOSTUNREACH", "ENETUNREACH", "WSAEHOSTDOWN", "WSAEHOSTUNREACH")


def is_alive(ip: str, timeout: float = 2.0, connect=socket.create_connection) -> bool:
    """Il dispositivo risponde? Anche un rifiuto di connessione vuol dire "è qui"."""
    for port in PROBE_PORTS:
        try:
            with connect((ip, port), timeout=timeout):
                return True
        except ConnectionRefusedError:
            return True
        except TimeoutError:
            return False  # un dispositivo presente risponde subito (anche solo rifiutando): assente
        except OSError as exc:
            if exc.errno in PRESENTE:
                return True
            if exc.errno in ASSENTE:
                return False  # nessuna risposta ARP: non c'è
    return False


def hostname(ip: str) -> str:
    try:
        return socket.gethostbyaddr(ip)[0]
    except OSError:
        return ""


def discover_devices(network: ipaddress.IPv4Network | None = None) -> list[dict]:
    """Dispositivi visti ora sulla rete: [{ip, mac, hostname, private}] (per "Cerca dispositivi")."""
    net = network or local_network()
    sweep(net)
    table = read_arp_table()
    rows = [{"mac": mac, "ip": ip} for mac, ip in table.items()
            if net is None or ipaddress.ip_address(ip) in net]
    with ThreadPoolExecutor(max_workers=16) as pool:
        names = list(pool.map(lambda r: hostname(r["ip"]), rows))
    for r, n in zip(rows, names):
        r["hostname"] = n
        r["private"] = is_private_mac(r["mac"])
    rows.sort(key=lambda r: tuple(int(x) for x in r["ip"].split(".")))
    return rows


class PresenceMonitor:
    """Controlla periodicamente i telefoni registrati e attiva/disattiva la protezione."""

    def __init__(self, config, set_armed: Callable[[bool, str], object], is_armed: Callable[[], bool],
                 clock: Callable[[], float] = time.time,
                 sweep_fn: Callable[[], None] = sweep,
                 arp_fn: Callable[[], dict[str, str]] = read_arp_table,
                 alive_fn: Callable[[str], bool] = is_alive):
        self.config = config
        self.set_armed = set_armed
        self.is_armed = is_armed
        self.clock = clock
        self._sweep, self._arp, self._alive = sweep_fn, arp_fn, alive_fn
        self.started = clock()
        self.last_seen: dict[str, float] = {}   # mac → ultima risposta
        self.last_ip: dict[str, str] = {}
        self.home: bool | None = None           # None = ancora da stabilire (avvio)
        self.last_check = 0.0
        self.last_change = ""                   # testo dell'ultimo cambio automatico
        self._was_enabled = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # --- configurazione ---------------------------------------------------
    @property
    def cfg(self) -> dict:
        return self.config.get("presence") or {}

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("enabled")) and bool(self.devices)

    @property
    def devices(self) -> list[dict]:
        return [d for d in (self.cfg.get("devices") or []) if d.get("mac")]

    # --- ciclo ------------------------------------------------------------
    def start(self) -> None:
        if self._thread:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="presenza", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.check()
            except Exception:  # noqa: BLE001
                log.exception("Presenza: controllo non riuscito")
            self._stop.wait(max(20.0, float(self.cfg.get("interval_seconds", 60))))

    def _reset(self, now: float) -> None:
        """Presenza appena accesa: si riparte da zero, con il periodo di attesa iniziale."""
        self.started = now
        self.home = None
        self.last_seen.clear()
        self.last_ip.clear()
        self.last_check = 0.0

    def check(self) -> None:
        """Un giro: ricerca, verifica dei telefoni registrati, eventuale cambio di stato."""
        now = self.clock()
        if not self.enabled:
            self._was_enabled = False
            return
        if not self._was_enabled:
            self._was_enabled = True
            self._reset(now)
        self._sweep()
        table = self._arp()
        owner = {ip: m for m, ip in table.items()}
        targets = {}
        for dev in self.devices:
            mac = normalize_mac(dev["mac"])
            ip = table.get(mac) or self.last_ip.get(mac)
            if not ip or owner.get(ip, mac) != mac:
                continue  # indirizzo ora usato da un altro dispositivo: non vale come prova
            self.last_ip[mac] = ip
            targets[mac] = ip
        if targets:  # verifiche in parallelo: un telefono assente non rallenta gli altri
            with ThreadPoolExecutor(max_workers=min(8, len(targets))) as pool:
                alive = dict(zip(targets, pool.map(self._alive, targets.values())))
            for mac, ok in alive.items():
                if ok:
                    self.last_seen[mac] = now
        self.last_check = now
        self.evaluate(now)

    def people(self, now: float | None = None) -> list[dict]:
        """[{name, mac, home, last_seen}] per l'interfaccia e /stato."""
        now = now or self.clock()
        away_s = float(self.cfg.get("away_minutes", 15)) * 60
        out = []
        for dev in self.devices:
            mac = normalize_mac(dev["mac"])
            seen = self.last_seen.get(mac, 0.0)
            out.append({"name": dev.get("name") or mac, "mac": mac, "last_seen": seen,
                        "home": bool(seen) and now - seen <= away_s})
        return out

    def evaluate(self, now: float) -> None:
        if not self.enabled:
            return
        people = self.people(now)
        anyone = any(p["home"] for p in people)
        away_s = float(self.cfg.get("away_minutes", 15)) * 60
        if not anyone and self.home is None and now - self.started < away_s:
            return  # appena avviato: si aspetta prima di dire "sono usciti tutti"
        if anyone == self.home:
            return
        previous, self.home = self.home, anyone
        if anyone:
            who = ", ".join(p["name"] for p in people if p["home"])
            if previous is False and self.cfg.get("disarm_on_arrival", True) and self.is_armed():
                self.last_change = _('rientro di {who}', who=who)
                self.set_armed(False, _("presenza: è rientrato {who}", who=who))
            log.info("Presenza: in casa %s", who)
        else:
            if self.cfg.get("arm_when_all_away", True) and not self.is_armed():
                self.last_change = _('usciti tutti')
                self.set_armed(True, _('presenza: sono usciti tutti'))
            log.info("Presenza: fuori casa tutti")

    def summary(self) -> str:
        """Riga per /stato e per l'interfaccia: "In casa: Marco · Fuori: Luca"."""
        if not self.enabled:
            return ""
        people = self.people()
        if not self.last_check:
            return _('👥 Presenza: primo controllo in corso…')
        inside = [p["name"] for p in people if p["home"]]
        outside = [p["name"] for p in people if not p["home"]]
        parts = []
        if inside:
            parts.append(_('In casa: ') + ", ".join(inside))
        if outside:
            parts.append((_('Fuori: ') if not parts else _('fuori: ')) + ", ".join(outside))
        return "👥 " + " · ".join(parts)
