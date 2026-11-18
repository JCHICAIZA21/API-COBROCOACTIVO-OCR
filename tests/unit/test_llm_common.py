import pytest

from docreader.adapters.llm_common import (
    needs_document,
    output_schema,
    parse_candidates,
    user_prompt,
)
from docreader.domain import (
    Candidate,
    DocumentTemplate,
    ExtractionContext,
    ExtractorError,
    FieldSpec,
    PageText,
)

FIELDS = (
    FieldSpec(name="nro_comparendo", description="Número del comparendo."),
    FieldSpec(name="placa", description="Placa del vehículo.", required=False),
)
TEMPLATE = DocumentTemplate(
    id="comparendo", version="0.1.0", description="Comparendo de tránsito", fields=FIELDS
)
NATIVE = PageText(number=1, text="Comparendo No. 12345", origin="native")
SCANNED = PageText(number=2, text="Placa ABC123", origin="ocr", confidence=0.7)


def test_document_is_needed_only_when_some_page_came_from_ocr():
    assert needs_document([NATIVE]) is False
    assert needs_document([NATIVE, SCANNED]) is True


def test_prompt_names_the_document_type_and_marks_each_page():
    ctx = ExtractionContext(template=TEMPLATE, fields=FIELDS, pages=(NATIVE, SCANNED), pdf=b"%PDF")

    prompt = user_prompt(ctx)

    assert "Comparendo de tránsito" in prompt
    page1, page2 = prompt.index("--- Página 1 ---"), prompt.index("--- Página 2 ---")
    assert page1 < prompt.index("Comparendo No. 12345") < page2 < prompt.index("Placa ABC123")


def test_schema_asks_every_pending_field_as_nullable_string():
    assert output_schema(FIELDS) == {
        "type": "object",
        "properties": {
            "nro_comparendo": {"type": ["string", "null"], "description": "Número del comparendo."},
            "placa": {"type": ["string", "null"], "description": "Placa del vehículo."},
        },
        "required": ["nro_comparendo", "placa"],
        "additionalProperties": False,
    }


def test_found_values_become_candidates_and_absent_ones_are_omitted():
    text = '{"nro_comparendo": " 12345 ", "placa": null, "otro": "x"}'

    found = parse_candidates(text, FIELDS, engine="claude:modelo", confidence=0.9)

    assert found == {
        "nro_comparendo": Candidate(value="12345", confidence=0.9, engine="claude:modelo")
    }


def test_empty_strings_are_treated_as_not_found():
    found = parse_candidates('{"nro_comparendo": "  ", "placa": ""}', FIELDS, engine="e", confidence=1)

    assert found == {}


@pytest.mark.parametrize(
    "text",
    [
        '{"nro_comparendo": "12345"',  # truncated JSON
        '["12345"]',  # not an object
        '{"nro_comparendo": 12345, "placa": null}',  # not a string
    ],
)
def test_invalid_response_raises_without_leaking_document_data(text):
    with pytest.raises(ExtractorError) as error:
        parse_candidates(text, FIELDS, engine="claude:modelo", confidence=0.9)

    assert "claude:modelo" in str(error.value)
    assert "12345" not in str(error.value)
    assert error.value.__cause__ is None
