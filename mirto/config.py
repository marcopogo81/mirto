# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Configurazione di Mirto.

La configurazione è un file YAML leggibile nella cartella di configurazione
dell'utente. Le credenziali NON stanno qui: sono nel portachiavi del sistema
(vedi ``mirto.secrets``).
"""

from __future__ import annotations

import copy
import logging
import os
import threading
from pathlib import Path
from typing import Any

import yaml
from platformdirs import user_config_dir, user_data_dir

from . import APP_NAME, NOME_DI_PRIMA

log = logging.getLogger(__name__)

DEFAULTS: dict[str, Any] = {
    "general": {
        "retention_days": 30,          # giorni di conservazione di foto ed eventi
        "prevent_sleep": True,         # impedisce la sospensione del computer
        "language": "auto",            # lingua di interfaccia e messaggi: auto (del sistema), it, en
        "legal_notice": 0,             # versione delle condizioni d'uso accettate (vedi gui/legal_ui.py)
        "legal_accepted_at": "",       # quando sono state accettate
    },
    "telemetry": {
        "stats": None,                 # statistiche anonime: None = non ancora chiesto, True, False (vedi telemetry.py)
        "install_id": "",              # ID casuale dell'installazione (solo con il sì; azzerabile)
        "update_check": True,          # chiede una volta al giorno qual è l'ultima versione (non invia dati)
        "endpoint": "",                # server delle statistiche (vuoto = quello di Mirto)
        "last_sent": 0,
        "declined_pending": False,     # il "no" anonimo deve ancora partire
        "notified_version": "",        # ultima versione nuova già segnalata
        "forget_ids": [],              # ID da cancellare dal server (consenso ritirato o ID rinnovato)
    },
    "cameras": {
        "auto_discover": True,         # aggiunge da sola le camere locali trovate
        "rescan_seconds": 3,           # ogni quanto controllare collegamenti/scollegamenti
        "overrides": {},               # per ID stabile: name, enabled, ignore_zones, motion_sensitivity
        "network": [],                 # [{id, name, url, mdns?}] camere RTSP/HTTP (mdns = annuncio Bonjour)
        "network_discover": True,      # cerca le camere che si annunciano in rete (mDNS _rtsp._tcp)
        "hidden_found": [],            # camere trovate in rete che l'utente ha nascosto (nome del servizio)
        "dark_told": [],               # camere a cui si è già detto «immagine scura»: non si ripete ogni notte
    },
    "detection": {
        "analysis_fps": 5,             # frame analizzati al secondo per camera
        "motion_min_area": 0.01,       # frazione minima dell'immagine che deve cambiare
        "model": "yolox_nano",         # yolox_nano (veloce) o yolox_s (più preciso)
        "person_threshold": 0.40,      # "c'è il dubbio che sia una persona"
        "watch": ["person"],           # categorie sorvegliate (chiavi COCO: person, car, dog, cat…)
        "object_threshold": 0.50,      # confidenza minima per animali, veicoli e oggetti
        "confirm_frames": 2,           # persona in almeno N frame...
        "window_frames": 5,            # ...su questi ultimi M frame
        "dark_threshold": 25,          # luminosità media sotto cui avvisare "immagine troppo scura"
        "bird_species": True,          # per gli uccelli, riconosce anche il gruppo (piccioni, corvidi…)
        "species_threshold": 0.35,     # probabilità minima del gruppo di uccelli
    },
    "audio": {
        "mac_mic": False,              # ascolta il microfono del Mac (si attiva nelle Impostazioni → Suoni)
        "node_mics": True,             # ascolta i microfoni dei nodi che ne hanno uno
        "mac_camera": "",              # camera da fotografare quando il Mac sente qualcosa (vuoto = la prima)
        "watch": ["glass", "smoke_alarm", "alarm", "scream"],  # suoni che mandano un avviso
        "always_notify": ["smoke_alarm"],  # avvisa anche a protezione disattivata (sicurezza)
        "thresholds": {},              # soglie per tipo di suono, es. {"dog": 0.5}
        "cooldown_seconds": 120,       # pausa tra due avvisi dello stesso suono dalla stessa sorgente
    },
    "recording": {
        "enabled": True,               # registrazione video (NVR)
        "mode": "events",              # di serie per ogni camera: off, events (clip degli eventi), continuous
        "cameras": {},                 # modalità per camera: {ID: "off" | "events" | "continuous"}
        "armed_only": {},              # per camera: {ID: true} = registra solo con la protezione attiva
        "folder": "",                  # cartella delle registrazioni (vuoto = <dati di Mirto>/recordings)
        "retention_days": 7,           # giorni di registrazione continua conservati
        "max_gb": 0,                   # spazio massimo in GB (0 = max_disk_fraction dello spazio disponibile)
        "max_disk_fraction": 0.5,      # al massimo metà dello spazio disponibile
        "min_free_gb": 2,              # spazio libero sotto cui si cancella o si sospende
        "pre_seconds": 10,             # secondi prima dell'evento nella clip
        "post_seconds": 20,            # secondi dopo l'evento nella clip
        "segment_seconds": 60,         # lunghezza degli spezzoni
        "local_fps": 10,               # fotogrammi al secondo registrati per le camere locali
        "local_max_width": 1280,       # larghezza massima registrata per le camere locali
        "ffmpeg": "",                  # percorso di ffmpeg (vuoto = quello incluso o del sistema)
        "encoder": "",                 # codificatore preferito (vuoto = il migliore disponibile)
        "telegram_clip": True,         # manda la clip dell'evento su Telegram dopo la foto
    },
    "presence": {
        "enabled": False,              # presenza dai telefoni sul Wi-Fi di casa
        "devices": [],                 # [{name, mac}] telefoni delle persone di casa
        "away_minutes": 15,            # "fuori" se il telefono non risponde da tanti minuti
        "arm_when_all_away": True,     # attiva la protezione quando escono tutti
        "disarm_on_arrival": True,     # la disattiva quando rientra qualcuno
        "interval_seconds": 60,        # ogni quanto controllare
    },
    "notifications": {
        "cooldown_seconds": 120,       # pausa minima tra due avvisi della stessa camera
        "telegram": {
            "enabled": False,
            "chat_id": "",             # ID del gruppo "Mirto Admin" (supergruppo, es. -100123...)
            "allowed_user_ids": [],    # utenti autorizzati ai comandi e ai pulsanti
        },
        "email": {
            "enabled": False,
            "provider": "gmail",
            "host": "",
            "port": 0,
            "security": "",            # "ssl" o "starttls"; vuoto = dal preset
            "username": "",
            "sender": "",
            "recipients": [],          # indirizzi Mirto Admin
        },
        "sms": {
            "enabled": False,
            "provider": "http",        # http | twilio | email2sms
            "mode": "always",          # always | fallback (solo se Telegram fallisce)
            "daily_limit": 20,
            "recipients": [],          # numeri in formato internazionale (+39...)
            "http": {
                "url": "",
                "method": "POST",
                "headers": {},
                "body": "",
                "content_type": "application/json",
                "success_status": [200, 201, 202],
                "success_contains": "",
            },
            "twilio": {"account_sid": "", "from_number": ""},
            "email2sms": {"address_template": "{numero}@provider.example"},
        },
    },
    "schedule": {
        "enabled": True,               # programmazione automatica attiva?
        "rules": [],                   # [{nome, azione: inserisci|disinserisci, ora: "HH:MM", giorni: [lun..dom], dal, al, attiva}]
    },
    "actions": {
        "armed": False,                # sistema inserito?
        "person_threshold": 0.60,      # soglia più alta delle notifiche
        "confirm_frames": 3,
        "window_frames": 5,
        "heartbeat_seconds": 60,
        "nodes": [],                   # [{id, name, url}] — certificato in nodes/<id>.pem, chiave nel portachiavi
        "items": [],                   # azioni configurate (vedi actions.engine.ActionSpec)
    },
}


# NOME_DI_PRIMA sta in mirto/__init__.py: lo guardano anche le credenziali.


def ambiente(nome: str) -> str | None:
    """Legge ``MIRTO_<nome>``, e se non c'e' accetta ancora il vecchio ``MYHOUSE_<nome>``.

    Chi usava il programma prima del cambio di nome ha quelle variabili scritte nei propri
    appunti e nei propri script. Farle smettere di funzionare in silenzio vuol dire farlo
    girare su una cartella vuota senza capire perche': meglio accettarle ancora e dirlo nel
    registro, cosi' si aggiornano quando si vogliono aggiornare.
    """
    if (v := os.environ.get(f"MIRTO_{nome}")):
        return v
    if (v := os.environ.get(f"MYHOUSE_{nome}")):
        log.warning("MYHOUSE_%s e' il nome di prima: ora si chiama MIRTO_%s (per ora vale ancora)",
                    nome, nome)
        return v
    return None


def _eredita(nuova: Path, vecchia: Path) -> Path:
    """Porta nella cartella nuova quel che c'era in quella del nome vecchio.

    Il programma si chiamava MyHouse, e la sua configurazione sta in una cartella con quel
    nome. Senza questo, al primo avvio dopo il cambio di nome uno ritrova il programma come
    appena installato — niente camere, niente nodi, nessun amministratore — con i suoi dati
    ancora sul disco, ma in un posto dove nessuno li cerca piu'.

    Si **sposta**, non si copia: due cartelle vive con la stessa configurazione sono una
    trappola, ne modifichi una e il programma legge l'altra. E si sposta una volta sola: se
    la cartella nuova esiste ed e' abitata, quella e' quella buona e la vecchia non si tocca.

    Se lo spostamento non riesce (permessi, cartella aperta da qualcun altro) non si muore:
    si scrive nel registro e si parte con la cartella nuova vuota. Un avvio che non parte e'
    peggio di una configurazione da rifare.
    """
    try:
        if nuova.is_dir() and any(nuova.iterdir()):
            return nuova
        if vecchia.is_dir() and any(vecchia.iterdir()):
            nuova.parent.mkdir(parents=True, exist_ok=True)
            if nuova.is_dir():
                nuova.rmdir()          # e' vuota: l'abbiamo appena guardata
            vecchia.rename(nuova)
            log.info("Cartella «%s» ereditata da «%s» (il programma si chiamava %s)",
                     nuova, vecchia, NOME_DI_PRIMA)
    except OSError as exc:
        log.warning("Non sono riuscito a ereditare «%s»: %s", vecchia, exc)
    return nuova


def config_dir() -> Path:
    override = ambiente("CONFIG_DIR")
    if override:
        p = Path(override)
    else:
        p = _eredita(Path(user_config_dir(APP_NAME, appauthor=False)),
                     Path(user_config_dir(NOME_DI_PRIMA, appauthor=False)))
    p.mkdir(parents=True, exist_ok=True)
    return p


def data_dir() -> Path:
    override = ambiente("DATA_DIR")
    if override:
        p = Path(override)
    else:
        p = _eredita(Path(user_data_dir(APP_NAME, appauthor=False)),
                     Path(user_data_dir(NOME_DI_PRIMA, appauthor=False)))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _deep_merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class Config:
    """Configurazione con valori di default, salvataggio atomico e accesso per percorso."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else config_dir() / "config.yaml"
        self._lock = threading.RLock()
        self.data: dict[str, Any] = copy.deepcopy(DEFAULTS)
        self.load_warning = ""   # perché si è ripartiti dai predefiniti: l'interfaccia lo mostra
        self.load()

    def load(self) -> None:
        """Legge la configurazione; se è rovinata riparte dai predefiniti **dicendolo**.

        Prima un file YAML non valido alzava l'eccezione fin dentro l'avvio: per chi usa il
        programma era «Mirto non si apre», senza una parola sul perché. Succede a chi apre il
        file a mano, e succede se il disco si riempie durante un salvataggio. Il file rovinato non
        si cancella — dentro ci sono le camere e gli orari di una casa — si mette da parte con
        «.rotto», così si può recuperare a mano. Illeggibile per i permessi è un caso diverso: lì
        il file è valido e spostarlo sarebbe un danno, quindi si avvisa e non si tocca (Q-06).
        """
        with self._lock:
            self.load_warning = ""
            if not self.path.exists():
                self.data = copy.deepcopy(DEFAULTS)
                return
            try:
                testo = self.path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                self.data = copy.deepcopy(DEFAULTS)
                self.load_warning = f"configurazione illeggibile ({exc}): avvio con le impostazioni predefinite"
                log.error("Configurazione %s illeggibile: %s", self.path, exc)
                return
            try:
                raw = yaml.safe_load(testo) or {}
                if not isinstance(raw, dict):
                    raise yaml.YAMLError("il file non contiene impostazioni")
            except yaml.YAMLError as exc:
                self.data = copy.deepcopy(DEFAULTS)
                da_parte = self._metti_da_parte()
                self.load_warning = (f"configurazione rovinata ({exc}): avvio con le impostazioni "
                                     f"predefinite" + (f"; il file di prima è in {da_parte.name}" if da_parte else ""))
                log.error("Configurazione %s rovinata: %s", self.path, exc)
                return
            self.data = _deep_merge(DEFAULTS, raw)

    def _metti_da_parte(self) -> Path | None:
        """Sposta la configurazione rovinata in «config.yaml.rotto». None se non si riesce."""
        destinazione = self.path.with_suffix(self.path.suffix + ".rotto")
        try:
            self.path.replace(destinazione)
        except OSError as exc:
            log.error("Configurazione rovinata non spostabile in %s: %s", destinazione, exc)
            return None
        return destinazione

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(yaml.safe_dump(self.data, allow_unicode=True, sort_keys=False), encoding="utf-8")
            try:  # solo per l'utente: la configurazione dice dove sono le camere e com'è fatta la casa
                os.chmod(tmp, 0o600)
            except OSError as exc:  # pragma: no cover - Windows e filesystem senza permessi
                log.debug("Permessi della configurazione non impostati: %s", exc)
            os.replace(tmp, self.path)

    def get(self, dotted: str, default: Any = None) -> Any:
        with self._lock:
            node: Any = self.data
            for part in dotted.split("."):
                if not isinstance(node, dict) or part not in node:
                    return default
                node = node[part]
            return copy.deepcopy(node)

    def set(self, dotted: str, value: Any) -> None:
        with self._lock:
            parts = dotted.split(".")
            node = self.data
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = copy.deepcopy(value)
