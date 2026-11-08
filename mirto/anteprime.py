# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Quanto rimpicciolire un fotogramma prima di convertirlo in immagine Qt.

Un riquadro da 176 pixel che mostra una camera 1920×1080 converte due milioni di pixel, dieci volte al
secondo, per disegnarne diciassettemila: 98 fotogrammi su 100 vengono buttati dopo averli copiati due
volte, sul thread della finestra. Con sei camere sono circa 1 GB/s di memcpy, e la domanda che fanno
sempre a un programma così è «quante camere regge».

Il conto sta qui, fuori da Qt, perché è aritmetica e si prova senza interfaccia (il motivo per cui
esiste anche *gui/testi.py*). Due cose che non sono ovvie:

- **si rimpicciolisce al riquadro, non a una misura fissa.** Lo stesso fotogramma finisce in una
  piastrella grande quanto la finestra e in un'anteprima da 176 pixel; una misura fissa sarebbe
  sbagliata in uno dei due posti;
- **non si rimpicciolisce mai sotto quello che serve**, e mai quando il riquadro è più grande del
  fotogramma: rimpicciolire per poi ingrandire si vede, e si vedrebbe proprio sulla camera che
  qualcuno sta guardando a schermo pieno.

Il margine esiste perché un riquadro può crescere fra due fotogrammi — la finestra si allarga, o si
fa doppio clic per ingrandire una camera — e con 100 ms fra un fotogramma e l'altro l'immagine
resterebbe morbida per un istante. Un quarto in più di lato costa una volta e mezza i pixel del
riquadro, che su un'anteprima da 176 pixel resta il 2 % di un fotogramma 1080p: il margine non
rimette in gioco il risparmio.

**Misurato** il 26/10 su 1920×1080 (container Linux, x86_64, OpenCV 5.0.0, due core), per fotogramma:
conversione intera 4,7 ms · anteprima da 176 px 0,16 ms · piastrella da 800 px veri 1,0 ms. Con sei
camere a 10 fotogrammi al secondo sono 280 ms di thread della finestra ogni secondo prima, 60 dopo.
I numeri servono per il rapporto fra loro, non come promessa: su un'altra macchina cambiano tutti.
"""

from __future__ import annotations

MARGINE = 1.25
"""Quanto più grande del riquadro si tiene l'immagine, per un riquadro che cresce fra due fotogrammi."""


def misura_anteprima(frame_l: int, frame_a: int, riquadro_l: int, riquadro_a: int,
                     margine: float = MARGINE, minimo: int = 0) -> tuple[int, int] | None:
    """Le misure a cui ridurre il fotogramma, o ``None`` se non c'è niente da guadagnare.

    ``riquadro_*`` sono i pixel veri in cui l'immagine verrà disegnata (su uno schermo Retina sono il
    doppio di quelli della finestra: chiederli in pixel logici darebbe un'immagine morbida su metà dei
    Mac). ``minimo`` è la larghezza sotto la quale non si scende, per chi disegna in un riquadro che
    può crescere mentre l'immagine è ferma.

    Le proporzioni si tengono: è così che l'immagine viene disegnata dentro il riquadro, e cambiarle
    qui vorrebbe dire deformarla.
    """
    if min(frame_l, frame_a, riquadro_l, riquadro_a) <= 0:
        return None
    scala = min(riquadro_l / frame_l, riquadro_a / frame_a) * margine
    if minimo > 0:
        scala = max(scala, minimo / frame_l)
    if scala >= 1.0:
        return None          # il riquadro è più grande del fotogramma: ridurre sarebbe perdere
    nuova_l, nuova_a = int(round(frame_l * scala)), int(round(frame_a * scala))
    if nuova_l < 2 or nuova_a < 2 or nuova_l >= frame_l or nuova_a >= frame_a:
        return None          # arrotondato a niente, o a quello che c'era già
    return nuova_l, nuova_a
