"""OpenAIExtractor against the real SDK, with the HTTP layer replaced by a mock transport."""

import base64
import json
import os
from typing import Any

import httpx2
import openai
import pytest

from docreader.adapters.llm_common import output_schema
from docreader.adapters.llm_openai import OpenAIExtractor
from docreader.domain import (
    DocumentTemplate,
    ExtractionContext,
    ExtractorError,
    FieldSpec,
    PageText,
)

MODEL = "gpt-6-luna"
FIELDS = (
    FieldSpec(name="nro_comparendo", description="Número del comparendo."),
    FieldSpec(name="placa", description="Placa del vehículo.", required=False),
)
TEMPLATE = DocumentTemplate(id="comparendo", version="1", description="Comparendo", fields=FIELDS)
NATIVE = PageText(number=1, text="Comparendo No. 12345", origin="native")
SCANNED = PageText(number=2, text="Placa ABC123", origin="ocr", confidence=0.6)
PDF = b"%PDF-1.7 fake"
ANSWER = '{"nro_comparendo": "12345", "placa": null}'


def context(*pages: PageText) -> ExtractionContext:
    return ExtractionContext(template=TEMPLATE, fields=FIELDS, pages=pages, pdf=PDF)


def response(*content: dict[str, Any], status: str = "completed", **extra: Any) -> dict[str, Any]:
    return {
        "id": "resp_1",
        "object": "response",
        "created_at": 0,
        "model": MODEL,
        "status": status,
        "output": [
            {
                "type": "message",
                "id": "msg_1",
                "role": "assistant",
                "status": status,
                "content": list(content),
            }
        ],
        "error": None,
        "incomplete_details": None,
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        **extra,
    }


def text(value: str) -> dict[str, Any]:
    return {"type": "output_text", "text": value, "annotations": []}


class FakeApi:
    """Answers every request with one canned response and records what was sent."""

    def __init__(self, body: dict[str, Any], status: int = 200) -> None:
        self.body, self.status = body, status
        self.requests: list[httpx2.Request] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(self.status, json=self.body)

    def client(self) -> openai.AsyncOpenAI:
        transport = httpx2.MockTransport(self.handle)
        return openai.AsyncOpenAI(
            api_key="test", max_retries=0, http_client=httpx2.AsyncClient(transport=transport)
        )

    @property
    def sent(self) -> dict[str, Any]:
        return json.loads(self.requests[-1].content)


async def test_native_pages_are_sent_as_text_with_a_strict_output_schema():
    api = FakeApi(response(text(ANSWER)))
    extractor = OpenAIExtractor(MODEL, client=api.client(), confidence=0.9)

    found = await extractor.extract(context(NATIVE))

    assert {k: (c.value, c.confidence, c.engine) for k, c in found.items()} == {
        "nro_comparendo": ("12345", 0.9, f"openai:{MODEL}")
    }
    body = api.sent
    assert api.requests[-1].url.path.endswith("/responses")
    assert body["model"] == MODEL
    assert body["store"] is False
    assert body["text"]["format"] == {
        "type": "json_schema",
        "name": "campos",
        "schema": output_schema(FIELDS),
        "strict": True,
    }
    assert body["instructions"]
    [user] = body["input"]
    [prompt] = user["content"]
    assert user["role"] == "user"
    assert prompt["type"] == "input_text"
    assert "Comparendo No. 12345" in prompt["text"]
    assert extractor.name == "openai"
    assert extractor.external is True


async def test_scanned_pages_send_the_pdf_before_the_text():
    api = FakeApi(response(text('{"nro_comparendo": null, "placa": "ABC123"}')))

    found = await OpenAIExtractor(MODEL, client=api.client()).extract(context(NATIVE, SCANNED))

    assert found["placa"].value == "ABC123"
    [user] = api.sent["input"]
    document, prompt = user["content"]
    assert document == {
        "type": "input_file",
        "filename": "documento.pdf",
        "file_data": "data:application/pdf;base64," + base64.b64encode(PDF).decode(),
    }
    assert prompt["type"] == "input_text"


@pytest.mark.parametrize(
    ("body", "status"),
    [
        # Refusal and status are checked before the content, even when it looks complete.
        (response(text(ANSWER), {"type": "refusal", "refusal": "No puedo ayudar."}), 200),
        (
            response(
                text(ANSWER),
                status="incomplete",
                incomplete_details={"reason": "max_output_tokens"},
            ),
            200,
        ),
        ({"error": {"message": "boom", "type": "server_error", "code": None}}, 500),
    ],
    ids=["refusal", "incomplete", "api_error"],
)
async def test_unusable_answers_raise_extractor_error_without_document_data(body, status):
    extractor = OpenAIExtractor(MODEL, client=FakeApi(body, status).client())

    with pytest.raises(ExtractorError) as error:
        await extractor.extract(context(NATIVE))

    assert str(error.value).startswith("openai:")
    assert "12345" not in str(error.value)


async def test_missing_api_key_is_an_extractor_error(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ExtractorError):
        await OpenAIExtractor(MODEL).extract(context(NATIVE))


async def test_reasoning_effort_and_max_output_tokens_are_configurable():
    api = FakeApi(response(text(ANSWER)))
    await OpenAIExtractor(MODEL, client=api.client()).extract(context(NATIVE))
    default = api.sent

    await OpenAIExtractor(
        MODEL, client=api.client(), reasoning_effort="none", max_output_tokens=512
    ).extract(context(NATIVE))

    assert "reasoning" not in default
    assert api.sent["reasoning"] == {"effort": "none"}
    assert api.sent["max_output_tokens"] == 512


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="sin OPENAI_API_KEY")
async def test_live_extraction_from_native_text():
    found = await OpenAIExtractor(MODEL, reasoning_effort="none").extract(context(NATIVE))

    assert found["nro_comparendo"].value == "12345"
    assert "placa" not in found
