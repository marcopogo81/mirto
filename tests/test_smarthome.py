# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Quello che Mirto sa della domotica, provato contro la casa finta.

È il momento in cui il passo 0 si ripaga: niente di qui sotto si potrebbe provare senza un impianto,
e un impianto non c'è. La casa finta sa fare le tre marche che contano, quindi si possono provare
tutte e tre le vie — compresa quella che serve ai dispositivi maleducati, che è l'unica che non si
sarebbe mai provata comprando una lampadina.

La prova che regge tutte le altre è ``IlBravoSiTrovaDaSolo``: Mirto non ha nessun argomento scritto
dentro, legge l'annuncio e da lì sa dove guardare e dove scrivere.
"""
import json
import threading
import time
import unittest

from mirto.actions import mqtt
from mirto.actions.smarthome import (
    ESPHOME,
    JSON_STATO,
    SCONOSCIUTA,
    ZIGBEE,
    CasaSmart,
    Dispositivo,
)
from mirto.actions.smarthome_finta import (
    ESPHOME as F_ESPHOME,
    LUCE,
    MALEDUCATO,
    PRESA,
    SENSORE,
    ZIGBEE as F_ZIGBEE,
    CasaFinta,
    DispositivoFinto,
)


class ConLaCasaFinta(unittest.TestCase):
    """Una casa finta accesa, e Mirto collegato al suo broker."""

    dispositivi: list[tuple] = []

    def setUp(self):
        self.finta = CasaFinta([DispositivoFinto(*d) for d in self.dispositivi]).avvia()
        self.addCleanup(self.finta.ferma)
        self.cambiati: list[str] = []
        self.novita = threading.Event()
        self.casa = CasaSmart("127.0.0.1", self.finta.porta, su_cambio=self._segnato).collega()
        self.addCleanup(self.casa.chiudi)

    def _segnato(self, d):
        self.cambiati.append(d.identificativo)
        self.novita.set()

    def aspetta(self, quanti: int = 1, secondi: float = 3.0) -> list:
        fine = time.monotonic() + secondi
        while time.monotonic() < fine and len(self.casa.dispositivi()) < quanti:
            time.sleep(0.02)
        trovati = self.casa.dispositivi()
        self.assertGreaterEqual(len(trovati), quanti,
                                f"atteso {quanti}, trovati {[d.nome for d in trovati]}")
        return trovati

    def uno(self, pezzo: str, secondi: float = 3.0) -> Dispositivo:
        fine = time.monotonic() + secondi
        while time.monotonic() < fine:
            for d in self.casa.dispositivi():
                if pezzo.lower() in d.nome.lower():
                    return d
            time.sleep(0.02)
        raise AssertionError(f"«{pezzo}» non trovato fra {[d.nome for d in self.casa.dispositivi()]}")

    def aspetta_stato(self, pezzo: str, atteso, secondi: float = 3.0) -> Dispositivo:
        fine = time.monotonic() + secondi
        while time.monotonic() < fine:
            d = self.uno(pezzo)
            if d.acceso is atteso:
                return d
            time.sleep(0.02)
        d = self.uno(pezzo)
        raise AssertionError(f"«{d.nome}» è {d.acceso!r}, atteso {atteso!r}")


class IlBravoSiTrovaDaSolo(ConLaCasaFinta):
    """Via 1, l'annuncio generico. Qui dentro non c'è nessun argomento scritto a mano."""

    dispositivi = [("luce_scale", "Luce delle scale", LUCE, F_ESPHOME),
                   ("presa_tv", "Presa della TV", PRESA, F_ESPHOME),
                   ("movimento", "Movimento in corridoio", SENSORE, F_ESPHOME)]

    def test_compaiono_tutti_senza_che_nessuno_li_dichiari(self):
        trovati = self.aspetta(3)
        self.assertEqual(sorted(d.nome for d in trovati),
                         ["Luce delle scale", "Movimento in corridoio", "Presa della TV"])

    def test_ne_sa_marca_e_tipo(self):
        luce = self.uno("scale")
        self.assertEqual(luce.marca, ESPHOME)
        self.assertEqual(luce.tipo, "luce")
        self.assertEqual(self.uno("Presa").tipo, "presa")
        self.assertEqual(self.uno("Movimento").tipo, "sensore")

    def test_un_sensore_non_si_comanda(self):
        # l'annuncio di un sensore non porta l'argomento dei comandi: offrirgli un interruttore
        # vorrebbe dire mettere in Mirto un pulsante che non fa niente
        self.assertFalse(self.uno("Movimento").comandabile)
        self.assertTrue(self.uno("scale").comandabile)

    def test_ne_sa_lo_stato_di_adesso(self):
        self.aspetta_stato("scale", False)
        self.finta.cambia("luce_scale", True)
        self.aspetta_stato("scale", True)

    def test_lo_comanda(self):
        luce = self.uno("scale")
        self.assertTrue(self.casa.comanda(luce.identificativo, True))
        self.aspetta_stato("scale", True)
        self.assertTrue(self.finta.dispositivi["luce_scale"].acceso,
                        "il comando deve arrivare al dispositivo, non solo all'argomento")
        self.assertTrue(self.casa.comanda(luce.identificativo, False))
        self.aspetta_stato("scale", False)

    def test_comandare_un_sensore_si_rifiuta(self):
        self.assertFalse(self.casa.comanda(self.uno("Movimento").identificativo, True))

    def test_se_sparisce_lo_toglie(self):
        # l'annuncio si cancella con un ritenuto vuoto: se non lo seguissimo, nell'elenco di Mirto
        # resterebbe per sempre un dispositivo staccato dal muro
        self.aspetta(3)
        self.finta.togli("presa_tv")
        fine = time.monotonic() + 3.0
        while time.monotonic() < fine and any("Presa" in d.nome for d in self.casa.dispositivi()):
            time.sleep(0.02)
        self.assertNotIn("Presa della TV", [d.nome for d in self.casa.dispositivi()])

    def test_riannunciandosi_non_perde_quello_che_sapevamo(self):
        """Un dispositivo che si riaccende ripubblica il suo annuncio ritenuto.

        Se a ogni riannuncio ripartissimo da «non lo so», un ESPHome che si riavvia di notte
        azzererebbe l'elenco di Mirto, e un'azione legata al suo stato resterebbe cieca finché il
        dispositivo non parla di nuovo — cioè finché qualcuno non tocca l'interruttore.
        """
        self.finta.cambia("luce_scale", True)
        self.aspetta_stato("scale", True)
        d = self.uno("scale")
        # Si ripubblica **solo** l'annuncio, non lo stato: se riaggiungessimo il dispositivo dalla
        # casa finta, subito dopo arriverebbe anche il suo stato e sarebbe quello a salvare la
        # prova, non la memoria di Mirto.
        visto = {}
        spia = mqtt.Cliente("127.0.0.1", self.finta.porta, nome="spia",
                            su_messaggio=lambda a, c, _r: visto.__setitem__(a, c)).collega()
        self.addCleanup(spia.chiudi)
        spia.iscrivi("homeassistant/#")
        fine = time.monotonic() + 2.0
        while time.monotonic() < fine and not visto:
            time.sleep(0.02)
        argomento = next(a for a in visto if a.endswith("/config"))
        spia.pubblica(argomento, visto[argomento], ritenuto=True)
        time.sleep(0.5)
        ancora = self.casa.dispositivo(d.identificativo)
        self.assertIsNotNone(ancora)
        self.assertIs(ancora.acceso, True, "lo stato che sapevamo non si butta via")

    def test_sa_se_e_vivo(self):
        luce = self.uno("scale")
        self.assertTrue(luce.vivo)
        self.assertTrue(luce.argomento_vita, "l'annuncio dice anche dove si vede se è vivo")


class IlSilenziosoSiTrovaDalSuoElenco(ConLaCasaFinta):
    """Via 2, la lingua della marca. Senza questa, metà dei dispositivi Zigbee non si vedrebbe."""

    dispositivi = [("luce_cucina", "Luce della cucina", LUCE, F_ZIGBEE),
                   ("movimento_corridoio", "Movimento in corridoio", SENSORE, F_ZIGBEE)]

    def test_compaiono_anche_se_non_si_annunciano(self):
        trovati = self.aspetta(2)
        self.assertEqual(sorted(d.nome for d in trovati),
                         ["Luce della cucina", "Movimento in corridoio"])
        for d in trovati:
            self.assertEqual(d.marca, ZIGBEE)

    def test_parla_la_loro_lingua(self):
        luce = self.uno("cucina")
        self.assertEqual(luce.forma, JSON_STATO)
        self.assertEqual(json.loads(luce.carico_per(True)), {"state": "ON"})

    def test_distingue_chi_si_comanda_da_chi_si_guarda(self):
        # Zigbee2MQTT lo dice nel campo «access» dell'elenco: leggerlo è la differenza fra offrire
        # un interruttore vero e offrirne uno finto accanto a un sensore
        self.assertTrue(self.uno("cucina").comandabile)
        self.assertFalse(self.uno("Movimento").comandabile)

    def test_lo_comanda(self):
        luce = self.uno("cucina")
        self.assertTrue(self.casa.comanda(luce.identificativo, True))
        self.aspetta_stato("cucina", True)
        self.assertTrue(self.finta.dispositivi["luce_cucina"].acceso)

    def test_segue_lo_stato_che_cambia_in_casa(self):
        self.aspetta_stato("Movimento", False)
        self.finta.cambia("movimento_corridoio", True)
        self.aspetta_stato("Movimento", True)

    def test_se_sparisce_dall_elenco_sparisce_da_mirto(self):
        self.aspetta(2)
        self.finta.togli("luce_cucina")
        fine = time.monotonic() + 3.0
        while time.monotonic() < fine and any("cucina" in d.nome for d in self.casa.dispositivi()):
            time.sleep(0.02)
        self.assertEqual([d.nome for d in self.casa.dispositivi()], ["Movimento in corridoio"])


class IlMaleducatoSiPrendeAOrecchio(ConLaCasaFinta):
    """Via 3. Brutta, e indispensabile: senza, per qualcuno Mirto è un vicolo cieco."""

    dispositivi = [("luce_garage", "Luce del garage", LUCE, MALEDUCATO)]

    def test_di_suo_non_si_vede(self):
        time.sleep(0.5)
        self.assertEqual(self.casa.dispositivi(), [],
                         "non si annuncia e non sta in nessun elenco: giusto che non si veda")

    def test_con_l_ascolto_grezzo_si_sente(self):
        self.casa.ascolta_tutto(True)
        self.finta.cambia("luce_garage", True)      # basta che parli una volta
        fine = time.monotonic() + 3.0
        while time.monotonic() < fine and not any("garage" in a for a, _ in self.casa.chi_parla()):
            time.sleep(0.02)
        argomenti = [a for a, _ in self.casa.chi_parla()]
        self.assertIn("tuya/luce_garage/dps", argomenti)

    def test_l_ascolto_grezzo_non_propone_roba_di_servizio(self):
        # «zigbee2mqtt/bridge/...» e gli argomenti del broker non sono dispositivi: proporli vorrebbe
        # dire far scegliere fra venti righe di cui diciannove sbagliate
        self.casa.ascolta_tutto(True)
        time.sleep(0.6)
        for argomento, _ in self.casa.chi_parla():
            self.assertFalse(argomento.startswith(("homeassistant/", "zigbee2mqtt/bridge/", "$")),
                             f"«{argomento}» non è un dispositivo")

    def test_adottato_si_comanda(self):
        self.casa.ascolta_tutto(True)
        self.finta.cambia("luce_garage", True)
        # Si aspetta il **valore**, non che il messaggio sia arrivato: iscrivendosi a «#» il primo
        # che arriva è il ritenuto di prima, «spento», e aspettare solo la comparsa dell'argomento
        # adottava il dispositivo con lo stato vecchio una volta su tante. È la regola del capitolo
        # 28 del metodo, scritta oggi e sbagliata qui sotto il giorno stesso.
        fine = time.monotonic() + 3.0
        while time.monotonic() < fine and not any(
                a == "tuya/luce_garage/dps" and b"true" in c
                for a, c in self.casa.chi_parla()):
            time.sleep(0.02)
        d = self.casa.adotta("tuya/luce_garage/dps", "Luce del garage")
        self.assertTrue(d.adottato, "va detto che è un'ipotesi, non un annuncio")
        self.assertIs(d.acceso, True, "quello che aveva già detto non si butta via")
        self.assertTrue(self.casa.comanda(d.identificativo, False))
        self.aspetta_stato("garage", False)
        self.assertFalse(self.finta.dispositivi["luce_garage"].acceso)

    def test_spegnere_l_ascolto_grezzo_smette_davvero_di_ricevere(self):
        """Non basta smettere di annotare: bisogna disiscriversi.

        Restando iscritti a «#» Mirto continuerebbe a ricevere ogni messaggio di ogni dispositivo
        per sempre — comprese le istantanee, se qualcuno ne pubblica. Quindi la prova non guarda
        l'elenco di chi parla, che resterebbe fermo comunque: conta quanti messaggi **arrivano**.
        """
        self.casa.ascolta_tutto(True)
        time.sleep(0.3)
        arrivati = []
        vero = self.casa._arrivato      # noqa: SLF001
        self.casa._cliente.su_messaggio = lambda a, c, r: (arrivati.append(a), vero(a, c, r))[1]  # noqa: SLF001
        self.finta.cambia("luce_garage", True)
        fine = time.monotonic() + 2.0
        while time.monotonic() < fine and not arrivati:
            time.sleep(0.02)
        self.assertTrue(arrivati, "con l'ascolto acceso deve arrivare qualcosa")

        self.casa.ascolta_tutto(False)
        time.sleep(0.3)
        arrivati.clear()
        self.finta.cambia("luce_garage", False)
        time.sleep(0.6)
        self.assertEqual(arrivati, [], "con l'ascolto spento non deve arrivare più niente")


class TutteETreInsieme(ConLaCasaFinta):
    """La casa già arredata del simulatore, che è il caso vero: marche diverse nello stesso impianto."""

    dispositivi = [("luce_scale", "Luce delle scale", LUCE, F_ESPHOME),
                   ("presa_tv", "Presa della TV", PRESA, F_ESPHOME),
                   ("luce_cucina", "Luce della cucina", LUCE, F_ZIGBEE),
                   ("movimento_corridoio", "Movimento in corridoio", SENSORE, F_ZIGBEE),
                   ("luce_garage", "Luce del garage", LUCE, MALEDUCATO)]

    def test_ne_trova_quattro_su_cinque_senza_fare_niente(self):
        trovati = self.aspetta(4)
        self.assertEqual(len(trovati), 4, "il quinto è il maleducato, e va preso a orecchio")

    def test_l_elenco_e_in_ordine_di_nome(self):
        # un elenco che cresce sotto gli occhi in ordine d'arrivo fa perdere il segno
        nomi = [d.nome for d in self.aspetta(4)]
        self.assertEqual(nomi, sorted(nomi, key=str.lower))

    def test_col_quinto_adottato_ci_sono_tutti(self):
        self.aspetta(4)
        self.casa.ascolta_tutto(True)
        self.finta.cambia("luce_garage", True)
        fine = time.monotonic() + 3.0
        while time.monotonic() < fine and not any("garage" in a for a, _ in self.casa.chi_parla()):
            time.sleep(0.02)
        self.casa.adotta("tuya/luce_garage/dps", "Luce del garage")
        self.assertEqual(len(self.casa.dispositivi()), 5)

    def test_due_marche_non_si_confondono(self):
        self.aspetta(4)
        self.assertEqual(self.uno("scale").marca, ESPHOME)
        self.assertEqual(self.uno("cucina").marca, ZIGBEE)
        self.assertEqual(json.loads(self.uno("cucina").carico_per(True)), {"state": "ON"})
        self.assertEqual(self.uno("scale").carico_per(True), b"ON")


class QuandoIlBrokerNonCeTest(unittest.TestCase):
    """Il caso che capita a tutti la prima volta: indirizzo sbagliato."""

    def test_dice_dove_non_e_riuscito_ad_arrivare(self):
        morto = __import__("socket").socket()
        morto.bind(("127.0.0.1", 0))
        porta = morto.getsockname()[1]
        morto.close()
        with self.assertRaises(mqtt.ErroreMqtt) as caduta:
            CasaSmart("127.0.0.1", porta).collega(secondi=1.0)
        self.assertIn(str(porta), str(caduta.exception))

    def test_un_comando_senza_collegamento_non_esplode(self):
        casa = CasaSmart("127.0.0.1", 1)
        self.assertFalse(casa.comanda("qualcosa", True))


class LeFormeDeiMessaggiTest(unittest.TestCase):
    """Come si leggono gli stati. Si sbaglia qui, e l'elenco resta a «non lo so» per sempre."""

    def test_le_parole_secche(self):
        d = Dispositivo("x", "X")
        for parola, atteso in ((b"ON", True), (b"on", True), (b" OFF ", False), (b"true", True),
                               (b"0", False), (b"DETECTED", True), (b"clear", False)):
            self.assertIs(d.leggi(parola), atteso, parola)

    def test_le_parole_che_l_annuncio_ha_insegnato(self):
        # un dispositivo può dire «acceso» a modo suo: l'annuncio lo spiega, e si usa quello
        d = Dispositivo("x", "X", parola_accesa="APERTA", parola_spenta="CHIUSA")
        self.assertIs(d.leggi(b"APERTA"), True)
        self.assertIs(d.leggi(b"CHIUSA"), False)
        self.assertEqual(d.carico_per(True), b"APERTA")

    def test_il_json(self):
        d = Dispositivo("x", "X", forma=JSON_STATO)
        self.assertIs(d.leggi(b'{"state": "ON"}'), True)
        self.assertIs(d.leggi(b'{"state": "OFF", "brightness": 80}'), False)
        self.assertEqual(json.loads(d.carico_per(False)), {"state": "OFF"})

    def test_quello_che_non_si_capisce_resta_non_si_sa(self):
        d = Dispositivo("x", "X")
        for parola in (b"", b"   ", b"forse", b"{", b'{"altro": 1}', b"[]"):
            self.assertIsNone(d.leggi(parola), parola)

    def test_non_lo_so_non_e_spento(self):
        # mostrare «spento» un dispositivo di cui non si sa niente è una bugia a cui qualcuno crede
        d = Dispositivo("x", "X")
        self.assertIsNone(d.acceso)
        self.assertIsNot(d.acceso, False)

    def test_un_dispositivo_senza_comando_non_si_comanda(self):
        self.assertFalse(Dispositivo("x", "X").comandabile)
        self.assertTrue(Dispositivo("x", "X", argomento_comando="a/set").comandabile)

    def test_il_nome_si_legge(self):
        from mirto.actions.smarthome import _nome_decente  # noqa: PLC0415

        self.assertEqual(_nome_decente("luce_delle_scale", "x"), "Luce delle scale")
        self.assertEqual(_nome_decente("", "casa/luce_garage"), "Luce garage")
        self.assertEqual(_nome_decente("Luce della cucina", "x"), "Luce della cucina")


class UnAnnuncioStortoTest(ConLaCasaFinta):
    """Un annuncio rotto non deve spegnere l'ascolto: il resto della casa deve continuare a vedersi."""

    dispositivi = [("luce_scale", "Luce delle scale", LUCE, F_ESPHOME)]

    def test_un_annuncio_illeggibile_si_ignora_e_basta(self):
        self.aspetta(1)
        spia = mqtt.Cliente("127.0.0.1", self.finta.porta, nome="guastatore").collega()
        self.addCleanup(spia.chiudi)
        spia.pubblica("homeassistant/light/rotta/config", b"{non e' json", ritenuto=True)
        spia.pubblica("homeassistant/light/senza_stato/config",
                      json.dumps({"name": "Senza stato"}), ritenuto=True)
        time.sleep(0.5)
        nomi = [d.nome for d in self.casa.dispositivi()]
        self.assertEqual(nomi, ["Luce delle scale"])
        self.assertTrue(self.casa.collegata(), "un messaggio storto non deve far cadere il collegamento")

    def test_un_componente_che_non_sappiamo_mostrare_si_lascia_stare(self):
        # una serranda o un termostato non sono «acceso o spento»: fingere di saperli comandare
        # sarebbe peggio che ignorarli
        self.aspetta(1)
        spia = mqtt.Cliente("127.0.0.1", self.finta.porta, nome="clima").collega()
        self.addCleanup(spia.chiudi)
        spia.pubblica("homeassistant/climate/termo/config",
                      json.dumps({"name": "Termostato", "state_topic": "termo/stato",
                                  "command_topic": "termo/set"}), ritenuto=True)
        time.sleep(0.5)
        self.assertEqual([d.nome for d in self.casa.dispositivi()], ["Luce delle scale"])


if __name__ == "__main__":
    unittest.main()


class ConfigFinta:
    """La configurazione, quanto basta: ``get`` a chiavi puntate, ``set`` e ``save``."""

    def __init__(self, dentro=None):
        self.dentro = dentro or {}
        self.salvata = 0

    def get(self, chiave, ripiego=None):
        posto = self.dentro
        for pezzo in chiave.split("."):
            if not isinstance(posto, dict) or pezzo not in posto:
                return ripiego
            posto = posto[pezzo]
        return posto

    def set(self, chiave, valore):
        posto = self.dentro
        pezzi = chiave.split(".")
        for pezzo in pezzi[:-1]:
            posto = posto.setdefault(pezzo, {})
        posto[pezzi[-1]] = valore

    def save(self):
        self.salvata += 1


class SegretiFinti(dict):
    def get(self, nome, ripiego=None):
        return dict.get(self, nome, ripiego)

    def set(self, nome, valore):
        self[nome] = valore

    def delete(self, nome):
        self.pop(nome, None)


class LaDomoticaRestaInPiediTest(unittest.TestCase):
    """Il difetto del 14/11: si configurava, funzionava, si chiudeva la finestra e spariva tutto.

    Il difetto non era il salvataggio: era il **posto**. Il collegamento viveva dentro la scheda
    delle impostazioni, e una cosa che deve restare viva mentre Mirto lavora non può vivere dentro
    una finestra — come non ci vivono i nodi né la presenza.
    """

    def setUp(self):
        from mirto.actions.smarthome import Domotica

        self.Domotica = Domotica
        self.finta = CasaFinta([
            DispositivoFinto("luce_scale", "Luce delle scale", LUCE, F_ESPHOME),
            DispositivoFinto("luce_cucina", "Luce della cucina", LUCE, F_ZIGBEE),
        ]).avvia()
        self.addCleanup(self.finta.ferma)
        self.cfg = ConfigFinta({"smarthome": {"enabled": True, "host": "127.0.0.1",
                                              "port": self.finta.porta}})
        self.segreti = SegretiFinti()

    def _domotica(self):
        d = self.Domotica(self.cfg, self.segreti)
        self.addCleanup(d.stop)
        return d

    def _aspetta(self, domotica, quanti, secondi=4.0):
        fine = time.monotonic() + secondi
        while time.monotonic() < fine and len(domotica.dispositivi()) < quanti:
            time.sleep(0.02)
        self.assertGreaterEqual(len(domotica.dispositivi()), quanti,
                                f"visti {[d.nome for d in domotica.dispositivi()]}")

    def test_si_apre_da_sola_all_avvio(self):
        d = self._domotica()
        d.start()
        self._aspetta(d, 2)
        self.assertTrue(d.stato()["collegata"])

    def test_chiusa_la_finestra_resta_su(self):
        """La prova del difetto: la scheda si stacca, la domotica no.

        Si imita quello che fa la scheda chiudendosi — smettere di ascoltare — e si pretende che il
        collegamento e i dispositivi siano ancora lì.
        """
        d = self._domotica()
        d.start()
        self._aspetta(d, 2)
        visto = []
        d.ascolta(visto.append)
        d.non_ascoltare(visto.append)          # è tutto quello che fa la scheda chiudendosi
        self.assertTrue(d.stato()["collegata"], "chiudere la finestra non deve staccare niente")
        self.assertEqual(len(d.dispositivi()), 2)
        self.assertTrue(d.comanda(d.dispositivi()[0].identificativo, True),
                        "e si deve ancora poter comandare")

    def test_riaprendo_mirto_la_configurazione_c_e_ancora(self):
        # la seconda metà dello stesso difetto: un altro avvio deve ritrovare tutto dalla
        # configurazione, senza che nessuno ridigiti l'indirizzo
        primo = self._domotica()
        primo.start()
        self._aspetta(primo, 2)
        primo.stop()
        secondo = self._domotica()
        secondo.start()
        self._aspetta(secondo, 2)
        self.assertTrue(secondo.stato()["collegata"])

    def test_senza_configurazione_non_fa_niente_e_non_si_lamenta(self):
        self.cfg.set("smarthome.host", "")
        d = self._domotica()
        self.assertFalse(d.start() or d.stato()["attiva"])
        self.assertEqual(d.dispositivi(), [])

    def test_spenta_non_si_collega_anche_se_l_indirizzo_c_e(self):
        self.cfg.set("smarthome.enabled", False)
        d = self._domotica()
        d.start()
        self.assertFalse(d.stato()["collegata"])

    def test_se_il_broker_e_spento_mirto_parte_lo_stesso_e_lo_dice(self):
        # un broker di casa spento non deve impedire l'avvio: sarebbe «Mirto non si apre» per una
        # lampadina
        self.cfg.set("smarthome.port", 1)
        d = self._domotica()
        self.assertFalse(d.start() or False)
        stato = d.stato()
        self.assertTrue(stato["attiva"], "resta configurata: è il broker a non rispondere")
        self.assertFalse(stato["collegata"])
        self.assertTrue(stato["errore"], "e si deve poter dire perché")

    def test_riapri_prende_le_impostazioni_nuove(self):
        self.cfg.set("smarthome.port", 1)
        d = self._domotica()
        d.start()
        self.assertFalse(d.stato()["collegata"])
        self.cfg.set("smarthome.port", self.finta.porta)
        self.assertTrue(d.riapri())
        self._aspetta(d, 2)
        self.assertEqual(d.stato()["errore"], "", "riuscendo, l'errore di prima va dimenticato")

    def test_gli_adottati_tornano_da_soli(self):
        self.cfg.set("smarthome.adopted", [{"topic": "tuya/x/dps", "name": "Presa strana",
                                            "command_topic": "tuya/x/dps/set"}])
        d = self._domotica()
        d.start()
        self._aspetta(d, 3)
        self.assertIn("Presa strana", [x.nome for x in d.dispositivi()])

    def test_chi_ascolta_viene_avvisato(self):
        d = self._domotica()
        d.start()
        self._aspetta(d, 2)
        visti = []
        d.ascolta(visti.append)
        self.finta.cambia("luce_scale", True)
        fine = time.monotonic() + 3.0
        while time.monotonic() < fine and not visti:
            time.sleep(0.02)
        self.assertTrue(visti, "un cambio in casa deve arrivare a chi ascolta")

    def test_un_ascoltatore_che_esplode_non_zittisce_gli_altri(self):
        # le Azioni e l'interfaccia ascolteranno insieme: se una delle due esplode, l'altra non
        # deve smettere di sapere cosa succede in casa
        d = self._domotica()
        d.start()
        self._aspetta(d, 2)
        buoni = []

        def cattivo(_x):
            raise RuntimeError("io esplodo")

        d.ascolta(cattivo)
        d.ascolta(buoni.append)
        self.finta.cambia("luce_scale", True)
        fine = time.monotonic() + 3.0
        while time.monotonic() < fine and not buoni:
            time.sleep(0.02)
        self.assertTrue(buoni)

    def test_la_password_si_prende_dal_portachiavi(self):
        from mirto.credentials import SMARTHOME_PASSWORD

        self.segreti.set(SMARTHOME_PASSWORD, "parola")
        d = self._domotica()
        d.start()
        self._aspetta(d, 2)
        self.assertEqual(d.casa.password, "parola",
                         "la password sta nel portachiavi, non nella configurazione")
        self.assertNotIn("parola", json.dumps(self.cfg.dentro),
                         "e non deve mai finire nel file di configurazione")

    def test_lo_stato_per_il_pannello_conta_quello_che_mostra(self):
        d = self._domotica()
        d.start()
        self._aspetta(d, 2)
        self.finta.cambia("luce_scale", True)
        fine = time.monotonic() + 3.0
        while time.monotonic() < fine and d.stato()["accesi"] < 1:
            time.sleep(0.02)
        stato = d.stato()
        self.assertEqual(stato["quanti"], 2)
        self.assertEqual(stato["accesi"], 1)
        self.assertEqual(stato["host"], "127.0.0.1")


class IlNucleoLaTieneLuiTest(unittest.TestCase):
    """Che il nucleo apra e chiuda la domotica si legge nel codice, non si spera.

    Si guardano i **nodi di chiamata**, non il testo: cercare «domotica.start» in un file passerebbe
    anche trovando la riga che la definisce, ed è il modo in cui si scrivono guardiani ciechi.
    """

    @staticmethod
    def _chiamate_dentro(nome_funzione: str) -> set[str]:
        import ast
        import pathlib

        albero = ast.parse(pathlib.Path("mirto/core.py").read_text(encoding="utf-8"))
        fuori = set()
        for nodo in ast.walk(albero):
            if not (isinstance(nodo, ast.FunctionDef) and nodo.name == nome_funzione):
                continue
            for dentro in ast.walk(nodo):
                if isinstance(dentro, ast.Call) and isinstance(dentro.func, ast.Attribute):
                    posto = dentro.func.value
                    if isinstance(posto, ast.Attribute):
                        fuori.add(f"{posto.attr}.{dentro.func.attr}")
        return fuori

    def test_il_nucleo_la_avvia(self):
        self.assertIn("domotica.start", self._chiamate_dentro("start"))

    def test_il_nucleo_la_ferma(self):
        self.assertIn("domotica.stop", self._chiamate_dentro("stop"))
