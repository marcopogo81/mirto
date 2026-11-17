# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Home Assistant trova Mirto da solo: la scoperta automatica.

Passo 6 del piano domotica, l'ultimo. È il verso opposto della via 1 del passo 1 — gli stessi
messaggi ``homeassistant/<componente>/<id>/config`` che Mirto *legge* per trovare le lampadine di
casa, scritti invece di letti.

**Perché queste prove sono severe sul formato.** Sbagliare un argomento o una chiave qui non dà un
errore: dà un Home Assistant in cui non compare niente, e nessuno capisce perché. Non c'è un
messaggio di rifiuto da leggere, non c'è un registro da guardare: c'è un elenco vuoto. Il formato è
stato verificato il 17/11 sulla documentazione di Home Assistant e, per la cancellazione, sul suo
**codice** — la documentazione su quella tace.

**Le due cose che questo file tiene, e che si vedono solo dopo:**

1. **la disponibilità.** Un sensore senza ``availability_topic`` resta sull'ultimo valore quando
   Mirto è spento: chi guarda il pannello vede «casa protetta» di un Mirto che non è in funzione da
   due giorni. È una bugia peggiore di un dato mancante, perché è credibile.
2. **la cancellazione.** Spegnendo il racconto, le voci vanno tolte con un carico vuoto, altrimenti
   in Home Assistant restano sensori grigi che la sua interfaccia non sa cancellare — bisogna andarli
   a togliere dal broker con un client MQTT. È la parte che quasi nessuno fa.
"""
import json
import unittest

from mirto.actions import racconto as R
from mirto.actions.scoperta import NODO, PREFISSO_SCOPERTA, cancellazioni, voci

CAMERE = [("usb-0", "Ingresso"), ("net-1", "Garage / retro")]


def per_argomento(elenco) -> dict:
    return {a: (c, rit) for a, c, rit in elenco}


class LArgomentoEQuelloCheHomeAssistantAscoltaTest(unittest.TestCase):
    """Verificato alla fonte: ``<prefisso>/<componente>/[<nodo>/]<oggetto>/config``."""

    def test_il_prefisso_e_quello_di_serie_di_home_assistant(self):
        self.assertEqual(PREFISSO_SCOPERTA, "homeassistant")

    def test_ogni_voce_finisce_con_config(self):
        """Senza il suffisso, Home Assistant non guarda nemmeno il messaggio."""
        for argomento, _c, _r in voci(camere=CAMERE, istantanee=True):
            self.assertTrue(argomento.endswith("/config"), f"{argomento} non finisce con /config")

    def test_ogni_voce_sta_sotto_il_nodo_di_mirto(self):
        """Raggruppa le nostre voci e le rende cancellabili tutte insieme."""
        for argomento, _c, _r in voci(camere=CAMERE, istantanee=True):
            pezzi = argomento.split("/")
            self.assertEqual(pezzi[0], PREFISSO_SCOPERTA)
            self.assertEqual(pezzi[2], NODO, f"{argomento} non sta sotto «{NODO}»")

    def test_gli_argomenti_non_hanno_segni_che_su_mqtt_non_sono_lettere(self):
        for argomento, _c, _r in voci(camere=[("a b", "Cam / 2")], istantanee=True):
            self.assertNotIn(" ", argomento)
            for segno in "+#":
                self.assertNotIn(segno, argomento)

    def test_sono_tutte_ritenute(self):
        """**Il punto di tutto.** Non ritenute, le vedrebbe solo un Home Assistant già acceso.

        Riavviandolo, Mirto gli spariarebbe dalla lista fino al riavvio di Mirto — e chi lo vede
        conclude che l'integrazione non funziona, perché nel suo mondo è andato tutto bene.
        """
        for argomento, _c, ritenuto in voci(camere=CAMERE, istantanee=True):
            self.assertTrue(ritenuto, f"{argomento} non è ritenuta: si perde al riavvio")


class CosaCompareInHomeAssistantTest(unittest.TestCase):

    def setUp(self):
        self.voci = per_argomento(voci(camere=CAMERE, versione="0.3.0", istantanee=True))

    def test_c_e_la_protezione(self):
        c, _r = self.voci[f"{PREFISSO_SCOPERTA}/binary_sensor/{NODO}/armed/config"]
        d = json.loads(c)
        self.assertEqual(d["state_topic"], R.argomento_protezione())
        self.assertEqual(d["payload_on"], "ON")
        self.assertEqual(d["payload_off"], "OFF")

    def test_la_protezione_non_finge_di_essere_un_altra_cosa(self):
        """Home Assistant non ha un «device_class» per «inserito».

        Metterne uno che somiglia — una porta, un allarme — farebbe comparire l'icona e il
        vocabolario di un'altra cosa, e chi guarda crederebbe di avere un sensore che non ha.
        """
        c, _r = self.voci[f"{PREFISSO_SCOPERTA}/binary_sensor/{NODO}/armed/config"]
        self.assertNotIn("device_class", json.loads(c))

    def test_c_e_un_sensore_di_movimento_per_camera(self):
        c, _r = self.voci[f"{PREFISSO_SCOPERTA}/binary_sensor/{NODO}/usb-0_motion/config"]
        d = json.loads(c)
        self.assertEqual(d["device_class"], "motion")
        self.assertEqual(d["state_topic"], R.argomenti_camera("Ingresso")["stato"])
        self.assertIn("Ingresso", d["name"])

    def test_lo_stato_punta_dove_mirto_pubblica_davvero(self):
        """La prova che lega i due moduli: se uno cambia la forma di un argomento, qui si vede.

        Sono due file diversi — chi pubblica e chi si fa trovare — e un argomento scritto a mano in
        uno dei due è il modo più facile di avere un Home Assistant che mostra un sensore eternamente
        «sconosciuto»: l'entità c'è, nessuno le parla, e non c'è nessun errore da nessuna parte.
        """
        visti = {json.loads(c)["state_topic"]
                 for a, (c, _r) in self.voci.items() if a.endswith("_motion/config")}
        self.assertEqual(visti, {R.argomenti_camera(nome)["stato"] for _id, nome in CAMERE})

    def test_le_foto_ci_sono_solo_se_le_istantanee_sono_accese(self):
        senza = per_argomento(voci(camere=CAMERE, istantanee=False))
        self.assertNotIn(f"{PREFISSO_SCOPERTA}/camera/{NODO}/usb-0/config", senza)
        self.assertIn(f"{PREFISSO_SCOPERTA}/binary_sensor/{NODO}/usb-0_motion/config", senza,
                      "spegnendo le foto è sparito anche il resto")

    def test_la_foto_punta_dove_mirto_la_pubblica(self):
        c, _r = self.voci[f"{PREFISSO_SCOPERTA}/camera/{NODO}/usb-0/config"]
        self.assertEqual(json.loads(c)["topic"], R.argomenti_camera("Ingresso")["istantanea"])

    def test_senza_camere_resta_la_protezione(self):
        """Chi non ha ancora collegato una camera deve comunque comparire in Home Assistant."""
        sole = per_argomento(voci(camere=[]))
        self.assertEqual(list(sole), [f"{PREFISSO_SCOPERTA}/binary_sensor/{NODO}/armed/config"])


class SiCapisceQuandoMirtoNonCeTest(unittest.TestCase):
    """La prima delle due cose: un sensore che resta sull'ultimo valore **mente**."""

    def setUp(self):
        self.voci = voci(camere=CAMERE, versione="0.3.0", istantanee=True)

    def test_ogni_voce_dice_dove_guardare_per_sapere_se_mirto_c_e(self):
        for argomento, carico, _r in self.voci:
            d = json.loads(carico)
            self.assertEqual(d.get("availability_topic"), R.argomento_stato(),
                             f"{argomento} non dice dove si vede se Mirto c'è")
            self.assertEqual(d.get("payload_available"), "online")
            self.assertEqual(d.get("payload_not_available"), "offline")

    def test_e_e_lo_stesso_argomento_del_testamento(self):
        """Se fossero due argomenti diversi, un Mirto morto male resterebbe «disponibile»."""
        argomento, _carico, _rit = R.testamento()
        for _a, carico, _r in self.voci:
            self.assertEqual(json.loads(carico)["availability_topic"], argomento)


class TutteLeVociSonoUnDispositivoSoloTest(unittest.TestCase):
    """Senza il blocco «device», in Home Assistant finiscono come entità senza padre.

    Chi ha quaranta cose attaccate al broker non le ritroverebbe. Con quello, c'è una scheda
    «Mirto» e dentro le sue.
    """

    def test_tutte_dichiarano_lo_stesso_dispositivo(self):
        visti = [json.loads(c)["device"] for _a, c, _r in voci(camere=CAMERE, istantanee=True)]
        self.assertTrue(visti)
        for d in visti:
            self.assertEqual(d, visti[0], "le voci dichiarano dispositivi diversi")
            self.assertEqual(d["identifiers"], [NODO])

    def test_la_versione_di_mirto_e_quella_vera(self):
        from mirto import __version__

        _a, c, _r = voci(camere=[], versione=__version__)[0]
        self.assertEqual(json.loads(c)["device"]["sw_version"], __version__)


class OgniVoceHaUnNomeSuoCheNonCambiaTest(unittest.TestCase):
    """``unique_id`` è come Home Assistant riconosce un'entità fra un riavvio e l'altro."""

    def test_sono_tutti_diversi(self):
        ids = [json.loads(c)["unique_id"] for _a, c, _r in voci(camere=CAMERE, istantanee=True)]
        self.assertEqual(len(ids), len(set(ids)), f"due voci hanno lo stesso identificativo: {ids}")

    def test_vengono_dall_id_della_camera_e_non_dal_nome(self):
        """**La distinzione che conta.** Rinominare una camera non deve creare un doppione.

        Se l'identificativo venisse dal nome, rinominare «Ingresso» in «Portone» darebbe a Home
        Assistant un sensore nuovo e lascerebbe il vecchio orfano per sempre — e chi rinomina una
        camera non si aspetta di dover fare manutenzione in un altro programma.
        """
        prima = per_argomento(voci(camere=[("usb-0", "Ingresso")]))
        dopo = per_argomento(voci(camere=[("usb-0", "Portone")]))
        chiave = f"{PREFISSO_SCOPERTA}/binary_sensor/{NODO}/usb-0_motion/config"
        self.assertEqual(json.loads(prima[chiave][0])["unique_id"],
                         json.loads(dopo[chiave][0])["unique_id"],
                         "rinominare una camera cambia l'identificativo")
        self.assertNotEqual(json.loads(prima[chiave][0])["name"],
                            json.loads(dopo[chiave][0])["name"],
                            "ma l'etichetta deve seguire il nome nuovo")

    def test_l_argomento_non_cambia_rinominando(self):
        prima = {a for a, _c, _r in voci(camere=[("usb-0", "Ingresso")])}
        dopo = {a for a, _c, _r in voci(camere=[("usb-0", "Portone")])}
        self.assertEqual(prima, dopo, "rinominando, Home Assistant vedrebbe un'entità nuova")


class CancellarsiEGentileTest(unittest.TestCase):
    """La seconda delle due cose, e quella che quasi nessuno fa."""

    def test_sono_gli_stessi_argomenti_col_carico_vuoto(self):
        """Verificato sul codice di Home Assistant: un carico vuoto rimuove l'entità."""
        pubblicate = {a for a, _c, _r in voci(camere=CAMERE, istantanee=True)}
        togliere = cancellazioni(camere=CAMERE)
        self.assertEqual({a for a, _c, _r in togliere}, pubblicate)
        for argomento, carico, ritenuto in togliere:
            self.assertEqual(carico, b"", f"{argomento}: il carico non è vuoto, non cancella")
            self.assertTrue(ritenuto, "una cancellazione non ritenuta non toglie quella ritenuta")

    def test_si_cancella_anche_quello_che_potrebbe_essere_rimasto_dalle_istantanee(self):
        """Il caso facile fatto bene: uno le accende, le spegne, poi disinstalla.

        Se la cancellazione guardasse l'impostazione di adesso, le voci delle foto resterebbero
        orfane proprio in quel caso — che è il più comune di tutti, perché la curiosità si prova e
        poi si spegne.
        """
        togliere = {a for a, _c, _r in cancellazioni(camere=CAMERE)}
        self.assertIn(f"{PREFISSO_SCOPERTA}/camera/{NODO}/usb-0/config", togliere,
                      "le voci delle istantanee non si cancellano se intanto sono state spente")


class CambiareLinguaNonCreaUnDoppioneTest(unittest.TestCase):
    """Le etichette si traducono, gli identificativi no — e sono nello stesso dizionario.

    Chi usa Mirto in inglese deve vedere «Armed» in Home Assistant, non «Protezione»: un programma
    tradotto a metà si nota subito, e si nota proprio nel posto che si guarda ogni giorno. Ma se
    l'identificativo o l'argomento prendessero anche loro la lingua, cambiarla creerebbe un secondo
    sensore e lascerebbe orfano il primo — e nessuno collega le due cose, perché a cambiare lingua
    non si immagina di toccare l'antifurto.
    """

    def setUp(self):
        from mirto.i18n import set_language

        self.set_language = set_language
        self.addCleanup(set_language, "it")

    def _voci(self, lingua):
        self.set_language(lingua)
        return per_argomento(voci(camere=CAMERE, istantanee=True))

    def test_le_etichette_seguono_la_lingua(self):
        it, en = self._voci("it"), self._voci("en")
        chiave = f"{PREFISSO_SCOPERTA}/binary_sensor/{NODO}/armed/config"
        self.assertEqual(json.loads(it[chiave][0])["name"], "Protezione")
        self.assertEqual(json.loads(en[chiave][0])["name"], "Armed")

    def test_anche_quelle_delle_camere(self):
        """Il nome della camera è dell'utente e resta suo; la parola che gli si attacca è nostra."""
        cam = f"{PREFISSO_SCOPERTA}/binary_sensor/{NODO}/usb-0_motion/config"
        self.assertEqual(json.loads(self._voci("it")[cam][0])["name"], "Ingresso · movimento")
        self.assertEqual(json.loads(self._voci("en")[cam][0])["name"], "Ingresso · motion")

    def test_gli_argomenti_non_la_seguono(self):
        self.assertEqual(set(self._voci("it")), set(self._voci("en")),
                         "cambiando lingua Home Assistant vedrebbe entità nuove")

    def test_gli_identificativi_non_la_seguono(self):
        def ids(lingua):
            return sorted(json.loads(c)["unique_id"] for c, _r in self._voci(lingua).values())

        self.assertEqual(ids("it"), ids("en"),
                         "cambiando lingua i vecchi sensori resterebbero orfani")

    def test_il_nome_del_dispositivo_resta_mirto(self):
        """È un nome proprio: tradurlo sarebbe come tradurre il cognome di qualcuno."""
        for lingua in ("it", "en"):
            _a, c, _r = voci(camere=[])[0] if self.set_language(lingua) else (None, None, None)
            self.assertEqual(json.loads(c)["device"]["name"], "Mirto")


if __name__ == "__main__":
    unittest.main()
