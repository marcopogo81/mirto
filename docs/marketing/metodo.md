# Video di marketing — metodo

Come si producono i filmati dei casi d'uso. I testi sono in [prompts.md](prompts.md).
Tutto gratuito e in locale, su un PC con RTX 5070 (12 GB).

## Strumenti

- **ComfyUI Desktop** (comfy.org/download).
- **Wan 2.2 14B, testo→video**: template ufficiale «Wan 2.2 14B: Da testo a video». I modelli
  (due file fp8_scaled da 13,3 GB, due LoRA lightx2v, VAE wan_2.1, text encoder umt5_xxl) si
  scaricano dal pannello «Modelli mancanti» del template: circa 29 GB.
- **Impostazioni:** 832×480, durata 6 secondi.
  - `enable_turbo_mode` **spento** per le versioni buone: 20 passi, resa molto più rifinita,
    tempi lunghi.
  - `enable_turbo_mode` acceso per provare un testo: 4 passi, circa 2 minuti e mezzo a clip.

## Come si lavora

1. **Una scena alla volta**, due varianti per tentativo (il seed cambia da solo).
2. **Una sola modifica per giro**, così si capisce cosa ha funzionato.
3. Il video buono si copia fuori dalla cartella `output` di ComfyUI e il suo prompt si registra
   in `prompts.md` con il nome del file.
4. La coda di ComfyUI non sopravvive a un riavvio o a un'interruzione di corrente: lanciare poche
   clip alla volta.

## Cosa abbiamo scartato

- **Wan 2.2 5B** (il modello piccolo): 9 minuti a clip, oggetti che compaiono e scompaiono, macchie.
- **Animare le carte SVG** (immagine→video): il modello non regge il fumetto a campiture piatte.
  Si genera da zero, solo testo; le carte si rifaranno dai fotogrammi dei video.
- **Stili provati e non scelti:** film 3D, disegno a mano anni '50, stop-motion gotico, sitcom 2D
  piatta. Scelto l'anime TV giapponese.
- **Pesi tra parentesi** `(testo:1.3)`: ComfyUI li accetta, ma con Wan rendono poco. Rendono di
  più l'ordine delle frasi, togliere azioni secondarie, più tentativi e il turbo spento.

## Cosa manca

- Riscrivere e approvare le altre undici scene.
- **Audio e scritte in montaggio**: i testi delle carte, il suono che il nodo emette davvero per
  quella scena (per i piccioni, il verso d'allarme di cornacchia), il logo in chiusura.
  Carattere, colori e posizione uguali in tutti i video.
- Mostrare **il prodotto vero** con la registrazione dello schermo: nei filmati telecamera e nodo
  sono disegnati dal modello.
- Versione definitiva a 1280×720 delle scene approvate.
