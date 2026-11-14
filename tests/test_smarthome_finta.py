# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""La casa smart finta: le tre marche si comportano davvero in modo diverso.

La prova che conta più di tutte è ``IlBravoSiSpiegaDaSe``: uno **straniero** che legge soltanto
l'annuncio — senza sapere niente di ESPHome, senza nessun argomento scritto nel suo codice — deve
riuscire a comandare la lampadina e a vederne lo stato. Se passa quella, la scheda SmartHome di Mirto
si può scrivere leggendo gli annunci invece di indovinare le marche, e il giorno che ESPHome cambia
qualcosa non si rompe niente.

Le altre due marche servono a provare il contrario: il silenzioso, che si trova solo dal suo elenco, e
il maleducato, che non si trova affatto e si prende a orecchio.
"""
import json
import threading
import time
import unittest

from mirto.actions import mqtt
from mirto.actions.smarthome_finta import (
    ESPHOME,
    LUCE,
    MALEDUCATO,
    PRESA,
    PREFISSO_ZIGBEE,
    SENSORE,
    ZIGBEE,
    CasaFinta,
    DispositivoFinto,
    casa_di_prova,
)


class Straniero:
    """Un client che non sa niente della casa: tiene l'ultimo messaggio per argomento e sa aspettare.

    Sta qui e non in ``mqtt.py`` di proposito: è il punto di vista di chi arriva da fuori, cioè quello
    che avrà Mirto al passo 1. Se per provare la casa finta gli servisse qualcosa di più, vorrebbe
    dire che la casa finta non si spiega da sé.
    """

    def __init__(self, porta: int) -> None:
        self.visto: dict[str, bytes] = {}
        self.quanti: dict[str, int] = {}
        self._novita = threading.Condition()
        self.cliente = mqtt.Cliente("127.0.0.1", porta, nome="straniero",
                                    su_messaggio=self._arrivato).collega()

    def _arrivato(self, argomento, carico, _ritenuto):
        with self._novita:
            self.visto[argomento] = carico
            self.quanti[argomento] = self.quanti.get(argomento, 0) + 1
            self._novita.notify_all()

    def ascolta(self, *filtri: str) -> "Straniero":
        self.cliente.iscrivi(*filtri)
        time.sleep(0.15)        # il tempo che il broker riconsegni i ritenuti
        return self

    def aspetta(self, argomento: str, secondi: float = 2.0, almeno: int = 1) -> bytes:
        with self._novita:
            fine = time.monotonic() + secondi
            while self.quanti.get(argomento, 0) < almeno:
                resto = fine - time.monotonic()
                if resto <= 0:
                    raise AssertionError(
                        f"niente su «{argomento}» (visti: {sorted(self.visto)})")
                self._novita.wait(resto)
            return self.visto[argomento]

    def aspetta_valore(self, argomento: str, atteso, secondi: float = 2.0):
        """Aspetta che su quell'argomento ci sia **quel valore**, non che siano arrivati N messaggi.

        Contare i messaggi è fragile, e si è visto: un ritenuto consegnato un istante prima o dopo
        cambia il conto, e la prova fallisce una volta su quindici per il motivo sbagliato. Il valore
        invece è il fatto che si vuole provare. ``atteso`` può essere byte o una struttura, e allora
        il confronto si fa sul JSON.
        """
        with self._novita:
            fine = time.monotonic() + secondi
            while True:
                visto = self.visto.get(argomento)
                if visto is not None:
                    uguale = (visto == atteso if isinstance(atteso, bytes)
                              else self._json(visto) == atteso)
                    if uguale:
                        return visto
                resto = fine - time.monotonic()
                if resto <= 0:
                    raise AssertionError(
                        f"su «{argomento}» atteso {atteso!r}, c'è {self.visto.get(argomento)!r}")
                self._novita.wait(resto)

    @staticmethod
    def _json(carico: bytes):
        try:
            return json.loads(carico)
        except ValueError:
            return None

    def niente_su(self, filtro: str, secondi: float = 0.5) -> None:
        time.sleep(secondi)
        trovati = [a for a in self.visto if mqtt.combacia(filtro, a)]
        assert not trovati, f"non doveva arrivare niente su «{filtro}», invece: {trovati}"

    def chiudi(self) -> None:
        self.cliente.chiudi()


class ConUnaCasa(unittest.TestCase):
    """Base: una casa con dentro i dispositivi che le prove le chiedono."""

    dispositivi: list[DispositivoFinto] = []

    def setUp(self):
        self.cambiati: list[tuple[str, str]] = []
        self.casa = CasaFinta([DispositivoFinto(d.identificativo, d.nome, d.tipo, d.marca, d.acceso)
                               for d in self.dispositivi],
                              su_cambio=lambda d, da: self.cambiati.append((d.identificativo, da)))
        self.casa.avvia()
        self.addCleanup(self.casa.ferma)
        self.porta = self.casa.porta

    def straniero(self, *filtri: str) -> Straniero:
        s = Straniero(self.porta)
        self.addCleanup(s.chiudi)
        return s.ascolta(*filtri) if filtri else s


class IlBravoSiSpiegaDaSe(ConUnaCasa):
    """ESPHome: l'annuncio basta. Qui dentro non c'è scritto nessun argomento a mano, di proposito."""

    dispositivi = [DispositivoFinto("luce_scale", "Luce delle scale", LUCE, ESPHOME)]

    def _annuncio(self) -> dict:
        s = self.straniero("homeassistant/#")
        argomenti = [a for a in s.visto if a.endswith("/config")]
        self.assertEqual(len(argomenti), 1, f"atteso un annuncio solo, trovati {argomenti}")
        return json.loads(s.visto[argomenti[0]])

    def test_l_annuncio_e_ritenuto_e_arriva_a_chi_si_collega_dopo(self):
        # il dispositivo era già in casa prima che lo straniero esistesse: lo trova comunque
        dentro = self._annuncio()
        self.assertEqual(dentro["name"], "Luce delle scale")
        self.assertIn("state_topic", dentro)
        self.assertIn("command_topic", dentro)

    def test_lo_straniero_la_comanda_leggendo_solo_l_annuncio(self):
        dentro = self._annuncio()
        s = self.straniero(dentro["state_topic"])
        self.assertEqual(s.aspetta(dentro["state_topic"]), b"OFF")
        s.cliente.pubblica(dentro["command_topic"], dentro.get("payload_on", "ON"))
        s.aspetta_valore(dentro["state_topic"], b"ON")
        self.assertTrue(self.casa.dispositivi["luce_scale"].acceso,
                        "il comando deve cambiare il dispositivo per davvero, non solo l'argomento")

    def test_lo_straniero_la_spegne_con_la_parola_che_l_annuncio_gli_ha_detto(self):
        dentro = self._annuncio()
        self.casa.cambia("luce_scale", True)
        s = self.straniero(dentro["state_topic"])
        s.aspetta_valore(dentro["state_topic"], b"ON")
        s.cliente.pubblica(dentro["command_topic"], dentro.get("payload_off", "OFF"))
        s.aspetta_valore(dentro["state_topic"], b"OFF")

    def test_l_annuncio_dice_anche_dove_si_vede_se_e_vivo(self):
        dentro = self._annuncio()
        s = self.straniero(dentro["availability_topic"])
        self.assertEqual(s.aspetta(dentro["availability_topic"]), b"online")

    def test_un_sensore_si_annuncia_senza_argomento_dei_comandi(self):
        # un sensore non si comanda: se l'annuncio dicesse il contrario, Mirto gli offrirebbe un
        # interruttore che non fa niente
        self.casa.aggiungi(DispositivoFinto("movimento", "Movimento", SENSORE, ESPHOME))
        s = self.straniero("homeassistant/#")
        dentro = json.loads(s.aspetta("homeassistant/binary_sensor/movimento/config"))
        self.assertNotIn("command_topic", dentro)

    def test_togliendolo_l_annuncio_si_cancella_con_un_carico_vuoto(self):
        argomento = "homeassistant/light/luce_scale/config"
        self.assertTrue(self.casa.togli("luce_scale"))
        s = self.straniero("homeassistant/#")
        # il ritenuto è stato cancellato: a chi arriva dopo non arriva più niente
        s.niente_su(argomento)


class IlSilenziosoSiTrovaDalSuoElenco(ConUnaCasa):
    """Zigbee2MQTT: l'annuncio generico non c'è, ma l'elenco dei dispositivi sì, ed è ritenuto."""

    dispositivi = [DispositivoFinto("luce_cucina", "Luce della cucina", LUCE, ZIGBEE),
                   DispositivoFinto("movimento_corridoio", "Movimento in corridoio", SENSORE, ZIGBEE)]

    def test_non_si_annuncia_e_chi_cerca_solo_quello_non_lo_vede(self):
        # è la crepa che la via 2 del piano serve a chiudere: qui si vede in chiaro
        self.straniero("homeassistant/#").niente_su("homeassistant/#")

    def test_ma_sta_nell_elenco_ritenuto_del_ponte(self):
        s = self.straniero(f"{PREFISSO_ZIGBEE}/bridge/devices")
        elenco = json.loads(s.aspetta(f"{PREFISSO_ZIGBEE}/bridge/devices"))
        nomi = {v["friendly_name"] for v in elenco}
        self.assertEqual(nomi, {"Luce della cucina", "Movimento in corridoio"})

    def test_il_ponte_dice_di_essere_in_piedi(self):
        s = self.straniero(f"{PREFISSO_ZIGBEE}/bridge/state")
        self.assertEqual(json.loads(s.aspetta(f"{PREFISSO_ZIGBEE}/bridge/state")),
                         {"state": "online"})

    def test_lo_stato_sta_dove_la_sua_lingua_dice(self):
        s = self.straniero(f"{PREFISSO_ZIGBEE}/Luce della cucina")
        stato = json.loads(s.aspetta(f"{PREFISSO_ZIGBEE}/Luce della cucina"))
        self.assertEqual(stato, {"state": "OFF"})

    def test_si_comanda_col_json_sul_set(self):
        argomento = f"{PREFISSO_ZIGBEE}/Luce della cucina"
        s = self.straniero(argomento)
        s.cliente.pubblica(f"{argomento}/set", json.dumps({"state": "ON"}))
        s.aspetta_valore(argomento, {"state": "ON"})

    def test_si_comanda_anche_con_la_scorciatoia_set_state(self):
        # la forma che si tende a non provare, e che Zigbee2MQTT accetta: «/set/state» con «ON» secco
        argomento = f"{PREFISSO_ZIGBEE}/Luce della cucina"
        s = self.straniero(argomento)
        s.cliente.pubblica(f"{argomento}/set/state", "ON")
        s.aspetta_valore(argomento, {"state": "ON"})

    def test_un_comando_senza_senso_non_cambia_niente(self):
        argomento = f"{PREFISSO_ZIGBEE}/Luce della cucina"
        s = self.straniero(argomento)
        s.aspetta_valore(argomento, {"state": "OFF"})
        s.cliente.pubblica(f"{argomento}/set", json.dumps({"state": "forse"}))
        time.sleep(0.3)
        self.assertEqual(json.loads(s.visto[argomento]), {"state": "OFF"},
                         "un comando che non si capisce non deve muovere niente")
        self.assertFalse(self.casa.dispositivi["luce_cucina"].acceso)

    def test_l_elenco_e_una_fotografia_non_una_cronaca(self):
        self.assertTrue(self.casa.togli("luce_cucina"))
        s = self.straniero(f"{PREFISSO_ZIGBEE}/bridge/devices")
        elenco = json.loads(s.aspetta(f"{PREFISSO_ZIGBEE}/bridge/devices"))
        self.assertEqual([v["friendly_name"] for v in elenco], ["Movimento in corridoio"])

    def test_un_sensore_non_si_comanda(self):
        sensore = self.casa.dispositivi["movimento_corridoio"]
        self.assertFalse(sensore.comandabile)
        self.assertIsNone(sensore.argomento_comando())
        self.assertEqual(sensore.argomenti_da_ascoltare(), [])


class IlMaleducatoSiPrendeAOrecchio(ConUnaCasa):
    """Il caso che fa esistere la terza via: non si annuncia e non parla nessuna lingua nota."""

    dispositivi = [DispositivoFinto("luce_garage", "Luce del garage", LUCE, MALEDUCATO)]

    def test_non_si_annuncia_e_non_sta_in_nessun_elenco(self):
        s = self.straniero("homeassistant/#", f"{PREFISSO_ZIGBEE}/bridge/devices")
        s.niente_su("homeassistant/#")
        elenco = json.loads(s.visto.get(f"{PREFISSO_ZIGBEE}/bridge/devices", b"[]"))
        self.assertEqual(elenco, [], "un maleducato non deve comparire fra gli Zigbee")

    def test_ma_parla_e_l_ascolto_grezzo_lo_sente(self):
        # la via 3: Mirto ascolta tutto e mostra chi sta parlando. È brutto, ma non è un vicolo cieco
        s = self.straniero("#")
        chi_parla = [a for a in s.visto if "garage" in a]
        self.assertEqual(chi_parla, ["tuya/luce_garage/dps"])
        self.assertEqual(json.loads(s.visto[chi_parla[0]]), {"1": False})

    def test_una_volta_capito_si_comanda(self):
        s = self.straniero("tuya/luce_garage/dps")
        s.cliente.pubblica("tuya/luce_garage/dps/set", json.dumps({"1": True}))
        s.aspetta_valore("tuya/luce_garage/dps", {"1": True})


class IDueVersiTest(ConUnaCasa):
    """I due versi insieme: la mano sull'interruttore, e il comando che arriva da Mirto."""

    dispositivi = [DispositivoFinto("luce_scale", "Luce delle scale", LUCE, ESPHOME),
                   DispositivoFinto("presa_tv", "Presa della TV", PRESA, ESPHOME)]

    def test_la_mano_sull_interruttore_si_vede_da_fuori(self):
        # il verso che fa partire i trigger: qualcuno accende la luce in casa, Mirto se ne accorge
        s = self.straniero("luce_scale/#")
        self.casa.cambia("luce_scale", True)
        s.aspetta_valore("luce_scale/light/luce_scale/state", b"ON")
        self.assertEqual(self.cambiati[-1], ("luce_scale", "finestra"))

    def test_un_comando_da_fuori_si_vede_da_dentro(self):
        # il verso che serve alle uscite delle azioni: Mirto comanda, la finestra lo deve mostrare
        s = self.straniero()
        s.cliente.pubblica("luce_scale/light/luce_scale/command", "ON")
        fine = time.monotonic() + 2.0
        while time.monotonic() < fine and not self.cambiati:
            time.sleep(0.01)
        self.assertEqual(self.cambiati[-1], ("luce_scale", "comando"),
                         "la finestra deve sapere che è stato un comando, non una mano")

    def test_un_comando_a_un_dispositivo_non_sposta_gli_altri(self):
        s = self.straniero("presa_tv/#")
        s.cliente.pubblica("luce_scale/light/luce_scale/command", "ON")
        time.sleep(0.3)
        self.assertFalse(self.casa.dispositivi["presa_tv"].acceso)

    def test_lo_stato_e_ritenuto_cosi_chi_arriva_dopo_lo_sa(self):
        self.casa.cambia("presa_tv", True)
        time.sleep(0.1)
        tardi = self.straniero("presa_tv/#")
        self.assertEqual(tardi.aspetta("presa_tv/switch/presa_tv/state"), b"ON")


class LaCasaDiProvaTest(unittest.TestCase):
    """La casa già arredata: chi apre il simulatore deve trovare tutti e tre i casi, non una vuota."""

    def test_ci_sono_tutte_e_tre_le_marche_e_almeno_un_sensore(self):
        dispositivi = casa_di_prova()
        self.assertEqual({d.marca for d in dispositivi}, {ESPHOME, ZIGBEE, MALEDUCATO},
                         "il caso che non si prova è il caso che si rompe")
        self.assertTrue(any(d.tipo == SENSORE for d in dispositivi),
                        "senza un sensore non si prova il verso dei trigger")
        self.assertTrue(any(d.comandabile for d in dispositivi))

    def test_gli_identificativi_non_si_ripetono(self):
        dispositivi = casa_di_prova()
        self.assertEqual(len({d.identificativo for d in dispositivi}), len(dispositivi))

    def test_una_marca_inventata_si_rifiuta_subito(self):
        with self.assertRaises(ValueError):
            DispositivoFinto("x", "X", LUCE, "philips-a-caso")
        with self.assertRaises(ValueError):
            DispositivoFinto("x", "X", "tostapane", ESPHOME)


class LeParoleDeiComandiTest(unittest.TestCase):
    """Le forme in cui un comando può arrivare. Si sbaglia qui, e il dispositivo non risponde mai."""

    def setUp(self):
        self.d = DispositivoFinto("x", "X", LUCE, ESPHOME)

    def test_le_parole_che_vogliono_dire_acceso(self):
        for parola in (b"ON", b"on", b" On ", b"TRUE", b"1", b'{"state": "ON"}', b'{"state": true}'):
            self.assertIs(self.d.leggi_comando("x", parola), True, parola)

    def test_le_parole_che_vogliono_dire_spento(self):
        for parola in (b"OFF", b"off", b"FALSE", b"0", b'{"state": "OFF"}', b'{"state": false}'):
            self.assertIs(self.d.leggi_comando("x", parola), False, parola)

    def test_quello_che_non_vuol_dire_niente(self):
        for parola in (b"", b"   ", b"forse", b"{", b'{"state": "forse"}', b'{"altro": "ON"}', b"[]"):
            self.assertIsNone(self.d.leggi_comando("x", parola), parola)


if __name__ == "__main__":
    unittest.main()
