import hashlib
from collections.abc import Mapping, Sequence

import pytest

from docreader.cache import MemoryCache
from docreader.domain import (
    Candidate,
    DocumentRef,
    DocumentRejected,
    DocumentTemplate,
    Extraction,
    ExtractionContext,
    FieldResult,
    FieldSpec,
    PageText,
    ReadRequest,
    ReadStatus,
    SourceKind,
    TemplateNotFound,
    Verdict,
)
from docreader.ports import FieldExtractor
from docreader.reader import DocumentReader

PDF = b"%PDF-1.7 fake document"

TEMPLATE = DocumentTemplate(
    id="comparendo",
    version="2",
    fields=(
        FieldSpec(name="nro", kind="id_number"),
        FieldSpec(name="fecha", kind="date"),
        FieldSpec(name="placa", kind="plate", required=False),
    ),
)


class FakeTemplates:
    def get(self, template_id: str) -> DocumentTemplate:
        if template_id != TEMPLATE.id:
            raise TemplateNotFound(template_id)
        return TEMPLATE

    def all(self) -> list[DocumentTemplate]:
        return [TEMPLATE]


class FakePages:
    def __init__(self, pages: Sequence[PageText] | None = None) -> None:
        self.pages = list(pages or [PageText(number=1, text="texto", origin="native")])
        self.calls: list[bytes] = []

    async def read(self, pdf: bytes) -> list[PageText]:
        self.calls.append(pdf)
        return self.pages


class FakeExtractor:
    def __init__(
        self,
        name: str,
        found: Mapping[str, tuple[str, float]] | Exception,
        *,
        external: bool = False,
    ) -> None:
        self.name = name
        self.external = external
        self.found = found
        self.calls: list[tuple[str, ...]] = []  # field names requested on each call

    async def extract(self, ctx: ExtractionContext) -> dict[str, Candidate]:
        self.calls.append(tuple(f.name for f in ctx.fields))
        if isinstance(self.found, Exception):
            raise self.found
        return {
            name: Candidate(value=value, confidence=conf, engine=self.name, page=1, snippet="…")
            for name, (value, conf) in self.found.items()
        }


def make_reader(
    *extractors: FieldExtractor, pages: FakePages | None = None, **kwargs: object
) -> DocumentReader:
    return DocumentReader(
        templates=FakeTemplates(),
        pages=pages or FakePages(),
        extractors=extractors,
        **kwargs,  # type: ignore[arg-type]
    )


def make_request(
    expected: dict[str, str] | None = None,
    *,
    allow_external_ai: bool = False,
    kind: SourceKind = "upload",
) -> ReadRequest:
    return ReadRequest(
        template_id="comparendo",
        document=DocumentRef(kind=kind, value="" if kind == "upload" else "doc-1"),
        expected=expected or {},
        allow_external_ai=allow_external_ai,
        reference="ref-1",
    )


ALL_FOUND = {"nro": ("1.023.456", 1.0), "fecha": ("15/03/2024", 1.0), "placa": ("abc-123", 1.0)}


async def test_reads_upload_with_normalized_values_and_evidence() -> None:
    rules = FakeExtractor("rules", ALL_FOUND)

    result = await make_reader(rules).read(make_request(), PDF)

    assert result.status == ReadStatus.OK
    assert result.template_id == "comparendo"
    assert result.template_version == "2"
    assert result.document_sha256 == hashlib.sha256(PDF).hexdigest()
    assert result.reference == "ref-1"
    assert result.pages == 1
    assert result.engines == ("rules",)
    assert result.elapsed_ms >= 0
    assert result.fields[0] == FieldResult(
        name="nro",
        value="1023456",
        confidence=1.0,
        engine="rules",
        page=1,
        snippet="…",
        expected=None,
        verdict=Verdict.NOT_PROVIDED,
    )
    assert [(f.name, f.value) for f in result.fields] == [
        ("nro", "1023456"),
        ("fecha", "2024-03-15"),
        ("placa", "ABC123"),
    ]


async def test_cascade_asks_each_extractor_only_for_pending_fields_in_order() -> None:
    first = FakeExtractor("rules", {"nro": ("1023456", 1.0), "fecha": ("15/03/2024", 0.5)})
    second = FakeExtractor("ollama", {"fecha": ("16/03/2024", 0.9), "placa": ("ABC123", 0.95)})
    third = FakeExtractor("claude", {"nro": ("999", 1.0)})

    result = await make_reader(first, second, third).read(make_request(), PDF)

    assert first.calls == [("nro", "fecha", "placa")]
    assert second.calls == [("fecha", "placa")]
    assert third.calls == []  # nothing pending: the cascade stops
    assert result.engines == ("rules", "ollama")
    values = {f.name: (f.value, f.engine) for f in result.fields}
    assert values == {
        "nro": ("1023456", "rules"),
        "fecha": ("2024-03-16", "ollama"),
        "placa": ("ABC123", "ollama"),
    }


async def test_cascade_keeps_the_highest_confidence_candidate() -> None:
    first = FakeExtractor("rules", {"fecha": ("15/03/2024", 0.6)})
    second = FakeExtractor("ollama", {"fecha": ("16/03/2024", 0.4)})

    result = await make_reader(first, second).read(make_request(), PDF)

    assert second.calls == [("nro", "fecha", "placa")]
    assert (result.fields[1].value, result.fields[1].confidence) == ("2024-03-15", 0.6)


async def test_candidates_for_unknown_fields_or_invalid_values_are_dropped() -> None:
    rules = FakeExtractor("rules", {"otro": ("x", 1.0), "fecha": ("31/02/2024", 1.0)})
    ollama = FakeExtractor("ollama", {"fecha": ("01/03/2024", 0.9)})

    result = await make_reader(rules, ollama).read(make_request(), PDF)

    assert "fecha" in ollama.calls[0]
    assert [f.name for f in result.fields] == ["nro", "fecha", "placa"]
    assert result.fields[1].value == "2024-03-01"


async def test_external_extractors_skipped_when_request_does_not_allow_them() -> None:
    claude = FakeExtractor("claude", ALL_FOUND, external=True)
    ollama = FakeExtractor("ollama", ALL_FOUND)

    result = await make_reader(claude, ollama, external_ai_enabled=True).read(
        make_request(allow_external_ai=False), PDF
    )

    assert claude.calls == []
    assert result.engines == ("ollama",)


async def test_global_cap_wins_over_request_permission() -> None:
    claude = FakeExtractor("claude", ALL_FOUND, external=True)

    result = await make_reader(claude, external_ai_enabled=False).read(
        make_request(allow_external_ai=True), PDF
    )

    assert claude.calls == []
    assert result.engines == ()
    assert result.status == ReadStatus.NEEDS_REVIEW


async def test_external_extractors_used_when_request_and_global_cap_allow() -> None:
    claude = FakeExtractor("claude", ALL_FOUND, external=True)

    result = await make_reader(claude, external_ai_enabled=True).read(
        make_request(allow_external_ai=True), PDF
    )

    assert result.engines == ("claude",)
    assert result.fields[0].engine == "claude"


async def test_failing_extractor_is_reported_and_cascade_continues() -> None:
    broken = FakeExtractor("ollama", RuntimeError("detalle con datos personales 1023456"))
    rules = FakeExtractor("rules", ALL_FOUND)

    result = await make_reader(broken, rules).read(make_request(), PDF)

    assert result.engines == ("ollama", "rules")
    assert result.messages == ("ollama: RuntimeError",)
    assert result.status == ReadStatus.OK


async def test_verdicts_compare_normalized_values() -> None:
    rules = FakeExtractor("rules", {"nro": ("1023456", 1.0), "fecha": ("15/03/2024", 1.0)})
    expected = {"nro": "1.023.456", "fecha": "16 de marzo de 2024", "placa": "ABC-123"}

    result = await make_reader(rules).read(make_request(expected), PDF)

    verdicts = {f.name: (f.expected, f.verdict) for f in result.fields}
    assert verdicts == {
        "nro": ("1023456", Verdict.MATCH),
        "fecha": ("2024-03-16", Verdict.MISMATCH),
        "placa": ("ABC123", Verdict.NOT_FOUND),
    }
    assert result.status == ReadStatus.OK  # a mismatch is a valid validation outcome


async def test_expected_value_that_does_not_normalize_is_shown_stripped_and_mismatches() -> None:
    rules = FakeExtractor("rules", ALL_FOUND)

    result = await make_reader(rules).read(make_request({"fecha": "  31/02/2024 "}), PDF)

    assert (result.fields[1].expected, result.fields[1].verdict) == ("31/02/2024", Verdict.MISMATCH)


async def test_unknown_expected_field_adds_a_message() -> None:
    rules = FakeExtractor("rules", ALL_FOUND)

    result = await make_reader(rules).read(make_request({"nombre": "x", "nro": "1023456"}), PDF)

    assert result.messages == ("campo esperado desconocido: nombre",)
    assert result.fields[0].verdict == Verdict.MATCH


async def test_missing_required_field_needs_review() -> None:
    rules = FakeExtractor("rules", {"nro": ("1023456", 1.0), "placa": ("ABC123", 1.0)})

    result = await make_reader(rules).read(make_request(), PDF)

    assert result.status == ReadStatus.NEEDS_REVIEW
    fecha = result.fields[1]
    assert (fecha.value, fecha.confidence, fecha.engine, fecha.verdict) == (
        None,
        0.0,
        None,
        Verdict.NOT_PROVIDED,
    )


async def test_required_field_below_min_confidence_needs_review() -> None:
    rules = FakeExtractor("rules", {**ALL_FOUND, "fecha": ("15/03/2024", 0.84)})

    result = await make_reader(rules, min_confidence=0.85).read(make_request(), PDF)

    assert result.status == ReadStatus.NEEDS_REVIEW


async def test_optional_field_missing_or_doubtful_keeps_status_ok() -> None:
    rules = FakeExtractor("rules", {**ALL_FOUND, "placa": ("ABC123", 0.1)})
    rules_without_placa = FakeExtractor(
        "rules", {k: v for k, v in ALL_FOUND.items() if k != "placa"}
    )

    doubtful = await make_reader(rules).read(make_request(), PDF)
    missing = await make_reader(rules_without_placa).read(make_request(), PDF)

    assert doubtful.status == ReadStatus.OK
    assert missing.status == ReadStatus.OK


class FakeSource:
    def __init__(self, kind: SourceKind, data: bytes) -> None:
        self.kind = kind
        self.data = data
        self.refs: list[str] = []

    async def fetch(self, ref: str) -> bytes:
        self.refs.append(ref)
        return self.data


async def test_reads_bytes_from_the_source_of_the_reference_kind() -> None:
    source = FakeSource("path", PDF)
    pages = FakePages()

    result = await make_reader(
        FakeExtractor("rules", ALL_FOUND), pages=pages, sources={"path": source}
    ).read(make_request(kind="path"))

    assert source.refs == ["doc-1"]
    assert pages.calls == [PDF]
    assert result.document_sha256 == hashlib.sha256(PDF).hexdigest()


async def test_rejects_reference_kind_without_source() -> None:
    reader = make_reader(
        FakeExtractor("rules", ALL_FOUND), sources={"path": FakeSource("path", PDF)}
    )

    with pytest.raises(DocumentRejected, match="origen no habilitado"):
        await reader.read(make_request(kind="url"))


async def test_rejects_upload_without_content() -> None:
    with pytest.raises(DocumentRejected):
        await make_reader(FakeExtractor("rules", ALL_FOUND)).read(make_request(), None)


@pytest.mark.parametrize("content", [b"%PDF-" + b"x" * 20, b"<html>not a pdf</html>", b""])
async def test_rejects_oversized_or_non_pdf_bytes(content: bytes) -> None:
    pages = FakePages()
    reader = make_reader(FakeExtractor("rules", ALL_FOUND), pages=pages, max_pdf_bytes=20)

    with pytest.raises(DocumentRejected):
        await reader.read(make_request(), content)
    assert pages.calls == []


async def test_rejects_oversized_bytes_from_a_source() -> None:
    reader = make_reader(sources={"blob": FakeSource("blob", PDF)}, max_pdf_bytes=len(PDF) - 1)

    with pytest.raises(DocumentRejected):
        await reader.read(make_request(kind="blob"))


async def test_unknown_template_raises_not_found() -> None:
    request = make_request().model_copy(update={"template_id": "otra"})

    with pytest.raises(TemplateNotFound):
        await make_reader().read(request, PDF)


class RecordingCache(MemoryCache):
    def __init__(self) -> None:
        super().__init__(max_items=10)
        self.keys: list[str] = []

    async def get(self, key: str) -> Extraction | None:
        self.keys.append(key)
        return await super().get(key)


async def test_cache_hit_skips_pages_and_extractors_but_revalidates() -> None:
    pages = FakePages()
    rules = FakeExtractor("rules", ALL_FOUND)
    reader = make_reader(rules, pages=pages, cache=MemoryCache(max_items=10))

    first = await reader.read(make_request({"nro": "1023456"}), PDF)
    second = await reader.read(make_request({"nro": "999"}), PDF)

    assert len(pages.calls) == 1
    assert len(rules.calls) == 1
    assert first.fields[0].verdict == Verdict.MATCH
    assert second.fields[0].verdict == Verdict.MISMATCH
    assert second.engines == ("rules",)


async def test_cache_key_is_document_template_version_and_policy() -> None:
    cache = RecordingCache()
    reader = make_reader(FakeExtractor("rules", ALL_FOUND), cache=cache, external_ai_enabled=True)
    sha = hashlib.sha256(PDF).hexdigest()

    await reader.read(make_request(allow_external_ai=False), PDF)
    await reader.read(make_request(allow_external_ai=True), PDF)

    assert cache.keys == [f"{sha}:comparendo:2:local", f"{sha}:comparendo:2:ext"]


async def test_extraction_with_a_failed_extractor_is_not_cached() -> None:
    broken = FakeExtractor("ollama", TimeoutError())
    reader = make_reader(broken, cache=MemoryCache(max_items=10))

    await reader.read(make_request(), PDF)
    await reader.read(make_request(), PDF)

    assert len(broken.calls) == 2


async def test_reports_pages_that_needed_ocr() -> None:
    pages = FakePages(
        [
            PageText(number=1, text="a", origin="native"),
            PageText(number=2, text="b", origin="ocr", confidence=0.8),
            PageText(number=3, text="c", origin="ocr", confidence=0.9),
        ]
    )

    result = await make_reader(FakeExtractor("rules", ALL_FOUND), pages=pages).read(
        make_request(), PDF
    )

    assert (result.pages, result.ocr_pages) == (3, (2, 3))


async def test_blank_expected_value_counts_as_not_provided() -> None:
    result = await make_reader(FakeExtractor("rules", ALL_FOUND)).read(
        make_request({"nro": "  "}), PDF
    )

    assert (result.fields[0].expected, result.fields[0].verdict) == (None, Verdict.NOT_PROVIDED)
