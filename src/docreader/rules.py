"""Local extractor: the template's regexes over the page text. Fast, free, no data leaves."""

from __future__ import annotations

import re
from functools import lru_cache

from .domain import Candidate, ExtractionContext, FieldSpec, PageText
from .normalize import normalize

_SNIPPET_RADIUS = 40


@lru_cache(maxsize=1024)
def _compile(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE | re.MULTILINE)


def _find(field: FieldSpec, pages: tuple[PageText, ...]) -> Candidate | None:
    """First match whose value normalizes, trying patterns in order over pages in order."""
    for pattern in field.patterns:
        regex = _compile(pattern)
        for page in pages:
            for match in regex.finditer(page.text):
                group = "value" if "value" in regex.groupindex else 0
                if normalize(field.kind, match[group]) is None:
                    continue
                start, end = match.span(group)
                context = page.text[max(0, start - _SNIPPET_RADIUS) : end + _SNIPPET_RADIUS]
                return Candidate(
                    value=match[group],
                    confidence=page.confidence,
                    engine="rules",
                    page=page.number,
                    snippet=" ".join(context.split()),
                )
    return None


class RegexExtractor:
    """`FieldExtractor` that applies each field's `patterns` to the document text."""

    name = "rules"
    external = False

    async def extract(self, ctx: ExtractionContext) -> dict[str, Candidate]:
        found = {field.name: _find(field, ctx.pages) for field in ctx.fields}
        return {name: candidate for name, candidate in found.items() if candidate}
