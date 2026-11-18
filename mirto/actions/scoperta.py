# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Home Assistant trova Mirto da solo, senza che nessuno scriva una riga di YAML.

Passo 6 del piano domotica, l'ultimo. **È il verso opposto della via 1 del passo 1**: gli stessi
messaggi ``homeassistant/<componente>/<id>/config`` che Mirto *legge* per trovare le lampadine di
casa, scritti invece di letti. Chi li scrive compare in Home Assistant; chi non li scrive va
aggiunto a mano, una voce per volta, da chi sa cos'è un argomento MQTT — cioè quasi nessuno.

**Verificato il 17/11/2026 sulla fonte, non a memoria**, perché sbagliare qui vuol dire che in Home
Assistant non compare niente e nessuno sa perché:

- l'argomento è ``<prefisso>/<componente>/[<nodo>/]<oggetto>/config``, e il prefisso di serie è
  ``homeassistant`` (documentazione di Home Assistant);
- **un carico vuoto cancella**: lo dice il codice di Home Assistant, non la documentazione, che su
  questo tace. È la parte che quasi nessuno fa, ed è quella che evita di lasciare sensori fantasma
  in Home Assistant quando Mirto se ne va.

**Perché una voce per componente e non il modo «a dispositivo».** Home Assistant ha aggiunto un
secondo modo, un messaggio solo che descrive tutto il dispositivo. È più ordinato e funziona solo
dalle versioni nuove. Il modo per componente funziona su tutto quello che c'è in giro, e chi ha un
Home Assistant vecchio di due anni su un Raspberry in cantina è esattamente la persona che Mirto si
aspetta di trovare. Quando il modo nuovo sarà il solo che conta, questa scelta si rifà qui, in un
posto solo.

**Tutte ritenute.** Un messaggio di configurazione non ritenuto lo vedrebbe solo un Home Assistant
già acceso in quel momento: riavviandolo, Mirto sparirebbe dalla sua lista fino al riavvio di Mirto.
Ritenute, invece, il broker le ripete a chi arriva, ed è il motivo per cui questa cosa funziona.
"""
from __future__ import annotations

import json

from ..i18n import _
from . import racconto as R

# Il ramo dove Home Assistant ascolta. Configurabile perché si può cambiare dalla sua parte, e chi
# lo ha cambiato non deve ritrovarsi Mirto invisibile senza capire perché.
PREFISSO_SCOPERTA = "homeassistant"
# Il «nodo» dentro l'argomento: raggruppa le voci di Mirto e le rende cancellabili tutte insieme.
NODO = "mirto"


def _device(versione: str) -> dict:
    """Il blocco che fa comparire **un dispositivo** in Home Assistant invece di sensori sparsi.

    Senza, le voci di Mirto finirebbero in mezzo a tutto il resto come entità senza padre: chi ha
    quaranta cose attaccate al broker non le ritroverebbe. Con questo, in Home Assistant c'è una
    scheda «Mirto» e dentro le sue.
    """
    return {
        "identifiers": [NODO],
        "name": "Mirto",
        "manufacturer": "Mirto",
        "model": "Mirto",
        "sw_version": str(versione or ""),
    }


def _disponibilita(prefisso: str) -> dict:
    """Come Home Assistant sa se Mirto c'è. Lo stesso argomento del testamento.

    Serve perché un sensore che resta sull'ultimo valore noto quando Mirto è spento **mente**: chi
    guarda il pannello vede «casa protetta» di un Mirto che non è in funzione da due giorni. Con
    questo, Home Assistant li mostra come «non disponibili», che è la verità.
    """
    return {
        "availability_topic": R.argomento_stato(prefisso),
        "payload_available": R.ONLINE.decode(),
        "payload_not_available": R.OFFLINE.decode(),
    }


def voci(prefisso: str = R.PREFISSO, camere=None, versione: str = "",
         istantanee: bool = False) -> list[tuple[str, bytes, bool]]:
    """Le configurazioni da pubblicare: ``[(argomento, carico, ritenuto), …]``.

    ``camere`` sono coppie ``(id, nome)``: l'**id** fa l'identificativo univoco, che deve restare
    uguale per sempre o Home Assistant crea un sensore nuovo e lascia il vecchio orfano; il **nome**
    fa l'argomento dello stato e quello che si legge sullo schermo. Sono due cose diverse di
    proposito: rinominare una camera cambia l'etichetta e non crea un doppione.

    **Le etichette passano dal traduttore, gli identificativi no.** Chi usa Mirto in inglese vede le
    sue entità in inglese in Home Assistant, e se cambia lingua le etichette seguono al riavvio —
    ma l'identificativo e l'argomento restano quelli, altrimenti cambiare lingua creerebbe un
    secondo sensore e lascerebbe orfano il primo. Il nome proprio «Mirto» non si traduce.
    """
    fuori: list[tuple[str, bytes, bool]] = []
    comune = {**_disponibilita(prefisso), "device": _device(versione)}

    fuori.append((
        f"{PREFISSO_SCOPERTA}/binary_sensor/{NODO}/armed/config",
        _carico({
            "name": _("Protezione"),
            "unique_id": f"{NODO}_armed",
            "object_id": f"{NODO}_armed",
            "state_topic": R.argomento_protezione(prefisso),
            "payload_on": R.ACCESO.decode(),
            "payload_off": R.SPENTO.decode(),
            # Nessun «device_class»: Home Assistant non ne ha uno per «inserito», e metterne uno che
            # somiglia (una porta, un allarme) farebbe comparire l'icona di un'altra cosa.
            **comune,
        }), True))

    for cam_id, nome in (camere or []):
        chiave = _chiave(cam_id)
        argomenti = R.argomenti_camera(nome, prefisso)
        fuori.append((
            f"{PREFISSO_SCOPERTA}/binary_sensor/{NODO}/{chiave}_motion/config",
            _carico({
                "name": f"{nome} · " + _("movimento"),
                "unique_id": f"{NODO}_{chiave}_motion",
                "object_id": f"{NODO}_{chiave}_motion",
                "state_topic": argomenti["stato"],
                "payload_on": R.ACCESO.decode(),
                "payload_off": R.SPENTO.decode(),
                "device_class": "motion",
                **comune,
            }), True))
        if istantanee:
            fuori.append((
                f"{PREFISSO_SCOPERTA}/camera/{NODO}/{chiave}/config",
                _carico({
                    "name": nome,
                    "unique_id": f"{NODO}_{chiave}_camera",
                    "object_id": f"{NODO}_{chiave}_camera",
                    "topic": argomenti["istantanea"],
                    **comune,
                }), True))
    return fuori


def cancellazioni(prefisso: str = R.PREFISSO, camere=None, versione: str = "",
                  istantanee: bool = True) -> list[tuple[str, bytes, bool]]:
    """Gli stessi argomenti con il carico **vuoto**: è così che Home Assistant si dimentica di noi.

    La parte che quasi nessuno fa, e che si vede solo quando si smette di usare un programma: senza,
    in Home Assistant restano sensori che non si aggiornano più e non si cancellano dall'interfaccia,
    e chi li ha deve andarli a cercare nel broker con un client MQTT. Lasciare quel lavoro a
    qualcun altro per risparmiarsi dieci righe è esattamente il genere di cosa che si giudica male.

    ``istantanee`` qui è ``True`` di serie, ed è voluto: cancellando si cancella **tutto quello che
    potremmo aver pubblicato**, comprese le voci delle istantanee se intanto sono state spente. Al
    contrario resterebbero orfane proprio nel caso più facile — uno le accende, le spegne, e poi
    disinstalla.
    """
    return [(argomento, b"", True)
            for argomento, _carico_, _rit in voci(prefisso, camere, versione, istantanee)]


def _chiave(cam_id: str) -> str:
    """L'id di una camera come pezzo di argomento. Non è il nome: deve restare uguale per sempre."""
    pulito = "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(cam_id or ""))
    return "_".join(p for p in pulito.split("_") if p).lower() or "camera"


def _carico(dati: dict) -> bytes:
    return json.dumps(dati, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
