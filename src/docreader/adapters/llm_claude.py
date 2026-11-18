"""Field extractor on the Claude API (Anthropic SDK), with structured outputs."""

from __future__ import annotations

from typing import Any

import anthropic

from ..domain import Candidate, ExtractionContext
from .llm_common import INSTRUCTIONS, output_schema, parse_candidates, user_prompt


class ClaudeExtractor:
    """Asks Claude for the pending fields. Sends document data outside the deployment."""

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
        content: list[dict[str, Any]] = [{"type": "text", "text": user_prompt(ctx)}]
        response = await self._get_client().messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=INSTRUCTIONS,
            messages=[{"role": "user", "content": content}],
            output_config={"format": {"type": "json_schema", "schema": output_schema(ctx.fields)}},
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        return parse_candidates(text, ctx.fields, engine=engine, confidence=self._confidence)

    def _get_client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            self._client = anthropic.AsyncAnthropic(timeout=self._timeout_s, max_retries=2)
        return self._client
