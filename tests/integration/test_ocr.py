"""PdfPageReader with the real OCR engines on synthetic comparendos.

Needs the OCR models: DOCREADER_TEST_OCR_MODELS (default ~/.cache/docreader-models), filled with
`python scripts/fetch_ocr_models.py <dir>`. Run with `-s` to see the timings.
"""

from __future__ import annotations

import os
import statistics
import time
from collections.abc import Callable, Iterator
from functools import partial
from pathlib import Path

import pytest

pytest.importorskip("rapidocr")
pytest.importorskip("tesserocr")

from docreader.adapters.ocr_rapidocr import DET_MODEL, REC_MODEL, RapidOcr
from docreader.adapters.ocr_tesseract import TesseractOcr
from docreader.adapters.pages import PdfPageReader
from docreader.domain import ExtractionContext, PageText
from docreader.normalize import normalize
from docreader.ports import OcrEngine
from docreader.rules import RegexExtractor
from docreader.templates import load_template
from tests.pdf_factory import Comparendo, Variant, comparendo_pdf

MODELS = Path(
    os.environ.get("DOCREADER_TEST_OCR_MODELS", Path.home() / ".cache" / "docreader-models")
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not all((MODELS / name).is_file() for name in (DET_MODEL, REC_MODEL, "spa.traineddata")),
        reason=f"faltan los modelos OCR en {MODELS} (scripts/fetch_ocr_models.py)",
    ),
]

ENGINES: dict[str, Callable[[], OcrEngine]] = {
    "rapidocr": partial(RapidOcr, models_dir=MODELS),
    "tesseract": partial(TesseractOcr, lang="spa", models_dir=MODELS),
}
DATASETS = (
    Comparendo(),
    Comparendo(
        nro_comparendo="05001000000098765432",
        identificacion="52.987.604",
        fecha="15/11/2023",
        placa="KDM07F",
        valor="$ 468.300",
        nombre="MARÍA JOSÉ NÚÑEZ ARIZA",
    ),
)
ORIGINS: dict[Variant, list[str]] = {
    "native": ["native", "native"],
    "scanned": ["ocr", "ocr"],
    "mixed": ["native", "ocr"],
}
TEMPLATE = load_template(Path("templates/comparendo.yaml"))
MIN_CONFIDENCE = 0.85  # ReaderSettings.min_confidence: below it, fields need review


@pytest.fixture(scope="module", params=list(ENGINES))
def engine(request: pytest.FixtureRequest) -> Iterator[tuple[str, PdfPageReader]]:
    name: str = request.param
    pages = PdfPageReader(ENGINES[name], workers=1, dpi=200)
    yield name, pages
    pages.close()


@pytest.mark.parametrize("name", list(ENGINES))
def test_engines_implement_the_port(name: str) -> None:
    engine = ENGINES[name]()

    assert isinstance(engine, OcrEngine)
    assert engine.name == name


@pytest.mark.parametrize("data", DATASETS, ids=["datos-1", "datos-2"])
@pytest.mark.parametrize("variant", list(ORIGINS))
async def test_key_fields_are_read_from_every_variant(
    engine: tuple[str, PdfPageReader], variant: Variant, data: Comparendo
) -> None:
    _, reader = engine
    pages = await reader.read(comparendo_pdf(data, variant, seed=DATASETS.index(data)))

    assert [page.origin for page in pages] == ORIGINS[variant]
    assert await _key_fields(pages) == {
        "nro_comparendo": normalize("id_number", data.nro_comparendo),
        "identificacion": normalize("id_number", data.identificacion),
        "fecha": normalize("date", data.fecha),
    }


async def test_ocr_confidence_clears_the_review_threshold(
    engine: tuple[str, PdfPageReader], request: pytest.FixtureRequest
) -> None:
    name, reader = engine
    if name == "tesseract":
        reason = "MeanTextConf de Tesseract ronda 0,81-0,86 en estos escaneos: hay que calibrarla"
        request.applymarker(pytest.mark.xfail(reason=reason))

    pages = await reader.read(comparendo_pdf(Comparendo(), "scanned"))

    assert min(page.confidence for page in pages) >= MIN_CONFIDENCE


async def test_timing_per_page(engine: tuple[str, PdfPageReader]) -> None:
    name, reader = engine
    native = await _p50_ms_per_page(reader, comparendo_pdf(Comparendo(), "native"), runs=20)
    scanned = await _p50_ms_per_page(reader, comparendo_pdf(Comparendo(), "scanned"), runs=3)

    print(f"\n{name} (workers=1, 200 ppp): nativo p50 {native:.1f} ms/página, ", end="")
    print(f"escaneado p50 {scanned:.0f} ms/página")


async def _key_fields(pages: list[PageText]) -> dict[str, str | None]:
    """The template's key fields as the reader would see them: regex match, then normalized."""
    keys = tuple(spec for spec in TEMPLATE.fields if spec.key)
    context = ExtractionContext(template=TEMPLATE, fields=keys, pages=tuple(pages), pdf=b"")
    found = await RegexExtractor().extract(context)
    kinds = {spec.name: spec.kind for spec in keys}
    return {name: normalize(kinds[name], candidate.value) for name, candidate in found.items()}


async def _p50_ms_per_page(reader: PdfPageReader, pdf: bytes, runs: int) -> float:
    pages = len(await reader.read(pdf))  # warm-up
    times = []
    for _ in range(runs):
        start = time.perf_counter()
        await reader.read(pdf)
        times.append((time.perf_counter() - start) * 1000 / pages)
    return statistics.median(times)
