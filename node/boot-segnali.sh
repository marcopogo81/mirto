#!/usr/bin/env bash
# Esegue i lavori che il servizio del nodo non può fare da solo.
#
# Il servizio gira come utente «mirto-node», senza privilegi (azione S-03), e la partizione di
# avvio della microSD è FAT e appartiene a root: non può scriverci né cancellarci. Invece di dargli
# i permessi su tutta la microSD per due file, il nodo lascia un biglietto in /etc/mirto-node e
# questo script — avviato da mirto-boot-segnali.path, che gira da root — lo esegue.
#
# I tre lavori:
#   /boot/firmware/mirto-abbina   file lasciato da una persona sulla microSD per riaprire
#                                   l'abbinamento → diventa il segnale «apri-abbinamento» in $CONF
#   $CONF/pin-da-mostrare           il PIN da scrivere sulla microSD al primo avvio
#   $CONF/pin-da-togliere           il PIN non vale più: via dalla microSD, dove lo leggerebbe chiunque
#   $CONF/registrazioni-da-preparare  un disco appena collegato appartiene a root: la cartella delle
#                                   registrazioni va creata e data al gruppo di MediaMTX
#   $CONF/disco-da-rimontare        il disco delle registrazioni staccato e riattaccato: la riga di
#                                   fstab monta solo all'avvio, qui si rifa' il mount - ma solo per
#                                   un punto di mount che in fstab c'e' gia'
#   $CONF/camera-da-applicare       la camera scelta nel wizard (POST /configura/camera): MediaMTX,
#                                   annuncio della camera e riavvio dei servizi
#   $CONF/audio-da-fare             «applica», «cerca» o «abbina AA:BB:…» (POST /configura/audio):
#                                   l'uscita della sirena e il Bluetooth, che vogliono privilegi
#   $CONF/wifi-da-fare              «cerca» o «collega» (POST /configura/wifi). La rete e la password
#                                   stanno in wifi-da-collegare.json, che legge il comando, non questo
#                                   copione: una password non deve passare da una riga di shell
#   $CONF/disco-da-fare             «cerca», «usa /dev/sda1», «prepara /dev/sda» o «applica»
#                                   (POST /configura/disco). «prepara» FORMATTA: qui si accetta solo
#                                   la forma giusta e il comando ricontrolla da sé che sia un disco
#                                   USB utilizzabile, mai quello da cui parte il sistema
#   $CONF/pacchetto-da-installare   il codice del nodo mandato da Mirto (POST /aggiorna, N-02):
#                                   il servizio l'ha gia' verificato e scompattato in $CONF/nuovo,
#                                   qui si esegue il suo install.sh, che e' rieseguibile
set -euo pipefail
CONF=/etc/mirto-node
BOOT=/boot/firmware
UTENTE=mirto-node

if [ -e "$BOOT/mirto-abbina" ]; then
  rm -f "$BOOT/mirto-abbina"
  install -o "$UTENTE" -g "$UTENTE" -m 600 /dev/null "$CONF/apri-abbinamento"
  logger -t mirto-nodo "abbinamento richiesto dalla microSD"
fi

if [ -s "$CONF/pin-da-mostrare" ]; then
  cp "$CONF/pin-da-mostrare" "$BOOT/mirto-pin.txt"
  sync   # la partizione di avvio è FAT e non ha nessun giornale: staccare la corrente perde il file
  rm -f "$CONF/pin-da-mostrare"
fi

if [ -e "$CONF/pin-da-togliere" ]; then
  rm -f "$BOOT/mirto-pin.txt"
  sync   # FAT senza giornale: un PIN «cancellato» ma non ancora sul disco tornerebbe vivo
  rm -f "$CONF/pin-da-togliere"
fi

if [ -s "$CONF/wifi-da-fare" ]; then
  RIGA="$(head -1 "$CONF/wifi-da-fare" | tr -cd 'a-z' | cut -c1-10)"
  rm -f "$CONF/wifi-da-fare"
  case "$RIGA" in
    cerca)   /usr/local/bin/mirto-nodo wifi --cerca >/dev/null 2>&1 \
               && logger -t mirto-nodo "reti Wi-Fi cercate (chiesto da Mirto)" \
               || logger -t mirto-nodo "ricerca delle reti NON riuscita" ;;
    collega) /usr/local/bin/mirto-nodo wifi --collega >/dev/null 2>&1 \
               && logger -t mirto-nodo "collegamento Wi-Fi eseguito (chiesto da Mirto)" \
               || logger -t mirto-nodo "collegamento Wi-Fi NON riuscito" ;;
    *)       logger -t mirto-nodo "richiesta Wi-Fi non riconosciuta" ;;
  esac
fi

if [ -s "$CONF/audio-da-fare" ]; then
  # Il contenuto è una riga sola e viene da un servizio senza privilegi: qui si gira da root, quindi si
  # accetta solo quello che si riconosce, e l'indirizzo della cassa passa solo se ha la forma giusta.
  RIGA="$(head -1 "$CONF/audio-da-fare" | tr -cd 'a-zA-Z0-9: ' | cut -c1-40)"
  rm -f "$CONF/audio-da-fare"
  case "$RIGA" in
    applica) /usr/local/bin/mirto-nodo audio --applica >/dev/null 2>&1 \
               && logger -t mirto-nodo "uscita audio applicata (chiesto da Mirto)" \
               || logger -t mirto-nodo "uscita audio NON applicata" ;;
    cerca)   /usr/local/bin/mirto-nodo audio --cerca >/dev/null 2>&1 \
               && logger -t mirto-nodo "casse Bluetooth cercate (chiesto da Mirto)" \
               || logger -t mirto-nodo "ricerca delle casse NON riuscita" ;;
    "dimentica "[0-9A-Fa-f][0-9A-Fa-f]:*)
             /usr/local/bin/mirto-nodo audio --dimentica "${RIGA#dimentica }" >/dev/null 2>&1 \
               && logger -t mirto-nodo "cassa dimenticata (chiesto da Mirto)" \
               || logger -t mirto-nodo "cassa NON dimenticata" ;;
    "abbina "[0-9A-Fa-f][0-9A-Fa-f]:*)
             MAC="${RIGA#abbina }"
             # Il motivo finisce nel registro: il 21/10 qui c'era >/dev/null 2>&1 e il registro diceva
             # solo «cassa … NON abbinata», mentre il perche' (lo diceva bluetoothctl) andava perso.
             if USCITA="$(/usr/local/bin/mirto-nodo audio --abbina "$MAC" 2>&1)"; then
               logger -t mirto-nodo "cassa $MAC abbinata (chiesto da Mirto)"
             else
               logger -t mirto-nodo "cassa $MAC NON abbinata: $(printf '%s' "$USCITA" | tr '\n' ' ' | tail -c 300)"
             fi ;;
    *)       logger -t mirto-nodo "richiesta audio non riconosciuta: «$RIGA»" ;;
  esac
fi

if [ -s "$CONF/pacchetto-da-installare" ]; then
  # N-02. Il pacchetto e' gia' stato verificato e scompattato dal servizio in $CONF/nuovo: firma
  # della richiesta, impronta del contenuto, nomi dell'archivio. Qui si fa la sola cosa che il
  # servizio non puo' fare, cioe' installare, e si scrive com'e' andata dove Mirto la trova.
  #
  # Non e' una barriera di sicurezza e non fingiamo che lo sia: chi comanda il servizio decide cosa
  # root installa. E' il patto di qualunque aggiornamento, e quello che lo tiene in piedi e' la
  # chiave dell'abbinamento, non questa riga.
  VERS="$(head -1 "$CONF/pacchetto-da-installare" | tr -cd 'a-zA-Z0-9.-' | cut -c1-32)"
  rm -f "$CONF/pacchetto-da-installare"
  NUOVO="$CONF/nuovo"
  if [ ! -f "$NUOVO/install.sh" ] || [ ! -f "$NUOVO/mirto_node/__init__.py" ]; then
    printf 'rotto\n%s\nnon ho trovato il codice da installare\n' "$VERS" > "$CONF/pacchetto-esito"
    logger -t mirto-nodo "aggiornamento $VERS: cartella incompleta, non installo"
  else
    logger -t mirto-nodo "aggiornamento a $VERS: installo"
    # L'uscita si tiene, e si manda a Mirto se l'installazione fallisce. Buttarla via e' costato il
    # 26/10: il primo aggiornamento vero e' fallito e l'unica cosa che si sapeva era «non e' andata a
    # buon fine» -- il motivo (due file che il pacchetto non portava) stava in una riga di install.sh
    # finita in /dev/null. Un avviso che dice che qualcosa non e' andato senza dire perche' lascia
    # soli, ed e' lo stesso difetto di un numero che non porta da nessuna parte.
    LOG="$(mktemp)"
    if ( cd "$NUOVO" && bash ./install.sh ) >"$LOG" 2>&1; then
      printf 'ok\n%s\ninstallato\n' "$VERS" > "$CONF/pacchetto-esito"
      logger -t mirto-nodo "aggiornamento a $VERS riuscito"
    else
      # Le ultime righe, non tutte: in un messaggio che finisce in una finestra ci sta poco, e la
      # riga che spiega e' l'ultima. Niente password qui dentro: install.sh non ne stampa, e quello
      # che passa sono percorsi e nomi di servizi.
      PERCHE="$(tail -n 6 "$LOG" | tr -d '\r' | tr '\n' ' ' | cut -c1-280)"
      printf 'fallito\n%s\nl'"'"'installazione non e'"'"' andata a buon fine, il nodo e'"'"' rimasto com'"'"'era: %s\n' \
        "$VERS" "${PERCHE:-nessun messaggio da install.sh}" > "$CONF/pacchetto-esito"
      logger -t mirto-nodo "aggiornamento a $VERS NON riuscito: resta la versione di prima"
      # Per intero nel registro di sistema: nella finestra ci stanno sei righe, qui no.
      sed -n '1,200p' "$LOG" | logger -t mirto-nodo-install
    fi
    rm -f "$LOG"
    chown "$UTENTE:$UTENTE" "$CONF/pacchetto-esito" 2>/dev/null || true
    chmod 600 "$CONF/pacchetto-esito" 2>/dev/null || true
    rm -rf "$NUOVO"
  fi
fi

if [ -e "$CONF/camera-da-applicare" ]; then
  # Il contenuto non si legge: la scelta sta già in node.toml, scritta dal servizio. Qui si esegue
  # soltanto - MediaMTX, annuncio in rete e riavvii - che senza privilegi il nodo non può fare.
  rm -f "$CONF/camera-da-applicare"
  if /usr/local/bin/mirto-nodo camera --applica >/dev/null 2>&1; then
    logger -t mirto-nodo "camera riconfigurata (chiesto da Mirto)"
  else
    logger -t mirto-nodo "camera NON riconfigurata: vedi  mirto-nodo stato"
  fi
fi

if [ -s "$CONF/disco-da-fare" ]; then
  # Una riga sola, da un servizio senza privilegi. Qui si gira da root e si formatta: si accetta solo
  # quello che si riconosce, e il nome del dispositivo passa solo se ha la forma di un dispositivo.
  # Il controllo che conta e' dentro mirto-nodo, che rifa' l'elenco dei dischi USB utilizzabili -
  # quello da cui parte il sistema non c'e' mai - e rifiuta tutto il resto.
  RIGA="$(head -1 "$CONF/disco-da-fare" | tr -cd 'a-z0-9/ ' | cut -c1-40)"
  rm -f "$CONF/disco-da-fare"
  case "$RIGA" in
    cerca)   /usr/local/bin/mirto-nodo disco --cerca >/dev/null 2>&1 \
               && logger -t mirto-nodo "dischi cercati (chiesto da Mirto)" \
               || logger -t mirto-nodo "ricerca dei dischi NON riuscita" ;;
    applica) /usr/local/bin/mirto-nodo disco --applica >/dev/null 2>&1 \
               && logger -t mirto-nodo "limiti della registrazione applicati (chiesto da Mirto)" \
               || logger -t mirto-nodo "limiti della registrazione NON applicati" ;;
    "usa /dev/"[a-z]*)
             DEV="${RIGA#usa }"
             /usr/local/bin/mirto-nodo disco --usa "$DEV" >/dev/null 2>&1 \
               && logger -t mirto-nodo "registrazione sul disco $DEV (chiesto da Mirto)" \
               || logger -t mirto-nodo "disco $DEV NON usato" ;;
    "prepara /dev/"[a-z]*)
             DEV="${RIGA#prepara }"
             logger -t mirto-nodo "preparo (FORMATTO) il disco $DEV: chiesto da Mirto"
             /usr/local/bin/mirto-nodo disco --prepara "$DEV" >/dev/null 2>&1 \
               && logger -t mirto-nodo "disco $DEV preparato e in uso" \
               || logger -t mirto-nodo "disco $DEV NON preparato" ;;
    *)       logger -t mirto-nodo "richiesta sul disco non riconosciuta: «$RIGA»" ;;
  esac
fi

if [ -s "$CONF/disco-da-rimontare" ]; then
  PUNTO="$(head -1 "$CONF/disco-da-rimontare")"
  # Si monta solo un punto che sta gia' in /etc/fstab, cioe' un disco che qualcuno ha scelto dal
  # pannello: un biglietto non deve poter far montare niente di nuovo. Se il disco non c'e' davvero il
  # mount fallisce e si tace - lo stato del nodo lo dice gia', e questo giro si ripete ogni minuto.
  if [ "${PUNTO#/}" != "$PUNTO" ] \
     && awk -v p="$PUNTO" '$1 !~ /^#/ && $2 == p { trovato = 1 } END { exit !trovato }' /etc/fstab; then
    # Una chiavetta staccata mentre era montata lascia il punto di mount dov'era, puntato a un
    # dispositivo che non esiste piu': finche' non lo si toglie, «mount» non ci rimette niente sopra.
    SORGENTE="$(findmnt -no SOURCE "$PUNTO" 2>/dev/null || true)"
    if [ -n "$SORGENTE" ] && [ ! -e "$SORGENTE" ]; then
      umount -l "$PUNTO" 2>/dev/null \
        && logger -t mirto-nodo "mount morto tolto: $SORGENTE non esiste piu' · $PUNTO"
    fi
    if ! mountpoint -q "$PUNTO" && mount "$PUNTO" 2>/dev/null; then
      logger -t mirto-nodo "disco delle registrazioni rimontato: $PUNTO"
    fi
  else
    logger -t mirto-nodo "richiesta di montare «$PUNTO» rifiutata: non e' in /etc/fstab"
  fi
  rm -f "$CONF/disco-da-rimontare"
fi

if [ -s "$CONF/registrazioni-da-preparare" ]; then
  CARTELLA="$(head -1 "$CONF/registrazioni-da-preparare")"
  case "$CARTELLA" in
    /*) mkdir -p "$CARTELLA" && chown -R mediamtx:mediamtx "$CARTELLA" \
          && chmod 2775 "$CARTELLA" "$(dirname "$CARTELLA")" \
          && logger -t mirto-nodo "cartella delle registrazioni preparata: $CARTELLA" ;;
    *)  logger -t mirto-nodo "cartella delle registrazioni rifiutata: «$CARTELLA» non è un percorso completo" ;;
  esac
  rm -f "$CONF/registrazioni-da-preparare"
fi
