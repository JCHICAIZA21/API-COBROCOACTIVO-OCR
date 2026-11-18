"""Domain model: immutable value objects shared by every layer. No I/O here."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Value(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- Templates: what to read from a document type -------------------------------------------

FieldKind = Literal["text", "id_number", "date", "money", "plate", "integer"]


class FieldSpec(_Value):
    """A field to read, declared in a document template."""

    name: str
    kind: FieldKind = "text"
    description: str = ""
    patterns: tuple[str, ...] = ()  # regexes; named group "value" (or the whole match) is the value
    required: bool = True
    key: bool = False  # key fields drive acceptance metrics


class DocumentTemplate(_Value):
    """Declarative description of one document type. One definition feeds every engine."""

    id: str
    version: str
    description: str = ""
    fields: tuple[FieldSpec, ...]


# --- Requests ---------------------------------------------------------------------------------

SourceKind = Literal["upload", "path", "url", "blob"]


class DocumentRef(_Value):
    """Where the PDF lives. `upload` carries no value: the bytes travel with the request."""

    kind: SourceKind
    value: str = ""


class ReadRequest(_Value):
    template_id: str
    document: DocumentRef
    expected: dict[str, str] = Field(default_factory=dict)  # input data to validate against
    allow_external_ai: bool = False  # policy: default forbidden (fail-safe with personal data)
    reference: str | None = None  # caller's correlation id, echoed back


# --- Extraction -------------------------------------------------------------------------------

PageOrigin = Literal["native", "ocr"]


class PageText(_Value):
    number: int  # 1-based
    text: str
    origin: PageOrigin
    confidence: float = 1.0


class Candidate(_Value):
    """A value proposed by an extractor for one field, before normalization."""

    value: str
    confidence: float
    engine: str
    page: int | None = None
    snippet: str = ""


class ExtractionContext(_Value):
    """Everything an extractor may look at. `fields` are only the still-unresolved ones."""

    template: DocumentTemplate
    fields: tuple[FieldSpec, ...]
    pages: tuple[PageText, ...]
    pdf: bytes


class Extraction(_Value):
    """Outcome of reading a document, independent of any request's expected values (cacheable)."""

    document_sha256: str
    template_id: str
    template_version: str
    candidates: dict[str, Candidate]
    pages: int
    ocr_pages: tuple[int, ...] = ()
    engines: tuple[str, ...] = ()
    messages: tuple[str, ...] = ()  # warnings; never personal data


# --- Results ----------------------------------------------------------------------------------


class Verdict(StrEnum):
    MATCH = "match"
    MISMATCH = "mismatch"
    NOT_FOUND = "not_found"
    NOT_PROVIDED = "not_provided"


class ReadStatus(StrEnum):
    OK = "ok"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"


class FieldResult(_Value):
    name: str
    value: str | None
    confidence: float
    engine: str | None
    page: int | None
    snippet: str
    expected: str | None
    verdict: Verdict


class ReadResult(_Value):
    template_id: str
    template_version: str
    document_sha256: str
    status: ReadStatus
    fields: tuple[FieldResult, ...]
    pages: int
    ocr_pages: tuple[int, ...]
    engines: tuple[str, ...]
    elapsed_ms: int
    reference: str | None = None
    messages: tuple[str, ...] = ()


# --- Errors -----------------------------------------------------------------------------------


class DocReaderError(Exception):
    """Base error of the library."""


class TemplateNotFound(DocReaderError):
    pass


class DocumentNotFound(DocReaderError):
    pass


class DocumentRejected(DocReaderError):
    """The document or its reference is not acceptable (size, type, forbidden location)."""


class ExtractorError(DocReaderError):
    """An extractor failed; the cascade continues with the next one."""
