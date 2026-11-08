# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Gestore delle sorgenti: camere locali (con collegamento a caldo) e di rete."""

from __future__ import annotations

import dataclasses
import inspect
import logging
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable
from urllib.parse import urlsplit

from ..config import Config
from ..netutil import redact_url, with_credentials
from .discovery import CameraInfo, DiscoveryWatcher, discover_local_cameras
from .network_discovery import (
    FAST_REFRESH_SECONDS,
    REFRESH_SECONDS,
    NetworkCameraBrowser,
    base_service,
    matches,
)
from .source import CameraSource, open_capture
from ..i18n import _

log = logging.getLogger(__name__)

STOP_JOIN_SECONDS = 5.0  # quanto si aspetta, in sottofondo, che il thread di una camera finisca


def default_threaded_discovery(platform: str = sys.platform) -> bool:
    """La scansione delle camere va in un thread suo?

    Su macOS no: l'elenco di AVFoundation si aggiorna attraverso il run loop principale, quindi la scansione
    resta sul thread della GUI (è veloce, non apre le camere). Su Windows e Linux sì: lì il ripiego può
    arrivare ad aprire le camere, e una finestra che si ferma ogni tre secondi è il primo difetto che si nota.
    """
    return not platform.startswith("darwin")


class CameraManager:
    """Tiene allineate le sorgenti attive con le camere disponibili.

    Callback:
      ``on_source_added(source)``   nuova camera attiva
      ``on_source_removed(cam_id)`` camera scollegata o disattivata
      ``on_status(cam_id, status)`` online / offline / connecting
    """

    def __init__(
        self,
        config: Config,
        discover: Callable[[], list[CameraInfo]] = discover_local_cameras,
        capture_factory=open_capture,
        network_browser: NetworkCameraBrowser | None = None,
        secrets=None,
    ):
        self.config = config
        self.secrets = secrets  # SecretStore: credenziali delle camere di rete (voci «rtsp:<id>»)
        self._discover = discover
        try:
            self._discover_takes_busy = "busy" in inspect.signature(discover).parameters
        except (TypeError, ValueError):  # scoperta su misura nei test
            self._discover_takes_busy = False
        self._capture_factory = capture_factory
        # ricerca delle camere di rete annunciate via mDNS (creata in start() se non passata)
        self.network_browser = network_browser
        # impostati dal motore dei nodi: indirizzo attuale di una camera di nodo e indirizzi dei nodi abbinati
        self.node_resolver: Callable[[str], str | None] | None = None
        self.node_hosts: Callable[[], set] | None = None
        self.node_names: Callable[[], dict] | None = None
        if network_browser is not None:
            network_browser.known_alive = self._streaming_from
        self.sources: dict[str, CameraSource] = {}
        self._reaper: ThreadPoolExecutor | None = None  # "spazzino": aspetta i thread fermati, fuori dalla GUI
        self._lock = threading.RLock()
        self.on_source_added: list[Callable[[CameraSource], None]] = []
        self.on_source_removed: list[Callable[[str], None]] = []
        self.on_status: list[Callable[[str, str], None]] = []
        self.watcher = DiscoveryWatcher(
            on_added=self._local_added,
            on_removed=self._local_removed,
            on_changed=self._local_changed,
            discover=self._discover_cameras,
            interval=float(config.get("cameras.rescan_seconds", 3)),
        )

    # --- camere di rete ------------------------------------------------
    @staticmethod
    def _net_info(net: dict) -> CameraInfo:
        extra = {"mdns": net["mdns"]} if net.get("mdns") else {}
        if net.get("node"):
            extra = {"node": str(net["node"])}  # camera di un nodo: l'indirizzo lo decide il nodo
        return CameraInfo(id=f"net:{net['id']}", name=net.get("name") or net["id"], kind="network",
                          url=net["url"], extra=extra)

    def open_indices(self) -> set[int]:
        """Indici OpenCV delle camere locali aperte adesso: la ricerca per indice non deve toccarle."""
        with self._lock:
            return {s.info.index for s in self.sources.values()
                    if s.info.kind == "local" and s.info.index is not None}

    def _discover_cameras(self) -> list[CameraInfo]:
        """Scansione delle camere locali, risparmiando quelle che Mirto ha già aperto."""
        if self._discover_takes_busy:
            return self._discover(busy=self.open_indices())
        return self._discover()

    def rtsp_credentials(self, cam_id: str) -> str:
        """«utente:password» della camera di rete, dal portachiavi ("" se non ce ne sono)."""
        if self.secrets is None or not cam_id.startswith("net:"):
            return ""
        from ..credentials import rtsp_name  # noqa: PLC0415

        try:
            return self.secrets.get(rtsp_name(cam_id.split(":", 1)[1])) or ""
        except Exception:  # noqa: BLE001
            log.exception("Credenziali della camera %s non leggibili", cam_id)
            return ""

    def _open(self, info: CameraInfo):
        """Apre la camera; per quelle dei nodi o trovate via mDNS rilegge prima l'indirizzo attuale."""
        service = info.extra.get("mdns") if info.kind == "network" else None
        url = None
        if info.kind == "network" and info.extra.get("node") and self.node_resolver is not None:
            try:
                url = self.node_resolver(info.id)
            except Exception as exc:  # noqa: BLE001
                log.debug("Indirizzo della camera %s dal nodo non disponibile: %s", info.name, exc)
        elif service and self.network_browser is not None:
            url = self.network_browser.resolve_url(service, info.url or "")
        if url:
            if url and url != info.url:
                log.info("Camera %s: indirizzo aggiornato %s → %s", info.name,
                         redact_url(info.url or ""), redact_url(url))
                self._update_net_url(info.id, url)
                info = dataclasses.replace(info, url=url)
                with self._lock:
                    src = self.sources.get(info.id)
                if src is not None:  # così l'indirizzo mostrato è quello in uso
                    src.info = dataclasses.replace(src.info, url=url)
        creds = self.rtsp_credentials(info.id)
        if creds:  # utente e password solo qui: non in configurazione, non nei registri
            info = dataclasses.replace(info, url=with_credentials(info.url or "", creds))
        return self._capture_factory(info)

    def _update_net_url(self, cam_id: str, url: str) -> None:
        """Salva l'ultimo indirizzo valido (serve se un giorno l'annuncio mDNS non arriva)."""
        nets = self.config.get("cameras.network") or []
        for net in nets:
            if f"net:{net.get('id')}" == cam_id and net.get("url") != url:
                net["url"] = url
                self.config.set("cameras.network", nets)
                try:
                    self.config.save()
                except Exception as exc:
                    log.warning("Salvataggio configurazione fallito: %s", exc)
                return

    def _streaming_from(self, addr: str, port: int) -> bool:
        """Mirto sta ricevendo video da ``addr:port`` (ultimo fotogramma da meno di 3 secondi)?"""
        now = time.time()
        with self._lock:
            srcs = [s for s in self.sources.values() if s.info.kind == "network"]
        for s in srcs:
            try:
                u = urlsplit(s.info.url or "")
                same = u.hostname == addr and (u.port or 554) == port
            except ValueError:
                continue
            if same:
                _f, ts, _seq = s.latest()
                if ts and now - ts < 3:
                    return True
        return False

    @staticmethod
    def new_network_id(name: str, nets: list[dict]) -> str:
        base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "camera"
        cid, k = base, 2
        while any(x.get("id") == cid for x in nets):
            cid, k = f"{base}-{k}", k + 1
        return cid

    def _twins(self, service: str) -> list:
        base = base_service(service)
        return [c for c in self.network_browser.found() if base_service(c.service) == base]

    @staticmethod
    def _live_of(twins) -> list[str]:
        return list(dict.fromkeys(a for c in twins for a in c.live))

    def network_address_info(self, net: dict) -> dict | None:
        """Indirizzo in uso e altri indirizzi che rispondono, per una camera di rete configurata."""
        if self.network_browser is None:
            return None
        twins = [c for c in self.network_browser.found() if matches(net, c)]
        if not twins:
            return None
        try:
            from urllib.parse import urlsplit  # noqa: PLC0415
            current = urlsplit(net.get("url", "")).hostname or ""
        except ValueError:
            current = ""
        live = self._live_of(twins)
        return {"current": current, "live": live, "others": [a for a in live if a != current]}

    def discovered_network_cameras(self) -> list[dict]:
        """Camere annunciate in rete e non ancora in Mirto: [{service, name, suggested, url, mirto}].

        Una camera già aggiunta a mano con lo stesso indirizzo viene collegata al suo
        annuncio (campo ``mdns``), così segue da sola i cambi di indirizzo IP.
        """
        if self.network_browser is None:
            return []
        found = self.network_browser.found()
        nets = self.config.get("cameras.network") or []
        hidden = {base_service(h) for h in (self.config.get("cameras.hidden_found") or [])}
        try:  # le camere dei nodi Mirto arrivano con il nodo: non sono "camere trovate in rete"
            node_hosts = {h.lower() for h in (self.node_hosts() if self.node_hosts else set()) if h}
        except Exception:  # noqa: BLE001
            node_hosts = set()
        linked = False
        out = []
        seen: set[str] = set()
        for cam in found:
            if cam.mirto and node_hosts and (
                    cam.host.lower() in node_hosts or cam.host.split(".")[0].lower() in node_hosts
                    or any(a in node_hosts for a in cam.addresses)):
                continue
            net = next((n for n in nets if matches(n, cam)), None)
            if net is None:
                if base_service(cam.service) in seen | hidden:  # doppione dello stesso dispositivo, o nascosta
                    continue
                seen.add(base_service(cam.service))
                live = self._live_of(self._twins(cam.service))
                first = cam.addresses[0] if cam.addresses else ""
                out.append({"service": cam.service, "name": cam.name, "suggested": cam.suggested_name,
                            "url": cam.url(), "mirto": cam.mirto, "alive": cam.alive,
                            "also": [a for a in live if a != first]})
            elif not net.get("mdns") and not any(n.get("mdns") == cam.service for n in nets):
                net["mdns"] = cam.service
                linked = True
                log.info("Camera %s collegata all'annuncio mDNS %s", net.get("name") or net["id"], cam.service)
        if linked:
            self.config.set("cameras.network", nets)
            self.config.save()
            with self._lock:
                for net in nets:
                    src = self.sources.get(f"net:{net['id']}")
                    if src and net.get("mdns") and not src.info.extra.get("mdns"):
                        src.info = dataclasses.replace(src.info, extra={"mdns": net["mdns"]})
        return out

    def network_watch(self, fast: bool) -> None:
        """Controlli di rete frequenti (``fast``) mentre l'utente guarda "Tutte le cam"."""
        if self.network_browser is None:
            return
        self.network_browser.set_interval(FAST_REFRESH_SECONDS if fast else REFRESH_SECONDS)
        if fast:
            self.network_browser.refresh_now()

    def network_refresh_now(self) -> None:
        if self.network_browser is not None:
            self.network_browser.refresh_now()

    def network_watch_state(self) -> dict | None:
        """{refreshing, last (time.time), interval} oppure None se la ricerca in rete è spenta."""
        b = self.network_browser
        if b is None or not getattr(b, "running", False):
            return None
        return {"refreshing": b.refreshing, "last": b.last_refresh, "interval": b.interval}

    def hide_discovered(self, service: str) -> None:
        """Toglie dal riquadro "Trovate in rete" una camera che non interessa."""
        hidden = self.config.get("cameras.hidden_found") or []
        if service not in hidden:
            hidden.append(service)
            self.config.set("cameras.hidden_found", hidden)
            self.config.save()

    def unhide_discovered(self) -> int:
        """Rimette nel riquadro tutte le camere nascoste; restituisce quante erano."""
        n = len(self.config.get("cameras.hidden_found") or [])
        self.config.set("cameras.hidden_found", [])
        self.config.save()
        return n

    def add_discovered(self, service: str, name: str = "") -> str | None:
        """Aggiunge una camera trovata in rete; restituisce il suo ID (``net:...``)."""
        cam = self.network_browser.get(service) if self.network_browser else None
        if cam is None:
            return None
        nets = self.config.get("cameras.network") or []
        existing = next((n for n in nets if matches(n, cam)), None)
        if existing:
            return f"net:{existing['id']}"
        label = name.strip() or cam.suggested_name
        cid = self.new_network_id(label, nets)
        nets.append({"id": cid, "name": label, "url": cam.url(), "mdns": cam.service})
        self.config.set("cameras.network", nets)
        self.config.save()
        self.reload()
        return f"net:{cid}"

    # --- configurazione per camera ------------------------------------
    def override(self, cam_id: str) -> dict:
        return (self.config.get("cameras.overrides") or {}).get(cam_id, {})

    def display_name(self, cam: CameraInfo) -> str:
        return self.override(cam.id).get("name") or cam.name

    def _apply_overrides(self, cam: CameraInfo) -> CameraInfo:
        ov = self.override(cam.id)
        if ov.get("opencv_index") is not None and cam.kind == "local":
            cam = dataclasses.replace(cam, index=int(ov["opencv_index"]))
        if ov.get("name"):
            cam = dataclasses.replace(cam, name=ov["name"])
        return cam

    def _enabled(self, cam: CameraInfo) -> bool:
        return bool(self.override(cam.id).get("enabled", True))

    # --- elenco completo per la sezione "Tutte le cam" -----------------
    @staticmethod
    def kind_label(cam: CameraInfo) -> str:
        if cam.kind == "network":
            return _('Camera del nodo') if cam.extra.get("node") else _('Di rete')
        dtype = str(cam.extra.get("device_type", "")).lower()
        text = f"{cam.name} {cam.extra.get('model', '')}".lower()
        if "continuity" in dtype or "iphone" in text or "continuity" in text or "ipad" in text:
            return "iPhone"
        if "builtin" in dtype:
            return _("Integrata")
        if "external" in dtype:
            return _("USB / esterna")
        # senza tipo dal sistema: si deduce dal nome ("Fotocamera di MacBook Air", "FaceTime HD Camera"…)
        builtin_words = ("facetime", "integrat", "built-in", "macbook", "imac", "studio display")
        if any(w in text for w in builtin_words):
            return _("Integrata")
        return _("USB / esterna")

    def all_cameras(self) -> list[dict]:
        """Tutte le camere conosciute, anche quelle disattivate o scollegate.

        Ogni voce: id, name (nome mostrato), original (nome del sistema), kind, enabled,
        connected (vista ora dal computer), status (stato della sorgente se attiva).
        """
        overrides = self.config.get("cameras.overrides") or {}
        out: dict[str, dict] = {}
        cams: list[CameraInfo] = list(self.watcher.known.values())
        for net in self.config.get("cameras.network") or []:
            cams.append(self._net_info(net))
        with self._lock:
            sources = dict(self.sources)
        nets = {f"net:{n['id']}": n for n in (self.config.get("cameras.network") or [])}
        try:
            node_names = self.node_names() if self.node_names else {}
        except Exception:  # noqa: BLE001
            node_names = {}
        for cam in cams:
            ov = overrides.get(cam.id, {})
            src = sources.get(cam.id)
            detail = ""  # per le camere di rete: indirizzo in uso ed eventuali altri che rispondono
            if cam.kind == "network" and cam.id in nets:
                info = self.network_address_info(nets[cam.id])
                current = (src.info.url if src else cam.url) or ""
                try:
                    from urllib.parse import urlsplit  # noqa: PLC0415
                    host = urlsplit(current).hostname or ""
                except ValueError:
                    host = ""
                if host:
                    detail = host
                if info:
                    others = [a for a in info["live"] if a != host]
                    if others:
                        detail += _(' · risponde anche su ') + ", ".join(others)
            node_id = cam.extra.get("node", "") if cam.kind == "network" else ""
            out[cam.id] = {
                "id": cam.id, "name": ov.get("name") or cam.name, "original": cam.name,
                "kind": self.kind_label(cam), "detail": detail, "enabled": bool(ov.get("enabled", True)),
                "connected": True, "status": src.status if src else "",
                "node": node_id, "node_name": node_names.get(node_id, node_id) if node_id else "",
                # Si può scambiare con un'altra: solo le camere locali, che si aprono per posizione,
                # e solo se ce n'è più d'una — con una sola non c'è nessuno scambio possibile.
                "scambiabile": cam.kind == "local" and not node_id,
            }
        for cid, ov in overrides.items():  # camere viste in passato e ora scollegate
            if cid not in out and ov.get("seen_name"):
                out[cid] = {
                    "id": cid, "name": ov.get("name") or ov["seen_name"], "original": ov["seen_name"],
                    "kind": ov.get("seen_kind", ""), "enabled": bool(ov.get("enabled", True)),
                    "connected": False, "status": "",
                }
        locali = sum(1 for c in out.values() if c.get("scambiabile"))
        if locali < 2:
            for c in out.values():
                c["scambiabile"] = False
        # Prima le camere "semplici", poi quelle di ogni nodo raggruppate; le scollegate in fondo.
        #
        # **Accesa o spenta non entra nell'ordine.** Ci entrava fino al 25/10, per mettere in cima
        # quelle in uso, e costava molto più di quello che rendeva: in «Tutte le cam» l'interruttore
        # si preme in una riga, e una riga che si sposta *per via della pressione* porta un'altra
        # camera sotto il dito. Marco: *«se attivo la cam dell'iPhone si attiva quella del MacBook e
        # viceversa»* — non si attivava l'altra, si erano scambiate di posto nel momento del clic.
        # Un elenco in cui si agisce non si riordina da sé: l'ordine dipende da cose che cambiano
        # quando si collega o si scollega una camera, non da quello che l'utente sta facendo adesso.
        return sorted(out.values(), key=lambda c: (not c["connected"], bool(c.get("node")),
                                                   (c.get("node_name") or "").lower(),
                                                   c["name"].lower()))

    def _remember(self, cam_id: str, ov: dict) -> None:
        """Annota nome e tipo, così la camera resta in elenco anche quando è scollegata."""
        info = self.watcher.known.get(cam_id)
        if info:
            ov["seen_name"] = info.name
            ov["seen_kind"] = self.kind_label(info)

    def set_enabled(self, cam_id: str, enabled: bool) -> None:
        """Attiva o disattiva una camera nel sistema (salvato in configurazione)."""
        overrides = self.config.get("cameras.overrides") or {}
        ov = dict(overrides.get(cam_id, {}))
        ov["enabled"] = bool(enabled)
        self._remember(cam_id, ov)
        overrides[cam_id] = ov
        self.config.set("cameras.overrides", overrides)
        self.config.save()
        self.reload()

    def indice_opencv(self, cam_id: str) -> int | None:
        """Con quale indice si apre oggi questa camera: quello scelto a mano, o quello trovato."""
        ov = self.override(cam_id)
        if ov.get("opencv_index") is not None:
            return int(ov["opencv_index"])
        info = self.watcher.known.get(cam_id)
        return info.index if info is not None and info.kind == "local" else None

    def scambia_indici(self, a_id: str, b_id: str) -> bool:
        """Due camere locali si scambiano l'indice con cui vengono aperte. True se è stato fatto.

        Serve perché il nome e l'immagine possono non corrispondere. Nome e ID li dà il sistema
        operativo; la camera però la apre OpenCV **per posizione**, e l'ordine in cui OpenCV elenca i
        dispositivi non è quello del sistema: il 25/10, sul Mac di Marco, la scheda «Fotocamera di
        iPhone 12» mostrava il MacBook Air. L'ordine di OpenCV dipende da come quella versione chiede
        le camere ad Apple, non si può leggere da fuori, e indovinarlo vuol dire vederlo cambiare al
        prossimo aggiornamento — con le camere che si riscambiano e nessuno che se ne accorge.

        Quindi non si indovina: lo dice chi sta **guardando l'immagine**, una volta, e da quel momento
        resta scritto. Si scambia invece di assegnare, perché se questa camera mostra l'altra allora
        l'altra mostra questa: sistemarne una sola lascerebbe due nomi sullo stesso indice.
        """
        a_idx, b_idx = self.indice_opencv(a_id), self.indice_opencv(b_id)
        if a_id == b_id or a_idx is None or b_idx is None:
            return False
        overrides = dict(self.config.get("cameras.overrides") or {})
        for cam_id, indice in ((a_id, b_idx), (b_id, a_idx)):
            ov = dict(overrides.get(cam_id, {}))
            ov["opencv_index"] = int(indice)
            self._remember(cam_id, ov)
            overrides[cam_id] = ov
        self.config.set("cameras.overrides", overrides)
        self.config.save()
        self.reload()
        return True

    def rename(self, cam_id: str, name: str) -> None:
        """Nome mostrato nell'app e negli avvisi; vuoto = nome del sistema."""
        overrides = self.config.get("cameras.overrides") or {}
        ov = dict(overrides.get(cam_id, {}))
        if name.strip():
            ov["name"] = name.strip()
        else:
            ov.pop("name", None)
        self._remember(cam_id, ov)
        overrides[cam_id] = ov
        self.config.set("cameras.overrides", overrides)
        self.config.save()
        with self._lock:
            src = self.sources.get(cam_id)
        if src:  # solo il nome cambia: niente riapertura della camera
            original = ov.get("seen_name") or src.info.name
            if src.info.kind == "local" and cam_id in self.watcher.known:
                original = self.watcher.known[cam_id].name
            elif src.info.kind == "network":
                net = {f"net:{n['id']}": n for n in (self.config.get("cameras.network") or [])}.get(cam_id, {})
                original = net.get("name") or net.get("id") or src.info.name
            src.info = dataclasses.replace(src.info, name=ov.get("name") or original)

    # --- ciclo di vita --------------------------------------------------
    def start(self, threaded_discovery: bool = True) -> None:
        if self.network_browser is None and self.config.get("cameras.network_discover", True):
            self.network_browser = NetworkCameraBrowser()
            self.network_browser.known_alive = self._streaming_from
        if self.network_browser is not None:
            self.network_browser.start()
        for net in self.config.get("cameras.network") or []:
            self._add_source(self._net_info(net))
        if self.config.get("cameras.auto_discover", True):
            if threaded_discovery:
                self.watcher.start()
            else:
                self.watcher.scan()

    def scan(self) -> None:
        """Da chiamare periodicamente dal thread principale se ``threaded_discovery=False``."""
        self.watcher.scan()

    def stop(self, timeout: float = 3.0) -> None:
        """Ferma tutte le camere in parallelo, entro ``timeout`` secondi in totale."""
        import time  # noqa: PLC0415

        self.watcher.stop()
        if self.network_browser is not None:
            self.network_browser.stop()
        with self._lock:
            srcs = dict(self.sources)
            self.sources.clear()
        for src in srcs.values():
            src.request_stop()
        deadline = time.monotonic() + timeout
        for src in srcs.values():
            src.join(timeout=max(0.05, deadline - time.monotonic()))
        with self._lock:
            pool, self._reaper = self._reaper, None
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)
        for cid in srcs:
            for cb in self.on_source_removed:
                cb(cid)

    def reload(self) -> None:
        """Riapplica la configurazione (camere di rete aggiunte/rimosse, abilitazioni)."""
        wanted_net = {f"net:{n['id']}": n for n in (self.config.get("cameras.network") or [])}
        with self._lock:
            current = dict(self.sources)
        for cid, src in current.items():
            if src.info.kind == "network" and cid not in wanted_net:
                self._remove_source(cid)
            elif not self._enabled(src.info):
                self._remove_source(cid)
            elif src.info.kind == "local" and self.indice_opencv(cid) not in (None, src.info.index):
                # L'indice con cui si apre questa camera è cambiato (`scambia_indici`): la sorgente
                # aperta sta ancora leggendo l'altra. Si chiude, e il giro qui sotto la riapre con
                # l'indice nuovo. Senza questa riga, scambiare due camere non faceva **niente** fino
                # al riavvio — e Marco, giustamente: *«non succede nulla che faccia capire che è
                # avvenuto qualcosa»*. `reload` vuol dire «riapplica la configurazione», e da dove si
                # apre una camera è configurazione quanto l'essere accesa o spenta.
                self._remove_source(cid)
        for cid, net in wanted_net.items():
            if cid not in current:
                self._add_source(self._net_info(net))
        for cam in self.watcher.known.values():
            if cam.id not in self.sources:
                self._add_source(cam)

    # --- callback dal watcher ------------------------------------------
    def _local_added(self, cam: CameraInfo) -> None:
        self._add_source(cam)

    def _local_removed(self, cam: CameraInfo) -> None:
        self._remove_source(cam.id)

    def _local_changed(self, cam: CameraInfo) -> None:
        with self._lock:
            src = self.sources.get(cam.id)
        if src:
            src.update_info(self._apply_overrides(cam))

    # --- gestione sorgenti ---------------------------------------------
    def _add_source(self, cam: CameraInfo) -> None:
        if not self._enabled(cam):
            log.info("Camera %s disattivata in configurazione", cam.name)
            return
        cam = self._apply_overrides(cam)
        with self._lock:
            if cam.id in self.sources:
                return
            src = CameraSource(cam, capture_factory=self._open, on_status=self._status)
            src.credentials = lambda cid=cam.id: self.rtsp_credentials(cid)
            self.sources[cam.id] = src
        src.start()
        for cb in self.on_source_added:
            cb(src)

    def reap(self, wait: Callable[[], None]) -> None:
        """Aspetta in sottofondo che un thread fermato finisca davvero.

        Chi chiude una camera (l'utente da Impostazioni, una scansione, una ricarica) lo fa dal thread della
        finestra: una camera RTSP irraggiungibile ci mette fino a 5 secondi a staccarsi, e aspettarla lì
        significa un'interfaccia congelata. Si chiede lo stop e si lascia l'attesa a questo thread.
        """
        def run() -> None:
            try:
                wait()
            except Exception:  # noqa: BLE001
                log.exception("Arresto di una sorgente non riuscito")

        with self._lock:
            if self._reaper is None or getattr(self._reaper, "_shutdown", False):
                self._reaper = ThreadPoolExecutor(max_workers=1, thread_name_prefix="spazzino")
            pool = self._reaper
        try:
            pool.submit(run)
        except RuntimeError:  # in chiusura: si aspetta qui, tanto non c'è più una finestra da bloccare
            run()

    def _remove_source(self, cam_id: str) -> None:
        with self._lock:
            src = self.sources.pop(cam_id, None)
        if src:
            src.request_stop()  # ritorna subito: il join va allo spazzino
            self.reap(lambda s=src: s.join(timeout=STOP_JOIN_SECONDS))
            for cb in self.on_source_removed:
                cb(cam_id)

    def _status(self, cam_id: str, status: str) -> None:
        for cb in self.on_status:
            cb(cam_id, status)
