"""OllamaExtractor over HTTP, with the Ollama server mocked by respx."""

import json
from typing import Any

import httpx
import pytest
import respx

from docreader.adapters.llm_common import output_schema
from docreader.adapters.llm_ollama import OllamaExtractor
from docreader.domain import (
    DocumentTemplate,
    ExtractionContext,
    ExtractorError,
    FieldSpec,
    PageText,
)

BASE_URL = "http://ollama.test:11434"
CHAT_URL = f"{BASE_URL}/api/chat"
MODEL = "gpt-oss:20b"
FIELDS = (
    FieldSpec(name="nro_comparendo", description="Número del comparendo."),
    FieldSpec(name="placa", description="Placa del vehículo.", required=False),
)
TEMPLATE = DocumentTemplate(id="comparendo", version="1", description="Comparendo", fields=FIELDS)
NATIVE = PageText(number=1, text="Comparendo No. 12345", origin="native")
SCANNED = PageText(number=2, text="Placa ABC123", origin="ocr", confidence=0.6)
ANSWER = '{"nro_comparendo": "12345", "placa": null}'


def context(*pages: PageText) -> ExtractionContext:
    return ExtractionContext(template=TEMPLATE, fields=FIELDS, pages=pages, pdf=b"%PDF")


def chat(content: str, done_reason: str = "stop") -> dict[str, Any]:
    return {
        "model": MODEL,
        "message": {"role": "assistant", "content": content},
        "done": True,
        "done_reason": done_reason,
    }


@respx.mock
async def test_pages_are_sent_as_text_with_the_output_schema_as_format():
    route = respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=chat(ANSWER)))
    extractor = OllamaExtractor(BASE_URL + "/", MODEL)

    found = await extractor.extract(context(NATIVE, SCANNED))

    assert {k: (c.value, c.confidence, c.engine) for k, c in found.items()} == {
        "nro_comparendo": ("12345", 0.8, f"ollama:{MODEL}")
    }
    body = json.loads(route.calls.last.request.content)
    assert body["model"] == MODEL
    assert body["stream"] is False
    assert body["format"] == output_schema(FIELDS)
    system, user = body["messages"]
    assert system["role"] == "system"
    assert set(user) == {"role", "content"}  # text only: no PDF, no images
    assert user["role"] == "user"
    assert "Comparendo No. 12345" in user["content"]
    assert "Placa ABC123" in user["content"]
    assert extractor.name == "ollama"
    assert extractor.external is False


@pytest.mark.parametrize(
    "outcome",
    [
        httpx.Response(500, json={"error": "model failed"}),
        httpx.Response(404, json={"error": "model not found"}),
        httpx.ConnectError("connection refused"),
        httpx.Response(200, json=chat(ANSWER, done_reason="length")),  # cut, even if it parses
        httpx.Response(200, json={"error": "unexpected"}),  # no message
        httpx.Response(200, text="not json"),
    ],
    ids=["server_error", "not_found", "unreachable", "truncated", "no_message", "not_json"],
)
@respx.mock
async def test_unusable_answers_raise_extractor_error_without_document_data(outcome):
    respx.post(CHAT_URL).mock(side_effect=[outcome])

    with pytest.raises(ExtractorError) as error:
        await OllamaExtractor(BASE_URL, MODEL).extract(context(NATIVE))

    assert str(error.value).startswith("ollama:")
    assert "12345" not in str(error.value)
