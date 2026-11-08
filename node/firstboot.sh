#!/usr/bin/env bash
# Primo avvio dell'immagine di Mirto Node (servizio mirto-firstboot, solo se esiste /etc/mirto-node/primo-avvio).
# Rende unico questo Raspberry: identità della macchina, nome in rete, chiavi SSH, chiave e certificato del
# nodo, password dell'utente e della camera. Poi il nodo parte con l'abbinamento aperto ("Nuovo nodo trovato"
# in Mirto), che non scade finché il nodo non viene abbinato la prima volta.
#
# Le cartelle sono variabili perché questo copione **si prova senza Raspberry**: le prove lo eseguono in una
# radice finta, con i comandi di sistema simulati (tests/test_node_firstboot.py). Il 20/10 un difetto
# nell'ordine dei passi è costato mezz'ora di prove su hardware vero: quel tipo di errore ora si vede qui.
set -euo pipefail
CONF="${MIRTO_CONF:-/etc/mirto-node}"
DEST="${MIRTO_DEST:-/opt/mirto-node}"
BOOT="${MIRTO_BOOT:-/boot/firmware}"
ETC="${MIRTO_ETC:-/etc}"
SYS="${MIRTO_SYS:-/sys}"
[ -f "$CONF/primo-avvio" ] || exit 0
echo "Mirto Node: primo avvio"

# 1. identità della macchina (la pulizia l'ha svuotata)
[ -s "$ETC/machine-id" ] || systemd-machine-id-setup

# 2. nome in rete unico: mirto-node-<ultime 4 cifre dell'indirizzo del cavo di rete>, ma solo se il nome è
#    quello generico dell'immagine: un nome scelto in Raspberry Pi Imager resta com'è.
#    La parte fissa «mirto-node-» serve a Mirto per riconoscere un nodo nuovo, non ancora battezzato:
#    è lo stesso prefisso del servizio e delle cartelle (mirto-node.service, /etc/mirto-node).
#    «mirto-nodo» è il nome generico delle immagini preparate prima del 20/10: va riconosciuto anche quello.
OLD="$(cat "$ETC/hostname" 2>/dev/null || hostname)"
if [ "$OLD" = "mirto-node" ] || [ "$OLD" = "mirto-nodo" ] || [ -z "$OLD" ]; then
  MAC="$(tr -d ':\n' < "$SYS/class/net/eth0/address" 2>/dev/null || true)"
  SUF="${MAC: -4}"
  [ -n "$SUF" ] || SUF="$(openssl rand -hex 2)"
  NEW="mirto-node-$SUF"
  echo "$NEW" > "$ETC/hostname"
  hostname "$NEW"
  if grep -q "^127\.0\.1\.1" "$ETC/hosts"; then
    sed -i "s/^127\.0\.1\.1.*/127.0.1.1\t$NEW/" "$ETC/hosts"
  else
    printf "127.0.1.1\t%s\n" "$NEW" >> "$ETC/hosts"
  fi
  echo "  nome: $OLD → $NEW"
else
  echo "  nome: $OLD (scelto in Raspberry Pi Imager, resta)"
fi

# 2c. password dell'utente: casuale, diversa per ogni microSD, scritta sulla partizione di avvio.
#     L'immagine pubblica non contiene nessuna password (S-05): un nodo appena acceso con credenziali note
#     è un nodo di chiunque sia sulla rete. Se chi ha scritto la microSD ha scelto una password in Raspberry
#     Pi Imager, quella resta e qui non si tocca niente.
ACCESSO="$BOOT/mirto-accesso.txt"
UTENTE="$(awk -F: '$3 >= 1000 && $3 < 60000 {print $1; exit}' "$ETC/passwd" || true)"
if [ -n "$UTENTE" ]; then
  STATO="$(passwd -S "$UTENTE" 2>/dev/null | awk '{print $2}')"
  if [ "$STATO" = "L" ] || [ "$STATO" = "NP" ]; then
    PAROLA="$(openssl rand -hex 9 | sed 's/.\{6\}/&-/g; s/-$//')"
    echo "$UTENTE:$PAROLA" | chpasswd
    passwd -u "$UTENTE" >/dev/null 2>&1 || true
    # Niente «umask 077» qui: resterebbe per tutto il resto del copione, compresa l'installazione che
    # viene dopo, e i file che crea nascerebbero 0600. Il 20/10 è andata proprio così: i due file di
    # avahi sono nati illeggibili per il demone, e il nodo non si è annunciato in rete. Al foglio bastano
    # i permessi che gli mette il chmod qui sotto.
    cat > "$ACCESSO" <<TXT
Mirto Node - accesso a questo nodo

  utente:   $UTENTE
  password: $PAROLA

Questa password e' stata generata da questo nodo al primo avvio: e' diversa su ogni microSD e non la
sa nessun altro - nemmeno chi te l'ha dato. Serve per entrare da tastiera o con
  ssh $UTENTE@$(cat "$ETC/hostname" 2>/dev/null).local

CONSERVALA. Se perdi il PIN dell'abbinamento, questa password e' il solo modo di farne uno nuovo:
  ssh $UTENTE@$(cat "$ETC/hostname" 2>/dev/null).local
  sudo mirto-nodo abbina        <- mostra un PIN nuovo
(Se da Mirto hai dato un nome al nodo, l'indirizzo diventa  mirto-<nome>.local .)
Senza di lei, un nodo che ha perso il PIN e non e' ancora abbinato si recupera solo riscrivendo la
microSD. Tienila dove tieni le altre password di casa, non attaccata al dispositivo: chi la legge
puo' entrare nel nodo. Per cambiarla, dopo essere entrato:  passwd
TXT
    chmod 600 "$ACCESSO" 2>/dev/null || true
    echo "  password dell'utente $UTENTE: generata, scritta in $ACCESSO"
  else
    echo "  password dell'utente $UTENTE: scelta in Raspberry Pi Imager, resta"
  fi
else
  # Successo il 20/10: nell'immagine non c'era nessun utente (li toglie make_public_image.sh) e
  # quello di cloud-init non c'era ancora. Qui si saltava tutto in silenzio, e il nodo restava una
  # scatola chiusa: nessuno poteva entrarci, né il padrone né noi. Adesso l'immagine l'utente ce
  # l'ha già; se comunque manca, il foglio deve dirlo invece di non esistere.
  cat > "$ACCESSO" <<TXT
Mirto Node - accesso a questo nodo

  NESSUN UTENTE su questa microSD: non c'e' nessun modo di entrare nel nodo da terminale.

Il nodo funziona lo stesso - si annuncia, si abbina a Mirto col PIN di mirto-pin.txt e fa il suo
lavoro - ma non si puo' entrarci per cambiare qualcosa.
Per averne uno: riscrivi la microSD con Raspberry Pi Imager impostando utente e password nelle
"impostazioni di personalizzazione", oppure usa un'immagine Mirto piu' recente, che l'utente ce
l'ha gia' dentro.
TXT
  chmod 600 "$ACCESSO" 2>/dev/null || true
  echo "  ATTENZIONE: nessun utente nel sistema, password non generata (scritto in $ACCESSO)"
fi

# 3. chiavi SSH del Raspberry (nuove per ogni copia dell'immagine)
ls "$ETC"/ssh/ssh_host_*_key >/dev/null 2>&1 || ssh-keygen -A

# 4. chiave e certificato del nodo, password della camera, configurazioni: le ricrea l'installazione
"$DEST/install.sh" --primo-avvio

# 6. il sistema si allarga a tutta la microSD. **Per ultimo, e in sottofondo**: l'immagine pubblica è
#    rimpicciolita a pochi GB e su una scheda da 64 GB «resize2fs» ci mette dai cinque ai quindici minuti
#    su un Raspberry lento. Stava all'inizio, e il 20/10 ha tenuto in ostaggio tutto il resto: niente
#    password, niente PIN, niente servizio, per un quarto d'ora, con il nodo che sembrava morto. Ora il
#    nodo è già in piedi e abbinabile mentre il disco cresce.
ROOTDEV="$(findmnt -n -o SOURCE /)"
PART="$(basename "$ROOTDEV")"
DISK="$(lsblk -no PKNAME "$ROOTDEV" 2>/dev/null || true)"
if [ -n "$DISK" ] && [ -r "$SYS/class/block/$PART/partition" ]; then
  N="$(cat "$SYS/class/block/$PART/partition")"
  END=$(( $(cat "$SYS/class/block/$PART/start") + $(cat "$SYS/class/block/$PART/size") ))
  if [ $(( $(cat "$SYS/block/$DISK/size") - END )) -gt 204800 ]; then
    echo "  allargo il sistema a tutta la microSD (in sottofondo: possono volerci minuti)"
    echo ", +" | sfdisk --force --no-reread -N "$N" "/dev/$DISK" >/dev/null 2>&1 || true
    partx -u -n "$N" "/dev/$DISK" 2>/dev/null || true
    systemd-run --unit=mirto-allarga --collect --quiet resize2fs "$ROOTDEV" 2>/dev/null \
      || resize2fs "$ROOTDEV" >/dev/null 2>&1 \
      || echo "  (allargamento non riuscito: sudo raspi-config → Advanced → Expand Filesystem)"
  fi
fi

rm -f "$CONF/primo-avvio"
# l'annuncio in rete (avahi) può essere partito col nome vecchio: riparte col nome nuovo, senza aspettarlo
systemctl --no-block restart avahi-daemon 2>/dev/null || true
echo "Mirto Node: primo avvio completato"
