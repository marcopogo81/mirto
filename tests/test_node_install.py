"""Strumenti di installazione del nodo: sezione [camera], configurazione di MediaMTX, credenziali."""
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "node"))

from mirto_node import install_tools as it  # noqa: E402

from tests.test_node_cli import bash_che_funziona, solo_sul_pinguino  # noqa: E402


class CameraNonVistaTest(unittest.TestCase):
    """Una camera attiva che il sistema non vede deve dirlo, e dire cosa fare.

    Trovato provando il 20/10: il flat era mezzo fuori sede. Mirto diceva «can't open the camera»,
    MediaMTX «selected camera is not available», e il verdetto vero («libcamera interfaces=0», nessuna
    riga in dmesg) stava due livelli sotto. Il nodo lo sapeva — `detect_sensor()` restituiva vuoto — e
    lo diceva in una riga fra cento: «Camera: non rilevata».
    """

    def test_a_camera_the_system_cannot_see_is_announced_loudly(self):
        msg = it.riga_camera("")
        self.assertIn("ATTENZIONE", msg)
        self.assertIn("NON la vede", msg)
        self.assertIn("flat", msg, "senza il rimedio l'avviso non serve a niente")
        self.assertIn("mirto-nodo camera", msg, "e serve la via d'uscita: spegnerla")
        self.assertIn("rpicam-hello", msg, "e come controllare se il rimedio ha funzionato")

    def test_a_camera_that_works_says_only_the_sensor(self):
        msg = it.riga_camera("imx219", noir=True, tuning="/x/imx219_noir.json")
        self.assertEqual(msg, "Camera: imx219 · NoIR · /x/imx219_noir.json")
        self.assertNotIn("ATTENZIONE", msg)


class InstallToolsTest(unittest.TestCase):
    def test_prepare_config_keeps_noir(self):
        with tempfile.TemporaryDirectory() as d:
            toml = Path(d, "node.toml")
            toml.write_text('nome = "Ingresso"\n')
            old = Path(d, "mediamtx.yml")
            old.write_text("rpiCameraTuningFile: /usr/share/libcamera/ipa/rpi/vc4/imx219_noir.json\n")
            self.assertTrue(it.prepare_config(toml, old))
            self.assertIn("noir = true", toml.read_text())
            self.assertFalse(it.prepare_config(toml, old))  # non la duplica
            self.assertEqual(toml.read_text().count("[camera]"), 1)

    def test_mediamtx_yaml(self):
        y = it.mediamtx_yaml(dict(it.CAMERA_DEFAULTS, fps=10), "mirto", "abc123", "/x/imx219_noir.json")
        for needle in ("rtspTransports: [tcp]", "moq: no", "user: mirto", "pass: abc123", "action: read",
                       "source: rpiCamera", "rpiCameraFPS: 10", "rpiCameraBitrate: 1500000", "rpiCameraIDRPeriod: 20", "rpiCameraTuningFile: /x/imx219_noir.json"):
            self.assertIn(needle, y)
        self.assertNotIn("action: publish", y)  # nessuno può pubblicare da fuori
        self.assertNotIn("TuningFile", it.mediamtx_yaml(it.CAMERA_DEFAULTS, "u", "p"))

    def test_tuning_and_sensor(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "vc4").mkdir()
            (root / "vc4" / "imx219_noir.json").write_text("{}")
            self.assertEqual(it.tuning_file("imx219", True, "Raspberry Pi 4 Model B", root),
                             str(root / "vc4" / "imx219_noir.json"))
            self.assertEqual(it.tuning_file("imx219", False, "Raspberry Pi 4 Model B", root), "")
            self.assertEqual(it.tuning_file("imx219", True, "Raspberry Pi 5 Model B", root), "")  # pisp assente
        out = "Available cameras\n-----------------\n0 : imx219 [3280x2464 10-bit RGGB] (/base/…)\n"
        run = lambda *a, **k: types.SimpleNamespace(stdout=out, stderr="")  # noqa: E731
        self.assertEqual(it.detect_sensor(run), "imx219")

    def test_credentials(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d, "rtsp")
            f.write_text("mirto:0a1b2c\n")
            self.assertEqual(it.read_credentials(f), ("mirto", "0a1b2c"))
            f.write_text("mirto:con spazi\n")
            with self.assertRaises(ValueError):
                it.read_credentials(f)


if __name__ == "__main__":
    unittest.main()


class ServizioSenzaRootTest(unittest.TestCase):
    """S-03: il servizio del nodo non gira più da root.

    Il server HTTPS ascolta su tutta la rete di casa ed esegue `aplay`, `bluetoothctl` e scrive
    nei LED. Finché girava da root, un errore qualsiasi in quel codice era un errore da root.
    """

    NODE = ROOT / "node"
    INSTALL = (ROOT / "node" / "install.sh").read_text(encoding="utf-8")

    def unit(self, nome: str) -> str:
        """Il testo della unit come l'installer la scrive (fra «cat > …» e «UNIT»)."""
        inizio = self.INSTALL.index(f"cat > /etc/systemd/system/{nome} <<UNIT")
        return self.INSTALL[inizio:self.INSTALL.index("\nUNIT", inizio)]

    def test_the_service_has_a_user_of_its_own(self):
        self.assertIn("useradd --system --no-create-home --shell /usr/sbin/nologin", self.INSTALL)
        unit = self.unit("mirto-node.service")
        self.assertIn("User=$NODE_USER", unit)
        self.assertIn("Group=$NODE_USER", unit)

    def test_the_unit_gives_up_the_powers_it_does_not_need(self):
        unit = self.unit("mirto-node.service")
        for riga in ("NoNewPrivileges=yes", "CapabilityBoundingSet=", "ProtectKernelTunables=yes",
                     "RestrictSUIDSGID=yes", "ProtectSystem=full", "ProtectHome=yes"):
            self.assertIn(riga, unit, riga)
        self.assertIn("ReadWritePaths=$CONF", unit)
        self.assertNotIn("/boot/firmware", unit,
                         "la microSD non è più scrivibile dal servizio: la tocca il servizio da root")

    def test_the_led_is_still_writable_after_the_hardening(self):
        """ProtectKernelTunables rende /sys di sola lettura: il LED va riaperto per nome.

        Trovato sul nodo vero: permessi giusti sul file, gruppo giusto, e «luce di prova» che non
        faceva niente. L'irrobustimento aveva chiuso fuori l'unica cosa che il nodo scrive in /sys.
        """
        unit = self.unit("mirto-node.service")
        if "ProtectKernelTunables=yes" not in unit:
            return  # senza quella protezione /sys resta scrivibile: niente da riaprire
        self.assertIn("/sys/class/leds/ACT", unit,
                      "con /sys di sola lettura il LED non si accende, qualunque siano i permessi")
        self.assertIn("-/sys/class/leds/", unit, "con il «-»: su un dispositivo senza quel LED si parte lo stesso")

    def test_only_the_groups_it_really_uses(self):
        self.assertIn("for g in audio bluetooth video gpio; do", self.INSTALL,
                      "audio per la cassa, bluetooth per la sirena, video per la camera")
        self.assertIn("SupplementaryGroups=$GROUPS_NODE", self.unit("mirto-node.service"))

    def test_the_configuration_belongs_to_the_service(self):
        self.assertIn('chown -R "$NODE_USER:$NODE_USER" "$CONF"', self.INSTALL)
        self.assertIn('install -d -m 700 -o "$NODE_USER" -g "$NODE_USER" "$CONF"', self.INSTALL)

    def test_the_led_rule_only_touches_files_that_always_exist(self):
        regola = (self.NODE / "99-mirto-leds.rules").read_text(encoding="utf-8")
        self.assertIn('SUBSYSTEM=="leds"', regola)
        self.assertIn("mirto-node", regola)
        self.assertIn("trigger", regola, "è l'unico file che il nodo scrive, ed esiste sempre")
        self.assertNotIn("brightness", regola,
                         "scrivere la luminosità non comanda il LED verde: non serve il permesso")
        for mai in ("delay_on", "delay_off"):
            self.assertNotIn(f"/{mai}", regola,
                             "esiste solo mentre il trigger è «timer»: la regola non lo troverebbe")
        self.assertIn("/etc/udev/rules.d/99-mirto-leds.rules", self.INSTALL)

    def test_the_microsd_is_handled_by_a_root_service(self):
        script = (self.NODE / "boot-segnali.sh").read_text(encoding="utf-8")
        for lavoro in ("mirto-abbina", "pin-da-mostrare", "pin-da-togliere", "camera-da-applicare"):
            self.assertIn(lavoro, script, lavoro)
        self.assertIn("boot-segnali.sh", self.INSTALL)
        percorso = self.unit("mirto-boot-segnali.path")
        for lavoro in ("/boot/firmware/mirto-abbina", "$CONF/pin-da-mostrare", "$CONF/pin-da-togliere",
                       "$CONF/camera-da-applicare"):
            self.assertIn(f"PathExists={lavoro}", percorso, lavoro)
        self.assertIn("Type=oneshot", self.unit("mirto-boot-segnali.service"))

    def test_every_job_of_the_root_service_is_actually_watched(self):
        """Un biglietto che nessuno aspetta resta lì: «registrazioni-da-preparare» era così.

        Il servizio da root parte da mirto-boot-segnali.path, che elenca i file da aspettare: se il
        programma lascia un biglietto che non è in quell'elenco, viene eseguito solo per caso — al
        riavvio, o quando arriva uno degli altri. Trovato il 20/10 scrivendo «nome-da-applicare», che dal 21/10 non esiste più.
        """
        script = (self.NODE / "boot-segnali.sh").read_text(encoding="utf-8")
        percorso = self.unit("mirto-boot-segnali.path")
        # i biglietti sono quelli che lo script *aspetta* («[ -s "$CONF/x" ]»), non quelli che scrive
        biglietti = set(re.findall(r'\[ -[se] "\$CONF/([a-z-]+)" \]', script))
        self.assertIn("pin-da-mostrare", biglietti, "la prova non sta leggendo i biglietti giusti")
        attesi = set(re.findall(r'PathExists=\$CONF/([a-z-]+)', percorso))
        self.assertEqual(biglietti - attesi, set(), "biglietti che il servizio non aspetta")

    def test_only_a_mount_point_already_in_fstab_can_be_mounted(self):
        """Il biglietto che rimonta il disco non deve poter far montare qualcosa di nuovo.

        Il nodo gira senza privilegi: tutto quello che chiede a root passa da un file, e un file lo
        puo\' scrivere anche qualcos\'altro. Qui root accetta solo un punto di mount che in
        /etc/fstab c\'e\' gia\', cioe\' un disco che qualcuno ha scelto dal pannello.
        """
        script = (self.NODE / "boot-segnali.sh").read_text(encoding="utf-8")
        self.assertIn("disco-da-rimontare", script)
        self.assertIn("/etc/fstab", script, "il controllo e\' che quel punto stia gia\' in fstab")
        self.assertIn('[ "${PUNTO#/}" != "$PUNTO" ]', script, "e che sia un percorso completo")
        dopo = script.index('if [ -s "$CONF/disco-da-rimontare" ]')
        self.assertIn("mountpoint -q", script[dopo:dopo + 1400],
                      "gia' montato: non si rimonta sopra")

    def test_the_node_code_does_not_call_what_windows_has_not(self):
        """Il nodo gira solo su Linux, ma le sue prove girano anche su Windows.

        Il 22/10 la CI e' morta li': `os.statvfs` su Windows non esiste, e il codice lo chiamava sempre.
        Non e' un capriccio della CI - e' il modo in cui ci si accorge che una funzione di sistema e'
        stata usata senza chiedersi dove giri. Chi serve davvero si cerca con `getattr` e si usa se c'e'.
        """
        import re as _re

        sospette = ("statvfs", "geteuid", "getuid", "chown", "fchown", "setuid")
        # cli.py resta fuori: «mirto-nodo» e' un comando che gira solo sul Raspberry, chiede
        # l'amministratore nella prima riga di main() e nessuna prova lo esegue. Qui si guardano i
        # moduli di cui le prove chiamano davvero le funzioni, su tutti e tre i sistemi.
        colpevoli = []
        for file in sorted((self.NODE / "mirto_node").glob("*.py")):
            if file.name == "cli.py":
                continue
            testo = file.read_text(encoding="utf-8")
            for nome in sospette:
                for m in _re.finditer(rf"\bos\.{nome}\(", testo):
                    riga = testo.count("\n", 0, m.start()) + 1
                    intorno = testo[max(0, m.start() - 400):m.start()]
                    if f'"{nome}"' in intorno or "try:" in intorno.rsplit("\n", 6)[0]:
                        continue       # cercata con getattr, o gia' dentro un try
                    colpevoli.append(f"{file.name}:{riga} os.{nome}")
        self.assertEqual(colpevoli, [],
                         "su Windows queste non ci sono tutte: cercale con getattr, o mettile in un "
                         "try, altrimenti e' la suite a morire e non si capisce perche'")

    def test_a_dead_mount_is_removed_before_remounting(self):
        """Finche\' il mount morto e\' li\', «mount» non puo\' rimetterci niente sopra."""
        script = (self.NODE / "boot-segnali.sh").read_text(encoding="utf-8")
        dopo = script.index('if [ -s "$CONF/disco-da-rimontare" ]')
        blocco = script[dopo:dopo + 1200]
        self.assertIn("findmnt -no SOURCE", blocco, "si guarda quale dispositivo e\' montato li\'")
        self.assertIn('[ ! -e "$SORGENTE" ]', blocco, "e se quel dispositivo non c\'e\' piu\'...")
        self.assertIn("umount -l", blocco, "...il mount morto si toglie")
        self.assertLess(blocco.index("umount -l"), blocco.index('mount "$PUNTO"'),
                        "prima si toglie quello morto, poi si rimonta")

    def test_the_recordings_stay_deletable_by_the_node(self):
        self.assertIn('chmod 2775 "$REC_DIR" "$REC_DIR/cam"', self.INSTALL,
                      "setgid e gruppo in scrittura: cancellare dipende dai permessi della cartella")
        self.assertIn('usermod -aG mediamtx "$NODE_USER"', self.INSTALL)
        self.assertIn("/etc/tmpfiles.d/mirto-node.conf", self.INSTALL,
                      "al riavvio, e su un disco rimontato, i permessi vanno rifatti")
        script = (self.NODE / "boot-segnali.sh").read_text(encoding="utf-8")
        self.assertIn("registrazioni-da-preparare", script)
        self.assertIn('case "$CARTELLA" in', script,
                      "il percorso arriva da un file: va controllato prima di passarlo a chown")

    def test_a_disk_without_unix_permissions_does_not_kill_the_installation(self):
        """22/10, difetto vero: una chiavetta vfat ha fermato l'aggiornamento al passo 4/8.

        «chmod: Operation not permitted» su `/mnt/mirto-reg`, e con `set -e` l'installazione e' morta
        li'. Su vfat e exfat proprietario e setgid non esistono — li decide il mount, uguali per tutti i
        file — quindi chown e chmod devono poter fallire senza portarsi via tutto il resto.
        """
        self.assertIn('chown -R mediamtx:mediamtx "$REC_DIR" 2>/dev/null', self.INSTALL,
                      "su un disco senza permessi Unix chown fallisce: non deve essere fatale")
        self.assertIn('chmod 2775 "$REC_DIR" "$REC_DIR/cam" 2>/dev/null', self.INSTALL)
        self.assertIn("vfat/exfat", self.INSTALL, "quando succede va detto, non nascosto")
        dentro = self.INSTALL.index('chown -R mediamtx:mediamtx "$REC_DIR"')
        self.assertIn("if ", self.INSTALL[dentro - 60:dentro],
                      "chown e chmod stanno dentro un «if»: il ramo che fallisce prosegue")
        self.assertIn("rm -f /etc/tmpfiles.d/mirto-node.conf", self.INSTALL,
                      "senza permessi Unix, tmpfiles.d riproverebbe a ogni avvio e fallirebbe a ogni avvio")

    def test_the_camera_is_never_left_off_when_the_installation_dies(self):
        """Un aggiornamento puo' fallire; non puo' lasciare una casa senza camera.

        install.sh spegne MediaMTX per liberare la camera e la riaccende cinquanta righe piu' sotto. Il
        22/10 e' morto in mezzo e il nodo e' rimasto senza stream finche' qualcuno non se n'e' accorto:
        da allora, appena spegne si prende l'impegno di riaccendere anche morendo.
        """
        stop = self.INSTALL.index("sc stop mediamtx")
        trap = self.INSTALL.index("trap 'systemctl start mediamtx")
        riavvio = self.INSTALL.index("sc restart mediamtx")
        tolto = self.INSTALL.index("trap - EXIT")
        self.assertLess(stop, trap, "la rete di sicurezza si mette subito dopo aver spento")
        self.assertLess(trap, riavvio)
        self.assertLess(riavvio, tolto, "si toglie solo dopo che la camera e' stata riaccesa davvero")
        self.assertIn('if [ "$FIRST" = 0 ]; then\n    trap', self.INSTALL,
                      "al primo avvio i servizi partono dopo install.sh: li' non si accende niente")

    def test_a_node_without_internet_can_still_be_updated(self):
        """Il nodo di prova sta su un cavo diretto, senza internet: «apt-get update» fallisce."""
        self.assertIn("if apt-get update -qq 2>/dev/null; then", self.INSTALL,
                      "con «set -e» un apt-get update fallito fermerebbe tutta l'installazione")
        self.assertIn('if [ -n "$MANCANTI" ]; then', self.INSTALL,
                      "senza rete si va avanti solo se i programmi ci sono già")
        for c in ("aplay", "bluetoothctl", "avahi-daemon", "openssl", "python3", "curl"):
            self.assertIn(c, self.INSTALL, c)

    def test_ffmpeg_is_wanted_but_never_required(self):
        """ffmpeg serve **solo** all'audio nelle registrazioni, che e' spento di serie.

        Metterlo fra i necessari vorrebbe dire un'installazione che fallisce su un nodo che funziona
        benissimo — e questo copione lo rilancia ogni aggiornamento del nodo (N-02), anche su un nodo
        senza internet: il nodo di casa sta su un cavo diretto. Un aggiornamento che non passa per un
        programma che quel nodo non usera' mai e' il modo piu' rapido per lasciare indietro i nodi.
        """
        self.assertIn("apt-get install -y -qq ffmpeg", self.INSTALL, "si prova a installarlo")
        dopo = self.INSTALL.split("install -y -qq ffmpeg", 1)[1][:200]
        self.assertIn("|| echo", dopo, "e se non si riesce si va avanti, dicendolo")
        mancanti = self.INSTALL.split('for c in ', 1)[1].split(';', 1)[0]
        self.assertNotIn("ffmpeg", mancanti,
                         "senza rete un nodo senza ffmpeg si aggiorna comunque: l'audio e' l'unica "
                         "cosa che non avra'")

    def test_the_image_carries_a_user_nobody_can_use_yet(self):
        """Il primo nodo nato da questa immagine, il 20/10, non aveva nessun utente: scatola chiusa.

        L'utente lo doveva creare cloud-init al primo avvio; quando `firstboot.sh` è arrivato al passo
        della password, nel sistema non c'era ancora nessuno con uid ≥ 1000, e ha saltato tutto in
        silenzio. Niente password, niente foglio, e in quel nodo non poteva entrare nessuno — né il
        padrone né noi. Ora l'utente sta **dentro l'immagine**, bloccato, e la password gliela dà il
        primo avvio: così non dipende più dall'ordine dei servizi.
        """
        img = (self.NODE / "make_public_image.sh").read_text(encoding="utf-8")
        self.assertIn("useradd -R", img)
        self.assertIn('usermod -R "$MNT" -L mirto', img, "nasce bloccato: nessuna password nota")
        self.assertIn("manca l'utente mirto", img, "e un controllo che se ne accorga")
        boot = (self.NODE / "firstboot.sh").read_text(encoding="utf-8")
        self.assertIn("NESSUN UTENTE", boot, "se comunque manca, il foglio deve dirlo invece di sparire")

    def test_the_image_says_in_its_name_what_it_carries(self):
        """Il nome del file porta la **versione**, non solo la data.

        Marco, 25/10: *«quando crei l'immagine mettici la versione, altrimenti bisogna scoprirlo»*.
        In `privato/` ci sono cinque immagini che si chiamano tutte uguale tranne la data, e per
        sapere cosa c'era dentro una scheda bisognava accenderla. La versione si legge dal programma
        che il copione sta per copiare dentro l'immagine: non si chiede a nessuno e non si scrive a
        mano, quindi non puo' raccontare una versione diversa da quella che installa.
        """
        img = (self.NODE / "make_public_image.sh").read_text(encoding="utf-8")
        self.assertIn('mirto_node/__init__.py', img, "la versione si legge dal programma che copia")
        self.assertIn('NAME="mirto-node-$STAMP${VERSIONE:+-$VERSIONE}"', img)
        versione = ""
        for riga in (self.NODE / "mirto_node" / "__init__.py").read_text(encoding="utf-8").splitlines():
            if riga.startswith("__version__"):
                versione = riga.split("=", 1)[1].strip().strip("\"'")
        self.assertTrue(versione, "il programma del nodo dichiara una versione")
        # Senza versione leggibile il nome resta quello di prima invece di diventare «…-.img.xz»:
        # un'immagine con un trattino appeso sembrerebbe rotta a chi la riceve.
        self.assertIn("${VERSIONE:+-$VERSIONE}", img)

    def test_the_user_checks_catch_a_closed_or_open_image(self):
        """I controlli finali bocciano sia l'immagine senza utente sia quella con una password dentro."""
        import subprocess
        import tempfile

        solo_sul_pinguino(self)
        bash = bash_che_funziona()
        if bash is None:
            self.skipTest("bash non disponibile")
        img = (self.NODE / "make_public_image.sh").read_text(encoding="utf-8")
        blocco = img[img.index("STATO_UTENTE="):img.index("# Qui si guarda l'immagine vera")]
        casi = (("mirto:!:20000:0:99999:7:::\n", "sudo:x:27:mirto\n", "0", "bloccato: va bene"),
                ("mirto:$6$sale$cifrata:20000:0:99999:7:::\n", "sudo:x:27:mirto\n", "1", "ha una password"),
                ("pi:!:20000:0:99999:7:::\n", "sudo:x:27:pi\n", "1", "non c'è mirto"),
                ("mirto:!:20000:0:99999:7:::\n", "sudo:x:27:\n", "1", "mirto non può usare sudo"))
        for shadow, group, atteso, perche in casi:
            with tempfile.TemporaryDirectory() as d:
                (Path(d) / "etc").mkdir()
                (Path(d) / "etc" / "shadow").write_text(shadow)
                (Path(d) / "etc" / "group").write_text(group)
                code = f'MNT="{d}"\nBAD=0\n{{\n{blocco}\n}} >/dev/null\nprintf "%s" "$BAD"'
                res = subprocess.run([bash, "-c", code], capture_output=True, text=True)
                self.assertEqual(res.stdout, atteso, f"{perche}: {res.stderr}")

    def test_the_network_announcement_stays_readable(self):
        """Avahi gira senza privilegi: un annuncio 0600 è un nodo invisibile.

        Successo il 20/10 per una umask ereditata dal primo avvio, ma capita anche a chi lancia
        install.sh con una umask stretta: i file li scrive «cat >», che rispetta la umask di chi passa.
        """
        for f in ("mirto-cam.service", "mirto-node.service"):
            self.assertIn(f"chmod 644 /etc/avahi/services/{f}", self.INSTALL, f)
        self.assertIn("\numask 022\n", self.INSTALL,
                      "e i permessi di tutto il resto non dipendono da chi lancia l'installazione")

    def test_the_program_stays_readable_however_it_arrived(self):
        """Il 21/10 il nodo è sparito da Mirto mentre la camera continuava a funzionare.

        `PermissionError: [Errno 13] … /opt/mirto-node/mirto_node/server.py`: i file erano arrivati
        dal Mac con permessi 600, `rsync -a` li porta tali e quali e `cp -r` li copia tali e quali in
        /opt — e il servizio, che gira senza privilegi (S-03), non può più leggere il proprio
        programma. `umask 022` non basta: vale per i file nuovi, non per le copie. L'installazione
        deve rendere leggibile quello che installa, qualunque cosa le venga messa davanti.
        """
        self.assertIn('chmod -R u=rwX,go=rX "$DEST"', self.INSTALL)
        img = (self.NODE / "make_public_image.sh").read_text(encoding="utf-8")
        self.assertIn('chmod -R u=rwX,go=rX "$MNT/opt/mirto-node"', img,
                      "altrimenti un'immagine può nascere con /opt illeggibile")

    def test_the_image_keeps_its_logs_across_reboots(self):
        """Il 20/10 di un primo avvio andato storto non restava niente: journald scriveva solo in memoria."""
        img = (self.NODE / "make_public_image.sh").read_text(encoding="utf-8")
        self.assertIn('mkdir -p "$MNT/var/log/journal"', img)

    def test_what_lands_on_the_microsd_is_flushed(self):
        """La partizione di avvio è FAT e non ha giornale: senza «sync» un distacco perde il PIN."""
        script = (self.NODE / "boot-segnali.sh").read_text(encoding="utf-8")
        for biglietto in ("pin-da-mostrare", "pin-da-togliere"):
            inizio = script.index(f'if [ -{"s" if biglietto.endswith("mostrare") else "e"} "$CONF/{biglietto}" ]')
            blocco = script[inizio:script.index("\nfi", inizio)]
            self.assertIn("sync", blocco, f"manca il sync nel blocco {biglietto}")

    def test_the_compression_level_follows_the_machine(self):
        """Su un Raspberry da 1 GB «xz -6» ci mette un'ora per 4 GB: il livello va scelto, non subito.

        Provato il 20/10 su un 3B: 264 MB compressi in 25 minuti, con **un core solo**, perché xz
        riduce da sé i thread quando la memoria è poca. Metà della dimensione non vale un'ora.
        """
        import subprocess

        solo_sul_pinguino(self)
        bash = bash_che_funziona()
        if bash is None:
            self.skipTest("bash non disponibile")
        script = (self.NODE / "make_public_image.sh").read_text(encoding="utf-8")
        righe = [r for r in script.splitlines() if r.startswith(("LIVELLO=", '[ -n "$LIVELLO"'))]
        self.assertEqual(len(righe), 2, "le righe che scelgono il livello sono cambiate")
        for mem, env, atteso in ((1024, "", "2"), (900, "", "2"), (4096, "", "6"), (16384, "", "6"),
                                 (1024, "MIRTO_XZ=9", "9")):
            code = f"{env} MEM_MB={mem}\n" + "\n".join(righe) + '\nprintf "%s" "$LIVELLO"'
            res = subprocess.run([bash, "-c", code], capture_output=True, text=True, check=True)
            self.assertEqual(res.stdout, atteso, f"{mem} MB, {env or 'senza MIRTO_XZ'}")

    def test_the_image_carries_every_file_the_installation_expects(self):
        """Quello che install.sh cerca accanto a sé deve stare dentro l'immagine, aggiornato.

        Al primo avvio install.sh gira da /opt/mirto-node e **non ricopia niente** (HERE == DEST):
        un file dimenticato da make_public_image.sh resta quello vecchio del cartone di partenza, per
        sempre. Il 21/10 mancava boot-segnali.sh, e un'immagine nuova avrebbe avuto l'unità che
        sorveglia «disco-da-fare» insieme a un copione che non sa cosa farne.
        """
        inst = (self.NODE / "install.sh").read_text(encoding="utf-8")
        img = (self.NODE / "make_public_image.sh").read_text(encoding="utf-8")
        voluti = sorted(set(re.findall(r'"\$HERE/([A-Za-z0-9._-]+)"', inst)) - {"mirto_node"})
        self.assertIn("boot-segnali.sh", voluti, "l'elenco si legge da install.sh: è cambiato?")
        blocco = img[img.index('echo "5/7'):img.index('echo "     controlli')]
        for f in voluti:
            self.assertIn(f'"$HERE/{f}"', blocco, f"make_public_image.sh non mette {f} nell'immagine")
            self.assertTrue((self.NODE / f).exists(), f"{f} non esiste nemmeno nel repository")
        self.assertIn("cmp -s", blocco, "e dopo averli copiati si controlla che siano gli stessi")

    def test_the_update_package_carries_every_file_the_installation_expects(self):
        """Lo stesso controllo dell'immagine, sul **pacchetto** che Mirto manda per aggiornare.

        Il 26/10 il primo aggiornamento vero da Mirto e' fallito con «l'installazione non e' andata
        a buon fine» e nient'altro. Il pacchetto non portava `prepare_image.sh` ne'
        `node.toml.example`, e `install.sh` li cerca accanto a se' al passo 2: con `set -e` muore li'
        e il nodo resta com'era. Non si era mai visto perche' fino a quel giorno nessun `/aggiorna`
        aveva mai installato davvero -- la 0.12.0 era stata messa a mano con rsync, che manda la
        cartella intera e quindi copre il difetto.

        Due elenchi scritti a mano in due file diversi restano pari finche' qualcuno se lo ricorda.
        Questa prova li lega: l'elenco si legge da `install.sh`, e `DA_MANDARE` deve contenerlo.
        """
        from mirto.actions.aggiorna_nodo import DA_MANDARE

        inst = (self.NODE / "install.sh").read_text(encoding="utf-8")
        voluti = sorted(set(re.findall(r'"\$HERE/([A-Za-z0-9._-]+)"', inst)))
        self.assertIn("prepare_image.sh", voluti, "l'elenco si legge da install.sh: e' cambiato?")
        self.assertIn("mirto_node", voluti, "idem per il programma")
        mancanti = [f for f in voluti if f not in DA_MANDARE]
        self.assertEqual(mancanti, [], "il pacchetto dell'aggiornamento non porta file che install.sh "
                                       "cerca accanto a se': l'installazione morirebbe al passo 2\n  "
                                       + "\n  ".join(mancanti))

    def test_what_the_package_promises_is_really_inside_it(self):
        """E non basta che l'elenco sia giusto: i file devono finire davvero nell'archivio."""
        import io
        import tarfile

        from mirto.actions.aggiorna_nodo import DA_MANDARE, fai_pacchetto

        crudo, _versione, _sha = fai_pacchetto()
        with tarfile.open(fileobj=io.BytesIO(crudo)) as tar:
            dentro = {m.name.split("/")[0] for m in tar.getmembers()}
        mancanti = sorted(n for n in DA_MANDARE if n not in dentro and (self.NODE / n).exists())
        self.assertEqual(mancanti, [], "elencati e non impacchettati:\n  " + "\n  ".join(mancanti))

    def test_the_cleanup_keeps_a_list_of_what_stays_not_of_what_goes(self):
        """Elencare i file da cancellare resta indietro di una funzione: il 21/10 erano otto.

        Fra quelli nuovi c'era wifi-da-collegare.json, che tiene la password del Wi-Fi di casa finché il
        servizio da root non l'ha usata: in un'immagine che si manda a tutti non ci deve arrivare mai.
        """
        pulizia = (self.NODE / "prepare_image.sh").read_text(encoding="utf-8")
        self.assertIn('TIENI="primo-avvio"', pulizia, "la pulizia dice cosa TIENE")
        self.assertIn("ATTENZIONE: in $CONF resta ancora", pulizia, "e verifica di averlo fatto")

    def test_the_image_is_not_made_if_a_state_file_survived(self):
        """La prova che conta: il controllo sull'immagine vera deve fermare anche un file che non conosce."""
        import subprocess

        solo_sul_pinguino(self)
        bash = bash_che_funziona()
        if bash is None:
            self.skipTest("bash non disponibile")
        script = (self.NODE / "make_public_image.sh").read_text(encoding="utf-8")
        inizio = script.index('TIENI_CONF="primo-avvio"')
        fine = script.index("primo-avvio: il nodo non si configurerebbe al primo avvio", inizio)
        blocco = script[inizio:script.index("\n", fine)] + "\n"
        with tempfile.TemporaryDirectory() as tmp:
            conf = Path(tmp) / "etc" / "mirto-node"
            conf.mkdir(parents=True)
            (conf / "primo-avvio").write_text("")
            code = f'MNT="{tmp}"\nBAD=0\n' + blocco + 'printf "%s" "$BAD"'
            res = subprocess.run([bash, "-c", code], capture_output=True, text=True)
            self.assertEqual(res.stdout[-1], "0", f"immagine pulita rifiutata: {res.stdout}")
            # ora ci si lascia dentro la password del Wi-Fi di casa, come succederebbe se il biglietto
            # per root non fosse stato consumato prima di spegnere
            (conf / "wifi-da-collegare.json").write_text('{"ssid": "CasaMia", "password": "segreto"}')
            res = subprocess.run([bash, "-c", code], capture_output=True, text=True)
            self.assertEqual(res.stdout[-1], "1", "un file di stato rimasto deve fermare l'immagine")
            self.assertIn("wifi-da-collegare.json", res.stdout)
            (conf / "wifi-da-collegare.json").unlink()
            (conf / "primo-avvio").unlink()
            res = subprocess.run([bash, "-c", code], capture_output=True, text=True)
            self.assertEqual(res.stdout[-1], "1", "e senza primo-avvio l'immagine non si configura")

    def test_the_image_does_not_carry_the_notes_away(self):
        pulizia = (self.NODE / "prepare_image.sh").read_text(encoding="utf-8")
        for f in ("pin-da-mostrare", "pin-da-togliere"):
            self.assertIn(f, pulizia, f)
