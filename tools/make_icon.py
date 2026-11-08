# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Icona di Mirto: un cespuglio di mirto con l'obiettivo di una camera dentro.

Il nome e' una pianta, il programma guarda. Il mirto e' un arbusto mediterraneo basso e
cespuglioso, e un cespuglio e' una massa tondeggiante: si riconosce da lontano, che e'
l'unica cosa che conta in un'icona.

**Un verde solo, su bianco**: la pianta in tinta unita, il fondo bianco. Il blu sta solo
nella lente, che e' l'unica cosa che deve attirare l'occhio. Le versioni con tre o quattro
verdi, aloni, patine lucide e ombre sui singoli lobi erano tutte piu' sporche di questa:
piu' tonalita' non fanno piu' profondita', fanno fango (Marco, 7/11).

Le versioni scartate, tutte lo stesso giorno, e perche':

* foglie sparse con un ramo che non toccava niente, e una lente con l'anello bianco che
  sembrava un occhio da cartone animato;
* un rametto verticale con la bacca appesa sotto: pendeva da una parte;
* una corona di foglie radiali intorno alla lente: faceva un timone di nave;
* il cespuglio con nove mirtilli: «uno scarafaggio maculato»;
* la versione lucida con fondo vivo e chioma chiara: i due verdi si impastavano in alto, e
  i due mirtilli alti piu' la lente facevano una faccia.

Le misure della tela non sono a occhio: su 1024 il corpo dell'icona e' 824, cioe' **100 px
di margine per lato**, raggio 185,4, ombra sfocata 28 px e spostata 12 px in giu'. Il
margine serve perche' macOS **non** ritaglia da solo l'icona di un'applicazione: la sagoma
la si disegna, e senza margine finisce attaccata al bordo e piu' grande delle altre nel Dock.

Uso: python make_icon.py <cartella di destinazione>
     python tools/make_icon.py mirto/gui/icons/app   (dalla radice del progetto)
Crea app.png (1024 px), app_256.png, app.ico (Windows) e app.icns (macOS).
"""
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

S = 1024          # dimensione finale
K = 4             # sovracampionamento per bordi morbidi
N = S * K

# La tela, secondo la griglia che si usa per le icone di macOS.
MARGINE = 100 / 1024
RAGGIO = 185.4 / 1024
OMBRA_GIU = 12 / 1024
OMBRA_SFOCA = 28 / 1024

# I due verdi. Tutto il disegno e' fatto con questi.
FONDO = "#FFFFFF"         # il fondo: bianco, cosi' la pianta e' la sola cosa che si vede
PIANTA = "#15673F"        # il cespuglio, in tinta unita

# Sulla tinta unita il verde va tirato su: lo scuro di prima, senza sfumature e sul bianco,
# sembrava nero. E niente foglie disegnate dentro la chioma: si vedevano solo a grandezza piena,
# a 32 px sparivano gia'. Quel che non si vede a 16 px non lavora, sporca (Marco, 7/11).

# La lente: fuori dal verde apposta, perche' e' la sola cosa che deve prendere l'occhio.
VETRO = "#6AA8F0"
BUIO = "#141230"

# I lobi della chioma: scostamento dal centro, altezza, raggio, in frazioni del lato. Scritti a
# mano invece che casuali, perche' un'icona deve venire identica a ogni esecuzione: altrimenti
# due compilazioni hanno due icone diverse.
LOBI = ((-0.252, 0.618, 0.124),
        (-0.132, 0.530, 0.166),
        ( 0.008, 0.492, 0.192),
        ( 0.150, 0.538, 0.160),
        ( 0.262, 0.622, 0.120))
BASE = 0.760        # dove il cespuglio appoggia

# I mirtilli fra le foglie. Tre: con nove, tanti puntini scuri su una massa fanno un insetto.
# Bassi e sfalsati: all'altezza della lente facevano due occhi, e la lente in mezzo una faccia.
MIRTILLI = ((-0.286, 0.578, 0.042), (0.252, 0.636, 0.038))


def alone(centro, raggio, colore, forza, sfoca):
    """Una luce tonda e sfumata. Serve **intorno alla lente**, non dietro la pianta: dietro la
    pianta schiarisce il fondo proprio dove sta la sagoma e si mangia il contrasto (Marco, 7/11).
    """
    m = Image.new("L", (N, N), 0)
    cx, cy = centro
    ImageDraw.Draw(m).ellipse([cx - raggio, cy - raggio, cx + raggio, cy + raggio], fill=255)
    m = m.filter(ImageFilter.GaussianBlur(sfoca)).point(lambda v: int(v * forza))
    lay = Image.new("RGBA", (N, N), colore + (0,))
    lay.putalpha(m)
    return lay


def hexc(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def cespuglio(colore):
    """La massa di un arbusto: lobi tondi sopra, e sotto una base **piatta**.

    Il segno che distingue un cespuglio da una nuvola e' che il cespuglio **sta per terra**:
    tondo sopra, dritto sotto.
    """
    lay = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    cx = N * 0.50
    sinistra = destra = None
    for dx, y, r in LOBI:
        x, yy, rr = cx + N * dx, N * y, N * r
        d.ellipse([x - rr, yy - rr, x + rr, yy + rr], fill=colore)
        sinistra = x - rr if sinistra is None else min(sinistra, x - rr)
        destra = x + rr if destra is None else max(destra, x + rr)
    # il pieno fra i lobi e la terra, largo quanto tutta la chioma: fermandolo ai centri dei lobi
    # laterali restavano due scalini in basso
    d.rectangle([sinistra, N * max(y for _dx, y, _r in LOBI), destra, N * BASE], fill=colore)
    return lay


def make() -> Image.Image:
    img = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    pad = int(N * MARGINE)
    raggio = int(N * RAGGIO)

    mask = Image.new("L", (N, N), 0)
    ImageDraw.Draw(mask).rounded_rectangle([pad, pad, N - pad, N - pad], radius=raggio, fill=255)

    # l'ombra sotto l'icona: non e' decorazione, e' quello che la fa stare sul Dock invece che
    # dentro al Dock
    ombra = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    giu = N * OMBRA_GIU
    ImageDraw.Draw(ombra).rounded_rectangle([pad, pad + giu, N - pad, N - pad + giu],
                                            radius=raggio, fill=(0, 0, 0, 128))
    img.alpha_composite(ombra.filter(ImageFilter.GaussianBlur(N * OMBRA_SFOCA)))

    fondo = Image.new("RGBA", (N, N), hexc(FONDO) + (255,))
    img.paste(fondo, (0, 0), mask)

    cx = N * 0.50
    cy = N * 0.540                 # il centro visivo della chioma, non del quadrato
    R = N * 0.142                  # la bacca grande: e' lei la cam

    pianta = cespuglio(hexc(PIANTA) + (255,))
    pianta.putalpha(ImageChops.multiply(pianta.getchannel("A"), mask))
    img.alpha_composite(pianta)

    d = ImageDraw.Draw(img)

    def tonda(x, y, r, fill):
        d.ellipse([x - r, y - r, x + r, y + r], fill=fill)

    # i mirtilli: dello stesso buio della lente, non di un terzo verde
    for bx, by, br in MIRTILLI:
        tonda(cx + N * bx, N * by, N * br, hexc(BUIO) + (255,))

    # --- la lente: vetro, non un disco ---
    vetro = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    vd = ImageDraw.Draw(vetro)

    def cerchio(dis, x, y, r, fill):
        dis.ellipse([x - r, y - r, x + r, y + r], fill=fill)

    # il bagliore che butta intorno: e' questo a farla sembrare accesa invece che disegnata.
    # **Ritagliato sulla maschera**: senza, sfonda il quadrato e l'icona finisce dentro un alone
    # azzurro grande come tutta la tela (7/11).
    bagliore = alone((cx, cy), R * 1.9, hexc(VETRO), 0.30, R * 0.70)
    bagliore.putalpha(ImageChops.multiply(bagliore.getchannel("A"), mask))
    img.alpha_composite(bagliore)

    cerchio(vd, cx, cy, R, hexc(BUIO) + (255,))               # il barilotto
    cerchio(vd, cx, cy, R * 0.62, hexc(VETRO) + (255,))       # il vetro
    # l'anello concentrico dentro il vetro: e' questo che fa «obiettivo» invece che «pupilla».
    # Senza, il riflesso bianco piu' il tondo scuro al centro leggono come un occhio.
    vd.ellipse([cx - R * 0.44, cy - R * 0.44, cx + R * 0.44, cy + R * 0.44],
               outline=hexc(BUIO) + (190,), width=int(N * 0.007))
    cerchio(vd, cx, cy, R * 0.24, hexc("#05060E") + (255,))   # la pupilla

    # il bombato: luce dall'alto e luce di ritorno dal basso. Sono queste due, insieme, a far
    # leggere una calotta di vetro invece di un cerchio piatto.
    vetro.alpha_composite(alone((cx - R * 0.20, cy - R * 0.26), R * 0.46,
                                (255, 255, 255), 0.42, R * 0.20))
    vetro.alpha_composite(alone((cx + R * 0.18, cy + R * 0.30), R * 0.34,
                                hexc("#BBDBFF"), 0.34, R * 0.18))

    # il filo di luce sul bordo del barilotto: il vetro finisce contro qualcosa
    bordo = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    ImageDraw.Draw(bordo).arc([cx - R * 0.93, cy - R * 0.93, cx + R * 0.93, cy + R * 0.93],
                              start=186, end=256, fill=(255, 255, 255, 120), width=int(N * 0.008))
    vetro.alpha_composite(bordo.filter(ImageFilter.GaussianBlur(N * 0.003)))

    # un solo riflesso netto: due o tre fanno vetro sporco
    cerchio(ImageDraw.Draw(vetro), cx - R * 0.31, cy - R * 0.33, R * 0.105, (255, 255, 255, 245))
    img.alpha_composite(vetro)

    # Niente patina di luce in alto: serviva quando il fondo era verde scuro. Su bianco non
    # schiarisce niente e lascia solo una velatura grigia nell'angolo.
    return img.resize((S, S), Image.LANCZOS)


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    icon = make()
    icon.save(out / "app.png")
    icon.resize((256, 256), Image.LANCZOS).save(out / "app_256.png")
    icon.save(out / "app.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    icon.save(out / "app.icns")
    print("creati:", ", ".join(p.name for p in sorted(out.iterdir())))


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "."))
