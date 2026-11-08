# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Gli strumenti in ``tools/``: non girano in produzione, ma se sbagliano lo fanno in silenzio."""
import importlib.util
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def carica(nome: str):
    """Importa uno script di ``tools/``, che ha il trattino nel nome e non è un modulo."""
    spec = importlib.util.spec_from_file_location(nome.replace("-", "_"), ROOT / "tools" / nome)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


fissa = carica("fissa-action.py")
note = carica("note-di-rilascio.py")
tempi = carica("tempi-test.py")

TAG_ANNOTATO = ("dcedce43c6f43de0b836d1fe38946645c9c638dc\trefs/tags/v2\n"
                "ff98106e4c7b2bc287b24eaf42907196329070c7\trefs/tags/v2^{}\n")
TAG_SEMPLICE = "3d3c42e5aac5ba805825da76410c181273ba90b1\trefs/tags/v7\n"


class FissaActionTest(unittest.TestCase):
    def test_an_annotated_tag_gives_the_commit_not_the_tag_object(self):
        sha = fissa.sha_del_riferimento("gitleaks/gitleaks-action", "v2", lambda r, t: TAG_ANNOTATO)
        self.assertEqual(sha, "ff98106e4c7b2bc287b24eaf42907196329070c7",
                         "un oggetto-tag non è un commit: GitHub Actions lo rifiuta")

    def test_a_plain_tag_gives_its_own_sha(self):
        sha = fissa.sha_del_riferimento("actions/checkout", "v7", lambda r, t: TAG_SEMPLICE)
        self.assertEqual(sha, "3d3c42e5aac5ba805825da76410c181273ba90b1")

    def test_an_unknown_tag_is_an_error(self):
        with self.assertRaises(LookupError):
            fissa.sha_del_riferimento("actions/checkout", "v99", lambda r, t: "")

    def test_the_version_stays_in_the_comment(self):
        nuova, tag = fissa.riscrivi_riga("      - uses: actions/checkout@v7\n",
                                         lambda r, t: "a" * 40)
        self.assertEqual(tag, "v7")
        self.assertEqual(nuova, f"      - uses: actions/checkout@{'a' * 40}  # v7\n")

    def test_an_already_pinned_action_is_renewed_from_its_comment(self):
        riga = f"      - uses: actions/checkout@{'b' * 40}  # v7\n"
        nuova, tag = fissa.riscrivi_riga(riga, lambda r, t: "c" * 40)
        self.assertEqual(tag, "v7")
        self.assertEqual(nuova, f"      - uses: actions/checkout@{'c' * 40}  # v7\n")

    def test_a_pinned_action_without_comment_is_left_alone(self):
        riga = f"      - uses: actions/checkout@{'b' * 40}\n"
        self.assertEqual(fissa.riscrivi_riga(riga, lambda r, t: "c" * 40), (riga, ""))

    def test_other_lines_are_left_alone(self):
        for riga in ("      - name: Run tests\n", "        run: python -m unittest\n", "\n"):
            self.assertEqual(fissa.riscrivi_riga(riga, lambda r, t: "c" * 40), (riga, ""))


CHANGELOG_FINTO = """# Changelog

## [Unreleased]

## [0.2.0] – da rilasciare

### Security
- Una cosa importante.

### Fixed
- Un errore corretto.

## [0.1.0] – 1 gennaio 2026

- Il primo rilascio.
"""


class NoteDiRilascioTest(unittest.TestCase):
    def test_the_notes_are_the_whole_section_and_stop_at_the_next_version(self):
        testo = note.sezione(CHANGELOG_FINTO, "0.2.0")
        self.assertTrue(testo.startswith("### Security"), testo[:40])
        self.assertIn("### Fixed", testo)
        self.assertNotIn("0.1.0", testo)
        self.assertNotIn("Il primo rilascio", testo)

    def test_the_heading_is_not_part_of_the_notes(self):
        self.assertNotIn("[0.1.0]", note.sezione(CHANGELOG_FINTO, "0.1.0"))
        self.assertEqual(note.sezione(CHANGELOG_FINTO, "0.1.0"), "- Il primo rilascio.")

    def test_an_unknown_version_is_an_error(self):
        with self.assertRaises(LookupError):
            note.sezione(CHANGELOG_FINTO, "9.9.9")

    def test_the_current_version_has_notes_in_the_real_changelog(self):
        import sys
        sys.path.insert(0, str(ROOT))
        from mirto import __version__

        testo = note.sezione((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), __version__)
        self.assertGreater(len(testo), 200, "le note della Release sarebbero quasi vuote")


class DcoTest(unittest.TestCase):
    """C-01: il DCO al posto del CLA, e un controllo che non si fida delle buone intenzioni.

    In un progetto che vuole restare libero un CLA è un segnale sbagliato: chiede ai contributori di
    cedere diritti «in caso un giorno si cambi licenza». Il Developer Certificate of Origin chiede
    l'unica cosa che serve davvero — dichiarare di avere il diritto di contribuire quel codice — e si
    firma con `git commit -s`.
    """

    DCO = ROOT / ".github" / "workflows" / "dco.yml"

    def test_the_check_runs_on_pull_requests(self):
        testo = self.DCO.read_text(encoding="utf-8")
        self.assertIn("pull_request:", testo)
        self.assertIn("Signed-off-by", testo)
        self.assertIn("fetch-depth: 0", testo, "senza la storia completa non si leggono i commit della PR")
        self.assertIn("rebase --signoff", testo, "chi sbaglia deve trovare il rimedio nell'errore")

    def test_the_check_and_its_local_copy_agree_about_merge_commits(self):
        """Il controllo vero e la sua copia locale devono perdonare le stesse commit (8/11).

        Una commit di fusione non la scrive nessuno, quindi non c'e' nessuno che possa certificarla, e
        `rebase --signoff` — il rimedio che l'errore suggerisce — la riscriverebbe. Se le due meta'
        non sono d'accordo, una pull request e' verde da una parte e rossa dall'altra, e chi la manda
        non capisce quale delle due gli stia dicendo la verita'.

        E' il difetto del 26/10 in un'altra veste: due elenchi scritti a mano in due posti restano
        pari finche' qualcuno se lo ricorda. Qui il legame e' questa prova.
        """
        self.assertIn("rev-list --no-merges", self.DCO.read_text(encoding="utf-8"),
                      "dco.yml chiede la firma anche alle commit di fusione, la copia locale no")
        mio = Path(__file__).read_text(encoding="utf-8")
        self.assertIn('"-15", "--no-merges"', mio,
                      "la copia locale perdona le fusioni, dco.yml no")

    def test_the_check_has_no_third_party_action(self):
        """Un controllo di dieci righe non vale una dipendenza in più nella catena di fornitura."""
        usati = re.findall(r"uses:\s*([\w.-]+/[\w.-]+)", self.DCO.read_text(encoding="utf-8"))
        self.assertEqual(set(usati), {"actions/checkout"}, f"action di terzi nel controllo DCO: {usati}")

    def test_every_action_is_pinned_to_a_sha(self):
        for riga in self.DCO.read_text(encoding="utf-8").splitlines():
            if "uses:" in riga:
                self.assertRegex(riga, r"@[0-9a-f]{40}\s+# v", f"action non fissata allo SHA: {riga.strip()}")

    def test_contributing_asks_for_the_sign_off_and_never_a_cla(self):
        testo = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        self.assertIn("git commit -s", testo)
        self.assertIn("developercertificate.org", testo)
        for parola in ("Contributor License Agreement", "CLA"):
            self.assertNotIn(parola, testo, "il CLA è stato scartato: non deve rientrare dalla finestra")

    # Le fusioni che **fabbrica una macchina**: GitHub quando si unisce una pull request, git
    # quando unisce due rami. Nessuno le scrive, quindi nessuno può certificarle, e `rebase
    # --signoff` — il rimedio che l'errore suggerisce — le riscriverebbe. Si riconoscono dal
    # soggetto e non dal numero di genitori, per il motivo spiegato nella prova qui sotto.
    # Il modello è stretto apposta: una commit umana che comincia per «Merge» (per esempio
    # «Merge due elenchi in uno») resta controllata come tutte le altre.
    FUSIONE_AUTOMATICA = re.compile(
        r"^Merge (pull request #\d+ |branch ['\"]|remote-tracking branch ['\"])")

    def test_the_history_is_already_signed(self):
        """La regola vale anche per chi la scrive: gli ultimi commit sono firmati.

        Le fusioni si saltano, e non è un dettaglio: una commit di fusione non la scrive nessuno,
        quindi non c'è nessuno che possa certificarla. Su una pull request GitHub ne fabbrica una
        sua, e **quella** è la commit che il lavoro si ritrova sotto il naso. Senza, la prova
        diventava rossa su *qualunque* pull request, di chiunque — e sarebbe toccato identico al
        primo contributore arrivato da fuori, accolto da una prova rossa che parla della nostra
        disciplina e che lui non poteva in nessun modo far diventare verde.

        **Perché il soggetto e non `--no-merges`.** L'8/11 la prima versione usava `--no-merges`:
        giusta sul Mac, inutile dove serviva. `tests.yml` fa un checkout **superficiale** (solo
        `dco` e `secrets` chiedono `fetch-depth: 0`), e in un clone di profondità 1 i genitori sono
        tagliati via: git non sa che quella commit è una fusione, quindi `--no-merges` non la
        esclude. La fusione di dependabot ha fatto rossa `main` su sei sistemi su sette, mentre in
        locale la prova era verde. Il soggetto invece sopravvive al taglio.

        La lezione, che vale oltre questa prova: **una correzione provata solo dove possiamo
        guardare non è una correzione provata.** Qui la differenza fra i due posti non era il
        sistema operativo, era la profondità del clone — una cosa che non si vede leggendo il
        codice della prova, ma solo leggendo il workflow che la esegue.

        Il controllo vero (`dco.yml`) non ci è mai cascato: clona intero e legge `base..testa`,
        cioè le commit del ramo, non la fusione finta.
        """
        import subprocess

        res = subprocess.run(["git", "log", "-15", "--no-merges", "--format=%H%n%B%n---FINE---"],
                             cwd=ROOT, capture_output=True, text=True)
        if res.returncode != 0:
            self.skipTest("non è un repository git (sorgente scompattata)")
        messaggi = [m for m in res.stdout.split("---FINE---") if m.strip()]
        self.assertTrue(messaggi)
        senza = []
        for m in messaggi:
            righe = m.strip().splitlines()
            soggetto = righe[1].strip() if len(righe) > 1 else ""
            if self.FUSIONE_AUTOMATICA.match(soggetto):
                continue
            if not re.search(r"^Signed-off-by: .+ <.+@.+>", m, re.M):
                senza.append(righe[0][:12])
        self.assertEqual(senza, [], f"commit senza Signed-off-by: {senza}")

    def test_an_automatic_merge_is_recognised_by_its_subject(self):
        """Il riconoscimento si prova qui, perché in CI non c'è una fusione da mostrargli.

        Il soggetto del 8/11 è quello vero che ha fatto rossa `main`.
        """
        for soggetto in ("Merge pull request #1 from marcopogo81/dependabot/github_actions/x",
                         "Merge branch 'main' into funzioni-rapide",
                         'Merge remote-tracking branch "origin/main"'):
            self.assertTrue(self.FUSIONE_AUTOMATICA.match(soggetto), soggetto)
        for soggetto in ("Merge due elenchi in uno, perché tenerli pari a mano non funziona",
                         "Mergiare non è una parola italiana",
                         "Il portachiavi non sapeva che abbiamo cambiato nome"):
            self.assertIsNone(self.FUSIONE_AUTOMATICA.match(soggetto),
                              f"una commit scritta da una persona resta controllata: {soggetto}")


class IlRilascioAspettaLeProveTest(unittest.TestCase):
    """O-03: non si pubblica un rilascio costruito su codice che non passa le prove (8/11).

    Prima «Tests» e «Release» partivano **sullo stesso tag, in parallelo**: se la suite falliva, la
    compilazione andava avanti e la bozza di Release nasceva lo stesso. E non si vedeva, perche'
    erano due corse diverse: quella del rilascio restava verde.

    Il legame e' una riga sola (`needs:`), ed e' proprio il tipo di riga che un riordino futuro
    scioglie senza accorgersene — un lavoro spostato, un `needs` riscritto, e il cancello si apre
    in silenzio. Qui si legge l'YAML invece del testo: cosi' la prova regge se qualcuno cambia
    l'ordine dei lavori o l'indentazione.
    """

    @staticmethod
    def carica(nome: str) -> dict:
        import yaml

        return yaml.safe_load((ROOT / ".github" / "workflows" / nome).read_text(encoding="utf-8"))

    @staticmethod
    def inneschi(d: dict):
        # PyYAML legge la chiave «on:» come il booleano True (YAML 1.1)
        return d[True] if True in d else d["on"]

    def test_the_builds_wait_for_the_tests(self):
        rilascio = self.carica("release.yml")
        lavori = rilascio["jobs"]
        prove = [n for n, c in lavori.items() if str(c.get("uses", "")).endswith("tests.yml")]
        self.assertTrue(prove, "il rilascio non richiama piu' le prove: il cancello e' aperto")
        for nome in ("macos", "windows"):
            needs = lavori[nome].get("needs")
            needs = [needs] if isinstance(needs, str) else (needs or [])
            self.assertTrue(set(needs) & set(prove),
                            f"il lavoro «{nome}» non aspetta le prove: compila comunque")

    def test_the_draft_still_waits_for_both_builds(self):
        needs = self.carica("release.yml")["jobs"]["rilascio"]["needs"]
        self.assertEqual(set(needs), {"macos", "windows"},
                         "la bozza di Release deve avere tutti e due i file, o nasce monca")

    def test_the_tests_are_callable_and_do_not_run_twice_on_a_tag(self):
        """`workflow_call` le rende richiamabili; il tag fuori dai suoi inneschi evita il doppione.

        Lasciandolo, su un tag la suite girerebbe due volte — una per conto suo e una dentro il
        rilascio — e i runner macOS costano dieci volte gli altri.
        """
        inneschi = self.inneschi(self.carica("tests.yml"))
        self.assertIn("workflow_call", inneschi, "senza questo, il rilascio non puo' richiamarle")
        tag = (inneschi.get("push") or {}).get("tags")
        self.assertFalse(tag, "il tag fa partire il rilascio, che le prove le chiama gia' lui")


class InstallerWindowsTest(unittest.TestCase):
    """W-06: le poche righe dell'installer che non si possono sbagliare due volte."""

    ISS = (ROOT / "packaging" / "mirto.iss").read_text(encoding="utf-8")
    RILASCIO = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    @classmethod
    def istruzioni(cls, sezione: str = "") -> list[str]:
        """Le righe che contano: senza commenti, e di una sola sezione se la si chiede."""
        righe, dentro = [], not sezione
        for riga in cls.ISS.splitlines():
            nuda = "" if riga.lstrip().startswith(";") else riga.strip()  # «;» a inizio riga = commento
            if riga.strip().startswith("["):
                dentro = riga.strip().lower() == sezione.lower() if sezione else True
            elif dentro and nuda:
                righe.append(nuda)
        return righe

    def test_the_appid_never_changes(self):
        self.assertIn("AppId={{725A6E59-1B0A-423B-9CED-5ADEB869AE55}", self.ISS,
                      "cambiare l'AppId installa la versione nuova accanto alla vecchia, non sopra")

    def test_the_version_comes_from_the_command_line(self):
        self.assertIn("AppVersion={#MirtoVersion}", self.ISS)
        scritte = [riga for riga in self.istruzioni() if re.search(r"\d+\.\d+\.\d+", riga)]
        self.assertEqual(scritte, ['#define MirtoVersion "0.0.0"'],
                         "la versione sta in mirto/__init__.py, non nell'installer")
        self.assertIn("/DMirtoVersion=", self.RILASCIO, "il workflow deve passare la versione a ISCC")

    def test_uninstalling_does_not_delete_the_recordings(self):
        cancellazioni = self.istruzioni("[UninstallDelete]")
        self.assertEqual(cancellazioni, ['Type: dirifempty; Name: "{app}"'],
                         "configurazione, eventi e registrazioni sono dell'utente: non si toccano")

    def test_no_administrator_is_asked_for(self):
        self.assertIn("PrivilegesRequired=lowest", self.ISS)

    def test_the_autostart_entry_goes_away_with_the_program(self):
        self.assertEqual(self.ISS.count("uninsdeletevalue"), 2,
                         "l'avvio automatico deve sparire quando si disinstalla")


class AppImpacchettataTest(unittest.TestCase):
    """L'app impacchettata deve **partire**. La prima volta che si e' aperta, non partiva.

    22/10, primo `.dmg` costruito e provato: si chiudeva prima di mostrare qualcosa, con
    «attempted relative import with no known parent package». Lo spec dava a PyInstaller
    `mirto/__main__.py` come script di partenza, e uno script PyInstaller lo esegue **come programma
    a se'**, chiamandolo `__main__`: quel file pero' e' il punto di ingresso di un pacchetto e usa
    importazioni relative, che fuori dal pacchetto non hanno piu' niente a cui riferirsi.

    Il difetto non si vedeva in nessun modo prima di aprire il pacchetto: `python -m mirto`
    funzionava benissimo, perche' li' il pacchetto c'e'. Qui si prova la cosa vera - si esegue
    davvero il file da cui parte l'app - invece di guardarla.
    """

    ROOT = Path(__file__).resolve().parents[1]

    def avvia(self, script: str) -> subprocess.CompletedProcess:
        """Esegue uno script come lo eseguirebbe PyInstaller: da fuori, non come modulo."""
        ambiente = dict(os.environ, PYTHONPATH=str(self.ROOT))
        return subprocess.run([sys.executable, str(self.ROOT / script), "--help"],
                              capture_output=True, text=True, timeout=120, env=ambiente, check=False)

    def test_the_launcher_actually_starts(self):
        esito = self.avvia("packaging/avvia.py")
        self.assertEqual(esito.returncode, 0, esito.stderr[-800:])
        self.assertIn("usage: mirto", esito.stdout)

    def test_the_package_entry_point_cannot_be_the_script(self):
        """E questo e' il motivo per cui avvia.py esiste: cosi' com'e', non si puo' eseguire da fuori."""
        esito = self.avvia("mirto/__main__.py")
        self.assertNotEqual(esito.returncode, 0)
        self.assertIn("relative import", esito.stderr,
                      "se un giorno partisse anche cosi', questa prova va riletta, non cancellata")

    def test_the_spec_starts_from_the_launcher(self):
        spec = (self.ROOT / "packaging" / "mirto.spec").read_text(encoding="utf-8")
        self.assertIn('["avvia.py"]', spec)
        self.assertNotIn('"../mirto/__main__.py"', spec,
                         "e' il file che non si puo' usare come script: ci siamo gia' cascati")


class ReleaseWorkflowTest(unittest.TestCase):
    """La Release non deve poter uscire con metà dei file."""

    def test_the_draft_waits_for_both_builds(self):
        import yaml  # noqa: PLC0415

        try:
            testo = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
            lavori = yaml.safe_load(testo)["jobs"]
        except ImportError:  # pragma: no cover - pyyaml non è una dipendenza del programma
            self.skipTest("pyyaml non disponibile")
        self.assertEqual(sorted(lavori["rilascio"]["needs"]), ["macos", "windows"])
        passi = " ".join(str(p) for p in lavori["rilascio"]["steps"])
        self.assertIn("--draft", passi, "la Release si pubblica a mano, dopo aver provato i file")


class ProveRosseSiLeggonoTest(unittest.TestCase):
    """Quando la CI è rossa, il motivo deve arrivare anche nelle annotazioni.

    Il registro completo di una esecuzione sta su un archivio che da qui non si scarica sempre: il
    20/10 la CI è rimasta rossa su Windows mezza giornata perché il motivo era leggibile solo lì.
    Le annotazioni invece si leggono dalla pagina del controllo e dall'API.
    """

    def test_a_failure_becomes_an_annotation(self):
        class FintoEsito:
            failures = [("test_qualcosa (tests.test_x.Caso.test_qualcosa)",
                         "Traceback...\nAssertionError: 384 != 420\n")]
            errors = []

        righe = []
        tempi.print = righe.append          # noqa: A001  (solo in questa prova)
        try:
            tempi.racconta_i_guai(FintoEsito())
        finally:
            del tempi.print
        annotazioni = [r for r in righe if str(r).startswith("::error")]
        self.assertEqual(len(annotazioni), 1)
        self.assertIn("test_qualcosa", annotazioni[0], "il nome della prova sta nel titolo")
        self.assertIn("384 != 420", annotazioni[0], "e il motivo nel corpo")
        self.assertNotIn("\n", annotazioni[0], "un comando di GitHub sta su una riga sola")

    def test_what_would_break_the_command_is_protected(self):
        self.assertEqual(tempi.annotazione("100% a capo\nqui"), "100%25 a capo%0Aqui")

    def test_too_many_failures_are_summed_up(self):
        class FintoEsito:
            failures = [(f"test_{i}", "AssertionError: no\n") for i in range(11)]
            errors = []

        righe = []
        tempi.print = righe.append          # noqa: A001
        try:
            tempi.racconta_i_guai(FintoEsito(), quante=3)
        finally:
            del tempi.print
        annotazioni = [r for r in righe if str(r).startswith("::error")]
        self.assertEqual(len(annotazioni), 4, "tre per bene, più il riassunto delle altre")
        self.assertIn("altre 8 prove rosse", annotazioni[-1])


class UnaChiamataRimandataDiceACheCosaAppartieneTest(unittest.TestCase):
    """`QTimer.singleShot(2500, lambda: self.etichetta.setText(""))` su un oggetto gia' morto (8/11).

    Nella forma a due argomenti il timer non appartiene a nessuno: scade comunque, e la lambda tocca
    un oggetto che nel frattempo Qt puo' aver distrutto — «Internal C++ object already deleted».
    Marco l'ha visto tre volte in un minuto salvando le impostazioni: la scheda si ricostruisce,
    l'etichetta «✓ Salvato» muore, e due secondi e mezzo dopo qualcuno le scrive dentro.

    Qt la forma giusta ce l'ha: `singleShot(ms, oggetto, funzione)` non esegue niente se
    l'**oggetto** e' sparito. Quindi una chiamata rimandata deve dire a che cosa appartiene, e il
    controllo non e' «non usare lambda» ma «di' di chi e' il tempo che stai aspettando».

    Quando e' uscito il difetto i casi erano quattro: uno schiantava gia', gli altri tre erano lo
    stesso difetto non ancora manifestato.
    """

    def test_a_deferred_lambda_names_the_object_it_belongs_to(self):
        import ast

        guai = []
        for f in sorted((ROOT / "mirto").rglob("*.py")):
            if "__pycache__" in str(f):
                continue
            for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
                if (isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "singleShot"
                        and len(n.args) == 2 and isinstance(n.args[1], ast.Lambda)):
                    guai.append(f"{f.relative_to(ROOT)}:{n.lineno}")
        self.assertEqual(guai, [], "singleShot(ms, lambda) non appartiene a nessuno: se l'oggetto che "
                                   "la lambda tocca muore prima, il programma si schianta. Usa "
                                   "singleShot(ms, oggetto, lambda):\n  " + "\n  ".join(guai))


class UnFoglioDiStileConLeGraffeDoppieTest(unittest.TestCase):
    """`"border-radius:10px; }}"` senza la `f` davanti: Qt scarta tutto il foglio (8/11).

    Le graffe si raddoppiano **solo** dentro un f-string, dove servono a dire «questa e' una graffa
    vera, non un campo». In una stringa normale `}}` resta `}}`, il CSS e' invalido e Qt lo butta —
    scrivendo «Could not parse stylesheet», che e' un avviso su stdout e non un errore: il pannello
    semplicemente nasce senza fondo e senza bordo, e nessuno sa perche'.

    Il caso vero era una riga spezzata in due: la prima meta' f-string, la seconda no. L'occhio
    legge le due righe come una sola stringa, il parser no.
    """

    def test_a_plain_string_in_a_stylesheet_has_no_doubled_braces(self):
        import ast

        guai = []
        for f in sorted((ROOT / "mirto").rglob("*.py")):
            if "__pycache__" in str(f):
                continue
            for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
                if not (isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "setStyleSheet"):
                    continue
                # Un f-string e' un JoinedStr e le sue graffe doppie il parser le ha gia' risolte:
                # qui restano solo le stringhe normali, dove «}}» non puo' che essere una svista.
                for pezzo in ast.walk(n):
                    if (isinstance(pezzo, ast.Constant) and isinstance(pezzo.value, str)
                            and ("}}" in pezzo.value or "{{" in pezzo.value)):
                        guai.append(f"{f.relative_to(ROOT)}:{n.lineno}: {pezzo.value.strip()[:60]}")
        self.assertEqual(guai, [], "graffe doppie in una stringa che non e' un f-string: Qt scarta "
                                   "tutto il foglio di stile senza dirlo come un errore:\n  "
                                   + "\n  ".join(guai))


class UnAvvisoCheNonChiedeNienteNonEUnAvvisoTest(unittest.TestCase):
    """L'avviso di zeroconf quando un indirizzo di rete sparisce (8/11).

    `Error with socket N (('169.254.x.y', 5353)): [Errno 49] Can't assign requested address`, col
    traceback appresso, a ogni cambio di interfaccia: un cavo staccato, il Wi-Fi che si riattacca,
    l'indirizzo link-local del cavo diretto al nodo. Zeroconf la gestisce da se' e poi la racconta.

    Si toglie per la stessa ragione delle righe dell'anello (22/10) e dell'uscita audio di stamattina:
    un avviso che compare da solo, non chiede niente a nessuno e si ripete, insegna a non leggere gli
    avvisi — e il prossimo, quello vero, passa inosservato in mezzo agli altri.
    """

    def setUp(self):
        import logging

        from mirto.logging_setup import _SenzaIndirizzoSparito

        self.filtro = _SenzaIndirizzoSparito()
        self.logging = logging
        logging.getLogger().setLevel(logging.INFO)

    def riga(self, nome, messaggio, livello=None):
        return self.logging.LogRecord(nome, livello or self.logging.WARNING, "", 1, messaggio, None, None)

    def test_the_address_that_went_away_is_not_worth_a_warning(self):
        r = self.riga("zeroconf", "Error with socket 31: [Errno 49] Can't assign requested address")
        self.assertFalse(self.filtro.filter(r), "a livello info non deve comparire")
        self.assertEqual(r.levelname, "DEBUG", "resta per chi la va a cercare")

    def test_everything_else_zeroconf_says_still_gets_through(self):
        self.assertTrue(self.filtro.filter(self.riga("zeroconf", "qualcosa che non abbiamo previsto")))

    def test_our_own_logs_are_never_touched(self):
        """Il filtro guarda **chi parla**, non solo cosa dice: un nostro messaggio uguale resta."""
        self.assertTrue(self.filtro.filter(self.riga("mirto.core", "Can't assign requested address")))

    def test_an_error_is_never_hidden(self):
        """Un ERROR di zeroconf non si tocca: il filtro vale per gli avvisi che si risolvono da se'."""
        r = self.riga("zeroconf", "Errno 49", self.logging.ERROR)
        self.assertTrue(self.filtro.filter(r))


class UnMetodoSenzaSelfVuoleStaticmethodTest(unittest.TestCase):
    """`@staticmethod` perso nel rinominio: il programma non partiva piu' (8/11).

    `MainWindow.tray_pixmap` era `@staticmethod`; il rinominio del 7/11 ha perso il decoratore e ha
    lasciato la firma `def tray_pixmap(color, size=22, …)`. Python non protesta: senza decoratore
    quella resta una funzione, e `self.tray_pixmap(colore)` infila **la finestra** dentro `color` e
    il colore dentro `size`. Il pixmap nasce nullo e si muore su `QColor(<la finestra>)` — cioe' il
    programma non si apre affatto.

    La prova che c'era non poteva vederlo, ed e' la parte da ricordare:
    `MainWindow.tray_pixmap("#2FB383", 22, pieno=True)` chiama **dalla classe**, cioe' nel modo in
    cui il programma non la chiama mai. Una funzione provata in un modo diverso da come la usa chi
    la usa e' una funzione non provata: verde non voleva dire funzionante.

    Questa guarda il codice invece di eseguirlo, quindi parla anche dal container dove PySide6 non
    c'e' — che e' esattamente il posto da cui il difetto e' uscito.
    """

    def test_every_method_either_takes_self_or_says_it_does_not(self):
        import ast

        salvacondotto = {"staticmethod", "classmethod", "property", "setter", "getter", "deleter"}
        guai = []
        for f in sorted(ROOT.rglob("*.py")):
            testo = str(f)
            if "__pycache__" in testo or "/.venv/" in testo or "/privato/" in testo:
                continue
            try:
                albero = ast.parse(f.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for classe in (n for n in ast.walk(albero) if isinstance(n, ast.ClassDef)):
                for m in classe.body:
                    if not isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    decoratori = {d.id if isinstance(d, ast.Name) else getattr(d, "attr", "")
                                  for d in m.decorator_list}
                    if decoratori & salvacondotto:
                        continue
                    args = m.args.posonlyargs + m.args.args
                    if not args or args[0].arg not in ("self", "cls"):
                        primo = args[0].arg if args else "niente"
                        guai.append(f"{f.relative_to(ROOT)}:{m.lineno}: "
                                    f"{classe.name}.{m.name} comincia con «{primo}»")
        self.assertEqual(guai, [], "un metodo che non prende self vuole @staticmethod, altrimenti "
                                   "chiamandolo sull'oggetto il primo argomento diventa l'oggetto:\n  "
                                   + "\n  ".join(guai))


class UnWorkflowNonAspettaUnRamoCheNonCeTest(unittest.TestCase):
    """`branches: [main, lancio]` dopo il passaggio al pubblico (8/11).

    `lancio` era il ramo del repository privato. Qui non esiste e non puo' esistere: il blocco in
    `.git/hooks/pre-push` verso `mirto` accetta solo `main` e i tag `v*`, perche' gli altri rami
    hanno dentro la storia privata. Un innesco su un ramo che non c'e' non rompe niente — e' peggio:
    non fa niente, e chi legge il file crede che da qualche parte ci sia un ramo `lancio`.
    """

    def test_no_workflow_waits_for_a_branch_that_cannot_exist(self):
        atteso = {"main"}
        guai = []
        for f in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
            for i, riga in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                m = re.match(r"\s*branches:\s*\[(?P<rami>[^\]]*)\]", riga)
                if not m:
                    continue
                rami = {r.strip().strip("'\"") for r in m.group("rami").split(",") if r.strip()}
                if rami - atteso:
                    guai.append(f"{f.name}:{i}: {sorted(rami - atteso)}")
        self.assertEqual(guai, [], "rami che sul repository pubblico non esistono (il pre-push "
                                   "lascia passare solo main e i tag v*):\n  " + "\n  ".join(guai))


class WorkflowsArePinnedTest(unittest.TestCase):
    """Dopo il fissaggio, nessun workflow deve tornare a un tag che si può spostare."""

    def test_every_action_is_pinned_to_a_sha(self):
        mobili = []
        for percorso in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
            for numero, riga in enumerate(percorso.read_text(encoding="utf-8").splitlines(), 1):
                trovato = fissa.USA.match(riga)
                if trovato and not fissa.SHA.match(trovato.group("rif")):
                    mobili.append(f"{percorso.name}:{numero} {trovato.group('repo')}@{trovato.group('rif')}")
        self.assertEqual(mobili, [], "action non fissate: lancia  python3 tools/fissa-action.py --scrivi")


if __name__ == "__main__":
    unittest.main()


class FixtureDelNodoTest(unittest.TestCase):
    """Nessuna prova scrive a mano la versione dell'API di un nodo «in ordine».

    Il 22/10, alzando l'API del nodo a 1.6, sono diventate rosse **undici** prove che parlavano di
    tutt'altro: temperatura, Wi-Fi debole, registrazione sulla microSD, la finestra di stato. Nessuna
    era rotta — i loro nodi finti erano indietro, e la diagnosi aveva ragione a dirlo. Il guaio è che
    il rosso compariva lontano dalla causa, e due di quelle prove girano solo dove PySide6 è
    installato, cioè non dove le scrivo: le ho scoperte dal Mac, dopo il commit.

    Un nodo «aggiornato» in una fixture si scrive ``API_ATTESA``, non ``1.5``. Resta lecito passarla
    come argomento (``dict(self.INFO, versione_api=1.3)``): quello è il nodo vecchio di proposito, ed è
    il punto della prova.
    """

    def test_no_test_hardcodes_the_api_version(self):
        colpevoli = []
        for f in sorted(Path(__file__).resolve().parent.glob("test_*.py")):
            for numero, riga in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r'"versione_api":\s*[0-9]', riga):
                    colpevoli.append(f"{f.name}:{numero}")
        self.assertEqual(colpevoli, [], "API scritta a mano in una fixture: usa API_ATTESA")


class TettoDellaSuiteTest(unittest.TestCase):
    """La suite ha un tetto di tempo, e un blocco lascia la pila di ogni thread.

    Il 22/10 un lavoro della CI è rimasto appeso **due ore e dodici minuti**, ha consumato più di
    cento minuti di Actions e ha finito il credito del mese: i lavori successivi non sono nemmeno
    partiti, e di quel blocco non è rimasto **niente** da cui ripartire. Il tetto non impedisce il
    blocco — lo rende leggibile.

    Questa prova esiste perché una riga in `tests/__init__.py` è il genere di cosa che sparisce in un
    riordino senza che nessuno se ne accorga, e si riscopre la prossima volta che si bruciano due ore.
    """

    INIT = Path(__file__).resolve().parent / "__init__.py"

    def test_the_suite_cannot_hang_forever(self):
        testo = self.INIT.read_text(encoding="utf-8")
        self.assertIn("dump_traceback_later", testo, "senza tetto un blocco gira fino al limite di GitHub")
        self.assertIn("exit=True", testo, "stampare la pila e restare appesi non servirebbe a niente")


class QualeCodiceStaGirandoTest(unittest.TestCase):
    """«Quello che hai davanti e' il codice di adesso?» — la domanda che il 23/10 e' costata mezz'ora.

    Marco provava una finestra avviata tre ore prima e mi diceva che c'era scritta una cosa; io
    leggevo nel codice che ce n'era un'altra. Tutt'e due avevamo ragione, e nessuno dei due poteva
    accorgersene guardando: sul disco il file era giusto, in memoria no.

    Il commit non basta a rispondere — letto adesso dice cosa c'e' **sul disco**, che e' proprio la
    meta' sbagliata. Servono due numeri: quando il programma ha letto i suoi file, e quanto e' recente
    il piu' nuovo adesso.
    """

    def test_the_line_says_version_and_when_it_started(self):
        from mirto import __version__
        from mirto.versione import riga

        testo = riga()
        self.assertIn(__version__, testo)
        self.assertIn("avviato", testo, "l'ora di avvio e' meta' della risposta")

    def test_nothing_to_say_when_the_disk_is_not_newer(self):
        from mirto.versione import CARICATO, avviso_codice_vecchio, codice_piu_nuovo_sul_disco

        self.assertEqual(codice_piu_nuovo_sul_disco(CARICATO), 0.0)
        self.assertEqual(avviso_codice_vecchio(CARICATO), "", "un avviso che compare sempre non si legge piu'")
        self.assertEqual(avviso_codice_vecchio(CARICATO + 0.5), "", "mezzo secondo e' il salvataggio, non una versione")

    def test_it_speaks_up_when_the_disk_has_moved_on(self):
        from mirto.versione import CARICATO, avviso_codice_vecchio, codice_piu_nuovo_sul_disco

        self.assertGreater(codice_piu_nuovo_sul_disco(CARICATO + 600), 0)
        avviso = avviso_codice_vecchio(CARICATO + 600)
        self.assertIn("Chiudi e riapri", avviso, "deve dire cosa fare, non solo che c'e' un problema")

    def test_a_frozen_build_does_not_cry_wolf(self):
        """In un eseguibile congelato i .py non ci sono: senza dati non si inventa un avviso."""
        from mirto.versione import codice_piu_nuovo_sul_disco

        self.assertEqual(codice_piu_nuovo_sul_disco(0.0), 0.0)

    def test_the_cache_folder_is_not_the_code(self):
        """Un .pyc piu' nuovo del .py e' normale: lo scrive Python stesso leggendo il sorgente."""
        import tempfile
        from pathlib import Path

        from mirto.versione import _ultima_modifica

        with tempfile.TemporaryDirectory() as d:
            radice = Path(d)
            (radice / "uno.py").write_text("x = 1", encoding="utf-8")
            os.utime(radice / "uno.py", (1_000_000, 1_000_000))
            (radice / "__pycache__").mkdir()
            (radice / "__pycache__" / "uno.pyc").write_bytes(b"\0")
            os.utime(radice / "__pycache__" / "uno.pyc", (2_000_000, 2_000_000))
            self.assertEqual(_ultima_modifica(radice), 1_000_000)


class SintassiDellaVersionePiuVecchiaTest(unittest.TestCase):
    """Tutto il codice deve essere leggibile dalla **piu' vecchia** Python che dichiariamo (3.11).

    `pyproject` dice `requires-python = ">=3.11"` e la CI prova davvero quella versione; qui dentro,
    pero', si sviluppa su una Python piu' nuova, e certe cose che la 3.12 accetta sulla 3.11 sono un
    **errore di sintassi** — per esempio un backslash dentro l'espressione di una f-string. Non e'
    un'ipotesi: il 23/10 ne ho scritto uno in una prova e l'ho visto solo rileggendo, non eseguendo.
    Un errore cosi' non si manifesta con un test rosso: la CI non riesce nemmeno a importare il file.
    """

    MINIMA = (3, 11)

    def test_every_source_file_parses_on_the_oldest_python_we_promise(self):
        import ast
        from pathlib import Path

        radice = Path(__file__).resolve().parents[1]
        guai = []
        for cartella in ("mirto", "node", "tests", "tools"):
            for f in sorted((radice / cartella).rglob("*.py")):
                if "__pycache__" in str(f):
                    continue
                try:
                    ast.parse(f.read_text(encoding="utf-8"), filename=str(f),
                              feature_version=self.MINIMA)
                except SyntaxError as exc:
                    guai.append(f"{f.relative_to(radice)}:{exc.lineno} {exc.msg}")
        self.assertEqual(guai, [], "non si leggono su Python {}.{}: ".format(*self.MINIMA) + "; ".join(guai))

    def test_the_promise_is_the_one_in_pyproject(self):
        """Se un giorno alziamo il minimo, questo test va aggiornato insieme — e lo dice lui."""
        from pathlib import Path

        testo = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('requires-python = ">={}.{}"'.format(*self.MINIMA), testo)


class UnImportDentroUnaFunzioneNonValePerLeAltreTest(unittest.TestCase):
    """Un nome importato **dentro** una funzione non esiste nelle altre funzioni dello stesso file.

    Sembra ovvio e costa caro lo stesso: il 23/10 ho aggiunto un metodo a `PassoDialog` che usava
    `suoni_del_nodo`, importato dentro `__init__`. Qui dentro non si vede — senza PySide6 i file
    dell'interfaccia non si importano nemmeno — quindi il `NameError` l'ha trovato Marco lanciando le
    prove delle finestre sul Mac, cioe' il piu' tardi possibile.

    Questo controllo legge l'albero sintattico invece di eseguire: per ogni nome importato dentro una
    funzione, controlla che le **altre** funzioni dello stesso file non lo usino senza averlo a loro
    volta. Non e' un analizzatore di visibilita' completo (non ne serve uno): copre esattamente lo
    schema che ha morso, e gira dove il codice dell'interfaccia non si puo' eseguire.
    """

    def test_gui_modules_do_not_borrow_names_from_another_function(self):
        import ast
        from pathlib import Path

        def legami(nodo) -> set:
            """I nomi che **questa** funzione lega: import, assegnamenti, argomenti, nomi annidati."""
            fuori = {a.arg for a in ast.walk(nodo) if isinstance(a, ast.arg)}
            for n in _dentro(nodo):
                if isinstance(n, (ast.Import, ast.ImportFrom)):
                    fuori |= {(a.asname or a.name).split(".")[0] for a in n.names}
                elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
                    fuori.add(n.id)
                elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    fuori.add(n.name)
                elif isinstance(n, ast.ExceptHandler) and n.name:
                    fuori.add(n.name)
                elif isinstance(n, ast.alias):
                    fuori.add((n.asname or n.name).split(".")[0])
            return fuori

        def _dentro(fn):
            """I nodi di questa funzione **senza** entrare in quelle annidate: hanno una visibilita' loro."""
            da_vedere = list(ast.iter_child_nodes(fn))
            while da_vedere:
                n = da_vedere.pop()
                yield n
                if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    da_vedere.extend(ast.iter_child_nodes(n))

        def controlla(fn, ereditati: set, globali: set, prestati: set, dove: str, guai: list) -> None:
            miei = ereditati | legami(fn)
            for n in _dentro(fn):
                if (isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
                        and n.id in prestati and n.id not in miei and n.id not in globali):
                    guai.append(f"{dove}:{n.lineno} «{n.id}» in {fn.name}() "
                                f"e' importato solo dentro un'altra funzione")
            for n in ast.iter_child_nodes(fn):
                for annidata in ([n] if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) else []):
                    controlla(annidata, miei, globali, prestati, dove, guai)
                if isinstance(n, (ast.ClassDef, ast.If, ast.Try, ast.With, ast.For, ast.While)):
                    for sotto in ast.walk(n):
                        if isinstance(sotto, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            controlla(sotto, miei, globali, prestati, dove, guai)

        radice = Path(__file__).resolve().parents[1]
        guai: list = []
        for f in sorted((radice / "mirto").rglob("*.py")):
            if "__pycache__" in str(f):
                continue
            albero = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
            globali: set = set()
            for nodo in albero.body:
                if isinstance(nodo, (ast.Import, ast.ImportFrom)):
                    globali |= {(a.asname or a.name).split(".")[0] for a in nodo.names}
                elif isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    globali.add(nodo.name)
                elif isinstance(nodo, ast.Assign):
                    globali |= {x.id for x in nodo.targets if isinstance(x, ast.Name)}
                elif isinstance(nodo, ast.AnnAssign) and isinstance(nodo.target, ast.Name):
                    globali.add(nodo.target.id)
            prestati: set = set()
            for fn in ast.walk(albero):
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for n in _dentro(fn):
                        if isinstance(n, (ast.Import, ast.ImportFrom)):
                            prestati |= {(a.asname or a.name).split(".")[0] for a in n.names}
            for cima in ast.walk(albero):
                if isinstance(cima, ast.ClassDef):
                    for n in cima.body:
                        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            controlla(n, set(), globali, prestati, str(f.relative_to(radice)), guai)
            for n in albero.body:
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    controlla(n, set(), globali, prestati, str(f.relative_to(radice)), guai)
        self.assertEqual(guai, [], "nomi presi in prestito da un'altra funzione:\n  " + "\n  ".join(guai))


class UnImportInFondoRendeLocaleTuttaLaFunzioneTest(unittest.TestCase):
    """Un `import` dentro una funzione rende quel nome **locale per tutta la funzione**, anche prima.

    Il 25/10 ho aggiunto un nastro in cima a `_tab_nodes_actions` usando `QHBoxLayout`, che quel file
    importa in testa. Trenta righe piu' sotto, da mesi, c'era un `from PySide6.QtWidgets import
    QHBoxLayout` locale — ridondante e innocuo finche' nessuno usava quel nome **prima** di quella
    riga. Da quel momento Python ha considerato `QHBoxLayout` una variabile locale per tutta la
    funzione, e la riga nuova e' diventata `UnboundLocalError`: non «nome sbagliato», ma «nome giusto
    che a quel punto non esiste ancora».

    Il costo e' stato 19 prove di finestra rosse in un colpo — tutte quelle che aprono le
    Impostazioni, perche' la scheda Nodi si costruisce all'apertura — cioe' **un giro di Marco**, per
    una cosa che si vedeva da qui. L'avevo anche vista, quella riga, e l'avevo giudicata innocua.

    Il fratello di questo controllo (sopra) guarda un nome importato in un'altra funzione; questo
    guarda lo stesso nome **nella stessa funzione, prima dell'import**. Insieme coprono i due modi in
    cui un import locale morde.
    """

    @staticmethod
    def _nella_funzione(fn):
        """I nodi di questa funzione, senza entrare in quelle annidate: hanno una visibilita' loro."""
        import ast

        da_vedere = list(ast.iter_child_nodes(fn))
        while da_vedere:
            n = da_vedere.pop()
            yield n
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                da_vedere.extend(ast.iter_child_nodes(n))

    def _guai(self, albero, dove: str) -> list:
        import ast

        guai = []
        for fn in ast.walk(albero):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            importati: dict = {}
            for n in self._nella_funzione(fn):
                if isinstance(n, (ast.Import, ast.ImportFrom)):
                    for a in n.names:
                        nome = (a.asname or a.name).split(".")[0]
                        importati[nome] = min(importati.get(nome, n.lineno), n.lineno)
            if not importati:
                continue
            for n in self._nella_funzione(fn):
                if (isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
                        and n.id in importati and n.lineno < importati[n.id]):
                    guai.append(f"{dove}:{n.lineno} «{n.id}» si usa qui, ma e' importato dentro "
                                f"{fn.name}() alla riga {importati[n.id]}: a questo punto non esiste")
        return guai

    def test_the_guard_sees_the_shape_that_bit(self):
        """Prima di fidarsi, si controlla che non sia diventato cieco."""
        import ast
        import textwrap

        rotto = textwrap.dedent("""
            def costruisci(self):
                riga = QHBoxLayout()
                from PySide6.QtWidgets import QHBoxLayout
                altra = QHBoxLayout()
        """)
        self.assertTrue(self._guai(ast.parse(rotto), "finto.py"), "il guardiano non vede piu' niente")
        sano = textwrap.dedent("""
            def costruisci(self):
                from PySide6.QtWidgets import QHBoxLayout
                riga = QHBoxLayout()
        """)
        self.assertEqual(self._guai(ast.parse(sano), "finto.py"), [],
                         "un import prima dell'uso e' la cosa normale e non si segnala")

    def test_no_name_is_used_before_its_local_import(self):
        import ast
        from pathlib import Path

        radice = Path(__file__).resolve().parents[1]
        guai: list = []
        for cartella in ("mirto", "node", "tools"):
            for f in sorted((radice / cartella).rglob("*.py")):
                if "__pycache__" in str(f):
                    continue
                albero = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
                guai += self._guai(albero, str(f.relative_to(radice)))
        self.assertEqual(guai, [], "nomi usati prima del loro import locale:\n  " + "\n  ".join(guai))


class QuelloCheSiChiedeAlCoreDeveEsistereTest(unittest.TestCase):
    """`self.core.qualcosa` deve essere qualcosa che `MirtoCore` ha davvero.

    Il 25/10, scrivendo la vista di flotta, ho chiesto al core `self.core.events`: il registro degli
    eventi si chiama `store`. La suite e' rimasta **verde** — quella riga sta dentro un `try`, e il
    ripiego era un dizionario vuoto. Quindi niente errore, niente rosso: soltanto una colonna che
    avrebbe detto «mai» per sempre, su tutti i nodi, senza che nessuno potesse sospettarne il
    motivo. Un crash si vede; un valore sbagliato che non si lamenta no.

    `MirtoCore` e' il posto da cui l'interfaccia prende tutto — camere, azioni, notifiche,
    registrazione, portachiavi — quindi un nome sbagliato li' dentro e' facile da fare e difficile da
    vedere. Questo controllo legge la classe e confronta: niente di furbo, ma copre la superficie
    piu' usata del programma.
    """

    @staticmethod
    def _cosa_ha_il_core(albero) -> set:
        import ast

        nomi: set = set()
        for cls in ast.walk(albero):
            if not (isinstance(cls, ast.ClassDef) and cls.name == "MirtoCore"):
                continue
            for n in ast.walk(cls):
                if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) \
                        and n.value.id == "self" and isinstance(n.ctx, ast.Store):
                    nomi.add(n.attr)
                elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n is not cls:
                    nomi.add(n.name)
                elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Attribute):
                    nomi.add(n.target.attr)
            for n in cls.body:   # nomi di classe, non di istanza
                if isinstance(n, ast.Assign):
                    nomi |= {x.id for x in n.targets if isinstance(x, ast.Name)}
        return nomi

    def test_the_guard_sees_a_name_that_is_not_there(self):
        """Prima di fidarsi, si controlla che non sia diventato cieco."""
        import ast
        import textwrap

        finto = ast.parse(textwrap.dedent("""
            class MirtoCore:
                def __init__(self):
                    self.store = 1
                def avvia(self):
                    pass
        """))
        ha = self._cosa_ha_il_core(finto)
        self.assertIn("store", ha)
        self.assertIn("avvia", ha)
        self.assertNotIn("events", ha, "il guardiano deve poter dire di no")

    def test_nothing_asks_the_core_for_something_it_has_not(self):
        import ast
        from pathlib import Path

        radice = Path(__file__).resolve().parents[1]
        ha = self._cosa_ha_il_core(ast.parse((radice / "mirto" / "core.py").read_text(encoding="utf-8")))
        self.assertIn("store", ha, "la lettura della classe e' andata storta: non si prova niente")
        guai = []
        for f in sorted((radice / "mirto").rglob("*.py")):
            if "__pycache__" in str(f):
                continue
            albero = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
            for n in ast.walk(albero):
                # `self.core.qualcosa`: l'unica forma in cui si e' sicuri di chi sia «core»
                if (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Attribute)
                        and n.value.attr == "core" and isinstance(n.value.value, ast.Name)
                        and n.value.value.id == "self" and n.attr not in ha
                        and not n.attr.startswith("__")):
                    guai.append(f"{f.relative_to(radice)}:{n.lineno} self.core.{n.attr} "
                                f"non esiste in MirtoCore")
        self.assertEqual(guai, [], "si chiede al core qualcosa che non ha:\n  " + "\n  ".join(guai))


class UnMetodoChiamatoDeveEsistereTest(unittest.TestCase):
    """`self._qualcosa()` deve esistere **nella classe che lo chiama**, o e' un errore che si vede
    solo aprendo la finestra.

    Il 24/10, inserendo una scheda nelle impostazioni, ho cancellato per sbaglio la riga
    `def _tab_recording(self):` — il corpo di quel metodo e' finito dentro il mio, e il metodo e'
    sparito. La suite e' rimasta verde: senza PySide6 quella finestra non si costruisce. L'ha visto
    Marco al primo clic su «Impostazioni», che e' il posto peggiore dove scoprirlo.

    Lo stesso giorno, piu' tardi, un secondo errore identico e' passato **da questo controllo**:
    `_nota_microfoni` l'avevo messo in `SettingsDialog`, ma lo chiama `_update`, che e' di
    `ActionDialog`. Due classi diverse nello stesso file, e il controllo guardava il file. Ora guarda
    la classe, e risale alle classi madri: quelle nostre si cercano in tutto il programma, quelle di
    Qt non hanno nomi con il trattino basso davanti, quindi non coprono niente.
    """

    # Metodi che una finestra eredita da Qt e che si passano **senza chiamarli**, come slot di un
    # pulsante: `bottoni.rejected.connect(self.reject)`. Non sono nostri e non si trovano nel file.
    SLOT_DI_QT = {"accept", "reject", "close", "show", "hide", "raise_", "update", "repaint",
                  "deleteLater", "setFocus", "showNormal", "activateWindow", "sizeHint"}

    @staticmethod
    def _membri(classe):
        import ast

        dentro = set()
        for m in classe.body:
            if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                dentro.add(m.name)
        for n in ast.walk(classe):
            if (isinstance(n, ast.Attribute) and isinstance(n.ctx, (ast.Store, ast.Del))
                    and isinstance(n.value, ast.Name) and n.value.id == "self"):
                dentro.add(n.attr)
            elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
                dentro.add(n.target.id)       # LARGO: int = 150
            elif isinstance(n, ast.Assign):
                # Le costanti del corpo della classe, anche scritte in fila: `LARGO, ALTO = 150, 92`.
                da_vedere = list(n.targets)
                while da_vedere:
                    bersaglio = da_vedere.pop()
                    if isinstance(bersaglio, ast.Name):
                        dentro.add(bersaglio.id)
                    elif isinstance(bersaglio, (ast.Tuple, ast.List)):
                        da_vedere.extend(bersaglio.elts)
            elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                # setattr(self, "_x", …) e simili: non si inseguono, ma si prende nota
                for a in n.args:
                    if isinstance(a, ast.Constant) and isinstance(a.value, str):
                        dentro.add(a.value)
            elif (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id in ("getattr", "setattr", "hasattr")):
                for a in n.args:
                    if isinstance(a, ast.Constant) and isinstance(a.value, str):
                        dentro.add(a.value)
        return dentro

    def test_every_private_method_called_on_self_exists(self):
        import ast
        from pathlib import Path

        radice = Path(__file__).resolve().parents[1]
        file = [f for f in sorted((radice / "mirto").rglob("*.py")) if "__pycache__" not in str(f)]
        alberi = {f: ast.parse(f.read_text(encoding="utf-8"), filename=str(f)) for f in file}

        # Tutte le classi del programma per nome: le madri si cercano qui, anche in un altro file.
        classi = {}
        for albero in alberi.values():
            for n in ast.walk(albero):
                if isinstance(n, ast.ClassDef):
                    classi.setdefault(n.name, []).append(n)

        def madri(classe, visti):
            """I nomi che la classe eredita, piu' un sospetto: «c'e' una madre che non so leggere»."""
            avuti, cieca = set(), False
            for b in classe.bases:
                nome = b.id if isinstance(b, ast.Name) else (b.attr if isinstance(b, ast.Attribute) else "")
                if not nome or nome in visti:
                    cieca = cieca or not nome
                    continue
                if nome in classi:
                    for altra in classi[nome]:
                        avuti |= self._membri(altra)
                        su, piu_cieca = madri(altra, visti | {nome})
                        avuti |= su
                        cieca = cieca or piu_cieca
                elif not nome.startswith("Q"):
                    # Non e' una classe nostra e non e' di Qt: non so cosa porta in dote, lascio stare.
                    cieca = True
            return avuti, cieca

        guai = []
        for f, albero in alberi.items():
            # Di chi e' «self» in un punto qualsiasi del file: della prima funzione che sale e che ha
            # «self» come primo parametro, e quindi della classe che contiene quella funzione. Dentro
            # una classe scritta in un metodo (i nostri «Ponte»), una lambda senza parametri eredita
            # il «self» del metodo: e' ancora la finestra, non il Ponte.
            padre = {}
            for n in ast.walk(albero):
                for figlio in ast.iter_child_nodes(n):
                    padre[figlio] = n

            def di_chi(nodo):
                dentro_funzione = False
                while nodo in padre:
                    nodo = padre[nodo]
                    if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                        primi = nodo.args.posonlyargs + nodo.args.args
                        if primi and primi[0].arg == "self":
                            dentro_funzione = True
                        continue
                    if isinstance(nodo, ast.ClassDef) and dentro_funzione:
                        return nodo
                return None

            for n in ast.walk(albero):
                if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                        and isinstance(n.func.value, ast.Name) and n.func.value.id == "self"
                        and n.func.attr.startswith("_") and not n.func.attr.startswith("__")):
                    continue
                classe = di_chi(n)
                if classe is None:
                    continue
                su, cieca = madri(classe, {classe.name})
                if cieca or n.func.attr in (self._membri(classe) | su):
                    continue
                guai.append(f"{f.relative_to(radice)}:{n.lineno} self.{n.func.attr}()"
                            f" non esiste in {classe.name}")
        self.assertEqual(guai, [], "metodi chiamati e mai definiti:\n  " + "\n  ".join(guai))

SEMPRE_DEFINITI = {"__file__", "__name__", "__doc__", "__package__", "__spec__",
                   "__builtins__", "__class__", "__path__", "__all__"}


def nomi_mai_definiti(sorgente: str, dove: str = "<prova>") -> list[str]:
    """I nomi che quel file legge e che nessuno ha definito: un `NameError` che aspetta.

    Non serve una libreria in piu': `symtable` sta nella libreria standard e sa, scatola per scatola,
    chi e' parametro, chi e' locale, chi arriva da fuori. Si guarda solo il caso che non ha scuse —
    un nome che quella funzione legge e che non e' ne' suo, ne' della funzione che la contiene, ne'
    del file, ne' di Python.
    """
    import builtins
    import symtable

    noti = set(dir(builtins)) | SEMPRE_DEFINITI
    guai: list[str] = []

    def scorri(tavola, fuori, percorso):
        mie = set(tavola.get_identifiers())
        for s in tavola.get_symbols():
            if (s.is_global() and not s.is_local() and not s.is_free() and not s.is_parameter()
                    and not s.is_imported() and not s.is_declared_global()
                    and s.get_name() not in fuori and s.get_name() not in noti):
                guai.append(f"{dove}:{percorso or '<file>'} legge «{s.get_name()}», che non esiste")
        for figlia in tavola.get_children():
            # Il corpo di una classe non si vede da dentro i suoi metodi: e' una regola di Python,
            # non una svista.
            dentro = fuori if tavola.get_type() == "class" else fuori | mie
            scorri(figlia, dentro, f"{percorso}.{figlia.get_name()}" if percorso else figlia.get_name())

    cima = symtable.symtable(sorgente, dove, "exec")
    scorri(cima, set(cima.get_identifiers()) | noti, "")
    return guai


class UnAttributoLettoDeveEsistereNellaClasseTest(unittest.TestCase):
    """`self.cfg` dentro una classe che non ha nessun `cfg`.

    Il 24/10, aggiungendo la riga «standard: 60%» sotto i riconoscimenti, ho scritto `self.cfg` in
    `ActionDialog`. La configurazione ce l'ha la finestra che la apre, che e' un'altra classe. Sono
    andate in errore **quaranta** prove di finestra, tutte per la stessa riga — e la cosa peggiore e'
    che quella mattina avevo gia' corretto lo stesso errore su un metodo (`_nota_microfoni`), e me
    l'ero pure scritto.

    Il controllo e' lo stesso di sopra, spostato dai metodi agli attributi: quello che la classe
    legge su `self` deve essere qualcosa che la classe, o una delle sue classi madri, assegna da
    qualche parte. Due cose non si contano, e sono le due che fanno rumore senza dire niente: le
    chiamate (Qt ne ha a migliaia, e non si possono leggere da qui) e la manciata di slot che si
    passano senza chiamarli.
    """

    def test_no_class_reads_an_attribute_it_does_not_have(self):
        import ast
        from pathlib import Path

        guardia = UnMetodoChiamatoDeveEsistereTest()
        radice = Path(__file__).resolve().parents[1]
        file = [f for f in sorted((radice / "mirto").rglob("*.py")) if "__pycache__" not in str(f)]
        alberi = {f: ast.parse(f.read_text(encoding="utf-8"), filename=str(f)) for f in file}
        classi: dict = {}
        for albero in alberi.values():
            for n in ast.walk(albero):
                if isinstance(n, ast.ClassDef):
                    classi.setdefault(n.name, []).append(n)

        def madri(classe, visti):
            avuti, cieca = set(), False
            for b in classe.bases:
                nome = b.id if isinstance(b, ast.Name) else (b.attr if isinstance(b, ast.Attribute) else "")
                if not nome or nome in visti:
                    cieca = cieca or not nome
                    continue
                if nome in classi:
                    for altra in classi[nome]:
                        avuti |= guardia._membri(altra)
                        su, piu_cieca = madri(altra, visti | {nome})
                        avuti |= su
                        cieca = cieca or piu_cieca
                elif not nome.startswith("Q"):
                    cieca = True
            return avuti, cieca

        guai = []
        for f, albero in alberi.items():
            padre = {}
            for n in ast.walk(albero):
                for figlio in ast.iter_child_nodes(n):
                    padre[figlio] = n
            for classe in [n for n in ast.walk(albero) if isinstance(n, ast.ClassDef)]:
                su, cieca = madri(classe, {classe.name})
                if cieca:
                    continue
                noti = guardia._membri(classe) | su | guardia.SLOT_DI_QT
                for n in ast.walk(classe):
                    if not (isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Load)
                            and isinstance(n.value, ast.Name) and n.value.id == "self"
                            and n.attr not in noti):
                        continue
                    sopra = padre.get(n)
                    if isinstance(sopra, ast.Call) and sopra.func is n:
                        continue    # e' una chiamata: i metodi di Qt non si possono leggere da qui
                    guai.append(f"{f.relative_to(radice)}:{n.lineno} self.{n.attr}"
                                f" non esiste in {classe.name}")
        self.assertEqual(guai, [], "attributi letti e mai assegnati:\n  " + "\n  ".join(guai))


class UnNomeLettoDeveEsistereTest(unittest.TestCase):
    """Il terzo errore della stessa giornata: `ponte` invece di `self.ponte`.

    Il 24/10 Marco ha aperto l'audio di un nodo e ha trovato `NameError: name 'ponte' is not defined`.
    I due guardiani di prima non potevano vederlo: non era un nome preso in prestito da un'altra
    funzione (ce n'era uno con quel nome, ma era un **parametro**), e non era un metodo mancante.
    Era un nome che non esiste, e senza PySide6 qui quella riga non si esegue mai.
    """

    def test_the_guard_still_sees_a_name_that_is_not_there(self):
        """Prima di fidarsi del guardiano, si controlla che non sia diventato cieco."""
        rotto = "class F:\n    def apri(self, ponte):\n        self.ponte = ponte\n" \
                "    def dopo(self):\n        return ponte.stato()\n"
        self.assertEqual(len(nomi_mai_definiti(rotto, "rotto.py")), 1, nomi_mai_definiti(rotto))
        sano = rotto.replace("return ponte.stato()", "return self.ponte.stato()")
        self.assertEqual(nomi_mai_definiti(sano, "sano.py"), [])

    def test_no_source_file_reads_a_name_that_does_not_exist(self):
        from pathlib import Path

        radice = Path(__file__).resolve().parents[1]
        guai = []
        for cartella in ("mirto", "node", "tools", "tests"):
            for f in sorted((radice / cartella).rglob("*.py")):
                if "__pycache__" in str(f) or "/build/" in str(f):
                    continue
                guai += nomi_mai_definiti(f.read_text(encoding="utf-8"), str(f.relative_to(radice)))
        self.assertEqual(guai, [], "nomi letti e mai definiti:\n  " + "\n  ".join(guai))


class UnWidgetSiTienePerManoTest(unittest.TestCase):
    """`voce.widget()` due volte di fila non e' la stessa cosa due volte.

    Il 24/10 la seconda schermata del wizard andava in errore a ogni spunta e la griglia restava mezza
    costruita: `AttributeError: 'NoneType' object has no attribute 'deleteLater'`. Il codice era

        if voce.widget() is not None:
            voce.widget().setParent(None)
            voce.widget().deleteLater()

    e sembrava prudente. Ma dopo `setParent(None)` quel riquadro non ha piu' ne' un genitore ne' un
    riferimento di Python: PySide6 lo distrugge, e la chiamata dopo torna `None` — con il controllo
    gia' passato. Si prende una volta sola e si tiene in una variabile.
    """

    def test_no_function_calls_widget_twice_on_the_same_thing(self):
        import ast
        from pathlib import Path

        radice = Path(__file__).resolve().parents[1]
        guai = []
        for f in sorted((radice / "mirto").rglob("*.py")):
            if "__pycache__" in str(f):
                continue
            sorgente = f.read_text(encoding="utf-8")
            albero = ast.parse(sorgente, filename=str(f))
            for fun in [n for n in ast.walk(albero)
                        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
                visti: dict[str, int] = {}
                for n in ast.walk(fun):
                    if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                            and n.func.attr == "widget" and not n.args):
                        chi = ast.get_source_segment(sorgente, n.func.value) or ""
                        visti[chi] = visti.get(chi, 0) + 1
                for chi, quante in visti.items():
                    if quante > 1:
                        guai.append(f"{f.relative_to(radice)}:{fun.name} chiama {chi}.widget() "
                                    f"{quante} volte: tienilo in una variabile")
        self.assertEqual(guai, [], "widget() chiamato piu' volte sulla stessa cosa:\n  " + "\n  ".join(guai))


class LaTerzaSchermataDiceDoveChiedereTest(unittest.TestCase):
    """«Cosa fa» diceva cosa fa, non a chi rivolgersi se quel che vuoi non c'e' (Marco, 24/10).

    Fino al 7/11 l'indirizzo era la posta personale di Marco, perche' il progetto era privato e un
    collegamento a GitHub sarebbe stato una porta chiusa. Diventando pubblico la porta si apre, e
    quella mail non va piu' bene per due motivi: e' l'indirizzo privato di una persona stampato
    dentro un programma che chiunque scarica, e una domanda scritta li' la legge uno solo, mentre la
    stessa domanda in una discussione la leggono (e le rispondono) tutti.

    Si guarda il testo del file perche' quella finestra, qui, non si puo' aprire: PySide6 nel
    container non si installa. Una prova grossolana che gira sempre vale piu' di una elegante che
    gira solo sul Mac di Marco.
    """

    def _pagine(self) -> str:
        from pathlib import Path

        t = (Path(__file__).resolve().parents[1] / "mirto" / "gui" / "settings.py").read_text(encoding="utf-8")
        inizio = t.index("    PAGINE = (")
        return t[inizio:t.index("\n    )\n", inizio)]

    def test_the_third_screen_says_who_to_write_to(self):
        pagine = self._pagine()
        self.assertIn("Imposta le azioni", pagine)
        self.assertIn('<a href="{discussioni}">', pagine,
                      "si deve poter chiedere da qui, non cercando dove chiedere chissa' dove")
        self.assertNotIn("mailto:", pagine,
                         "l'indirizzo personale di uno non si stampa dentro il programma di tutti")
        self.assertNotIn("Cosa fa'", pagine, "il titolo vecchio non deve restare in giro")

    def test_the_address_is_actually_filled_in(self):
        """Il segnaposto senza chi lo riempie e' un collegamento a `{discussioni}`, cioe' a nulla."""
        from pathlib import Path

        t = (Path(__file__).resolve().parents[1] / "mirto" / "gui" / "settings.py").read_text(encoding="utf-8")
        self.assertIn("discussioni=DISCUSSIONI_URL", t,
                      "la descrizione ha {discussioni} dentro: qualcuno deve passarglielo")
        self.assertIn("from .. import DISCUSSIONI_URL", t, "e deve prenderlo dal posto unico")


class NessunIndirizzoPersonaleNelProgrammaTest(unittest.TestCase):
    """Nel programma non ci sono indirizzi di posta personali (7/11).

    Il repository diventa pubblico: tutto quello che e' scritto dentro `mirto/` lo legge chiunque,
    sorgenti e cataloghi di traduzione compresi. Una mail privata la' dentro non si puo' ritirare —
    resta nelle copie scaricate, nei fork, nelle traduzioni. Chi vuole chiedere qualcosa passa dalle
    discussioni.

    `tuoindirizzo@gmail.com` in Impostazioni e' un esempio di dove l'utente scrive *la sua* mail:
    quello deve restare, ed e' per questo che qui si vieta la mail di Marco e i `mailto:`, non
    "qualsiasi cosa con una chiocciola".
    """

    def test_no_personal_mail_in_the_package(self):
        from pathlib import Path

        pkg = Path(__file__).resolve().parents[1] / "mirto"
        guai = []
        for f in sorted(list(pkg.rglob("*.py")) + list(pkg.rglob("*.json"))):
            for n, riga in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if "marcopogo81@" in riga or "mailto:" in riga:
                    guai.append(f"{f.relative_to(pkg.parent)}:{n}")
        self.assertEqual(guai, [], "indirizzo personale o mailto: dentro il programma:\n  "
                                   + "\n  ".join(guai))


class SiPuoUscireAncheSulMacTest(unittest.TestCase):
    """Sul Mac «Esci da Mirto» non si vedeva da nessuna parte (Marco, 7/11).

    `QuitRole` dice a Qt che quella voce e' l'uscita del programma, e su macOS Qt la **sposta**
    nel menu dell'applicazione, quello accanto alla mela. Serve che ci sia: cosi' il ⌘Q e il
    «Quit» di sistema passano dalla nostra conferma invece di ammazzare il programma con le
    camere accese. Ma sposta**re** vuol dire togliere: dal menu «Mirto» la voce spariva, mentre
    su Windows restava, e chi non conosceva la scorciatoia non aveva modo di uscire — la finestra
    chiusa non ha nemmeno una barra dei menu da cui cercarla.

    Quindi sul Mac ce ne vogliono due: quella spostata, che serve al sistema, e una visibile nel
    menu, che serve alla persona. Questa prova pretende tutte e due, perche' togliendo la seconda
    non si rompe niente di rumoroso: sparisce solo una voce, e uno se ne accorge usandolo.

    Si guarda il testo del file: PySide6 nel container non si installa, e una prova grossolana che
    gira sempre vale piu' di una elegante che gira solo sul Mac di Marco.
    """

    def _costruzione_del_menu(self) -> str:
        import ast
        from pathlib import Path

        sorgente = (Path(__file__).resolve().parents[1] / "mirto" / "gui" / "app.py")
        testo = sorgente.read_text(encoding="utf-8")
        albero = ast.parse(testo)
        for n in ast.walk(albero):
            if isinstance(n, ast.FunctionDef) and n.name == "_build_menu_bar":
                righe = testo.splitlines()[n.lineno - 1:n.end_lineno]
                return "\n".join(righe)
        self.fail("_build_menu_bar non c'e' piu': questa prova va riscritta, non tolta")

    def test_the_quit_entry_is_visible_on_the_mac_too(self):
        corpo = self._costruzione_del_menu()
        self.assertIn("QuitRole", corpo,
                      "senza QuitRole il Quit di sistema salta la conferma e spegne tutto")
        self.assertIn('sys.platform == "darwin"', corpo,
                      "la voce in piu' serve solo sul Mac: su Windows QuitRole non sposta niente")
        self.assertIn("NoRole", corpo,
                      "la seconda voce deve dire a Qt di lasciarla dov'e', o sparisce come l'altra")
        self.assertGreaterEqual(corpo.count("Esci da Mirto"), 2,
                                "sul Mac ne servono due: quella spostata e quella che si vede")


class UnaLunghezzaNonSiScriveAManoTest(unittest.TestCase):
    """`startswith("mirto-")` e poi `[8:]`: otto era la lunghezza di «myhouse-» (7/11).

    Rinominando il programma, due righe tagliavano via un numero fisso di caratteri deciso quando
    il prefisso era `myhouse-`. Il prefisso e' diventato `mirto-`, due lettere piu' corto, e la
    camera «mirto-ingresso» si chiamava «Gresso». Non si rompe niente in modo rumoroso: esce un
    nome sbagliato, e uno lo scopre guardando.

    Il numero si misura dalla stringa (`len(prefisso)`), e questa prova lo pretende: se accanto a
    un `startswith("X")` c'e' un taglio a un numero diverso da `len("X")`, e' un numero scritto a
    mano che aspetta il prossimo rinomino.
    """

    def test_no_slice_disagrees_with_the_prefix_beside_it(self):
        import re
        from pathlib import Path

        radice = Path(__file__).resolve().parents[1]
        inizio = re.compile(r"""startswith\(\s*(['"])(?P<p>[^'"]+)\1""")
        taglio = re.compile(r"\[\s*(?P<n>\d+)\s*:\s*\]")
        guai = []
        for cartella in ("mirto", "node", "tools"):
            for f in sorted((radice / cartella).rglob("*")):
                if f.suffix not in (".py", ".sh") or "__pycache__" in str(f):
                    continue
                righe = f.read_text(encoding="utf-8").splitlines()
                for i, _r in enumerate(righe):
                    finestra = "\n".join(righe[i:i + 2])
                    pre = inizio.search(finestra)
                    tag = taglio.search(finestra)
                    if pre and tag and int(tag.group("n")) != len(pre.group("p")):
                        guai.append(f"{f.relative_to(radice)}:{i + 1}: taglia a "
                                    f"{tag.group('n')} accanto a «{pre.group('p')}» "
                                    f"(lungo {len(pre.group('p'))})")
        self.assertEqual(guai, [], "lunghezza scritta a mano invece di len(...):\n  " + "\n  ".join(guai))


class DentroUnArchivioIlSeparatoreELaBarraTest(unittest.TestCase):
    """`str(f.relative_to(dove))` dentro un tar: su Windows mette la barra rovesciata (8/11).

    Il pacchetto che Mirto manda a un nodo si costruiva con `str(Path)` come nome dei membri. Su
    macOS e Linux `str()` e `as_posix()` danno la stessa cosa, quindi non si vedeva; su Windows
    `mirto_node/server.py` diventa `mirto_node\\server.py`, e quel nome arriva sul Raspberry come
    **un file solo**, con la barra rovesciata dentro il nome, nella radice del pacchetto. `install.sh`
    non trova nessuna cartella `mirto_node` e l'aggiornamento muore. Cioe': un Mirto su Windows non
    poteva aggiornare un nodo, e nessuno di noi due usa Windows.

    L'ha trovato la CI del repository pubblico, rossa solo sui due lavori Windows — ma la prova che
    l'ha vista (`test_the_package_carries_only_what_the_node_needs`) **puo' fallire solo su Windows**,
    cioe' dorme proprio dove lavoriamo. Questa invece guarda il codice, e quindi parla anche da qui:
    in un modulo che costruisce archivi, un percorso relativo e' il nome di un membro, e il nome di
    un membro non dipende dal sistema su cui gira il programma.
    """

    def test_a_module_that_builds_archives_always_uses_as_posix(self):
        archivi = re.compile(r"^\s*(import|from)\s+(tarfile|zipfile)\b", re.M)
        relativo = re.compile(r"\.relative_to\(")
        guai = []
        for cartella in ("mirto", "node", "tools"):
            for f in sorted((ROOT / cartella).rglob("*.py")):
                if "__pycache__" in str(f):
                    continue
                testo = f.read_text(encoding="utf-8")
                if not archivi.search(testo):
                    continue
                for i, riga in enumerate(testo.splitlines(), 1):
                    for m in relativo.finditer(riga):
                        # la chiusura della parentesi di `relative_to(...)`, e cosa viene dopo
                        resto, livello = "", 0
                        for j, c in enumerate(riga[m.end() - 1:], m.end() - 1):
                            livello += (c == "(") - (c == ")")
                            if livello == 0:
                                resto = riga[j + 1:]
                                break
                        if not resto.startswith(".as_posix()"):
                            guai.append(f"{f.relative_to(ROOT)}:{i}: {riga.strip()}")
        self.assertEqual(guai, [], "qui un percorso relativo diventa il nome di un membro di un "
                                   "archivio: ci vuole .as_posix(), o su Windows esce una barra "
                                   "rovesciata dentro il nome:\n  " + "\n  ".join(guai))


class GliIndirizziDelProgettoStannoInUnPostoSoloTest(unittest.TestCase):
    """Rilasci, donazioni e discussioni erano tre indirizzi scritti a mano in tre file (7/11).

    Il repository cambia nome prima del lancio pubblico. Tre indirizzi scritti a mano sono tre
    posti da ricordarsi, e quello che si dimentica diventa un pulsante che porta su una pagina che
    non esiste. Stanno in `mirto/__init__.py`, e chi li usa li importa.

    Si cercano *le costanti*, non "github.com": i modelli dell'AI si scaricano da GitHub (YOLOX,
    Coral) e quelli sono indirizzi di altri, non del progetto. Cercando il valore vero la prova
    continua a funzionare anche il giorno che il repository cambia nome.
    """

    def test_project_urls_only_in_one_place(self):
        from pathlib import Path

        from mirto import REPO_URL, SOSTIENI_URL

        pkg = Path(__file__).resolve().parents[1] / "mirto"
        guai = []
        for f in sorted(pkg.rglob("*.py")):
            if f.name == "__init__.py" and f.parent == pkg:
                continue
            for n, riga in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if REPO_URL in riga or SOSTIENI_URL in riga:
                    guai.append(f"{f.relative_to(pkg.parent)}:{n}")
        self.assertEqual(guai, [], "indirizzo del progetto scritto a mano fuori da "
                                   "mirto/__init__.py (importalo da la'):\n  " + "\n  ".join(guai))


class UnNomeNonSiScriveDueVolteTest(unittest.TestCase):
    """`LUCE` definito due volte nello stesso file, con due significati diversi.

    Il 24/10, scrivendo i generi di un componente, ho messo in fondo a `pannello_catena.py`:

        SUONO, LUCE, ALTRO = "suono", "luce", "altro"

    e trenta righe sopra c'era gia' `LUCE, SIRENA, ... = "light", "siren", ...`. Python non dice
    niente: il secondo vince, e da quel momento una luce smette di essere una luce. L'ho visto solo
    perche' una prova stampava «altro» dove doveva esserci «luce» — senza quella riga di stampa
    sarebbe finito dentro, e si sarebbe visto fra giorni in una finestra.

    Si guarda **solo il primo livello** del file: dentro un `if` o un `try` ridefinire un nome e' la
    cosa normale (`try: import X / except: X = None`), e segnalarlo sarebbe rumore.
    """

    def test_no_module_level_name_is_written_twice(self):
        import ast
        from pathlib import Path

        radice = Path(__file__).resolve().parents[1]
        guai = []
        for cartella in ("mirto", "node", "tools", "tests"):
            for f in sorted((radice / cartella).rglob("*.py")):
                if "__pycache__" in str(f):
                    continue
                albero = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
                visti: dict = {}
                for n in albero.body:
                    bersagli = []
                    if isinstance(n, ast.Assign):
                        da_vedere = list(n.targets)
                        while da_vedere:
                            x = da_vedere.pop()
                            if isinstance(x, ast.Name):
                                bersagli.append(x.id)
                            elif isinstance(x, (ast.Tuple, ast.List)):
                                da_vedere.extend(x.elts)
                    elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
                        bersagli.append(n.target.id)
                    for nome in bersagli:
                        if nome in visti and nome != "_":
                            guai.append(f"{f.relative_to(radice)}:{n.lineno} «{nome}» era gia' "
                                        f"definito alla riga {visti[nome]}")
                        visti[nome] = n.lineno
        self.assertEqual(guai, [], "nomi scritti due volte nello stesso file:\n  " + "\n  ".join(guai))


def parametri_riusati(sorgente: str, dove: str = "<prova>") -> list[str]:
    """I parametri che una funzione riscrive con qualcosa che non viene da loro.

    Le due riscritture oneste di un parametro non si segnalano, perché si vedono leggendo e sono
    normali in tutto il programma:

    * ``if x is None: x = <il valore di serie>`` — dentro un ``if`` che controlla quel parametro;
    * ``try: value = int(value) / except ValueError: value = 45`` — dentro l'``except`` di un
      ``try`` che quel parametro lo stava già sistemando.
    """
    import ast

    def scritti(corpo) -> set:
        fuori = set()
        for n in corpo:
            for x in ast.walk(n):
                if isinstance(x, ast.Assign):
                    fuori |= {b.id for b in x.targets if isinstance(b, ast.Name)}
        return fuori

    def guarda(nodo, parametri, protetti, guai, nome_funzione):
        for figlio in ast.iter_child_nodes(nodo):
            if isinstance(figlio, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue  # ha i suoi parametri: lo guarda il suo giro
            ora = set(protetti)
            if isinstance(figlio, (ast.If, ast.While)):
                ora |= {x.id for x in ast.walk(figlio.test) if isinstance(x, ast.Name)}
            elif isinstance(figlio, ast.Try):
                ora |= scritti(figlio.body)
            elif isinstance(figlio, ast.Assign):
                da_lui = {x.id for x in ast.walk(figlio.value) if isinstance(x, ast.Name)}
                for b in figlio.targets:
                    if (isinstance(b, ast.Name) and b.id in parametri
                            and b.id not in da_lui and b.id not in protetti):
                        guai.append(f"{dove}:{figlio.lineno} {nome_funzione}() riscrive il "
                                    f"parametro «{b.id}» con qualcos'altro")
            guarda(figlio, parametri, ora, guai, nome_funzione)

    guai: list = []
    albero = ast.parse(sorgente, filename=str(dove))
    for fun in [n for n in ast.walk(albero) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        suoi = fun.args
        nomi = {a.arg for a in suoi.posonlyargs + suoi.args + suoi.kwonlyargs}
        guarda(fun, nomi, set(), guai, fun.name)
    return guai


class UnParametroNonSiRiusaPerAltroTest(unittest.TestCase):
    """`dentro` era l'indice del pezzo, e dentro la funzione diventava un menu.

    Il 24/10, nel «+» in tre passi, `_voci_del_componente(self, dove_menu, comp, ..., dentro=None)`
    riceveva in `dentro` **a quale pezzo** attaccare la cosa nuova, e trenta righe dopo scriveva:

        dentro = dove_menu if len(cose) == 1 else dove_menu.addMenu(...)

    cioè il sotto-menu. Da quel momento la lambda che passava `dentro` al metodo che aggiunge il
    pezzo passava un `QMenu`, e Marco al primo clic sul «+» ha visto
    `TypeError: unsupported operand type(s) for +: 'QMenu' and 'int'`.

    È lo stesso errore di `LUCE` — un nome, due significati — ma dentro una funzione, dove il
    guardiano del primo livello non guarda. Qui si guardano i **parametri**, perché quello che li
    rende pericolosi è che il valore buono arriva da fuori: chi chiama continua a passarlo giusto, e
    il danno si vede soltanto dove quel nome viene riletto.
    """

    def test_the_guard_sees_the_error_it_was_born_for(self):
        guai = parametri_riusati(
            "def voci(dove_menu, cose, dentro=None):\n"
            "    dentro = dove_menu.addMenu('x')\n"
            "    for p in cose:\n"
            "        dove_menu.addAction(p, lambda d=dentro: usa(d))\n")
        self.assertEqual(len(guai), 1, f"il guardiano non vede più l'errore del 24/10: {guai}")
        self.assertIn("«dentro»", guai[0])

    def test_the_two_honest_ways_are_not_a_complaint(self):
        self.assertEqual(parametri_riusati(
            "def f(x=None, value=0):\n"
            "    if x is None:\n"
            "        x = [1, 2]\n"
            "    try:\n"
            "        value = int(value)\n"
            "    except ValueError:\n"
            "        value = 45\n"
            "    return x, value\n"), [])

    def test_a_parameter_made_from_itself_is_not_a_complaint(self):
        self.assertEqual(parametri_riusati("def f(testo):\n    testo = testo.strip()\n    return testo\n"), [])

    def test_no_function_reuses_a_parameter_for_something_else(self):
        from pathlib import Path

        radice = Path(__file__).resolve().parents[1]
        guai: list = []
        for cartella in ("mirto", "node", "tools", "tests"):
            for f in sorted((radice / cartella).rglob("*.py")):
                if "__pycache__" in str(f):
                    continue
                guai += parametri_riusati(f.read_text(encoding="utf-8"), str(f.relative_to(radice)))
        self.assertEqual(guai, [], "parametri riusati per un'altra cosa:\n  " + "\n  ".join(guai))


class IlPacchettoDellAppPortaIlNodoTest(unittest.TestCase):
    """Da N-02 Mirto aggiorna i nodi mandando il codice che ha dentro: dentro ci deve stare.

    Senza la cartella `node/`, un Mirto installato dal pacchetto sa comandare i nodi ma non sa
    aggiornarli, e il pulsante «Aggiorna il nodo» sparisce senza che nessuno capisca perche'. Qui si
    prova che lo spec la porta: una cosa che si scoprirebbe solo installando il pacchetto, cioe' il
    giorno della release.
    """

    def test_the_spec_carries_the_node_code(self):
        from pathlib import Path

        spec = (Path(__file__).resolve().parents[1] / "packaging" / "mirto.spec").read_text(encoding="utf-8")
        self.assertIn('("../node", "node")', spec)

    def test_the_app_finds_it_where_it_puts_it(self):
        """Lo spec la mette in «node/», e chi la cerca guarda in «node/»: due nomi, un posto solo."""
        from mirto.actions.aggiorna_nodo import cartella_del_nodo

        dove = cartella_del_nodo()
        self.assertIsNotNone(dove, "nel progetto il codice del nodo c'e'")
        self.assertEqual(dove.name, "node")


def metodi_che_non_esistono(file_prove, radice=None) -> list[str]:
    """Nelle prove: `dlg._metodo()` su un oggetto appena costruito, dove quella classe non ce l'ha.

    Il secondo guardiano controlla `self._metodo()` **dentro** una classe. Una prova pero' lavora
    dall'esterno — `dlg = StatoDialog(...)` e poi `dlg._rileggi()` — e li' non arrivava nessuno: il
    25/10 ho rinominato `_aggiorna` in `_rileggi` e la chiamata nella prova e' rimasta indietro.
    L'errore e' uscito sul Mac di Marco, cioe' ha speso un suo giro per una cosa che si vedeva da qui.

    Si guarda **solo** il caso certo: una variabile assegnata una volta sola con `Classe(...)`, dove
    `Classe` arriva da un `from mirto… import Classe` nello stesso file, e un metodo privato. Se la
    classe non si trova, o eredita da qualcosa che sta altrove, si lascia perdere: un guardiano che
    sbaglia e' un guardiano che si spegne.
    """
    import ast
    from pathlib import Path

    radice = Path(radice or Path(__file__).resolve().parents[1])

    def classi_di(mod: str) -> dict:
        f = radice / (mod.replace(".", "/") + ".py")
        if not f.is_file():
            return {}
        fuori = {}
        for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"), filename=str(f))):
            if isinstance(n, ast.ClassDef):
                suoi = {m.name for m in n.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))}
                suoi |= {t.attr for x in ast.walk(n) if isinstance(x, ast.Assign)
                         for t in x.targets if isinstance(t, ast.Attribute)
                         and isinstance(t.value, ast.Name) and t.value.id == "self"}
                fuori[n.name] = (suoi, [b.id for b in n.bases if isinstance(b, ast.Name)])
        return fuori

    f = Path(file_prove)
    albero = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
    dove = {}
    for n in ast.walk(albero):
        if isinstance(n, ast.ImportFrom) and n.module and n.module.startswith(("mirto", "mirto_node")):
            for a in n.names:
                dove[a.asname or a.name] = n.module
    guai = []
    for fun in [n for n in ast.walk(albero) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        nati, scritti = {}, {}
        for n in ast.walk(fun):
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                nome = n.targets[0].id
                scritti[nome] = scritti.get(nome, 0) + 1
                if isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Name):
                    nati[nome] = n.value.func.id
        for n in ast.walk(fun):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)):
                continue
            o = n.func.value
            if not (isinstance(o, ast.Name) and n.func.attr.startswith("_")):
                continue
            classe = nati.get(o.id)
            if classe is None or scritti.get(o.id, 0) != 1 or classe not in dove:
                continue    # riassegnata, o non si sa di che classe sia: non si accusa
            tabella = classi_di(dove[classe])
            if classe not in tabella:
                continue
            # Le basi che stanno altrove (QDialog, QWidget…) si ignorano, e non e' una scorciatoia:
            # qui si guardano solo i nomi **privati**, e un `_qualcosa` di una classe di Qt non lo
            # chiama una nostra prova. Arrendersi davanti a una base esterna vorrebbe dire
            # arrendersi sempre, perche' ogni finestra eredita da Qt — cioe' un guardiano spento.
            visti, resta, giri = set(), [classe], 0
            while resta and giri < 20:
                giri += 1
                c = resta.pop()
                if c in tabella:
                    visti |= tabella[c][0]
                    resta += tabella[c][1]
            if visti and n.func.attr not in visti:
                guai.append(f"{f.name}:{n.lineno} {o.id}.{n.func.attr}() — {classe} non ce l'ha")
    return guai


class UnaProvaChiamaMetodiCheEsistonoTest(unittest.TestCase):
    """Le prove di finestra le lancia Marco: un nome vecchio li' dentro costa un suo giro.

    E' successo due volte il 25/10, con due rinomini diversi. La prima l'ha presa una prova di
    comportamento; la seconda nessuno, perche' stava in una prova e chiamava il metodo dall'esterno.
    """

    def test_no_test_calls_a_method_that_is_not_there(self):
        from pathlib import Path

        radice = Path(__file__).resolve().parents[1]
        guai = []
        for f in sorted((radice / "tests").glob("*.py")):
            guai += metodi_che_non_esistono(f, radice)
        self.assertEqual(guai, [], "prove che chiamano metodi inesistenti:\n  " + "\n  ".join(guai))

    def test_the_guard_sees_the_one_that_cost_a_round(self):
        import tempfile
        from pathlib import Path

        finto = Path(tempfile.mkdtemp()) / "test_finto.py"
        finto.write_text("from mirto.gui.stato_ui import StatoDialog\n\n\n"
                         "def prova():\n"
                         "    dlg = StatoDialog('x', None)\n"
                         "    dlg._aggiorna()\n", encoding="utf-8")
        guai = metodi_che_non_esistono(finto)
        self.assertEqual(len(guai), 1, f"il guardiano non vede il rinomino del 25/10: {guai}")
        self.assertIn("_aggiorna", guai[0])

    def test_a_method_that_is_there_is_not_a_complaint(self):
        import tempfile
        from pathlib import Path

        finto = Path(tempfile.mkdtemp()) / "test_finto.py"
        finto.write_text("from mirto.gui.stato_ui import StatoDialog\n\n\n"
                         "def prova():\n"
                         "    dlg = StatoDialog('x', None)\n"
                         "    dlg._rileggi()\n", encoding="utf-8")
        self.assertEqual(metodi_che_non_esistono(finto), [])


class OgniRottaDelNodoSiRaggiungeDaMirtoTest(unittest.TestCase):
    """Guardiano #11: ogni cosa che il nodo sa fare deve avere un modo di chiederla da Mirto.

    Marco, 20/10, ed e' il requisito di N-07: *«se serve il terminale, quel pezzo non e' finito»* —
    un tester con una cassa Bluetooth non apre una sessione ssh. Il 20/10 audio, Wi-Fi, disco,
    rinomina e «aggiungi un altro computer» si facevano **solo** da `mirto-nodo`, e quel difetto
    non si vedeva da nessuna prova: il nodo funzionava, Mirto funzionava, e in mezzo c'era un
    terminale.

    Si controlla in due passi, perche' un pulsante non si legge da un albero sintattico:

    1. ogni rotta POST del nodo ha un metodo in `mirto/actions/nodes.py` che la chiede. Se non ce
       l'ha, nessuna finestra puo' averla e quella cosa si fa solo sul Raspberry;
    2. ogni metodo del client e' chiamato da **qualcun altro** dentro `mirto/`. Un metodo che solo
       il client conosce e' una capacita' che esiste nel codice e non in nessuna schermata — che e'
       lo stesso difetto visto dall'altro lato.

    Si aggiunge una rotta al nodo e si dimentica il lato app: questa prova diventa rossa subito,
    invece di lasciare un pezzo raggiungibile solo da terminale finche' qualcuno non se ne accorge.
    """

    # L'unica rotta che non passa da `nodes.py`, per costruzione: l'abbinamento avviene **prima** che
    # esista una chiave con cui firmare, quindi vive in `pairing.py` con una richiesta non firmata.
    # Mirto la chiama (`unsigned_request(url, "POST", "/abbina", …)`): non e' una cosa da terminale.
    ALTROVE = {"/abbina": ("mirto/actions/pairing.py", 'unsigned_request')}

    def rotte_del_nodo(self) -> set[str]:
        """Le rotte POST che il server del nodo serve, lette da `_post` invece che da un elenco.

        Lette dal codice e non scritte qui: un elenco a mano e' la cosa che si dimentica di
        aggiornare, cioe' esattamente il difetto che questa prova esiste per impedire.
        """
        import ast as _ast
        from pathlib import Path as _Path

        testo = (_Path(__file__).resolve().parents[1] / "node" / "mirto_node" / "server.py"
                 ).read_text(encoding="utf-8")
        albero = _ast.parse(testo)
        post = next((n for n in _ast.walk(albero)
                     if isinstance(n, _ast.FunctionDef) and n.name == "_post"), None)
        self.assertIsNotNone(post, "«_post» non si chiama piu' cosi': il guardiano e' cieco")
        fuori = set()
        for nodo in _ast.walk(post):
            # parts == ["configura", "audio"]  →  /configura/audio
            if isinstance(nodo, _ast.Compare) and isinstance(nodo.left, _ast.Name) \
                    and nodo.left.id == "parts" and isinstance(nodo.comparators[0], _ast.List):
                pezzi = [e.value for e in nodo.comparators[0].elts if isinstance(e, _ast.Constant)]
                if len(pezzi) == len(nodo.comparators[0].elts):
                    fuori.add("/" + "/".join(str(p) for p in pezzi))
            # self.path == "/abbina"
            if isinstance(nodo, _ast.Compare) and isinstance(nodo.comparators[0], _ast.Constant) \
                    and isinstance(nodo.comparators[0].value, str) \
                    and str(nodo.comparators[0].value).startswith("/"):
                fuori.add(str(nodo.comparators[0].value))
        return fuori

    def rotte_che_mirto_chiama(self) -> set[str]:
        """Le rotte che il client sa chiedere, lette dalle sue chiamate a `request("POST", …)`."""
        import ast as _ast
        import re as _re
        from pathlib import Path as _Path

        testo = (_Path(__file__).resolve().parents[1] / "mirto" / "actions" / "nodes.py"
                 ).read_text(encoding="utf-8")
        fuori = set()
        for nodo in _ast.walk(_ast.parse(testo)):
            if not (isinstance(nodo, _ast.Call) and isinstance(nodo.func, _ast.Attribute)
                    and nodo.func.attr == "request" and len(nodo.args) >= 2):
                continue
            metodo, dove = nodo.args[0], nodo.args[1]
            if not (isinstance(metodo, _ast.Constant) and metodo.value == "POST"):
                continue
            if isinstance(dove, _ast.Constant):
                fuori.add(str(dove.value))
            elif isinstance(dove, _ast.JoinedStr):   # f"/azioni/{nome}/attiva"
                pezzi = "".join(p.value if isinstance(p, _ast.Constant) else "{}" for p in dove.values)
                fuori.add(_re.sub(r"\{\}", "<x>", pezzi))
        return fuori

    def test_every_post_route_of_the_node_can_be_asked_from_mirto(self):
        del_nodo = self.rotte_del_nodo()
        self.assertGreater(len(del_nodo), 10, "lette troppo poche rotte: il guardiano e' cieco")
        da_mirto = self.rotte_che_mirto_chiama()
        # «/azioni/<nome>/attiva» dal lato nodo e' una forma, non una costante: la si riconosce a parte.
        con_nome = {r for r in da_mirto if "<x>" in r}
        mancanti = sorted(r for r in del_nodo - da_mirto - set(self.ALTROVE)
                          if not (len(r.split("/")) == 4 and con_nome))
        self.assertEqual(mancanti, [], "il nodo sa fare cose che Mirto non sa chiedere, quindi si "
                                       "fanno solo da terminale (N-07):\n  " + "\n  ".join(mancanti))

    def test_the_guard_sees_a_route_left_behind(self):
        """Prima di fidarsi: se si toglie una chiamata al client, il guardiano se ne accorge."""
        del_nodo = self.rotte_del_nodo()
        self.assertIn("/configura/disco", del_nodo)
        finto = self.rotte_che_mirto_chiama() - {"/configura/disco"}
        self.assertIn("/configura/disco", del_nodo - finto - set(self.ALTROVE),
                      "togliendo la chiamata, la rotta deve risultare mancante")

    def test_every_capability_of_the_client_is_used_by_somebody(self):
        """Un metodo che solo il client conosce e' una capacita' che non sta in nessuna schermata."""
        import ast as _ast
        import re as _re
        from pathlib import Path as _Path

        radice = _Path(__file__).resolve().parents[1]
        src = (radice / "mirto" / "actions" / "nodes.py").read_text(encoding="utf-8")
        cls = next((n for n in _ast.walk(_ast.parse(src))
                    if isinstance(n, _ast.ClassDef) and n.name == "NodeClient"), None)
        self.assertIsNotNone(cls, "«NodeClient» non si chiama piu' cosi': il guardiano e' cieco")
        metodi = [f.name for f in cls.body
                  if isinstance(f, _ast.FunctionDef) and not f.name.startswith("_")]
        self.assertGreater(len(metodi), 20, "letti troppi pochi metodi: il guardiano e' cieco")
        altrove = "".join(f.read_text(encoding="utf-8") for f in radice.glob("mirto/**/*.py")
                          if f.name != "nodes.py")
        soli = [m for m in metodi if not _re.search(r"\." + _re.escape(m) + r"\b", altrove)]
        self.assertEqual(soli, [], "il client sa fare cose che nessuna parte di Mirto chiede, "
                                   "quindi non stanno in nessuna schermata (N-07):\n  " + "\n  ".join(soli))

    def test_the_one_exception_is_really_called_somewhere_else(self):
        """L'eccezione non e' un permesso: e' un rimando, e il posto a cui rimanda deve esistere.

        Un elenco di eccezioni che nessuno verifica diventa il posto dove si nascondono i pezzi
        dimenticati — e questo guardiano nasce proprio contro quello.
        """
        from pathlib import Path as _Path

        radice = _Path(__file__).resolve().parents[1]
        for rotta, (dove, chi) in self.ALTROVE.items():
            testo = (radice / dove).read_text(encoding="utf-8")
            self.assertIn(chi, testo, f"{rotta}: {dove} non chiama piu' {chi}")
            self.assertIn(f'"{rotta}"', testo, f"{rotta}: {dove} non la nomina piu'")


def nomi_offerti(path) -> set[str] | None:
    """I nomi che un modulo offre a chi lo importa. ``None`` se non si sa dirlo con certezza.

    Un guardiano che sbaglia e' un guardiano che si spegne: davanti a un `from x import *` qui si
    rinuncia invece di indovinare.
    """
    import ast as _ast

    try:
        albero = _ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None
    fuori: set[str] = set()
    # Anche quello che sta dentro un `if` o un `try` in cima al file conta: `theme` riprende i colori
    # da `colori` con un from-import, e in altri file ci sono import condizionali per piattaforma.
    for nodo in _ast.walk(albero):
        if isinstance(nodo, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
            fuori.add(nodo.name)
        elif isinstance(nodo, _ast.Assign):
            fuori |= {t.id for t in nodo.targets if isinstance(t, _ast.Name)}
        elif isinstance(nodo, _ast.AnnAssign) and isinstance(nodo.target, _ast.Name):
            fuori.add(nodo.target.id)
        elif isinstance(nodo, _ast.Import):
            fuori |= {a.asname or a.name.split(".")[0] for a in nodo.names}
        elif isinstance(nodo, _ast.ImportFrom):
            if any(a.name == "*" for a in nodo.names):
                return None
            fuori |= {a.asname or a.name for a in nodo.names}
    return fuori


def attributi_che_non_esistono(path, radice) -> list[str]:
    """`T.qualcosa` dove `T` e' un modulo **del progetto** che non ha quel nome.

    Guardiano #12, nato il 26/10 da una prova di finestra rossa: `prank_ui.py` chiedeva l'icona a
    `colori` (`T.icon(...)`), e `icon()` sta in `theme`. Nessuno dei guardiani di prima poteva
    vederlo: il #4 guarda i **nomi** e `T` era importato regolarmente, il #10 guarda `self.core`.
    Fra un modulo e l'altro non guardava nessuno, e in questo progetto `colori` e `theme` sono due
    moduli che si somigliano apposta — uno senza Qt per le prove che girano dove Qt non c'e'.
    """
    import ast as _ast

    try:
        albero = _ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    guai = []
    for nodo in _ast.walk(albero):
        if not isinstance(nodo, _ast.ImportFrom) or not nodo.level:
            continue
        base = path.parent
        for _ in range(nodo.level - 1):
            base = base.parent
        if nodo.module:
            base = base.joinpath(*nodo.module.split("."))
        for a in nodo.names:
            quale = base / (a.name + ".py")
            if not quale.is_file():
                continue           # non e' un modulo: e' una funzione o una classe presa da dentro
            offerti = nomi_offerti(quale)
            if offerti is None:
                continue
            alias = a.asname or a.name
            for uso in _ast.walk(albero):
                if (isinstance(uso, _ast.Attribute) and isinstance(uso.value, _ast.Name)
                        and uso.value.id == alias and not uso.attr.startswith("__")
                        and uso.attr not in offerti):
                    guai.append(f"{path.relative_to(radice)}:{uso.lineno} legge «{alias}.{uso.attr}», "
                                f"che {quale.relative_to(radice)} non ha")
    return guai


class UnModuloNonHaQuelloCheGliSiChiedeTest(unittest.TestCase):
    """Guardiano #12: `T.qualcosa` su un modulo del progetto che non ha `qualcosa`.

    Marco, 26/10: *«failed tests»*. `prank_ui.py` faceva `T.icon(...)` con `T` = `colori`, e `icon()`
    sta in `theme`: la finestra Prank andava in errore aprendosi con la modalita' accesa. Tutta la
    suite era verde, perche' quella riga gira solo dentro Qt — e nel contenitore Qt non c'e'.

    I due moduli si somigliano **apposta**: `colori` sono i colori senza Qt, cosi' i testi si provano
    dove PySide6 non e' installato, e `theme` ci aggiunge icone e fogli di stile riprendendosi i
    colori. Due moduli fatti per essere scambiabili sono due moduli che prima o poi si scambiano, e
    questa prova e' il prezzo di quella comodita'.
    """

    def test_no_module_is_asked_for_something_it_does_not_have(self):
        from pathlib import Path

        radice = Path(__file__).resolve().parents[1]
        guai = []
        for cartella in ("mirto", "node", "tools", "tests"):
            for f in sorted((radice / cartella).rglob("*.py")):
                if "__pycache__" in str(f) or "/build/" in str(f):
                    continue
                guai += attributi_che_non_esistono(f, radice)
        self.assertEqual(guai, [], "moduli a cui si chiede quello che non hanno:\n  " + "\n  ".join(guai))

    def test_the_guard_sees_the_one_that_cost_a_window_test(self):
        """Prima di fidarsi: il difetto del 26/10, ricostruito, deve farlo parlare."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            radice = Path(d)
            (radice / "pacco").mkdir()
            (radice / "pacco" / "colori.py").write_text('TEXT = "#fff"\nPAUSED = "#f00"\n')
            uso = radice / "pacco" / "uso.py"
            uso.write_text("from . import colori as T\n\n\ndef f():\n"
                           '    return T.icon("x", T.TEXT, 16), T.PAUSED\n')
            guai = attributi_che_non_esistono(uso, radice)
            self.assertEqual(len(guai), 1, f"il guardiano non vede «T.icon»: {guai}")
            self.assertIn("T.icon", guai[0])
            self.assertNotIn("T.TEXT", guai[0], "quelli che ci sono non sono una lamentela")

    def test_a_star_import_makes_it_give_up_instead_of_guessing(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            radice = Path(d)
            (radice / "pacco").mkdir()
            (radice / "pacco" / "tutto.py").write_text("from os.path import *\n")
            uso = radice / "pacco" / "uso.py"
            uso.write_text("from . import tutto as T\n\n\ndef f():\n    return T.qualunque\n")
            self.assertEqual(attributi_che_non_esistono(uso, radice), [],
                             "con un «import *» non si sa cosa arriva: meglio tacere che sbagliare")


class LaTrappolaScrittaVaAncheControllataTest(unittest.TestCase):
    """`isVisible()` in una prova: la trappola era scritta dal 24/10, e il 26/10 l'ho rifatta.

    *Metodo di lavoro* la elenca fra «le trappole di una prova di finestra scritta alla cieca»: una
    prova non apre la finestra, e un figlio di una finestra che nessuno ha aperto **non e' visibile**,
    qualunque cosa gli si dica. La domanda vera e' `isHidden()`: *questa riga e' stata nascosta?*

    Scriverla non e' bastato. Una lezione che resta scritta e si rifa' ha bisogno di un meccanismo,
    non di un secondo paragrafo: tutte le prove di finestra di questo progetto usano `isHidden()`, e
    questa pretende che restino tutte cosi'.
    """

    def test_no_window_test_asks_a_widget_if_it_is_visible(self):
        import ast
        from pathlib import Path

        guai = []
        for f in sorted((Path(__file__).resolve().parents[1] / "tests").glob("*.py")):
            try:
                albero = ast.parse(f.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):
                continue
            for nodo in ast.walk(albero):
                if (isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Attribute)
                        and nodo.func.attr == "isVisible"):
                    guai.append(f"{f.name}:{nodo.lineno} chiede «isVisible()»: in una prova e' sempre "
                                f"falso (la finestra non e' aperta). Si chiede «isHidden()»")
        self.assertEqual(guai, [], "\n  " + "\n  ".join(guai))


class UnFotogrammaNonSiConverteIntero(unittest.TestCase):
    """Chi converte un fotogramma deve dire in che riquadro va a finire (Q-07, 26/10).

    Il risparmio di Q-07 sta tutto nel secondo argomento: ``frame_to_qimage(frame)`` converte due
    milioni di pixel per disegnarne diciassettemila, ``frame_to_qimage(frame, riquadro)`` no. E' il
    tipo di cosa che torna da sola -- si aggiunge un terzo riquadro, si copia la riga piu' vicina, e
    nessuno se ne accorge perche' l'immagine e' giusta: solo la CPU lo sa.

    Quindi non resta scritto in un commento: ogni chiamata deve passare il riquadro.
    """

    def test_every_conversion_says_which_box_the_frame_goes_into(self):
        import ast
        from pathlib import Path

        radice = Path(__file__).resolve().parents[1] / "mirto"
        guai = []
        for f in sorted(radice.rglob("*.py")):
            try:
                albero = ast.parse(f.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):
                continue
            for nodo in ast.walk(albero):
                if not (isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Name)
                        and nodo.func.id == "frame_to_qimage"):
                    continue
                if len(nodo.args) < 2 and not any(k.arg == "dentro" for k in nodo.keywords):
                    guai.append(f"{f.name}:{nodo.lineno} converte tutto il fotogramma: va passato il "
                                f"riquadro in cui finisce (vedi mirto/anteprime.py)")
        self.assertEqual(guai, [], "\n  " + "\n  ".join(guai))

    def test_the_guard_sees_the_call_it_was_born_for(self):
        import ast

        albero = ast.parse("pix = QPixmap.fromImage(frame_to_qimage(frame))")
        chiamate = [n for n in ast.walk(albero) if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Name) and n.func.id == "frame_to_qimage"]
        self.assertEqual(len(chiamate), 1)
        self.assertEqual(len(chiamate[0].args), 1, "era la riga di prima di Q-07")

    def test_the_preview_does_not_go_back_to_the_textbook_choice(self):
        """INTER_AREA e' la scelta da manuale per rimpicciolire: qui e' un peggioramento.

        Misurato il 26/10 su un fotogramma 1920x1080: AREA 7,6 ms, LINEAR 0,16 ms, mentre la vecchia
        conversione intera ne costava 4,7. Con AREA, Q-07 *aumenta* il lavoro sul thread della
        finestra invece di ridurlo. E' esattamente il genere di riga che qualcuno rimette «a posto»
        leggendo un manuale, quindi la pretende una prova e non solo un commento.
        """
        import ast
        from pathlib import Path

        sorgente = (Path(__file__).resolve().parents[1] / "mirto" / "gui" / "widgets.py")
        albero = ast.parse(sorgente.read_text(encoding="utf-8"))
        funzione = next(n for n in ast.walk(albero) if isinstance(n, ast.FunctionDef)
                        and n.name == "frame_to_qimage")
        interpolazioni = [k.value.attr for n in ast.walk(funzione)
                          if isinstance(n, ast.Call) for k in n.keywords
                          if k.arg == "interpolation" and isinstance(k.value, ast.Attribute)]
        self.assertEqual(interpolazioni, ["INTER_LINEAR"],
                         "AREA costa 7,6 ms contro 0,16: con quella Q-07 peggiora invece di migliorare")
