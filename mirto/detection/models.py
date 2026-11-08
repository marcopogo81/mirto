# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Modelli di riconoscimento: persone e oggetti (YOLOX), specie degli uccelli, suoni (YAMNet).

Tutti con licenza Apache 2.0. I file vengono scaricati al primo uso e verificati con
l'impronta SHA-256, così nessuno può sostituirli. Per i modelli aggiunti dopo
(``ASSETS``) l'impronta si fissa al primo download ("trust on first use") e da lì in
poi viene controllata a ogni avvio; ``python -m mirto --verifica-modelli`` stampa le
impronte da scrivere qui nel codice prima di una release.
"""

from __future__ import annotations

import hashlib
import logging
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from ..config import data_dir
from ..netssl import ssl_context
from ..i18n import N_, _

log = logging.getLogger(__name__)

BASE = "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/"


@dataclass(frozen=True)
class ModelSpec:
    name: str
    url: str
    sha256: str
    input_size: int
    description: str


MODELS = {
    "yolox_nano": ModelSpec(
        "yolox_nano", BASE + "yolox_nano.onnx",
        "c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d", 416,
        N_("veloce, ~3,7 MB: adatto all'uso continuo su CPU"),
    ),
    "yolox_s": ModelSpec(
        "yolox_s", BASE + "yolox_s.onnx",
        "c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063", 640,
        N_("più preciso, ~36 MB: consigliato su Apple Silicon"),
    ),
}


@dataclass(frozen=True)
class Asset:
    name: str          # nome del file nella cartella dei modelli
    url: str
    sha256: str        # vuoto = si fissa al primo download (vedi sopra)
    description: str


CORAL = "https://github.com/google-coral/test_data/raw/master/"
YAMNET = "https://huggingface.co/audiomagic/yamnet-onnx/resolve/main/"
ASSETS = {
    "birds_model": Asset("inat_bird_mobilenet_v2_quant.tflite", CORAL + "mobilenet_v2_1.0_224_inat_bird_quant.tflite",
                         "350fcd8cf1df1560060d464595dfed8b174b05792788052896004848d9ad04f9",
                         N_("specie degli uccelli (iNaturalist, 965 specie, ~3,5 MB)")),
    "birds_labels": Asset("inat_bird_labels.txt", CORAL + "inat_bird_labels.txt",
                          "a16108dfe3f8daff015b87a97ab6a17e717b9b1bccd719f6d8f747746d7b9277",
                          N_("nomi delle specie degli uccelli")),
    "yamnet_model": Asset("yamnet.onnx", YAMNET + "yamnet.onnx",
                          "d3835ffbbd4a1bb3e777f0ca217b5007907f5171dd5d17c4236b95b2af8f908e",
                          N_("suoni (YAMNet, 521 tipi di suono, ~15 MB)")),
    "yamnet_labels": Asset("yamnet_class_map.csv", YAMNET + "yamnet_class_map.csv",
                           "cdf24d193e196d9e95912a2667051ae203e92a2ba09449218ccb40ef787c6df2",
                           N_("nomi dei suoni")),
}


def models_dir() -> Path:
    override = os.environ.get("MIRTO_MODELS_DIR")
    p = Path(override) if override else data_dir() / "models"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_model(name: str) -> tuple[Path, ModelSpec]:
    """Restituisce il percorso del modello, scaricandolo e verificandolo se serve."""
    spec = MODELS.get(name)
    if spec is None:
        raise ValueError(_('Modello sconosciuto: {name}. Disponibili: {join}', name=name, join=', '.join(MODELS)))
    path = models_dir() / f"{spec.name}.onnx"
    if path.exists() and _sha256(path) == spec.sha256:
        return path, spec
    log.info("Scarico il modello %s (%s)…", spec.name, spec.description)
    tmp = path.with_suffix(".part")
    with urllib.request.urlopen(spec.url, timeout=60, context=ssl_context()) as resp, open(tmp, "wb") as out:  # noqa: S310
        while chunk := resp.read(1 << 20):
            out.write(chunk)
    digest = _sha256(tmp)
    if digest != spec.sha256:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(_('Impronta del modello non valida ({digest}): download annullato', digest=digest))
    os.replace(tmp, path)
    return path, spec


def _download(url: str, dest: Path) -> str:
    """Scarica in un file temporaneo e restituisce (dopo averlo spostato) l'impronta SHA-256."""
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "Mirto"})
    with urllib.request.urlopen(req, timeout=60, context=ssl_context()) as resp, open(tmp, "wb") as out:  # noqa: S310
        while chunk := resp.read(1 << 20):
            out.write(chunk)
    return _sha256(tmp)


def ensure_asset(key: str, download: bool = True) -> Path:
    """Percorso di un file di ``ASSETS``, scaricato e verificato se serve.

    Impronta nel codice: deve coincidere. Altrimenti si usa quella salvata al primo download
    (``<file>.sha256``): un file cambiato dopo viene rifiutato.
    """
    asset = ASSETS[key]
    path = models_dir() / asset.name
    pin_file = path.with_suffix(path.suffix + ".sha256")
    expected = asset.sha256 or (pin_file.read_text().strip() if pin_file.exists() else "")
    if path.exists():
        digest = _sha256(path)
        if not expected or digest == expected:
            if not expected:
                pin_file.write_text(digest + "\n")
            return path
        log.warning("%s: impronta cambiata (%s), lo riscarico", asset.name, digest)
    if not download:
        raise FileNotFoundError(asset.name)
    log.info("Scarico %s (%s)…", asset.name, asset.description)
    digest = _download(asset.url, path)
    tmp = path.with_suffix(path.suffix + ".part")
    if expected and digest != expected:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(_('Impronta del modello non valida ({digest}): download annullato', digest=digest))
    os.replace(tmp, path)
    if not asset.sha256:
        pin_file.write_text(digest + "\n")
        log.info("%s: impronta fissata al primo download: %s", asset.name, digest)
    return path


def asset_digests() -> dict[str, str]:
    """Impronte dei file presenti (per fissarle nel codice: ``--verifica-modelli``)."""
    out = {}
    for key, asset in ASSETS.items():
        p = models_dir() / asset.name
        out[key] = _sha256(p) if p.exists() else ""
    return out
