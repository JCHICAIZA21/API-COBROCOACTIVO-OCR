"""PdfPageReader: native text first, OCR only for pages without a usable text layer.

The fake OCR engines live at module level so worker processes can unpickle them.
"""

from __future__ import annotations

import io
import os
from collections.abc import Callable
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from PIL import ImageStat
from PIL.Image import Image
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from docreader.adapters.pages import PdfPageReader
from docreader.domain import DocumentRejected, ExtractionContext, PageText
from docreader.ports import OcrEngine, PageReader
from docreader.rules import RegexExtractor
from docreader.templates import load_template
from tests.pdf_factory import Comparendo, Variant, comparendo_pdf

DATA = Comparendo()
SCAN_DPI = 60  # small scans keep the tests fast; the fakes only look at the image
SCAN_SIZE = "510x660"  # letter page at SCAN_DPI


class FakeOcr:
    """Describes the size of the image it receives."""

    name = "fake"

    def recognize(self, image: Image) -> tuple[str, float]:
        return f"imagen {image.size[0]}x{image.size[1]}", 0.87


class PolarityOcr:
    """Says whether the image looks like a light page (dark text on white) and its size."""

    name = "polarity"

    def recognize(self, image: Image) -> tuple[str, float]:
        light = ImageStat.Stat(image.convert("L")).mean[0] > 128
        return f"{'clara' if light else 'oscura'} {image.size[0]}x{image.size[1]}", 0.9


class CountingOcr:
    """Reports how many engines its process has built so far."""

    name = "counting"
    built = 0

    def __init__(self) -> None:
        CountingOcr.built += 1

    def recognize(self, image: Image) -> tuple[str, float]:
        return f"motor {CountingOcr.built}", 0.9


class CrashingOcr:
    """Kills its worker process, as a native crash on a hostile PDF would."""

    name = "crashing"

    def recognize(self, image: Image) -> tuple[str, float]:
        os._exit(3)


def test_is_a_page_reader() -> None:
    assert isinstance(PdfPageReader(None, workers=0), PageReader)


async def test_native_pages_come_from_the_text_layer() -> None:
    pages = await _read(comparendo_pdf(DATA, "native"))

    assert [(p.number, p.origin, p.confidence) for p in pages] == [
        (1, "native", 1.0),
        (2, "native", 1.0),
    ]
    first = pages[0].text
    assert f"COMPARENDO No. {DATA.nro_comparendo}" in first
    assert f"IDENTIFICACIÓN: C.C. {DATA.identificacion}" in first
    assert f"EJECUTADO: {DATA.nombre}" in first
    assert "\r" not in first


async def test_scanned_pages_are_read_with_ocr_from_the_embedded_scan() -> None:
    pages = await _read(comparendo_pdf(DATA, "scanned", dpi=SCAN_DPI))

    assert pages == [
        PageText(number=1, text=f"imagen {SCAN_SIZE}", origin="ocr", confidence=0.87),
        PageText(number=2, text=f"imagen {SCAN_SIZE}", origin="ocr", confidence=0.87),
    ]


async def test_without_ocr_a_page_with_no_text_layer_keeps_zero_confidence() -> None:
    pages = await _read(comparendo_pdf(DATA, "scanned", dpi=SCAN_DPI), ocr=None)

    assert pages == [
        PageText(number=1, text="", origin="native", confidence=0.0),
        PageText(number=2, text="", origin="native", confidence=0.0),
    ]


async def test_mixed_document_uses_ocr_only_where_the_text_layer_is_missing() -> None:
    pages = await _read(comparendo_pdf(DATA, "mixed", dpi=SCAN_DPI))

    assert [(p.number, p.origin, p.confidence) for p in pages] == [
        (1, "native", 1.0),
        (2, "ocr", 0.87),
    ]
    assert DATA.nro_comparendo in pages[0].text


async def test_page_with_too_little_text_is_rendered_at_the_configured_dpi() -> None:
    pages = await _read(_scan_with_caption("Pág. 1"), dpi=100)

    assert pages == [PageText(number=1, text="imagen 850x1100", origin="ocr", confidence=0.87)]


async def test_rotated_scan_is_rendered_upright() -> None:
    pages = await _read(_rotated(comparendo_pdf(DATA, "scanned", dpi=SCAN_DPI), 90), dpi=100)

    assert pages[0].text == "imagen 1100x850"


@pytest.mark.parametrize("mode", ["L", "1"])  # 8-bit JPEG scan; 1-bit CCITT fax-style scan
async def test_embedded_scans_reach_ocr_upright_and_at_their_own_resolution(mode: str) -> None:
    pages = await _read(_image_pdf(_scan_image().convert(mode)), ocr=PolarityOcr)

    assert pages[0].text == f"clara {SCAN_SIZE}"


@pytest.mark.parametrize("pdf", [b"%PDF-1.7\nesto no es un PDF", comparendo_pdf(DATA)[:900]])
async def test_corrupt_pdf_is_rejected(pdf: bytes) -> None:
    with pytest.raises(DocumentRejected, match="dañado"):
        await _read(pdf)


async def test_password_protected_pdf_is_rejected() -> None:
    with pytest.raises(DocumentRejected, match="contraseña"):
        await _read(_encrypted_pdf())


async def test_worker_processes_build_the_ocr_engine_once_and_survive_close() -> None:
    pdf = comparendo_pdf(DATA, "mixed", dpi=SCAN_DPI)
    pages_reader = PdfPageReader(CountingOcr, workers=1)
    try:
        first = await pages_reader.read(pdf)
        second = await pages_reader.read(comparendo_pdf(DATA, "scanned", dpi=SCAN_DPI))
        pages_reader.close()
        after_close = await pages_reader.read(pdf)
    finally:
        pages_reader.close()

    assert [p.origin for p in first] == ["native", "ocr"]
    assert DATA.identificacion in first[0].text
    assert [p.text for p in (first[1], *second, after_close[1])] == ["motor 1"] * 4
    assert CountingOcr.built == 0  # never built in the calling process


async def test_a_document_that_kills_its_worker_is_rejected_and_the_reader_recovers() -> None:
    pages_reader = PdfPageReader(CrashingOcr, workers=1)
    try:
        with pytest.raises(DocumentRejected, match="no se pudo procesar"):
            await pages_reader.read(comparendo_pdf(DATA, "scanned", dpi=SCAN_DPI))
        pages = await pages_reader.read(comparendo_pdf(DATA, "native"))
    finally:
        pages_reader.close()

    assert [p.origin for p in pages] == ["native", "native"]


@pytest.mark.parametrize("variant", ["native", "mixed"])
async def test_factory_labels_match_the_comparendo_template(variant: Variant) -> None:
    data = Comparendo(identificacion="79.876.543", placa="XYZ12C", valor="$ 980.500")
    template = load_template(Path("templates/comparendo.yaml"))
    pages = await _read(comparendo_pdf(data, variant, dpi=SCAN_DPI), ocr=None)

    found = await RegexExtractor().extract(
        ExtractionContext(template=template, fields=template.fields, pages=tuple(pages), pdf=b"")
    )

    assert {name: candidate.value for name, candidate in found.items()} == {
        "nro_comparendo": data.nro_comparendo,
        "identificacion": data.identificacion,
        "fecha": data.fecha,
        "placa": data.placa,
        "valor": "980.500",
    }


# --- Helpers ---------------------------------------------------------------------------------


async def _read(
    pdf: bytes, ocr: Callable[[], OcrEngine] | None = FakeOcr, dpi: int = 200
) -> list[PageText]:
    """Read with a fresh in-process reader."""
    pages_reader = PdfPageReader(ocr, workers=0, dpi=dpi)
    try:
        return await pages_reader.read(pdf)
    finally:
        pages_reader.close()


def _scan_image() -> Image:
    scan = pdfium.PdfDocument(comparendo_pdf(DATA, "scanned", dpi=SCAN_DPI))
    return next(scan[0].get_objects(max_depth=1)).get_bitmap().to_pil().copy()


def _image_pdf(image: Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "PDF", resolution=SCAN_DPI)
    return buffer.getvalue()


def _scan_with_caption(caption: str) -> bytes:
    """A full-page scan plus a few characters of real text (e.g. a page number stamp)."""
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter, invariant=1)
    pdf.drawImage(ImageReader(_scan_image()), 0, 0, *letter)
    pdf.drawString(500, 20, caption)
    pdf.save()
    return buffer.getvalue()


def _rotated(pdf: bytes, degrees: int) -> bytes:
    document = pdfium.PdfDocument(pdf)
    for page in document:
        page.set_rotation(degrees)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _encrypted_pdf() -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, encrypt="secreto")
    pdf.drawString(60, 700, "Documento protegido con contraseña de apertura")
    pdf.save()
    return buffer.getvalue()
