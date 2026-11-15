# Video di marketing — prompt

Prompt per i filmati brevi che mostrano i casi d'uso delle [carte](../cards.md).
Come si generano, con che modello e cosa abbiamo imparato: vedi [metodo.md](metodo.md).

Stato al 15 novembre 2026: **una scena approvata su dodici** (05, i piccioni).

## Regole fisse

- **Il dispositivo Mirto è sempre lo stesso oggetto:** una scatolina verde con un solo foro
  tondo sul davanti, da cui riprende la camera. Sirena e microfono sono dentro e non si vedono:
  niente trombe, altoparlanti, microfoni, pali.
- **La misura si dà per confronto** («as small as a fist and much smaller than a pigeon»).
  Il solo «small» viene ignorato e la scatola esce grande come una piastrella.
- **Sequenza di ogni scena di rilevamento:** il soggetto entra da fuori campo e arriva → la lente
  diventa rossa → attorno al soggetto compare il riquadro verde di riconoscimento → la scatolina
  trema ed escono i fumetti → spavento e fuga fuori campo.
- **L'avviso alla persona** si mostra con un telefono: dalla scatolina parte una busta bianca che
  vola fino al telefono, che vibra. Del proprietario si vede solo la mano con il telefono.
- **Si scrive ciò che si vede, non ciò che significa.** «The box shakes» funziona; «sound waves
  pulse outward» ha prodotto una macchia blu.
- **Si dice anche cosa NON c'è all'inizio** e cosa resta fermo alla fine.

## Struttura

`[STILE] + [scena con posizioni] + [azione in ordine] + [cosa resta fermo] + [CHIUSURA]`

**Stile** (uguale per tutti):

> Japanese TV anime style, 2D cel-shaded animation, bold black outlines, bright saturated colors,
> big expressive eyes, wildly exaggerated comedic reaction faces, speed lines and dynamic poses,
> classic slapstick cartoon physics.

**Chiusura** (uguale per tutti):

> Static camera, medium wide shot, exaggerated squash and stretch, energetic anime animation.

## 05 — The balcony squatters · APPROVATA

Video di riferimento: `ComfyUI_00043_.mp4` (15/11/2026). Turbo spento, 832×480, 6 secondi.

```text
Japanese TV anime style, 2D cel-shaded animation, bold black outlines, bright saturated colors, big expressive eyes, wildly exaggerated comedic reaction faces, speed lines and dynamic poses, classic slapstick cartoon physics. Sunny morning on an empty house balcony with a railing and potted plants. On the left, mounted on the wall, is a tiny green box, as small as a fist and much smaller than a pigeon, with a single round hole on its front and a camera lens inside the hole; it has no other parts and it stays visible and unchanged for the whole shot. At first the railing is empty. Three fat gray pigeons fly in from the right and land on the railing in the center, then settle down and strut around smugly. While they fly in and land, the pigeons are plain, with nothing drawn around them or above them. Only after they have settled, the lens inside the hole glows red. Then a thin bright green rectangular outline appears around the whole body of each pigeon, like the detection boxes of an AI vision system. After the green outlines have appeared, the tiny green box shakes violently and comic-book speech balloons with the words BOOM BOOM pop out of it. The pigeons get a huge comical fright: they jump up with bulging eyes, feathers flying everywhere, then flap away in a fast blur out of the frame to the right, and the green outlines vanish. The railing, the plants and the tiny green box stay in place. Static camera, medium wide shot, exaggerated squash and stretch, energetic anime animation.
```

## 01 — The cat and the flower pot · bozza, da lanciare

Versione asciutta, sullo schema dei piccioni. Senza porta: nella prova precedente il gatto si
spaventava appena uscito, rientrava, poi tornava e giocava con il dispositivo.

```text
Japanese TV anime style, 2D cel-shaded animation, bold black outlines, bright saturated colors, big expressive eyes, wildly exaggerated comedic reaction faces, speed lines and dynamic poses, classic slapstick cartoon physics. Night on a house balcony with a low brick wall under a starry sky. On the far left, mounted on the wall, is a tiny green box, as small as a fist, with a single round hole on its front and a camera lens inside the hole; it stays unchanged for the whole shot and nothing ever touches it. In the center, a terracotta flower pot with pink flowers stands on the wall. At first there is no cat. A chubby white and gray cat walks in calmly from the right edge of the frame, goes all the way to the flower pot and sniffs the flowers. Only when the cat is sniffing the flower pot, the lens inside the hole glows red and the tiny green box shakes. The cat gets a big fright, jumps up, turns around and runs away out of the frame to the right. The cat does not come back. The balcony stays empty and still until the end, with the flower pot and the tiny green box in place. Static camera, medium wide shot, energetic anime animation.
```

## 02 — The night visitor · bozza, da lanciare

Il ladro ha un piede di porco (non un sacco: non si capiva cosa facesse). Dopo l'allarme partono
gli irrigatori. **Da decidere:** l'irrigazione comandata da Mirto è plausibile con l'uscita a relè
del nodo, ma non è una funzione dichiarata.

```text
Japanese TV anime style, 2D cel-shaded animation, bold black outlines, bright saturated colors, big expressive eyes, wildly exaggerated comedic reaction faces, speed lines and dynamic poses, classic slapstick cartoon physics. Night in the front garden of a house with a green lawn under a starry sky. In the center is the closed front door of the house. On the far left, mounted on the wall, is a tiny green box, as small as a fist, with a single round hole on its front and a camera lens inside the hole; it stays unchanged for the whole shot and nothing ever touches it. At first there is nobody. A burglar in a black mask and a striped shirt, holding a crowbar in his hand, tiptoes in from the right edge of the frame and goes all the way to the front door, then lifts the crowbar toward the door lock. Only when the crowbar is at the door, the lens inside the hole glows red and the tiny green box shakes. Then the lawn sprinklers pop up from the grass and spray jets of water all over the burglar. The soaked burglar gets a big fright, drops the crowbar, turns around and runs away out of the frame to the right while the water keeps spraying him. The burglar does not come back. The garden stays empty until the end, with the door closed and the tiny green box in place. Static camera, medium wide shot, energetic anime animation.
```

## Le altre scene (03, 04, 06–12)

Da riscrivere con le regole qui sopra. La tornata precedente è in
[prompts-v2-archivio.txt](prompts-v2-archivio.txt): resa buona, ma l'azione non regge
(«quello che avviene nei filmati non ha alcun senso», tranne i piccioni).
