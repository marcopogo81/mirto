#!/usr/bin/env bash
# Installa o aggiorna Mirto Node sul Raspberry: servizio del nodo, camera (MediaMTX con password),
# audio (Bluetooth / jack / USB), annunci in rete.      Uso:  sudo ./install.sh
# Rieseguibile: configurazione, chiave, certificato, password della camera e abbinamenti restano.
# Con --primo-avvio (lo usa firstboot.sh sull'immagine del nodo): niente rete né riavvii di servizi,
# rigenera solo ciò che manca (chiave, certificato, password della camera, configurazioni).
set -euo pipefail
# I file che questo copione scrive («cat > …») nascono con la umask di chi lo lancia. Il 20/10 il primo
# avvio ne aveva una stretta ereditata dal passo precedente, e i due annunci di avahi sono nati 0600:
# il demone, che gira senza privilegi, non poteva leggerli e il nodo era invisibile in rete. Vale anche
# per chi lo lancia a mano con una umask sua. I permessi li decide questo copione, non chi lo chiama.
umask 022
[ "$(id -u)" = 0 ] || { echo "Avvialo con sudo:  sudo ./install.sh"; exit 1; }
FIRST=0
[ "${1:-}" = "--primo-avvio" ] && FIRST=1
# durante il primo avvio i servizi partono da soli DOPO questo script: non si avviano né fermano da qui
sc() {
  if [ "$FIRST" = 1 ]; then
    case "$1" in
      daemon-reload) systemctl daemon-reload ;;
      enable) shift; local a args=(); for a in "$@"; do [ "$a" = --now ] || args+=("$a"); done
              systemctl enable "${args[@]}" ;;
      *) : ;;
    esac
  else
    systemctl "$@"
  fi
}
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST=/opt/mirto-node
CONF=/etc/mirto-node
NODE_USER=mirto-node     # utente di sistema del servizio (S-03): senza shell, senza casa, senza poteri
HOST="$(hostname)"
MTX_VERSION="v1.21.2"
MTX_SHA256="180a235374a548d9133cdd5e20f5dbd8b01317be1c86e0d7e66df585dc42ed6f"   # linux_arm64, dal sito ufficiale
MTX_DIR=/opt/mediamtx
PY="python3 -m mirto_node.install_tools"

echo "1/8  Pacchetti (audio, Bluetooth, annuncio in rete)…"
if [ "$FIRST" = 0 ]; then
if apt-get update -qq 2>/dev/null; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq alsa-utils bluez bluez-alsa-utils avahi-daemon openssl \
    python3 curl rpicam-apps-lite >/dev/null 2>&1 || \
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq alsa-utils bluez bluez-alsa-utils avahi-daemon openssl python3 curl >/dev/null
  # ffmpeg serve **solo** all'audio nelle registrazioni (`flusso_audio.py`), che e' spento di serie:
  # si prova a installarlo e se non si riesce si va avanti. Metterlo fra i necessari vorrebbe dire
  # un'installazione che fallisce su un nodo che funziona benissimo — e questo copione lo rilancia
  # ogni aggiornamento del nodo (N-02), anche su un nodo senza internet.
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ffmpeg >/dev/null 2>&1 \
    || echo "     ffmpeg non installato: l'audio nelle registrazioni non sara' disponibile su questo nodo."
else
  # Un nodo senza internet è normale: cavo diretto al computer, rete isolata, Wi-Fi spento.
  # Si va avanti lo stesso se i programmi ci sono già; se ne manca uno, meglio fermarsi qui.
  MANCANTI=""
  for c in aplay bluetoothctl avahi-daemon openssl python3 curl; do
    command -v "$c" >/dev/null 2>&1 || MANCANTI="${MANCANTI:+$MANCANTI }$c"
  done
  if [ -n "$MANCANTI" ]; then
    echo "     Il nodo non raggiunge internet e mancano: $MANCANTI"
    echo "     Collegalo a una rete con internet e rilancia l'installazione."
    exit 1
  fi
  echo "     Niente internet: salto l'aggiornamento dei pacchetti (ci sono già tutti)."
  command -v ffmpeg >/dev/null 2>&1 \
    || echo "     Senza ffmpeg l'audio nelle registrazioni non è disponibile su questo nodo (tutto il resto sì)."
fi
rfkill unblock bluetooth 2>/dev/null || true
fi
sc enable --now bluetooth >/dev/null 2>&1 || true
sc enable --now bluealsa >/dev/null 2>&1 || true

echo "2/8  Programma in $DEST…"
install -d "$DEST"
if [ "$HERE" != "$DEST" ]; then
  rm -rf "$DEST/mirto_node"
  cp -r "$HERE/mirto_node" "$DEST/"
  # servono anche sul nodo: primo avvio dell'immagine e reinstallazione senza la cartella del Mac
  install -m 755 "$HERE/install.sh" "$HERE/firstboot.sh" "$HERE/prepare_image.sh" "$HERE/boot-segnali.sh" "$DEST/"
  install -m 644 "$HERE/node.toml.example" "$HERE/99-mirto-leds.rules" "$DEST/"
fi
find "$DEST" -name "__pycache__" -prune -exec rm -rf {} +
# Il programma lo legge il servizio, che gira SENZA privilegi (S-03): deve poterlo leggere chiunque.
# `cp -r` tiene i permessi della sorgente, e una sorgente arrivata con umask stretta (rsync da un
# computer dove i file sono 600, uno zip scompattato male, una chiavetta) rende /opt illeggibile:
# il servizio muore in partenza con «PermissionError: … server.py» e il nodo sparisce da Mirto,
# mentre la camera continua a funzionare perché è un altro servizio. Successo il 21/10.
chmod -R u=rwX,go=rX "$DEST"
chmod 755 "$DEST/install.sh" "$DEST/firstboot.sh" "$DEST/prepare_image.sh" "$DEST/boot-segnali.sh" 2>/dev/null || true
cat > /usr/local/bin/mirto-nodo <<'SH'
#!/bin/sh
# Configurazione guidata del nodo Mirto (installata da node/install.sh)
[ "$(id -u)" = 0 ] || exec sudo "$0" "$@"
cd /opt/mirto-node && exec python3 -m mirto_node.cli "$@"
SH
chmod 755 /usr/local/bin/mirto-nodo
cd "$DEST"

echo "3/8  Utente del servizio e configurazione in $CONF…"
# Il servizio non gira più da root: un bug nel server HTTPS resta confinato a un utente che non
# possiede niente. I gruppi servono per la cassa (audio), la sirena Bluetooth e la camera.
id "$NODE_USER" >/dev/null 2>&1 || \
  useradd --system --no-create-home --shell /usr/sbin/nologin "$NODE_USER"
GROUPS_NODE=""
for g in audio bluetooth video gpio; do
  getent group "$g" >/dev/null 2>&1 || continue
  usermod -aG "$g" "$NODE_USER"
  GROUPS_NODE="${GROUPS_NODE:+$GROUPS_NODE }$g"
done
install -d -m 700 -o "$NODE_USER" -g "$NODE_USER" "$CONF"
install -d -m 700 -o "$NODE_USER" -g "$NODE_USER" "$CONF/chiavi"    # una chiave per abbinamento (keys.py)
if [ ! -f "$CONF/node.toml" ]; then
  # il pezzo da togliere si misura dal prefisso, non si scrive a mano: era «h[8:]» (la lunghezza di
  # «myhouse-») e il 7/11, cambiando nome al programma, «mirto-ingresso» diventava «Gresso»
  NAME="$(python3 -c "import sys; p='mirto-'; h=sys.argv[1]; h=h[len(p):] if h.startswith(p) else h; print(h.replace('-',' ').capitalize())" "$HOST")"
  sed "s/NOME_NODO/$NAME/" "$HERE/node.toml.example" > "$CONF/node.toml"
fi
OLD_MTX=""
for f in "$MTX_DIR/mediamtx.yml" /home/*/mediamtx/mediamtx.yml; do [ -f "$f" ] && OLD_MTX="$f" && break; done
$PY prepara-config "$CONF/node.toml" $OLD_MTX
NEW_KEY=0
# se è stata ritirata (chiave-revocata) non si rigenera: ogni Mirto ha la sua chiave
[ -f "$CONF/chiave" ] || [ -f "$CONF/chiave-revocata" ] || \
  { openssl rand -hex 32 > "$CONF/chiave"; chmod 600 "$CONF/chiave"; NEW_KEY=1; }
[ -f "$CONF/rtsp" ] || { echo "mirto:$(openssl rand -hex 12)" > "$CONF/rtsp"; chmod 600 "$CONF/rtsp"; }
# API locale di MediaMTX (accendere/spegnere la registrazione): con password, solo per il servizio del nodo
[ -f "$CONF/mtx-api" ] || { echo "mirto-api:$(openssl rand -hex 16)" > "$CONF/mtx-api"; chmod 600 "$CONF/mtx-api"; }
if [ ! -f "$CONF/node.crt" ]; then
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 3650 \
    -keyout "$CONF/node.key" -out "$CONF/node.crt" -subj "/CN=$HOST.local" \
    -addext "subjectAltName=DNS:$HOST.local,DNS:localhost" 2>/dev/null
  chmod 600 "$CONF/node.key"
fi
# nodo mai abbinato: il servizio apre l'abbinamento per 5 minuti e genera il PIN appena parte
[ -e "$CONF/abbinato" ] || : > "$CONF/apri-abbinamento"
PORT="$(sed -n 's/^porta *= *\([0-9]*\).*/\1/p' "$CONF/node.toml" | head -1)"; PORT="${PORT:-8443}"
FP="$(openssl x509 -in "$CONF/node.crt" -noout -fingerprint -sha256 | cut -d= -f2)"

echo "4/8  Camera (MediaMTX $MTX_VERSION)…"
if $PY camera-attiva "$CONF/node.toml"; then
  if [ "$(cat "$MTX_DIR/VERSION" 2>/dev/null)" != "$MTX_VERSION" ]; then
    TMP="$(mktemp -d)"
    curl -fsSL -o "$TMP/mtx.tgz" \
      "https://github.com/bluenviron/mediamtx/releases/download/$MTX_VERSION/mediamtx_${MTX_VERSION}_linux_arm64.tar.gz"
    echo "$MTX_SHA256  $TMP/mtx.tgz" | sha256sum -c --quiet || { echo "MediaMTX: somma di controllo errata"; exit 1; }
    install -d "$MTX_DIR"
    tar xzf "$TMP/mtx.tgz" -C "$MTX_DIR" mediamtx
    echo "$MTX_VERSION" > "$MTX_DIR/VERSION"
    rm -rf "$TMP"
  fi
  id mediamtx >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin mediamtx
  GROUPS_CAM="video"
  getent group render >/dev/null && GROUPS_CAM="video render"
  for g in $GROUPS_CAM; do usermod -aG "$g" mediamtx; done
  sc stop mediamtx 2>/dev/null || true            # libera la camera per riconoscerla
  # Da qui in avanti la casa non vede piu' niente, e il riavvio di MediaMTX sta cinquanta righe piu'
  # sotto: se lo script muore in mezzo — e con «set -e» basta un comando che fallisce — la camera resta
  # spenta e nessuno lo dice. Successo il 22/10 su un disco vfat: il nodo e' rimasto senza stream
  # finche' qualcuno non se n'e' accorto. Un aggiornamento puo' fallire; non puo' lasciare una casa
  # senza camera. Il trap la riaccende comunque, e si toglie appena il riavvio vero e' stato fatto.
  if [ "$FIRST" = 0 ]; then
    trap 'systemctl start mediamtx >/dev/null 2>&1 || true' EXIT
  fi
  # registrazione sul nodo: cartella sul disco USB (se collegato) o sulla microSD, di proprietà di MediaMTX
  REC_LINE="$($PY registrazioni "$CONF/node.toml")"
  if [ -n "$REC_LINE" ]; then
    REC_DIR="${REC_LINE%%	*}"; REC_DISK="${REC_LINE#*	}"
    if [ -n "$REC_DISK" ] && ! mountpoint -q "$REC_DISK"; then
      echo "     ATTENZIONE: il disco $REC_DISK non è collegato: la registrazione partirà quando lo colleghi"
    else
      mkdir -p "$REC_DIR/cam"
      usermod -aG mediamtx "$NODE_USER"
      # Ci scrive MediaMTX, ma l'anello (cancellare i più vecchi) lo gira il nodo, che non è root:
      # cancellare dipende dai permessi della cartella, quindi gruppo in scrittura e setgid.
      # Su un disco senza permessi Unix — vfat o exfat, cioè una chiavetta come arriva dal negozio —
      # proprietario e setgid non esistono: li decide il mount, uguali per tutti i file. Lì chown e
      # chmod falliscono, ed è giusto che falliscano; quello che non era giusto è che si portassero
      # via tutta l'installazione (22/10: «chmod: Operation not permitted», installazione morta al
      # passo 4/8 con la camera già spenta). Si tira dritto, dicendolo.
      if chown -R mediamtx:mediamtx "$REC_DIR" 2>/dev/null \
         && chmod 2775 "$REC_DIR" "$REC_DIR/cam" 2>/dev/null; then
        printf 'd %s 2775 mediamtx mediamtx -\nd %s/cam 2775 mediamtx mediamtx -\n' "$REC_DIR" "$REC_DIR" \
          > /etc/tmpfiles.d/mirto-node.conf   # rifatta a ogni avvio, anche su un disco rimontato
        echo "     registrazione in $REC_DIR (ad anello: i file più vecchi si cancellano da soli)"
      else
        # niente tmpfiles.d: a ogni avvio proverebbe a rimettere permessi che quel disco non sa tenere
        rm -f /etc/tmpfiles.d/mirto-node.conf
        echo "     registrazione in $REC_DIR (ad anello) — disco senza permessi Unix (vfat/exfat):"
        echo "     li decide il mount, e va bene così"
      fi
    fi
  fi
  $PY mediamtx "$CONF/node.toml" "$CONF/rtsp" > "$MTX_DIR/mediamtx.yml.new"
  mv "$MTX_DIR/mediamtx.yml.new" "$MTX_DIR/mediamtx.yml"
  chown root:mediamtx "$MTX_DIR/mediamtx.yml"; chmod 640 "$MTX_DIR/mediamtx.yml"   # contiene la password
  cat > /etc/systemd/system/mediamtx.service <<UNIT
[Unit]
Description=MediaMTX (camera del nodo Mirto)
Wants=network-online.target
After=network-online.target

[Service]
User=mediamtx
Group=mediamtx
# ora UTC nei nomi dei file registrati: niente doppioni al cambio dell'ora legale
Environment=TZ=UTC0
SupplementaryGroups=$GROUPS_CAM
WorkingDirectory=$MTX_DIR
ExecStart=$MTX_DIR/mediamtx $MTX_DIR/mediamtx.yml
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
  $PY avahi-camera > /etc/avahi/services/mirto-cam.service
  chmod 644 /etc/avahi/services/mirto-cam.service   # avahi gira senza privilegi: deve poterlo leggere
  sc daemon-reload
  sc enable mediamtx >/dev/null 2>&1
  sc restart mediamtx
  trap - EXIT          # riaccesa per davvero: la rete di sicurezza non serve piu'
else
  echo "     camera disattivata in node.toml: MediaMTX fermo"
  sc disable --now mediamtx >/dev/null 2>&1 || true
  rm -f /etc/avahi/services/mirto-cam.service
fi

echo "5/8  Annuncio del nodo in rete…"
cat > /etc/avahi/services/mirto-node.service <<XML
<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">Mirto Node %h</name>
  <service>
    <type>_mirto-node._tcp</type>
    <port>$PORT</port>
    <txt-record>fp=$FP</txt-record>
  </service>
</service-group>
XML
chmod 644 /etc/avahi/services/mirto-node.service    # idem: con una umask stretta nascerebbe 0600
sc reload avahi-daemon 2>/dev/null || sc restart avahi-daemon

echo "6/8  Servizio del nodo…"
cat > /etc/systemd/system/mirto-node.service <<UNIT
[Unit]
Description=Mirto Node (camera, sirena, luci… per Mirto)
Wants=network-online.target
After=network-online.target bluetooth.target bluealsa.service mediamtx.service

[Service]
User=$NODE_USER
Group=$NODE_USER
SupplementaryGroups=$GROUPS_NODE
WorkingDirectory=$DEST
ExecStart=/usr/bin/python3 -m mirto_node --config $CONF/node.toml
Restart=always
RestartSec=5
NoNewPrivileges=yes
ProtectHome=yes
PrivateTmp=yes
ProtectSystem=full
ProtectKernelTunables=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
CapabilityBoundingSet=
# Dove il nodo scrive: la sua configurazione e i due file del LED verde. ProtectKernelTunables
# rende /sys di sola lettura, e il LED sta li': senza questa riga «luce di prova» non funziona,
# per quanto giusti siano i permessi. Il «-» davanti vuol dire «se non c'e', pazienza».
# La microSD la tocca mirto-boot-segnali.service, da root.
ReadWritePaths=$CONF -/sys/class/leds/ACT -/sys/class/leds/led0

[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/systemd/system/mirto-firstboot.service <<UNIT
[Unit]
Description=Mirto Node: primo avvio dell'immagine (nome, chiavi e certificato nuovi)
ConditionPathExists=$CONF/primo-avvio
# dopo cloud-init, che imposta il nome scelto in Raspberry Pi Imager (se c'è)
After=local-fs.target cloud-init-local.service cloud-init-network.service cloud-init.service
Before=ssh.service mediamtx.service mirto-node.service

[Service]
Type=oneshot
ExecStart=$DEST/firstboot.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
UNIT
# LED verde comandabile dal gruppo del nodo (la regola vale dal prossimo avvio; qui si applica subito)
install -m 644 "$HERE/99-mirto-leds.rules" /etc/udev/rules.d/99-mirto-leds.rules 2>/dev/null || \
  install -m 644 "$DEST/99-mirto-leds.rules" /etc/udev/rules.d/99-mirto-leds.rules 2>/dev/null || true
if [ "$FIRST" = 0 ]; then
  udevadm control --reload >/dev/null 2>&1 || true
  udevadm trigger --subsystem-match=leds >/dev/null 2>&1 || true
fi
for d in /sys/class/leds/ACT /sys/class/leds/led0; do
  [ -d "$d" ] || continue
  chgrp "$NODE_USER" "$d/trigger" "$d/brightness" 2>/dev/null || true
  chmod g+w "$d/trigger" "$d/brightness" 2>/dev/null || true
done

# I due lavori sulla microSD che il servizio, senza privilegi, non può fare (vedi boot-segnali.sh)
cat > /etc/systemd/system/mirto-boot-segnali.service <<UNIT
[Unit]
Description=Mirto Node: lavori sulla microSD per conto del servizio senza privilegi

[Service]
Type=oneshot
ExecStart=$DEST/boot-segnali.sh
UNIT
cat > /etc/systemd/system/mirto-boot-segnali.path <<UNIT
[Unit]
Description=Mirto Node: aspetta un biglietto dal servizio o un file sulla microSD

[Path]
PathExists=/boot/firmware/mirto-abbina
PathExists=$CONF/pin-da-mostrare
PathExists=$CONF/pin-da-togliere
PathExists=$CONF/camera-da-applicare
PathExists=$CONF/audio-da-fare
PathExists=$CONF/wifi-da-fare
PathExists=$CONF/disco-da-fare
PathExists=$CONF/pacchetto-da-installare
PathExists=$CONF/registrazioni-da-preparare
PathExists=$CONF/disco-da-rimontare

[Install]
WantedBy=multi-user.target
UNIT

# tutto quello che il servizio deve poter leggere e scrivere è suo (anche se è stato creato da root)
chown -R "$NODE_USER:$NODE_USER" "$CONF"

sc daemon-reload
sc enable mirto-firstboot >/dev/null 2>&1
sc enable mirto-node >/dev/null 2>&1
sc enable --now mirto-boot-segnali.path >/dev/null 2>&1
sc restart mirto-node

if [ "$FIRST" = 1 ]; then
  echo "Primo avvio: configurazione rigenerata, impronta $FP"
  exit 0
fi
echo "7/8  Controllo…"
sleep 3
for s in mirto-node mediamtx; do
  if systemctl is-enabled "$s" >/dev/null 2>&1; then
    printf "     %-14s %s\n" "$s" "$(systemctl is-active "$s")"
  fi
done

echo "8/8  Fatto."
echo
echo "  Configurazione guidata:  mirto-nodo   (stato · wifi · audio · camera · abbina)"
echo "  Indirizzo per Mirto:   https://$HOST.local:$PORT"
echo "  Impronta del certificato:"
echo "    $FP"
if [ ! -e "$CONF/abbinato" ]; then
  PIN="$(sed -n 's/.*"pin": *"\([0-9]*\)".*/\1/p' "$CONF/pin" 2>/dev/null)"
  echo "  Abbinamento APERTO per 5 minuti: in Mirto compare \"Nuovo nodo trovato\" → Abbina"
  echo "  PIN da scrivere in Mirto:  ${PIN:-leggilo con  mirto-nodo stato}"
  echo "  (controlla che l'impronta sia quella qui sopra). Più tardi: mirto-nodo abbina"
else
  echo "  Già abbinato. Per un altro Mac: mirto-nodo abbina"
fi
echo "  La password della camera la riceve Mirto dal nodo, non serve scriverla."
echo
if ! grep -q '^bluetooth_mac = "..:' "$CONF/node.toml" && grep -q '^uscita = "bluetooth"' "$CONF/node.toml"; then
  echo "  Manca la cassa: scegli l'uscita della sirena con   mirto-nodo audio"
fi
