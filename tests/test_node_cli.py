# SPDX-License-Identifier: Apache-2.0
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "node"))

from mirto_node import cli  # noqa: E402
from mirto_node import audio as node_audio  # noqa: E402
from mirto_node import mixer  # noqa: E402
from mirto_node.audio import AudioPlayer, alsa_cards, alsa_device, chime_pcm  # noqa: E402


def solo_sul_pinguino(test) -> None:
    """Fa saltare la prova dove i copioni del nodo non possono girare.

    Sono scritti per il Raspberry, cioè per Linux: usano «sed -i» senza argomento (su macOS il sed di
    BSD vuole «sed -i \'\'» e fallisce), i permessi di Unix e un bash vero. Provarli altrove non dice
    niente sul nodo e fa solo diventare rossa la CI — successo il 20/10 sera, due volte.
    """
    if not sys.platform.startswith("linux"):
        test.skipTest("i copioni del nodo girano su Linux (qui sed, permessi e shell sono altri)")


def bash_che_funziona(candidati=None, prova=None):
    """Il primo bash che esegue davvero un comando, oppure None.

    Su Windows il ``bash`` che si trova nel PATH può essere quello del sottosistema
    Linux senza nessuna distribuzione installata: il programma c'è, parte, stampa
    le istruzioni per installarne una e fallisce. Cercare solo il nome non basta.
    """
    if candidati is None:
        candidati = [os.environ.get("MIRTO_BASH"), shutil.which("bash"),
                     r"C:\Program Files\Git\bin\bash.exe"]
    if prova is None:
        def prova(eseguibile):
            return subprocess.run([eseguibile, "-c", "exit 0"], capture_output=True).returncode
    for eseguibile in candidati:
        if not eseguibile:
            continue
        try:
            if prova(eseguibile) == 0:
                return eseguibile
        except OSError:
            continue
    return None


PI4_CARDS = """ 0 [Headphones     ]: bcm2835_headpho - bcm2835 Headphones
                      bcm2835 Headphones
 1 [vc4hdmi0       ]: vc4-hdmi - vc4-hdmi-0
                      vc4-hdmi-0
 2 [vc4hdmi1       ]: vc4-hdmi - vc4-hdmi-1
                      vc4-hdmi-1
 3 [Device         ]: USB-Audio - USB Audio Device
                      C-Media Electronics Inc. USB Audio Device at usb-0000:01:00.0-1.3, full speed
"""

TOML = '''# commento iniziale
nome = "Ingresso"
porta = 8443

[audio]
# dove suona
uscita = "bluetooth"
bluetooth_mac = ""

[camera]
attiva = true
noir = false
'''


class CassaBluetoothTest(unittest.TestCase):
    """Un legame perso non è una cassa lontana — ma «Paired: no» non vuol dire legame perso.

    Il 18/10, sul nodo vero: `Paired: no`, `Trusted: yes`, collegamento che muore con
    `br-connection-unknown`, e il nodo che diceva «cassa Bluetooth spenta o lontana». Si è aspettato
    per niente, e la regola scritta allora fu: senza `Paired: yes` non provare nemmeno.

    Il 21/10, sulla stessa JBL Flip 6, quella regola si è rivelata sbagliata: la cassa si è collegata e
    ha suonato con `Paired: no` e `Trusted: yes` — le casse A2DP spesso non lasciano un legame salvato.
    Chi decide, adesso, è **l'errore del collegamento**: `br-connection-unknown` vuol dire legame rotto
    (e lo rifà una persona dal pannello), tutto il resto è una cassa da riprovare.
    """

    MAC = "90:F2:60:38:E2:3D"

    def player(self, info: str, connect: str = "Connection successful", **kw):
        chiamate = []

        def run(args, **_kw):
            chiamate.append(list(args))
            testo = connect if len(args) > 1 and args[1] == "connect" else info

            class Risposta:
                stdout = testo
                stderr = ""
            return Risposta()

        p = AudioPlayer(output="bluetooth", bt_mac=self.MAC, run=run, cards=lambda: [], **kw)
        return p, chiamate

    SCOLLEGATA = "\tName: JBL Flip 6\n\tPaired: yes\n\tTrusted: yes\n\tConnected: no\n"
    NON_ABBINATA = "\tName: JBL Flip 6\n\tPaired: no\n\tTrusted: yes\n\tConnected: no\n"
    COLLEGATA = "\tName: JBL Flip 6\n\tPaired: yes\n\tTrusted: yes\n\tConnected: yes\n"

    def test_a_lost_bond_is_said_out_loud_with_the_remedy(self):
        """Il legame rotto lo dice l'errore del collegamento, non il campo «Paired»."""
        p, _ = self.player(self.NON_ABBINATA, connect="Failed to connect: br-connection-unknown")
        self.assertTrue(p.ready(), "intanto la sirena suona dall'uscita di sistema")
        self.assertIn("abbinata", p.last_error)
        self.assertIn("Audio del nodo", p.last_error, "il rimedio è nel pannello, senza terminale")
        self.assertNotIn("lontana", p.last_error, "era il messaggio che faceva aspettare per niente")
        self.assertFalse(p.status()["collegata"])

    def test_a_speaker_that_works_without_a_bond_is_not_declared_lost(self):
        """La prova del 21/10: «Paired: no, Trusted: yes» e la cassa suona lo stesso."""
        p, chiamate = self.player(self.NON_ABBINATA)
        p.ready()
        self.assertIn("connect", [c[1] for c in chiamate if len(c) > 1],
                      "una cassa che il nodo conosce si prova: «Paired: no» non è una condanna")
        self.assertNotIn("non più abbinata", p.last_error)
        self.assertTrue(p.status()["abbinata"], "il nodo la conosce: è «Trusted»")

    def test_a_speaker_bluez_has_forgotten_is_looked_for_again(self):
        """«not available» vuol dire che BlueZ non ce l'ha in mano: una ricerca corta, e si riprova."""
        p, chiamate = self.player(self.NON_ABBINATA, connect="Device 90:F2:60:38:E2:3D not available")
        p.ready()
        fatti = [" ".join(c) for c in chiamate]
        self.assertIn("bluetoothctl --timeout 10 scan on", fatti, "prima di riprovare si guarda in giro")
        self.assertEqual(len([f for f in fatti if "connect" in f]), 2, "e il collegamento si ritenta")

    def test_a_speaker_merely_switched_off_is_still_described_as_before(self):
        p, chiamate = self.player(self.SCOLLEGATA)
        self.assertTrue(p.ready())
        self.assertIn("connect", [c[1] for c in chiamate if len(c) > 1], "qui il tentativo ha senso")
        self.assertIn("spenta o lontana", p.last_error)
        self.assertTrue(p.status()["abbinata"], "abbinata sì, collegata no: sono due cose diverse")

    def test_a_disconnected_speaker_is_retried_within_a_minute(self):
        """Quattro minuti sono troppi per una cassa che deve suonare durante un'intrusione."""
        p, _ = self.player(self.SCOLLEGATA)
        self.assertEqual(p._pausa_sveglia(), node_audio.RIPROVA_SCOLLEGATA)
        self.assertLessEqual(node_audio.RIPROVA_SCOLLEGATA, 60)

    def test_a_connected_speaker_is_left_alone(self):
        p, _ = self.player(self.COLLEGATA)
        self.assertEqual(p._pausa_sveglia(), p.keep_alive * 60, "collegata: si tocca solo per tenerla sveglia")

    def test_a_speaker_with_a_broken_bond_is_not_hammered_every_minute(self):
        p, _ = self.player(self.NON_ABBINATA, connect="Failed to connect: br-connection-unknown")
        p.ready()
        self.assertEqual(p._pausa_sveglia(), p.keep_alive * 60,
                         "riprovare non serve: serve che qualcuno rifaccia l'abbinamento")

    def test_a_speaker_merely_unpaired_is_still_retried_within_a_minute(self):
        p, _ = self.player(self.NON_ABBINATA)
        self.assertEqual(p._pausa_sveglia(), node_audio.RIPROVA_SCOLLEGATA,
                         "il nodo la conosce: una sirena muta per quattro minuti non è una sirena")

    def test_without_bluetoothctl_nothing_is_claimed(self):
        """Se non si riesce a leggere lo stato, non si accusa l'abbinamento: si prova e si vede."""
        p, chiamate = self.player("")
        p.ready()
        self.assertIn("connect", [c[1] for c in chiamate if len(c) > 1])
        self.assertNotIn("abbinata", p.last_error)
        self.assertNotIn("abbinata", p.status())


class AlsaCardsTest(unittest.TestCase):
    def test_types(self):
        cards = alsa_cards(PI4_CARDS)
        self.assertEqual([(c["id"], c["tipo"]) for c in cards],
                         [("Headphones", "jack"), ("vc4hdmi0", "hdmi"), ("vc4hdmi1", "hdmi"), ("Device", "usb")])
        self.assertEqual(cards[3]["nome"], "USB Audio Device")

    def test_device_names(self):
        self.assertEqual(alsa_device("jack", "Headphones"), "plughw:CARD=Headphones,DEV=0")
        self.assertEqual(alsa_device("usb", "Device"), "plughw:CARD=Device,DEV=0")
        self.assertEqual(alsa_device("hdmi", "vc4hdmi0"), "hdmi:CARD=vc4hdmi0,DEV=0")
        self.assertEqual(alsa_device("bluetooth", "", "AA:BB:CC:DD:EE:FF"), "bluealsa:DEV=AA:BB:CC:DD:EE:FF,PROFILE=a2dp")
        self.assertEqual(alsa_device("predefinito", ""), "default")
        self.assertEqual(alsa_device("usb", ""), "default")

    def test_wired_player_ready_and_status(self):
        cards = lambda: alsa_cards(PI4_CARDS)  # noqa: E731
        p = AudioPlayer(output="usb", card="Device", cards=cards)
        self.assertTrue(p.ready())
        st = p.status()
        self.assertEqual((st["uscita"], st["scheda"], st["collegata"], st["nome"]),
                         ("usb", "Device", True, "USB Audio Device"))
        gone = AudioPlayer(output="usb", card="Altra", cards=cards)
        self.assertTrue(gone.ready(), "la scheda non c'è, ma c'è un ripiego: vedi il test qui sotto")
        self.assertIn("non trovata", gone.last_error, "il motivo resta scritto")
        self.assertFalse(gone.status()["collegata"], "e «collegata» resta falso: il guasto non si nasconde")

    def test_a_siren_is_never_silent_the_jack_takes_over(self):
        """Cassa Bluetooth spenta: la sirena deve suonare comunque, dal jack.

        Chiesto guardando la casa vera: la JBL si spegne, si scarica, o la ruba il telefono. Una
        sirena che tace perché una cassa non si è collegata non è una sirena.
        """
        cards = lambda: alsa_cards(PI4_CARDS)  # noqa: E731
        class Spenta:
            stdout = "Connected: no"
        p = AudioPlayer(output="bluetooth", bt_mac="AA:BB:CC:DD:EE:FF", cards=cards,
                        run=lambda *a, **k: Spenta())
        self.assertTrue(p.ready(), "senza la cassa deve trovare un'altra uscita, non arrendersi")
        self.assertEqual(p.device, "plughw:CARD=Headphones,DEV=0", "il jack del Raspberry")
        self.assertIn("jack", p.ripiego, f"deve dire da dove sta suonando: {p.ripiego!r}")
        st = p.status()
        self.assertFalse(st["collegata"], "la cassa resta non collegata: non si nasconde il problema")
        self.assertEqual(st["uscita_in_uso"], "jack")

    def test_when_the_speaker_answers_the_fallback_steps_aside(self):
        cards = lambda: alsa_cards(PI4_CARDS)  # noqa: E731
        risposte = ["Connected: no", "Connected: yes"]
        class Finta:
            def __init__(self, testo):
                self.stdout = testo
        p = AudioPlayer(output="bluetooth", bt_mac="AA:BB:CC:DD:EE:FF", cards=cards,
                        run=lambda *a, **k: Finta(risposte[0]))
        self.assertTrue(p.ready())
        self.assertEqual(p.status()["uscita_in_uso"], "jack")
        risposte[0] = "Connected: yes"                      # la cassa torna
        self.assertTrue(p.ready())
        self.assertEqual(p.device, "bluealsa:DEV=AA:BB:CC:DD:EE:FF,PROFILE=a2dp")
        self.assertEqual(p.ripiego, "", "tornata la cassa, il ripiego non si annuncia più")

    def test_without_any_card_it_still_tries_the_default_output(self):
        class Spenta:
            stdout = "Connected: no"
        p = AudioPlayer(output="bluetooth", bt_mac="AA:BB:CC:DD:EE:FF", cards=lambda: [],
                        run=lambda *a, **k: Spenta())
        self.assertTrue(p.ready(), "meglio provare l'uscita di sistema che restare muti")
        self.assertEqual(p.device, "default")

    def test_a_wired_output_that_disappears_falls_back_too(self):
        """Non è un privilegio del Bluetooth: anche una scheda USB staccata ha un ripiego."""
        cards = lambda: alsa_cards(PI4_CARDS)  # noqa: E731
        p = AudioPlayer(output="usb", card="Sparita", cards=cards)
        self.assertTrue(p.ready())
        self.assertEqual(p.device, "plughw:CARD=Headphones,DEV=0")
        self.assertIn("non trovata", p.status().get("ultimo_errore", ""),
                      "il motivo del ripiego resta scritto")

    def test_unknown_output_falls_back(self):
        self.assertEqual(AudioPlayer(output="boh").output, "predefinito")

    def test_chime(self):
        pcm = chime_pcm()
        self.assertGreater(len(pcm), 22050)
        self.assertEqual(len(pcm) % 2, 0)


class TomlEditTest(unittest.TestCase):
    def test_replace_keeps_comments(self):
        out = cli.set_toml_values(TOML, "audio", {"uscita": "jack", "scheda": "Headphones"})
        self.assertIn("# dove suona\nuscita = \"jack\"\nbluetooth_mac = \"\"\nscheda = \"Headphones\"\n\n[camera]", out)
        self.assertIn("# commento iniziale", out)

    def test_values_and_new_section(self):
        out = cli.set_toml_values(TOML, "camera", {"noir": True, "larghezza": 1920})
        self.assertIn("noir = true\nlarghezza = 1920\n", out)
        out = cli.set_toml_values(out, "rete", {"nome": 'a"b'})
        self.assertTrue(out.endswith('\n[rete]\nnome = "a\\"b"\n'))
        top = cli.set_toml_values(TOML, None, {"porta": 9443})
        self.assertIn("porta = 9443\n", top)

    def test_result_is_valid_toml(self):
        try:
            import tomllib
        except ImportError:
            self.skipTest("tomllib non disponibile")
        out = cli.set_toml_values(TOML, "audio", {"uscita": "usb", "scheda": "Device"})
        cfg = tomllib.loads(out)
        self.assertEqual(cfg["audio"]["scheda"], "Device")
        self.assertEqual(cfg["camera"]["attiva"], True)


class ParsersTest(unittest.TestCase):
    def test_bt_devices(self):
        text = ("Device AA:BB:CC:00:11:22 JBL Flip 6\nDevice 11:22:33:44:55:66 11-22-33-44-55-66\n"
                "[NEW] Device AA:BB:CC:00:11:22 JBL Flip 6\n")
        self.assertEqual(cli.parse_bt_devices(text),
                         [("AA:BB:CC:00:11:22", "JBL Flip 6"), ("11:22:33:44:55:66", "")])

    SCANSIONE = """SetDiscoveryFilter success
Discovery started
[NEW] Device 41:A8:E2:A2:ED:09 41-A8-E2-A2-ED-09
[NEW] Device FF:02:3C:D5:9A:7E BLE MF8470
[NEW] Device 28:6B:B4:65:0D:A2 28-6B-B4-65-0D-A2
[NEW] Device 90:F2:60:38:E2:3D JBL Flip 6
[CHG] Device 90:F2:60:38:E2:3D Connected: yes
[DEL] Device 90:F2:60:38:E2:3D JBL Flip 6
[CHG] Device 41:A8:E2:A2:ED:09 RSSI: 0xffffffc4 (-60)
"""

    def test_what_passed_by_during_the_search(self):
        """Preso dalla ricerca vera del 21/10: la cassa c'è, e trenta secondi dopo l'elenco non l'ha più."""
        visti = cli.casse_da_scansione(self.SCANSIONE)
        self.assertIn(("90:F2:60:38:E2:3D", "JBL Flip 6"), visti, "la cassa si prende al volo, prima del [DEL]")
        self.assertEqual([m for m, _n in visti], ["FF:02:3C:D5:9A:7E", "90:F2:60:38:E2:3D"],
                         "i senza nome non si propongono nemmeno")

    def test_only_what_can_be_a_speaker_is_offered(self):
        casuale = "Device FF:02:3C:D5:9A:7E (random)\n\tName: BLE MF8470\n\tPaired: no\n"
        pubblico = "Device 90:F2:60:38:E2:3D (public)\n\tName: JBL Flip 6\n\tPaired: no\n"
        self.assertFalse(cli.pare_una_cassa("FF:02:3C:D5:9A:7E", "BLE MF8470", casuale),
                         "indirizzo BLE casuale: l'audio non ci passa")
        self.assertTrue(cli.pare_una_cassa("90:F2:60:38:E2:3D", "JBL Flip 6", pubblico))
        self.assertTrue(cli.pare_una_cassa("90:F2:60:38:E2:3D", "", "Device 90:F2:60:38:E2:3D (public)\n"
                                           "\tPaired: yes\n"), "quella già abbinata resta, anche senza nome")
        self.assertFalse(cli.pare_una_cassa("28:6B:B4:65:0D:A2", "", "Device 28:6B:B4:65:0D:A2 (public)\n"),
                         "senza nome non si sa nemmeno cosa sia")
        self.assertTrue(cli.pare_una_cassa("90:F2:60:38:E2:3D", "JBL Flip 6", ""),
                        "se bluetoothctl non risponde si tiene: meglio una riga in più che perdere la cassa")

    def test_wifi_list(self):
        text = (" :CasaMia:62:WPA2\n*:CasaMia:80:WPA2\n :Ospiti\\:5G:40:WPA2 WPA3\n ::30:WPA2\n :Bar:20:\n")
        nets = cli.parse_wifi_list(text)
        self.assertEqual([n["ssid"] for n in nets], ["CasaMia", "Ospiti:5G", "Bar"])
        self.assertTrue(nets[0]["in_uso"])
        self.assertEqual(nets[0]["segnale"], 80)
        self.assertEqual(nets[2]["sicurezza"], "aperta")

    def test_audio_choices(self):
        ch = cli.audio_choices(alsa_cards(PI4_CARDS))
        self.assertEqual([(c["uscita"], c["scheda"]) for c in ch],
                         [("bluetooth", ""), ("jack", "Headphones"), ("usb", "Device"), ("hdmi", "vc4hdmi0"),
                          ("hdmi", "vc4hdmi1"), ("predefinito", "")])


class ImageScriptsTest(unittest.TestCase):
    NODE = Path(__file__).resolve().parents[1] / "node"

    def test_host_slug(self):
        self.assertEqual(cli.host_slug("Garage Nord"), "garage-nord")
        self.assertEqual(cli.host_slug("  Città/Ingresso! "), "citta-ingresso")
        self.assertEqual(cli.host_slug("???"), "")

    SCRIPT = ("install.sh", "firstboot.sh", "prepare_image.sh", "make_public_image.sh")

    def test_scripts_exist_and_parse(self):
        for name in self.SCRIPT:
            self.assertTrue((self.NODE / name).exists(), name)
        bash = bash_che_funziona()
        if bash is None:
            self.skipTest("bash non disponibile")
        for name in self.SCRIPT:
            self.assertEqual(subprocess.run([bash, "-n", str(self.NODE / name)]).returncode, 0, name)

    def test_install_ships_image_scripts_and_quiet_first_boot(self):
        text = (self.NODE / "install.sh").read_text()
        self.assertIn('"$HERE/firstboot.sh" "$HERE/prepare_image.sh"', text)
        self.assertIn("mirto-firstboot.service", text)
        self.assertIn("Before=ssh.service", text)
        # durante il primo avvio nessun servizio va avviato o fermato direttamente (si bloccherebbe l'avvio)
        body = text.split('HERE="$(cd', 1)[1]
        self.assertNotIn("systemctl restart", body)
        self.assertNotIn("systemctl stop", body)

    def test_cleanup_removes_secrets_and_powers_off(self):
        text = (self.NODE / "prepare_image.sh").read_text()
        for what in ("chiave", "node.key", "rtsp", "abbinato", "ssh_host_", "machine-id", "802-11-wireless",
                     "primo-avvio", "poweroff"):
            self.assertIn(what, text)
        self.assertLess(text.index("802-11-wireless"), text.index("poweroff"))


class PublicImageTest(unittest.TestCase):
    NODE = Path(__file__).resolve().parents[1] / "node"

    def test_public_image_removes_people_and_checks(self):
        text = (self.NODE / "make_public_image.sh").read_text()
        for what in ("userdel", "var/lib/cloud", "ssh_host_", "machine-id",
                     "resize2fs -M", "sha256sum", "Controlli NON superati"):
            self.assertIn(what, text)

    def test_the_public_image_carries_no_password_at_all(self):
        """S-05: l'immagine pubblica non contiene credenziali note.

        Prima l'utente nasceva con `plain_text_passwd: mirto` e `passwd --expire`: la scadenza obbliga a
        cambiarla *al primo accesso*, ma il primo ad accedere poteva essere chiunque fosse sulla rete.
        Con cinque nodi in una casa — e cinquanta dai tester — sarebbe stata una porta aperta per ciascuno.
        Ora l'utente nasce bloccato e la password la genera il nodo al primo avvio.
        """
        text = (self.NODE / "make_public_image.sh").read_text()
        self.assertNotIn("plain_text_passwd: mirto", text, "password nota dentro l'immagine")
        self.assertNotIn("passwd, --expire", text, "non serve far scadere una password che non esiste")
        self.assertIn("lock_passwd: true", text)
        self.assertIn('grep -qE "plain_text_passwd', text,
                      "l'immagine deve controllare da sé di non portarsi via una password")

    def test_the_first_boot_invents_a_password_and_writes_it_on_the_sd(self):
        text = (self.NODE / "firstboot.sh").read_text()
        for what in ("openssl rand -hex", "chpasswd", 'BOOT="${MIRTO_BOOT:-/boot/firmware}"',
                     'ACCESSO="$BOOT/mirto-accesso.txt"', 'passwd -S "$UTENTE"'):
            self.assertIn(what, text, f"manca «{what}» nel primo avvio")
        self.assertIn('STATO" = "L"', text,
                      "una password scelta in Raspberry Pi Imager non si sovrascrive")

    def test_two_nodes_do_not_get_the_same_password(self):
        """La riga che genera la password è presa dal file: se cambia, cambia anche questa prova.

        Il bash va cercato, non chiamato per nome: su Windows il primo «bash» del PATH è quello del
        sottosistema Linux senza nessuna distribuzione installata, che parte, stampa le istruzioni per
        installarne una e fallisce. È esattamente perché questa riga diceva ["bash", …] che la CI è
        rimasta rossa su Windows dal pomeriggio del 20/10 al mattino dopo.
        """
        import re
        import subprocess

        bash = bash_che_funziona()
        if bash is None:
            self.skipTest("bash non disponibile")
        if subprocess.run([bash, "-c", "command -v openssl && command -v sed"],
                          capture_output=True).returncode != 0:
            self.skipTest("servono openssl e sed, che sul Raspberry ci sono")
        riga = next(r.strip() for r in (self.NODE / "firstboot.sh").read_text().splitlines()
                    if r.strip().startswith("PAROLA="))
        fatte = set()
        for _ in range(3):
            script = riga + '\nprintf "%s" "$PAROLA"'
            res = subprocess.run([bash, "-c", script], capture_output=True, text=True, check=True)
            parola = res.stdout.strip()
            self.assertRegex(parola, r"^[0-9a-f]{6}-[0-9a-f]{6}-[0-9a-f]{6}$", f"formato inatteso: {parola}")
            fatte.add(parola)
        self.assertEqual(len(fatte), 3, "tre nodi hanno avuto la stessa password")
        self.assertGreaterEqual(len(re.sub(r"-", "", fatte.pop())), 18, "meno di 72 bit di casualità")

    def test_the_access_sheet_says_to_keep_it_and_how_to_get_a_new_pin(self):
        """Il foglio che viaggia col nodo è l'unica risposta a «ho perso il PIN».

        Prima diceva di cancellare il file quando non serve più: ma quella password è il solo modo di
        rientrare in un nodo che ha perso il PIN e non è ancora abbinato — altrimenti si riscrive la
        microSD. Deciso il 20/10 col piano plug & play, insieme alle etichette da stampare.
        """
        text = (self.NODE / "firstboot.sh").read_text(encoding="utf-8")
        foglio = text.split("Mirto Node - accesso a questo nodo", 1)[1].split("TXT", 1)[0]
        self.assertIn("CONSERVALA", foglio)
        self.assertIn("mirto-nodo abbina", foglio, "come farsi un PIN nuovo")
        self.assertNotIn("cancella questo file", foglio)

    def test_firstboot_respects_imager_name_and_grows(self):
        text = (self.NODE / "firstboot.sh").read_text()
        self.assertIn('"$OLD" = "mirto-node"', text, "la parte fissa del nome di un nodo nuovo")
        self.assertIn('"$OLD" = "mirto-nodo"', text, "anche le immagini preparate prima del 20/10")
        self.assertIn('NEW="mirto-node-$SUF"', text)
        self.assertIn("resize2fs", text)
        self.assertIn("--no-block restart avahi-daemon", text)


if __name__ == "__main__":
    unittest.main()


class AbbinamentoCassaTest(unittest.TestCase):
    """L'abbinamento di una cassa dalla finestra, com'è venuto fuori provandolo sul Pi 4 il 21/10.

    Tutto quello che si lanciava un comando per volta moriva con «Device … not available»: fra un
    `bluetoothctl` e l'altro BlueZ butta via una cassa non abbinata. Quello che funziona è una sola
    sessione, con la ricerca attiva e i tempi d'attesa dentro — e la cassa si collega con `Paired: no`,
    quindi il successo non si misura sull'abbinamento ma sul collegamento.
    """

    MAC = "90:F2:60:38:E2:3D"

    info_finta = "Device 90:F2:60:38:E2:3D (public)\n\tPaired: no\n\tConnected: no\n"

    def nodo(self, registri, collegata):
        """Sostituisce i comandi di sistema. `registri`: cosa stampa ogni sessione di bluetoothctl.

        `collegata`: la risposta di `bluetoothctl info` dopo ogni sessione, una per volta.
        """
        import tempfile
        import types
        from pathlib import Path as P

        from mirto_node import cli

        chiamate, coda_registri, coda_esiti = [], list(registri), list(collegata)

        def finto_sh(cmd, timeout=30, check=False):
            chiamate.append(list(cmd))
            testo = ""
            if cmd[:2] == ["bluetoothctl", "info"]:
                testo = self.info_finta
            return types.SimpleNamespace(returncode=0, stdout=testo, stderr="")

        def finta_sessione(passi):
            # Dall'8/11 ogni passo porta anche il **segno** che dice quando e' finito: i tempi sono
            # massimi, non attese. Qui serve solo il comando.
            chiamate.append(["bluetoothctl-sessione"] + [passo[0] for passo in passi])
            return coda_registri.pop(0) if coda_registri else ""

        def finto_collegata(_mac):
            return coda_esiti.pop(0) if coda_esiti else False

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for nome, valore in (("sh", finto_sh), ("_sessione_bluetoothctl", finta_sessione),
                             ("_collegata", finto_collegata), ("CONF", P(tmp.name)),
                             ("write_cfg", lambda *a, **k: None), ("restart", lambda *a: True),
                             ("time", types.SimpleNamespace(sleep=lambda *_a: None, time=lambda: 0))):
            self.addCleanup(setattr, cli, nome, getattr(cli, nome))
            setattr(cli, nome, valore)
        return cli, chiamate, P(tmp.name)

    def esito(self, conf) -> dict:
        import json

        return json.loads((conf / "audio-esito.json").read_text())

    def test_everything_happens_in_one_session_of_bluetoothctl(self):
        """Un comando per volta non funziona: fra l'uno e l'altro la cassa non esiste più."""
        from mirto_node import cli

        passi = [passo[0] for passo in cli.passi_abbinamento(self.MAC)]
        self.assertEqual(passi[:5], ["power on", "agent NoInputNoOutput", "default-agent",
                                     "pairable on", "scan on"])
        self.assertEqual(passi[5:], [f"pair {self.MAC}", f"trust {self.MAC}", f"connect {self.MAC}",
                                     "quit"], "prima si cerca, poi si collega")
        # Dall'8/11 il numero accanto al passo è un **massimo**, non un'attesa: deve restare largo
        # (alla cassa servono una ventina di secondi per farsi vedere) ma accanto deve esserci il
        # segno da sorvegliare, altrimenti quel massimo torna a essere un'attesa e si paga sempre.
        scan = next(p for p in cli.passi_abbinamento(self.MAC) if p[0] == "scan on")
        self.assertGreaterEqual(scan[1], 12,
                                "alla cassa servono una ventina di secondi per farsi vedere")
        self.assertTrue(scan[2], "senza segno da aspettare il massimo diventa un'attesa fissa")
        riga = f"[NEW] Device {self.MAC.lower()} Cassa del salotto"
        self.assertTrue(re.search(scan[2], riga, re.I),
                        "il segno non riconosce la riga con cui bluetoothctl annuncia la cassa")
        lunghi = [p for p in cli.passi_abbinamento(self.MAC) if p[1] > 3]
        self.assertTrue(all(p[2] for p in lunghi),
                        f"passi lunghi senza segno: {[p[0] for p in lunghi if not p[2]]}")

    def test_a_speaker_that_connects_without_pairing_counts_as_done(self):
        """La JBL si è collegata con «Paired: no»: il successo è il collegamento, non l'abbinamento."""
        cli, chiamate, conf = self.nodo(["pair ...\nDevice not available\nconnect: Connection successful"],
                                        [True])
        self.assertEqual(cli._abbina_cassa(self.MAC), 0)
        self.assertEqual(len([c for c in chiamate if c[0] == "bluetoothctl-sessione"]), 1)
        self.assertEqual(self.esito(conf)["esito"], "ok")

    def test_a_dead_bond_is_redone_without_a_terminal(self):
        cli, chiamate, conf = self.nodo(["Failed to connect: br-connection-unknown", "Connection successful"],
                                        [False, True])
        self.assertEqual(cli._abbina_cassa(self.MAC), 0)
        fatti = [" ".join(c) for c in chiamate if c[0] == "bluetoothctl"]
        self.assertIn(f"bluetoothctl remove {self.MAC}", fatti, "il legame morto si butta")
        self.assertEqual(len([c for c in chiamate if c[0] == "bluetoothctl-sessione"]), 2,
                         "e si rifà tutta la sequenza")
        self.assertEqual(self.esito(conf)["esito"], "ok")

    def test_a_speaker_that_is_simply_off_keeps_its_bond(self):
        """Se la cassa è spenta, buttare il legame peggiorerebbe le cose: non si tocca."""
        cli, chiamate, conf = self.nodo(["Failed to connect: le-connection-abort-by-local"], [False])
        self.assertEqual(cli._abbina_cassa(self.MAC), 1)
        fatti = [" ".join(c) for c in chiamate if c[0] == "bluetoothctl"]
        self.assertNotIn(f"bluetoothctl remove {self.MAC}", fatti)
        esito = self.esito(conf)
        self.assertEqual(esito["esito"], "errore")
        self.assertIn("in abbinamento", esito["messaggio"], "chi legge deve sapere cosa fare")
        self.assertIn("le-connection-abort-by-local", esito["messaggio"], "e il motivo vero, per noi")

    def test_what_to_do_depends_on_whether_the_node_saw_the_speaker(self):
        """Tre guai diversi, tre gesti diversi: non una frase sola che li copre tutti male."""
        cli, _c, conf = self.nodo(["[NEW] Device 90:F2:60:38:E2:3D JBL Flip 6\n"
                                   "Failed to connect: le-connection-abort-by-local"], [False])
        cli._abbina_cassa(self.MAC)
        self.assertIn("la vede ma lei rifiuta", self.esito(conf)["messaggio"])

        cli, _c, conf = self.nodo(["Failed to connect: le-connection-abort-by-local"], [False])
        cli._abbina_cassa(self.MAC)
        self.assertIn("non la vede proprio", self.esito(conf)["messaggio"])

        cli, _c, conf = self.nodo(["[NEW] Device 90:F2:60:38:E2:3D JBL Flip 6\n"
                                   "hci0 90:F2:60:38:E2:3D type BR/EDR connect failed (status 0x04, "
                                   "Connect Failed)\nFailed to connect: br-connection-page-timeout"], [False])
        cli._abbina_cassa(self.MAC)
        messaggio = self.esito(conf)["messaggio"]
        self.assertIn("non risponde alla chiamata", messaggio,
                      "si vede ma non risponde: è agganciata a un altro dispositivo (21/10 sera)")
        self.assertIn("collegata a un telefono o a un computer", messaggio)

        cli, _c, conf = self.nodo(["Failed to connect: br-connection-unknown"] * 2, [False, False])
        cli._abbina_cassa(self.MAC)
        messaggio = self.esito(conf)["messaggio"]
        self.assertIn("ha dato il posto a un altro dispositivo", messaggio,
                      "è il caso del telefono che si abbina e prende il posto del nodo (21/10)")
        self.assertIn("mentre lampeggia", messaggio, "e il gesto che lo rimedia")

    def test_the_reason_is_fished_out_of_a_very_noisy_log(self):
        """Una sessione stampa centinaia di righe di ricerca: il motivo è l'ultima che spiega qualcosa."""
        from mirto_node import cli

        registro = ("\x1b[0;94m[bluetoothctl]> \x1b[0mscan on\n"
                    "\x1b[0;94m[bluetoothctl]> \x1b[0m[CHG] Device 41:A8 RSSI: 0xffffffc4 (-60)\n"
                    "\x1b[0;94m[bluetoothctl]> \x1b[0mpair 90:F2:60:38:E2:3D\n"
                    "Device 90:F2:60:38:E2:3D not available\n"
                    "[JBL Flip 6]> Failed to connect: org.bluez.Error.NotAvailable br-connection-unknown\n")
        self.assertEqual(cli.motivo_da_sessione(registro),
                         "Failed to connect: org.bluez.Error.NotAvailable br-connection-unknown")
        self.assertEqual(cli.motivo_da_sessione("tutto bene\nConnection successful"), "",
                         "senza errori non si inventa un motivo")

    def test_when_the_first_try_fails_the_controller_is_woken_up(self):
        """Il pezzo che mancava, trovato il 21/10 sera sul Pi 4.

        Dopo un pomeriggio di tentativi falliti il controller del Raspberry rifiuta tutto con errori
        generici, anche con la cassa resettata e a mezzo metro. Riavviato il servizio e azzerato hci0,
        la stessa sequenza ha risposto «Pairing successful» al primo colpo.
        """
        cli, chiamate, _conf = self.nodo(["Failed to connect: br-connection-unknown",
                                          "Pairing successful"], [False, True])
        self.assertEqual(cli._abbina_cassa(self.MAC), 0)
        fatti = [" ".join(c) for c in chiamate]
        self.assertIn("systemctl restart bluetooth", fatti, "il servizio si riavvia")
        self.assertIn("hciconfig hci0 reset", fatti, "e il controller si azzera")
        prima_sessione = next(i for i, c in enumerate(chiamate) if c[0] == "bluetoothctl-sessione")
        risveglio = next(i for i, c in enumerate(chiamate) if c[:2] == ["systemctl", "restart"])
        self.assertGreater(risveglio, prima_sessione, "prima si prova, poi si sveglia: costa 5 secondi")

    def test_a_speaker_paired_but_not_connected_is_called_again(self):
        """Una cassa appena abbinata chiude il collegamento da sé: va richiamata, non abbandonata."""
        self.info_finta = "Device 90:F2:60:38:E2:3D (public)\n\tPaired: yes\n\tBonded: yes\n"
        cli, chiamate, conf = self.nodo(["Pairing successful"], [False, False, True])
        self.assertEqual(cli._abbina_cassa(self.MAC), 0)
        connect = [c for c in chiamate if c[:2] == ["bluetoothctl", "connect"]]
        self.assertGreaterEqual(len(connect), 1, "il collegamento si ritenta a legame fatto")
        self.assertEqual(self.esito(conf)["esito"], "ok")

    def test_an_address_that_is_not_one_asks_nothing_to_bluetooth(self):
        cli, chiamate, conf = self.nodo([], [])
        self.assertEqual(cli._abbina_cassa("non-un-indirizzo"), 1)
        self.assertEqual([c for c in chiamate if c[0].startswith("bluetooth")], [])
        self.assertEqual(self.esito(conf)["esito"], "errore")


class StampaSuConsoleStrettaTest(unittest.TestCase):
    """Su Windows la console è cp1252: un ✓ stampato fa morire il processo, non solo la riga.

    Successo il 21/10: la CI era rossa sui due runner Windows e il motivo non si poteva leggere,
    perché il registro completo non si scarica e il processo moriva prima di raccontarlo.
    """

    def test_a_console_that_cannot_write_the_tick_does_not_kill_us(self):
        import contextlib
        import io

        from mirto_node import cli

        class ConsoleStretta(io.StringIO):
            encoding = "cp1252"

            def write(self, testo):
                testo.encode("cp1252")        # come fa Windows: esplode sui caratteri che non ha
                return super().write(testo)

        stretta = ConsoleStretta()
        with contextlib.redirect_stdout(stretta):
            cli.ok("fatto")                   # non deve sollevare niente
        self.assertIn("fatto", stretta.getvalue())


class LimitiNelloStatoTest(unittest.TestCase):
    """Lo stato del nodo dice qual è il tetto: senza, non si sa se quello scelto è arrivato.

    Trovato provando l'anello il 21/10: non cancellava (giustamente, era sotto il tetto) ma non c'era
    modo di distinguerlo da «il tetto non è mai arrivato al nodo».
    """

    def righe(self, **limiti) -> str:
        import tempfile

        from mirto_node import cli

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = {"registrazione": dict({"attiva": True, "disco": ""}, **limiti)}
        return "\n".join(cli.recording_lines(cfg))

    def test_the_status_says_the_cap_and_the_free_space_it_keeps(self):
        righe = self.righe(massimo_gb=2, minimo_libero_percento=25)
        self.assertIn("Tetto", righe)
        self.assertIn("25%", righe, "e quanto spazio tiene libero")

    def test_no_cap_is_said_in_words(self):
        self.assertIn("nessuno", self.righe(massimo_gb=0), "«tetto 0» non si scrive come un numero")


class LettoreSenzaSchedaTest(unittest.TestCase):
    """Un lettore di schede senza scheda è un disco da 0 byte: non si prepara e non ci si registra.

    Trovato provando il pannello il 21/10: la finestra offriva «Prepara il disco…» su
    «/dev/sda · 0 GB · da preparare · SD Transcend», e wipefs sarebbe morto con un errore che nessuno
    può capire. Il nodo rifiuta da solo, anche se la richiesta arrivasse da un Mirto vecchio.
    """

    def test_a_reader_with_no_card_is_refused(self):
        import json
        import tempfile
        import types
        from pathlib import Path as P

        from mirto_node import cli

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        chiamate = []

        def finto_sh(cmd, timeout=30, check=False):
            chiamate.append(list(cmd))
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        for nome, valore in (("sh", finto_sh), ("CONF", P(tmp.name)),
                             ("_dischi_visti", lambda: [{"path": "/dev/sda", "disk": "/dev/sda",
                                                         "size": 0, "fstype": "", "uuid": "",
                                                         "mount": "", "model": "SD Transcend",
                                                         "parts": [], "is_part": False}]),
                             ("time", types.SimpleNamespace(sleep=lambda *_a: None, time=lambda: 0))):
            self.addCleanup(setattr, cli, nome, getattr(cli, nome))
            setattr(cli, nome, valore)

        self.assertIsNone(cli._formatta_disco("/dev/sda"))
        self.assertEqual([c for c in chiamate if c[0] in ("wipefs", "mkfs.ext4", "sfdisk")], [],
                         "niente deve toccare il disco")
        esito = json.loads((P(tmp.name) / "disco-esito.json").read_text())
        self.assertEqual(esito["esito"], "errore")
        self.assertIn("senza scheda", esito["messaggio"], "e chi legge deve capire cosa fare")


class DiskTest(unittest.TestCase):
    """mirto-nodo disco: dischi USB, riga di fstab."""

    LSBLK = """{"blockdevices": [
      {"name":"mmcblk0","path":"/dev/mmcblk0","size":31914983424,"type":"disk","fstype":null,"mountpoint":null,
       "tran":null,"model":null,"uuid":null,"children":[
         {"name":"mmcblk0p2","path":"/dev/mmcblk0p2","size":31000000000,"type":"part","fstype":"ext4",
          "mountpoint":"/","tran":null,"model":null,"uuid":"aaa"}]},
      {"name":"sda","path":"/dev/sda","size":240057409536,"type":"disk","fstype":null,"mountpoint":null,
       "tran":"usb","model":"Samsung SSD T7 ","uuid":null,"children":[
         {"name":"sda1","path":"/dev/sda1","size":240000000000,"type":"part","fstype":"exfat",
          "mountpoint":"/media/x","tran":null,"model":null,"uuid":"1234-ABCD"}]},
      {"name":"sdb","path":"/dev/sdb","size":32000000000,"type":"disk","fstype":null,"mountpoint":null,
       "tran":"usb","model":"Chiavetta","uuid":null}
    ]}"""

    def test_usb_candidates_skip_sd(self):
        from mirto_node import cli

        c = cli.usb_candidates(self.LSBLK)
        self.assertEqual([x["path"] for x in c], ["/dev/sda1", "/dev/sdb"])
        self.assertEqual((c[0]["disk"], c[0]["fstype"], c[0]["uuid"], c[0]["model"]),
                         ("/dev/sda", "exfat", "1234-ABCD", "Samsung SSD T7"))
        self.assertEqual(c[1]["fstype"], "")

    def test_system_disk_never_offered(self):
        from mirto_node import cli

        boot_from_usb = """{"blockdevices": [
          {"name":"sda","path":"/dev/sda","size":500000000000,"type":"disk","tran":"usb","model":"SSD sistema",
           "children":[
             {"name":"sda1","path":"/dev/sda1","size":536870912,"type":"part","fstype":"vfat",
              "mountpoint":"/boot/firmware","uuid":"B"},
             {"name":"sda2","path":"/dev/sda2","size":499000000000,"type":"part","fstype":"ext4",
              "mountpoint":"/","uuid":"R"}]},
          {"name":"sdb","path":"/dev/sdb","size":64000000000,"type":"disk","tran":"usb","model":"Dati",
           "children":[
             {"name":"sdb1","path":"/dev/sdb1","size":32000000000,"type":"part","fstype":"ntfs","mountpoint":null},
             {"name":"sdb2","path":"/dev/sdb2","size":32000000000,"type":"part","fstype":"ext4",
              "mountpoint":"/media/x"}]}]}"""
        c = cli.usb_candidates(boot_from_usb)
        self.assertEqual([x["path"] for x in c], ["/dev/sdb1", "/dev/sdb2"])
        self.assertEqual(c[0]["parts"], [("/dev/sdb1", ""), ("/dev/sdb2", "/media/x")])  # mostrati prima di CANCELLA
        self.assertTrue(c[0]["is_part"])

    def _finto_lsblk(self, cli, chiamate, uscita=None):
        """Sostituisce i comandi di sistema: nessuna prova deve poter toccare un disco vero."""
        import types

        def finto_sh(cmd, timeout=30, check=False):
            chiamate.append(list(cmd))
            testo = (self.LSBLK if uscita is None else uscita) if cmd[0] == "lsblk" else ""
            return types.SimpleNamespace(returncode=0, stdout=testo, stderr="")

        def finto_run(cmd, **k):
            chiamate.append(list(cmd))
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        self.addCleanup(setattr, cli, "sh", cli.sh)
        self.addCleanup(setattr, cli, "subprocess", cli.subprocess)
        cli.sh = finto_sh
        cli.subprocess = types.SimpleNamespace(run=finto_run)

    def _conf_finta(self, cli):
        import tempfile
        from pathlib import Path

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(setattr, cli, "CONF", cli.CONF)
        cli.CONF = Path(tmp.name)
        return cli.CONF

    def test_the_search_writes_the_list_where_mirto_reads_it(self):
        import json

        from mirto_node import cli

        conf = self._conf_finta(cli)
        self._finto_lsblk(cli, [])
        self.assertEqual(cli._cerca_dischi(), 0)
        dati = json.loads((conf / "dischi-trovati.json").read_text())
        self.assertEqual([d["path"] for d in dati["dischi"]], ["/dev/sda1", "/dev/sdb"])
        self.assertIn("Samsung", dati["dischi"][0]["etichetta"], "la riga che si legge nella finestra")
        self.assertEqual(json.loads((conf / "disco-esito.json").read_text())["esito"], "ok")

    def test_the_command_that_formats_refuses_a_disk_it_did_not_offer(self):
        """La prova che conta: «prepara» cancella, e il disco di sistema non è fra quelli proposti.

        Il nodo lo controlla già, ma questo comando gira da root e deve reggere da solo: qui si chiede
        di preparare la microSD e si verifica che non sia partito **nessun** comando distruttivo.
        """
        from mirto_node import cli

        self._conf_finta(cli)
        chiamate = []
        self._finto_lsblk(cli, chiamate)
        self.assertIsNone(cli._formatta_disco("/dev/mmcblk0"), "la microSD non si tocca")
        self.assertIsNone(cli._formatta_disco("/dev/sdz"), "e nemmeno un disco che non c'è")
        fatti = [c[0] for c in chiamate]
        for pericoloso in ("wipefs", "sfdisk", "mkfs.ext4", "umount"):
            self.assertNotIn(pericoloso, fatti, f"{pericoloso} non deve nemmeno partire")

    def test_using_a_disk_that_is_not_there_says_so_instead_of_mounting(self):
        import json

        from mirto_node import cli

        conf = self._conf_finta(cli)
        chiamate = []
        self._finto_lsblk(cli, chiamate)
        self.assertEqual(cli._usa_dispositivo("/dev/sdz1"), 1)
        esito = json.loads((conf / "disco-esito.json").read_text())
        self.assertEqual((esito["azione"], esito["esito"]), ("usa", "errore"))
        self.assertIn("ricollegalo", esito["messaggio"], "chi legge deve sapere cosa fare")
        self.assertNotIn("mount", [c[0] for c in chiamate])

    def test_a_disk_to_format_is_not_mounted_as_it_is(self):
        """/dev/sdb non ha formato: «usa» deve rifiutarlo, non montarlo né formattarlo di nascosto."""
        import json

        from mirto_node import cli

        conf = self._conf_finta(cli)
        chiamate = []
        self._finto_lsblk(cli, chiamate)
        self.assertEqual(cli._usa_dispositivo("/dev/sdb"), 1)
        self.assertIn("preparato", json.loads((conf / "disco-esito.json").read_text())["messaggio"])
        for pericoloso in ("mkfs.ext4", "mount", "wipefs"):
            self.assertNotIn(pericoloso, [c[0] for c in chiamate])

    def test_atomic_write(self):
        import tempfile
        from pathlib import Path

        from mirto_node import cli

        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "fstab"
            f.write_text("vecchio\n")
            cli.write_atomic(f, "nuovo\n")
            self.assertEqual(f.read_text(), "nuovo\n")
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["fstab"])

    def test_fstab(self):
        from pathlib import Path

        from mirto_node import cli

        line = cli.fstab_line("u-1", "ext4")
        self.assertEqual(line.split()[:3], ["UUID=u-1", "/mnt/mirto-reg", "ext4"])
        self.assertIn("nofail", line)
        self.assertIn("uid=mediamtx", cli.fstab_line("u-2", "exfat"))
        old = "proc /proc proc defaults 0 0\nUUID=x /mnt/mirto-reg ext4 defaults 0 2\n"
        new = cli.set_fstab(old, line)
        self.assertEqual(new.count("/mnt/mirto-reg"), 1)
        self.assertIn("proc /proc", new)
        self.assertTrue(new.endswith(line + "\n"))
        self.assertEqual(cli.REC_MOUNT, Path("/mnt/mirto-reg"))


class PermessiDelServizioTest(unittest.TestCase):
    """«stato» gira da root e vede tutto; il servizio no. La differenza va detta, non nascosta.

    È costato quaranta minuti di prove il 18/10: la finestra di abbinamento risultava APERTA nello
    stato e CHIUSA a Mirto, e la registrazione non partiva, perché i file scritti da root con
    permessi 0600 il servizio non riesce nemmeno ad aprirli.
    """

    def cartella(self, d) -> Path:
        conf = Path(d)
        (conf / "chiavi").mkdir()
        (conf / "pin").write_text("{}")
        (conf / "mtx-api").write_text("utente:password")
        (conf / "node.crt").write_text("pubblico")
        return conf

    @staticmethod
    def stat_finta(modi: dict):
        """``stat`` finta: proprietario e permessi detti dalla prova, non dal sistema operativo.

        Serve perché su Windows `chmod` sposta solo il flag «sola lettura» e i proprietari non
        esistono: ogni file risulterebbe leggibile da chiunque e la lista uscirebbe vuota. Ma qui
        non si prova il sistema operativo — si prova la regola del nodo, che vale comunque. Con la
        stat finta la regola è provata su tutti e tre i sistemi; il giro vero sul filesystem resta
        nella prova qui sotto, dove i file sono davvero di chi lancia i test.
        """
        def stat(percorso):
            uid, modo = modi[Path(percorso).name]
            return os.stat_result((modo, 0, 0, 1, uid, 0, 0, 0, 0, 0))
        return stat

    def test_files_that_are_not_the_service_s_are_listed(self):
        with tempfile.TemporaryDirectory() as d:
            conf = self.cartella(d)
            modi = {"pin": (0, 0o600),        # di root, illeggibile al servizio
                    "mtx-api": (0, 0o600),
                    "node.crt": (0, 0o644)}   # leggibile da chiunque: al servizio basta
            cattivi = cli.file_non_del_servizio(conf, uid=999999,   # nessun file è suo
                                                stat=self.stat_finta(modi))
            self.assertIn("pin", cattivi)
            self.assertIn("mtx-api", cattivi)
            self.assertNotIn("node.crt", cattivi, "se è leggibile da tutti non è un problema")

    def test_nothing_to_report_when_the_files_belong_to_the_service(self):
        with tempfile.TemporaryDirectory() as d:
            conf = self.cartella(d)
            mio = os.stat(conf / "pin").st_uid
            self.assertEqual(cli.file_non_del_servizio(conf, uid=mio), [])

    def test_without_the_service_user_there_is_nothing_to_check(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(cli.file_non_del_servizio(self.cartella(d), uid=None), [],
                             "nodo vecchio che gira ancora da root: nessun allarme da dare")

    def test_the_status_says_how_to_fix_it(self):
        sorgente = Path(cli.__file__).read_text(encoding="utf-8")
        corpo = sorgente[sorgente.index("def cmd_stato"):sorgente.index("def cmd_wifi")]
        self.assertIn("file_non_del_servizio", corpo)
        self.assertIn("chown -R", corpo, "chi legge l'avviso deve avere anche il rimedio")


class ScrittureDiStatoTest(unittest.TestCase):
    """Ogni file di stato passa da fs.scrivi, che lo restituisce all'utente del servizio."""

    NODE = Path(cli.__file__).parent
    # Scritture che non sono file di stato in /etc/mirto-node, e quindi non c'entrano:
    AMMESSE = {
        "(self.base / name).write_text(value)",   # i LED, in /sys
        "self.boot_pin.write_text(testo)",        # la microSD: FAT, non ha proprietari
    }

    def test_the_service_state_goes_through_fs(self):
        """In server.py i file di stato passano da fs.scrivi, che li restituisce al servizio."""
        fuori = [riga.strip() for riga in (self.NODE / "server.py").read_text(encoding="utf-8").splitlines()
                 if ".write_text(" in riga and riga.strip() not in self.AMMESSE]
        self.assertEqual(fuori, [], "scritto da root, un file 0600 il servizio non riesce a leggerlo")

    def test_every_new_key_file_is_handed_to_the_service(self):
        """In keys.py ogni file nuovo è seguito da _chmod, che stringe i permessi e fa il chown."""
        righe = (self.NODE / "keys.py").read_text(encoding="utf-8").splitlines()
        for numero, riga in enumerate(righe):
            if ".write_text(" not in riga:
                continue
            dopo = " ".join(righe[numero + 1:numero + 4])
            self.assertIn("_chmod(", dopo,
                          f"keys.py riga {numero + 1}: una chiave creata da root resterebbe di root")


class BashProbeTest(unittest.TestCase):
    def test_a_bash_that_does_not_answer_is_not_used(self):
        esiti = {"finto": 1, "vero": 0}.get
        self.assertEqual(bash_che_funziona(["finto", "vero"], esiti), "vero")
        self.assertIsNone(bash_che_funziona(["finto"], esiti))
        self.assertIsNone(bash_che_funziona([None, ""], esiti))

    def test_a_bash_that_cannot_start_is_not_used(self):
        def esplode(eseguibile):
            raise OSError("non eseguibile")
        self.assertIsNone(bash_che_funziona(["qualunque"], esplode))


# Le due schede USB del 22/10: il microfono e la cassa attaccati insieme al nodo per la prima volta.
# Copiato dal Raspberry, parola per parola, perché il difetto si vede solo con queste due righe
# vicine — «USB-Audio» tutte e due, e nessuna che dica in che verso va.
SCHEDE_22_10 = """ 0 [Headphones     ]: bcm2835_headpho - bcm2835 Headphones
                      bcm2835 Headphones
 1 [Device         ]: USB-Audio - USB PnP Sound Device
                      C-Media Electronics Inc. USB PnP Sound Device at usb-0000:01:00.0-1.4, full spe
 2 [vc4hdmi0       ]: vc4-hdmi - vc4-hdmi-0
                      vc4-hdmi-0
 3 [vc4hdmi1       ]: vc4-hdmi - vc4-hdmi-1
                      vc4-hdmi-1
 4 [UACDemoV10     ]: USB-Audio - UACDemoV1.0
                      Jieli Technology UACDemoV1.0 at usb-0000:01:00.0-1.3, full speed
"""

# «amixer -c Device» e «amixer -c UACDemoV10» sul nodo, 22/10. Da notare, perché è il motivo per cui
# l'AGC si cerca per nome: si dichiara «pswitch», manopola di RIPRODUZIONE, pur essendo l'automatismo
# del microfono.
MANOPOLE_MICROFONO = """Simple mixer control 'Mic',0
  Capabilities: cvolume cvolume-joined cswitch cswitch-joined
  Capture channels: Mono
  Limits: Capture 0 - 16
  Mono: Capture 0 [0%] [0.00dB] [on]
Simple mixer control 'Auto Gain Control',0
  Capabilities: pswitch pswitch-joined
  Playback channels: Mono
  Mono: Playback [on]
"""

MANOPOLE_CASSA = """Simple mixer control 'PCM',0
  Capabilities: pvolume pswitch pswitch-joined
  Playback channels: Front Left - Front Right
  Limits: Playback 0 - 147
  Mono:
  Front Left: Playback 44 [30%] [-20.16dB] [on]
  Front Right: Playback 44 [30%] [-20.16dB] [on]
"""


class VersoDelleSchedeTest(unittest.TestCase):
    """Una scheda che non sa suonare non si propone per la sirena, e viceversa.

    Il 22/10, col microfono USB e la cassa USB attaccati insieme, Mirto proponeva **entrambi** come
    uscita della sirena: `/proc/asound/cards` li scrive uguali, «USB-Audio», e la direzione non c'è.
    Scegliendo il microfono la sirena avrebbe suonato in un posto che non sa suonare — e ALSA, in quel
    caso, non si lamenta nemmeno.
    """

    def radice(self, versi: dict[int, str]) -> Path:
        radice = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, radice, True)
        (radice / "cards").write_text(SCHEDE_22_10)
        for n, pcm in versi.items():
            for nome in pcm.split():
                (radice / f"card{n}" / nome).mkdir(parents=True)
        return radice

    def schede(self):
        # Il ferro vero: cuffie e HDMI suonano, la card 1 (C-Media) solo registra, la card 4 (Jieli)
        # solo suona.
        return alsa_cards(radice=self.radice({0: "pcm0p", 1: "pcm0c", 2: "pcm0p", 3: "pcm0p", 4: "pcm0p"}))

    def test_direction_comes_from_the_pcm_devices(self):
        versi = {c["id"]: (c["suona"], c["registra"]) for c in self.schede()}
        self.assertEqual(versi["Device"], (False, True), "il C-Media è un microfono: non suona")
        self.assertEqual(versi["UACDemoV10"], (True, False), "il Jieli è una cassa: non registra")
        self.assertEqual(versi["Headphones"], (True, False))

    def test_a_microphone_is_not_offered_as_a_siren_output(self):
        etichette = [u["etichetta"] for u in node_audio.audio_choices(self.schede())]
        self.assertTrue(any("UACDemoV1.0" in e for e in etichette), "la cassa USB si deve poter scegliere")
        self.assertFalse(any("USB PnP Sound Device" in e for e in etichette),
                         "il microfono fra le casse: è il difetto del 22/10")

    def test_only_capture_cards_are_offered_as_microphones(self):
        self.assertEqual([m["scheda"] for m in node_audio.mic_choices(self.schede())], ["Device"])

    def test_without_proc_nothing_is_hidden(self):
        """Non sapere la direzione non è un buon motivo per nascondere una scheda a chi la cerca."""
        schede = alsa_cards(SCHEDE_22_10)
        self.assertTrue(all(c["suona"] and c["registra"] for c in schede))
        self.assertEqual(len(node_audio.mic_choices(schede)), 5)

    def test_a_card_without_pcm_devices_is_not_hidden_either(self):
        schede = alsa_cards(radice=self.radice({0: "pcm0p"}))
        senza = {c["id"]: (c["suona"], c["registra"]) for c in schede}["Device"]
        self.assertEqual(senza, (True, True), "cartella vuota: non si sa, e non si decide al posto di ALSA")


class ManopoleTest(unittest.TestCase):
    """Leggere, girare e ricordare le manopole di una scheda audio."""

    def finto_amixer(self, uscite: dict, esiti: dict | None = None):
        """``run`` finto: risponde con il testo di «amixer -c X» e registra i comandi ricevuti."""
        fatti = []

        class Res:
            def __init__(self, out="", code=0):
                self.stdout, self.stderr, self.returncode = out, "", code

        def run(argv, **_k):
            fatti.append(argv)
            scheda = argv[2]
            if len(argv) == 3:                      # lettura
                return Res(uscite.get(scheda, ""), 0 if scheda in uscite else 1)
            return Res("", (esiti or {}).get(scheda, 0))

        return run, fatti

    def manopole(self, cartella=None, uscite=None, esiti=None):
        run, fatti = self.finto_amixer(uscite or {"Device": MANOPOLE_MICROFONO,
                                                  "UACDemoV10": MANOPOLE_CASSA}, esiti)
        return mixer.Manopole(cartella=cartella, run=run), fatti

    def cartella(self) -> Path:
        c = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, c, True)
        return c

    # --- leggere ----------------------------------------------------------
    def test_a_knob_can_be_a_volume_and_a_switch_at_once(self):
        pcm, = mixer.leggi(MANOPOLE_CASSA)
        self.assertEqual((pcm["nome"], pcm["indice"]), ("PCM", 0))
        self.assertTrue(pcm["volume"] and pcm["interruttore"], "il PCM di una cassa USB ha volume e muto")
        self.assertEqual((pcm["percento"], pcm["db"], pcm["acceso"]), (30, "-20.16", True))
        self.assertEqual((pcm["minimo"], pcm["massimo"], pcm["direzione"]), (0, 147, "riproduzione"))

    def test_a_stereo_knob_is_one_knob(self):
        """Due canali, un cursore: due cursori per la stessa cosa sono un modo di far sbagliare."""
        self.assertEqual(len(mixer.leggi(MANOPOLE_CASSA)), 1)

    def test_the_microphone_knobs_as_the_hardware_prints_them(self):
        mic, agc = mixer.leggi(MANOPOLE_MICROFONO)
        self.assertEqual((mic["nome"], mic["percento"], mic["massimo"], mic["direzione"]),
                         ("Mic", 0, 16, "cattura"))
        self.assertEqual(agc["nome"], "Auto Gain Control")
        self.assertFalse(agc["volume"], "l'AGC è solo un interruttore")
        self.assertEqual(agc["direzione"], "riproduzione",
                         "si dichiara di riproduzione pur essendo del microfono: per questo lo cerchiamo per nome")

    def test_a_card_that_is_not_there_has_no_knobs(self):
        m, _ = self.manopole()
        self.assertEqual(m.leggi("Sparita"), [])
        self.assertEqual(m.leggi(""), [])

    # --- valori di fabbrica ----------------------------------------------
    def test_factory_values_for_a_microphone(self):
        da_fare = mixer.valori_di_fabbrica(mixer.leggi(MANOPOLE_MICROFONO), "cattura")
        self.assertEqual(da_fare, [("Mic,0", "on"), ("Mic,0", "100%"), ("Auto Gain Control,0", "off")],
                         "guadagno al massimo e automatismo spento: misurato sul ferro il 22/10")

    def test_factory_values_for_a_speaker(self):
        self.assertEqual(mixer.valori_di_fabbrica(mixer.leggi(MANOPOLE_CASSA), "riproduzione"),
                         [("PCM,0", "on"), ("PCM,0", f"{mixer.CASSA}%")])

    def test_a_muted_card_is_switched_on_before_the_volume(self):
        """Il volume giusto su una scheda mutata non serve a niente: prima si accende."""
        da_fare = mixer.valori_di_fabbrica(mixer.leggi(MANOPOLE_CASSA), "riproduzione")
        self.assertLess(da_fare.index(("PCM,0", "on")), da_fare.index(("PCM,0", f"{mixer.CASSA}%")))

    def test_preparing_a_speaker_does_not_touch_a_microphone_agc(self):
        """Su una scheda che fa entrambi, ogni ruolo tocca solo le manopole sue."""
        self.assertEqual(mixer.valori_di_fabbrica(mixer.leggi(MANOPOLE_MICROFONO), "riproduzione"), [])

    # --- girare e ricordare ----------------------------------------------
    def test_turning_a_knob_runs_amixer_and_remembers(self):
        cartella = self.cartella()
        m, fatti = self.manopole(cartella)
        self.assertTrue(m.gira("Device", "Mic,0", "60%"))
        self.assertEqual(fatti[-1], ["amixer", "-c", "Device", "sset", "Mic,0", "60%"])
        self.assertEqual(m.ricordate(), {"Device": {"Mic,0": "60%"}})

    def test_a_nonsense_value_never_reaches_amixer(self):
        m, fatti = self.manopole(self.cartella())
        for brutto in ("200%", "-10%", "boh", "", "on;rm -rf /"):
            with self.assertRaises(ValueError):
                m.gira("Device", "Mic,0", brutto)
        self.assertEqual([f for f in fatti if len(f) > 3], [])

    def test_a_refused_knob_is_not_remembered(self):
        m, _ = self.manopole(self.cartella(), esiti={"Device": 1})
        self.assertFalse(m.gira("Device", "Mic,0", "60%"))
        self.assertEqual(m.ricordate(), {})

    # --- prepararle -------------------------------------------------------
    def test_the_first_time_a_card_is_used_it_gets_the_factory_values(self):
        cartella = self.cartella()
        m, fatti = self.manopole(cartella)
        m.prepara("Device", "cattura")
        self.assertEqual([f[4:] for f in fatti if len(f) > 3],
                         [["Mic,0", "on"], ["Mic,0", "100%"], ["Auto Gain Control,0", "off"]])
        self.assertEqual(m.ricordate()["Device"], {"Mic,0": "100%", "Auto Gain Control,0": "off"})

    def test_what_the_person_chose_wins_over_the_factory(self):
        """Chi ha girato una manopola l'ha girata: al riavvio si rimette quella, non la nostra."""
        cartella = self.cartella()
        primo, _ = self.manopole(cartella)
        primo.gira("Device", "Mic,0", "40%")
        secondo, fatti = self.manopole(cartella)
        secondo.prepara("Device", "cattura")
        self.assertEqual([f[4:] for f in fatti if len(f) > 3], [["Mic,0", "40%"]])

    def test_a_card_is_prepared_once_per_process(self):
        m, fatti = self.manopole(self.cartella())
        m.prepara("Device", "cattura")
        quante = len([f for f in fatti if len(f) > 3])
        m.prepara("Device", "cattura")
        self.assertEqual(len([f for f in fatti if len(f) > 3]), quante)

    def test_a_card_that_comes_back_is_prepared_again(self):
        """Un dispositivo USB staccato e riattaccato torna ai valori di fabbrica suoi: si rifà."""
        m, fatti = self.manopole(self.cartella())
        m.prepara("Device", "cattura")
        quante = len([f for f in fatti if len(f) > 3])
        m.dimentica("Device")
        m.prepara("Device", "cattura")
        self.assertGreater(len([f for f in fatti if len(f) > 3]), quante)

    def test_an_absent_card_is_not_marked_as_done(self):
        """Se la scheda non c'è non si prepara niente, e non si smette di riprovare quando torna."""
        uscite = {}
        run, fatti = self.finto_amixer(uscite)
        m = mixer.Manopole(cartella=self.cartella(), run=run)
        m.prepara("Device", "cattura")
        self.assertEqual([f for f in fatti if len(f) > 3], [])
        uscite["Device"] = MANOPOLE_MICROFONO
        m.prepara("Device", "cattura")
        self.assertTrue([f for f in fatti if len(f) > 3], "tornata la scheda, si prepara")

    def test_knobs_are_saved_without_leaving_a_half_written_file(self):
        cartella = self.cartella()
        m, _ = self.manopole(cartella)
        m.gira("Device", "Mic,0", "60%")
        self.assertEqual([p.name for p in cartella.iterdir()], [mixer.FILE])

    def test_without_a_folder_nothing_is_remembered_and_nothing_breaks(self):
        m, fatti = self.manopole(None)
        self.assertTrue(m.gira("Device", "Mic,0", "60%"))
        self.assertEqual(m.ricordate(), {})
        self.assertTrue(fatti)


class RipieghiTest(unittest.TestCase):
    """Dove ripiega la sirena quando l'uscita scelta non risponde — ed è il momento peggiore per sbagliare.

    Si ripiega **durante un allarme**: se il ripiego è un posto che non suona, la sirena tace proprio
    quando serve, e nessuno lo scopre finché non succede qualcosa.
    """

    def schede(self):
        radice = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, radice, True)
        (radice / "cards").write_text(SCHEDE_22_10)
        for n, pcm in {0: "pcm0p", 1: "pcm0c", 2: "pcm0p", 3: "pcm0p", 4: "pcm0p"}.items():
            (radice / f"card{n}" / pcm).mkdir(parents=True)
        schede = alsa_cards(radice=radice)
        return lambda: schede

    def test_a_microphone_is_never_a_fallback(self):
        """La card 1 è il microfono e viene prima della cassa: si prendeva quella."""
        p = AudioPlayer(output="bluetooth", bt_mac="AA:BB:CC:DD:EE:FF", cards=self.schede())
        usb = [card for tipo, card in p._ripieghi() if tipo == "usb"]
        self.assertEqual(usb, ["UACDemoV10"], "il ripiego USB dev'essere la cassa, non il microfono")

    def test_a_jack_switched_off_is_not_a_fallback(self):
        acceso = AudioPlayer(output="bluetooth", bt_mac="AA:BB:CC:DD:EE:FF", cards=self.schede())
        spento = AudioPlayer(output="bluetooth", bt_mac="AA:BB:CC:DD:EE:FF", cards=self.schede(), jack=False)
        self.assertIn("jack", [t for t, _c in acceso._ripieghi()])
        self.assertNotIn("jack", [t for t, _c in spento._ripieghi()],
                         "chi l'ha spento sa che lì non c'è attaccato niente")
