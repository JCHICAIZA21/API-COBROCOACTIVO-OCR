"""Ports: the small interfaces the core depends on. Adapters implement them."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from PIL.Image import Image

from .domain import (
    Candidate,
    DocumentTemplate,
    Extraction,
    ExtractionContext,
    PageText,
    SourceKind,
)


@runtime_checkable
class DocumentSource(Protocol):
    """Fetches PDF bytes from one kind of location (path, url, blob)."""

    kind: SourceKind

    async def fetch(self, ref: str) -> bytes: ...


@runtime_checkable
class PageReader(Protocol):
    """Turns PDF bytes into per-page text: native text layer, OCR only where it is missing."""

    async def read(self, pdf: bytes) -> list[PageText]: ...


@runtime_checkable
class OcrEngine(Protocol):
    """Recognizes text in one page image. Synchronous: runs inside CPU worker processes."""

    name: str

    def recognize(self, image: Image) -> tuple[str, float]:
        """Return (text in reading order, one line per text line; confidence in [0, 1])."""
        ...


@runtime_checkable
class FieldExtractor(Protocol):
    """Proposes values for the unresolved fields. Returns only the fields it found."""

    name: str
    external: bool  # True when it sends document data outside the deployment

    async def extract(self, ctx: ExtractionContext) -> dict[str, Candidate]: ...


@runtime_checkable
class TemplateRepository(Protocol):
    def get(self, template_id: str) -> DocumentTemplate: ...

    def all(self) -> list[DocumentTemplate]: ...


@runtime_checkable
class ExtractionCache(Protocol):
    async def get(self, key: str) -> Extraction | None: ...

    async def put(self, key: str, extraction: Extraction) -> None: ...
