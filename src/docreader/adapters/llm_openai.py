"""Field extractor on the OpenAI Responses API, with strict structured outputs."""

from __future__ import annotations

import base64
from typing import Any

import openai
from openai import omit
from openai.types.responses import Response

from ..domain import Candidate, ExtractionContext, ExtractorError
from .llm_common import INSTRUCTIONS, needs_document, output_schema, parse_candidates, user_prompt


class OpenAIExtractor:
    """Asks an OpenAI model for the pending fields. Sends document data outside the deployment."""

    name = "openai"
    external = True

    def __init__(
        self,
        model: str,
        *,
        client: openai.AsyncOpenAI | None = None,
        confidence: float = 0.9,
        max_output_tokens: int = 1024,
        timeout_s: float = 60.0,
        reasoning_effort: str | None = None,
    ) -> None:
        self._model = model
        self._client = client
        self._confidence = confidence
        self._max_output_tokens = max_output_tokens
        self._timeout_s = timeout_s
        self._reasoning_effort = reasoning_effort

    async def extract(self, ctx: ExtractionContext) -> dict[str, Candidate]:
        engine = f"{self.name}:{self._model}"
        schema = output_schema(ctx.fields)
        try:  # the client is created here too: without a key the SDK raises on construction
            response = await self._get_client().responses.create(
                model=self._model,
                instructions=INSTRUCTIONS,
                input=[{"role": "user", "content": _content(ctx)}],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "campos",
                        "schema": schema,
                        "strict": True,
                    }
                },
                max_output_tokens=self._max_output_tokens,
                reasoning={"effort": self._reasoning_effort} if self._reasoning_effort else omit,
                store=False,
            )
        except openai.OpenAIError as e:
            raise ExtractorError(f"{engine}: falló la llamada a la API ({type(e).__name__})") from e

        # Status and refusal first: an incomplete or refused answer may not follow the schema.
        if response.status == "incomplete":
            reason = response.incomplete_details.reason if response.incomplete_details else None
            raise ExtractorError(f"{engine}: respuesta incompleta ({reason})")
        if _refused(response):
            raise ExtractorError(f"{engine}: el modelo rechazó la solicitud")
        return parse_candidates(
            response.output_text, ctx.fields, engine=engine, confidence=self._confidence
        )

    def _get_client(self) -> openai.AsyncOpenAI:
        if self._client is None:
            self._client = openai.AsyncOpenAI(timeout=self._timeout_s, max_retries=2)
        return self._client


def _refused(response: Response) -> bool:
    return any(
        part.type == "refusal"
        for item in response.output
        if item.type == "message"
        for part in item.content
    )


def _content(ctx: ExtractionContext) -> list[dict[str, Any]]:
    """The page text; scanned pages also send the PDF (before the text) so the model sees them."""
    content: list[dict[str, Any]] = [{"type": "input_text", "text": user_prompt(ctx)}]
    if needs_document(ctx.pages):
        pdf = base64.b64encode(ctx.pdf).decode()
        content.insert(
            0,
            {
                "type": "input_file",
                "filename": "documento.pdf",
                "file_data": f"data:application/pdf;base64,{pdf}",
            },
        )
    return content
