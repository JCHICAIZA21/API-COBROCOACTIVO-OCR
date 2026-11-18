"""Composition root: the only place that knows concrete adapters. Imports are lazy so optional
extras (OCR engines, LLM SDKs, asyncpg) are needed only when configured."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

from .cache import MemoryCache
from .domain import DocReaderError
from .ports import DocumentSource, FieldExtractor, OcrEngine, PageReader
from .reader import DocumentReader
from .rules import RegexExtractor
from .settings import ReaderSettings
from .templates import YamlTemplateRepository


def build_reader(settings: ReaderSettings | None = None) -> DocumentReader:
    """Build a ready-to-use reader from settings (environment variables by default)."""
    s = settings or ReaderSettings()
    max_bytes = s.max_pdf_mb * 1024 * 1024
    return DocumentReader(
        templates=YamlTemplateRepository(s.templates_dir),
        pages=build_page_reader(s),
        extractors=build_extractors(s),
        sources=build_sources(s, max_bytes),
        cache=MemoryCache(s.cache_size) if s.cache_size > 0 else None,
        min_confidence=s.min_confidence,
        external_ai_enabled=s.external_ai_enabled,
        max_pdf_bytes=max_bytes,
    )


def build_page_reader(s: ReaderSettings) -> PageReader:
    from .adapters.pages import PdfPageReader

    return PdfPageReader(
        ocr_factory=ocr_factory(s),
        workers=s.cpu_workers,
        min_native_chars=s.min_native_chars,
        dpi=s.ocr_dpi,
    )


def ocr_factory(s: ReaderSettings) -> Callable[[], OcrEngine] | None:
    """A picklable factory: each CPU worker process builds its own OCR engine once."""
    match s.ocr_engine:
        case "tesseract":
            from .adapters.ocr_tesseract import TesseractOcr

            return partial(TesseractOcr, lang=s.ocr_lang)
        case "rapidocr":
            from .adapters.ocr_rapidocr import RapidOcr

            return partial(RapidOcr)
        case "none":
            return None
    raise DocReaderError(f"motor OCR desconocido: {s.ocr_engine}")


def build_sources(s: ReaderSettings, max_bytes: int) -> dict[str, DocumentSource]:
    from .adapters.sources import FileShareSource, HttpSource, PostgresBlobSource

    sources: dict[str, DocumentSource] = {}
    if s.fileshare_dir:
        sources["path"] = FileShareSource(s.fileshare_dir, max_bytes=max_bytes)
    if s.http_allowed_hosts:
        sources["url"] = HttpSource(
            s.http_allowed_hosts, max_bytes=max_bytes, timeout_s=s.http_timeout_s
        )
    if s.blob_dsn:
        sources["blob"] = PostgresBlobSource(s.blob_dsn, s.blob_query, max_bytes=max_bytes)
    return sources


def build_extractors(s: ReaderSettings) -> list[FieldExtractor]:
    """Cascade in the configured order. External engines exist only under the global cap."""
    extractors: list[FieldExtractor] = []
    for name in s.extractors:
        match name:
            case "rules":
                extractors.append(RegexExtractor())
            case "ollama" if s.ollama_enabled:
                from .adapters.llm_ollama import OllamaExtractor

                extractors.append(
                    OllamaExtractor(s.ollama_url, s.ollama_model, timeout_s=s.llm_timeout_s)
                )
            case "claude" if s.external_ai_enabled:
                from .adapters.llm_claude import ClaudeExtractor

                extractors.append(
                    ClaudeExtractor(
                        s.claude_model, confidence=s.llm_confidence, timeout_s=s.llm_timeout_s
                    )
                )
            case "openai" if s.external_ai_enabled:
                from .adapters.llm_openai import OpenAIExtractor

                extractors.append(
                    OpenAIExtractor(
                        s.openai_model, confidence=s.llm_confidence, timeout_s=s.llm_timeout_s
                    )
                )
    return extractors
