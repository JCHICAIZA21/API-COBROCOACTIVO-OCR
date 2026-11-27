"""Use case: read a document with a template, extract its fields and validate the expected data."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Mapping, Sequence

from .domain import (
    Candidate,
    DocumentRef,
    DocumentRejected,
    DocumentTemplate,
    Extraction,
    ExtractionContext,
    FieldResult,
    FieldSpec,
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
        pdf = await self._load_pdf(request.document, content)
        allow_external = request.allow_external_ai and self._external_ai_enabled
        sha256 = hashlib.sha256(pdf).hexdigest()
        policy = "ext" if allow_external else "local"
        key = f"{sha256}:{template.id}:{template.version}:{policy}"
        cache = self._cache
        extraction = await cache.get(key) if cache is not None else None
        if extraction is None:
            extraction = await self._extract(template, pdf, sha256, allow_external)
            if cache is not None and not extraction.messages:  # never cache a failed engine run
                await cache.put(key, extraction)
        candidates = extraction.candidates
        fields = tuple(
            _field_result(spec, candidates.get(spec.name), request.expected.get(spec.name))
            for spec in template.fields
        )
        names = {spec.name for spec in template.fields}
        unknown = tuple(
            f"campo esperado desconocido: {name}" for name in request.expected if name not in names
        )
        review = any(
            spec.required and not self._resolved(candidates, spec.name) for spec in template.fields
        )
        return ReadResult(
            template_id=template.id,
            template_version=template.version,
            document_sha256=extraction.document_sha256,
            status=ReadStatus.NEEDS_REVIEW if review else ReadStatus.OK,
            fields=fields,
            pages=extraction.pages,
            ocr_pages=extraction.ocr_pages,
            engines=extraction.engines,
            elapsed_ms=int((time.perf_counter() - start) * 1000),
            reference=request.reference,
            messages=extraction.messages + unknown,
        )

    async def aclose(self) -> None:
        """Release adapter resources (CPU process pool, HTTP clients, DB pools)."""
        close = getattr(self._pages, "close", None)
        if close is not None:
            close()
        for source in self._sources.values():
            aclose = getattr(source, "aclose", None)
            if aclose is not None:
                await aclose()

    async def _load_pdf(self, document: DocumentRef, content: bytes | None) -> bytes:
        """Bytes of the referenced PDF, rejected if too large or not a PDF."""
        if document.kind == "upload":
            if content is None:
                raise DocumentRejected("falta el contenido del PDF cargado")
            pdf = content
        else:
            source = self._sources.get(document.kind)
            if source is None:
                raise DocumentRejected(f"origen no habilitado: {document.kind}")
            pdf = await source.fetch(document.value)
        if len(pdf) > self._max_pdf_bytes:
            raise DocumentRejected("el PDF supera el tamaño máximo permitido")
        if not pdf.startswith(b"%PDF-"):
            raise DocumentRejected("el contenido no es un PDF")
        return pdf

    async def _extract(
        self, template: DocumentTemplate, pdf: bytes, sha256: str, allow_external: bool
    ) -> Extraction:
        """Run the cascade: each extractor only sees the fields still unresolved."""
        pages = tuple(await self._pages.read(pdf))
        specs = {spec.name: spec for spec in template.fields}
        best: dict[str, Candidate] = {}
        engines: list[str] = []
        messages: list[str] = []
        for extractor in self._extractors:
            if extractor.external and not allow_external:
                continue
            pending = tuple(spec for spec in template.fields if not self._resolved(best, spec.name))
            if not pending:
                break
            engines.append(extractor.name)
            ctx = ExtractionContext(template=template, fields=pending, pages=pages, pdf=pdf)
            try:
                found = await extractor.extract(ctx)
            except Exception as exc:
                messages.append(f"{extractor.name}: {type(exc).__name__}")
                continue
            for name, candidate in found.items():
                spec = specs.get(name)
                value = normalize(spec.kind, candidate.value) if spec else None
                current = best.get(name)
                if value is not None and (
                    current is None or candidate.confidence > current.confidence
                ):
                    best[name] = candidate.model_copy(update={"value": value})
        return Extraction(
            document_sha256=sha256,
            template_id=template.id,
            template_version=template.version,
            candidates=best,
            pages=len(pages),
            ocr_pages=tuple(page.number for page in pages if page.origin == "ocr"),
            engines=tuple(engines),
            messages=tuple(messages),
        )

    def _resolved(self, candidates: Mapping[str, Candidate], name: str) -> bool:
        candidate = candidates.get(name)
        return candidate is not None and candidate.confidence >= self._min_confidence


def _field_result(spec: FieldSpec, candidate: Candidate | None, raw: str | None) -> FieldResult:
    """Compare the extracted value with the expected one, both in canonical form."""
    raw = raw.strip() if raw else ""
    expected = normalize(spec.kind, raw)
    if not raw:
        verdict = Verdict.NOT_PROVIDED
    elif candidate is None:
        verdict = Verdict.NOT_FOUND
    elif candidate.value == expected:
        verdict = Verdict.MATCH
    else:
        verdict = Verdict.MISMATCH
    return FieldResult(
        name=spec.name,
        value=candidate.value if candidate else None,
        confidence=candidate.confidence if candidate else 0.0,
        engine=candidate.engine if candidate else None,
        page=candidate.page if candidate else None,
        snippet=candidate.snippet if candidate else "",
        expected=expected or raw or None,
        verdict=verdict,
    )
