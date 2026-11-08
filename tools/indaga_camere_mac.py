#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Chi è davvero la camera dietro ogni indice di OpenCV, su macOS.

Il 25/10 Marco ha visto una scheda intitolata «Fotocamera di iPhone 12» che mostrava l'immagine del
MacBook Air. Nome e ID vengono da AVFoundation; la camera però la apriamo **per indice** con OpenCV,
e i due elenchi non danno lo stesso ordine. Con due camere sole, «invertito» e «ordinato per un altro
criterio» sono indistinguibili: indovinare la regola vorrebbe dire scambiare le camere a qualcun
altro, su un altro Mac o con un'altra versione di OpenCV.

Questa sonda non indovina: **apre un indice per volta e guarda quale dispositivo si accorge di essere
usato.** Se un segnale di AVFoundation cambia in modo affidabile mentre quell'indice è aperto, allora
Mirto può tararsi da solo, una volta, su qualunque Mac — ed è la differenza fra una correzione e
una toppa.

Non tocca niente e non salva niente: stampa e basta. Si lancia così, con le camere libere (chiudi
Mirto, FaceTime, Photo Booth):

    cd ~/Documents/Mirto && .venv/bin/python tools/indaga_camere_mac.py 2>&1 | tee prove_indagine.txt
"""

from __future__ import annotations

import sys
import time

SEGNALI = ("isInUseByAnotherApplication", "isSuspended", "isConnected")


def dispositivi():
    import AVFoundation  # noqa: PLC0415

    video = list(AVFoundation.AVCaptureDevice.devicesWithMediaType_(AVFoundation.AVMediaTypeVideo) or [])
    muxed = list(AVFoundation.AVCaptureDevice.devicesWithMediaType_(AVFoundation.AVMediaTypeMuxed) or [])
    return video + muxed


def con_la_sessione():
    """L'altro elenco possibile: quello che Apple consiglia da quando il primo è deprecato.

    Se OpenCV usa questo e noi l'altro, l'ordine diverso è spiegato — e la correzione è usare lo
    stesso elenco, senza nessuna regola inventata.
    """
    import AVFoundation  # noqa: PLC0415

    tipi = []
    for nome in ("AVCaptureDeviceTypeBuiltInWideAngleCamera", "AVCaptureDeviceTypeExternal",
                 "AVCaptureDeviceTypeExternalUnknown", "AVCaptureDeviceTypeContinuityCamera",
                 "AVCaptureDeviceTypeDeskViewCamera"):
        tipo = getattr(AVFoundation, nome, None)
        if tipo is not None:
            tipi.append(tipo)
    try:
        sessione = AVFoundation.AVCaptureDeviceDiscoverySession.discoverySessionWithDeviceTypes_mediaType_position_(
            tipi, AVFoundation.AVMediaTypeVideo, 0)
        return list(sessione.devices() or [])
    except Exception as exc:  # noqa: BLE001
        print(f"  (sessione di scoperta non disponibile: {exc})")
        return []


def stato(devs) -> dict:
    fuori = {}
    for d in devs:
        riga = {}
        for s in SEGNALI:
            try:
                riga[s] = bool(getattr(d, s)())
            except Exception:  # noqa: BLE001
                riga[s] = None
        fuori[str(d.uniqueID())] = riga
    return fuori


def main() -> int:
    if sys.platform != "darwin":
        print("Questa sonda serve solo su macOS.")
        return 1
    import cv2  # noqa: PLC0415

    devs = dispositivi()
    nomi = {str(d.uniqueID()): str(d.localizedName()) for d in devs}
    print("Elenco 1 — devicesWithMediaType (quello che usa Mirto oggi):")
    for i, d in enumerate(devs):
        tipo = ""
        try:
            tipo = str(d.deviceType() or "")
        except Exception:  # noqa: BLE001
            pass
        print(f"  {i}  {str(d.localizedName()):<36} {d.uniqueID()}  {tipo}")

    print("\nElenco 2 — AVCaptureDeviceDiscoverySession (quello consigliato da Apple):")
    sessione = con_la_sessione()
    for i, d in enumerate(sessione):
        print(f"  {i}  {str(d.localizedName()):<36} {d.uniqueID()}")
    if sessione and [str(d.uniqueID()) for d in sessione] != [str(d.uniqueID()) for d in devs]:
        print("  >>> I DUE ELENCHI HANNO ORDINE DIVERSO: può bastare questo a spiegare tutto.")

    print("\nChi si accorge di essere usato, aprendo un indice per volta:")
    prima = stato(devs)
    for indice in range(len(devs)):
        cap = cv2.VideoCapture(indice, cv2.CAP_AVFOUNDATION)
        try:
            if not cap.isOpened():
                print(f"  indice {indice}: non si apre")
                continue
            cap.read()
            time.sleep(1.0)
            dopo = stato(dispositivi())
            cambiati = []
            for uid, valori in dopo.items():
                for s in SEGNALI:
                    if valori.get(s) != prima.get(uid, {}).get(s):
                        cambiati.append(f"{nomi.get(uid, uid)}: {s} {prima.get(uid, {}).get(s)} → {valori.get(s)}")
            print(f"  indice {indice}: " + ("; ".join(cambiati) if cambiati
                                            else "nessun segnale cambiato (questa strada non funziona)"))
        finally:
            cap.release()
            time.sleep(0.5)
    print("\nFine. Niente è stato cambiato: questa sonda legge soltanto.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
