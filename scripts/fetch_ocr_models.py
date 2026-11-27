"""Download the pinned OCR models into one directory (used by the Docker build).

Usage: python scripts/fetch_ocr_models.py <dest_dir>

Standard library only, so it runs before the project is installed. Files keep their upstream
names, which is what `RapidOcr(models_dir=...)` and `TesseractOcr(models_dir=...)` expect.
Existing files with the right checksum are kept; anything else is downloaded and verified.
"""

from __future__ import annotations

import hashlib
import shutil
import sys
import urllib.request
from pathlib import Path
from typing import NamedTuple

_RAPIDOCR = "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/onnx/PP-OCRv5"
_TESSDATA_FAST = "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0"


class Model(NamedTuple):
    name: str
    url: str
    sha256: str


# RapidOCR checksums: rapidocr 3.10 `default_models.yaml`. spa: tessdata_fast tag 4.1.0.
MODELS = (
    Model(
        "ch_PP-OCRv5_det_mobile.onnx",
        f"{_RAPIDOCR}/det/ch_PP-OCRv5_det_mobile.onnx",
        "4d97c44a20d30a81aad087d6a396b08f786c4635742afc391f6621f5c6ae78ae",
    ),
    Model(
        "latin_PP-OCRv5_rec_mobile.onnx",
        f"{_RAPIDOCR}/rec/latin_PP-OCRv5_rec_mobile.onnx",
        "b20bd37c168a570f583afbc8cd7925603890efbcdc000a59e22c269d160b5f5a",
    ),
    Model(
        "spa.traineddata",
        f"{_TESSDATA_FAST}/spa.traineddata",
        "6f2e04d02774a18f01bed44b1111f2cd7f3ba7ac9dc4373cd3f898a40ea6b464",
    ),
)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(model: Model, dest: Path) -> None:
    """Download one model into `dest` unless a verified copy is already there."""
    target = dest / model.name
    if target.exists() and sha256_of(target) == model.sha256:
        print(f"ok (ya existe) {model.name}")
        return
    partial = target.with_name(target.name + ".part")
    # Pinned https URLs only (constants above).
    with (
        urllib.request.urlopen(model.url, timeout=120) as response,  # noqa: S310
        partial.open("wb") as out,
    ):
        shutil.copyfileobj(response, out)
    actual = sha256_of(partial)
    if actual != model.sha256:
        partial.unlink()
        raise SystemExit(f"checksum inválido para {model.name}: {actual}")
    partial.replace(target)
    print(f"ok (descargado) {model.name}")


def main(argv: list[str]) -> None:
    if len(argv) != 2:
        raise SystemExit("uso: python scripts/fetch_ocr_models.py <directorio_destino>")
    dest = Path(argv[1])
    dest.mkdir(parents=True, exist_ok=True)
    for model in MODELS:
        fetch(model, dest)


if __name__ == "__main__":
    main(sys.argv)
