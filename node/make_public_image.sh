#!/usr/bin/env bash
# Crea l'immagine PUBBLICA del nodo Mirto partendo da un'immagine fatta con  mirto-nodo prepara-immagine.
#
#   sudo ./make_public_image.sh  immagine-nodo-AAAA-MM-GG.img.gz  [cartella di uscita]
#
# Va eseguito su Linux come amministratore (va bene il Raspberry stesso: ha già tutti gli strumenti) e lavora
# su una COPIA: l'immagine di partenza non viene toccata. Rispetto all'immagine personale:
#   • toglie gli utenti (con le loro chiavi SSH) e azzera cloud-init, così Raspberry Pi Imager può impostare
#     utente, password, Wi-Fi e SSH di chi la scarica; senza personalizzazioni l'utente mirto nasce
#     **senza password**, e il primo avvio gliene dà una casuale, diversa per ogni microSD, scritta in
#     /boot/firmware/mirto-accesso.txt (S-05: nessun nodo in rete con credenziali che si sanno a memoria);
#   • rimette la configurazione di rete "solo cavo" e controlla che non resti nessuna password Wi-Fi;
#   • aggiorna il programma del nodo con quello di questa cartella;
#   • la rimpicciolisce (al primo avvio si riallarga a tutta la microSD) e la comprime in .img.xz con SHA-256.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Avvialo con sudo"; exit 1; }
IN="${1:?Uso: sudo $0 immagine.img.gz [cartella di uscita]}"
OUT_DIR="${2:-$(dirname "$IN")}"
HERE="$(cd "$(dirname "$0")" && pwd)"
STAMP="$(date +%Y-%m-%d)"
# La versione nel nome del file. Il programma che finisce dentro l'immagine e' quello della cartella
# `mirto_node` qui accanto, quindi la versione si legge da li' — non si chiede a nessuno e non si
# scrive a mano. Marco, 25/10: *«quando crei l'immagine mettici la versione, altrimenti bisogna
# scoprirlo»*. Ed e' vero: in `privato/` ci sono cinque immagini che si chiamano tutte uguale tranne
# la data, e per sapere cosa c'e' dentro una scheda bisognava accenderla.
VERSIONE="$(sed -n 's/^__version__ *= *"\(.*\)".*/\1/p' "$HERE/mirto_node/__init__.py" 2>/dev/null)"
NAME="mirto-node-$STAMP${VERSIONE:+-$VERSIONE}"
WORK="$OUT_DIR/.lavoro-$NAME.img"
MNT="$(mktemp -d)"
LOOP=""

for t in losetup e2fsck resize2fs dumpe2fs sfdisk xz userdel useradd usermod fstrim sha256sum; do
  command -v "$t" >/dev/null || { echo "Manca il programma $t"; exit 1; }
done
FREE_GB=$(( $(df --output=avail -k "$OUT_DIR" | tail -1) / 1024 / 1024 ))
[ "$FREE_GB" -ge 12 ] || { echo "Servono almeno 12 GB liberi in $OUT_DIR (ci sono $FREE_GB GB)"; exit 1; }

cleanup() {
  set +e
  mountpoint -q "$MNT/boot/firmware" && umount "$MNT/boot/firmware"
  mountpoint -q "$MNT" && umount "$MNT"
  [ -n "$LOOP" ] && losetup -d "$LOOP" 2>/dev/null
  rmdir "$MNT" 2>/dev/null
}
trap cleanup EXIT

echo "1/7  Copia di lavoro (solo i dati, gli spazi vuoti non occupano posto)…"
case "$IN" in
  *.gz) gzip -dc "$IN" ;;
  *.xz) xz -dc "$IN" ;;
  *) cat "$IN" ;;
esac | dd of="$WORK" bs=4M conv=sparse status=progress
sync

echo "2/7  Apro l'immagine…"
LOOP="$(losetup -fP --show "$WORK")"
e2fsck -fy "${LOOP}p2" >/dev/null || true
mount "${LOOP}p2" "$MNT"
mount "${LOOP}p1" "$MNT/boot/firmware"
[ -f "$MNT/etc/mirto-node/primo-avvio" ] || {
  echo "Questa immagine non è stata preparata con  mirto-nodo prepara-immagine: mi fermo."; exit 1; }

echo "3/7  Tolgo utenti, chiavi e stato di cloud-init…"
for u in $(awk -F: '$3 >= 1000 && $3 < 60000 {print $1}' "$MNT/etc/passwd"); do
  userdel -R "$MNT" -r "$u" 2>/dev/null || userdel -R "$MNT" "$u"
  echo "     utente $u tolto"
done
rm -rf "$MNT"/home/* "$MNT/root/.ssh" "$MNT"/root/.*_history   # mirto si crea dopo, qui sotto
rm -f "$MNT"/etc/sudoers.d/010_pi-nopasswd "$MNT"/etc/sudoers.d/90-cloud-init-users
rm -rf "$MNT"/var/lib/cloud/*
rm -rf "$MNT"/var/lib/mirto-node/registrazioni/cam/*     # video registrati sulla microSD
sed -i '\#/mnt/mirto-reg#d' "$MNT/etc/fstab"             # disco USB delle registrazioni
rm -f "$MNT"/etc/ssh/ssh_host_*
truncate -s 0 "$MNT/etc/machine-id"
rm -rf "$MNT"/var/log/journal/* "$MNT"/var/cache/apt/archives/*.deb
# Registro che sopravvive ai riavvii: senza, di un primo avvio andato storto non resta niente — il 20/10
# è mancato proprio quello per capire cos'era successo. Se la cartella c'è, journald ci scrive dentro.
mkdir -p "$MNT/var/log/journal"
chmod 2755 "$MNT/var/log/journal"
grep -q "^systemd-journal:" "$MNT/etc/group" && chown 0:"$(awk -F: '/^systemd-journal:/ {print $3}' \
  "$MNT/etc/group")" "$MNT/var/log/journal" || true
find "$MNT/var/log" -type f -exec truncate -s 0 {} +
echo "mirto-node" > "$MNT/etc/hostname"
sed -i "s/^127\.0\.1\.1.*/127.0.1.1\tmirto-node/" "$MNT/etc/hosts"

# L'utente lo crea l'immagine, non il primo avvio. Prima lo lasciavamo fare a cloud-init dal file
# user-data qui sotto, e il 20/10, sul primo nodo nato da questa immagine, e' andata male: quando
# firstboot.sh e' arrivato al passo della password, nel sistema non c'era ancora nessun utente con uid
# >= 1000, quindi ha saltato tutto in silenzio. Risultato: nessuna password, nessun foglio, e un nodo
# in cui non poteva entrare nessuno. Creandolo qui l'utente c'e' sempre, qualunque sia l'ordine dei
# servizi al primo avvio - e nasce **bloccato**, senza nessuna password: gliene da' una casuale
# firstboot.sh, diversa per ogni microSD (S-05).
echo "3bis/7  Utente mirto, bloccato e senza password…"
GRUPPI=""
for g in sudo adm audio video plugdev users input render netdev gpio i2c spi bluetooth; do
  grep -q "^$g:" "$MNT/etc/group" && GRUPPI="${GRUPPI:+$GRUPPI,}$g"
done
useradd -R "$MNT" -m -s /bin/bash -u 1000 -c "Mirto Node" ${GRUPPI:+-G "$GRUPPI"} mirto
usermod -R "$MNT" -L mirto                      # nessuna password: «!» nel file delle ombre
echo "     utente mirto nei gruppi: ${GRUPPI:-nessuno}"

echo "4/7  Primo avvio: utente predefinito e rete solo cavo (Raspberry Pi Imager li può sostituire)…"
cat > "$MNT/boot/firmware/user-data" <<'YAML'
#cloud-config
# Mirto Node – impostazioni del primo avvio.
# Raspberry Pi Imager ("Modifica impostazioni") sostituisce questo file con utente, Wi-Fi e SSH scelti da te.
# Senza personalizzazioni l'utente mirto nasce senza password: il primo avvio ne genera una casuale e la
# scrive in /boot/firmware/mirto-accesso.txt, che si legge mettendo la microSD in qualunque computer.
hostname: mirto-node
manage_etc_hosts: true
timezone: Europe/Rome
keyboard:
  model: pc105
  layout: "it"
user:
  name: mirto
  shell: /bin/bash
  lock_passwd: true      # nessuna password nota: la genera il primo avvio, diversa per ogni microSD
  groups: [sudo, adm, audio, video, plugdev, users, input, render, netdev, gpio, i2c, spi, bluetooth]
  sudo: "ALL=(ALL:ALL) ALL"
ssh_pwauth: true
runcmd:
  - [ systemctl, enable, --now, ssh ]
YAML
cat > "$MNT/boot/firmware/meta-data" <<META
instance-id: $NAME
META
cat > "$MNT/boot/firmware/network-config" <<'NET'
# Rete del nodo Mirto: solo cavo (il Wi-Fi si configura con  mirto-nodo wifi)
network:
  version: 2
  ethernets:
    eth0:
      dhcp4: true
      optional: true
NET
rm -f "$MNT/boot/firmware/mirto-pulizia.txt"

echo "5/7  Programma del nodo aggiornato a questa versione…"
rm -rf "$MNT/opt/mirto-node/mirto_node"
cp -r "$HERE/mirto_node" "$MNT/opt/mirto-node/"
find "$MNT/opt/mirto-node" -name "__pycache__" -prune -exec rm -rf {} +
# Come in install.sh: il servizio gira senza privilegi e il programma deve restare leggibile, qualunque
# permesso avesse la cartella da cui si copia. Un'immagine con /opt a 600 nasce già morta (21/10).
chmod -R u=rwX,go=rX "$MNT/opt/mirto-node"
# Tutto quello che install.sh si aspetta di trovare accanto a se'. Al primo avvio install.sh gira da
# /opt/mirto-node, cioe' da dentro l'immagine, e **non ricopia niente** (HERE == DEST): quindi un file
# dimenticato qui resta quello vecchio per sempre. Il 21/10 mancava boot-segnali.sh: l'immagine avrebbe
# avuto l'unita' che sorveglia «disco-da-fare» e un copione che non sa cosa farne - le finestre del
# Wi-Fi, dell'audio e del disco avrebbero detto «sto cercando» senza che partisse niente.
install -m 755 "$HERE/install.sh" "$HERE/firstboot.sh" "$HERE/prepare_image.sh" \
  "$HERE/boot-segnali.sh" "$MNT/opt/mirto-node/"
install -m 644 "$HERE/node.toml.example" "$HERE/99-mirto-leds.rules" "$MNT/opt/mirto-node/"
for f in install.sh firstboot.sh prepare_image.sh boot-segnali.sh node.toml.example 99-mirto-leds.rules; do
  cmp -s "$HERE/$f" "$MNT/opt/mirto-node/$f" \
    || { echo "     ✗ $f dentro l'immagine non e' quello di questa cartella"; exit 1; }
done
# l'unità del primo avvio come la scrive install.sh (dopo cloud-init, prima di ssh e del nodo)
sed -n "/^cat > \/etc\/systemd\/system\/mirto-firstboot.service <<UNIT$/,/^UNIT$/p" "$HERE/install.sh" \
  | sed '1d;$d' | sed "s#\$CONF#/etc/mirto-node#g; s#\$DEST#/opt/mirto-node#g" \
  > "$MNT/etc/systemd/system/mirto-firstboot.service"
mkdir -p "$MNT/etc/systemd/system/multi-user.target.wants"
ln -sf /etc/systemd/system/mirto-firstboot.service \
  "$MNT/etc/systemd/system/multi-user.target.wants/mirto-firstboot.service"
touch "$MNT/etc/mirto-node/primo-avvio"

echo "     controlli…"
BAD=0
STATO_UTENTE="$(awk -F: '$1 == "mirto" {print substr($2, 1, 1)}' "$MNT/etc/shadow")"
[ -n "$STATO_UTENTE" ] || { echo "     ✗ manca l'utente mirto: nessuno potrebbe entrare nel nodo"; BAD=1; }
case "$STATO_UTENTE" in
  "!"|"*"|"") ;;
  *) echo "     ✗ l'utente mirto ha una password dentro l'immagine"; BAD=1 ;;
esac
grep -q "^sudo:.*mirto" "$MNT/etc/group" || { echo "     ✗ mirto non è fra chi può usare sudo"; BAD=1; }
# Qui si guarda l'immagine vera, ed e' l'ultimo posto dove fermarla: in /etc/mirto-node deve restare
# SOLO quello che l'immagine deve avere. Un elenco di file proibiti resta indietro di una funzione -
# il 21/10 erano comparsi otto file di stato nuovi, uno dei quali con la password del Wi-Fi di casa.
TIENI_CONF="primo-avvio"
for f in "$MNT"/etc/mirto-node/* "$MNT"/etc/mirto-node/.[!.]*; do
  [ -e "$f" ] || continue
  nome="$(basename "$f")"
  case " $TIENI_CONF " in *" $nome "*) continue ;; esac
  echo "     ✗ resta /etc/mirto-node/$nome"; BAD=1
done
[ -e "$MNT/etc/mirto-node/primo-avvio" ] || {
  echo "     ✗ manca /etc/mirto-node/primo-avvio: il nodo non si configurerebbe al primo avvio"; BAD=1; }
if grep -r -l -i -E "psk|wpa-psk|password=" "$MNT/etc/NetworkManager/system-connections" "$MNT/etc/netplan" \
     "$MNT/boot/firmware/network-config" 2>/dev/null; then echo "     ✗ password Wi-Fi trovata"; BAD=1; fi
ls "$MNT"/etc/ssh/ssh_host_* >/dev/null 2>&1 && { echo "     ✗ restano chiavi SSH"; BAD=1; }
grep -qE "plain_text_passwd|^ *password:" "$MNT/boot/firmware/user-data" && {
  echo "     ✗ c'è una password scritta nell'immagine"; BAD=1; }
grep -q "lock_passwd: true" "$MNT/boot/firmware/user-data" || {
  echo "     ✗ l'utente dell'immagine non nasce senza password"; BAD=1; }
ls "$MNT"/home/mirto >/dev/null 2>&1 || { echo "     ✗ manca la cartella di mirto"; BAD=1; }
for h in "$MNT"/home/*; do
  [ -e "$h" ] || continue
  [ "$(basename "$h")" = "mirto" ] || { echo "     ✗ resta $(basename "$h") in /home"; BAD=1; }
done
ls "$MNT"/var/lib/mirto-node/registrazioni/cam/*.mp4 >/dev/null 2>&1 && { echo "     ✗ restano video registrati"; BAD=1; }
[ "$BAD" = 0 ] || { echo "Controlli NON superati: immagine non creata."; exit 1; }
echo "     ✓ utente mirto bloccato, nessuna chiave, password nota, rete salvata o video registrato"

echo "6/7  Rimpicciolisco…"
fstrim "$MNT" >/dev/null 2>&1 || true
umount "$MNT/boot/firmware"; umount "$MNT"
e2fsck -fy "${LOOP}p2" >/dev/null || true
resize2fs -M "${LOOP}p2"
BS=$(dumpe2fs -h "${LOOP}p2" 2>/dev/null | awk -F: '/^Block size/ {gsub(/ /,"",$2); print $2}')
BC=$(dumpe2fs -h "${LOOP}p2" 2>/dev/null | awk -F: '/^Block count/ {gsub(/ /,"",$2); print $2}')
losetup -d "$LOOP"; LOOP=""
START=$(sfdisk -d "$WORK" | awk -F'[=,]' '/img2 :/ {gsub(/ /,"",$2); print $2}')
SECTORS=$(( (BS * BC + 511) / 512 + 8192 ))     # 4 MB di margine
echo "$START,$SECTORS" | sfdisk --no-reread --no-tell-kernel -N 2 "$WORK" >/dev/null
truncate -s $(( (START + SECTORS) * 512 )) "$WORK"
echo "     immagine: $(( (START + SECTORS) * 512 / 1024 / 1024 )) MB"

# Quanto stringere dipende dalla macchina, non dai gusti: su un Raspberry con 1 GB di RAM «xz -6»
# riduce da solo i thread a uno e ci mette piu' di un'ora per 4 GB (provato il 20/10 su un 3B: 264 MB
# in 25 minuti). Meta' della dimensione non vale un'ora di attesa: sotto i 2 GB di memoria si usa -2,
# che va quattro volte piu' veloce e costa un 15% di spazio. Si puo' imporre con  MIRTO_XZ=6 .
MEM_MB="$(awk '/MemTotal/ {print int($2 / 1024)}' /proc/meminfo 2>/dev/null || true)"
MEM_MB="${MEM_MB:-4096}"
LIVELLO="${MIRTO_XZ:-}"
[ -n "$LIVELLO" ] || { [ "$MEM_MB" -lt 2048 ] && LIVELLO=2 || LIVELLO=6; }
echo "7/7  Comprimo con xz -$LIVELLO (${MEM_MB} MB di memoria): qualche minuto, su un Raspberry anche venti…"
xz -T0 "-$LIVELLO" -c "$WORK" > "$OUT_DIR/$NAME.img.xz"
rm -f "$WORK"
( cd "$OUT_DIR" && sha256sum "$NAME.img.xz" > "$NAME.img.xz.sha256" )
echo
echo "Fatto: $OUT_DIR/$NAME.img.xz  ($(du -h "$OUT_DIR/$NAME.img.xz" | cut -f1))"
cat "$OUT_DIR/$NAME.img.xz.sha256"
