"""OcrEngine over Tesseract 5 (tesserocr; the wheel bundles the library, tessdata goes apart)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from tesserocr import PSM, PyTessBaseAPI

if TYPE_CHECKING:
    from PIL.Image import Image


class TesseractOcr:
    """`OcrEngine` with one Tesseract instance, created once and reused for every page."""

    name = "tesseract"

    def __init__(self, lang: str = "spa", models_dir: Path | None = None) -> None:
        tessdata = models_dir or os.environ.get("TESSDATA_PREFIX")
        if tessdata is None:
            raise FileNotFoundError("falta el directorio tessdata (models_dir o TESSDATA_PREFIX)")
        for code in lang.split("+"):
            model = Path(tessdata) / f"{code}.traineddata"
            if not model.is_file():
                raise FileNotFoundError(f"no se encontró el modelo OCR {model}")
        self._api = PyTessBaseAPI(path=str(tessdata), lang=lang, psm=PSM.AUTO)

    def recognize(self, image: Image) -> tuple[str, float]:
        self._api.SetImage(image)
        text = self._api.GetUTF8Text()
        lines = (line.strip() for line in text.splitlines())
        return "\n".join(line for line in lines if line), self._api.MeanTextConf() / 100
