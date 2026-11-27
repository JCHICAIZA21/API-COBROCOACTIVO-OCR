"""Field extractor on a local Ollama server (`/api/chat` with a JSON Schema as `format`)."""

from __future__ import annotations

import httpx

from ..domain import Candidate, ExtractionContext, ExtractorError
from .llm_common import INSTRUCTIONS, output_schema, parse_candidates, user_prompt


class OllamaExtractor:
    """Asks a local model for the pending fields. Text only: Ollama text models can't see the PDF.

    Local: no document data leaves the deployment.
    """

    name = "ollama"
    external = False

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        client: httpx.AsyncClient | None = None,
        confidence: float = 0.8,
        timeout_s: float = 120.0,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/api/chat"
        self._model = model
        self._client = client
        self._confidence = confidence
        self._timeout_s = timeout_s

    async def extract(self, ctx: ExtractionContext) -> dict[str, Candidate]:
        engine = f"{self.name}:{self._model}"
        payload = {
            "model": self._model,
            "stream": False,
            "format": output_schema(ctx.fields),
            "messages": [
                {"role": "system", "content": INSTRUCTIONS},
                {"role": "user", "content": user_prompt(ctx)},
            ],
        }
        try:
            response = await self._get_client().post(
                self._url, json=payload, timeout=self._timeout_s
            )
            response.raise_for_status()
        except httpx.HTTPError as e:
            raise ExtractorError(f"{engine}: falló la llamada a Ollama ({type(e).__name__})") from e
        return parse_candidates(
            _answer(response, engine), ctx.fields, engine=engine, confidence=self._confidence
        )

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient()
        return self._client


def _answer(response: httpx.Response, engine: str) -> str:
    """The assistant text of a non-streamed chat. Errors never echo the body (personal data)."""
    try:
        body = response.json()
        # Stop reason first: a cut answer may not follow the schema.
        if body.get("done_reason") == "length":
            raise ExtractorError(f"{engine}: respuesta truncada (length)")
        content = body["message"]["content"]
    except (ValueError, AttributeError, KeyError, TypeError):
        raise ExtractorError(f"{engine}: respuesta inválida de Ollama") from None
    if not isinstance(content, str):
        raise ExtractorError(f"{engine}: respuesta inválida de Ollama")
    return content
