#!/usr/bin/env bash
# Pulizia del nodo prima di copiarne la microSD come immagine (lanciata da: mirto-nodo prepara-immagine).
# Toglie ciò che è personale o deve essere unico; al riavvio mirto-firstboot rigenera tutto.
# Alla fine SPEGNE il Raspberry: la scheda va copiata sul Mac prima di riaccenderlo.
# Resoconto in /boot/firmware/mirto-pulizia.txt (leggibile anche dal Mac).
set -uo pipefail
CONF=/etc/mirto-node
LOG=/boot/firmware/mirto-pulizia.txt
[ -d /boot/firmware ] || LOG=/root/mirto-pulizia.txt
exec > >(tee "$LOG") 2>&1
echo "Pulizia di $(hostname) – $(date '+%d/%m/%Y %H:%M')"
step() { echo "• $*"; }

step "fermo i servizi del nodo"
systemctl stop mirto-node mediamtx 2>/dev/null

step "segreti e stato del nodo: chiave, certificato, password della camera, abbinamento, configurazione"
rm -f "$CONF"/{chiave,node.key,node.crt,rtsp,mtx-api,abbinato,abbina_fino,pin,chiave-revocata,apri-abbinamento,pin-da-mostrare,pin-da-togliere,node.toml}
rm -rf "$CONF/chiavi"          # le chiavi dei Mirto abbinati: l'immagine non deve portarsele dietro

# E tutto il resto. L'elenco qui sopra dice *perche'* ogni cosa va via, ma elencare i file e' il modo
# sbagliato di decidere cosa esce di casa: ogni funzione nuova ne aggiunge qualcuno e l'elenco resta
# indietro. Il 21/10, con le finestre di Wi-Fi, audio e disco, in $CONF erano comparsi otto file di
# stato nuovi - fra cui wifi-da-collegare.json, che contiene la password del Wi-Fi di casa in chiaro
# finche' il servizio da root non l'ha usata. Quindi: si tiene quello che serve, si cancella il resto.
TIENI="primo-avvio"
if [ -d "$CONF" ]; then
  for f in "$CONF"/* "$CONF"/.[!.]*; do
    [ -e "$f" ] || continue
    nome="$(basename "$f")"
    case " $TIENI " in *" $nome "*) continue ;; esac
    rm -rf "$f" && echo "    via $nome"
  done
fi
rm -f /boot/firmware/mirto-pin.txt
rm -f /opt/mediamtx/mediamtx.yml /etc/avahi/services/mirto-node.service /etc/avahi/services/mirto-cam.service

step "casse Bluetooth abbinate"
for m in $(bluetoothctl devices 2>/dev/null | awk '/^Device/ {print $2}'); do
  bluetoothctl remove "$m" >/dev/null 2>&1 && echo "    rimossa $m"
done

step "video registrati sulla microSD (registrazione del nodo)"
rm -rf /var/lib/mirto-node/registrazioni/cam/* 2>/dev/null
# un disco USB con le registrazioni non fa parte dell'immagine: la riga di montaggio si toglie
sed -i '\#/mnt/mirto-reg#d' /etc/fstab

step "chiavi SSH del Raspberry (si rigenerano al primo avvio)"
rm -f /etc/ssh/ssh_host_*

step "identità della macchina"
truncate -s 0 /etc/machine-id
[ -L /var/lib/dbus/machine-id ] || rm -f /var/lib/dbus/machine-id

step "registri e cronologia dei comandi"
journalctl --rotate >/dev/null 2>&1
journalctl --vacuum-time=1s >/dev/null 2>&1
find /var/log -type f \( -name "*.gz" -o -name "*.[0-9]" -o -name "*.old" \) -delete
find /var/log -type f -exec truncate -s 0 {} +
rm -f /root/.bash_history /home/*/.bash_history /root/.python_history /home/*/.python_history
apt-get clean

step "reti Wi-Fi salvate (ultimo passo: se sei collegato via Wi-Fi la sessione cade qui)"
nmcli -t -f UUID,TYPE con show 2>/dev/null | awk -F: '$2 == "802-11-wireless" {print $1}' | while read -r u; do
  nmcli con delete "$u" >/dev/null 2>&1 && echo "    rete eliminata"
done

# Raspberry Pi Imager (cloud-init) lascia la configurazione di rete nella partizione di avvio, con la
# password del Wi-Fi in chiaro: la si sostituisce con il solo cavo di rete
if [ -f /boot/firmware/network-config ]; then
  cat > /boot/firmware/network-config <<'NET'
# Rete del nodo Mirto: solo cavo (il Wi-Fi si configura con  mirto-nodo wifi)
network:
  version: 2
  ethernets:
    eth0:
      dhcp4: true
      optional: true
NET
  echo "    network-config della microSD: ora solo cavo, senza password del Wi-Fi"
fi
for f in /etc/netplan/*.yaml; do
  [ -f "$f" ] && grep -q -i "wifis" "$f" && rm -f "$f" && echo "    eliminato $f"
done
if grep -q -r -i -l "psk" /etc/NetworkManager/system-connections /boot/firmware/network-config 2>/dev/null; then
  echo "    ATTENZIONE: resta una password Wi-Fi in qualche file di rete"
fi

step "nome generico (al primo avvio diventa mirto-node-XXXX)"
echo "mirto-node" > /etc/hostname
sed -i "s/^127\.0\.1\.1.*/127.0.1.1\tmirto-node/" /etc/hosts

# Verifica finale: in $CONF deve restare solo quello che l'immagine deve avere. Se resta altro lo si
# scrive nel resoconto, che si legge dal Mac: un'immagine si copia una volta e si manda a tutti.
RESTA="$(ls -A "$CONF" 2>/dev/null | grep -v -x -F "$TIENI" | tr '\n' ' ')"
[ -z "$RESTA" ] || echo "    ATTENZIONE: in $CONF resta ancora: $RESTA"

touch "$CONF/primo-avvio"
systemctl enable mirto-firstboot >/dev/null 2>&1
echo "Fatto. Spengo: copia la microSD sul Mac PRIMA di riaccendere il Raspberry."
sync
sleep 2
systemctl poweroff
