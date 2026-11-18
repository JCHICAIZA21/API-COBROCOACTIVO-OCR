"""ClaudeExtractor against the real SDK, with the HTTP layer replaced by a mock transport."""

import json
from typing import Any

import anthropic
import httpx2
import pytest

from docreader.adapters.llm_claude import ClaudeExtractor
from docreader.adapters.llm_common import output_schema
from docreader.domain import DocumentTemplate, ExtractionContext, FieldSpec, PageText

MODEL = "claude-haiku-5-5"
FIELDS = (
    FieldSpec(name="nro_comparendo", description="Número del comparendo."),
    FieldSpec(name="placa", description="Placa del vehículo.", required=False),
)
TEMPLATE = DocumentTemplate(id="comparendo", version="1", description="Comparendo", fields=FIELDS)
NATIVE = PageText(number=1, text="Comparendo No. 12345", origin="native")
SCANNED = PageText(number=2, text="Placa ABC123", origin="ocr", confidence=0.6)
PDF = b"%PDF-1.7 fake"


def context(*pages: PageText) -> ExtractionContext:
    return ExtractionContext(template=TEMPLATE, fields=FIELDS, pages=pages, pdf=PDF)


def message(text: str = "", stop_reason: str = "end_turn", **extra: Any) -> dict[str, Any]:
    content = [{"type": "text", "text": text}] if text else []
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": MODEL,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5},
        **extra,
    }


class FakeApi:
    """Answers every request with one canned response and records what was sent."""

    def __init__(self, body: dict[str, Any], status: int = 200) -> None:
        self.body, self.status = body, status
        self.requests: list[httpx2.Request] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(self.status, json=self.body)

    def client(self) -> anthropic.AsyncAnthropic:
        transport = httpx2.MockTransport(self.handle)
        return anthropic.AsyncAnthropic(
            api_key="test", max_retries=0, http_client=httpx2.AsyncClient(transport=transport)
        )

    @property
    def sent(self) -> dict[str, Any]:
        return json.loads(self.requests[-1].content)


async def test_native_pages_are_sent_as_text_with_a_strict_output_schema():
    api = FakeApi(message('{"nro_comparendo": "12345", "placa": null}'))
    extractor = ClaudeExtractor(MODEL, client=api.client(), confidence=0.9)

    found = await extractor.extract(context(NATIVE))

    assert {k: (c.value, c.confidence, c.engine) for k, c in found.items()} == {
        "nro_comparendo": ("12345", 0.9, f"claude:{MODEL}")
    }
    body = api.sent
    assert body["model"] == MODEL
    assert body["output_config"]["format"] == {
        "type": "json_schema",
        "schema": output_schema(FIELDS),
    }
    [user] = body["messages"]
    [text] = user["content"]
    assert text["type"] == "text"
    assert "Comparendo No. 12345" in text["text"]
    assert body["system"]
    assert extractor.name == "claude"
    assert extractor.external is True
