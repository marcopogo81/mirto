# Mirto – Guida utente

Mirto è un programma di videosorveglianza per **macOS** e **Windows** che lavora interamente sul computer
di casa: riceve il video delle camere, riconosce persone, animali, veicoli e suoni con modelli di
intelligenza artificiale locali e invia gli avvisi su **Telegram**, via **email** e, facoltativamente, via
**SMS**. Con i **nodi** (per esempio un Raspberry) aggiunge camere, sirene, luci e dissuasori.

Immagini e registrazioni restano sul computer: escono soltanto le foto e le clip degli avvisi.

> **La guida dentro l'app.** Si apre da **Aiuto → Guida utente**. In ogni scheda delle **Impostazioni** il
> collegamento **📖 Guida completa** porta direttamente al capitolo che la descrive.

---

## Indice

- [1. Introduzione](#1-introduzione)
- [2. Installazione su macOS](#2-installazione-su-macos)
- [2b. Installazione su Windows](#2b-installazione-su-windows)
- [3. Primo avvio](#3-primo-avvio)
- [4. La finestra principale](#4-la-finestra-principale)
- [5. Camere](#5-camere)
- [6. Protezione e avvisi](#6-protezione-e-avvisi)
- [7. Notifiche](#7-notifiche) – [7.1 Telegram](#71-telegram) · [7.2 Email](#72-email) · [7.3 SMS](#73-sms)
- [8. Riconoscimento (AI)](#8-riconoscimento-ai) – [8.1 Camere](#81-camere-cosa-riconoscere) · [8.2 Microfoni](#82-microfoni-i-suoni)
- [9. Registrazione e Rivedi](#9-registrazione-e-rivedi)
- [10. Programmazione](#10-programmazione)
- [11. Presenza](#11-presenza)
- [12. Nodi](#12-nodi)
- [13. Azioni](#13-azioni)
- [14. Impostazioni generali e funzionamento continuo](#14-impostazioni-generali-e-funzionamento-continuo)
- [15. Prove e diagnostica](#15-prove-e-diagnostica)
- [16. Risoluzione dei problemi](#16-risoluzione-dei-problemi)
- [17. Sicurezza, privacy e condizioni d'uso](#17-sicurezza-privacy-e-condizioni-duso)
- Appendici: [A. Comandi Telegram](#appendice-a-comandi-telegram) ·
[B. Installazione dai sorgenti e riga di comando](#appendice-b-installazione-dai-sorgenti-e-riga-di-comando) ·
[C. Comandi del nodo](#appendice-c-comandi-del-nodo) ·
[D. Dove sono i dati](#appendice-d-dove-sono-i-dati) ·
[E. Valori predefiniti e limiti](#appendice-e-valori-predefiniti-e-limiti)

---

## 1. Introduzione

### Come funziona

1. Mirto riceve il video dalle camere che hai attivato: camera integrata, camere USB, iPhone (Continuity
   Camera), camere di rete RTSP/HTTP e camere dei nodi.
2. Quando l'immagine cambia, il modello di riconoscimento controlla il fotogramma. Se riconosce ciò che hai
   scelto di sorvegliare (di serie una **persona**), crea un **evento** con foto e, se la registrazione è
   attiva, una clip video.
3. Se la **protezione** è attiva, l'evento diventa un **avviso**: foto su Telegram ed email, eventuale SMS ed
   eventuali **azioni** sui nodi (sirena, luci, suoni).

### Requisiti

| Requisito | Dettaglio |
|---|---|
| Sistema operativo | macOS (Apple Silicon o Intel) oppure Windows 10/11 |
| Camere | Almeno una camera integrata, USB, di rete o di un nodo |
| Rete | Connessione a internet per Telegram, email e il primo download dei modelli |
| Account | Telegram (consigliato), un account email per gli avvisi; SMS facoltativi tramite un provider |
| Nodi (facoltativi) | Raspberry Pi 4 o 5 con Raspberry Pi OS Lite 64-bit (vedi [12.1](#121-preparare-un-nodo-raspberry)) |

Tempo indicativo per la prima configurazione: 30 minuti.

---

## 2. Installazione su macOS

1. Scarica il file `.dmg` dalla [pagina delle Release](https://github.com/marcopogo81/mirto/releases).
2. Aprilo e trascina **Mirto** nella cartella **Applicazioni**.
3. Avvia Mirto da Applicazioni.

> **App non ancora firmata.** Alla prima apertura macOS può segnalare che l'app non può essere verificata.
> Prova ad aprirla, poi vai in **Impostazioni di Sistema → Privacy e sicurezza**, scorri in fondo e premi
> **Apri comunque**. Dalla volta successiva si apre normalmente.

Per installare dai sorgenti con Python vedi l'[Appendice B](#appendice-b-installazione-dai-sorgenti-e-riga-di-comando).

---

## 2b. Installazione su Windows

1. Scarica l'installer (`Mirto-<versione>-setup.exe`) dalla
   [pagina delle Release](https://github.com/marcopogo81/mirto/releases).
2. Avvialo e segui la procedura; Mirto compare nel menu Start.
3. Se Windows SmartScreen avvisa che l'app non è riconosciuta, scegli **Ulteriori informazioni → Esegui
   comunque**.

`ffmpeg`, usato per la registrazione, è incluso: non va installato.

**Differenze rispetto a macOS**

| macOS | Windows |
|---|---|
| Portachiavi | **Gestione credenziali → Credenziali Windows** (voci `Mirto`, `rtsp:…`, `node_hmac_key:…`) |
| `~/Library/Application Support/Mirto/` | `%LOCALAPPDATA%\Mirto\`, cioè `C:\Users\<utente>\AppData\Local\Mirto\` |
| Riconoscimento su Apple Neural Engine (CoreML) o CPU | Riconoscimento su **GPU (DirectML)** o CPU: lo indica la colonna *Sistema* |
| Icona nella barra dei menu, in alto | Icona nell'**area di notifica**, in basso a destra |
| Dopo un blocco Mirto riparte da solo | Dopo un blocco va riaperto a mano (vedi [14](#14-impostazioni-generali-e-funzionamento-continuo)) |
| ⌘Q per uscire, ⌘, per le Impostazioni | **Ctrl+Q** per uscire, **Ctrl+,** per le Impostazioni |

L'installazione dai sorgenti su Windows è descritta nell'[Appendice B](#appendice-b-installazione-dai-sorgenti-e-riga-di-comando).

---

## 3. Primo avvio

1. **Condizioni d'uso.** Leggile, spunta **Ho letto e accetto le condizioni d'uso** e premi **Accetto**.
   Senza accettarle Mirto si chiude. Restano consultabili in **Aiuto → Condizioni d'uso** (vedi
   [17](#17-sicurezza-privacy-e-condizioni-duso)).
2. **Statistiche d'uso.** Mirto chiede **«Aiuti a migliorare Mirto?»**. La risposta predefinita è
   **No, grazie**; con **Sì, invia le statistiche d'uso** parte una volta al giorno un breve messaggio anonimo.
   **Mostra cosa viene inviato** visualizza il messaggio esatto. La scelta si cambia in
   [Impostazioni → Generale](#14-impostazioni-generali-e-funzionamento-continuo).
3. **Permesso della fotocamera.** Il sistema chiede l'accesso alla **Fotocamera**: rispondi **Consenti**. Su
   macOS il permesso si gestisce in *Impostazioni di Sistema → Privacy e sicurezza → Fotocamera*.
4. **Avvio dei servizi.** Nella colonna *Sistema* le righe passano da ambra («collegamento…»,
   «caricamento…») a verde in pochi secondi. Al primo avvio viene scaricato il modello di riconoscimento
   (circa 3,7 MB), verificato con un'impronta SHA-256.
5. **Scegli le camere** da usare in **Tutte le cam** (vedi [5](#5-camere)) e **configura Telegram** (vedi
   [7.1](#71-telegram)).

> **Immagine nera?** Chiudi Mirto del tutto (⌘Q / Ctrl+Q) e riaprilo: la prima volta, mentre il sistema
> chiede il permesso, la camera può non accendersi. Se il problema resta vedi [16](#16-risoluzione-dei-problemi).

---
## 4. La finestra principale

| Area | Contenuto |
|---|---|
| **Fascia di stato** (in alto) | Colore dello stato della protezione; lampeggia in rosso quando viene rilevata una persona |
| **Colonna di sinistra** | Stato in grande con il logo, i comandi **ATTIVA/DISATTIVA PROTEZIONE**, **Silenzia 1 ora**, **FERMA TUTTO** (solo quando serve), **Impostazioni** e il riquadro **Sistema** |
| **Area centrale** | Le viste **Griglia camere**, **Tutte le cam**, **Rivedi** e **Nodi** (quest'ultima solo con almeno un nodo abbinato) |
| **Striscia Eventi** (sotto le camere) | Gli eventi recenti, dal più recente a sinistra |
| **Barra inferiore** | Messaggi del programma, versione e autore |

### Le viste

- **Griglia camere**: una casella per ogni camera attiva, con nome e stato sopra il video. **Doppio clic**
  ingrandisce una camera; **Torna alla griglia** (o un altro doppio clic) torna indietro. Passando il mouse
  sopra una camera compare il pulsante **Foto**; il **tasto destro** offre **Foto ora** e **Ingrandisci / torna
  alla griglia**. Il cursore in basso a destra regola la grandezza delle anteprime. L'ultima casella
  tratteggiata apre **Tutte le cam**.
- **Tutte le cam**: tutte le camere che il computer vede, per sceglierle e gestirle (vedi [5](#5-camere)).
- **Rivedi**: le registrazioni giorno per giorno (vedi [9](#9-registrazione-e-rivedi)).
- **Nodi**: tutti i nodi abbinati in una tabella (vedi [12.4](#124-la-tabella-dei-nodi)).

### Il riquadro Sistema

Mostra in tempo reale lo stato di ogni componente, con un pallino colorato: **verde** pronto, **ambra** in
avvio o da controllare, **rosso** problema. Passando il mouse su una riga compare il dettaglio; un clic
espande o chiude le righe che hanno altre informazioni.

| Riga | Cosa indica |
|---|---|
| **Camere in linea** | Camere in uso e il loro stato; poi quelle spente, non collegate e nuove in rete |
| **AI Recognition** | Modello in uso, dove lavora (Apple Neural Engine, GPU DirectML o CPU), categorie sorvegliate e soglia |
| **Notifiche** | Telegram (gruppo, bot, utenti autorizzati con ✓ o ⚠), email e SMS; eventuali avvisi in coda |
| **Suoni** | Microfoni in ascolto e ultimo suono riconosciuto |
| **Registrazione** | Camere che registrano, spazio usato e libero |
| **Presenza** | Chi è in casa (🏠) e chi è fuori (🚶) |
| **Nodi in linea** | Per ogni nodo: cosa è acceso, cassa, camera, registrazione, temperatura e versione; da qui una riga di un'azione si può accendere o spegnere con un clic |
| **Nuovo nodo trovato** | Compare quando in rete c'è un nodo da abbinare, con **Abbina…** e **Ignora** |

### Eventi

La striscia **Eventi** mostra ora, camera e foto di ogni evento; un puntino viola segna animali, veicoli e
oggetti, il simbolo **▶** gli eventi con video. Il selettore **Foto | Video** filtra la striscia.

- **Doppio clic**: apre la foto (o il video, nella vista *Video*).
- **Tasto destro**: **Rivedi in Mirto**, **Apri la foto**, **Elimina selezionati**.
- **Cartella** apre la cartella di foto e video, **Elimina** (o tasto ⌫/Canc) cancella gli eventi selezionati,
  **Svuota** li cancella tutti.

Gli eventi più vecchi del periodo di conservazione (di serie 30 giorni) si cancellano da soli.

### Menu

| Menu | Voci |
|---|---|
| **Mirto** | **Impostazioni…** (⌘, / Ctrl+,), **Chiudi finestra (resta in background)** (⌘W / Ctrl+W), **Esci da Mirto** (⌘Q / Ctrl+Q) |
| **Prove** | Le prove di [15](#15-prove-e-diagnostica) |
| **Aiuto** | **Comandi Telegram**, **Guida utente**, **Apri la cartella dei log**, **Sostieni Mirto**, **Condizioni d'uso**, **Informazioni su Mirto** |

Le conferme delle operazioni importanti (uscire, Ferma tutto, svuotare gli eventi, spegnere l'unica camera)
compaiono con un cartello giallo: il pulsante rosso conferma, **Annulla** (o Esc) lascia tutto com'è.

---

## 5. Camere

In **Tutte le cam** compaiono tutte le camere che il computer vede, raggruppate per origine (camere di
questo computer e di rete, camere di ciascun nodo, camere non collegate). Per ogni camera:

| Comando | Funzione |
|---|---|
| **Interruttore** | Verde = **attiva**: compare nella griglia, viene analizzata e invia avvisi. Grigio = **spenta**: Mirto non la apre |
| **Anteprima** | Solo per le camere spente: la accende per qualche secondo per vedere cosa inquadra |
| **Foto** | Salva e apre una foto della camera |
| **Rinomina** | Nome usato nell'app e negli avvisi (per esempio «Ingresso») |
| **Non è questa?** | Se l'immagine appartiene a un'altra camera, scegli quale: i due nomi si scambiano |
| **Rimuovi** | Solo per le camere di rete aggiunte a mano |

La scelta resta salvata: una camera spenta resta spenta anche se la scolleghi e la ricolleghi. Per esempio,
spegni l'iPhone per evitare che Mirto lo usi quando il telefono è vicino al computer.

### Camere di rete

- **Trovate in rete**: le camere che si annunciano sulla rete locale compaiono in questo riquadro. **Aggiungi**
  le porta nella griglia (Mirto le ritrova anche se cambiano indirizzo IP); **Nascondi** le toglie dal
  riquadro, **Mostra le camere nascoste** le rimette. **Ricontrolla ora** ripete subito la ricerca.
- **Aggiungi camera di rete…**: per una camera IP, inserisci un nome e l'indirizzo `rtsp://…` o `http://…`
  indicato nel manuale della camera. Utente e password vengono salvati nel Portachiavi (Credenziali Windows).
- Le camere dei **nodi** non vanno aggiunte: compaiono da sole dopo l'abbinamento (vedi [12](#12-nodi)).

Sulla scheda di ogni camera di rete è indicato l'indirizzo in uso; «risponde anche su …» segnala un secondo
indirizzo raggiungibile (per esempio cavo e Wi-Fi insieme).

> La camera integrata non vede al buio. Quando l'immagine è troppo scura Mirto lo segnala una volta per
> camera, e la casella mostra un bordo ambra finché la luce non torna.

---

## 6. Protezione e avvisi

### Stati

| Stato | Colore | Significato |
|---|---|---|
| **Protetta** | Verde | Ogni rilevamento genera un avviso e può avviare le azioni |
| **In pausa** | Ambra | Protezione attiva, avvisi sospesi fino all'ora indicata |
| **Non protetta** | Grigio | Nessun avviso: gli eventi restano solo sul computer |

Quando viene rilevata una persona il logo diventa rosso, sotto lo stato compare **RILEVAMENTO** e la casella
della camera si evidenzia: **rosso** = persona, **viola** = animale, veicolo od oggetto, **blu** = movimento,
**ambra** = camera silenziata o immagine troppo scura. Disattivando la protezione le evidenze rosse spariscono.

### Comandi

| Comando | Funzione |
|---|---|
| **ATTIVA PROTEZIONE / DISATTIVA PROTEZIONE** | Cambia lo stato. Lo stesso vale da Telegram (`/attiva`, `/disattiva`), dalla programmazione oraria e dalla presenza |
| **Silenzia 1 ora** | Compare a protezione attiva dopo un allarme recente: sospende gli avvisi per 60 minuti. Diventa **Riattiva avvisi** mentre sono sospesi |
| **FERMA TUTTO** | Compare solo quando un'azione è in corso o in attesa di conferma: spegne le azioni sui nodi, sospende i dissuasori per un'ora e disattiva la protezione. Da Telegram `/ferma` funziona sempre |

Un cambio fatto a mano vale fino al prossimo orario programmato (vedi [10](#10-programmazione)).

### L'avviso

Ogni avviso arriva su Telegram con la foto (e, se la registrazione è attiva, la clip pochi secondi dopo) e
via email con la foto allegata. Sotto la foto Telegram offre:

- **Falso allarme**: segna l'evento come errato;
- **Silenzia 1 ora**: sospende gli avvisi di quella camera;
- **Attiva \<azione\>**: conferma un'azione impostata con «Chiedi conferma su Telegram»;
- **⛔ Ferma tutto**: come il pulsante dell'app.

Dopo un avviso la stessa camera non ne invia un altro per **2 minuti**. I comandi Telegram sono elencati
nell'[Appendice A](#appendice-a-comandi-telegram).

---
## 7. Notifiche

**Impostazioni → Notifiche** elenca a sinistra i canali **Telegram**, **Email** e **SMS** (● attivo, ○ spento)
e a destra le impostazioni del canale selezionato. Dopo ogni modifica premi **Salva**.

### 7.1 Telegram

Telegram è il canale principale: riceve la foto con i pulsanti per reagire e permette di comandare Mirto da
remoto. Servono un **bot** e un **gruppo**.

**Creare il bot**

1. In Telegram cerca **@BotFather** (con la spunta blu) e premi **Avvia**.
2. Scrivi `/newbot`, scegli un nome (es. *Mirto*) e uno username che termina con `bot`.
3. Copia il **token** che BotFather restituisce (es. `7123456789:AAH…`).

> Il token equivale alla password del bot: non condividerlo e non usare **Revoke** in BotFather, altrimenti
> Mirto smette di ricevere i comandi.

**Creare il gruppo**

1. Crea un gruppo (es. **Mirto Admin**) e aggiungi il bot e i familiari.
2. Nelle informazioni del gruppo: **Modifica → Cronologia chat per i nuovi membri → Visibile**. Il gruppo
   diventa un supergruppo: le email conterranno il collegamento diretto al messaggio Telegram.
3. *(Facoltativo, per inviare messaggi vocali ai nodi)* in BotFather: `/setprivacy` → scegli il bot → **Disable**.

In alternativa al gruppo puoi usare la chat privata con il bot.

**Collegare Mirto**

1. **Impostazioni → Notifiche → Telegram**: spunta **Invia gli avvisi su Telegram**, incolla il **token** e
   premi **Salva**.
2. Nel gruppo scrivi `/id`. Il bot risponde con l'**ID chat** (inizia con `-100`) e il tuo **ID utente**.
3. Inserisci l'ID chat in **ID del gruppo** e il tuo ID in **Utenti autorizzati** (più ID separati da virgola).
4. Premi **Invia messaggio di prova**, poi **Salva**.

Gli **utenti autorizzati** possono dare comandi e usare i pulsanti; gli altri membri del gruppo ricevono solo
gli avvisi. Per aggiungere un familiare: entra nel gruppo, scrive `/id`, e tu aggiungi il suo ID utente.

Il menu dei comandi del bot (in italiano e in inglese) viene impostato automaticamente da Mirto.
Lo stato del collegamento si legge nella riga **Notifiche** del riquadro *Sistema*: nome del gruppo, numero di
membri, bot e utenti autorizzati (⚠ rosso = non è più nel gruppo, ⚠ ambra = ID sconosciuto).

> **Un bot per computer.** Telegram consegna i comandi di un bot a un solo programma: due Mirto con lo
> stesso token se li contendono. Per un secondo computer crea un secondo bot con BotFather.

### 7.2 Email

Mirto invia un'email con la foto allegata e, se il gruppo Telegram è un supergruppo, il collegamento al
messaggio. Con Gmail serve una **password per le app**.

1. Su [myaccount.google.com](https://myaccount.google.com) → **Sicurezza** attiva la **Verifica in due
   passaggi**.
2. Apri [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords), crea una password
   chiamata **Mirto** e copia le 16 lettere.
3. Compila **Impostazioni → Notifiche → Email**:

| Campo | Valore |
|---|---|
| **Invia email con foto allegata e link a Telegram** | Spuntato |
| Provider | **Gmail** (disponibili anche Yahoo, iCloud, Libero, Aruba e server personalizzato) |
| Server SMTP / Porta | Vuoto e 0: valgono quelli del provider (il server serve solo con «personalizzato») |
| Utente | Il tuo indirizzo |
| Password | La password per le app, **senza spazi** |
| Destinatari | Indirizzi separati da virgola |

4. Premi **Invia email di prova** (controlla anche lo Spam la prima volta), poi **Salva**.

Outlook/Hotmail non è supportato: richiede un tipo di accesso Microsoft diverso.

### 7.3 SMS

Gli SMS sono un canale di riserva a pagamento: arrivano anche senza connessione dati. Contengono solo testo
(camera, ora, tipo di rilevamento) e il collegamento al messaggio Telegram; partono **solo per le persone**.

1. **Impostazioni → Notifiche → SMS**: spunta **Attiva gli SMS** e scegli il **provider**; compaiono solo i
   campi necessari.

| Provider | Uso | Dati richiesti |
|---|---|---|
| **HTTP generico** | Servizi con API web (Skebby, Aruba SMS, Brevo…) | URL, metodo, corpo, Content-Type, intestazioni e chiave API |
| **Twilio** | Servizio internazionale | Account SID, token e numero mittente |
| **Email-to-SMS** | Provider che convertono un'email in SMS | Indirizzo modello, es. `{numero}@provider.it` (usa l'email configurata) |

2. Per il provider **HTTP generico**: nell'URL e nel corpo si usano i segnaposto `{numero}`, `{testo}` e
   `{api_key}`, per esempio `{"to": "{numero}", "message": "{testo}"}`. Se la chiave va in un'intestazione,
   scrivila in **intestazioni (JSON)**, es. `{"Authorization": "Bearer {api_key}"}`. In **risposta deve
   contenere** puoi indicare una parola che il provider restituisce in caso di successo (es. `OK`).
3. **Destinatari** in formato internazionale (es. `+393331234567`), separati da virgola.
4. **Quando**: *Per ogni evento* oppure *Solo se Telegram non risponde*.
5. **Limite al giorno** (di serie 20) per contenere i costi.
6. **Invia SMS di prova** (arriva al primo destinatario), poi **Salva**.

La chiave API e il token Twilio sono salvati nel Portachiavi (Credenziali Windows).

---
## 8. Riconoscimento (AI)

La scheda **Impostazioni → AI** raccoglie tutto ciò che Mirto riconosce, in due sottoschede: **Camere** e
**Microfoni**. Il riconoscimento avviene sul computer: immagini e audio non vengono inviati a servizi esterni.

### 8.1 Camere: cosa riconoscere

| Impostazione | Funzione | Valore di serie |
|---|---|---|
| **Modello che guarda le immagini** | *YOLOX Nano* (veloce, ~3,7 MB, adatto alla CPU) o *YOLOX-S* (più preciso, ~36 MB, consigliato su Apple Silicon) | YOLOX Nano |
| **Soglia persona per gli avvisi** | Sicurezza minima per avvisare di una persona | 40% |
| **Pausa tra due avvisi della stessa camera (s)** | Evita raffiche di messaggi | 120 s |
| **Categorie** | Cosa genera un avviso: Persone, Animali, Veicoli, Oggetti; **Mostra tutte le categorie…** apre le altre (in tutto 80) | Solo *Persona* |
| **Soglia per animali, veicoli e oggetti** | Sicurezza minima per le categorie diverse dalle persone | 50% |
| **Per gli uccelli riconosci anche il gruppo** | Un secondo modello (~3,5 MB, scaricato al primo uccello) indica il gruppo: piccioni e tortore, corvidi, storni, passeri, gabbiani, aironi, cormorani, parrocchetti, merli e tordi, rapaci | Attivo |

Comportamento per categoria:

- **Persone**: fascia rossa, avviso, eventuali SMS e azioni antifurto.
- **Animali, veicoli e oggetti**: avviso su Telegram ed email (es. «🐾 Rilevato: Cane»), riquadro viola; non
  avviano sirene né SMS.
- Ogni categoria ha la sua pausa di 2 minuti per camera.
- Per gli uccelli l'avviso riporta il gruppo e, tra parentesi, la specie più probabile, che è indicativa.
  Sotto il 35% di sicurezza resta «Uccello».

**Regolare la soglia persona.** Dopo qualche giorno di uso: se trovi eventi senza persone (tende, ombre,
animali) alzala di 0,05 alla volta; se una persona non viene segnalata abbassala di 0,05 e verifica che ci sia
luce sufficiente. Le soglie di questa scheda sono quelle di serie: un'azione può usarne una propria (vedi
[13](#13-azioni)).

### 8.2 Microfoni: i suoni

Mirto riconosce i suoni con un modello locale (YAMNet, ~15 MB, scaricato al primo uso).

1. Spunta **Ascolta il microfono del computer**: la prima volta il sistema chiede il permesso **Microfono**.
2. In **Foto dalla camera** scegli quale camera fotografare quando il computer sente un suono.
3. Il microfono di un **nodo** si accende in **Impostazioni → Nodi → Configurazione → Audio → Microfono** con
   **Usa questo microfono**; **Mirto ascolta questo microfono** lo usa per il riconoscimento (vedi
   [12.3](#123-configurare-un-nodo)).
4. In **Suoni che mandano un avviso** scegli i suoni e premi **Salva**.

| Suono | Esempi | Di serie |
|---|---|---|
| 🪟 Vetro rotto | Finestra o vetrina in frantumi | Avviso |
| 🔥 Allarme fumo o antincendio | Rilevatore di fumo | Avviso, **anche a protezione disattivata** |
| 🚨 Sirena o allarme | Antifurto, allarme dell'auto | Avviso |
| 😱 Urla | Persone che gridano | Avviso |
| 😢 Pianto | Pianto di un bambino | — |
| 💥 Spari o esplosioni | Spari, petardi, scoppi | — |
| 🐕 Cane che abbaia | Il cane in giardino | — |
| 🚪 Colpi o porta sbattuta | Bussare, porta sbattuta, campanello | — |
| 🗣 Voci | Persone che parlano | — |
| 🐦 Versi di uccelli | Gracchi, tubare dei piccioni | — |

L'avviso arriva come quelli delle camere (es. «🪟 Suono: Vetro rotto») con la foto della camera associata.
Ogni suono ha una pausa di 2 minuti per microfono. Mentre un nodo suona (sirena, dissuasore) e nei 10 secondi
successivi i suoni vengono ignorati, tranne l'allarme fumo. **Silenzia** e **In pausa** valgono anche per i
suoni. Per far **reagire** un nodo a un suono (es. vetro rotto → sirena) aggiungi il suono tra gli obiettivi
di un'azione (vedi [13](#13-azioni)).

> L'ascolto non viene registrato né inviato: l'audio resta in memoria pochi secondi, il tempo di riconoscere
> il suono. Fa eccezione la registrazione dell'audio di un nodo, che si attiva esplicitamente (vedi [9](#9-registrazione-e-rivedi)).

---

## 9. Registrazione e Rivedi

### Impostazioni → Registrazione

| Impostazione | Funzione | Di serie |
|---|---|---|
| **Registra le camere** | Accende o spegne la registrazione | Attiva |
| **Conserva foto ed eventi (giorni)** | Dopo quanti giorni foto ed eventi si cancellano; i video seguono lo spazio su disco | 30 |
| **Cartella** / **Scegli…** | Dove salvare le registrazioni (anche un disco esterno) | Cartella dei dati di Mirto |
| **Di serie** / **Per camera** | Modalità di registrazione per tutte le camere o per ciascuna | Solo eventi |
| **solo con protezione attiva** | Per camera: a protezione disattivata non registra nulla (la foto dell'evento resta) | Spento |
| **Conserva la registrazione continua** | Giorni di registrazione continua conservati | 7 |
| **Spazio massimo** | Quota massima dello spazio disponibile | 50% |
| **Clip dell'evento** | Secondi prima e dopo l'evento | 10 prima, 20 dopo |
| **Camere del computer e USB** | Fotogrammi al secondo registrati | 10 |
| **Manda la clip su Telegram dopo la foto** | Invia la clip come risposta alla foto | Attiva |

| Modalità | Cosa salva | Spazio indicativo |
|---|---|---|
| **Solo eventi** | Una clip per ogni evento, da 10 s prima a 20 s dopo | Pochi MB al giorno |
| **Continua** | Tutto, giorno e notte, per i giorni scelti | Circa 10 GB al giorno per camera |
| **Spenta** | Solo la foto dell'evento | — |

La colonna **Adesso** mostra lo stato effettivo con le impostazioni salvate.

**Gestione dello spazio.** Le registrazioni più vecchie si cancellano da sole; Mirto non supera la quota
scelta e lascia sempre almeno 2 GB liberi. Se il disco si riempie comunque, la registrazione si sospende e
arriva un avviso su Telegram.

I file sono MP4 da un minuto, una cartella per camera, leggibili con qualunque lettore (QuickTime, VLC). Le
camere di rete vengono registrate senza ricodifica; quelle del computer e USB vengono compresse con
l'accelerazione hardware disponibile.

### La vista Rivedi

**Rivedi** (in alto, accanto a *Griglia camere* e *Tutte le cam*) mostra le registrazioni di una camera giorno
per giorno.

- **Camera e giorno**: scegli la camera; ◀ ▶ cambiano giorno, **Oggi** torna a oggi, **📁 Cartella** apre i file
  del giorno.
- **Barra delle 24 ore**: in blu le parti registrate; i puntini segnano gli eventi con video (🔴 persone,
  🟣 animali e oggetti, 🔵 suoni). In verde acqua le parti presenti solo sul nodo: un clic le scarica.
  Clic su un'ora = vai lì; clic su un evento = parte 5 secondi prima. **Rotellina** = ingrandisci attorno al
  mouse, **trascina** = scegli un intervallo; **‹ Indietro**, **24 ore**, doppio clic o Esc tornano indietro.
- **Riproduzione**: ⏮ ⏭ evento precedente e successivo, ±10 s (frecce ← →), riproduci/pausa (barra spaziatrice),
  **Velocità** fino a 16×, **📷 Foto** salva il fotogramma, **⤓ Esporta…** salva un intervallo in MP4 (copia
  senza ricodifica).
- Dalla striscia **Eventi**, tasto destro → **Rivedi in Mirto** apre direttamente quel momento.

In modalità *Solo eventi* la barra contiene le clip degli eventi e gli ultimi minuti; per rivedere l'intera
giornata usa la modalità *Continua*.

### Registrazione sul nodo

Ogni nodo registra anche da sé la propria camera, ad anello, come copia di sicurezza (vedi [12.3](#123-configurare-un-nodo),
*Disco*). L'**audio del microfono del nodo** può essere incluso nelle registrazioni solo se lo attivi in
**Impostazioni → Nodi → Configurazione → Disco → Registra anche l'audio del microfono del nodo** (di serie è spento).

> **Privacy.** Per le camere che riprendono strada o spazi condominiali valgono le regole del Garante
> Privacy: cartello, inquadratura limitata e conservazione breve (preferisci *Solo eventi*). L'audio ha regole
> più severe del video: attivalo solo se sei certo di poterlo fare e informa chi vive in casa.

---
## 10. Programmazione

**Impostazioni → Programmazione** attiva e disattiva la protezione automaticamente agli orari scelti.

1. Verifica che **Programmazione automatica attiva** sia spuntata.
2. Premi **Aggiungi…** e compila la regola: **Nome** (es. *Notte*), **Cosa fare** (🔒 Attiva protezione /
   🔓 Disattiva protezione), **A che ora**, **Giorni** (*Tutti i giorni* o giorni specifici) e, facoltativamente,
   **Solo tra due date** (es. le vacanze).
3. Premi **OK**, poi **Salva**.

**Modifica…** ed **Elimina** agiscono sulla regola selezionata; **Esempio notte + mattina** crea due regole
pronte (attiva alle 23:00, disattiva alle 07:00).

- Un cambio fatto a mano vale fino al prossimo orario programmato.
- Ogni cambio automatico viene comunicato nel gruppo Telegram.
- Da Telegram: `/programma` mostra le regole, `/programma sospendi` le sospende, `/programma riattiva` le
  riattiva.

---

## 11. Presenza

Mirto può capire chi è in casa dai **telefoni collegati al Wi-Fi**: quando escono tutti attiva la
protezione, quando rientra qualcuno la disattiva. Ogni cambio viene comunicato su Telegram (👥).

### Preparare i telefoni

I telefoni vengono riconosciuti dall'**indirizzo MAC**, che deve restare fisso:

- **iPhone**: Impostazioni → Wi-Fi → **(i)** accanto alla rete di casa → **Indirizzo Wi-Fi privato: Fisso** (oppure
  Disattivato). «Configura IP» resta su *Automatico*. Nella stessa schermata si legge l'**Indirizzo Wi-Fi**.
- **Android**: Wi-Fi → rete di casa → Impostazioni avanzate → **Privacy: MAC del dispositivo**.

### Configurazione

1. **Impostazioni → Presenza**: spunta **Riconosci chi è in casa dai telefoni collegati al Wi-Fi**.
2. **Cerca dispositivi in rete…**: dopo circa 10 secondi compare l'elenco dei dispositivi. Scegli quello con lo
   stesso indirizzo MAC del telefono, premi **Aggiungi** e indica di chi è. In alternativa usa **Aggiungi a
   mano…** e inserisci l'indirizzo.
3. Ripeti per ogni persona, poi premi **Salva**.

| Impostazione | Funzione | Di serie |
|---|---|---|
| **Quando escono tutti, attiva la protezione** | Attiva la protezione quando nessun telefono risponde | Attiva |
| **Quando rientra qualcuno, disattiva la protezione** | Disattiva la protezione al primo rientro | Attiva |
| **Considera «fuori» dopo (minuti senza risposta)** | Evita falsi «uscito» quando il telefono è in stand-by | 15 |

La presenza agisce solo sui cambi: se attivi la protezione a mano con tutti in casa, resta attiva. All'avvio
Mirto attende i minuti indicati prima di decidere che non c'è nessuno. Lo stato si legge nella riga
**Presenza** del riquadro *Sistema* e con `/stato` su Telegram.

> **Sicurezza.** Un telefono rubato o un indirizzo MAC copiato potrebbero disattivare la protezione. Per la
> massima sicurezza lascia spenta *Quando rientra qualcuno, disattiva la protezione* e disattiva da Telegram.

---

## 12. Nodi

Un **nodo** è un dispositivo Mirto, per esempio un Raspberry, con tutto ciò che ha collegato: camere, casse
(per sirena, suoni e messaggi vocali), luci, microfono, disco per le registrazioni. Si abbina una volta sola:

- le sue camere compaiono da sole in *Tutte le cam*, raggruppate sotto il nome del nodo, e lo seguono anche se
  cambia indirizzo (cavo, Wi-Fi, nuovo IP);
- si configura interamente da Mirto, senza terminale;
- rimuovendolo si tolgono anche le sue camere e le azioni che agiscono solo su di lui.

### 12.1 Preparare un nodo Raspberry

**Occorrente**: Raspberry Pi 4 o 5 con alimentatore, microSD da 16 GB o più, Camera Module (normale o NoIR,
facoltativa), una cassa (jack, USB o Bluetooth), un cavo di rete per la prima configurazione.

- **Con una scheda già pronta** (immagine Mirto): inserisci la microSD, collega il cavo di rete e accendi. Il
  nodo si chiama `mirto-node-XXXX` ed è subito abbinabile; il **PIN** è sul foglio fornito con il dispositivo o
  nel file `mirto-pin.txt` della microSD.
- **Partendo da Raspberry Pi OS**: scrivi *Raspberry Pi OS Lite (64-bit)* con Raspberry Pi Imager abilitando SSH,
  poi installa il software del nodo come descritto nell'[Appendice C](#appendice-c-comandi-del-nodo). Al termine
  l'installazione mostra indirizzo, impronta del certificato e PIN.

Collega la camera alla presa **CAMERA** a Raspberry spento.

### 12.2 Abbinare un nodo

1. Accendi il nodo. Nella colonna *Sistema* compare **Nuovo nodo trovato → Abbina…**; in alternativa
   **Impostazioni → Nodi → Cerca nodi in rete**.
2. Controlla che l'**impronta del certificato** corrisponda a quella del nodo.
3. Inserisci il **PIN di 6 cifre** e scegli il **nome** del nodo (es. *Garage*): è il nome con cui compare in
   Mirto e negli avvisi. Il nome in rete resta `mirto-node-XXXX.local`.
4. Premi **Abbina**. Le camere del nodo compaiono in pochi secondi; Mirto propone poi di creare le azioni
   (vedi [13](#13-azioni)).
5. **Se il nodo è attaccato al cavo di rete, Mirto chiede se metterlo sul Wi-Fi adesso** e apre il passo
   descritto in [12.4](#124-la-tabella-dei-nodi). Rispondi di sì: quando il nodo finisce su un palo il cavo
   si stacca, e un nodo che non ha mai visto il tuo Wi-Fi sparisce nel momento in cui lo scolleghi — e per
   configurargli il Wi-Fi bisogna raggiungerlo, cosa che non puoi più fare. Il cavo resta attaccato finché
   il Wi-Fi non funziona, quindi una password sbagliata non costa niente. A un nodo già in Wi-Fi non si
   chiede nulla.

| Situazione | Durata della finestra di abbinamento |
|---|---|
| Nodo mai abbinato | Non scade finché non viene abbinato |
| Riapertura successiva | **5 minuti**, con un PIN nuovo |
| Dopo 5 PIN errati | La finestra si chiude e va riaperta |

**Ignora** nasconde la proposta fino al riavvio di Mirto. Per abbinare un **secondo computer** usa **Altri
computer → Apri l'abbinamento** (vedi sotto), che mostra il PIN da inserire sull'altro computer. Ogni computer
riceve una chiave propria; se un computer sconosciuto si abbina, arriva un avviso su Telegram.

### 12.3 Configurare un nodo

In **Impostazioni → Nodi** l'elenco **NODI ABBINATI** mostra ogni nodo con il suo stato (verde in linea,
rosso non risponde), **Rinomina il nodo** e **Rimuovi**. Selezionando un nodo si aprono tre sottoschede.

**Camere** – le camere trasmesse dal nodo con il loro stato in Mirto; un clic su una camera ne cambia il nome.

**Configurazione** – le finestre di gestione del nodo:

| Voce | Funzioni |
|---|---|
| **Altri computer** | Elenco dei Mirto abbinati (nome, indirizzo, data). **Apri l'abbinamento** mostra il PIN per un altro computer, **Chiudi l'abbinamento** lo chiude, **Stacca…** revoca la chiave di un computer. L'ultimo abbinamento si revoca solo dal nodo |
| **Audio** | **Uscita predefinita del nodo** con **Suono di prova**; schede **Jack**, **Bluetooth** (**Cerca casse…**, **Abbina**, **Dimentica**), **USB** e **Microfono** (**Usa questo microfono**, **Mirto ascolta questo microfono**, livello in tempo reale); volume e guadagno si applicano subito |
| **Camera** | **Camera accesa**, **È il modello NoIR** (solo con illuminatori a infrarossi), **Risoluzione** (1280×720 basta per riconoscere una persona, 1920×1080 per leggere un volto o una targa) |
| **Disco** | **Cerca dischi…** elenca SSD e chiavette collegati al nodo; **Registra qui** usa un disco già pronto, **Prepara il disco…** lo formatta (cancella tutto: va confermato riscrivendo il nome del dispositivo). Imposta la registrazione ad anello, lo spazio massimo, lo spazio da lasciare libero e **Registra anche l'audio del microfono del nodo** |
| **Modalità Prank** | Accende per 10, 30, 60 o 120 minuti la modalità che toglie il budget orario ai dissuasori (vedi [13](#13-azioni)); **Spegni adesso** la chiude |
| **Stato e diagnosi** | Cosa non va e come rimediare, poi versione, indirizzi, temperatura, registrazione, sirena e abbinamenti. **Rileggi**, **Aggiorna il nodo**, **Copia per l'assistenza** (testo senza password né PIN) |
| **Wi-Fi** | **Cerca reti…** mostra le reti viste dal nodo con il segnale; inserisci la password o usa **Prendila da questo computer** (dal Portachiavi o dai profili Wi-Fi di Windows, come amministratore), poi **Collega**. Il nodo resta sul cavo finché il Wi-Fi non funziona |

**Suoni** – i suoni che il nodo sa emettere (`rapace`, `falco`, `allarme_corvi`, `sparo`, `cannoncino` e quelli
caricati). **Aggiungi un suono…** carica un file audio (WAV, MP3, M4A, AAC, OGG, FLAC), convertito automaticamente; **Ascolta** lo riproduce
sul nodo, **Togli** lo elimina (Mirto avvisa se un'azione lo usa).

**Registrazione sul nodo.** Il nodo registra la propria camera ad anello anche con il computer spento o la rete
assente: senza disco usa la microSD con un limite basso (circa 2 GB); con un SSD o una chiavetta (consigliato)
conserva giorni. Se il disco viene scollegato la registrazione si ferma e, dopo 5 minuti, arriva un avviso su
Telegram. In *Rivedi* le parti presenti solo sul nodo si scaricano con un clic.

**Consigli.** Per un'installazione fissa preferisci una cassa via jack o USB: una cassa Bluetooth può spegnersi
o essere occupata da un telefono. Se la cassa scelta non risponde la sirena suona comunque dall'uscita
successiva disponibile (jack, USB, uscita di sistema) e *Sistema* lo segnala. Una camera normale vede al buio
solo con una luce accesa, una NoIR con un illuminatore infrarosso.

### 12.4 La tabella dei nodi

Con almeno un nodo abbinato compare in alto la vista **Nodi**: una riga per nodo con **Nodo**, **Stato**,
**Versione**, **Spazio**, **Temp.**, **Rete**, **Avvisi** e **Ultimo evento**. L'ultima colonna segnala una camera
che ha smesso di vedere eventi pur restando in linea. **Doppio clic** su un nodo lo apre nelle Impostazioni;
**Aggiorna** compare quando è disponibile una versione più recente.

### 12.5 Aggiornare un nodo

Quando il software del nodo incluso in Mirto è più recente di quello installato, un avviso compare in
cima a **Impostazioni → Nodi**, nella riga del nodo in *Sistema* e nella tabella **Nodi**. **Aggiorna il nodo**
invia il pacchetto firmato sul canale cifrato dell'abbinamento; il nodo si installa e si riavvia (circa un
minuto, durante il quale non risponde). Configurazione, chiavi, abbinamenti e registrazioni restano.

---
## 13. Azioni

Un'**azione** è ciò che Mirto fa eseguire ai nodi quando riconosce qualcosa: suonare la sirena, accendere una
luce, emettere un verso contro gli uccelli. Le azioni si gestiscono in **Impostazioni → Azioni** e possono
coinvolgere più nodi (per esempio una luce all'ingresso e la sirena in garage).

### Le quattro tipologie

La tipologia non si sceglie: deriva da ciò che l'azione fa e da ciò che riconosce. Tu scegli **quando è attiva**
e **cosa fa**.

| Tipologia | Quando scatta | Limiti |
|---|---|---|
| 🚨 **Antifurto** | Solo a protezione attiva, per persone riconosciute con certezza (soglia 60%) o per i suoni scelti (es. vetro rotto); mai per gli animali | Nessun limite d'uso; la sirena suona al massimo 3 minuti per volta e 3 minuti ogni mezz'ora |
| 🔔 **Campanello e avvisi** | Anche a protezione disattivata: un suono, una luce o un avviso quando arriva qualcuno | Quelli che imposti: pausa tra due scatti, scatti massimi al giorno, fascia oraria |
| 🦅 **Dissuasore** | Sempre, per uccelli e animali; **mai per le persone** e mai se nell'inquadratura c'è anche una persona | 30 secondi per scatto e 2 minuti di suono ogni ora (imposti dal nodo), più i tuoi limiti |
| 🎭 **Prank** | Solo con la **modalità Prank** accesa su quel nodo **e** la casa non protetta; è l'unica tipologia che può puntare un suono su una persona | Nessun budget orario; restano 30 secondi per scatto. Tace appena attivi la protezione |

I limiti dei nodi sono applicati dal nodo stesso e nessun programma li può superare. Quando un budget è
esaurito, l'avviso indica fra quanto riprende.

### Libreria azioni

**Libreria azioni** propone azioni già pronte per i nodi abbinati, una per tipologia: **Antifurto** (sirena),
**Campanello**, **Dissuasore uccelli e animali** (piccioni e corvidi, 40 scatti al giorno) e **Prank** (importata
spenta). Spunta quelle che vuoi e premi **Importa**: restano modificabili come le altre.

### Nuova azione

**Nuova azione** apre una procedura guidata in quattro passi (**Indietro** / **Avanti**, **Salva** all'ultimo):

1. **Informazioni generali** – **Nome** (letto anche su Telegram), **Quando è attiva** (*Solo quando la casa è
   protetta (antifurto)*, *Sempre, anche a protezione spenta* oppure *Prank: scherzo, solo a modalità accesa sul
   nodo*), **Quando parte** (*Subito, in automatico* o *Chiedi conferma su Telegram*), **Attesa della conferma**,
   **Se nessuno conferma** (*Non fare nulla* o *Esegui comunque (solo con persona certa)*) e **Fascia oraria**
   (es. `06:00-20:30`), secondo la tipologia.
2. **Cosa la fa scattare** – **Da quali camere** e/o **Da quali microfoni**, e **Cosa far riconoscere al modello
   AI**: persona, qualunque uccello, un gruppo di uccelli, un animale o un suono. Un'azione può usare una soglia
   propria al posto di quella di serie, che resta indicata accanto.
3. **Imposta le azioni** – la **catena** di operazioni da eseguire (vedi sotto), **Suoni da emettere**, **Pausa tra
   due scatti**, **Scatti massimi al giorno** e **Avvisa su Telegram quando scatta**, secondo la tipologia.
4. **La accendo adesso?** – riepilogo della tipologia e dei limiti; l'azione può essere salvata accesa o spenta.

**La catena.** È una sequenza di pezzi su qualunque dispositivo rilevato: **+** (**Comincia da qui** /
**Aggiungi un pezzo**) propone ciò che sanno fare i nodi e le loro casse (sirena, luci, uscite, suoni del nodo,
suoni caricati) e **Aspetta** per inserire una pausa. Per ogni pezzo si scelgono la cassa, il suono, quante
volte e per quanto. Più pezzi possono partire **insieme** o **a turno** (uno diverso a ogni scatto); la freccia
tra due pezzi imposta l'attesa; **ripeti tutto ×** ripete l'intera striscia. **Prova tutta la catena** la esegue
sul nodo prima di salvarla.

### Gestire le azioni

Ogni azione in elenco mostra tipologia, nodi coinvolti e stato (**ATTIVA**, **SPENTA**, **DA SALVARE**), con:

- **Prova**: accende davvero l'azione per 5 secondi, dopo una conferma;
- **Modifica…** ed **Elimina**.

Le modifiche diventano effettive con **Salva**. Ogni azione deve avere un nome diverso. Dal riquadro *Sistema*
un'azione si accende o spegne anche con un clic sulla sua riga.

### Regole che non si possono cambiare

- Sirena, luci e uscite generiche (antifurto) partono solo a protezione attiva e mai per gli animali.
- I dissuasori non partono mai per le persone, salvo un'azione **Prank** a modalità accesa e casa non protetta.
- Ogni uscita si spegne da sola allo scadere della durata; **Ferma tutto** spegne tutto e sospende i dissuasori
  per un'ora.
- L'audio verso il nodo non parte mai da solo: si usa da Telegram con `/ascolta` e i messaggi vocali.
- Le uscite generiche (relè, prese, servizi HTTP) comandano ciò che vi colleghi: la scelta e l'uso conforme dei
  dispositivi sono responsabilità di chi li installa.

> **Rispetta i vicini e le regole.** Suoni forti come `sparo` e `cannoncino` possono disturbare: usa la fascia
> oraria, tieni il volume moderato (Impostazioni → Nodi → Configurazione → Audio) e verifica il regolamento del
> tuo Comune sui dissuasori acustici.

---

## 14. Impostazioni generali e funzionamento continuo

**Impostazioni → Generale**:

| Voce | Funzione |
|---|---|
| **Lingua / Language** | *Automatica* (quella del sistema), *Italiano* o *English*. Avvisi e messaggi cambiano subito, la finestra alla riapertura |
| **Avvia Mirto all'accesso** | Avvia Mirto all'accesso al computer. Su macOS lo riavvia anche se si chiude per errore; su Windows usa la chiave di registro `Run` e, dopo un blocco, Mirto va riaperto a mano |
| **Impedisci la sospensione del computer** | Il computer non va in stop mentre Mirto è aperto |
| **Chiudendo la finestra resta acceso in background** | Chiudendo la finestra la sorveglianza continua |
| **Invia statistiche d'uso** | Una volta al giorno un messaggio anonimo: numero casuale, versione, sistema, lingua, numero di camere e nodi per fasce, funzioni attive. Mai foto, video, audio, nomi o indirizzi. **Cosa viene inviato…** mostra il messaggio, **Nuovo ID casuale** rinnova il numero. Dettagli in `PRIVACY.md` |
| **Controlla se esce una nuova versione di Mirto** | Una volta al giorno verifica l'ultima versione disponibile; avvisa una volta per versione |

Le nuove impostazioni valgono subito dopo **Salva**. Modello e soglie sono in **AI** (vedi [8](#8-riconoscimento-ai)),
la conservazione di foto ed eventi in **Registrazione** (vedi [9](#9-registrazione-e-rivedi)).

### Lasciare Mirto sempre acceso

- Attiva **Avvia Mirto all'accesso** e **Impedisci la sospensione del computer**.
- Su un portatile **lascia il coperchio aperto**: chiuso, il computer va comunque in stop. Tienilo collegato
  all'alimentatore e, se possibile, limita la carica della batteria all'80%.
- Tieni aperta **una sola copia** di Mirto.
- **In background**: chiudendo la finestra Mirto chiede se restare in background (con *Non chiederlo più* non
  lo chiede più). La sorveglianza continua e Mirto resta come icona nella barra dei menu (macOS) o nell'area di
  notifica (Windows), con il colore dello stato: verde protetta, ambra in pausa, grigio non protetta, rosso
  persona rilevata. Un clic riapre la finestra; il menu dell'icona offre **Apri Mirto**, **Attiva/Disattiva
  protezione** ed **Esci da Mirto**.
- **Per chiudere del tutto**: **Mirto → Esci da Mirto** (⌘Q / Ctrl+Q) e conferma. La sorveglianza si ferma.

---

## 15. Prove e diagnostica

Le prove si trovano in **Impostazioni → Prove** e nel menu **Prove**:

| Prova | Funzione |
|---|---|
| **Invia un avviso di prova completo** | Foto dal vivo su Telegram, email ed eventuali SMS, anche a protezione disattivata |
| **Messaggio Telegram di prova** / **Email di prova** / **SMS di prova** | Verifica un singolo canale |
| **Scatta una foto da tutte le camere** | Controlla le inquadrature |
| **Controlla i nodi** | Verifica che i nodi rispondano |
| **Esegui i test automatici del programma** | Controlla il funzionamento interno (circa 30 secondi) |

Strumenti di diagnostica:

- **Impostazioni → Nodi → Configurazione → Stato e diagnosi** per ogni nodo, con **Copia per l'assistenza**.
- **Aiuto → Apri la cartella dei log** per il registro dettagliato.
- **Aiuto → Informazioni su Mirto** per versione e build in uso.
- Dalla riga di comando, `--diagnostica` stampa un rapporto da allegare a una segnalazione, senza nomi delle
  camere, indirizzi, destinatari o token (vedi [Appendice B](#appendice-b-installazione-dai-sorgenti-e-riga-di-comando)).

---
## 16. Risoluzione dei problemi

### Camere e riconoscimento

| Problema | Soluzione |
|---|---|
| Immagine nera o camera che non si accende | Chiudi Mirto del tutto (⌘Q / Ctrl+Q) e riaprilo. Verifica il permesso in *Impostazioni di Sistema → Privacy e sicurezza → Fotocamera* (macOS). Dai sorgenti: `--diagnosi-camere` salva una foto per ogni camera |
| Il nome della camera non corrisponde all'immagine | In **Tutte le cam** premi **Non è questa?** e scegli la camera giusta |
| Non arrivano avvisi | Lo stato deve essere **Protetta** e non *In pausa*; tra due prove aspetta 2 minuti; controlla le categorie in **AI → Camere** |
| Troppi falsi avvisi | Alza la soglia in **AI → Camere** di 0,05 alla volta; per animali e oggetti usa la loro soglia |
| Gli uccelli restano «Uccello» senza gruppo | Il modello delle specie si scarica al primo uccello (serve internet); sotto il 35% di sicurezza il gruppo non viene indicato |
| I suoni non vengono riconosciuti | **AI → Microfoni**: microfono spuntato? Su macOS concedi il permesso in *Privacy e sicurezza → Microfono*. La riga **Suoni** di *Sistema* indica i microfoni in ascolto |
| Il computer va in stop | Coperchio aperto, alimentatore collegato, **Impedisci la sospensione del computer** attiva |

### Notifiche

| Problema | Soluzione |
|---|---|
| Il bot non risponde a `/id` | Mirto deve essere aperto, con Telegram spuntato e il token salvato |
| Telegram in rosso nel riquadro *Sistema* | Passa il mouse sulla riga per leggere l'errore; verifica la connessione a internet |
| «Un'altra copia di Mirto usa lo stesso bot» | Deve esserci un solo Mirto per bot: chiudi le altre copie o crea un secondo bot |
| «Utente non autorizzato» sui pulsanti | Aggiungi il tuo ID utente in **Utenti autorizzati** |
| Email in errore | Controlla la password per le app (senza spazi) e la verifica in due passaggi di Google |

### Presenza

| Problema | Soluzione |
|---|---|
| Risulto «fuori» ma sono in casa | Il telefono deve avere l'indirizzo Wi-Fi privato **Fisso** e lo stesso MAC registrato in **Presenza**; aumenta i minuti di «fuori» |
| *Cerca dispositivi* non trova il telefono | Sblocca il telefono e ripeti la ricerca |

### Nodi

| Problema | Soluzione |
|---|---|
| Un nodo si comporta in modo anomalo | **Configurazione → Stato e diagnosi**: indica cosa non va e come rimediare. **Copia per l'assistenza** prepara un rapporto senza password né PIN |
| «Nuovo nodo trovato» non compare | Il nodo è già abbinato oppure sono trascorsi i 5 minuti di una riapertura: riapri l'abbinamento (**Altri computer** da un computer già abbinato, oppure sul nodo) e premi **Cerca nodi in rete** |
| «PIN sbagliato» | Ogni apertura genera un PIN nuovo; dopo 5 tentativi errati la finestra si chiude e va riaperta |
| Avviso «Nodo … abbinato da …» su Telegram | Un altro computer si è abbinato. Se non lo riconosci, revocalo in **Altri computer → Stacca…** oppure sul nodo con `mirto-nodo abbina --revoca-tutti` |
| Camera del nodo nera o non raggiungibile | **Configurazione → Camera**: se il nodo non vede nessuna camera, spegnilo e ricollega il cavo piatto (contatti verso il connettore HDMI) |
| Il nodo non si collega al Wi-Fi | Lascialo sul cavo e usa **Configurazione → Wi-Fi**: se la rete non compare o il segnale è sotto il 40%, avvicina il nodo al router. Stacca il cavo solo quando compare il nuovo indirizzo |
| La sirena non suona | **Configurazione → Audio**: verifica l'uscita e premi **Suono di prova**. Una cassa USB spenta non è distinguibile da una accesa: controlla l'interruttore |
| La cassa Bluetooth risulta «non più abbinata» o non risponde | Spesso è collegata a un telefono o ha sostituito la chiave del nodo con quella di un altro dispositivo. Metti la cassa in abbinamento (tasto Bluetooth che lampeggia veloce) e premi **Abbina** in **Configurazione → Audio**; spegni il Bluetooth sul telefono se necessario. Per la sirena preferisci jack o USB |
| Il dissuasore non scatta | Il nodo deve essere in linea; controlla obiettivi, pausa tra due scatti, limite giornaliero, fascia oraria e budget orario (l'avviso indica quando riprende) |
| `/ascolta` risponde «Audio non disponibile» | Collega un microfono USB al nodo e accendilo in **Configurazione → Audio → Microfono** |
| Aggiornamento del nodo non riuscito | **Stato e diagnosi** riporta il motivo; il nodo resta alla versione precedente e l'aggiornamento si può ripetere |

Il registro dettagliato si apre da **Aiuto → Apri la cartella dei log**.

---

## 17. Sicurezza, privacy e condizioni d'uso

### Sicurezza

- Token, password email, chiavi dei nodi e password delle camere sono nel **Portachiavi** (macOS) o nelle
  **Credenziali Windows**, mai in file di testo. Nei registri gli indirizzi delle camere compaiono con la
  password nascosta.
- Nessuna porta viene aperta sul router: è Mirto a contattare Telegram.
- Il bot obbedisce solo agli **utenti autorizzati** e ignora i comandi inviati mentre Mirto era spento.
- I nodi comunicano su HTTPS con certificato verificato e richieste firmate; ogni computer abbinato ha una
  chiave propria e può essere revocato. Il servizio sul nodo non gira come amministratore.

### Privacy

- Riconoscimento di immagini e suoni avviene sul computer. Le immagini escono solo come foto e clip degli
  avvisi; l'audio dell'ascolto non viene salvato né inviato.
- La presenza legge solo gli indirizzi dei dispositivi sulla rete locale; nessun dato esce dal computer.
- Le statistiche d'uso sono facoltative e anonime (vedi [14](#14-impostazioni-generali-e-funzionamento-continuo)
  e `PRIVACY.md`).
- Informa familiari, ospiti e collaboratori domestici della presenza di camere, microfoni e del controllo dei
  telefoni.

### Condizioni d'uso

Sono le stesse accettate al primo avvio e consultabili in **Aiuto → Condizioni d'uso**:

- **Solo software**: Mirto analizza immagini e invia comandi ai nodi e alle loro uscite. Scegliere,
  installare e usare in modo conforme i dispositivi collegati è responsabilità di chi li installa.
- **Inquadra solo la tua proprietà**: per strada, scale e cortili comuni valgono le regole sulla protezione dei
  dati (in Italia del Garante Privacy): inquadratura limitata e cartello «area videosorvegliata».
- **Avvisa chi vive o lavora in casa**: per i collaboratori valgono anche le regole sul controllo a distanza dei
  lavoratori (Statuto dei lavoratori, art. 4).
- **Nessun dispositivo deve poter fare del male**, ferire, mettere in pericolo o impedire di uscire; evita
  allarmi acustici ripetuti senza motivo (si fermano comunque dopo 3 minuti).
- **Nessuna garanzia**: Mirto non è un antifurto certificato e non sostituisce un servizio di vigilanza; può
  non rilevare un evento o non inviare un avviso. È fornito «così com'è» con licenza Apache 2.0.
- **Uso a tuo rischio**, nei limiti consentiti dalla legge.

---

## Appendice A. Comandi Telegram

| Comando | Inglese | Funzione |
|---|---|---|
| `/stato` | `/status` | Stato di camere, nodi, presenza, microfoni e prossimo orario programmato |
| `/foto [camera]` | `/photo`, `/snapshot` | Foto istantanea (es. `/foto ingresso`) |
| `/attiva` | `/arm` | Attiva la protezione (anche `/inserisci`) |
| `/disattiva` | `/disarm` | Disattiva la protezione (anche `/disinserisci`) |
| `/pausa [minuti]` | `/pause` | Sospende gli avvisi (di serie 60 minuti) |
| `/riprendi` | `/resume` | Riattiva gli avvisi |
| `/ferma` | `/stop` | Ferma tutte le azioni e disattiva la protezione |
| `/programma [sospendi\|riattiva]` | `/schedule` | Mostra, sospende o riattiva gli orari automatici |
| `/ascolta [secondi]` | `/listen` | Registra dal microfono del nodo e invia l'audio nel gruppo |
| `/clip [camera] [secondi]` | `/video` | Video degli ultimi secondi (di serie 30, massimo 120) di una camera che registra |
| `/id` | — | Mostra gli ID di chat e utente |
| `/aiuto` | `/help` | Elenco dei comandi |

Un **messaggio vocale** inviato al bot viene riprodotto dalla cassa del nodo. L'elenco è disponibile anche in
**Aiuto → Comandi Telegram**.

---

## Appendice B. Installazione dai sorgenti e riga di comando

### macOS

Serve Python 3.11 o più recente ([python.org](https://www.python.org/downloads/) o `brew install python`):

```bash
cd ~/Documents/Mirto
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m mirto
```

Su macOS il permesso della fotocamera e del microfono viene chiesto per l'app da cui avvii Mirto (per esempio
il Terminale).

### Windows

In PowerShell. Installa Python da python.org o con winget, **non** dal Microsoft Store: quella versione gira in
una sandbox e può non vedere le camere USB.

```powershell
winget install --id Git.Git -e
winget install --id Python.Python.3.12 -e
# chiudi e riapri PowerShell
git clone https://github.com/marcopogo81/mirto.git
cd mirto
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m mirto
```

### Opzioni da riga di comando

Si usano con `python -m mirto <opzione>` (su Windows `.\.venv\Scripts\python.exe -m mirto <opzione>`):

| Opzione | Inglese | Funzione |
|---|---|---|
| `--elenca-camere` | `--list-cameras` | Elenca le camere con il loro ID stabile |
| `--prova-camera ID` | `--test-camera` | Salva una foto di prova dalla camera indicata (`--output` per il nome del file) |
| `--diagnosi-camere` | `--diagnose-cameras` | Prova tutti gli indici delle camere e salva una foto per ciascuno |
| `--senza-interfaccia` | `--headless` | Avvia Mirto senza finestra |
| `--verifica-modelli` | `--check-models` | Scarica e prova i modelli delle specie degli uccelli e dei suoni |
| `--prova-registrazione [S]` | `--test-recording` | Registra per S secondi (di serie 60) da ogni camera attiva e stima spazio e carico (con Mirto chiuso) |
| `--diagnostica` | `--diagnostics` | Stampa un rapporto da allegare a una segnalazione, senza dati personali |
| `--simulatore-nodo` | `--node-simulator` | Avvia un nodo simulato per le prove (`--porta`, `--nome-nodo`) |
| `--version` | — | Mostra la versione |
| `-v` | `--verbose` | Registro dettagliato |

---

## Appendice C. Comandi del nodo

### Installare il software del nodo su Raspberry Pi OS

Dal computer, nella cartella dei sorgenti di Mirto (`<utente>` e `<nodo>` sono quelli scelti in Raspberry Pi
Imager):

```bash
scp -r node <utente>@<nodo>.local:
ssh -t <utente>@<nodo>.local "sudo ./node/install.sh"
```

L'installazione configura il servizio del nodo, la camera (MediaMTX con password sullo stream), l'audio,
l'annuncio in rete e il comando `mirto-nodo`; al termine mostra indirizzo, impronta e PIN. Serve una volta
sola: gli aggiornamenti successivi si inviano da Mirto (vedi [12.5](#125-aggiornare-un-nodo)).

### Il comando `mirto-nodo`

Sul Raspberry, via SSH. Senza argomenti apre un menu. Tutte le funzioni sono disponibili anche da Mirto.

| Comando | Funzione |
|---|---|
| `mirto-nodo stato` | Riepilogo: servizi, camera, audio, rete, temperatura, impronta, abbinamento, registrazione |
| `mirto-nodo wifi` | Collega il nodo a una rete Wi-Fi |
| `mirto-nodo audio` | Uscita della sirena: Bluetooth, jack 3,5 mm, USB o HDMI, con suono di prova |
| `mirto-nodo camera` | Camera normale o NoIR, risoluzione, camera spenta |
| `mirto-nodo nome Garage` | Cambia l'etichetta del nodo (il nome in rete non cambia) |
| `mirto-nodo abbina` | Riapre l'abbinamento per 5 minuti e mostra il PIN |
| `mirto-nodo abbina --elenco` | Elenca i Mirto abbinati |
| `mirto-nodo abbina --revoca NOME` | Revoca un Mirto (`--revoca installazione` ritira la chiave condivisa delle installazioni precedenti alla 0.6) |
| `mirto-nodo abbina --revoca-tutti` | Revoca tutti i Mirto e riapre l'abbinamento |
| `mirto-nodo disco` | Prepara e usa un SSD o una chiavetta USB per la registrazione |
| `mirto-nodo prepara-immagine` | Pulisce il nodo per farne un'immagine da copiare su altre schede, poi lo spegne |

**Riaprire l'abbinamento senza terminale**: crea un file vuoto `mirto-abbina` nella partizione di avvio della
microSD; all'accensione successiva il nodo riapre l'abbinamento e scrive il PIN in `mirto-pin.txt`.

**Più nodi uguali.** Su un nodo funzionante, `mirto-nodo prepara-immagine` (conferma scrivendo `PREPARA`)
rimuove Wi-Fi, chiavi, certificato, password, casse abbinate, registri e video, poi spegne il Raspberry. Fai prima
una copia di sicurezza della scheda. Al primo avvio ogni copia diventa unica (`mirto-node-XXXX.local`, chiavi
nuove, abbinamento aperto con il PIN in `mirto-pin.txt`). Dettagli in `node/README.md`.

Contratto API, sicurezza e dettagli tecnici del nodo: `node/README.md`.

---

## Appendice D. Dove sono i dati

| Contenuto | macOS | Windows |
|---|---|---|
| Configurazione | `~/Library/Application Support/Mirto/config.yaml` | `%LOCALAPPDATA%\Mirto\config.yaml` |
| Foto e clip degli eventi | `…/Mirto/events/` | `%LOCALAPPDATA%\Mirto\events\` |
| Registrazioni | `…/Mirto/recordings/` (o la cartella scelta) | `%LOCALAPPDATA%\Mirto\recordings\` (o la cartella scelta) |
| Registri | `…/Mirto/logs/` | `%LOCALAPPDATA%\Mirto\logs\` |
| Modelli di riconoscimento | `…/Mirto/models/` | `%LOCALAPPDATA%\Mirto\models\` |
| Credenziali | Portachiavi (voce «Mirto») | Credenziali Windows (*Pannello di controllo → Account utente → Gestione credenziali*) |

Il file di configurazione è leggibile solo dal tuo account (e dagli amministratori del computer).

---

## Appendice E. Valori predefiniti e limiti

| Voce | Valore |
|---|---|
| Soglia persona per gli avvisi | 40% |
| Soglia persona per le azioni antifurto | 60% |
| Soglia animali, veicoli e oggetti | 50% |
| Soglia del gruppo di uccelli | 35% |
| Pausa tra due avvisi della stessa camera o dello stesso suono | 120 secondi |
| Conservazione di foto ed eventi | 30 giorni |
| Registrazione di serie | Solo eventi, 10 s prima e 20 s dopo |
| Conservazione della registrazione continua | 7 giorni |
| Spazio massimo / spazio sempre libero | 50% del disponibile / 2 GB |
| Presenza: «fuori» dopo | 15 minuti |
| SMS al giorno | 20 |
| `/pausa` e Silenzia | 60 minuti |
| `/clip` | 30 secondi (massimo 120) |
| Sirena (limite del nodo) | 3 minuti per volta, 3 minuti ogni 30 minuti |
| Dissuasore (limite del nodo) | 30 secondi per scatto, 2 minuti ogni ora |
| Dissuasore dalla Libreria | Pausa 5 minuti, 40 scatti al giorno |
| Modalità Prank | 10, 30, 60 o 120 minuti |
| Ferma tutto | Sospende i dissuasori per 1 ora |
| Abbinamento | Senza scadenza sul nodo mai abbinato, poi 5 minuti; 5 PIN errati chiudono la finestra |
| Registrazione sul nodo senza disco | Circa 2 GB sulla microSD |
| Registrazione sul nodo con disco | Ad anello, 10% del disco sempre libero |
