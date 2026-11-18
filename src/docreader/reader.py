"""Use case: read a document with a template, extract its fields and validate the expected data."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Mapping, Sequence

from .domain import (
    Candidate,
    DocumentTemplate,
    Extraction,
    ExtractionContext,
    FieldResult,
    ReadRequest,
    ReadResult,
    ReadStatus,
    Verdict,
)
from .normalize import normalize
from .ports import DocumentSource, ExtractionCache, FieldExtractor, PageReader, TemplateRepository


class DocumentReader:
    """Reads one PDF: pages → cascade of extractors → normalized values → validation."""

    def __init__(
        self,
        *,
        templates: TemplateRepository,
        pages: PageReader,
        extractors: Sequence[FieldExtractor],
        sources: Mapping[str, DocumentSource] | None = None,
        cache: ExtractionCache | None = None,
        min_confidence: float = 0.85,
        external_ai_enabled: bool = False,
        max_pdf_bytes: int = 20 * 1024 * 1024,
    ) -> None:
        self._templates = templates
        self._pages = pages
        self._extractors = tuple(extractors)
        self._sources = dict(sources or {})
        self._cache = cache
        self._min_confidence = min_confidence
        self._external_ai_enabled = external_ai_enabled
        self._max_pdf_bytes = max_pdf_bytes

    async def read(self, request: ReadRequest, content: bytes | None = None) -> ReadResult:
        start = time.perf_counter()
        template = self._templates.get(request.template_id)
        pdf = content or b""
        extraction = await self._extract(template, pdf, hashlib.sha256(pdf).hexdigest())
        fields = tuple(
            self._field_result(spec.name, extraction.candidates.get(spec.name))
            for spec in template.fields
        )
        return ReadResult(
            template_id=template.id,
            template_version=template.version,
            document_sha256=extraction.document_sha256,
            status=ReadStatus.OK,
            fields=fields,
            pages=extraction.pages,
            ocr_pages=extraction.ocr_pages,
            engines=extraction.engines,
            elapsed_ms=int((time.perf_counter() - start) * 1000),
            reference=request.reference,
            messages=extraction.messages,
        )

    async def _extract(self, template: DocumentTemplate, pdf: bytes, sha256: str) -> Extraction:
        pages = tuple(await self._pages.read(pdf))
        specs = {spec.name: spec for spec in template.fields}
        best: dict[str, Candidate] = {}
        engines: list[str] = []
        for extractor in self._extractors:
            pending = tuple(spec for spec in template.fields if not self._resolved(best, spec.name))
            if not pending:
                break
            engines.append(extractor.name)
            ctx = ExtractionContext(template=template, fields=pending, pages=pages, pdf=pdf)
            for name, candidate in (await extractor.extract(ctx)).items():
                spec = specs.get(name)
                value = normalize(spec.kind, candidate.value) if spec else None
                current = best.get(name)
                if value is not None and (current is None or candidate.confidence > current.confidence):
                    best[name] = candidate.model_copy(update={"value": value})
        return Extraction(
            document_sha256=sha256,
            template_id=template.id,
            template_version=template.version,
            candidates=best,
            pages=len(pages),
            engines=tuple(engines),
        )

    def _resolved(self, candidates: Mapping[str, Candidate], name: str) -> bool:
        candidate = candidates.get(name)
        return candidate is not None and candidate.confidence >= self._min_confidence

    def _field_result(self, name: str, candidate: Candidate | None) -> FieldResult:
        if candidate is None:
            return FieldResult(
                name=name,
                value=None,
                confidence=0.0,
                engine=None,
                page=None,
                snippet="",
                expected=None,
                verdict=Verdict.NOT_FOUND,
            )
        return FieldResult(
            name=name,
            value=candidate.value,
            confidence=candidate.confidence,
            engine=candidate.engine,
            page=candidate.page,
            snippet=candidate.snippet,
            expected=None,
            verdict=Verdict.NOT_PROVIDED,
        )
