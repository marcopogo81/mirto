# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
# Pacchetto Mirto con PyInstaller.
#   macOS:   pyinstaller packaging/mirto.spec   → dist/Mirto.app
#   Windows: pyinstaller packaging/mirto.spec   → dist/Mirto/Mirto.exe
# Il modello di riconoscimento viene scaricato al primo avvio (non incluso).
# -*- mode: python ; coding: utf-8 -*-
import os
import sys

from PyInstaller.utils.hooks import collect_dynamic_libs

sys.path.insert(0, os.path.dirname(SPECPATH))  # noqa: F821  (SPECPATH lo definisce PyInstaller)
from mirto import __version__ as MIRTO_VERSION  # noqa: E402  (la versione sta in un posto solo)

block_cipher = None

# ffmpeg per la registrazione video (dal pacchetto imageio-ffmpeg): va tra i binari, così su macOS viene firmato
binaries = []
try:
    import imageio_ffmpeg

    binaries.append((imageio_ffmpeg.get_ffmpeg_exe(), "imageio_ffmpeg/binaries"))
except Exception as exc:  # noqa: BLE001
    print("ATTENZIONE: ffmpeg non incluso (registrazione video non disponibile):", exc)
binaries += collect_dynamic_libs("ai_edge_litert")  # motore del modello delle specie degli uccelli
binaries += collect_dynamic_libs("onnxruntime")     # su Windows porta con sé le DLL di DirectML

# Si parte da «avvia.py», non da «mirto/__main__.py»: PyInstaller uno script lo esegue come
# programma a se', e li' le importazioni relative del pacchetto non hanno piu' un pacchetto a cui
# riferirsi - l'app si chiudeva alla prima riga, prima di mostrare qualcosa (22/10, primo .dmg aperto).
a = Analysis(
    ["avvia.py"],
    pathex=[".."],
    binaries=binaries,
    hiddenimports=["mirto.__main__", "ai_edge_litert.interpreter", "imageio_ffmpeg", "mirto.gui.app", "mirto.actions.simulator", "keyring.backends", "PySide6.QtSvg",
                   "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "mirto.gui.mic_qt", "mirto.gui.review"],
    datas=[("../mirto/gui/icons", "mirto/gui/icons"), ("../mirto/locales", "mirto/locales"),
           # Il codice del nodo viaggia **dentro** l'app: da N-02 è quello che Mirto manda a un
           # nodo per aggiornarlo, quindi un pacchetto senza questa cartella sa comandare i nodi ma
           # non sa aggiornarli — e il pulsante sparirebbe senza che nessuno capisca perché.
           ("../node", "node"),
           ("../GUIDA_UTENTE.md", "."), ("../USER_GUIDE.md", "."), ("../LICENSE", "."), ("../NOTICE", "."), ("../THIRD_PARTY_NOTICES.md", ".")],
    excludes=["tkinter"],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Mirto",
    console=False,
    icon="../mirto/gui/icons/app/app.ico",   # icona su Windows (barra delle applicazioni)
)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, name="Mirto")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Mirto.app",
        icon="../mirto/gui/icons/app/app.icns",  # icona nel Dock e nel Finder
        bundle_identifier="it.mirto.app",
        info_plist={
            "CFBundleName": "Mirto",
            "CFBundleShortVersionString": MIRTO_VERSION,
            # Obbligatorio: senza questa voce macOS nega l'accesso alla camera
            "NSCameraUsageDescription": "Mirto usa la camera per la videosorveglianza della casa.",
            # Solo se attivi "Ascolta il microfono del Mac" (Impostazioni → Suoni): l'audio non viene salvato
            "NSMicrophoneUsageDescription": "Mirto ascolta i suoni (vetro rotto, allarme fumo…) per avvisarti. L'audio resta sul Mac.",
            "NSLocalNetworkUsageDescription": "Mirto comunica con i nodi Mirto nella rete di casa.",
            "NSBonjourServices": ["_mirto-node._tcp", "_rtsp._tcp"],
            "LSMinimumSystemVersion": "12.0",
        },
    )
