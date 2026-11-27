"""ClaudeExtractor against the real SDK, with the HTTP layer replaced by a mock transport."""

import base64
import json
import os
from typing import Any

import anthropic
import httpx2
import pytest

from docreader.adapters.llm_claude import ClaudeExtractor
from docreader.adapters.llm_common import output_schema
from docreader.domain import (
    DocumentTemplate,
    ExtractionContext,
    ExtractorError,
    FieldSpec,
    PageText,
)

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


async def test_scanned_pages_send_the_pdf_before_the_text():
    api = FakeApi(message('{"nro_comparendo": null, "placa": "ABC123"}'))
    extractor = ClaudeExtractor(MODEL, client=api.client())

    found = await extractor.extract(context(NATIVE, SCANNED))

    assert found["placa"].value == "ABC123"
    [user] = api.sent["messages"]
    document, text = user["content"]
    assert document == {
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": base64.b64encode(PDF).decode(),
        },
    }
    assert text["type"] == "text"


ANSWER = '{"nro_comparendo": "12345", "placa": null}'


@pytest.mark.parametrize(
    ("body", "status"),
    [
        # Stop reasons are checked before the content, even when it looks complete.
        (message(ANSWER, "refusal", stop_details={"type": "refusal", "category": None}), 200),
        (message(ANSWER, stop_reason="max_tokens"), 200),
        (message(ANSWER, stop_reason="model_context_window_exceeded"), 200),
        ({"type": "error", "error": {"type": "api_error", "message": "boom"}}, 500),
    ],
    ids=["refusal", "max_tokens", "context_window", "api_error"],
)
async def test_unusable_answers_raise_extractor_error_without_document_data(body, status):
    extractor = ClaudeExtractor(MODEL, client=FakeApi(body, status).client())

    with pytest.raises(ExtractorError) as error:
        await extractor.extract(context(NATIVE))

    assert str(error.value).startswith("claude:")
    assert "12345" not in str(error.value)


async def test_effort_and_max_tokens_are_configurable_and_thinking_is_left_to_the_model():
    api = FakeApi(message(ANSWER))
    await ClaudeExtractor(MODEL, client=api.client()).extract(context(NATIVE))
    default = api.sent

    await ClaudeExtractor(MODEL, client=api.client(), effort="low", max_tokens=2048).extract(
        context(NATIVE)
    )

    assert "effort" not in default["output_config"]
    assert "thinking" not in default
    assert api.sent["output_config"]["effort"] == "low"
    assert api.sent["max_tokens"] == 2048


async def test_refusals_fall_back_to_another_model_server_side():
    api = FakeApi(message(ANSWER))

    await ClaudeExtractor(MODEL, client=api.client()).extract(context(NATIVE))

    assert api.sent["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in api.requests[-1].headers["anthropic-beta"]


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="sin ANTHROPIC_API_KEY")
async def test_live_extraction_from_native_text():
    found = await ClaudeExtractor(MODEL, effort="low").extract(context(NATIVE))

    assert found["nro_comparendo"].value == "12345"
    assert "placa" not in found
