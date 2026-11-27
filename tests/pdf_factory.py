"""Synthetic Colombian comparendo / "mandamiento de pago" PDFs for tests. No real data.

    pdf = comparendo_pdf(Comparendo(placa="XYZ12C"), "scanned")

Variants: `native` (text layer), `scanned` (one degraded page image per page, no text layer) and
`mixed` (page 1 native, page 2 scanned). Page 1 holds every field with the labels that
`templates/comparendo.yaml` looks for; page 2 repeats the identification and the comparendo.
"""

from __future__ import annotations

import io
import random
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

import pypdfium2 as pdfium
from PIL import Image, ImageFilter
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

Variant = Literal["native", "scanned", "mixed"]

_BOLD, _REGULAR = "Helvetica-Bold", "Helvetica"


@dataclass(frozen=True)
class Comparendo:
    """Values printed in the document, formatted as they appear on paper."""

    nro_comparendo: str = "11001000000045678912"
    identificacion: str = "1.023.456.789"
    fecha: str = "02/01/2024"
    placa: str = "HKL482"
    valor: str = "$ 1.234.567"
    nombre: str = "JOSÉ ÁNGEL MUÑOZ PEÑA"


def comparendo_pdf(
    data: Comparendo | None = None, variant: Variant = "native", *, dpi: int = 200, seed: int = 0
) -> bytes:
    """A 2-page document. `dpi` and `seed` shape the simulated scan (deterministic)."""
    return _build(data or Comparendo(), variant, dpi, seed)


def page_texts(data: Comparendo | None = None) -> list[str]:
    """Ground truth: the text of each page, one printed line per line."""
    return ["\n".join(text for _, _, text in page) for page in _pages(data or Comparendo())]


@lru_cache(maxsize=64)
def _build(data: Comparendo, variant: Variant, dpi: int, seed: int) -> bytes:
    native = _native(data)
    if variant == "native":
        return native
    scanned = _scanned(native, dpi, seed)
    if variant == "scanned":
        return scanned
    mixed = pdfium.PdfDocument.new()
    mixed.import_pages(pdfium.PdfDocument(native), [0])
    mixed.import_pages(pdfium.PdfDocument(scanned), [1])
    buffer = io.BytesIO()
    mixed.save(buffer)
    return buffer.getvalue()


def _pages(d: Comparendo) -> list[list[tuple[str, int, str]]]:
    """(font, size, text) per printed line, per page."""
    first = [
        (_BOLD, 13, "SECRETARÍA DISTRITAL DE MOVILIDAD"),
        (_BOLD, 11, "OFICINA DE GESTIÓN DE COBRO - JURISDICCIÓN COACTIVA"),
        (_BOLD, 11, "RESOLUCIÓN No. 2024-118734 DEL 15 DE MARZO DE 2024"),
        *_body("""
            Por medio de la cual se libra mandamiento de pago dentro del proceso coactivo

        """),
        (_BOLD, 10, f"EJECUTADO: {d.nombre}"),
        (_BOLD, 10, f"IDENTIFICACIÓN: C.C. {d.identificacion}"),
        (_BOLD, 10, f"COMPARENDO No. {d.nro_comparendo}"),
        (_BOLD, 10, f"FECHA DE IMPOSICIÓN: {d.fecha}"),
        (_BOLD, 10, f"PLACA: {d.placa}"),
        (_BOLD, 10, f"VALOR DE LA MULTA: {d.valor}"),
        *_body("""
            INFRACCIÓN: C29 - Conducir un vehículo a velocidad superior a la máxima.

            El suscrito funcionario ejecutor, en uso de las facultades conferidas por la
            Ley 1066 de 2006, el artículo 826 del Estatuto Tributario y la Ley 769 de 2002
            (Código Nacional de Tránsito), y teniendo en cuenta que la sanción impuesta se
            encuentra en firme y constituye una obligación clara, expresa y exigible a cargo
            del ejecutado, procede a librar mandamiento de pago por la vía coactiva.

            Que revisado el sistema de información, el señor ejecutado no acreditó el pago
            de la multa ni la celebración de un acuerdo de pago dentro del término legal.
            Dirección de notificación: Calle 26 Sur No. 68-45, Bogotá D.C.
        """),
    ]
    second = [
        (_BOLD, 11, "RESUELVE"),
        *_body(f"""

            ARTÍCULO PRIMERO: Librar mandamiento de pago a favor de la Secretaría Distrital
            de Movilidad y en contra de {d.nombre}, identificado con
            C.C. {d.identificacion}, por la multa del comparendo {d.nro_comparendo}.

            ARTÍCULO SEGUNDO: Notificar personalmente el presente acto al ejecutado o a
            su apoderado, previa citación para que comparezca dentro de los diez (10) días.

            ARTÍCULO TERCERO: Advertir al ejecutado que dispone de quince (15) días para
            pagar o proponer las excepciones del artículo 831 del Estatuto Tributario.

        """),
        (_BOLD, 10, "NOTIFÍQUESE Y CÚMPLASE"),
        *_body("""
            Dada en Bogotá D.C., a los quince (15) días del mes de marzo.

        """),
        (_BOLD, 10, "MARÍA FERNANDA LÓPEZ GÜIZA"),
        (_REGULAR, 10, "Funcionaria Ejecutora - Subdirección de Jurisdicción Coactiva"),
        (_REGULAR, 9, "Proyectó: Andrés Núñez Ibáñez - Revisó: Ángela Rodríguez Peñaloza"),
    ]
    return [first, second]


def _body(block: str) -> list[tuple[str, int, str]]:
    """Regular 10 pt lines of an indented block; inner blank lines are kept."""
    return [(_REGULAR, 10, line.strip()) for line in block.splitlines()[1:-1]]


def _native(data: Comparendo) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter, invariant=1)
    for page in _pages(data):
        y = 740
        for font, size, text in page:
            pdf.setFont(font, size)
            pdf.drawString(60, y, text)
            y -= size + 9
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def _scanned(native: bytes, dpi: int, seed: int) -> bytes:
    """Rasterize every page and degrade it like a mediocre office scan; image-only PDF."""
    document = pdfium.PdfDocument(native)
    images = [
        _degrade(page.render(scale=dpi / 72, grayscale=True).to_pil(), seed + index)
        for index, page in enumerate(document)
    ]
    buffer = io.BytesIO()
    images[0].save(buffer, "PDF", resolution=dpi, save_all=True, append_images=images[1:])
    return buffer.getvalue()


def _degrade(image: Image.Image, seed: int) -> Image.Image:
    """Tilt, blur, salt-and-pepper speckles and JPEG artifacts."""
    rnd = random.Random(seed)
    gray = image.convert("L").rotate(0.8, resample=Image.Resampling.BICUBIC, fillcolor=255)
    gray = gray.filter(ImageFilter.GaussianBlur(0.6))
    pixels = gray.load()
    assert pixels is not None
    width, height = gray.size
    for _ in range(width * height // 60):
        pixels[rnd.randrange(width), rnd.randrange(height)] = rnd.choice((0, 90, 200))
    buffer = io.BytesIO()
    gray.save(buffer, "JPEG", quality=65)
    return Image.open(io.BytesIO(buffer.getvalue()))
