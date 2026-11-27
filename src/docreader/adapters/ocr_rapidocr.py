"""OcrEngine over RapidOCR (ONNX Runtime, CPU): PP-OCRv5 mobile detection + latin recognition."""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR

if TYPE_CHECKING:
    from PIL.Image import Image

# File names inside `models_dir` (as published; see scripts/fetch_ocr_models.py).
DET_MODEL = "ch_PP-OCRv5_det_mobile.onnx"
REC_MODEL = "latin_PP-OCRv5_rec_mobile.onnx"


class RapidOcr:
    """`OcrEngine` for Spanish: PP-OCRv5 latin mobile, the most accurate local model we measured.

    `threads`: ONNX Runtime threads per engine (one engine per worker process); 4 was the
    fastest on a 22-thread laptop, all threads was slower. `det_max_side`: detection input size
    (longest side, px); smaller is faster (1280: -25 %), at 1024 accents started to fail.
    """

    name = "rapidocr"

    def __init__(
        self, models_dir: Path | None = None, *, threads: int = 4, det_max_side: int = 1600
    ) -> None:
        params: dict[str, Any] = {
            "Global.log_level": "warning",
            "Global.use_cls": False,  # pages come upright; per-line 180° check costs time
            "Det.ocr_version": OCRVersion.PPOCRV5,
            "Det.model_type": ModelType.MOBILE,
            "Det.limit_type": "max",
            "Det.limit_side_len": det_max_side,
            # Tighter boxes than the default 1.6 give recognition more width per character:
            # with looser boxes it drops repeated digits (`000000` → `00000`) in comparendo numbers.
            "Det.unclip_ratio": 1.4,
            "Rec.ocr_version": OCRVersion.PPOCRV5,
            "Rec.model_type": ModelType.MOBILE,
            "Rec.lang_type": LangRec.LATIN,
            "EngineConfig.onnxruntime.intra_op_num_threads": threads,
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
        }
        if models_dir is not None:
            for key, file in (("Det.model_path", DET_MODEL), ("Rec.model_path", REC_MODEL)):
                path = Path(models_dir) / file
                if not path.is_file():
                    raise FileNotFoundError(f"no se encontró el modelo OCR {path}")
                params[key] = str(path)
        self._engine = RapidOCR(params=params)

    def recognize(self, image: Image) -> tuple[str, float]:
        result = self._engine(image)
        texts: Sequence[str] | None = getattr(result, "txts", None)
        if not texts:
            return "", 0.0
        boxes = [[(float(x), float(y)) for x, y in box] for box in result.boxes]
        scores = [float(score) for score in result.scores]
        return _reading_order(boxes, list(texts)), statistics.fmean(scores)


Quad = list[tuple[float, float]]  # top-left, top-right, bottom-right, bottom-left


def _reading_order(boxes: list[Quad], texts: list[str]) -> str:
    """One output line per text line, top to bottom and left to right.

    Boxes whose skew-corrected vertical centers are closer than half a line height share a
    line, so `label: value` printed apart on paper ends up on one line.
    """
    slopes = [(tr[1] - tl[1]) / (tr[0] - tl[0]) for tl, tr, _, _ in boxes if tr[0] - tl[0] > 50]
    slope = statistics.median(slopes) if slopes else 0.0
    items = []
    for (tl, tr, br, bl), text in zip(boxes, texts, strict=True):
        x = (tl[0] + bl[0]) / 2
        y = (tl[1] + tr[1] + br[1] + bl[1]) / 4 - slope * (tl[0] + tr[0]) / 2
        height = math.dist(tl, bl)
        items.append((y, x, height, text.strip()))
    items.sort()
    tolerance = statistics.median(item[2] for item in items) / 2
    lines: list[list[tuple[float, str]]] = []
    line_y = -math.inf
    for y, x, _, text in items:
        if y - line_y > tolerance:
            lines.append([])
            line_y = y
        lines[-1].append((x, text))
    return "\n".join(" ".join(text for _, text in sorted(line)) for line in lines)
