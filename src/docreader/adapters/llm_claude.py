"""Field extractor on the Claude API (Anthropic SDK), with structured outputs."""

from __future__ import annotations

import base64
from typing import Any

import anthropic

from ..domain import Candidate, ExtractionContext, ExtractorError
from .llm_common import INSTRUCTIONS, needs_document, output_schema, parse_candidates, user_prompt

# On a safety decline the API re-runs the request on the model Anthropic recommends for that
# category. Models without server-side fallback (Haiku 5.5) just keep the refusal.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"
_TRUNCATED = ("max_tokens", "model_context_window_exceeded")


class ClaudeExtractor:
    """Asks Claude for the pending fields. Sends document data outside the deployment.

    Thinking is left to the model (Opus 5.5 can't disable it); `effort` is the control.
    """

    name = "claude"
    external = True

    def __init__(
        self,
        model: str,
        *,
        client: anthropic.AsyncAnthropic | None = None,
        confidence: float = 0.9,
        max_tokens: int = 1024,
        timeout_s: float = 60.0,
        effort: str | None = None,
    ) -> None:
        self._model = model
        self._client = client
        self._confidence = confidence
        self._max_tokens = max_tokens
        self._timeout_s = timeout_s
        self._effort = effort

    async def extract(self, ctx: ExtractionContext) -> dict[str, Candidate]:
        engine = f"{self.name}:{self._model}"
        output_config: dict[str, Any] = {
            "format": {"type": "json_schema", "schema": output_schema(ctx.fields)}
        }
        if self._effort:
            output_config["effort"] = self._effort
        try:
            response = await self._get_client().beta.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=INSTRUCTIONS,
                messages=[{"role": "user", "content": _content(ctx)}],
                output_config=output_config,
                betas=[_FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.AnthropicError as e:
            raise ExtractorError(f"{engine}: falló la llamada a la API ({type(e).__name__})") from e

        # Stop reason first: a refused or cut answer may not follow the schema.
        if response.stop_reason == "refusal":
            raise ExtractorError(f"{engine}: el modelo rechazó la solicitud")
        if response.stop_reason in _TRUNCATED:
            raise ExtractorError(f"{engine}: respuesta truncada ({response.stop_reason})")
        text = "".join(block.text for block in response.content if block.type == "text")
        return parse_candidates(text, ctx.fields, engine=engine, confidence=self._confidence)

    def _get_client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            self._client = anthropic.AsyncAnthropic(timeout=self._timeout_s, max_retries=2)
        return self._client


def _content(ctx: ExtractionContext) -> list[dict[str, Any]]:
    """The page text; scanned pages also send the PDF (before the text) so Claude sees them."""
    content: list[dict[str, Any]] = [{"type": "text", "text": user_prompt(ctx)}]
    if needs_document(ctx.pages):
        pdf = base64.b64encode(ctx.pdf).decode()
        source = {"type": "base64", "media_type": "application/pdf", "data": pdf}
        content.insert(0, {"type": "document", "source": source})
    return content
