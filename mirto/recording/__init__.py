# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Registrazione video (NVR): spezzoni MP4 per camera, clip degli eventi, conservazione.

* ``ffmpeg``   — trova il programma ffmpeg e sceglie il codificatore H.264 migliore disponibile;
* ``store``    — cartelle e spezzoni su disco (i tempi stanno nei nomi dei file, in UTC);
* ``recorder`` — un registratore per camera: copia del flusso RTSP senza ricodifica, oppure codifica
  dei fotogrammi delle camere locali;
* ``manager``  — registratori di tutte le camere, clip degli eventi, spazio su disco, stato.
"""
