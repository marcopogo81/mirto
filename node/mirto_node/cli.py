# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""``mirto-nodo``: configurazione guidata del nodo, direttamente sul Raspberry.

    mirto-nodo            menu
    mirto-nodo stato      come sta il nodo (servizi, camera, audio, rete)
    mirto-nodo wifi       collega il nodo a una rete Wi-Fi
    mirto-nodo audio      sceglie l'uscita della sirena: Bluetooth, jack, USB o HDMI
    mirto-nodo camera     camera normale o NoIR, risoluzione, accesa o spenta
    mirto-nodo abbina     riapre per 5 minuti l'abbinamento con Mirto e mostra il PIN (es. un secondo Mac)
    mirto-nodo abbina --elenco         i Mirto abbinati: nome, indirizzo e data di ciascuno
    mirto-nodo abbina --revoca NOME    stacca un solo Mirto (NOME dall'elenco; «installazione» ritira la chiave vecchia)
    mirto-nodo abbina --revoca-tutti   stacca tutti i Mirto abbinati, poi riapre l'abbinamento
    mirto-nodo nome N     cambia l'etichetta del nodo (il nome in rete non si tocca, mai)
    mirto-nodo disco      SSD o chiavetta USB per la registrazione (ad anello, non si riempie mai)
    mirto-nodo prepara-immagine   pulisce il nodo per copiarne la microSD come immagine, poi lo spegne

Ogni scelta finisce in ``/etc/mirto-node/node.toml`` (i commenti restano) e i servizi vengono riavviati da
soli. Le password (Wi-Fi) le chiede il sistema e non passano da questo programma.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import install_tools as tools
from . import audio as audio_mod
from .audio import (OUTPUTS, AudioError, AudioPlayer, alsa_cards, audio_choices, chime_pcm,
                    parse_bt_devices)
from . import fs
from .rete import in_parole, stato_rete

CONF = Path("/etc/mirto-node")
TOML = CONF / "node.toml"
MTX_DIR = Path("/opt/mediamtx")
CAM_AVAHI = Path("/etc/avahi/services/mirto-cam.service")

OUTPUT_LABEL = audio_mod.OUTPUT_LABEL      # definita in audio.py: la usa anche il servizio

RESOLUTIONS = tools.RESOLUTIONS      # definite in install_tools: le usa anche il servizio


# ---------------------------------------------------------------- funzioni pure (provate dai test)

def toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def set_toml_values(text: str, section: str | None, values: dict) -> str:
    """Cambia o aggiunge chiavi in una sezione di un file TOML semplice, lasciando intatti i commenti.

    ``section`` None = parte iniziale (prima di ogni ``[sezione]``). La sezione si crea se manca.
    """
    lines = text.splitlines()
    head = re.compile(r"^\s*\[([^\]]+)\]\s*(#.*)?$")
    if section is None:
        start, end = 0, next((i for i, ln in enumerate(lines) if head.match(ln)), len(lines))
    else:
        start = next((i + 1 for i, ln in enumerate(lines) if (m := head.match(ln)) and m.group(1).strip() == section),
                     None)
        if start is None:
            while lines and not lines[-1].strip():
                lines.pop()
            lines += ["", f"[{section}]"]
            start = len(lines)
        end = next((i for i in range(start, len(lines)) if head.match(lines[i])), len(lines))
    todo = dict(values)
    for i in range(start, end):
        m = re.match(r"^(\s*)([A-Za-z0-9_-]+)\s*=", lines[i])
        if m and m.group(2) in todo:
            lines[i] = f"{m.group(1)}{m.group(2)} = {toml_value(todo.pop(m.group(2)))}"
    if todo:
        at = end
        while at > start and not lines[at - 1].strip():  # prima delle righe vuote che chiudono la sezione
            at -= 1
        lines[at:at] = [f"{k} = {toml_value(v)}" for k, v in todo.items()]
    return "\n".join(lines) + "\n"


def casse_da_scansione(text: str) -> list[tuple[str, str]]:
    """Righe ``[NEW] Device AA:BB:… Nome`` di una ricerca → [(mac, nome)], senza doppioni.

    Serve perché l'elenco di ``bluetoothctl devices`` **dimentica**: BlueZ tiene in memoria i
    dispositivi non abbinati solo finché si annunciano, e una cassa in abbinamento smette di
    annunciarsi appena qualcuno la tocca. Il 21/10, sul Pi 4, la ricerca trovava «JBL Flip 6» e
    trenta secondi dopo l'elenco non la conteneva più (``[DEL] Device 90:F2:…``): restavano solo i
    dispositivi BLE del vicinato, che invece continuano a gridare. Quello che si vede durante la
    ricerca, quindi, va preso al volo.
    """
    seen, out = set(), []
    for m in re.finditer(r"^\s*(\[\w+\]\s*)?Device\s+((?:[0-9A-F]{2}:){5}[0-9A-F]{2})\s+(.*)$",
                         text, re.I | re.M):
        tag, mac, resto = (m.group(1) or "").strip().upper(), m.group(2).upper(), m.group(3).strip()
        if resto.lower().startswith(("name:", "alias:")):
            nome = resto.split(":", 1)[1].strip()
        elif tag in ("[CHG]", "[DEL]"):
            continue       # «RSSI: …», «Connected: yes»: proprietà, non nomi (e [DEL] se n'è andato)
        else:
            nome = resto
        if not nome or nome.replace("-", ":").upper() == mac:
            continue                                   # senza nome non si sa nemmeno cosa sia
        if mac in seen:
            continue
        seen.add(mac)
        out.append((mac, nome))
    return out


def pare_una_cassa(mac: str, nome: str, info: str) -> bool:
    """Questo dispositivo può essere una cassa? (``info`` è la risposta di ``bluetoothctl info``.)

    Il pannello di Mirto proponeva tutto quello che si annuncia in zona: il 21/10, in casa, dodici
    dispositivi BLE (telefoni, orologi, un «BLE MF8470») e una sola cassa vera. Chi prova sceglie la
    riga sbagliata, e l'audio su un indirizzo BLE casuale non ci passa nemmeno in teoria.

    Si tiene: quello che è già abbinato (anche senza nome), e quello che ha un nome vero e un
    indirizzo **pubblico**. Si butta: gli indirizzi casuali (BLE) e i senza nome. Non si guarda la
    classe del dispositivo perché prima dell'abbinamento spesso non c'è, e scartare una cassa vera
    sarebbe peggio che mostrarne una finta.
    """
    if re.search(r"^\s*(Paired|Bonded):\s*yes", info, re.M | re.I):
        return True
    if not nome or nome.replace("-", ":").upper() == mac.upper():
        return False
    return not re.search(r"^Device\s+\S+\s+\(random\)", info, re.M | re.I)


def _nmcli_fields(line: str) -> list[str]:
    """Campi di ``nmcli -t``: separati da ':' con '\\:' per i due punti dentro i valori."""
    return [f.replace("\\:", ":").replace("\\\\", "\\") for f in re.split(r"(?<!\\):", line)]


def parse_wifi_list(text: str) -> list[dict]:
    """``nmcli -t -f IN-USE,SSID,SIGNAL,SECURITY dev wifi list`` → reti per segnale decrescente, senza doppioni."""
    best: dict[str, dict] = {}
    for line in text.splitlines():
        f = _nmcli_fields(line)
        if len(f) < 4 or not f[1]:
            continue
        try:
            signal = int(f[2])
        except ValueError:
            signal = 0
        net = {"ssid": f[1], "segnale": signal, "sicurezza": f[3] or "aperta", "in_uso": f[0].strip() == "*"}
        old = best.get(net["ssid"])
        if old is None or signal > old["segnale"]:
            net["in_uso"] = net["in_uso"] or bool(old and old["in_uso"])
            best[net["ssid"]] = net
        elif net["in_uso"]:
            old["in_uso"] = True
    return sorted(best.values(), key=lambda n: -n["segnale"])


# ---------------------------------------------------------------- piccoli aiuti per il terminale

def sh(cmd: list[str], timeout: float = 30, check: bool = False) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=check)
    except FileNotFoundError:
        return subprocess.CompletedProcess(cmd, 127, "", f"{cmd[0]}: comando non trovato")
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(cmd, 124, exc.stdout or "", "tempo scaduto")


def ask(prompt: str, default: str = "") -> str:
    try:
        ans = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
    except EOFError:
        ans = ""
    return ans or default


def yes(prompt: str, default: bool = True) -> bool:
    ans = ask(f"{prompt} (s/n)", "s" if default else "n").lower()
    return ans.startswith(("s", "y"))


def choose(title: str, labels: list[str], default: int | None = None) -> int | None:
    """Elenco numerato; restituisce l'indice scelto o None (invio a vuoto senza predefinito, oppure 0)."""
    print(f"\n{title}")
    for i, label in enumerate(labels, 1):
        print(f"  {i}) {label}")
    print("  0) annulla")
    while True:
        ans = ask("Scelta", str(default + 1) if default is not None else "")
        if ans in ("", "0"):
            return None
        if ans.isdigit() and 1 <= int(ans) <= len(labels):
            return int(ans) - 1
        print("  Numero non valido.")


def stampa(testo: str) -> None:
    """Stampa anche dove la console non sa scrivere tutti i caratteri.

    Il nodo gira su Linux, ma le sue prove girano anche su Windows, dove la console è `cp1252`: il
    segno di spunta ✓ non si può codificare e il processo **muore** con UnicodeEncodeError, portandosi
    dietro tutta la suite. Successo il 21/10, e per mezza giornata il motivo non si è potuto leggere
    perché il registro completo della CI non si scaricava.
    """
    try:
        print(testo)
    except UnicodeEncodeError:
        come = getattr(sys.stdout, "encoding", None) or "ascii"
        print(testo.encode(come, "replace").decode(come, "replace"))


def ok(msg: str) -> None:
    stampa(f"  ✓ {msg}")


def warn(msg: str) -> None:
    stampa(f"  ! {msg}")


def read_cfg() -> dict:
    try:
        return tools.read_toml(TOML)
    except FileNotFoundError:
        sys.exit(f"Manca {TOML}: il nodo non è installato (sudo ./node/install.sh).")


def write_cfg(section: str | None, values: dict) -> None:
    TOML.write_text(set_toml_values(TOML.read_text(), section, values))


def restart(service: str) -> bool:
    res = sh(["systemctl", "restart", service], timeout=60)
    (ok if res.returncode == 0 else warn)(f"{service} riavviato" if res.returncode == 0
                                          else f"{service} non riparte: {res.stderr.strip()}")
    return res.returncode == 0


def active(service: str) -> str:
    return sh(["systemctl", "is-active", service]).stdout.strip() or "assente"


# ---------------------------------------------------------------- comandi

def cmd_stato() -> int:
    cfg = read_cfg()
    host = socket.gethostname()
    print(f"\nNodo «{cfg.get('nome', host)}» · {tools.board_model() or 'modello sconosciuto'}")
    print(f"  Indirizzo per Mirto   https://{host}.local:{cfg.get('porta', 8443)}")
    try:
        from .__main__ import fingerprint  # noqa: PLC0415
        print(f"  Impronta                {fingerprint(CONF / 'node.crt')}")
    except (OSError, ValueError) as exc:
        warn(f"certificato non leggibile: {exc}")

    print(f"  Abbinamento             {pairing_line()}")
    lines = keys_lines()
    print(f"  Chiavi                  {lines[0] if lines else 'nessuna: il nodo non è abbinato a nessun Mirto'}")
    for line in lines[1:]:
        print(f"                          {line}")
    cattivi = file_non_del_servizio(CONF, uid_servizio())
    if cattivi:
        print()
        warn(f"{len(cattivi)} file non sono dell'utente del servizio ({fs.UTENTE}): "
             f"{', '.join(cattivi[:6])}{'…' if len(cattivi) > 6 else ''}")
        print("  Questo «stato» gira da root e li legge; il servizio no, quindi si comporta come se")
        print("  non esistessero: abbinamento che risulta chiuso, registrazione che non parte.")
        print(f"  Si risolve con:  sudo chown -R {fs.UTENTE}:{fs.UTENTE} {CONF}")

    print("\nServizi")
    for s in ("mirto-node", "mediamtx", "bluetooth", "avahi-daemon"):
        print(f"  {s:<22}  {active(s)}")

    cam = tools.camera_cfg(cfg)
    print("\nCamera")
    if not cam["attiva"]:
        print("  spenta (mirto-nodo camera per accenderla)")
    else:
        try:
            socket.create_connection(("127.0.0.1", 8554), timeout=2).close()
            live = "trasmette su rtsp://%s.local:8554/cam" % host
        except OSError:
            live = "NON trasmette (porta 8554 chiusa)"
        print(f"  {cam['larghezza']}×{cam['altezza']} · {cam['fps']} fps{' · NoIR' if cam['noir'] else ''} · {live}")
        sensore = tools.detect_sensor()
        if sensore:
            print(f"  Sensore                 {sensore}")
        else:
            warn(tools.CAMERA_NON_VISTA)
            print("\n".join("    " + riga for riga in tools.CAMERA_NON_VISTA_RIMEDIO.splitlines()))

    print("\nRegistrazione")
    for line in recording_lines(cfg):
        print(f"  {line}")

    a = cfg.get("audio") or {}
    out = a.get("uscita", "bluetooth")
    print("\nAudio (sirena)")
    print(f"  Uscita                  {OUTPUT_LABEL.get(out, out)}"
          + (f" · scheda {a['scheda']}" if a.get("scheda") else ""))
    if out == "bluetooth":
        mac = a.get("bluetooth_mac", "")
        if not mac:
            warn("nessuna cassa abbinata: mirto-nodo audio")
        else:
            info = sh(["bluetoothctl", "info", mac], timeout=10).stdout
            name = re.search(r"Name:\s*(.+)", info)
            conn = "collegata" if re.search(r"Connected:\s*yes", info) else "NON collegata (spenta o lontana?)"
            print(f"  Cassa                   {name.group(1).strip() if name else mac} · {conn}")
    elif a.get("scheda"):
        present = any(c["id"] == a["scheda"] for c in alsa_cards())
        print(f"  Scheda                  {'presente' if present else 'NON trovata (scollegata?)'}")

    print("\nRete")
    ips = sh(["hostname", "-I"]).stdout.split()
    print(f"  Indirizzi               {', '.join(ips) or 'nessuno'}")
    stampa(f"  Collegato               {in_parole(stato_rete())}")
    ssid = sh(["iwgetid", "-r"]).stdout.strip()
    if not ssid:
        nets = parse_wifi_list(sh(["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "dev", "wifi", "list",
                                   "--rescan", "no"]).stdout)
        ssid = next((n["ssid"] for n in nets if n["in_uso"]), "")
    print(f"  Wi-Fi                   {ssid or 'non collegato'}")
    try:
        temp = int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000
        print(f"  Temperatura             {temp:.0f} °C{'  (alta!)' if temp >= 75 else ''}")
    except (OSError, ValueError):
        pass
    print()
    return 0


RETI_TROVATE = "reti-trovate.json"           # l'ultima ricerca di reti Wi-Fi, per Mirto
WIFI_DA_COLLEGARE = "wifi-da-collegare.json"  # rete e password scelte in Mirto (0600, si cancella subito)
WIFI_ESITO = "wifi-esito.json"               # com'è andato l'ultimo collegamento


def _esito_wifi(azione: str, esito: str, messaggio: str = "") -> None:
    fs.scrivi(CONF / WIFI_ESITO, json.dumps({"quando": int(time.time()), "azione": azione,
                                             "esito": esito, "messaggio": messaggio[:300]}) + "\n")


def _prepara_wifi() -> bool:
    """Radio accesa e paese impostato: senza, le reti non si vedono nemmeno."""
    if sh(["which", "nmcli"]).returncode != 0:
        _esito_wifi("cerca", "errore", "NetworkManager non c'è su questo nodo")
        return False
    sh(["rfkill", "unblock", "wifi"])
    if not sh(["raspi-config", "nonint", "get_wifi_country"]).stdout.strip():
        sh(["raspi-config", "nonint", "do_wifi_country", "IT"])
    return True


def _reti_viste() -> list[dict]:
    sh(["nmcli", "dev", "wifi", "rescan"], timeout=25)
    return parse_wifi_list(sh(["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "dev", "wifi", "list"]).stdout)


def _cerca_reti() -> int:
    """Cerca le reti Wi-Fi e scrive l'elenco dove Mirto lo possa leggere.

    Gira da root: la radio e il paese del Wi-Fi non si toccano senza privilegi. L'elenco è quello che
    vede **il nodo**, non il computer: è il dato che serve a sapere se da lì il segnale arriva.
    """
    if not _prepara_wifi():
        warn("nmcli non disponibile")
        return 1
    reti = _reti_viste()
    fs.scrivi(CONF / RETI_TROVATE, json.dumps({"quando": int(time.time()), "reti": reti}) + "\n")
    _esito_wifi("cerca", "ok", f"{len(reti)} reti")
    ok(f"{len(reti)} reti trovate")
    return 0


def _collega_wifi() -> int:
    """Collega il nodo alla rete scelta in Mirto. La password sta in un file 0600 che si cancella subito."""
    richiesta = CONF / WIFI_DA_COLLEGARE
    try:
        dati = json.loads(richiesta.read_text())
    except (OSError, ValueError):
        _esito_wifi("collega", "errore", "richiesta non leggibile")
        return 1
    finally:
        richiesta.unlink(missing_ok=True)      # la password non resta su disco più del necessario
    ssid, password = str(dati.get("ssid", "")), str(dati.get("password", ""))
    if not ssid or not _prepara_wifi():
        _esito_wifi("collega", "errore", "rete non indicata o NetworkManager assente")
        return 1
    cmd = ["nmcli", "dev", "wifi", "connect", ssid]
    if password:
        cmd += ["password", password]          # argomento, non riga di shell: niente da interpretare
    if dati.get("nascosta"):
        cmd += ["hidden", "yes"]
    res = sh(cmd, timeout=60)
    if res.returncode != 0:
        # il messaggio di nmcli non contiene la password, ma non si rischia: si manda solo il motivo noto
        motivo = "password sbagliata o segnale troppo debole"
        if "No network with SSID" in (res.stdout + res.stderr):
            motivo = "rete non trovata: è accesa e a portata?"
        _esito_wifi("collega", "errore", motivo)
        warn(f"collegamento non riuscito: {motivo}")
        return 1
    indirizzi = sh(["hostname", "-I"]).stdout.split()
    _esito_wifi("collega", "ok", f"collegato a «{ssid}» · {', '.join(indirizzi)}")
    ok(f"collegato a «{ssid}»: {', '.join(indirizzi)}")
    return 0


def cmd_wifi(argv: list[str] | None = None) -> int:
    if argv and argv[0] == "--cerca":
        return _cerca_reti()
    if argv and argv[0] == "--collega":
        return _collega_wifi()
    if sh(["which", "nmcli"]).returncode != 0:
        print("NetworkManager (nmcli) non c'è: usa  sudo raspi-config → System Options → Wireless LAN.")
        return 1
    sh(["rfkill", "unblock", "wifi"])
    country = sh(["raspi-config", "nonint", "get_wifi_country"]).stdout.strip()
    if not country:
        sh(["raspi-config", "nonint", "do_wifi_country", "IT"])
        ok("paese del Wi-Fi impostato a IT")
    if os.environ.get("SSH_CONNECTION") and sh(["iwgetid", "-r"]).stdout.strip():
        warn("sei collegato in SSH: se cambi rete Wi-Fi la sessione può cadere. Col cavo è più sicuro.")
    print("Cerco le reti…")
    sh(["nmcli", "dev", "wifi", "rescan"], timeout=20)
    nets = parse_wifi_list(sh(["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "dev", "wifi", "list"]).stdout)
    if not nets:
        warn("nessuna rete trovata")
        return 1
    labels = [f"{n['ssid']:<28} segnale {n['segnale']:>3}%  {n['sicurezza']}{'  ← in uso' if n['in_uso'] else ''}"
              for n in nets]
    i = choose("Reti Wi-Fi", labels + ["(rete nascosta: scrivo il nome)"])
    if i is None:
        return 0
    ssid = nets[i]["ssid"] if i < len(nets) else ask("Nome della rete")
    if not ssid:
        return 0
    cmd = ["nmcli", "--ask", "dev", "wifi", "connect", ssid]
    if i >= len(nets):
        cmd += ["hidden", "yes"]
    print("La password la chiede il sistema e non viene mostrata.")
    res = subprocess.run(cmd)  # interattivo: nmcli chiede la password sul terminale
    if res.returncode != 0:
        warn("collegamento non riuscito (password sbagliata o segnale debole?)")
        return 1
    ok(f"collegato a «{ssid}»: {', '.join(sh(['hostname', '-I']).stdout.split())}")
    print("  Mirto ritrova il nodo da solo (nome .local), non serve cambiare nulla.")
    return 0


def _bt_setup() -> str:
    """Abbina una cassa Bluetooth; restituisce il suo indirizzo o ''."""
    sh(["rfkill", "unblock", "bluetooth"])
    sh(["systemctl", "start", "bluetooth"])
    sh(["bluetoothctl", "power", "on"])
    known = parse_bt_devices(sh(["bluetoothctl", "devices", "Paired"]).stdout) \
        or parse_bt_devices(sh(["bluetoothctl", "paired-devices"]).stdout)
    if known:
        i = choose("Casse già abbinate", [f"{n or '(senza nome)'}  {m}" for m, n in known] + ["Cerca una nuova cassa"])
        if i is None:
            return ""
        if i < len(known):
            return known[i][0]
    input("\nMetti la cassa in abbinamento (tasto Bluetooth finché lampeggia veloce), poi premi Invio… ")
    print("Cerco per 15 secondi…")
    sh(["bluetoothctl", "--timeout", "15", "scan", "on"], timeout=25)
    found = [d for d in parse_bt_devices(sh(["bluetoothctl", "devices"]).stdout) if d[1]]
    if not found:
        warn("nessuna cassa trovata: è accesa e in abbinamento?")
        return ""
    i = choose("Dispositivi trovati", [f"{n:<28} {m}" for m, n in found])
    if i is None:
        return ""
    mac, name = found[i]
    for step in ("pair", "trust", "connect"):
        res = sh(["bluetoothctl", step, mac], timeout=30)
        good = res.returncode == 0 or "AlreadyExists" in res.stdout + res.stderr
        if not good and step != "pair":
            warn(f"{step}: {(res.stdout + res.stderr).strip().splitlines()[-1:] or ['errore']}")
            return ""
    ok(f"«{name}» abbinata e collegata")
    return mac


CASSE_TROVATE = "casse-trovate.json"     # l'ultima ricerca di casse Bluetooth, per Mirto
AUDIO_ESITO = "audio-esito.json"         # com'è andata l'ultima cosa chiesta da Mirto


def _esito_audio(azione: str, esito: str, messaggio: str = "") -> None:
    """Lascia detto com'è andata: Mirto non vede il terminale, e un'attesa senza risposta è un bug."""
    fs.scrivi(CONF / AUDIO_ESITO, json.dumps({"quando": int(time.time()), "azione": azione,
                                              "esito": esito, "messaggio": messaggio[:300]}) + "\n")


def _applica_audio() -> int:
    """Rende vera la sezione [audio] di node.toml. La chiama il menu e il servizio da root per Mirto."""
    cfg = read_cfg()
    a = cfg.get("audio") or {}
    uscita, scheda = a.get("uscita", "bluetooth"), a.get("scheda", "")
    if uscita == "jack" and scheda:
        # il Raspberry manda l'audio sul jack solo se glielo si chiede (altrimenti può preferire l'HDMI)
        sh(["amixer", "-c", scheda, "sset", "PCM", "90%", "unmute"])
    restart("mirto-node")
    _esito_audio("applica", "ok", f"uscita: {OUTPUT_LABEL.get(uscita, uscita)}")
    ok(f"uscita audio: {OUTPUT_LABEL.get(uscita, uscita)}" + (f" · {scheda}" if scheda else ""))
    return 0


def _casse_note() -> list[tuple[str, str]]:
    return (parse_bt_devices(sh(["bluetoothctl", "devices", "Paired"]).stdout)
            or parse_bt_devices(sh(["bluetoothctl", "paired-devices"]).stdout))


def _cerca_casse(secondi: int = 15) -> int:
    """Cerca casse Bluetooth e scrive l'elenco dove Mirto lo possa leggere.

    Gira da root perché l'abbinamento Bluetooth vuole privilegi, e lascia il risultato in un file del
    servizio: il pannello di Mirto lo chiede, non resta appeso a una connessione aperta per quindici
    secondi.
    """
    sh(["rfkill", "unblock", "bluetooth"])
    sh(["systemctl", "start", "bluetooth"])
    sh(["bluetoothctl", "power", "on"])
    note = {m for m, _n in _casse_note()}
    scansione = sh(["bluetoothctl", "--timeout", str(secondi), "scan", "on"], timeout=secondi + 15)
    visti: dict[str, str] = {}
    for m, n in casse_da_scansione(scansione.stdout + scansione.stderr):
        visti[m] = n                                   # quello che si è visto passare durante la ricerca
    for m, n in parse_bt_devices(sh(["bluetoothctl", "devices"]).stdout):
        if n:
            visti.setdefault(m, n)
    trovate = [{"mac": m, "nome": n, "abbinata": m in note} for m, n in visti.items()
               if pare_una_cassa(m, n, sh(["bluetoothctl", "info", m]).stdout)]
    for m, n in _casse_note():          # le già abbinate ci sono comunque, anche se non si annunciano
        if not any(c["mac"] == m for c in trovate):
            trovate.append({"mac": m, "nome": n or "(senza nome)", "abbinata": True})
    fs.scrivi(CONF / CASSE_TROVATE, json.dumps({"quando": int(time.time()), "casse": trovate}) + "\n")
    _esito_audio("cerca", "ok", f"{len(trovate)} casse")
    ok(f"{len(trovate)} casse trovate")
    return 0


# Quando una cassa rifiuta il collegamento con questo motivo, il legame Bluetooth e' morto da una delle
# due parti: succede mettendo la cassa in abbinamento, perche' molte casse sostituiscono la chiave del
# dispositivo precedente. Aspettare non serve a niente, e finora il rimedio era scritto nella guida e
# andava fatto a mano: bluetoothctl remove, poi di nuovo pair.
LEGAME_MORTO = "br-connection-unknown"
# La cassa si annuncia ma non risponde alla chiamata sul Bluetooth classico: «page timeout» a livello
# radio, e l2ping che dice «Host is down» su un dispositivo a mezzo metro con RSSI -42. Succede quando
# e' gia' agganciata a un telefono o a un computer: continua ad annunciarsi, ma non accetta nessun
# altro. Visto il 21/10 sera, con la JBL che il Mac si era ripresa da solo.
NON_RISPONDE = ("page-timeout", "AuthenticationTimeout", "status 0x04", "status 0x08")


def passi_abbinamento(mac: str) -> list[tuple[str, float]]:
    """La sequenza che collega davvero una cassa, e quanto aspettare dopo ogni comando.

    Provata il 21/10 su una JBL Flip 6, dopo che tutto il resto aveva fallito. Due cose, imparate li':

    * **una sola sessione di bluetoothctl.** Lanciando un comando per volta, ogni comando e' un
      processo diverso e fra l'uno e l'altro BlueZ butta via la cassa, che non e' abbinata e quindi per
      lui e' «temporanea»: il comando dopo risponde «Device … not available» su una cassa accesa e li'
      davanti. Dentro una sessione sola, con la ricerca attiva, la cassa resta in mano a BlueZ;
    * **il tempo.** Fra «scan on» e il momento in cui compare «[NEW] Device … JBL Flip 6» passano una
      ventina di secondi: chiedere prima e' chiedere a vuoto.

    L'abbinamento vero e proprio (``pair``) **non serve**: la cassa si e' collegata con ``Paired: no``
    e ``Bonded: no``. Si prova lo stesso, perche' certe casse lo vogliono, ma se fallisce non e' un
    guaio: quello che conta e' ``trust`` (cosi' il nodo la ricollega da solo) e ``connect``.

    I tempi sono **massimi**, non attese: dall'8/11 ogni passo va avanti appena la sua cosa e'
    successa. Prima erano fissi e si sommavano sempre — 52 secondi anche quando tutto riusciva al
    primo colpo. Marco, provando: *«la cassa ha emesso il suono tipico suo per dire che si e'
    collegata ma l'applicazione ha atteso un timeout di quasi 30 secondi»*. Quel suono e' il
    ``connect`` riuscito: il copione aveva ancora 13 secondi di attesa davanti, per niente.
    """
    return [("power on", 1, ""), ("agent NoInputNoOutput", 1, ""), ("default-agent", 1, ""),
            ("pairable on", 1, ""),
            # fin qui sono comandi che rispondono subito: il massimo e' gia' piccolo e non c'e'
            # niente di utile da aspettare.
            ("scan on", 16, rf"Device\s+{re.escape(mac)}"),   # la cassa si e' fatta vedere
            (f"pair {mac}", 16, r"(Pairing successful|Failed to pair|AlreadyExists)"),
            (f"trust {mac}", 3, r"(trust succeeded|Changing .* trust succeeded)"),
            (f"connect {mac}", 12, r"(Connection successful|Failed to connect)"),
            ("quit", 1, "")]


def motivo_da_sessione(testo: str) -> str:
    """L'ultima riga che spiega qualcosa, in mezzo al fiume di righe di una sessione di bluetoothctl."""
    pulito = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", testo or "")
    motivi = []
    for riga in pulito.splitlines():
        riga = re.sub(r"^.*?\[(?:bluetoothctl|[^\]]+)\]> *", "", riga).strip()
        if re.search(r"(Failed|not available|Error|refused|Authentication|Timeout)", riga, re.I):
            motivi.append(riga)
    return motivi[-1] if motivi else ""


def _sessione_bluetoothctl(passi: list[tuple[str, float, str]]) -> str:
    """Una sola sessione di bluetoothctl: i comandi entrano dallo stdin, uno quando l'altro ha finito.

    L'uscita va in un file e non in una pipe: durante la ricerca bluetoothctl scrive moltissimo, e una
    pipe piena bloccherebbe tutto a meta' abbinamento. Quel file serve anche a sapere **quando** un
    passo e' finito: si rilegge mentre si aspetta, e appena compare la riga che dice com'e' andata si
    passa al comando dopo. Il tempo scritto accanto al passo e' un massimo, non un'attesa.
    """
    with tempfile.NamedTemporaryFile("w+", prefix="mirto-bt-", suffix=".log") as registro:
        try:
            proc = subprocess.Popen(["bluetoothctl"], stdin=subprocess.PIPE, stdout=registro,
                                    stderr=subprocess.STDOUT, text=True)
        except FileNotFoundError:
            return "bluetoothctl: comando non trovato"
        try:
            for comando, massimo, segno in passi:
                proc.stdin.write(comando + "\n")
                proc.stdin.flush()
                _aspetta_il_segno(registro, segno, massimo)
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
        registro.seek(0)
        return registro.read()


def _aspetta_il_segno(registro, segno: str, massimo: float) -> None:
    """Aspetta che nel registro compaia ``segno``, al massimo ``massimo`` secondi.

    Senza ``segno`` aspetta e basta: sono i comandi che rispondono subito, dove non c'e' niente da
    sorvegliare. Si guarda ogni due decimi di secondo — bluetoothctl scrive molto e rileggere un file
    piccolo e' niente — e si esce appena la riga c'e'.
    """
    if not segno:
        time.sleep(massimo)
        return
    atteso = re.compile(segno, re.I)
    fine = time.monotonic() + massimo
    while time.monotonic() < fine:
        time.sleep(0.2)
        dove = registro.tell()
        try:
            registro.seek(0)
            if atteso.search(registro.read()):
                return
        finally:
            registro.seek(dove)


def vista_nella_ricerca(registro: str, mac: str) -> bool:
    """Durante la sessione la cassa si è fatta vedere? (``[NEW] Device AA:BB:… Nome``.)

    Cambia completamente cosa dire a chi guarda: una cassa che non si annuncia è spenta, lontana o
    ancora attaccata a un telefono; una cassa che si annuncia ma rifiuta il collegamento aspetta solo
    di essere messa in abbinamento.
    """
    return bool(re.search(r"Device\s+" + re.escape(mac.upper()), registro or "", re.I))


def _risveglia_bluetooth() -> None:
    """Rimette in sesto il controller Bluetooth del Raspberry.

    Provato sul Pi 4 il 21/10 sera, dopo un pomeriggio di tentativi falliti: il controller resta in uno
    stato in cui rifiuta tutto con errori generici (``br-connection-unknown``, ``page-timeout``) anche
    con la cassa resettata e a mezzo metro. Riavviato il servizio e azzerato ``hci0``, la stessa
    identica sequenza ha risposto «Pairing successful» al primo colpo. Costa cinque secondi dentro
    un'operazione che ne dura sessanta, e si fa solo quando il primo tentativo è andato male.
    """
    sh(["systemctl", "restart", "bluetooth"], timeout=30)
    time.sleep(3)
    for argomento in ("down", "reset", "up"):
        sh(["hciconfig", "hci0", argomento], timeout=15)
        time.sleep(1)
    sh(["bluetoothctl", "power", "on"], timeout=15)
    time.sleep(2)


def _abbinata(mac: str) -> bool:
    """La cassa ha un legame con questo nodo? (``Paired`` o ``Bonded``.)"""
    return bool(re.search(r"^\s*(Paired|Bonded):\s*yes", sh(["bluetoothctl", "info", mac]).stdout,
                          re.M | re.I))


def _collega_e_basta(mac: str, tentativi: int = 3) -> bool:
    """Solo il collegamento, ritentato: serve quando il legame c'è già.

    Una cassa appena abbinata chiude il collegamento da sé (``disconnected with reason 2``) e va
    richiamata: il 21/10 l'abbinamento era riuscito e il nodo si fermava lì, a un passo dalla fine.
    """
    for _ in range(tentativi):
        sh(["bluetoothctl", "connect", mac], timeout=25)
        if _collegata(mac):
            return True
        time.sleep(3)
    return False


def _collegata(mac: str) -> bool:
    return bool(re.search(r"^\s*Connected:\s*yes", sh(["bluetoothctl", "info", mac]).stdout, re.M | re.I))


def _abbina_cassa(mac: str) -> int:
    """Abbina, rende fidata e collega una cassa. L'esito lo legge Mirto."""
    if not re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", (mac or "").upper()):
        _esito_audio("abbina", "errore", "indirizzo della cassa non valido")
        warn("indirizzo non valido")
        return 1
    mac = mac.upper()
    sh(["rfkill", "unblock", "bluetooth"])
    sh(["systemctl", "start", "bluetooth"])
    registro = _sessione_bluetoothctl(passi_abbinamento(mac))
    riuscito, motivo = _collegata(mac), motivo_da_sessione(registro)
    if not riuscito and _abbinata(mac):
        # Il legame c'è: manca solo il collegamento, che una cassa appena abbinata chiude da sé.
        riuscito = _collega_e_basta(mac)
    if not riuscito:
        if LEGAME_MORTO in registro:
            # Se la cassa fosse soltanto spenta, buttare il legame peggiorerebbe le cose: si perderebbe
            # un abbinamento che funziona per un difetto che non c'e'.
            warn("la cassa non riconosce più questo nodo: rifaccio l'abbinamento da zero")
            sh(["bluetoothctl", "remove", mac], timeout=20)
        warn("sveglio il Bluetooth del Raspberry e riprovo")
        _risveglia_bluetooth()
        # I due registri si sommano: il motivo da dire è l'ultimo, ma quello che si è visto passare
        # vale anche se è successo nel primo tentativo.
        registro += "\n" + _sessione_bluetoothctl(passi_abbinamento(mac))
        riuscito, motivo = _collegata(mac), motivo_da_sessione(registro)
        if not riuscito and _abbinata(mac):
            riuscito = _collega_e_basta(mac)
    if not riuscito:
        # Il motivo per primo: il messaggio si taglia a 300 caratteri, e la parte che serve a capire
        # cos'è successo non deve essere quella che cade (21/10). E il rimedio dipende da una cosa
        # sola: il nodo l'ha vista passare o no? Sono due situazioni diverse, con due gesti diversi.
        if any(segno in registro for segno in NON_RISPONDE):
            rimedio = ("la cassa si fa vedere ma non risponde alla chiamata: quasi sempre e' gia' "
                       "collegata a un telefono o a un computer (scollegala da li', o spegni il "
                       "Bluetooth su quel dispositivo), altrimenti e' troppo lontana")
        elif LEGAME_MORTO in registro:
            rimedio = ("la cassa ha dato il posto a un altro dispositivo (succede quando un telefono si "
                       "abbina: molte casse tengono una chiave sola). Tieni premuto il tasto Bluetooth "
                       "della cassa finché lampeggia veloce e, mentre lampeggia, premi «Abbina»")
        elif vista_nella_ricerca(registro, mac):
            rimedio = ("il nodo la vede ma lei rifiuta: mettila in abbinamento (tasto Bluetooth finché "
                       "lampeggia veloce) e riprova senza spegnerla")
        else:
            rimedio = ("il nodo non la vede proprio: accendila, avvicinala e mettila in abbinamento "
                       "(tasto Bluetooth finché lampeggia veloce); se è collegata a un telefono, "
                       "scollegala da lì")
        _esito_audio("abbina", "errore", f"non si collega: {motivo} · {rimedio}")
        warn(f"collegamento non riuscito: {motivo}")
        warn(rimedio)
        return 1
    write_cfg("audio", {"uscita": "bluetooth", "scheda": "", "bluetooth_mac": mac})
    restart("mirto-node")
    _esito_audio("abbina", "ok", f"cassa {mac} abbinata e collegata")
    ok(f"cassa {mac} abbinata")
    return 0


def _dimentica_cassa(mac: str) -> int:
    """Toglie l'abbinamento di una cassa: l'elenco lo tiene BlueZ, e toglierne una vuole privilegi.

    Si disconnette prima di rimuovere, perche' una cassa collegata che sparisce dall'elenco resta
    attaccata finche' qualcuno non la spegne, e il nodo continuerebbe a mandarle audio.
    """
    mac = (mac or "").upper()
    if not re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", mac):
        _esito_audio("dimentica", "errore", "indirizzo della cassa non valido")
        warn("indirizzo non valido")
        return 1
    sh(["bluetoothctl", "disconnect", mac], timeout=15)
    res = sh(["bluetoothctl", "remove", mac], timeout=15)
    uscito = (res.stdout or "") + (res.stderr or "")
    riuscito = "not available" not in uscito.lower()
    _esito_audio("dimentica", "ok" if riuscito else "errore",
                 "cassa dimenticata" if riuscito else "questa cassa il nodo non la conosceva")
    (ok if riuscito else warn)(f"{mac}: " + ("dimenticata" if riuscito else "non era abbinata"))
    return 0 if riuscito else 1


def cmd_audio(argv: list[str] | None = None) -> int:
    if argv and argv[0] == "--applica":
        return _applica_audio()
    if argv and argv[0] == "--cerca":
        return _cerca_casse()
    if argv and argv[0] == "--abbina":
        return _abbina_cassa(argv[1] if len(argv) > 1 else "")
    if argv and argv[0] == "--dimentica":
        return _dimentica_cassa(argv[1] if len(argv) > 1 else "")
    cfg = read_cfg()
    a = cfg.get("audio") or {}
    print(f"Uscita attuale: {OUTPUT_LABEL.get(a.get('uscita', 'bluetooth'))}"
          + (f" · {a['scheda']}" if a.get("scheda") else "") + (f" · {a['bluetooth_mac']}" if a.get("bluetooth_mac")
                                                                 and a.get("uscita") == "bluetooth" else ""))
    choices = audio_choices(alsa_cards())
    i = choose("Dove deve suonare la sirena?", [c["etichetta"] for c in choices])
    if i is None:
        return 0
    pick = choices[i]
    values = {"uscita": pick["uscita"], "scheda": pick["scheda"]}
    if pick["uscita"] == "bluetooth":
        mac = _bt_setup()
        if not mac:
            return 1
        values["bluetooth_mac"] = mac
    elif pick["uscita"] == "jack":
        # il Raspberry manda l'audio sul jack solo se glielo si chiede (altrimenti può preferire l'HDMI)
        sh(["amixer", "-c", pick["scheda"], "sset", "PCM", "90%", "unmute"])
    write_cfg("audio", values)
    ok(f"uscita salvata in {TOML}")
    if pick["uscita"] == "jack" and pick["scheda"]:
        pass   # l'amixer l'ha già fatto qui sopra: _applica_audio lo rifà quando la richiesta arriva da Mirto
    player = AudioPlayer(output=values["uscita"], bt_mac=values.get("bluetooth_mac", a.get("bluetooth_mac", "")),
                         card=values["scheda"])
    sh(["systemctl", "stop", "mirto-node"], timeout=30)  # libera l'uscita audio per la prova
    try:
        if yes("Faccio un suono di prova (din-don, volume basso)?"):
            try:
                player.loop(chime_pcm(), 1.0)
                player._thread.join(10)
                if player.last_error:
                    raise AudioError(player.last_error)
                if not yes("Hai sentito il din-don?"):
                    warn("controlla volume e cavo della cassa; per il jack: sudo alsamixer")
            except AudioError as exc:
                warn(f"suono non riuscito: {exc}")
    finally:
        player.close()
        restart("mirto-node")
    return 0


def _write_mediamtx(cfg: dict) -> None:
    sensor = tools.detect_sensor()
    cam = tools.camera_cfg(cfg)
    tuning = tools.tuning_file(sensor, bool(cam["noir"]), tools.board_model())
    user, pwd = tools.read_credentials(CONF / "rtsp")
    tmp = MTX_DIR / "mediamtx.yml.new"
    tmp.write_text(tools.mediamtx_for(cfg, user, pwd, tuning, CONF))
    sh(["chown", "root:mediamtx", str(tmp)])
    tmp.chmod(0o640)
    tmp.replace(MTX_DIR / "mediamtx.yml")


def _applica_camera() -> int:
    """Rende vera la sezione [camera] di node.toml: MediaMTX, annuncio in rete, servizi.

    Serve a due padroni: il menu qui sotto e il wizard di Mirto, che scrive node.toml attraverso il
    servizio e poi lascia a root il biglietto «camera-da-applicare» — perché toccare /opt/mediamtx e
    riavviare i servizi non è cosa che possa fare un servizio senza privilegi (S-03).
    """
    cfg = read_cfg()
    cam = tools.camera_cfg(cfg)
    if not cam["attiva"]:
        sh(["systemctl", "disable", "--now", "mediamtx"])
        CAM_AVAHI.unlink(missing_ok=True)
        sh(["systemctl", "reload", "avahi-daemon"])
        ok("camera spenta")
        restart("mirto-node")
        return 0
    try:
        _write_mediamtx(cfg)
    except ValueError as exc:
        warn(str(exc))
        return 1
    CAM_AVAHI.write_text(tools.CAM_AVAHI)
    sh(["systemctl", "reload", "avahi-daemon"])
    sh(["systemctl", "enable", "mediamtx"])
    restart("mediamtx")
    restart("mirto-node")
    ok(f"camera {cam['larghezza']}×{cam['altezza']} · {cam['fps']} fps{' · NoIR' if cam['noir'] else ''}")
    return 0


def cmd_camera(argv: list[str] | None = None) -> int:
    if argv and argv[0] == "--applica":      # senza domande: lo lancia il servizio da root
        return _applica_camera()
    cfg = read_cfg()
    if not (MTX_DIR / "mediamtx").exists():
        print("MediaMTX non è installato: rilancia  sudo ./node/install.sh  con la camera attiva in node.toml.")
        return 1
    cam = tools.camera_cfg(cfg)
    sh(["systemctl", "stop", "mediamtx"], timeout=30)  # la camera si riconosce solo se nessuno la usa
    sensor = tools.detect_sensor()
    print(f"Camera collegata: {sensor or 'nessuna rilevata (cavo piatto ben inserito?)'}")
    i = choose("Cosa vuoi fare?", ["Accendere / regolare la camera", "Spegnere la camera (il nodo resta per le azioni)"],
               0)
    if i is None:
        if cam["attiva"]:
            sh(["systemctl", "start", "mediamtx"])
        return 0
    if i == 1:
        write_cfg("camera", {"attiva": False})
        return _applica_camera()
    # «NoIR» non vuol dire «con visione notturna», e chiederlo così non aiuta nessuno: il modello NoIR
    # serve solo a chi ha faretti a infrarossi. Il sensore è identico nei due modelli (imx219 in entrambi),
    # quindi il nodo non può indovinarlo: la domanda deve restare, ma dire quando si risponde sì.
    print("\nNoIR vuol dire «senza filtro infrarossi»: è un modello diverso della stessa camera, e serve")
    print("solo se hai dei faretti a infrarossi che illuminano la scena — è così che si vede di notte.")
    print("Senza quei faretti, nel buio non vede niente nemmeno la NoIR, e di giorno dà i colori rosati.")
    print("Il sensore è lo stesso nei due modelli, quindi non posso capirlo da qui: lo dice la scatola")
    print("(«NoIR» nel nome) o l'obiettivo, che nella NoIR non ha il vetrino verdino.")
    print("Se hai una Camera Module normale, o non sai cosa rispondere, la risposta è no.")
    noir = yes("È il modello NoIR?", bool(cam["noir"]))
    cur = next((k for k, r in enumerate(RESOLUTIONS) if (r[1], r[2], r[3]) ==
                (cam["larghezza"], cam["altezza"], cam["fps"])), 1)
    r = choose("Risoluzione", [r[0] for r in RESOLUTIONS], cur)
    if r is None:
        r = cur
    _, w, h, fps = RESOLUTIONS[r]
    write_cfg("camera", {"attiva": True, "noir": noir, "larghezza": w, "altezza": h, "fps": fps})
    esito = _applica_camera()
    if esito == 0:
        print("Mirto si aggiorna da solo entro pochi minuti.")
    return esito


def _pairing():
    from .server import PairingWindow  # noqa: PLC0415

    # niente file di avvio qui: ad aprire la finestra ci pensa questo comando, non la lettura dello stato
    return PairingWindow(CONF, boot_flag=Path("/nonexistent"))


def _keys():
    from .keys import KeyStore  # noqa: PLC0415

    return KeyStore(CONF)


def uid_servizio(utente: str = fs.UTENTE) -> int | None:
    """L'uid dell'utente del servizio, o None se non esiste (prove sul Mac, nodi vecchi da root)."""
    try:
        import pwd  # noqa: PLC0415

        return pwd.getpwnam(utente).pw_uid
    except (ImportError, KeyError):
        return None


def file_non_del_servizio(folder: Path, uid: int | None, stat=os.stat) -> list[str]:
    """I file di stato che il servizio non riuscirebbe a leggere, perché non sono suoi.

    «mirto-nodo» gira da root e il servizio no: un file scritto da root con permessi 0600 è
    invisibile al servizio. Dall'esterno tutto sembra a posto — «stato» legge da root e vede
    tutto — mentre il servizio risponde «abbinamento chiuso» o non accende la registrazione.
    Questo controllo rende visibile proprio quella differenza.
    """
    if uid is None:
        return []
    cattivi = []
    for percorso in sorted(folder.rglob("*")):
        if percorso.is_dir():
            continue
        try:
            st = stat(percorso)
        except OSError:
            continue
        if st.st_uid == uid or st.st_mode & 0o044:   # suo, oppure leggibile da chiunque
            continue
        cattivi.append(str(percorso.relative_to(folder)))
    return cattivi


def pairing_line() -> str:
    """Riga di stato dell'abbinamento: aperto (con PIN), abbinato da chi, oppure chiuso."""
    w = _pairing()
    left = w.remaining()
    if left != 0:
        pin = w.pin()
        quanto = ("APERTO finché non viene abbinato" if left == w.SENZA_SCADENZA
                  else f"APERTO ancora {left // 60} min {left % 60:02d} s")
        return (f"{quanto} · PIN {pin or '(scaduto)'}"
                "  —  in Mirto compare \"Nuovo nodo trovato\"")
    if w.paired():
        import time  # noqa: PLC0415

        d = w.paired_info()
        when = time.strftime("%d/%m/%Y %H:%M", time.localtime(d["quando"])) if d.get("quando") else ""
        who = ", ".join(x for x in (d.get("da", ""), when) if x)
        return f"abbinato ({who})" if who else "abbinato"
    return "chiuso, mai abbinato: mirto-nodo abbina per riaprirlo"


def install_key_line() -> str:
    """Riga sulla chiave dell'installazione: è condivisa, va ritirata, e da sola scade."""
    store = _keys()
    if not store.install_key_hex():
        return ""
    left = store.retire_in()
    when = (f"si ritira da sola tra {left // 86400} g {left % 86400 // 3600} h" if left
            else "si ritira da sola 7 giorni dopo il primo abbinamento con chiave propria")
    return ("chiave dell'installazione · CONDIVISA, DA RITIRARE "
            f"({when}) → mirto-nodo abbina --revoca installazione")


def keys_lines() -> list[str]:
    """Una riga per ogni chiave che il nodo accetta: chi, da dove, quando."""
    import time  # noqa: PLC0415

    store = _keys()
    lines = []
    for e in store.entries():
        when = time.strftime("%d/%m/%Y %H:%M", time.localtime(e["quando"])) if e["quando"] else "data sconosciuta"
        lines.append(f"{e['cliente']} · {e['indirizzo'] or 'indirizzo sconosciuto'} · {when}   [{e['nome']}]")
    old = install_key_line()
    if old:
        lines.append(old)
    return lines


def cmd_abbina(argv: list[str] | None = None) -> int:
    argv = list(argv or [])
    host = socket.gethostname()
    w = _pairing()
    store = _keys()
    opt = argv[0].lstrip("-") if argv else ""

    if opt in ("elenco", "chiavi"):
        lines = keys_lines()
        print("\nMirto abbinati a questo nodo:")
        if not lines:
            print("  (nessuno: il nodo non risponde a nessun Mirto finché non lo abbini)")
        for line in lines:
            print(f"  • {line}")
        print("\nSe non riconosci un Mirto, staccalo:  mirto-nodo abbina --revoca <nome>")
        print("Per staccarli tutti:  mirto-nodo abbina --revoca-tutti\n")
        return 0

    if opt == "revoca":
        name = " ".join(argv[1:]).strip() or ask("Quale abbinamento revoco? (nome dall'elenco)")
        if not name:
            warn("niente da revocare")
            return 1
        gone = store.revoke(name)
        if not gone:
            warn(f"nessun abbinamento «{name}»: guarda  mirto-nodo abbina --elenco")
            return 1
        ok(f"«{name}» revocato: quel Mirto dovrà rifare l'abbinamento")
        if not store.files() and not store.install_key_hex():
            (CONF / "abbinato").unlink(missing_ok=True)
        return 0

    if opt in ("revoca-tutti", "revoca-tutte"):
        print("Stacca TUTTI i Mirto abbinati a questo nodo e ritira la chiave dell'installazione:")
        print("dovranno rifare l'abbinamento, uno per uno, con il PIN.")
        if not yes("Procedo?", False):
            print("Annullato: non ho toccato nulla.")
            return 0
        revoked = store.revoke_all()
        (CONF / "abbinato").unlink(missing_ok=True)
        ok(f"{revoked} chiav{'e revocata' if revoked == 1 else 'i revocate'}")
    elif opt:
        warn(f"opzione sconosciuta: {argv[0]}")
        print(__doc__)
        return 2

    cfg = read_cfg()
    pin, fresh = w.open_or_current()
    if fresh and w.senza_scadenza():
        ok("abbinamento aperto: non scade finché il nodo non viene abbinato la prima volta")
    elif fresh:
        ok(f"abbinamento aperto per {w.seconds // 60} minuti (si chiude da solo al primo abbinamento)")
    else:
        left = w.remaining()
        ok("abbinamento già aperto (non scade finché il nodo non viene abbinato), PIN invariato"
           if left == w.SENZA_SCADENZA
           else f"abbinamento già aperto: restano {left // 60} min {left % 60:02d} s, PIN invariato")
    print("In Mirto compare \"Nuovo nodo trovato\" → Abbina (oppure Impostazioni → Nodi → Cerca in rete).")
    print(f"\n  PIN da scrivere in Mirto   {pin}\n")
    print("Controlla che l'impronta mostrata da Mirto sia questa:")
    print(f"  Indirizzo   https://{host}.local:{cfg.get('porta', 8443)}")
    try:
        from .__main__ import fingerprint  # noqa: PLC0415
        print(f"  Impronta    {fingerprint(CONF / 'node.crt')}")
    except (OSError, ValueError):
        pass
    print("\nIl PIN vale solo per questo abbinamento: Mirto riceve una chiave sua, diversa da quella")
    print("degli altri. Non serve mai copiare la chiave a mano.")
    old = install_key_line()
    if old:
        print(f"\nATTENZIONE: {old}")
    # La chiave a mano serve solo a un Mirto che non sa usare il PIN (versione vecchia). È una
    # chiave permanente: resta valida finché qualcuno non la revoca, e chi la legge comanda il nodo.
    # Prima bastava un «s» e ne nasceva una, sempre con lo stesso nome: in un pomeriggio di prove se
    # ne accumulano quattro e nell'elenco non si capisce più quale serve.
    print("\nQuasi sempre basta il PIN qui sopra, e non serve altro.")
    if yes("Ti serve invece una chiave da copiare a mano? (crea una chiave PERMANENTE sul nodo)", False):
        nome = ask("Per quale Mirto? (un nome che riconoscerai nell'elenco)", "copiata a mano")
        chiave = store.create(nome, "copiata sul nodo")
        print(f"\n  Chiave      {chiave}")
        print("  Non condividerla: chi la legge comanda il nodo.")
        from .keys import slug  # noqa: PLC0415

        print(f"  Resta valida finché non la togli con:  mirto-nodo abbina --revoca {slug(nome)}")
        print("  Per vedere tutte le chiavi:  mirto-nodo abbina --elenco")
    return 0


host_slug = tools.host_slug          # definita in install_tools: la usa anche il servizio


def cmd_nome(argv: list[str] | None = None) -> int:
    """Il nome con cui si chiama il nodo. È un'etichetta: il nome in rete non si tocca.

    Fino al 21/10 cambiava anche l'hostname, e in mezza giornata di prove quella eleganza ha rotto due
    volte le chiavi SSH salvate sul Mac, ha fatto collidere un nome riusato con un altro Raspberry e ha
    costretto Mirto a riscrivere l'indirizzo salvato. Il nome in rete resta mirto-node-XXXX.
    """
    cfg = read_cfg()
    name = " ".join(argv or []).strip() or ask("Nuovo nome del nodo (es. Garage)", cfg.get("nome", ""))
    slug = host_slug(name)
    if not slug:
        warn("nome non valido")
        return 1
    rete = socket.gethostname()
    print(f"Nome del nodo: {name}")
    print(f"Nome in rete:  {rete}.local  (non cambia: è l'identità della macchina)")
    if not yes("Procedo?", True):
        return 0
    write_cfg(None, {"nome": name})
    restart("mirto-node")
    ok(f"ora si chiama «{name}» · in rete resta {rete}.local")
    return 0


def cmd_prepara_immagine() -> int:
    script = Path("/opt/mirto-node/prepare_image.sh")
    if not script.exists() or not Path("/opt/mirto-node/firstboot.sh").exists():
        print("Manca il primo avvio dell'immagine: aggiorna prima il nodo con  sudo ./node/install.sh")
        return 1
    print("""
Prepara questo Raspberry per diventare l'IMMAGINE dei nodi Mirto. Verranno cancellati:
  • chiave, certificato, password della camera e abbinamento del nodo (Mirto dovrà riabbinarlo)
  • configurazione del nodo (uscita audio, nome) e casse Bluetooth abbinate
  • reti Wi-Fi salvate (al prossimo avvio serve il cavo di rete, poi  mirto-nodo wifi)
  • video registrati sulla microSD e montaggio del disco USB delle registrazioni (i video sul disco restano lì)
  • chiavi SSH del Raspberry, identità della macchina, registri e cronologia dei comandi
Restano: sistema, programmi, il tuo utente e la sua chiave SSH.
Alla fine il Raspberry SI SPEGNE. Copia la microSD sul Mac PRIMA di riaccenderlo: al primo avvio
prende un nome nuovo (mirto-node-XXXX), chiavi nuove e apre l'abbinamento, che non scade
finché il nodo non viene abbinato la prima volta.
Hai già un backup della scheda di adesso? Senza, non si torna indietro.""")
    if ask("\nPer procedere scrivi PREPARA") != "PREPARA":
        print("Annullato: non ho toccato nulla.")
        return 0
    res = sh(["systemd-run", "--unit=mirto-pulizia", "--collect", "--quiet", str(script)])
    if res.returncode != 0:
        warn(f"pulizia non avviata: {res.stderr.strip()}")
        return 1
    ok("pulizia avviata: tra circa un minuto il Raspberry si spegne (LED verde fermo)")
    print("  Resoconto nella partizione di avvio della microSD: mirto-pulizia.txt")
    return 0


# ---------------------------------------------------------------- registrazione e disco USB

REC_MOUNT = Path("/mnt/mirto-reg")


def _gb(n: float) -> str:
    return f"{n / 1e9:.1f} GB"


def recording_lines(cfg: dict) -> list[str]:
    """Righe di «mirto-nodo stato» sulla registrazione: dove, quanto spazio, da quando, se registra."""
    from .recording import (AUDIO_PATH, CAMERA_PATH, MediaMTXApi, list_segments,  # noqa: PLC0415
                            rec_folder, recording_cfg)

    rec = recording_cfg(cfg)
    if not rec["attiva"]:
        return ["spenta ([registrazione] attiva = false)"]
    disk = str(rec.get("disco") or "").strip()
    radice = rec_folder(rec)
    # Due cartelle: accendere l'audio cambia il percorso registrato, e gli spezzoni di prima restano
    # dove sono. Guardarne una sola vorrebbe dire dire «copre 2 ore» a un nodo che ne ha venti.
    cartelle = [radice / CAMERA_PATH, radice / AUDIO_PATH]
    folder = radice / (AUDIO_PATH if rec.get("audio") else CAMERA_PATH)
    lines = [f"{'Disco USB ' + disk if disk else 'microSD (2 GB ad anello; per di più: mirto-nodo disco)'}"]
    if disk and not os.path.ismount(disk):
        return lines + ["DISCO NON COLLEGATO: registrazione ferma finché non lo ricolleghi"]
    segs = sorted((s for d in cartelle if d.exists() for s in list_segments(d)),
                  key=lambda s: s["inizio"])
    used = sum(s["byte"] for s in segs)
    try:
        du = shutil.disk_usage(folder)
        lines.append(f"Spazio {_gb(used)} di video · {_gb(du.free)} liberi su {_gb(du.total)}")
    except OSError:
        lines.append(f"Spazio {_gb(used)} di video")
    if segs:
        hours = (segs[-1]["fine"] - segs[0]["inizio"]) / 3600
        since = time.strftime("%d/%m %H:%M", time.localtime(segs[0]["inizio"]))
        lines.append(f"Copre {hours:.1f} ore (dal {since})")
    # I limiti si dicono: senza, non c'e' modo di sapere se quelli scelti in Mirto sono arrivati
    # davvero quaggiu'. Il 21/10, provando l'anello, mancava proprio questa riga.
    tetto = float(rec.get("massimo_gb") or 0)
    lines.append(f"Tetto {_gb(tetto * 1e9) if tetto else 'nessuno'}"
                 f" · tiene libero il {rec.get('minimo_libero_percento', 10)}% del disco")
    # L'audio nelle registrazioni: spento di serie, e quando e' acceso va detto qui, perche' questa
    # e' la schermata che si guarda per sapere cosa sta davvero registrando il nodo.
    if rec.get("audio"):
        lines.append("Audio del microfono ACCESO nelle registrazioni"
                     + ("" if shutil.which("ffmpeg") else " — ma ffmpeg manca: si registra solo il video"))
    try:  # stessa password dell'API che usa il servizio del nodo
        creds = tools.read_credentials(CONF / "mtx-api")
    except (OSError, ValueError):
        creds = None
    api = MediaMTXApi(credentials=creds)
    state = api.recording()
    if state is None and api.last_status == 401:
        lines.append("MediaMTX rifiuta la password dell'API: rilancia  sudo ./node/install.sh")
    else:
        lines.append("Registra adesso" if state else "NON registra adesso" if state is False else
                     "MediaMTX non risponde (camera spenta?)")
    return lines


SYSTEM_MOUNTS = {"/", "/boot", "/boot/firmware", "[SWAP]", "/usr", "/var", "/home"}
LSBLK = ["lsblk", "-J", "-b", "-o", "NAME,PATH,SIZE,TYPE,FSTYPE,MOUNTPOINT,TRAN,MODEL,UUID"]


def _mounts(dev: dict) -> set[str]:
    out = {dev.get("mountpoint") or ""}
    for c in dev.get("children") or []:
        out |= _mounts(c)
    return out - {""}


def usb_candidates(lsblk_json: str) -> list[dict]:
    """Dischi e partizioni USB utilizzabili da ``lsblk -J -b -o NAME,PATH,SIZE,TYPE,FSTYPE,MOUNTPOINT,TRAN,MODEL,UUID``.

    Una voce per partizione (o per disco senza partizioni), con il disco a cui appartiene. Esclusi la microSD e
    **qualunque disco che contiene il sistema** (un Raspberry che parte da un SSD USB: /, /boot, swap)."""
    import json  # noqa: PLC0415

    out = []
    for dev in json.loads(lsblk_json or "{}").get("blockdevices", []):
        if dev.get("type") != "disk" or (dev.get("tran") or "") != "usb":
            continue
        if _mounts(dev) & SYSTEM_MOUNTS:
            continue  # disco di sistema: mai proposto
        parts = [c for c in dev.get("children") or [] if c.get("type") == "part"]
        siblings = [(c.get("path"), c.get("mountpoint") or "") for c in parts]
        for p in parts or [dev]:
            out.append({"path": p.get("path"), "disk": dev.get("path"), "size": int(p.get("size") or 0),
                        "fstype": p.get("fstype") or "", "uuid": p.get("uuid") or "",
                        "mount": p.get("mountpoint") or "", "model": (dev.get("model") or "").strip(),
                        "parts": siblings, "is_part": p is not dev})
    return out


def fstab_line(uuid: str, fstype: str, mount: Path = REC_MOUNT) -> str:
    """Riga di /etc/fstab: ``nofail`` così il Raspberry si avvia anche senza disco."""
    opts = "defaults,nofail,noatime,x-systemd.device-timeout=10"
    if fstype in ("exfat", "vfat"):
        opts += ",uid=mediamtx,gid=mediamtx,umask=0007"  # niente permessi Unix: si danno all'utente di MediaMTX
    return f"UUID={uuid} {tools.posix(mount)} {fstype} {opts} 0 {2 if fstype == 'ext4' else 0}"


def set_fstab(text: str, line: str, mount: Path = REC_MOUNT) -> str:
    """Toglie eventuali righe vecchie per lo stesso punto di montaggio e aggiunge quella nuova."""
    keep = [ln for ln in text.splitlines() if not (len(ln.split()) > 1 and ln.split()[1] == tools.posix(mount))]
    return "\n".join(keep + [line]) + "\n"


def write_atomic(path: Path, text: str) -> None:
    """Scrive un file di sistema (es. /etc/fstab) senza rischiare di lasciarlo a metà se manca la corrente."""
    tmp = path.with_name(path.name + ".mirto-new")
    with open(tmp, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    try:
        st = path.stat()
        os.chmod(tmp, st.st_mode & 0o7777)
        if hasattr(os, "chown"):  # non esiste su Windows (il nodo gira su Linux, ma i test girano ovunque)
            os.chown(tmp, st.st_uid, st.st_gid)
    except OSError:
        pass
    os.replace(tmp, path)


DISCHI_TROVATI = "dischi-trovati.json"   # l'ultima ricerca di dischi, per Mirto
DISCO_ESITO = "disco-esito.json"         # com'è andata l'ultima cosa chiesta da Mirto


def _esito_disco(azione: str, esito: str, messaggio: str = "") -> None:
    fs.scrivi(CONF / DISCO_ESITO, json.dumps({"quando": int(time.time()), "azione": azione,
                                              "esito": esito, "messaggio": messaggio[:300]}) + "\n")


def _dischi_visti() -> list[dict]:
    """I dischi USB utilizzabili, con lo spazio libero di quelli montati.

    Il disco da cui parte il sistema non è fra questi: lo esclude ``usb_candidates``. È la protezione
    che conta, perché da qui passa anche la richiesta di formattare che arriva da Mirto.
    """
    res = sh(LSBLK)
    dischi = usb_candidates(res.stdout) if res.returncode == 0 else []
    for d in dischi:
        d["etichetta"] = (f"{d['path']}  {_gb(d['size'])}  {d['fstype'] or 'non formattato'}  "
                          f"{d['model']}").strip()
        if d["mount"]:
            try:
                du = shutil.disk_usage(d["mount"])
                d["libero"], d["totale"] = du.free, du.total
            except OSError:
                pass
    return dischi


def _cerca_dischi() -> int:
    """Scrive l'elenco dei dischi dove Mirto lo possa leggere."""
    dischi = _dischi_visti()
    fs.scrivi(CONF / DISCHI_TROVATI, json.dumps({"quando": int(time.time()), "dischi": dischi}) + "\n")
    _esito_disco("cerca", "ok", f"{len(dischi)} dischi USB utilizzabili"
                 if dischi else "nessun disco USB: collega l'SSD o la chiavetta e riprova")
    ok(f"{len(dischi)} dischi trovati")
    return 0


def _formatta_disco(disk: str) -> dict | None:
    """Prepara in ext4 l'INTERO disco: cancella tutto. Chi chiama ha già chiesto la conferma.

    Ricontrolla da zero che sia un disco USB utilizzabile, anche quando la richiesta arriva da Mirto
    che l'aveva già controllato: è l'unico comando del nodo che distrugge dati, e un controllo in più
    costa un millesimo di secondo.
    """
    voce = next((c for c in _dischi_visti() if c["disk"] == disk), None)
    if voce is None:
        warn(f"{disk} non è un disco USB utilizzabile (quello da cui parte il sistema non si tocca)")
        return None
    if not int(voce.get("size") or 0):
        # Un lettore di schede senza scheda dentro è un disco da 0 byte: wipefs e mkfs morirebbero con
        # un errore incomprensibile. Trovato provando il pannello il 21/10.
        warn(f"{disk} è un lettore senza scheda dentro: infila la scheda, stacca e riattacca il lettore")
        _esito_disco("prepara", "errore", "lettore senza scheda: infila la scheda, "
                     "stacca e riattacca il lettore, poi cerca di nuovo i dischi")
        return None
    for path, mnt in voce["parts"] or [(voce["disk"], voce["mount"])]:
        if mnt and sh(["umount", mnt], timeout=60).returncode != 0:
            warn(f"{path} è in uso ({mnt}) e non si smonta: chiudi chi lo usa e riprova. Nessuna modifica.")
            return None
    for cmd in (["wipefs", "-a", disk], ["sfdisk", "--quiet", disk]):
        r = subprocess.run(cmd, input="type=83\n" if cmd[0] == "sfdisk" else None, capture_output=True,
                           text=True, timeout=60)
        if r.returncode != 0:
            warn(f"{cmd[0]}: {r.stderr.strip()}")
            return None
    sh(["partprobe", disk], timeout=30)
    sh(["udevadm", "settle", "--timeout=15"], timeout=20)
    part = ""
    for _try in range(10):  # la partizione nuova compare dopo qualche istante
        part = next((x["path"] for x in usb_candidates(sh(LSBLK).stdout)
                     if x["disk"] == disk and x["is_part"]), "")
        if part and Path(part).exists():
            break
        time.sleep(1)
    if not part or not Path(part).exists():
        warn("la partizione nuova non compare: scollega e ricollega il disco, poi riprova")
        return None
    r = sh(["mkfs.ext4", "-F", "-L", "mirto-reg", part], timeout=600)
    if r.returncode != 0:
        warn(f"formattazione non riuscita: {r.stderr.strip()}")
        return None
    ok(f"{part} formattato (ext4)")
    voce = dict(voce, path=part, fstype="ext4", mount="",
                uuid=sh(["blkid", "-s", "UUID", "-o", "value", part]).stdout.strip())
    return voce


def _usa_disco(c: dict) -> int:
    """Monta il disco scelto, lo mette in fstab e accende la registrazione lì."""
    if not c.get("uuid"):
        warn("identificativo del disco (UUID) non trovato: riprova dopo averlo ricollegato")
        return 1
    if c["mount"] and c["mount"] != str(REC_MOUNT):
        sh(["umount", c["mount"]])
    REC_MOUNT.mkdir(parents=True, exist_ok=True)
    write_atomic(Path("/etc/fstab"), set_fstab(Path("/etc/fstab").read_text(),
                                               fstab_line(c["uuid"], c["fstype"])))
    sh(["systemctl", "daemon-reload"])
    r = sh(["mount", str(REC_MOUNT)], timeout=60)
    if r.returncode != 0 and not os.path.ismount(REC_MOUNT):
        warn(f"montaggio non riuscito: {r.stderr.strip()}")
        return 1
    ok(f"disco montato in {REC_MOUNT} (si rimonta da solo all'avvio; senza disco il Raspberry parte lo stesso)")
    (REC_MOUNT / "mirto-reg" / "cam").mkdir(parents=True, exist_ok=True)
    sh(["chown", "-R", "mediamtx:mediamtx", str(REC_MOUNT / "mirto-reg")])
    write_cfg("registrazione", {"attiva": True, "disco": str(REC_MOUNT)})
    try:
        _write_mediamtx(read_cfg())
    except ValueError as exc:
        warn(str(exc))
        return 1
    restart("mediamtx")
    restart("mirto-node")
    ok("registrazione sul disco USB: i file più vecchi si cancellano da soli quando serve spazio")
    return 0


def _quanto_spazio() -> str:
    try:
        du = shutil.disk_usage(REC_MOUNT)
        return f"{_gb(du.free)} liberi su {_gb(du.total)}"
    except OSError:
        return "spazio non misurabile"


def _usa_dispositivo(dev: str) -> int:
    """``--usa /dev/sda1``: il disco è già formattato bene, va solo montato e acceso (per Mirto)."""
    voce = next((c for c in _dischi_visti() if c["path"] == dev), None)
    if voce is None:
        warn(f"{dev} non è fra i dischi USB utilizzabili")
        _esito_disco("usa", "errore", "disco non trovato: ricollegalo e rifai la ricerca")
        return 1
    if voce["fstype"] not in tools.FORMATI_BUONI:
        _esito_disco("usa", "errore", "il disco va preparato prima di usarlo")
        return 1
    esito = _usa_disco(voce)
    _esito_disco("usa", "ok" if esito == 0 else "errore",
                 f"registrazione su {dev} · {_quanto_spazio()}" if esito == 0
                 else "montaggio non riuscito: guarda  mirto-nodo stato")
    return esito


def _prepara_dispositivo(disk: str) -> int:
    """``--prepara /dev/sda``: formatta tutto il disco e poi lo usa (per Mirto). Cancella."""
    voce = _formatta_disco(disk)
    if voce is None:
        _esito_disco("prepara", "errore", "preparazione non riuscita: il disco è ancora come prima "
                                          "o scollegalo e ricollegalo")
        return 1
    esito = _usa_disco(voce)
    _esito_disco("prepara", "ok" if esito == 0 else "errore",
                 f"disco preparato (ext4) e registrazione avviata · {_quanto_spazio()}" if esito == 0
                 else "disco preparato, ma il montaggio non è riuscito: guarda  mirto-nodo stato")
    return esito


def _applica_disco() -> int:
    """``--applica``: i limiti nuovi di [registrazione]. MediaMTX e il servizio li leggono all'avvio."""
    try:
        _write_mediamtx(read_cfg())
    except ValueError as exc:
        warn(str(exc))
        _esito_disco("limiti", "errore", str(exc))
        return 1
    restart("mediamtx")
    restart("mirto-node")
    rec = read_cfg().get("registrazione") or {}
    _esito_disco("limiti", "ok", f"{'registrazione accesa' if rec.get('attiva', True) else 'registrazione spenta'}"
                 f" · tetto {rec.get('massimo_gb') or 'nessuno'} GB"
                 f" · {rec.get('minimo_libero_percento', 10)}% sempre libero")
    return 0


def cmd_disco(argv: list[str] | None = None) -> int:
    """Collega un SSD o una chiavetta USB per la registrazione (ad anello: non si riempie mai)."""
    if argv and argv[0] == "--cerca":
        return _cerca_dischi()
    if argv and argv[0] == "--usa":
        return _usa_dispositivo(argv[1] if len(argv) > 1 else "")
    if argv and argv[0] == "--prepara":
        return _prepara_dispositivo(argv[1] if len(argv) > 1 else "")
    if argv and argv[0] == "--applica":
        return _applica_disco()
    cands = _dischi_visti()
    if not cands:
        warn("nessun disco USB utilizzabile trovato (quello da cui parte il sistema non viene mai proposto): "
             "collega l'SSD o la chiavetta e riprova")
        return 1
    labels = [c["etichetta"] + (f"  (montato in {c['mount']})" if c["mount"] else "") for c in cands]
    i = choose("Su quale disco registrare?", labels)  # nessuna scelta predefinita: va scelto apposta
    if i is None:
        return 0
    c = cands[i]
    if c["fstype"] not in tools.FORMATI_BUONI:
        print(f"\n{c['path']} non ha un formato utilizzabile ({c['fstype'] or 'nessuno'}).")
        print(f"Si può preparare (formato ext4) l'INTERO disco {c['disk']} ({c['model'] or 'USB'}). Contiene:")
        for path, mnt in c["parts"] or [(c["disk"], c["mount"])]:
            print(f"    {path}" + (f"  montato in {mnt}" if mnt else ""))
        print(f"ATTENZIONE: TUTTO IL CONTENUTO DI {c['disk']} VERRÀ CANCELLATO.")
        if ask("Scrivi CANCELLA per continuare") != "CANCELLA":
            print("Annullato: nessuna modifica.")
            return 0
        c = _formatta_disco(c["disk"])
        if c is None:
            return 1
    elif c["fstype"] != "ext4":
        warn(f"formato {c['fstype']}: va bene, ma ext4 resiste meglio allo spegnimento improvviso")
    return _usa_disco(c)


COMMANDS = {
    "stato": (cmd_stato, "come sta il nodo"),
    "wifi": (cmd_wifi, "collega a una rete Wi-Fi"),
    "audio": (cmd_audio, "uscita della sirena (Bluetooth, jack, USB, HDMI)"),
    "camera": (cmd_camera, "camera normale o NoIR, risoluzione, accesa/spenta"),
    "abbina": (cmd_abbina, "abbinamento: apre e mostra il PIN · --elenco · --revoca NOME · --revoca-tutti"),
    "nome": (cmd_nome, "cambia l'etichetta del nodo (il nome in rete non cambia)"),
    "disco": (cmd_disco, "SSD o chiavetta USB per la registrazione (ad anello)"),
    "prepara-immagine": (cmd_prepara_immagine, "pulisce il nodo per farne l'immagine (poi lo spegne)"),
}

# Comandi che accettano opzioni dopo il nome: «--applica», «--cerca»… Sono le stesse cose che chiede
# Mirto passando dal servizio da root, più qualche scorciatoia a mano (mirto-nodo nome Garage).
CON_ARGOMENTI = {"nome", "abbina", "camera", "audio", "wifi", "disco"}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("-h", "--help", "aiuto"):
        print(__doc__)
        return 0
    if os.geteuid() != 0:
        print("Serve l'amministratore: sudo mirto-nodo …")
        return 1
    if argv:
        if argv[0] not in COMMANDS:
            print(f"Comando sconosciuto: {argv[0]}\n{__doc__}")
            return 2
        if argv[0] in CON_ARGOMENTI:
            return COMMANDS[argv[0]][0](argv[1:])
        return COMMANDS[argv[0]][0]()
    while True:
        names = list(COMMANDS)
        i = choose("Mirto Nodo — cosa vuoi fare?", [f"{n:<17} {COMMANDS[n][1]}" for n in names])
        if i is None:
            return 0
        try:
            COMMANDS[names[i]][0]()
        except KeyboardInterrupt:
            print("\n(interrotto)")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        sys.exit(130)
