#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Chi c'è in rete che somigli a una camera, e **come** si fa sentire.

Mirto trova le camere di rete ascoltando un solo tipo di annuncio: `_rtsp._tcp` (mDNS). È quello
che usa il nostro nodo, ed è il motivo per cui le sue camere compaiono da sole. Una cam comprata in
negozio quasi mai si annuncia così: parla **ONVIF**, cioè risponde a un messaggio in multicast
(WS-Discovery) invece di gridare il proprio nome. Due linguaggi diversi, e chi ascolta solo il primo
conclude che non ci sia nessuno.

Questa sonda chiede in tutt'e due i modi e stampa quello che sente, senza toccare niente e senza
salvare niente. Serve a rispondere a una domanda sola: *questa camera, in rete, si presenta? e come?*

    cd ~/Documents/Mirto && .venv/bin/python tools/cerca_camere_rete.py

Non fa scansioni della sottorete: non bussa a porte di indirizzi che nessuno ha nominato. Guarda solo
chi si è presentato da sé, più gli indirizzi che il computer ha già in tabella ARP se glielo chiedi
con `--vicini` — quelli sono dispositivi con cui questo computer ha già parlato, non una pesca a
strascico.
"""

from __future__ import annotations

import argparse
import re
import socket
import struct
import sys
import time

MULTICAST, PORTA_WSD = "239.255.255.250", 3702
PORTE_CAMERA = ((554, "RTSP"), (8554, "RTSP"), (80, "web"), (8080, "web"), (443, "web sicuro"))

# Il messaggio di WS-Discovery: «chi c'è che sia un dispositivo di rete?». Il tipo `NetworkVideoTransmitter`
# è quello che ONVIF dà alle camere; si chiede anche il tipo generico, perché alcune rispondono solo a
# quello e una camera che risponde «sono un dispositivo» è comunque una camera trovata.
PROBE = """<?xml version="1.0" encoding="UTF-8"?>
<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"
            xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing"
            xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
            xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
  <e:Header>
    <w:MessageID>uuid:{uuid}</w:MessageID>
    <w:To e:mustUnderstand="true">urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>
    <w:Action e:mustUnderstand="true">http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>
  </e:Header>
  <e:Body><d:Probe><d:Types>{tipo}</d:Types></d:Probe></e:Body>
</e:Envelope>"""


def _tag(testo: str, nome: str) -> str:
    """Il contenuto di un tag, qualunque prefisso di namespace abbia davanti.

    Si legge con un'espressione invece che con un parser XML di proposito: le risposte vere sono
    piene di prefissi diversi fra marca e marca, e qui serve sapere **dove** sta la camera, non
    convalidare un documento.
    """
    m = re.search(rf"<(?:\w+:)?{nome}[^>]*>(.*?)</(?:\w+:)?{nome}>", testo, re.S | re.I)
    return " ".join(m.group(1).split()) if m else ""


def onvif(secondi: float) -> dict[str, dict]:
    """Manda la domanda in multicast e raccoglie chi risponde, per indirizzo."""
    import uuid as _uuid

    trovati: dict[str, dict] = {}
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, struct.pack("b", 2))
    s.settimeout(0.5)
    try:
        s.bind(("", 0))
        for tipo in ("dn:NetworkVideoTransmitter", "d:Device"):
            testo = PROBE.format(uuid=_uuid.uuid4(), tipo=tipo)
            try:
                s.sendto(testo.encode(), (MULTICAST, PORTA_WSD))
            except OSError as exc:
                print(f"  (non riesco a mandare la domanda: {exc})")
                return trovati
        fine = time.monotonic() + secondi
        while time.monotonic() < fine:
            try:
                dati, mittente = s.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            risposta = dati.decode("utf-8", "replace")
            dove = trovati.setdefault(mittente[0], {"xaddrs": set(), "tipi": "", "scopi": ""})
            for url in _tag(risposta, "XAddrs").split():
                dove["xaddrs"].add(url)
            dove["tipi"] = dove["tipi"] or _tag(risposta, "Types")
            dove["scopi"] = dove["scopi"] or _tag(risposta, "Scopes")
    finally:
        s.close()
    return trovati


def mdns(secondi: float) -> tuple[list[str], list[str]]:
    """``(annunci _rtsp._tcp, tutti i tipi di servizio visti)``.

    Il secondo elenco è la parte che spiega: se la camera si annuncia in un modo suo — e molte lo
    fanno — qui si legge con che nome, e si sa cosa cercare.
    """
    try:
        from zeroconf import ServiceBrowser, Zeroconf  # noqa: PLC0415
    except ImportError:
        print("  (zeroconf non installato: questa parte si salta)")
        return [], []

    camere: list[str] = []
    tipi: set[str] = set()

    class Orecchie:
        def add_service(self, zc, tipo, nome):
            if tipo == "_services._dns-sd._udp.local.":
                tipi.add(nome)
                return
            info = zc.get_service_info(tipo, nome, timeout=1500)
            indirizzi = [socket.inet_ntoa(a) for a in (info.addresses if info else []) if len(a) == 4]
            camere.append(f"{nome}  →  {', '.join(indirizzi) or '?'}:{info.port if info else '?'}")

        def update_service(self, *a):
            pass

        def remove_service(self, *a):
            pass

    zc = Zeroconf()
    try:
        ServiceBrowser(zc, "_rtsp._tcp.local.", Orecchie())
        ServiceBrowser(zc, "_services._dns-sd._udp.local.", Orecchie())
        time.sleep(secondi)
    finally:
        zc.close()
    return camere, sorted(tipi)


def chi_parla_rtsp(indirizzo: str, porta: int, timeout: float = 2.0) -> str:
    """Chi c'è dietro una porta RTSP: si chiede a lui, con un `OPTIONS`.

    Nata dalla prima esecuzione vera, il 25/10: in casa sono comparsi **tre** indirizzi con la porta
    RTSP aperta, e due erano i nostri nodi. Sapere che una porta è aperta non dice niente; sapere che
    dietro c'è `mediamtx` — cioè un nodo Mirto — invece chiude la domanda in un colpo, e lascia
    accesa l'attenzione solo su chi resta.
    """
    richiesta = (f"OPTIONS rtsp://{indirizzo}:{porta}/ RTSP/1.0\r\nCSeq: 1\r\n"
                 "User-Agent: Mirto-sonda\r\n\r\n")
    try:
        with socket.create_connection((indirizzo, porta), timeout) as s:
            s.sendall(richiesta.encode())
            risposta = s.recv(4096).decode("utf-8", "replace")
    except OSError:
        return ""
    server = re.search(r"^Server:\s*(.+)$", risposta, re.M | re.I)
    if server:
        return server.group(1).strip()
    prima = next((r.strip() for r in risposta.splitlines() if r.strip()), "")
    return prima if prima.startswith("RTSP/") else ""


# MediaMTX — il server che mette in rete le camere dei nostri nodi — **non dice «mediamtx»**: si
# presenta col nome della libreria con cui e' scritto. Il 25/10 la sonda ha trovato tre porte RTSP in
# casa e non ne ha riconosciuta nessuna, perche' cercavo la parola sbagliata. La riconosce la realta',
# non il nome che immagino io.
SERVER_NOSTRI = ("gortsplib", "mediamtx")
PORTA_NODO = 8443          # l'API del nodo: se risponde anche questa, non e' una somiglianza


def _di_chi_e(indirizzo: str, server: str) -> str:
    """La frase che segue il nome del server, quando si puo' dire di piu' che «risponde»."""
    if not any(n in server.lower() for n in SERVER_NOSTRI):
        return ""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.6)
    try:
        nodo = s.connect_ex((indirizzo, PORTA_NODO)) == 0
    except OSError:
        nodo = False
    finally:
        s.close()
    return " ← un nodo Mirto" if nodo else " ← MediaMTX (il server dei nostri nodi)"


def porte_aperte(indirizzo: str, timeout: float = 0.4) -> list[str]:
    """Quali porte da camera risponde quell'indirizzo. Solo su indirizzi che si sono già fatti vivi."""
    aperte = []
    for porta, cosa in PORTE_CAMERA:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            risponde = s.connect_ex((indirizzo, porta)) == 0
        except OSError:
            risponde = False
        finally:
            s.close()
        if not risponde:
            continue
        riga = f"{porta} ({cosa})"
        if cosa == "RTSP":
            chi = chi_parla_rtsp(indirizzo, porta)
            if chi:
                riga += f" — {chi}{_di_chi_e(indirizzo, chi)}"
        aperte.append(riga)
    return aperte


# Le porte che vale la pena provare quando si guarda **un** indirizzo: oltre alle solite, quelle che
# usano le camere cinesi economiche. Su una scansione di tutta la rete sarebbero troppe (254 × 12);
# su un indirizzo solo costano quattro secondi e rispondono a una domanda precisa.
PORTE_ESTESE = ((23, "telnet"), (80, "web"), (443, "web sicuro"), (554, "RTSP"), (8000, "web/SDK"),
                (8080, "web"), (8443, "web sicuro"), (8554, "RTSP"), (8899, "ONVIF di alcune marche"),
                (9000, "web/SDK"), (10554, "RTSP"), (34567, "protocollo XM/Dahua"))


def chi_parla_http(indirizzo: str, porta: int, timeout: float = 2.0) -> str:
    """Come si presenta un server web: la riga di stato e l'intestazione `Server`.

    Su una camera sconosciuta dice spesso più di una scansione intera — il nome del server web è
    quello che il produttore non si è preso la briga di cambiare.
    """
    richiesta = f"HEAD / HTTP/1.0\r\nHost: {indirizzo}\r\nUser-Agent: Mirto-sonda\r\n\r\n"
    try:
        with socket.create_connection((indirizzo, porta), timeout) as s:
            if porta in (443, 8443):
                import ssl  # noqa: PLC0415

                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                s = ctx.wrap_socket(s, server_hostname=indirizzo)
            s.sendall(richiesta.encode())
            risposta = s.recv(4096).decode("utf-8", "replace")
    except (OSError, ValueError):
        return ""
    server = re.search(r"^Server:\s*(.+)$", risposta, re.M | re.I)
    stato = next((r.strip() for r in risposta.splitlines() if r.startswith("HTTP/")), "")
    return " · ".join(x for x in (server.group(1).strip() if server else "", stato) if x)


def un_indirizzo(indirizzo: str) -> list[str]:
    """Tutto quello che si può chiedere a **un** dispositivo senza entrarci dentro."""
    fuori = []
    for porta, cosa in PORTE_ESTESE:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.0)
        try:
            risponde = s.connect_ex((indirizzo, porta)) == 0
        except OSError:
            risponde = False
        finally:
            s.close()
        if not risponde:
            continue
        riga = f"{porta} ({cosa})"
        if cosa == "RTSP":
            chi = chi_parla_rtsp(indirizzo, porta)
            if chi:
                riga += f" — {chi}{_di_chi_e(indirizzo, chi)}"
        elif "web" in cosa:
            chi = chi_parla_http(indirizzo, porta)
            if chi:
                riga += f" — {chi}"
        fuori.append(riga)
    return fuori


def mia_sottorete() -> str:
    """I primi tre numeri dell'indirizzo di questo computer sulla rete di casa, es. «192.168.1»."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))      # indirizzo riservato alla documentazione: non parte niente
        mio = s.getsockname()[0]
    except OSError:
        return ""
    finally:
        s.close()
    return mio.rsplit(".", 1)[0] if mio.count(".") == 3 else ""


def tutta_la_sottorete(base: str, quanti_insieme: int = 64) -> list[str]:
    """Bussa alle porte da camera su **tutta** la sottorete, non solo su chi si è fatto vivo.

    Questa è una scansione, e si chiede apposta con `--sottorete`: il resto della sonda guarda solo
    chi si è presentato, e la differenza va lasciata in mano a chi lancia il comando.

    Nasce da un punto cieco trovato da Marco il 25/10: `--vicini` legge la tabella ARP, cioè i
    dispositivi con cui **questo computer ha già parlato**. Una camera che dialoga solo col proprio
    cloud non scambia un pacchetto con noi, quindi lì dentro non compare mai — e «non compare» veniva
    letto come «non è in rete». Due cose diverse, e la sonda le confondeva.
    """
    from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415

    indirizzi = [f"{base}.{n}" for n in range(1, 255)]
    with ThreadPoolExecutor(max_workers=quanti_insieme) as pool:
        esiti = list(pool.map(lambda ip: (ip, porte_aperte(ip, timeout=0.35)), indirizzi))
    return [f"{ip}: {', '.join(porte)}" for ip, porte in esiti if porte]


def vicini() -> list[str]:
    """Gli indirizzi con cui questo computer ha già parlato (tabella ARP). Non è una scansione."""
    import subprocess  # noqa: PLC0415

    try:
        res = subprocess.run(["arp", "-a"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    return sorted({m.group(1) for m in re.finditer(r"\((\d+\.\d+\.\d+\.\d+)\)", res.stdout)})


def main() -> int:
    ap = argparse.ArgumentParser(description="Chi in rete si presenta come camera, e in che lingua.")
    ap.add_argument("--secondi", type=float, default=4.0, help="quanto ascoltare (per difetto 4)")
    ap.add_argument("--vicini", action="store_true",
                    help="prova le porte anche sugli indirizzi già nella tabella ARP di questo computer")
    ap.add_argument("--indirizzo", metavar="192.168.1.19",
                    help="guarda **un** dispositivo preciso, con molte più porte e chiedendo a ognuna "
                         "chi risponde: la domanda da fare quando si sa già dove sta")
    ap.add_argument("--sottorete", nargs="?", const="", metavar="192.168.1",
                    help="scansiona tutta la sottorete (per difetto quella di questo computer): "
                         "serve per le camere che parlano solo col loro cloud e non si fanno mai "
                         "vedere né dagli annunci né dalla tabella ARP")
    args = ap.parse_args()

    if args.indirizzo:
        print(f"Cosa risponde {args.indirizzo}")
        righe = un_indirizzo(args.indirizzo)
        for riga in righe:
            print(f"   {riga}")
        if not righe:
            print("   nessuna delle porte provate risponde: o non è acceso, o parla solo verso l'esterno.")
        print("\nFine. Questa sonda legge soltanto: non ha cambiato niente e non ha salvato niente.")
        return 0

    print("1) ONVIF — la domanda in multicast a cui rispondono le camere di marca")
    risposte = onvif(args.secondi)
    if not risposte:
        print("   nessuna risposta. Non vuol dire che non ci sia: molte cam rispondono solo dopo")
        print("   essere state configurate con la loro app, e alcune hanno ONVIF spento di serie.")
    for ip, dati in sorted(risposte.items()):
        print(f"   {ip}")
        for url in sorted(dati["xaddrs"]):
            print(f"      servizio:  {url}")
        if dati["tipi"]:
            print(f"      tipo:      {dati['tipi']}")
        if dati["scopi"]:
            print(f"      descrive:  {dati['scopi'][:160]}")

    print("\n2) mDNS — quello che Mirto ascolta (_rtsp._tcp)")
    camere, tipi = mdns(args.secondi)
    for riga in camere:
        print(f"   {riga}")
    if not camere:
        print("   nessun annuncio _rtsp._tcp: a Mirto questa camera non comparirà da sola.")
    if tipi:
        print("\n   Tutti i tipi di annuncio sentiti in rete (se la cam si annuncia a modo suo, è qui):")
        for t in tipi:
            print(f"      {t}")

    indirizzi = list(risposte)
    if args.vicini:
        indirizzi += [v for v in vicini() if v not in indirizzi]
    if indirizzi:
        print("\n3) Porte da camera, su chi si è fatto vivo")
        for ip in indirizzi:
            aperte = porte_aperte(ip)
            if aperte:
                print(f"   {ip}: {', '.join(aperte)}")
    if args.sottorete is not None:
        base = args.sottorete or mia_sottorete()
        if not base:
            print("\n4) Sottorete: non capisco qual è la rete di questo computer; passala tu, es. --sottorete 192.168.1")
        else:
            print(f"\n4) Tutta la sottorete {base}.1-254 — anche chi non si è mai presentato")
            righe = [r for r in tutta_la_sottorete(base) if r.split(":")[0] not in indirizzi]
            for riga in righe:
                print(f"   {riga}")
            if not righe:
                print("   nessun altro indirizzo con porte da camera aperte.")
            print("   (gli indirizzi senza porte da camera non si stampano: ci sono, ma non c'entrano)")
    print("\nFine. Questa sonda legge soltanto: non ha cambiato niente e non ha salvato niente.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
