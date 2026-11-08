# Privacy (italiano)

> Questa è la traduzione italiana di [`PRIVACY.md`](PRIVACY.md), che resta la versione di
> riferimento: se le due dicessero cose diverse, vale l'inglese. Esiste perché un'informativa che
> chi la legge non capisce non informa nessuno — ed è l'unico documento del progetto tenuto in due
> lingue, per questo motivo e per nessun altro.

Mirto funziona in locale: video, foto e audio restano sul tuo computer; gli avvisi vanno solo ai canali che
configuri. Oltre a questo, Mirto invia solo ciò che segue.

Una cosa è cambiata il 26 ottobre 2026, e non riguarda internet: un nodo con un microfono può registrarlo
**dentro** il proprio video, quindi le clip di quel nodo — compresa quella che arriva su Telegram — hanno il
suono. È **spenta di serie** e si accende un nodo per volta (*Impostazioni → Nodi → Disco*), perché l'audio ha
regole molto più severe del video: in Italia il Garante considera la registrazione audio nella videosorveglianza
eccedente nella gran parte dei casi. Il resto di questa pagina non cambia: quell'audio resta sul tuo computer e
sul nodo, e va solo ai canali di avviso che hai configurato.

- **Statistiche d'uso (solo con il tuo sì)**: al primo avvio Mirto chiede; la risposta predefinita è **no**
  (chiudere la finestra senza scegliere = te lo richiede la volta dopo). Con il sì, una volta al giorno: un numero
  creato a caso, versione, sistema, lingua, camere e nodi per fasce, modalità di registrazione e funzioni accese.
  Il numero resta lo stesso finché non lo rinnovi: i dati sono **pseudonimi**, non del tutto anonimi. Rinnovandolo
  o ritirando il consenso, i dati del numero vecchio vengono **cancellati dal server**. Con il no: **una sola
  volta** un «no» con la sola versione di Mirto, senza numero, per contare anche chi rifiuta. Mai foto, video,
  audio, nomi, indirizzi, dati di Telegram, posta o SMS. Si cambia in **Impostazioni → Generale**, dove
  **Cosa viene inviato…** mostra il messaggio esatto. Base giuridica: il consenso (art. 6.1.a GDPR).
- **Controllo degli aggiornamenti** (disattivabile): una volta al giorno chiede qual è l'ultima versione; l'unico
  dato è la versione di Mirto (nell'intestazione User-Agent). Avviene anche prima della risposta sulle statistiche.
- **Il server** (`tools/stats-worker`, Cloudflare): il nostro codice non legge né salva gli indirizzi IP e i
  registri delle richieste sono spenti; Cloudflare, come fornitore (responsabile del trattamento), tratta l'IP solo
  per consegnare la richiesta. I dati si cancellano 13 mesi dopo l'ultimo messaggio di un'installazione.
- **Modelli di riconoscimento**: non sono inclusi. Al primo uso Mirto li scarica una volta sola da
  **github.com** (persone e oggetti, specie degli uccelli) o **huggingface.co** (suoni) e li verifica con
  un'impronta SHA-256 scritta nel codice. Quei server vedono quello che vede chiunque consegni un file: il
  tuo indirizzo IP, l'ora e quale file hai chiesto — come se avessi aperto il link in un browser. **Non
  parte nulla di tuo**: nessun identificativo, nessuna statistica, nessuna impostazione. Se preferisci
  evitare anche questa connessione, metti i file nella cartella dei modelli (o indicala con
  `MIRTO_MODELS_DIR`) prima di accendere il riconoscimento.
- **Titolare**: il responsabile del progetto Mirto, Marco Dalicco; contatto tramite la pagina GitHub del
  progetto o il canale riservato descritto in [`SECURITY.md`](SECURITY.md).
