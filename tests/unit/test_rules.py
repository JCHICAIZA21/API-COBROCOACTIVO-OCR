from pathlib import Path

from docreader.domain import (
    Candidate,
    DocumentTemplate,
    ExtractionContext,
    FieldSpec,
    PageText,
)
from docreader.normalize import normalize
from docreader.ports import FieldExtractor
from docreader.rules import RegexExtractor
from docreader.templates import load_template

COMPARENDO_YAML = Path(__file__).resolve().parents[2] / "templates" / "comparendo.yaml"
PLACA = FieldSpec(name="placa", kind="plate", patterns=(r"placa\s*:?\s*(?P<value>[A-Z0-9-]+)",))


def context(
    *texts: str, fields: tuple[FieldSpec, ...] = (PLACA,), ocr: bool = False
) -> ExtractionContext:
    template = DocumentTemplate(id="t", version="1", fields=fields)
    pages = tuple(
        PageText(
            number=i,
            text=text,
            origin="ocr" if ocr else "native",
            confidence=0.7 if ocr else 1.0,
        )
        for i, text in enumerate(texts, start=1)
    )
    return ExtractionContext(template=template, fields=fields, pages=pages, pdf=b"%PDF-")


async def test_extracts_named_group_value_with_page_and_confidence() -> None:
    extractor = RegexExtractor()

    found = await extractor.extract(context("Vehículo\nPLACA: ABC123\nfin", ocr=True))

    assert found == {
        "placa": Candidate(
            value="ABC123",
            confidence=0.7,
            engine="rules",
            page=1,
            snippet="Vehículo PLACA: ABC123 fin",
        )
    }


async def test_whole_match_is_the_value_without_named_group() -> None:
    field = FieldSpec(name="placa", kind="plate", patterns=(r"\b[A-Z]{3}\d{3}\b",))

    found = await RegexExtractor().extract(context("vehículo xyz987 parqueado", fields=(field,)))

    assert found["placa"].value == "xyz987"


async def test_skips_matches_that_do_not_normalize() -> None:
    found = await RegexExtractor().extract(context("placa: N/A\nplaca: 12\nplaca: DEF-456"))

    assert found["placa"].value == "DEF-456"


async def test_earlier_pattern_wins_over_earlier_page() -> None:
    field = FieldSpec(
        name="placa",
        kind="plate",
        patterns=(r"placa del veh[ií]culo\s+(?P<value>\w+)", r"placa\s+(?P<value>\w+)"),
    )

    found = await RegexExtractor().extract(
        context("placa AAA111", "placa del vehículo BBB222", fields=(field,))
    )

    assert (found["placa"].value, found["placa"].page) == ("BBB222", 2)


async def test_earlier_page_wins_for_the_same_pattern() -> None:
    found = await RegexExtractor().extract(context("placa AAA111", "placa BBB222"))

    assert (found["placa"].value, found["placa"].page) == ("AAA111", 1)


async def test_only_requested_fields_and_only_found_ones() -> None:
    fecha = FieldSpec(name="fecha", kind="date", patterns=(r"fecha:\s*(?P<value>\S+)",))
    valor = FieldSpec(name="valor", kind="money", patterns=(r"valor:\s*(?P<value>\S+)",))
    template = DocumentTemplate(id="t", version="1", fields=(PLACA, fecha, valor))
    ctx = ExtractionContext(
        template=template,
        fields=(fecha, valor),
        pages=(PageText(number=1, text="placa: ABC123 fecha: 15/03/2024", origin="native"),),
        pdf=b"%PDF-",
    )

    found = await RegexExtractor().extract(ctx)

    assert list(found) == ["fecha"]
    assert found["fecha"].value == "15/03/2024"


async def test_snippet_is_40_chars_around_the_value_on_one_line() -> None:
    before = "x" * 60 + "\n" + "a" * 30 + " placa: "  # last 40 chars: "x\n" + 30 a + " placa: "
    after = "\tbbbb\n" + "c" * 60  # first 40 chars: "\tbbbb\n" + 34 c

    found = await RegexExtractor().extract(context(before + "ABC123" + after))

    assert found["placa"].snippet == "x " + "a" * 30 + " placa: ABC123 bbbb " + "c" * 34


async def test_comparendo_template_reads_a_synthetic_comparendo() -> None:
    template = load_template(COMPARENDO_YAML)
    text = (
        "SECRETARÍA DISTRITAL DE MOVILIDAD\n"
        "ORDEN DE COMPARENDO No. 11001000000012345678\n"
        "Fecha de imposición: 15/03/2024\n"
        "Infractor: PERSONA DE PRUEBA\n"
        "C.C. No. 1.023.456.789\n"
        "Placa: abc-123\n"
        "Valor de la multa: $ 1.234.567\n"
    )
    ctx = ExtractionContext(
        template=template,
        fields=template.fields,
        pages=(PageText(number=1, text=text, origin="native"),),
        pdf=b"%PDF-",
    )

    found = await RegexExtractor().extract(ctx)

    normalized = {f.name: normalize(f.kind, found[f.name].value) for f in template.fields}
    assert normalized == {
        "nro_comparendo": "11001000000012345678",
        "identificacion": "1023456789",
        "fecha": "2024-03-15",
        "placa": "ABC123",
        "valor": "1234567",
    }


def test_is_a_local_field_extractor() -> None:
    extractor = RegexExtractor()

    assert isinstance(extractor, FieldExtractor)
    assert extractor.name == "rules"
    assert extractor.external is False
