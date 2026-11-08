# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
import unittest

from mirto.actions.engine import ActionSpec
from mirto.actions.presets import ACTION_KINDS, default_action, describe_action, load_actions, unique_name


class PresetsTest(unittest.TestCase):
    def test_default_actions_are_valid_and_prudent(self):
        for kind in ACTION_KINDS:
            spec = ActionSpec.from_dict(default_action("ingresso", "cmd", kind, f"Prova {kind}"))
            self.assertEqual(spec.node, "ingresso")
        self.assertEqual(default_action("n", "s", "siren", "S")["mode"], "confirm")
        self.assertEqual(default_action("n", "u", "generic", "U")["mode"], "confirm")
        self.assertEqual(default_action("n", "l", "light", "L")["mode"], "auto")

    def test_load_normalizes_and_drops_orphans_and_invalid(self):
        items = [
            {"name": "Luci", "node": "a", "action": "luci", "kind": "Light", "mode": "AUTO"},
            {"name": "Vecchia", "node": "rimosso", "action": "x", "kind": "light"},
            {"name": "Rotta", "node": "a", "action": "x", "kind": "laser"},
        ]
        keep, dropped = load_actions(items, {"a"})
        self.assertEqual([a["name"] for a in keep], ["Luci"])
        self.assertEqual(keep[0]["kind"], "light")
        self.assertEqual(keep[0]["mode"], "auto")
        self.assertEqual(dropped, ["Vecchia", "Rotta"])

    def test_unique_name_and_description(self):
        self.assertEqual(unique_name("Luci – Ingresso", {"Luci – Ingresso"}), "Luci – Ingresso (2)")
        text = describe_action({"name": "Sirena", "kind": "siren", "mode": "confirm", "duration": 60, "delay": 10})
        self.assertIn("chiede conferma su Telegram", text)
        self.assertIn("60 s dopo 10 s", text)
        self.assertIn("solo a comando", describe_action({"name": "Microfono", "kind": "audio"}))

    def test_legacy_and_unknown_node_kinds(self):
        from mirto.actions.presets import kind_from_node
        self.assertEqual(kind_from_node("siren"), "siren")
        self.assertEqual(kind_from_node("fog"), "generic")
        self.assertEqual(kind_from_node("qualcosa"), "generic")
        keep, _dropped = load_actions([{"name": "Vecchia", "node": "a", "action": "x", "kind": "lock",
                                        "mode": "auto", "close_action": "chiudi"}], {"a"})
        self.assertEqual((keep[0]["kind"], keep[0]["mode"]), ("generic", "confirm"))
        self.assertNotIn("close_action", keep[0])




class MatchNodeCamerasTest(unittest.TestCase):
    def test_camera_linked_to_node(self):
        from mirto.actions.presets import match_node_cameras
        nodes = [{"id": "ingresso", "name": "Ingresso", "url": "https://mirto-ingresso.local:8443",
                  "info": {"host": "mirto-ingresso.local", "indirizzi": ["192.168.1.18"],
                           "camere": [{"percorso": "/cam", "porta": 8554, "in_linea": True}]}},
                 {"id": "garage", "name": "Garage", "url": "https://garage.local:8443", "info": {}}]
        nets = [{"id": "ingresso", "url": "rtsp://192.168.1.18:8554/cam",
                 "mdns": "Mirto Cam mirto-ingresso._rtsp._tcp.local."},
                {"id": "giardino", "url": "rtsp://10.0.0.5:554/stream1"}]
        cam_to_node, node_cams = match_node_cameras(nets, nodes)
        self.assertEqual(cam_to_node, {"net:ingresso": "ingresso"})
        self.assertEqual(node_cams["ingresso"][0]["cam_id"], "net:ingresso")
        self.assertNotIn("garage", node_cams)
        # stessa camera con indirizzo diverso (es. cavo) ma annuncio mDNS dello stesso Raspberry
        nets[0]["url"] = "rtsp://192.168.2.2:8554/cam"
        nets[0]["mdns"] = "Mirto Cam mirto-ingresso #2._rtsp._tcp.local."
        self.assertEqual(match_node_cameras(nets, nodes)[0], {"net:ingresso": "ingresso"})
        # camera del nodo non ancora aggiunta a Mirto
        _, node_cams = match_node_cameras(nets[1:], nodes)
        self.assertIsNone(node_cams["ingresso"][0]["cam_id"])


class LaLibreriaEPerScopiTest(unittest.TestCase):
    """La libreria propone **scopi**, non comandi del nodo (26/10).

    Marco: *«come faccio a creare un'azione campanello sempre attiva se non posso scegliere la
    tipologia di azione? in base al suono che uso?»* e poi *«possiamo preimpostare nella libreria
    queste azioni»*. Prima la libreria era un'azione per ogni comando — «Sirena – Ingresso», «Audio
    generato – Garage» — cioe' una libreria di dispositivi: diceva cosa sa fare il nodo, non a cosa
    serve a te. Per farsi un campanello bisognava sapere tre cose che il programma sa gia'.
    """

    COMANDI = [{"nome": "sirena", "tipo": "siren"},
               {"nome": "dissuasore", "tipo": "deterrent"},
               {"nome": "audio_generato", "tipo": "deterrent"},
               {"nome": "luce_prova", "tipo": "light"}]

    def _per_scopo(self, comandi=None, taken=()):
        from mirto.actions.presets import proposte_per_scopo

        pronte = proposte_per_scopo("ingresso", "Ingresso", self.COMANDI if comandi is None else comandi,
                                    set(taken))
        return {a["quando"] + ":" + a["action"]: a for a in pronte}, pronte

    def test_the_four_purposes_come_out_of_one_node(self):
        per, pronte = self._per_scopo()
        self.assertEqual(len(pronte), 4, sorted(per))
        self.assertIn("protetta:sirena", per, "antifurto: la sirena, a casa protetta")
        self.assertIn("sempre:audio_generato", per, "campanello: un suono, a casa aperta")
        self.assertIn("sempre:dissuasore", per, "dissuasore: animali")
        self.assertIn("prank:audio_generato", per, "scherzo")

    def test_every_ready_action_is_valid_for_the_engine(self):
        """Una proposta che il motore rifiuta e' peggio di nessuna proposta: la importi e sparisce."""
        _per, pronte = self._per_scopo()
        for a in pronte:
            spec = ActionSpec.from_dict(a)
            self.assertTrue(spec.targets, f"{a['name']} non riconosce niente")
            self.assertEqual(spec.quando, a["quando"], a["name"])

    def test_the_doorbell_is_not_a_deterrent(self):
        """Un dissuasore con «persona» fra gli obiettivi non e' un dissuasore -- regola di sempre --
        quindi un campanello nasce «generica», e va bene: cambia solo quali limiti ha."""
        per, _p = self._per_scopo()
        campanello = per["sempre:audio_generato"]
        self.assertEqual(ActionSpec.from_dict(campanello).kind, "generic")
        self.assertIn("person", campanello["targets"])

    def test_the_prank_arrives_switched_off_with_the_person_ticked(self):
        """Scelta di Marco, 26/10: completa ma spenta. Senza «persona» non servirebbe a niente -- e'
        l'unica azione a cui e' permessa -- ma un'azione puntata su una persona non si accende da
        sola a un clic di distanza."""
        per, _p = self._per_scopo()
        prank = per["prank:audio_generato"]
        self.assertFalse(prank["enabled"], "arriva spenta: la si legge, poi la si accende")
        self.assertIn("person", prank["targets"])
        self.assertEqual(ActionSpec.from_dict(prank).kind, "deterrent",
                         "ed e' l'unico dissuasore che tiene «persona»")

    def test_a_node_without_generated_audio_falls_back_to_the_deterrent(self):
        """L'ordine dentro SCOPI e' una preferenza, non un vincolo: un jingle e' meglio di uno sparo
        per un campanello, ma su un nodo che non ce l'ha lo sparo e' meglio di niente."""
        senza = [c for c in self.COMANDI if c["nome"] != "audio_generato"]
        per, _p = self._per_scopo(senza)
        self.assertIn("sempre:dissuasore", per)
        self.assertIn("prank:dissuasore", per)

    def test_a_node_that_cannot_do_something_is_not_offered_it(self):
        """Una voce che quel nodo non puo' eseguire e' una promessa che si scopre dopo averla
        importata."""
        per, pronte = self._per_scopo([{"nome": "luce_prova", "tipo": "light"}])
        self.assertEqual(pronte, [], sorted(per))

    def test_two_nodes_do_not_propose_the_same_name(self):
        from mirto.actions.presets import proposte_per_scopo

        primi = proposte_per_scopo("ingresso", "Ingresso", self.COMANDI, set())
        nomi = {a["name"] for a in primi}
        secondi = proposte_per_scopo("ingresso", "Ingresso", self.COMANDI, nomi)
        self.assertFalse(nomi & {a["name"] for a in secondi}, "un nome ripetuto non si puo' salvare")

    def test_the_library_does_not_swallow_two_purposes_of_one_command(self):
        """Campanello e scherzo possono suonare lo stesso jingle: li distingue solo «quando».

        La chiave dei doppioni era `comando|tipo`, e con due scopi sullo stesso comando il secondo si
        mangiava il primo -- in libreria compariva una voce sola, scelta dall'ordine di lettura.
        """
        from mirto.actions.pannello_azioni import libreria_aggregata

        _per, pronte = self._per_scopo()
        voci = libreria_aggregata({"ingresso": pronte})
        self.assertEqual(len(voci), 4, [v["titolo"] for v in voci])

    def test_the_same_purpose_on_two_nodes_is_one_entry(self):
        """Quello che la libreria aggregata faceva gia' (24/10) deve continuare a valere."""
        from mirto.actions.pannello_azioni import libreria_aggregata
        from mirto.actions.presets import proposte_per_scopo

        a = proposte_per_scopo("ingresso", "Ingresso", self.COMANDI, set())
        b = proposte_per_scopo("garage", "Garage", self.COMANDI, {x["name"] for x in a})
        voci = libreria_aggregata({"ingresso": a, "garage": b})
        self.assertEqual(len(voci), 4, "quattro scopi, non otto")
        self.assertTrue(all(len(v["nodi"]) == 2 for v in voci))

    def test_the_commands_the_library_asks_for_exist_on_the_node(self):
        """Se il nodo rinomina un comando, la libreria si svuota **in silenzio**.

        E' il difetto piu' brutto possibile qui: nessun errore, nessuna prova rossa, solo una finestra
        che dice «hai gia' importato tutte le azioni pronte». Quindi i nomi si rileggono dal codice
        del nodo, come il guardiano #11 fa con le rotte.
        """
        import re
        from pathlib import Path

        from mirto.actions.presets import SCOPI

        sorgente = (Path(__file__).resolve().parents[1] / "node" / "mirto_node" / "__main__.py"
                    ).read_text(encoding="utf-8")
        dichiarati = set(re.findall(r'devices\[?"(\w+)"\]?', sorgente))
        dichiarati |= set(re.findall(r'devices = \{"(\w+)"', sorgente))
        self.assertIn("sirena", dichiarati, "la lettura del codice del nodo non ha funzionato")
        for chiave, _nome, comandi in SCOPI:
            self.assertTrue(set(comandi) & dichiarati,
                            f"lo scopo «{chiave}» chiede {comandi}, che il nodo non dichiara piu'")


class QuandoSiLeggeNellaRigaTest(unittest.TestCase):
    """«Quando e' attiva» nella riga leggibile: non c'era, ed e' la differenza fra due voci."""

    def test_the_line_says_when_the_action_is_active(self):
        from mirto.actions.presets import azione_per_scopo

        self.assertIn("solo a casa protetta",
                      describe_action(azione_per_scopo("antifurto", "n1", "sirena", "A")))
        self.assertIn("anche a casa aperta",
                      describe_action(azione_per_scopo("campanello", "n1", "audio_generato", "B")))
        self.assertIn("Prank", describe_action(azione_per_scopo("prank", "n1", "dissuasore", "C")))

    def test_a_prank_is_not_given_limits_it_does_not_have(self):
        """Un prank non ha pausa ne' tetto giornaliero: scriverglieli accanto sarebbe la riga che
        dice il contrario di quello che fa."""
        from mirto.actions.presets import azione_per_scopo

        riga = describe_action(azione_per_scopo("prank", "n1", "dissuasore", "C"))
        self.assertNotIn("volte al giorno", riga)
        riga_dissuasore = describe_action(azione_per_scopo("dissuasore", "n1", "dissuasore", "D"))
        self.assertIn("volte al giorno", riga_dissuasore, "dove valgono, si dicono")

    def test_the_pause_of_an_always_on_action_is_written_too(self):
        """Un campanello che non risuona entro un minuto lo fa per la pausa, e finche' non era
        scritta da nessuna parte sembrava un difetto del campanello."""
        from mirto.actions.presets import azione_per_scopo

        riga = describe_action(azione_per_scopo("campanello", "n1", "audio_generato", "B"))
        self.assertIn("una volta al minuto", riga)


if __name__ == "__main__":
    unittest.main()
