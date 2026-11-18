import hashlib
from collections.abc import Mapping, Sequence

from docreader.domain import (
    Candidate,
    DocumentRef,
    DocumentTemplate,
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
        found: Mapping[str, tuple[str, float]] | Exception = {},
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


def upload(
    expected: dict[str, str] | None = None, *, allow_external_ai: bool = False, kind: SourceKind = "upload"
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

    result = await make_reader(rules).read(upload(), PDF)

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

    result = await make_reader(first, second, third).read(upload(), PDF)

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

    result = await make_reader(first, second).read(upload(), PDF)

    assert second.calls == [("nro", "fecha", "placa")]
    assert (result.fields[1].value, result.fields[1].confidence) == ("2024-03-15", 0.6)


async def test_candidates_for_unknown_fields_or_invalid_values_are_dropped() -> None:
    rules = FakeExtractor("rules", {"otro": ("x", 1.0), "fecha": ("31/02/2024", 1.0)})
    ollama = FakeExtractor("ollama", {"fecha": ("01/03/2024", 0.9)})

    result = await make_reader(rules, ollama).read(upload(), PDF)

    assert "fecha" in ollama.calls[0]
    assert [f.name for f in result.fields] == ["nro", "fecha", "placa"]
    assert result.fields[1].value == "2024-03-01"


async def test_external_extractors_skipped_when_request_does_not_allow_them() -> None:
    claude = FakeExtractor("claude", ALL_FOUND, external=True)
    ollama = FakeExtractor("ollama", ALL_FOUND)

    result = await make_reader(claude, ollama, external_ai_enabled=True).read(
        upload(allow_external_ai=False), PDF
    )

    assert claude.calls == []
    assert result.engines == ("ollama",)


async def test_global_cap_wins_over_request_permission() -> None:
    claude = FakeExtractor("claude", ALL_FOUND, external=True)

    result = await make_reader(claude, external_ai_enabled=False).read(
        upload(allow_external_ai=True), PDF
    )

    assert claude.calls == []
    assert result.engines == ()
    assert result.status == ReadStatus.NEEDS_REVIEW


async def test_external_extractors_used_when_request_and_global_cap_allow() -> None:
    claude = FakeExtractor("claude", ALL_FOUND, external=True)

    result = await make_reader(claude, external_ai_enabled=True).read(
        upload(allow_external_ai=True), PDF
    )

    assert result.engines == ("claude",)
    assert result.fields[0].engine == "claude"


async def test_failing_extractor_is_reported_and_cascade_continues() -> None:
    broken = FakeExtractor("ollama", RuntimeError("detalle con datos personales 1023456"))
    rules = FakeExtractor("rules", ALL_FOUND)

    result = await make_reader(broken, rules).read(upload(), PDF)

    assert result.engines == ("ollama", "rules")
    assert result.messages == ("ollama: RuntimeError",)
    assert result.status == ReadStatus.OK
