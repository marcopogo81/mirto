# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Il primo avvio dell'immagine, provato **senza Raspberry**.

Il 20/10 due difetti del primo avvio sono saltati fuori solo su hardware vero, dopo mezz'ora di giro
(costruisci l'immagine, scrivi la microSD, accendi, aspetta): l'utente che non c'era, e il
ridimensionamento del disco che teneva in ostaggio tutto il resto. Erano due cose leggibili
nell'ordine dei passi, e questo è il banco di prova che le fa vedere in due secondi: `firstboot.sh`
gira davvero, in una radice finta, con i comandi di sistema simulati da copioncini che si limitano a
registrare come sono stati chiamati.
"""
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_node_cli import bash_che_funziona, solo_sul_pinguino  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "node" / "firstboot.sh"

STUB = """#!/bin/bash
echo "$(basename "$0") $*" >> "LOG"
exit 0
"""


class PrimoAvvioTest(unittest.TestCase):
    """Gira dove i copioni del nodo hanno senso: **Linux**, che è quello che c'è sul Raspberry.

    Su macOS il sed è quello di BSD e «sed -i» vuole un argomento: il copione fallisce per un motivo che
    non riguarda il nodo. Su Windows non c'è nemmeno una shell POSIX.
    """

    @classmethod
    def setUpClass(cls):
        if not sys.platform.startswith("linux"):
            raise unittest.SkipTest("i copioni del nodo girano su Linux: altrove sed, permessi e shell "
                                    "sono altri e la prova direbbe solo bugie")
        cls.bash = bash_che_funziona()
        if cls.bash is None:
            raise unittest.SkipTest("bash non disponibile")

    def prepara(self, utente: str = "mirto", stato: str = "L") -> dict:
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(d)], check=False))
        self.d = d
        etc, boot, conf, dest, sysd, bind = (d / "etc", d / "boot", d / "conf", d / "dest",
                                             d / "sys", d / "bin")
        for c in (etc / "ssh", boot, conf, dest, bind,
                  sysd / "class" / "net" / "eth0", sysd / "class" / "block" / "mmcblk0p2",
                  sysd / "block" / "mmcblk0"):
            c.mkdir(parents=True, exist_ok=True)
        riga = f"{utente}:x:1000:1000::/home/{utente}:/bin/bash\n" if utente else ""
        (etc / "passwd").write_text("root:x:0:0::/root:/bin/bash\n" + riga)
        (etc / "hostname").write_text("mirto-node\n")
        (etc / "hosts").write_text("127.0.0.1\tlocalhost\n127.0.1.1\tmirto-node\n")
        (etc / "machine-id").write_text("4d7f\n")
        (sysd / "class" / "net" / "eth0" / "address").write_text("dc:a6:32:9d:96:d3\n")
        # microSD da 64 GB con una radice da 3,6 GB: c'è spazio libero in coda, come nell'immagine vera
        (sysd / "class" / "block" / "mmcblk0p2" / "partition").write_text("2\n")
        (sysd / "class" / "block" / "mmcblk0p2" / "start").write_text("1064960\n")
        (sysd / "class" / "block" / "mmcblk0p2" / "size").write_text("7497440\n")
        (sysd / "block" / "mmcblk0" / "size").write_text("124518400\n")
        (conf / "primo-avvio").write_text("")
        self.log = d / "comandi.txt"
        self.log.write_text("")
        base = STUB.replace("LOG", str(self.log))
        for nome in ("systemd-machine-id-setup", "hostname", "ssh-keygen", "systemctl", "sfdisk",
                     "partx", "resize2fs", "systemd-run", "chpasswd", "openssl-non-usato"):
            (bind / nome).write_text(base)
            (bind / nome).chmod(0o755)
        (bind / "chpasswd").write_text(base.replace("exit 0", "cat > /dev/null\nexit 0"))
        (bind / "chpasswd").chmod(0o755)
        (bind / "findmnt").write_text(base.replace("exit 0", "echo /dev/mmcblk0p2\nexit 0"))
        (bind / "findmnt").chmod(0o755)
        (bind / "lsblk").write_text(base.replace("exit 0", "echo mmcblk0\nexit 0"))
        (bind / "lsblk").chmod(0o755)
        (bind / "passwd").write_text(base.replace(
            "exit 0", f'[ "$1" = "-S" ] && echo "$2 {stato} 01/01/2026 0 99999 7 -1"\nexit 0'))
        (bind / "passwd").chmod(0o755)
        # l'installazione vera crea file che devono restare leggibili (es. gli annunci di avahi):
        # questo finto ne crea uno, così la prova può guardarne i permessi
        (dest / "install.sh").write_text(base.replace(
            "exit 0", f'cat > "{etc}/finto-annuncio.service" <<< "<service-group/>"\nexit 0'))
        (dest / "install.sh").chmod(0o755)
        env = dict(os.environ, PATH=f"{bind}:{os.environ['PATH']}", MIRTO_CONF=str(conf),
                   MIRTO_DEST=str(dest), MIRTO_BOOT=str(boot), MIRTO_ETC=str(etc),
                   MIRTO_SYS=str(sysd))
        return env

    def avvia(self, env: dict) -> subprocess.CompletedProcess:
        res = subprocess.run([self.bash, str(SCRIPT)], env=env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"il primo avvio è fallito:\n{res.stdout}\n{res.stderr}")
        return res

    def comandi(self) -> list[str]:
        return [r for r in self.log.read_text().splitlines() if r.strip()]

    def test_the_sheet_with_the_password_is_written(self):
        """È il foglio che viaggia col dispositivo: senza, in quel nodo non entra più nessuno."""
        self.avvia(self.prepara())
        foglio = (self.d / "boot" / "mirto-accesso.txt")
        self.assertTrue(foglio.exists(), "nessun mirto-accesso.txt")
        testo = foglio.read_text()
        self.assertIn("utente:   mirto", testo)
        self.assertRegex(testo, r"password: [0-9a-f]{6}-[0-9a-f]{6}-[0-9a-f]{6}")
        self.assertIn("CONSERVALA", testo)
        self.assertIn("mirto-nodo abbina", testo, "come farsi un PIN nuovo")
        self.assertEqual(foglio.stat().st_mode & 0o777, 0o600, "la legge solo chi ha la microSD")

    def test_two_cards_get_two_different_passwords(self):
        self.avvia(self.prepara())
        prima = (self.d / "boot" / "mirto-accesso.txt").read_text()
        self.avvia(self.prepara())
        dopo = (self.d / "boot" / "mirto-accesso.txt").read_text()
        self.assertNotEqual(re.search(r"password: (\S+)", prima).group(1),
                            re.search(r"password: (\S+)", dopo).group(1))

    def test_the_node_gets_its_name_from_the_cable_address(self):
        self.avvia(self.prepara())
        self.assertEqual((self.d / "etc" / "hostname").read_text().strip(), "mirto-node-96d3")
        self.assertIn("127.0.1.1\tmirto-node-96d3", (self.d / "etc" / "hosts").read_text())

    def test_the_disk_grows_last_and_in_the_background(self):
        """Il difetto del 20/10: «resize2fs» stava per primo e teneva fermo tutto per un quarto d'ora.

        Su una scheda da 64 GB, partendo da un'immagine rimpicciolita, allargare il filesystem sono
        minuti: finché non finiva non c'erano né password, né PIN, né servizio in ascolto, e il nodo
        sembrava morto. Ora è l'ultimo passo e parte in sottofondo.
        """
        self.avvia(self.prepara())
        righe = self.comandi()
        quando_installa = next(i for i, r in enumerate(righe) if r.startswith("install.sh"))
        quando_allarga = next(i for i, r in enumerate(righe) if r.startswith("systemd-run"))
        self.assertGreater(quando_allarga, quando_installa,
                           "il disco si allarga dopo che il nodo è pronto, non prima")
        self.assertIn("resize2fs", righe[quando_allarga], "e in sottofondo, con systemd-run")
        foglio = righe.index(next(r for r in righe if r.startswith("chpasswd")))
        self.assertLess(foglio, quando_allarga, "il foglio si scrive prima dell'attesa lunga")

    def test_the_sheet_does_not_leave_a_strict_umask_behind(self):
        """Il difetto del 20/10, sera: il nodo non si annunciava più in rete.

        Il passo che scrive il foglio faceva «umask 077», e quella umask restava per tutto il resto del
        copione — compresa l'installazione che viene dopo. I due file di avahi nascevano 0600, e il
        demone, che gira senza privilegi, non poteva leggerli: nodo invisibile a Mirto. Si vedeva solo
        sull'immagine pubblica, perché sull'immagine personale quel ramo non viene nemmeno eseguito.
        """
        self.avvia(self.prepara())
        foglio = self.d / "boot" / "mirto-accesso.txt"
        creato = self.d / "etc" / "finto-annuncio.service"
        self.assertEqual(foglio.stat().st_mode & 0o777, 0o600, "il foglio resta riservato")
        self.assertTrue(creato.exists(), "l'installazione finta non ha creato niente")
        self.assertEqual(creato.stat().st_mode & 0o777, 0o644,
                         "i file creati dopo il foglio nascono con i permessi normali")

    def test_the_first_boot_marker_is_consumed(self):
        self.avvia(self.prepara())
        self.assertFalse((self.d / "conf" / "primo-avvio").exists(), "il primo avvio non si ripete")
        self.assertIn("install.sh --primo-avvio", self.comandi())

    def test_a_password_chosen_in_imager_is_left_alone(self):
        res = self.avvia(self.prepara(stato="P"))
        self.assertFalse((self.d / "boot" / "mirto-accesso.txt").exists())
        self.assertIn("scelta in Raspberry Pi Imager", res.stdout)
        self.assertNotIn("chpasswd", " ".join(self.comandi()), "una password scelta non si tocca")

    def test_without_any_user_the_sheet_says_so(self):
        """Successo il 20/10: nessun utente nell'immagine, e il foglio non veniva scritto affatto."""
        res = self.avvia(self.prepara(utente=""))
        foglio = (self.d / "boot" / "mirto-accesso.txt")
        self.assertTrue(foglio.exists(), "anche la brutta notizia va scritta")
        self.assertIn("NESSUN UTENTE", foglio.read_text())
        self.assertIn("ATTENZIONE", res.stdout)
        self.assertIn("install.sh --primo-avvio", self.comandi(), "il nodo parte lo stesso")


if __name__ == "__main__":
    unittest.main()
