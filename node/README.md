# Mirto Node

Trasforma un **Raspberry** in un nodo Mirto: **camera** (MediaMTX con password sullo stream, normale o NoIR),
**sirena** su cassa Bluetooth, jack, USB o HDMI e **luce di prova** (il LED verde del Raspberry). Si abbina a
Mirto con un clic e si regola con il comando `mirto-nodo`.

> Prima volta? La guida passo passo è il capitolo **12 «Nodi»** di `GUIDA_UTENTE.md` (12.1 «Preparare un nodo Raspberry»).

Parla con Mirto con lo stesso contratto del simulatore (`mirto/actions/simulator.py`): HTTPS con
certificato fissato in Mirto, ogni richiesta firmata (HMAC-SHA256), annuncio in rete `_mirto-node._tcp`.
Usa solo Python e programmi del sistema: nessuna libreria da installare con pip.

**Limiti applicati dal nodo**, qualunque cosa chieda Mirto: la sirena suona al massimo **3 minuti** per
volta e 3 minuti in totale ogni 30 minuti; un dissuasore **30 secondi** per volta e 2 minuti ogni ora; ogni
azione si spegne da sola allo scadere; "Ferma tutto" spegne tutto. Quando un budget è finito, il rifiuto
dice **quando riprende** — il nodo tiene il registro degli scatti della finestra, quindi è un calcolo — e
porta anche `motivo` e i secondi, così Mirto può dirlo nella lingua di chi legge. La **modalità Prank**
(`POST /prank`) toglie il budget dei dissuasori e nient'altro: restano i 30 secondi per scatto, scade da
sé, e "Ferma tutto" la spegne. Porte: 8443 (nodo, HTTPS), 8554 (camera, RTSP) e 8889 (camera nel browser).

**Limiti verso la rete**, che valgono per chiunque anche prima di sapere se ha una chiave: una connessione
che non parla cade dopo **15 secondi**; il corpo di una richiesta è al massimo **64 KB** (1 KB per
`/abbina`, 20 MB per `/audio/riproduci`, che porta un WAV) e un `Content-Length` negativo o non numerico
viene rifiutato subito con 400; senza le intestazioni della firma, o con una marca temporale fuori
tolleranza, il nodo risponde 401 **senza leggere il corpo**; oltre **16 richieste insieme** risponde 503.
La stretta di mano TLS avviene nel thread della richiesta, non nel ciclo che accetta le connessioni:
altrimenti basterebbe una connessione muta per fermare il nodo.

## 1. Installazione

Dal Mac, copia la cartella sul Raspberry ed esegui l'installazione:

```bash
scp -r ~/Documents/Mirto/node tuoutente@mirto-ingresso.local:
ssh tuoutente@mirto-ingresso.local
sudo ./node/install.sh
```

L'installazione mette tutto il necessario:
- il **servizio del nodo** (sirena, luce di prova);
- la **camera**: MediaMTX (versione fissata e verificata) in `/opt/mediamtx`, configurato da `node.toml`
  (sezione `[camera]`: risoluzione, fps, `noir = true` per la Camera Module NoIR) e protetto da **password**
  (`/etc/mirto-node/rtsp`, generata alla prima installazione: Mirto la riceve dal nodo, non serve scriverla);
- gli **annunci in rete** del nodo e della camera.

Alla fine compaiono **indirizzo**, **impronta del certificato** e il **PIN** di 6 cifre: servono in Mirto.
Per aggiornare il nodo basta ripetere gli stessi comandi: configurazione, chiave, certificato e password della
camera restano. Se prima avevi MediaMTX installato a mano (es. in `~/mediamtx`), l'installazione lo sostituisce
(riprendendo l'impostazione NoIR); la vecchia cartella si può cancellare.

Da Mac, il comando `ssh` va dato con `-t` se lanci l'installazione direttamente:
`ssh -t tuoutente@mirto-ingresso.local "sudo ./node/install.sh"`.

## 2. Configurazione guidata: `mirto-nodo`

L'installazione aggiunge il comando **`mirto-nodo`**: senza argomenti apre un menu, altrimenti:

| Comando | Cosa fa |
|---|---|
| `mirto-nodo stato` | servizi, camera, uscita audio, rete, temperatura, impronta del certificato |
| `mirto-nodo wifi` | elenca le reti e collega il nodo (la password la chiede il sistema, senza mostrarla); si fa anche da Mirto (Impostazioni → Nodi → *Wi-Fi del nodo…*), ricerca delle reti compresa |
| `mirto-nodo audio` | sceglie dove suona la sirena: **Bluetooth**, **jack 3,5 mm**, **USB** o **HDMI**, poi fa un din-don di prova; si fa anche da Mirto (Impostazioni → Nodi → *Audio del nodo…*), ricerca e abbinamento delle casse compresi |
| `mirto-nodo camera` | camera normale o **NoIR**, risoluzione, oppure camera spenta; si fa anche da Mirto (Impostazioni → Nodi → *Camera del nodo…*) |
| `mirto-nodo abbina` | riapre per 5 minuti l'abbinamento con Mirto (es. un secondo Mac). Si fa anche da Mirto: Impostazioni → Nodi → *Altri computer…*, che mostra il PIN mentre la finestra è aperta |
| `mirto-nodo nome Garage` | cambia l'**etichetta** del nodo, quella che si legge in Mirto; il nome in rete resta quello di fabbrica (`mirto-node-XXXX.local`). Dopo l'abbinamento conviene farlo da Mirto |
| `mirto-nodo disco` | SSD o chiavetta USB per la registrazione: lo prepara (ext4, solo dopo `CANCELLA`), lo monta in `/mnt/mirto-reg` e ci sposta la registrazione (vedi 3d). Si fa anche da Mirto (Impostazioni → Nodi → *Disco del nodo…*), formattazione compresa: lì la conferma è riscrivere il nome del dispositivo |
| `mirto-nodo prepara-immagine` | pulisce il nodo per farne l'immagine, poi lo spegne (vedi 3c) |

Se serve, chiede da solo la password di amministratore (`sudo`). Le scelte vanno in `/etc/mirto-node/node.toml`
(i commenti restano) e i servizi si riavviano da soli; Mirto si accorge dei cambi entro pochi minuti.

### Uscite audio

- **Bluetooth**: metti la cassa in abbinamento quando lo chiede; abbinamento, fiducia e collegamento li fa il
  comando. Il nodo la tiene sveglia e la ricollega se si stacca.
- **Jack 3,5 mm**: cassa amplificata o con ingresso AUX. È l'uscita più affidabile (niente batteria, niente
  abbinamento); il volume del Raspberry si regola con `sudo alsamixer`.
- **USB**: scheda audio o cassa USB; viene indicata per nome, quindi non cambia se aggiungi altre schede.
- **HDMI**: TV o monitor collegato al Raspberry.

Nell'elenco compaiono solo le schede che **sanno suonare**: un microfono USB non viene proposto come uscita
della sirena, anche se `/proc/asound/cards` lo scrive uguale alle altre.

> **Una cassa USB spenta non si può distinguere da una accesa.** Resta una scheda audio presente, ALSA accetta
> il suono e `aplay` esce con successo: la sirena suona nel vuoto e nessuno può accorgersene — né il nodo, né
> Mirto. Se non si sente niente, la prima cosa da guardare è l'interruttore della cassa.

A mano, in `node.toml`: `uscita = "bluetooth" | "jack" | "usb" | "hdmi" | "predefinito"` e, per le uscite via cavo,
`scheda = "<nome in /proc/asound/cards>"`.

Prova della sirena vera per 3 secondi, **volume basso**: `cd /opt/mirto-node && sudo python3 -m mirto_node --prova-sirena 3`

### Microfono e manopole della scheda (API 1.6)

Un **microfono USB** si accende da Mirto: *Impostazioni → Nodi → «Audio del nodo…» → Microfono del nodo*.
Si propongono solo le schede che sanno registrare. Il nodo scrive `[microfono]` in `node.toml` e riavvia il
proprio servizio: da quel momento Mirto riceve l'audio in diretta e riconosce i suoni (vetro rotto, allarme
fumo, cane). **L'audio non viene salvato sul nodo**, e arriva al computer solo mentre lo sta ascoltando.

Nella stessa finestra ci sono le **manopole vere** della cassa e del microfono, lette dalla scheda con
`amixer`: volume, muto, guadagno del microfono, automatismi. Si applicano subito (non serve «Applica») e il
nodo le ricorda da sé, perché ALSA le dimentica a ogni riavvio e salvarle in `/var/lib/alsa` vorrebbe i
privilegi di root.

La prima volta che il nodo prende in uso una scheda le mette a valori sensati: **microfono al massimo con
l'automatismo del guadagno spento**, cassa a volume alto, e qualunque manopola mutata accesa. Non è un
dettaglio da smanettoni: un microfono USB C-Media nasce col guadagno a 0 su 16 e l'automatismo acceso, e in
quello stato una voce arriva all'1% del fondo scala — il riconoscimento dei suoni non sente niente e non c'è
modo di capire perché. Misurato il 22/10 su un Raspberry Pi 4: automatismo acceso 14% del fondo scala,
spento **38%**, e un colpo di mani a due metri **50%**.

Se stacchi e riattacchi il dispositivo, il nodo rimette le manopole da sé: un dispositivo USB torna sempre ai
valori di fabbrica suoi.

## 3. Collegarlo a Mirto

**Senza chiave da copiare** (consigliato): l'abbinamento è aperto dopo l'installazione, dopo il primo avvio
dell'immagine e ogni volta che lanci `mirto-nodo abbina`. Serve il **PIN di 6 cifre** che il nodo mostra: lo
stampa `mirto-nodo abbina` (e `mirto-nodo stato` finché la finestra è aperta), e al primo avvio di
un'immagine è nel file `mirto-pin.txt` nella partizione di avvio della microSD.

**Quanto dura la finestra**: finché il nodo non è stato abbinato **nemmeno una volta**, non scade. Il PIN del
primo avvio viaggia stampato insieme al dispositivo, e un nodo acceso una settimana dopo deve essere ancora
abbinabile senza aprire un terminale. Dal primo abbinamento in poi ogni riapertura dura **5 minuti**.

1. In Mirto, nella colonna *Sistema*, compare **Nuovo nodo trovato → Abbina…** (oppure Impostazioni → Nodi →
   **Cerca nodi in rete**).
2. Controlla che l'**impronta** sia quella mostrata dal nodo (`mirto-nodo stato`), scrivi il **PIN**, scegli il
   nome e premi **Abbina**: Mirto riceve la chiave sulla connessione cifrata, dopo aver verificato il
   certificato. Le camere del nodo compaiono da sole.
3. Il **nome** lo chiede Mirto subito dopo l'abbinamento (`POST /configura/nome`, API 1.5): scrivendo
   "Garage" il nodo diventa *Garage* in Mirto e sul dispositivo. È solo un'**etichetta**: in rete il nodo
   resta `mirto-node-XXXX.local`, l'indirizzo salvato non si muove e nessun abbinamento si rompe. Dal
   terminale si può ancora (`mirto-nodo nome Garage`), ma lì tocca a te dire a Mirto il nome nuovo.
4. Impostazioni → Nodi → clic sul nodo → ramo **⚡ Azioni**: **Attiva** le proposte "Sirena" (con conferma su
   Telegram, dopo 10 s) e "Luci".
5. **Prova** sull'azione, poi **Salva**. Con la protezione attiva, una persona rilevata fa partire le azioni.

La finestra si chiude al primo abbinamento e **non** si riapre riavviando il servizio: per un secondo Mac o dopo
i 5 minuti di una riapertura, `mirto-nodo abbina` (riapre per 5 minuti con un PIN nuovo), oppure un file vuoto
**`mirto-abbina`** nella partizione di avvio della microSD (letto e cancellato all'accensione, utile senza
terminale: genera anche `mirto-pin.txt`). La chiave viene consegnata solo a chi è nella rete di casa, conosce
il PIN e ha davanti il certificato giusto. Dopo **5 PIN sbagliati** la finestra si chiude davvero, anche su un
nodo mai abbinato: il PIN sparisce anche dalla microSD e si riapre dal nodo.

**Una chiave per ogni abbinamento.** Ogni Mirto riceve una chiave sua, in `/etc/mirto-node/chiavi/` (cartella
`0700`, file `0600`), con accanto una scheda `.json` che dice **chi** l'ha chiesta, **da quale indirizzo** e
**quando**. `/stato` dice da chi e quando è stato abbinato il nodo, e Mirto lo mostra in Impostazioni → Nodi;
se compare un Mirto diverso dal proprio, arriva un avviso su Telegram.

| Comando | Cosa fa |
|---|---|
| `mirto-nodo abbina --elenco` | i Mirto abbinati, uno per riga: nome, indirizzo, data (anche in `mirto-nodo stato`, e in Mirto: *Altri computer…*) |
| `mirto-nodo abbina --revoca NOME` | stacca **un solo** Mirto (il nome è quello dell'elenco); gli altri continuano a funzionare. Da Mirto si fa in *Altri computer…*, tranne **l'ultimo**: quello si stacca solo da qui, perché lascerebbe il nodo senza nessun Mirto |
| `mirto-nodo abbina --revoca installazione` | ritira la chiave dell'installazione |
| `mirto-nodo abbina --revoca-tutti` | stacca tutti e ritira la chiave dell'installazione |

**La chiave dell'installazione è solo un ponte.** Quella scritta da `install.sh` in `/etc/mirto-node/chiave` è
condivisa: chi la conosce comanda il nodo. Resta valida perché i nodi abbinati prima della versione 0.6 non
smettano di funzionare, ma dal **primo abbinamento con chiave propria** parte il conto: `mirto-nodo stato` e
Mirto chiedono di ritirarla e, se nessuno lo fa, **dopo 7 giorni si ritira da sola**. Ritirarla cancella il file
e lascia `chiave-revocata`, così non torna nemmeno reinstallando.

## 3b. La camera dello stesso Raspberry

Il nodo dichiara a Mirto le camere che trasmette (le legge dall'annuncio `mirto-cam.service` di MediaMTX),
con porta, percorso ed eventuali credenziali (`/etc/mirto-node/rtsp`, riga `utente:password`). Mirto **crea
da solo** la camera del nodo: compare in *Tutte le cam* sotto il nome del nodo, segue l'indirizzo del nodo e non
appare tra le "Trovate in rete". Una camera aggiunta in passato a mano viene adottata dal nodo senza rifare nulla.

## 3d. Registrazione sul nodo (API 1.4)

Il nodo registra da solo la sua camera, indipendentemente da Mirto: lo fa **MediaMTX** (`record`, fMP4, spezzoni da
1 minuto in `<cartella>/cam/AAAAMMGG-hhmmss.mp4`, ora UTC: il servizio gira con `TZ=UTC0`). Il servizio del nodo
(`recording.py`) ogni minuto:

- accende o spegne la registrazione con l'API locale di MediaMTX (`127.0.0.1:9997`): se il disco USB configurato non è
  montato la ferma, così MediaMTX non scrive sulla microSD al suo posto;
- tiene libero `minimo_libero_percento` (10) del disco e non supera `massimo_gb`, cancellando dal file più vecchio (mai
  quello in scrittura): **anello**, non si blocca mai per spazio. Sulla microSD il limite di serie è 2 GB.

Sezione `[registrazione]` di `node.toml`: `attiva`, `disco` (punto di montaggio; vuoto = microSD), `massimo_gb`,
`minimo_libero_percento`. `mirto-nodo disco` la compila da sé; da Mirto, *Disco del nodo…* (`GET`/`POST /configura/disco`, API 1.5) fa le stesse cose: `cerca` i dischi collegati, `usa` uno già
formattato bene, `prepara` (**formatta**) quello che non lo è, `limiti` per accendere/spegnere la
registrazione e cambiare i due limiti dell'anello. La formattazione vale solo per un disco che compare
nell'ultima ricerca — che non propone mai quello da cui parte il sistema — e solo se la richiesta ripete
il nome del dispositivo; il comando da root ricontrolla tutto da sé prima di cancellare.

API (firmate come le altre): `GET /registrazioni?da=EPOCH&a=EPOCH` → `{"spezzoni": [{nome, inizio, fine, byte}],
"stato": {…}}`; `GET /registrazioni/file?nome=AAAAMMGG-hhmmss.mp4` → il file (solo nomi validi, niente percorsi).

### L'audio del microfono nelle registrazioni (API 1.13) — **spento di serie**

`[registrazione] audio = true` (da Mirto: *Nodi → Disco → «Registra anche l'audio»*, `POST /configura/disco`
`{"azione": "audio", "attivo": true}`). Acceso, il servizio del nodo avvia un **ffmpeg** che legge il video da
`rtsp://…/cam`, prende il PCM del microfono da **stdin** e pubblica `rtsp://…/cam-audio` con le due tracce
(`flusso_audio.py`). Da lì in avanti il nodo registra **quel** percorso, e Mirto registra quel flusso senza
scartare l'audio.

Tre cose che non sono dettagli:

- l'audio arriva da **stdin**, non da ALSA: il microfono del nodo lo legge un `arecord` solo, condiviso fra chi
  ascolta (`mic.py`), e un ffmpeg che aprisse ALSA per conto suo sarebbe un secondo lettore — su una scheda che
  non si apre due volte, accendere l'audio spegnerebbe il riconoscimento dei suoni;
- il flusso con l'audio è un percorso **a parte**: se quel ffmpeg muore si perde l'audio, non la camera. Il nodo
  se ne accorge al controllo dopo (un minuto) e **torna a registrare il video**;
- **le registrazioni di prima restano dove sono** (`<cartella>/cam/`), mute ma leggibili: `GET /registrazioni` e
  l'anello guardano tutt'e due le cartelle. Se ne guardassero una sola, accendere l'audio farebbe sparire mezzo
  archivio dalla finestra *Rivedi* e l'anello non conterebbe più lo spazio occupato dall'altra.

Serve `ffmpeg` sul nodo: `install.sh` lo installa quando c'è rete e **va avanti** se non ci riesce (l'audio è
spento di serie, e un aggiornamento che fallisce per un programma che quel nodo non userà mai lascerebbe
indietro i nodi). Se manca, `POST /configura/disco` con `azione: "audio"` **rifiuta** e dice perché — come
rifiuta se il microfono del nodo è spento: chi accende questa cosa lo fa credendo di aver registrato l'audio di
quello che è successo, e scoprire il contrario dopo è peggio che non poterla accendere.

> **L'audio ha regole molto più severe del video.** In Italia il Garante considera la registrazione audio nella
> videosorveglianza eccedente nella gran parte dei casi. Per questo è spento di serie, si accende per nodo con
> una scelta esplicita, e accanto alla spunta c'è scritto cosa comporta.
`/stato` contiene `registrazione` (disco, spazio, da quando, errore); `/info` ha `"registrazione": true`.

## 3c. Immagine del nodo (per prepararne altri)

1. **Backup** della microSD attuale sul Mac (vedi la guida personale): serve per tornare indietro.
2. `mirto-nodo prepara-immagine` (bisogna scrivere `PREPARA`): cancella segreti e stato del nodo, casse
   Bluetooth, reti Wi-Fi (anche quella scritta da Raspberry Pi Imager nella partizione di avvio), chiavi SSH,
   identità della macchina, registri; poi spegne. Resoconto in `mirto-pulizia.txt` sulla microSD.

   > **Come si capisce che è spento.** Il LED **verde** si spegne e `ssh` non risponde più: quello è il
   > sistema fermo. Il LED **rosso resta acceso** e la ventola continua a girare, perché su un Raspberry
   > `poweroff` ferma il sistema ma non toglie corrente alla scheda: la spia rossa è quella dei 5 V e la
   > ventola del case è attaccata direttamente ad essi. Provato su un Pi 3B il 20/10: sembra non spento, ed
   > è fermo. Aspetta il LED verde spento, poi stacca la corrente.
3. Copia la microSD sul Mac **prima** di riaccendere: quella copia è l'immagine.
4. Al primo avvio di ogni copia (`mirto-firstboot`): nome `mirto-node-XXXX` (dall'indirizzo del cavo di rete),
   chiavi SSH, chiave, certificato e password della camera nuovi; abbinamento aperto finché il nodo non viene
   abbinato, con il PIN scritto in `mirto-pin.txt` nella partizione di avvio della microSD.
5. Col cavo di rete: tutto da Mirto (Impostazioni → Nodi: *Wi-Fi del nodo…*, *Camera del nodo…*, *Audio del nodo…*, e il nome si chiede subito dopo l'abbinamento), oppure sul Raspberry `mirto-nodo wifi`, `mirto-nodo nome Garage` (prima di abbinarlo), `mirto-nodo audio`.

**Immagine pubblica** (da distribuire): dall'immagine del punto 3, su Linux come amministratore (va bene il
Raspberry stesso, con almeno 12 GB liberi):
```bash
sudo ./node/make_public_image.sh immagine-nodo-AAAA-MM-GG.img.gz
```
Lavora su una copia: toglie utenti e chiavi SSH, azzera cloud-init (così Raspberry Pi Imager può impostare utente,
Wi-Fi e SSH di chi la scarica), rimette la rete "solo cavo", controlla che non restino password, la rimpicciolisce
(al primo avvio si riallarga a tutta la microSD) e crea `mirto-node-AAAA-MM-GG.img.xz` con la sua impronta
SHA-256.

**Nessuna password viaggia dentro l'immagine.** L'utente `mirto` è **dentro l'immagine**, bloccato e senza
password (prima lo creava cloud-init al primo avvio: il 20/10 il primo nodo nato dall'immagine si è trovato
senza nessun utente, perché la password veniva generata prima che l'utente esistesse). Il
**primo avvio gli dà una password casuale, diversa per ogni microSD**, scritta in
`/boot/firmware/mirto-accesso.txt`: si legge rimettendo la microSD in un computer, oppure da tastiera sul
Raspberry. Chi invece sceglie una password in Raspberry Pi Imager se la tiene — il primo avvio non la tocca.
**Quella password si conserva, non si cancella.** Se il PIN dell'abbinamento va perso e il nodo non è
ancora abbinato, entrare col terminale e lanciare `mirto-nodo abbina` è l'unico modo di farne uno nuovo:
senza, si riscrive la microSD. Va tenuta dove si tengono le altre password di casa — non attaccata al
dispositivo: chi ha in mano la microSD ha in mano il nodo, come per qualsiasi Raspberry. È per questo che
il foglio consegnato col dispositivo porta PIN, utente e password insieme.

## 4. Controlli

| Cosa | Comando sul Raspberry |
|---|---|
| Riepilogo di tutto | `mirto-nodo stato`, oppure in Mirto: Impostazioni → Nodi → *Stato e diagnosi…*, che dice **cosa non va** col rimedio accanto e ha un pulsante per copiare un rapporto da incollare in un messaggio (senza PIN né password) |
| Stato del servizio | `systemctl status mirto-node --no-pager -l` |
| Registro | `sudo journalctl -u mirto-node -n 50 --no-pager` |
| Camera (MediaMTX) | `systemctl status mediamtx --no-pager -l` |
| Password della camera | `sudo cat /etc/mirto-node/rtsp` (per vederla nel browser: `http://<nodo>.local:8889/cam`) |
| Rinominare un nodo già abbinato | in Mirto: Impostazioni → Nodi → *Rinomina il nodo…* (`POST /configura/nome`): cambia l'etichetta sul nodo e in Mirto, e la camera e le azioni che portavano solo il nome vecchio lo seguono. L'indirizzo in rete non cambia. Sul nodo: `mirto-nodo nome Garage` |
| Aggiungere un secondo computer | in Mirto: Impostazioni → Nodi → *Altri computer…* (`GET`/`POST /configura/abbinamenti`, API 1.5), oppure `mirto-nodo abbina` |
| Scegliere o preparare il disco delle registrazioni | `mirto-nodo disco`, oppure in Mirto: Impostazioni → Nodi → *Disco del nodo…* (`POST /configura/disco`, API 1.5) |
| Cambiare risoluzione, fps, NoIR | `mirto-nodo camera`, oppure in Mirto: Impostazioni → Nodi → *Camera del nodo…* (`POST /configura/camera`, API 1.5) |
| Mettere il nodo sul Wi-Fi | in Mirto: Impostazioni → Nodi → *Wi-Fi del nodo…* (`GET`/`POST /configura/wifi`, API 1.5), oppure `mirto-nodo wifi`. Le reti sono quelle che vede **il nodo**: lascia il cavo attaccato finché non vedi l'indirizzo nuovo |
| Impronta del certificato | `cd /opt/mirto-node && sudo python3 -m mirto_node --impronta` |
| Chiavi degli abbinamenti | `sudo ls -l /etc/mirto-node/chiavi/` (una per Mirto abbinato) |
| PIN in corso | `mirto-nodo stato` (riga *Abbinamento*, solo a finestra aperta) |
| Chiave dell'installazione (nodi vecchi) | `sudo cat /etc/mirto-node/chiave` |
| Cassa collegata? | `bluetoothctl info AA:BB:CC:DD:EE:FF \| grep Connected` |
| Riavviare | `sudo systemctl restart mirto-node` |

Le casse Bluetooth si spengono da sole dopo un po' di silenzio: il nodo manda ogni 4 minuti un suono
impercettibile per tenerle sveglie (`tieni_sveglia_minuti` in `node.toml`) e, se la cassa si è scollegata,
prova a ricollegarla. Se la cassa è spenta, Mirto riceve l'errore "sirena non disponibile" e lo segnala su Telegram.
