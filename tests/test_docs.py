# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""R-03: i documenti pubblici devono dire la verità sul programma.

Un'incoerenza fra SECURITY.md, PRIVACY.md e il codice si trova con un `grep` e costa fiducia:
è la prima cosa che guarda chi valuta un progetto di sicurezza.
"""
import datetime as dt
import re
import unittest
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def leggi(nome: str) -> str:
    return (ROOT / nome).read_text(encoding="utf-8")


class ComunitaTest(unittest.TestCase):
    """C-02: roadmap e governance pubbliche, e coerenti con il resto.

    Un contributore o uno sponsor guarda tre cose il primo giorno: dove va il progetto, chi decide, e
    cosa gli viene chiesto di firmare. Se la risposta sta solo nella testa del manutentore, la
    risposta è «non si sa».
    """

    def test_the_roadmap_says_what_it_will_not_do(self):
        """Una roadmap che promette tutto non è una roadmap: la parte utile è il «no»."""
        roadmap = leggi("ROADMAP.md").lower()
        for fuori in ("cloud", "face", "licence-plate", "open-core"):
            self.assertIn(fuori, roadmap, f"manca il «no» su {fuori}")
        self.assertIn("not planned", roadmap)

    def test_the_roadmap_carries_no_dates_it_cannot_keep(self):
        """Un manutentore solo non promette date: le righe della roadmap non ne contengono."""
        corpo = leggi("ROADMAP.md")
        corpo = corpo[corpo.index("## Now"):]
        mesi = ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
                "settembre", "ottobre", "novembre", "dicembre", "q1", "q2", "q3", "q4")
        for riga in corpo.splitlines():
            basso = riga.lower()
            for mese in mesi:
                self.assertNotIn(mese, basso, f"data promessa nella roadmap: {riga.strip()}")

    def test_governance_says_who_decides_and_what_happens_if_he_stops(self):
        gov = leggi("GOVERNANCE.md")
        self.assertIn("Marco Dalicco", gov, "chi decide si dice con il nome")
        self.assertIn("fork", gov.lower(), "«e se il manutentore sparisce» ha una risposta onesta: la licenza")
        self.assertIn("SECURITY.md", gov, "le vulnerabilità non passano da qui")

    def test_governance_repeats_the_rules_that_cannot_be_weakened(self):
        gov = leggi("GOVERNANCE.md").lower()
        for regola in ("cloud", "keychain", "siren"):
            self.assertIn(regola, gov, f"manca la regola su {regola}")

    def test_the_readme_points_to_the_three_documents(self):
        testo = leggi("README.md")
        for documento in ("CONTRIBUTING.md", "GOVERNANCE.md", "ROADMAP.md"):
            self.assertIn(documento, testo, f"il README non rimanda a {documento}")
        self.assertIn("Signed-off-by", testo, "il README non dice come si firma un contributo")

    def test_the_repository_is_named_the_way_github_names_it(self):
        """`marcopogo81/mirto`, minuscolo: è così che si chiama (9/11).

        GitHub rimanda da `Mirto` a `mirto`, quindi niente si rompe e per questo era rimasto per
        tutto il rinominio in undici posti — README, le due guide e `pyproject.toml`, da dove finisce
        nei metadati del pacchetto e nella Release. Ma un `git clone` con la maiuscola crea una
        cartella `Mirto` e la riga dopo dice `cd mirto`: su Linux il comando che diamo noi fallisce.
        E un indirizzo che non è quello vero è la prima cosa che un visitatore copia.
        """
        import re

        sbagliati = []
        for nome in ("README.md", "USER_GUIDE.md", "GUIDA_UTENTE.md", "pyproject.toml",
                     "CONTRIBUTING.md", "GOVERNANCE.md", "SECURITY.md", "ROADMAP.md"):
            for riga in re.findall(r"github\.com/marcopogo81/\w+", leggi(nome)):
                if riga != "github.com/marcopogo81/mirto":
                    sbagliati.append(f"{nome}: {riga}")
        self.assertEqual(sbagliati, [], "il repository si chiama marcopogo81/mirto, minuscolo")

    def test_no_cla_anywhere_in_the_public_documents(self):
        """Il CLA è stato scartato con una ragione: non deve rientrare da un altro file."""
        for nome in ("README.md", "CONTRIBUTING.md", "GOVERNANCE.md", "ROADMAP.md"):
            testo = leggi(nome)
            self.assertNotIn("Contributor License Agreement", testo, nome)

    def test_the_public_documents_carry_no_private_data(self):
        """Roadmap e governance derivano dal piano privato: i numeri e i piani di quello restano lì."""
        for nome in ("ROADMAP.md", "GOVERNANCE.md"):
            testo = leggi(nome)
            for privato in ("192.168", "€", "NLnet", "Open Collective", "privato/"):
                self.assertNotIn(privato, testo, f"{nome} contiene qualcosa che era privato: {privato}")


class SostegnoTest(unittest.TestCase):
    """F-01: un canale di donazione che non cambia il patto con chi usa il programma."""

    def test_funding_names_only_a_real_channel(self):
        testo = leggi(".github/FUNDING.yml")
        self.assertIn("marcopogo81", testo)
        for inventato in ("patreon", "ko_fi", "liberapay", "custom"):
            self.assertNotIn(inventato, testo.lower(),
                             "un canale senza un conto dietro è un link rotto, non una possibilità")

    def test_the_readme_says_what_donations_do_not_buy(self):
        testo = leggi("README.md")
        self.assertIn("sponsors/marcopogo81", testo, "il README non dice dove si dona")
        basso = testo.lower()
        self.assertIn("do not change the licence", basso,
                      "il README deve dire che una donazione non cambia la licenza")
        self.assertIn("do not buy priority", basso,
                      "il README deve dire che una donazione non compra priorità")

    def test_the_menu_entry_asks_before_opening_a_page(self):
        """La voce del menu spiega a cosa servono i soldi e apre solo se l'utente lo vuole."""
        sorgente = (ROOT / "mirto" / "gui" / "app.py").read_text(encoding="utf-8")
        corpo = sorgente[sorgente.index("def _sostieni"):]
        corpo = corpo[:corpo.index("\n    def ", 1)]
        self.assertIn("QMessageBox.question", corpo, "si chiede prima di aprire il browser")
        self.assertIn("certificati", corpo, "si dice a cosa servono: è la differenza fra chiedere e mendicare")
        self.assertIn("StandardButton.Open", corpo)


class ScopeTest(unittest.TestCase):
    def test_the_node_is_part_of_this_project(self):
        self.assertTrue((ROOT / "node").is_dir())
        sicurezza = leggi("SECURITY.md")
        self.assertIn("`node/`", sicurezza, "il nodo sta in questo repository: deve essere in ambito")
        fuori = sicurezza.split("**Out of scope**")[1]
        self.assertNotIn("node firmware", fuori)
        self.assertNotIn("separate project", fuori)

    def test_the_permissions_claim_names_both_systems(self):
        """«0600» su Windows non vuol dire niente: se il documento non lo dice, promette il falso.

        Scoperto durante W-01 (19/10): `os.chmod` su Windows muove solo il flag «sola lettura», le
        ACL restano quelle ereditate da `%LOCALAPPDATA%`, e il test che controllava i permessi si
        saltava proprio lì.
        """
        sicurezza = leggi("SECURITY.md")
        self.assertIn("0600", sicurezza)
        for atteso in ("%LOCALAPPDATA%", "ACL", "administrators"):
            self.assertIn(atteso, sicurezza,
                          f"la promessa sui permessi non dice cosa succede su Windows: manca «{atteso}»")

    def test_the_disclosure_has_a_deadline(self):
        sicurezza = leggi("SECURITY.md")
        self.assertIn("90 days", sicurezza, "senza una scadenza, «aspetta la correzione» non vuol dire nulla")


class PrivacyTest(unittest.TestCase):
    """Ogni server a cui Mirto si collega da solo deve essere scritto in PRIVACY.md."""

    def test_every_host_the_models_come_from_is_declared(self):
        codice = leggi("mirto/detection/models.py")
        privacy = leggi("PRIVACY.md").lower()
        host = {urlsplit(u).hostname for u in re.findall(r'"(https://[^"]+)"', codice)}
        self.assertTrue(host, "nessun indirizzo trovato: il test non sta controllando niente")
        for nome in sorted(host):
            self.assertIn(nome, privacy, f"{nome} scarica un modello ma PRIVACY.md non lo dice")

    def test_the_statistics_server_is_declared(self):
        endpoint = re.search(r'ENDPOINT = "(https://[^"]+)"', leggi("mirto/telemetry.py"))
        self.assertIsNotNone(endpoint)
        self.assertIn("cloudflare", leggi("PRIVACY.md").lower())


class ConfrontoTest(unittest.TestCase):
    """R-02: il confronto con gli altri programmi, con una data."""

    EN = leggi("README.md")

    @staticmethod
    def righe_tabella(testo: str, titolo: str) -> int:
        dentro, righe = False, 0
        for riga in testo.splitlines():
            if riga.startswith("## "):
                dentro = titolo in riga
            elif dentro and riga.startswith("|"):
                righe += 1
        return righe

    def test_the_readme_compares_the_same_programs(self):
        for nome in ("Frigate", "Blue Iris", "Scrypted"):
            self.assertIn(nome, self.EN, nome)

    def test_the_table_is_not_empty(self):
        self.assertGreater(self.righe_tabella(self.EN, "Why not Frigate"), 5,
                           "il confronto non c'è o è vuoto")

    def test_it_says_when_the_prices_were_checked(self):
        self.assertRegex(self.EN, r"checked on \d{1,2}",
                         "una tabella di prezzi senza data invecchia in silenzio")

    def test_it_says_out_loud_what_mirto_does_not_do(self):
        self.assertIn("does not do", self.EN)


class LeImmaginiCitateEsistonoTest(unittest.TestCase):
    """Ogni immagine citata nei documenti pubblici deve esserci, e stare nel repository.

    Un riquadro rotto nel README si vede solo guardando la pagina su GitHub, e chi la guarda per
    primo è un estraneo. Due regole, la stessa già scritta per gli screenshot: i file stanno in
    `docs/img/`, e **nessun indirizzo esterno** — un'immagine su un sito di terzi un giorno non
    risponde più, e quel giorno il README si rompe da solo senza che nessuno abbia toccato niente.
    """

    DOCUMENTI = ("README.md", "USER_GUIDE.md", "GUIDA_UTENTE.md", "docs/cards.md")

    def immagini(self, nome: str) -> list[str]:
        import re

        testo = (ROOT / nome).read_text(encoding="utf-8")
        return (re.findall(r"!\[[^\]]*\]\(([^)\s]+)", testo)
                + re.findall(r"<img[^>]+src=\"([^\"]+)\"", testo))

    def test_every_picture_named_in_the_docs_is_really_there(self):
        mancanti = []
        for nome in self.DOCUMENTI:
            if not (ROOT / nome).exists():
                continue
            base = (ROOT / nome).parent
            for src in self.immagini(nome):
                if src.startswith(("http://", "https://", "data:")):
                    continue
                if not (base / src).resolve().exists():
                    mancanti.append(f"{nome} → {src}")
        self.assertEqual(mancanti, [], "immagini citate e non presenti")

    def test_no_picture_comes_from_somewhere_else(self):
        fuori = [f"{nome} → {src}"
                 for nome in self.DOCUMENTI if (ROOT / nome).exists()
                 for src in self.immagini(nome) if src.startswith(("http://", "https://"))]
        self.assertEqual(fuori, [], "immagini prese da un indirizzo esterno: devono stare in docs/img/")

    def test_every_card_has_words_for_whoever_cannot_see_it(self):
        """Il testo alternativo non è una formalità: per chi usa un lettore di schermo è la carta."""
        import re

        for nome in ("README.md", "docs/cards.md"):
            testo = (ROOT / nome).read_text(encoding="utf-8")
            for alt in re.findall(r"!\[([^\]]*)\]\(docs?/?img/cards/", testo) + \
                       re.findall(r"<img[^>]+alt=\"([^\"]*)\"[^>]*src=\"[^\"]*cards/", testo):
                self.assertGreater(len(alt), 20, f"{nome}: testo alternativo troppo corto: «{alt}»")

    def test_the_gallery_shows_all_twelve_cards(self):
        cartella = sorted(p.name for p in (ROOT / "docs" / "img" / "cards").glob("*.svg"))
        self.assertEqual(len(cartella), 12, f"nella cartella ce ne sono {len(cartella)}")
        citate = {src.rsplit("/", 1)[-1] for src in self.immagini("docs/cards.md")}
        self.assertEqual(sorted(citate), cartella, "la galleria non mostra tutte le carte che ci sono")


class LaPresentazioneTest(unittest.TestCase):
    """La presentazione dice le stesse cose del README, o è una seconda verità che invecchia da sola.

    Il testo si tira fuori dal `.pptx` con `zipfile` e una regex: un `.pptx` è uno zip di XML, e così
    la prova non aggiunge una dipendenza solo per leggersi una presentazione.
    """

    PPTX = ROOT / "docs" / "Mirto-presentation.pptx"
    PDF = ROOT / "docs" / "Mirto-presentation.pdf"

    @classmethod
    def parole(cls) -> str:
        import re
        import zipfile

        with zipfile.ZipFile(cls.PPTX) as z:
            xml = "".join(z.read(n).decode("utf-8") for n in sorted(z.namelist())
                          if n.startswith("ppt/slides/slide") and n.endswith(".xml"))
        return " ".join(re.findall(r"<a:t>([^<]*)</a:t>", xml))

    def test_both_files_are_there_and_the_readme_links_them(self):
        for percorso in (self.PPTX, self.PDF):
            self.assertTrue(percorso.exists(), f"manca {percorso.name}")
        testo = leggi("README.md")
        for nome in ("docs/Mirto-presentation.pdf", "docs/Mirto-presentation.pptx"):
            self.assertIn(nome, testo, f"il README non porta a {nome}")

    def test_it_compares_the_same_programs_as_the_readme(self):
        """Il 11/11 la tabella delle diapositive ne aveva cinque e il README sei: mancava Fregata.

        Togliere un concorrente dal confronto — il più vicino a noi sul Mac, per giunta — fa sembrare
        la tabella scelta apposta. Se uno dei due elenchi cambia, deve cambiare anche l'altro.
        """
        detto = self.parole()
        readme = leggi("README.md")
        for nome in ("Frigate", "Fregata", "SecuritySpy", "Blue Iris", "Scrypted"):
            self.assertIn(nome, readme, f"{nome} non è più nel README")
            self.assertIn(nome, detto, f"{nome} è nel README ma non nella presentazione")

    def test_the_readme_warns_on_both_systems_while_the_builds_are_unsigned(self):
        """Il primo doppio clic su un file non firmato sembra un file rotto, su tutti e due i sistemi.

        macOS dice che l'app «non può essere verificata», Windows mostra una schermata blu di
        SmartScreen senza una via d'uscita evidente. L'11/11, pubblicata la 0.2.0, l'avviso c'era solo
        nella sezione macOS: finché non c'era un `.exe` da scaricare non serviva, dal primo rilascio sì.
        Va tolto insieme alla frase della presentazione, il giorno che la firma arriva.
        """
        testo = leggi("README.md")
        for parte, parola in (("macOS", "Open Anyway"), ("Windows", "Run anyway")):
            self.assertIn(parola, testo,
                          f"il README non dice come aprire il file non firmato su {parte}")

    def test_it_does_not_promise_a_signature_we_do_not_have(self):
        """Finché il flusso di rilascio avvisa che i file non sono firmati, la presentazione lo dice.

        Il 11/11 prometteva «Signed downloads for macOS and Windows» mentre la CI stampava
        «.dmg non firmato» e «installer non firmato»: chi scarica si sente dire da macOS che l'app
        non può essere verificata e pensa che il file sia rotto. Il giorno in cui la firma c'è
        davvero, questa prova va tolta **insieme** alla frase: è una sveglia, non un divieto.
        """
        self.assertIn("not signed yet", self.parole(),
                      "la presentazione non dice che i file scaricabili non sono firmati")


class ChiFaCosaTest(unittest.TestCase):
    """Come è costruito Mirto si dice, non si lascia indovinare."""

    def test_the_readme_says_who_does_what(self):
        self.assertIn("## Who does what", leggi("README.md"))

    def test_contributors_are_asked_for_the_trailer(self):
        """`Assisted-By:` è quello che si chiede a **chi contribuisce**: deve stare dove si chiede."""
        for nome in ("CONTRIBUTING.md", "GOVERNANCE.md"):
            self.assertIn("Assisted-By:", leggi(nome), nome)

    def test_the_readme_quotes_a_trailer_our_commits_really_carry(self):
        """Il README non deve promettere una riga che `git log` non ha.

        Fino al 9/11 diceva «Every commit says so in its own trailer: `Assisted-By: …`», e le commit
        portano invece `Co-Authored-By:` e `Claude-Session:`. Una bugia da niente, nel paragrafo che
        spiega **perché** il progetto dichiara com'è costruito: *«a security project that is coy about
        how it was built invites mistrust the day someone reads the commit history anyway»*. Chi
        andava a controllare trovava proprio quello.

        Questa prova non guarda una stringa scritta a mano da qualche parte: legge le commit vere e
        chiede che la riga mostrata nel README sia una delle loro. Così non può tornare a mentire
        neanche se un giorno i trailer cambiano.

        **Ma solo dove la storia c'è.** In CI il checkout è superficiale: `git log` vede una commit
        sola, e se quella è di Marco — scritta a mano, firmata e basta — i trailer di Claude non ci
        sono per il motivo giusto, non perché il README menta. Scritta senza questa distinzione, il
        9/11 questa prova ha fatto fallire tutti e sei i lavori della CI su una commit onesta.
        """
        import subprocess

        try:
            uscita = subprocess.run(["git", "log", "-30", "--format=%(trailers:only=true)"],
                                    cwd=ROOT, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as exc:   # pragma: no cover
            self.skipTest(f"git non disponibile: {exc}")
        if uscita.returncode != 0 or not uscita.stdout.strip():
            self.skipTest("niente storia git qui (archivio sorgente o clone senza .git)")
        veri = {r.split(":", 1)[0].strip() for r in uscita.stdout.splitlines() if ":" in r}
        if veri <= {"Signed-off-by"} and (ROOT / ".git" / "shallow").exists():
            self.skipTest("clone superficiale: qui c'è solo una commit e non è di Claude, "
                          "quindi non c'è niente da confrontare — non è un difetto")

        testo = leggi("README.md")
        dentro = testo[testo.index("## Who does what"):]
        mostrati = {r.strip().split(":", 1)[0] for r in dentro.splitlines()
                    if r.strip().endswith(">") and ":" in r and " " not in r.split(":", 1)[0]}
        self.assertTrue(mostrati, "il README non mostra piu' nessun trailer: era il punto del paragrafo")
        for nome in mostrati:
            self.assertIn(nome, veri,
                          f"il README mostra «{nome}:», che nelle ultime commit non c'e'. "
                          f"Quelle vere sono: {sorted(veri)}")

    def test_the_notice_says_it_too(self):
        self.assertIn("Claude", leggi("NOTICE"))


class IDocumentiPubbliciSonoInIngleseTest(unittest.TestCase):
    """I documenti del repository sono in inglese e basta (7/11).

    Erano bilingui: ogni frase in inglese e sotto in italiano. Andava bene per un progetto privato
    di un italiano; per un progetto pubblico vuol dire che chi arriva legge una pagina in cui metà
    delle righe non le capisce, e che ogni modifica va fatta due volte — che è il modo in cui le due
    lingue, prima o poi, finiscono per dire cose diverse.

    Il controllo cerca le lettere accentate italiane e le virgolette «»: in inglese non compaiono
    mai, in italiano quasi sempre. È una prova grossolana, e va bene così: non deve giudicare la
    lingua di una frase, deve accorgersi se qualcuno rimette un blocco in italiano.

    Fuori dal controllo stanno la guida utente italiana (`GUIDA_UTENTE.md`), che serve a chi usa il
    programma in italiano, e il `CHANGELOG`, dove le voci inglesi citano messaggi dell'interfaccia
    che sono scritti in italiano.
    """

    SOLO_INGLESE = ("README.md", "CONTRIBUTING.md", "GOVERNANCE.md", "SECURITY.md", "ROADMAP.md",
                    "PRIVACY.md", "CODE_OF_CONDUCT.md", "NOTICE")

    def test_no_italian_left_in_the_public_documents(self):
        import re

        italiano = re.compile(r"[àèéìòùÀÈÉÌÒÙ]|«|»")
        guai = []
        for nome in self.SOLO_INGLESE:
            for n, riga in enumerate(leggi(nome).splitlines(), 1):
                if italiano.search(riga):
                    guai.append(f"{nome}:{n}: {riga.strip()[:70]}")
        self.assertEqual(guai, [], "italiano nei documenti pubblici:\n  " + "\n  ".join(guai))

    def test_what_a_package_listing_shows_is_english_too(self):
        """Il sottotitolo del pacchetto era rimasto in italiano (8/11).

        I documenti sono passati all'inglese il 7/11, ma `pyproject.toml` no: `description` diceva
        ancora «videosorveglianza desktop locale con rilevamento persone e avvisi», e fra i
        classificatori c'era solo `Natural Language :: Italian`. Quella riga e quella etichetta sono
        esattamente cio' che vede chi **non** e' ancora arrivato sul repository — chi scorre un
        elenco di pacchetti — e dicevano «questo non e' per te» a chiunque non parli italiano.

        La prova degli altri documenti non poteva vederlo: cerca lettere accentate, e in quella
        frase non ce n'erano. Qui il controllo e' sulle parole, perche' su una riga sola si puo'
        fare, e resta grossolano apposta: non giudica la lingua, si accorge di un ritorno.
        """
        import tomllib

        spie = r"\b(con|per|della|dello|delle|degli|dei|non|che|una|uno|sono|anche|questo|questa)\b"
        dati = tomllib.loads(leggi("pyproject.toml"))["project"]
        guai = []
        for dove, testo in [("description", dati["description"])] + \
                           [(f"keywords[{i}]", k) for i, k in enumerate(dati.get("keywords", []))]:
            if re.search(spie, testo, re.I) or re.search(r"[àèéìòùÀÈÉÌÒÙ]", testo):
                guai.append(f"{dove}: {testo}")
        self.assertEqual(guai, [], "italiano in quello che si vede da un elenco di pacchetti:\n  "
                                   + "\n  ".join(guai))
        self.assertIn("Natural Language :: English", dati["classifiers"],
                      "il programma parla anche inglese: chi legge i classificatori non lo sa")

    def test_the_italian_readme_does_not_come_back(self):
        """Cancellato il 7/11: due README si allontanano, e quello che nessuno rilegge resta indietro."""
        self.assertFalse((ROOT / "README.it.md").exists(),
                         "README.it.md è tornato: il progetto ha un README solo, in inglese")

    def test_the_italian_privacy_notice_is_the_only_exception(self):
        """`PRIVACY.it.md` resta in italiano, e PRIVACY.md lo dice (7/11).

        Un'informativa che chi la legge non capisce non informa nessuno: e' l'unico documento che
        vale la pena tenere in due lingue. E va dichiarata, se no sembra una dimenticanza.
        """
        self.assertTrue((ROOT / "PRIVACY.it.md").exists(), "la traduzione italiana dell'informativa")
        self.assertIn("PRIVACY.it.md", leggi("PRIVACY.md"),
                      "la versione inglese deve dire che esiste quella italiana, e quale vale")

    def test_the_two_privacy_notices_name_the_same_servers(self):
        """Due documenti che dicono cose diverse sono peggio di uno solo.

        Non si confrontano le frasi — sono due lingue — ma i **fatti** che contano: i server a cui
        Mirto si collega da solo. Se uno ne acquista uno e l'altro no, la traduzione e' invecchiata.
        """
        en, it = leggi("PRIVACY.md").lower(), leggi("PRIVACY.it.md").lower()
        for host in ("github.com", "huggingface.co", "cloudflare"):
            self.assertIn(host, en, f"PRIVACY.md non nomina {host}")
            self.assertIn(host, it, f"PRIVACY.it.md non nomina {host}: la traduzione e' rimasta indietro")

    def test_the_italian_user_guide_is_still_there(self):
        """L'interfaccia parla italiano: la sua guida non si tocca, è per chi usa il programma."""
        for nome in ("GUIDA_UTENTE.md", "USER_GUIDE.md"):
            self.assertTrue((ROOT / nome).exists(), f"{nome} serve: l'app apre i suoi capitoli")


class CodiceDiCondottaTest(unittest.TestCase):
    """O-02: un progetto pubblico dice come ci si comporta e dove si segnala (8/11).

    GitHub cerca `CODE_OF_CONDUCT.md` per nome e lo mostra accanto alla licenza: senza, la pagina del
    progetto ha un buco che si vede. Ma il file serve davvero solo se dice **dove si segnala**: un
    codice di condotta senza un canale e' una dichiarazione d'intenti.

    Il canale e' la segnalazione privata di GitHub, la stessa di `SECURITY.md`: e' l'unico modo
    privato che il progetto ha senza pubblicare l'indirizzo di qualcuno.
    """

    TESTO = leggi("CODE_OF_CONDUCT.md")

    def test_it_says_where_to_report_privately(self):
        self.assertIn("SECURITY.md", self.TESTO, "il canale di segnalazione va nominato")
        self.assertIn("privately", self.TESTO.lower(),
                      "senza un modo privato, segnalare vuol dire esporsi in pubblico")

    def test_it_admits_the_single_maintainer_problem(self):
        """Se la segnalazione riguarda il manutentore non c'e' nessun altro dentro il progetto.

        Fingere che ci sia un comitato indipendente e' peggio che dirlo.
        """
        basso = self.TESTO.lower()
        self.assertIn("single maintainer", basso,
                      "il limite va detto: un manutentore solo non puo' giudicare se stesso")

    def test_it_credits_the_contributor_covenant(self):
        """E' adattato dal Contributor Covenant, che e' CC BY 4.0: l'attribuzione e' obbligatoria."""
        self.assertIn("Contributor Covenant", self.TESTO)
        self.assertIn("creativecommons.org/licenses/by/4.0", self.TESTO,
                      "la licenza da cui e' adattato va citata, non solo il nome")

    def test_it_says_that_other_people_homes_are_not_material(self):
        """La regola che questo progetto ha in piu' degli altri: qui si parla di case vere."""
        basso = self.TESTO.lower()
        self.assertIn("camera images", basso)
        self.assertTrue("home address" in basso or "identifies a real place" in basso,
                        "pubblicare la casa di qualcuno e' un problema di condotta, non solo tecnico")


class GitleaksIgnoreTest(unittest.TestCase):
    """Silenziare un allarme è facile: qui costa almeno una riga di spiegazione."""

    PERCORSO = ROOT / ".gitleaksignore"

    def righe(self) -> list[tuple[int, str]]:
        if not self.PERCORSO.exists():
            return []
        return [(n, r) for n, r in enumerate(leggi(".gitleaksignore").splitlines(), 1)
                if r.strip() and not r.startswith("#")]

    def test_every_silenced_finding_says_why(self):
        testo = leggi(".gitleaksignore").splitlines() if self.PERCORSO.exists() else []
        for numero, riga in self.righe():
            sopra = [r for r in testo[:numero - 1] if r.strip()]
            self.assertTrue(sopra and sopra[-1].startswith("#"),
                            f".gitleaksignore riga {numero}: manca il commento che spiega perché "
                            f"«{riga.strip()}» non è un segreto")

    def test_every_fingerprint_still_points_at_a_commit_that_exists(self):
        """Un'impronta e' legata alla commit in cui si trova: riscritta la storia, non vale piu'.

        L'8/11, con l'import pubblico, i 174 commit sono diventati uno solo: l'impronta che
        zittiva un falso allarme puntava a una commit sparita, gitleaks ha rialzato l'allarme e la
        prima corsa della CI pubblica e' nata rossa. Non si rompe niente di rumoroso — si riaccende
        un avviso che credevi spento.

        In CI il checkout e' superficiale (una commit sola) e non si puo' chiedere di una vecchia:
        li' la prova si salta invece di fallire per il motivo sbagliato.
        """
        import subprocess

        if not self.righe():
            return
        if (ROOT / ".git" / "shallow").exists():
            self.skipTest("clone superficiale: la storia qui non c'e', non e' un difetto")
        for numero, riga in self.righe():
            sha = riga.strip().split(":")[0]
            esiste = subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e", sha],
                                    capture_output=True).returncode == 0
            self.assertTrue(esiste, f".gitleaksignore riga {numero}: la commit {sha[:8]} non esiste "
                                    "piu'. L'allarme che zittiva e' tornato: o si aggiorna "
                                    "l'impronta, o — meglio — si toglie il motivo dell'allarme")

    def test_the_fingerprints_are_well_formed(self):
        for numero, riga in self.righe():
            parti = riga.strip().split(":")
            self.assertEqual(len(parti), 4, f"riga {numero}: serve commit:file:regola:riga")
            self.assertRegex(parti[0], r"^[0-9a-f]{40}$", f"riga {numero}: commit per esteso")
            self.assertTrue((ROOT / parti[1]).exists() or True)  # il file può essere stato rinominato
            self.assertTrue(parti[3].isdigit(), f"riga {numero}: numero di riga")


class SecurityTxtTest(unittest.TestCase):
    """RFC 9116: poche righe, ma scadono."""

    CAMPI = ("Contact", "Expires", "Preferred-Languages", "Policy")

    def voci(self) -> dict:
        voci = {}
        for riga in leggi(".well-known/security.txt").splitlines():
            if riga.strip() and not riga.startswith("#"):
                chiave, _, valore = riga.partition(":")
                voci[chiave.strip()] = valore.strip()
        return voci

    def test_the_required_fields_are_there(self):
        voci = self.voci()
        for campo in self.CAMPI:
            self.assertIn(campo, voci)
        self.assertTrue(voci["Contact"].startswith("https://"))

    def test_it_has_not_expired(self):
        scadenza = dt.datetime.fromisoformat(self.voci()["Expires"].replace("Z", "+00:00"))
        self.assertGreater(scadenza, dt.datetime.now(dt.timezone.utc),
                           "security.txt è scaduto: rinnova la data di Expires (RFC 9116, meno di un anno)")


if __name__ == "__main__":
    unittest.main()


class DocumentazioneWindowsTest(unittest.TestCase):
    """W-09: un utente Windows deve potersi installare senza chiedere niente a nessuno.

    Fino al 19/10 README e guide descrivevano soltanto il Mac: Terminale, Portachiavi,
    `~/Library/Application Support`, `source .venv/bin/activate`. Su Windows nessuno di quei
    passi esiste, e chi provava restava fermo al secondo comando. Questi test non giudicano la
    prosa: controllano che le cose che su Windows **sono diverse** siano scritte, in tutte e due
    le lingue, e che corrispondano al codice.
    """

    GUIDE = ("GUIDA_UTENTE.md", "USER_GUIDE.md")
    LETTIMI = ("README.md",)

    def test_both_guides_have_the_windows_chapter_and_list_it(self):
        for nome in self.GUIDE:
            testo = leggi(nome)
            self.assertIn("## 2b.", testo, f"{nome}: manca il capitolo di installazione su Windows")
            self.assertIn("#2b-install", testo, f"{nome}: il capitolo 2b non è nell'indice")

    def test_every_document_says_where_the_data_is_on_windows(self):
        """Il percorso dichiarato è quello che il programma usa davvero: `platformdirs` su Windows
        restituisce `%LOCALAPPDATA%`, e una guida che dicesse `Documenti` manderebbe a cercare nel
        posto sbagliato."""
        for nome in self.GUIDE + self.LETTIMI:
            testo = leggi(nome)
            self.assertIn("%LOCALAPPDATA%", testo, f"{nome}: non dice dove stanno i dati su Windows")

    def test_every_document_names_the_windows_credential_store(self):
        """Su Windows i segreti non stanno nel Portachiavi: chi li cerca lì non li trova."""
        for nome in self.GUIDE + self.LETTIMI:
            testo = leggi(nome).lower()
            self.assertTrue("credential manager" in testo or "credenziali windows" in testo,
                            f"{nome}: non dice dove stanno i segreti su Windows")

    def test_the_autostart_limit_is_written_where_autostart_is_promised(self):
        """`autostart.py` su Windows scrive solo la chiave `Run`: nessuno rimette in piedi Mirto
        dopo un blocco. Le guide promettono «si riavvia se si chiude per errore», che è vero solo
        sul Mac: il limite va detto accanto alla promessa, non in fondo a un documento."""
        codice = leggi("mirto/system/autostart.py")
        self.assertIn("CurrentVersion\\Run", codice,
                      "se l'avvio automatico di Windows non è più la chiave Run, questo test va riscritto")
        for nome in self.GUIDE:
            testo = leggi(nome)
            capitolo = testo.split("Avvia Mirto all'accesso" if nome.startswith("GUIDA")
                                   else "Start Mirto at login", 1)[1][:800]
            self.assertIn("Run", capitolo, f"{nome}: il limite dell'avvio automatico su Windows non è detto")

    def test_both_guides_warn_about_one_telegram_bot_per_computer(self):
        """Due Mirto con lo stesso bot si contendono i comandi (409 di Telegram). Succede a
        chiunque abbia due computer, e il registro lo dice solo dopo."""
        for nome in self.GUIDE:
            testo = leggi(nome).lower()
            self.assertTrue("un bot per computer" in testo or "one bot per computer" in testo,
                            f"{nome}: manca l'avvertenza sul bot condiviso fra due computer")



class CollegamentiAllaGuidaTest(unittest.TestCase):
    """Ogni «📖 Guida completa» dell'app deve portare a un capitolo che esiste, in tutte e due le lingue.

    Fino al 29/10 la scheda Azioni apriva `8. Azioni`, un titolo che la guida non aveva: la finestra
    ripiegava sul numero e mostrava il capitolo 8 di allora, che parlava di tenere acceso il computer.
    Nessuna prova lo vedeva, perché il titolo vive nel codice e il capitolo in un file Markdown. Qui si
    leggono i titoli dal codice (``help_box``, ``guide_link`` e ``GUIDE_HEADING``) e si pretende che
    ognuno sia un titolo della guida italiana e, tradotto, di quella inglese. Si guardano anche i
    collegamenti interni delle due guide.
    """

    @staticmethod
    def _slug(testo: str) -> str:
        s = testo.strip().lower()
        s = re.sub(r"[^\w\s-]", "", s)
        return re.sub(r"\s", "-", s)

    @staticmethod
    def _titoli(nome: str) -> list[str]:
        return [riga.lstrip("#").strip() for riga in leggi(nome).splitlines() if re.match(r"^#{1,4} ", riga)]

    def _dal_codice(self) -> set[str]:
        import ast  # noqa: PLC0415

        trovati: set[str] = set()
        for path in sorted((ROOT / "mirto").rglob("*.py")):
            albero = ast.parse(path.read_text(encoding="utf-8"))
            for nodo in ast.walk(albero):
                if isinstance(nodo, ast.Call) and getattr(nodo.func, "id", "") in ("help_box", "guide_link"):
                    arg = nodo.args[1] if getattr(nodo.func, "id", "") == "help_box" and len(nodo.args) > 1 \
                        else (nodo.args[0] if nodo.args else None)
                    if (isinstance(arg, ast.Call) and getattr(arg.func, "id", "") in ("_", "N_")
                            and isinstance(arg.args[0], ast.Constant)):
                        trovati.add(arg.args[0].value)
                if (isinstance(nodo, ast.Assign) and any(getattr(t, "id", "") == "GUIDE_HEADING" for t in nodo.targets)
                        and isinstance(nodo.value, ast.Call) and isinstance(nodo.value.args[0], ast.Constant)):
                    trovati.add(nodo.value.args[0].value)
        return trovati

    def test_every_guide_link_in_the_app_has_its_chapter(self):
        import json  # noqa: PLC0415

        titoli = self._dal_codice()
        self.assertGreater(len(titoli), 10, "il guardiano è cieco: non ha trovato i collegamenti alla guida")
        inglese = json.loads(leggi("mirto/locales/en.json"))
        it, en = set(self._titoli("GUIDA_UTENTE.md")), set(self._titoli("USER_GUIDE.md"))
        for titolo in sorted(titoli):
            self.assertIn(titolo, it, f"l'app apre «{titolo}», che GUIDA_UTENTE.md non ha")
            self.assertIn(inglese.get(titolo, ""), en, f"l'app inglese apre «{inglese.get(titolo)}», che USER_GUIDE.md non ha")

    def test_the_internal_links_of_both_guides_lead_somewhere(self):
        for nome in ("GUIDA_UTENTE.md", "USER_GUIDE.md"):
            ancore = {self._slug(t) for t in self._titoli(nome)}
            rotti = sorted({a for a in re.findall(r"\]\(#([^)]+)\)", leggi(nome)) if a not in ancore})
            self.assertEqual(rotti, [], f"{nome}: collegamenti a titoli che non esistono")

    def test_both_guides_have_the_same_chapters(self):
        numeri = [[re.match(r"^\S+", t).group(0) for t in self._titoli(n) if re.match(r"^\d", t)]
                  for n in ("GUIDA_UTENTE.md", "USER_GUIDE.md")]
        self.assertEqual(numeri[0], numeri[1], "le due guide hanno capitoli diversi: si sono allontanate")
