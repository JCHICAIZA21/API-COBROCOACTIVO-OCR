"""Configuration from environment variables (prefix DOCREADER_). One place for every knob."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ExtractorName = Literal["rules", "ollama", "claude", "openai"]


class ReaderSettings(BaseSettings):
    """Settings of the reusable core (`docreader`)."""

    model_config = SettingsConfigDict(env_prefix="DOCREADER_", env_file=".env", extra="ignore")

    templates_dir: Path = Path("templates")
    max_pdf_mb: int = 20
    min_confidence: float = 0.85
    cache_size: int = 10_000

    # Policy: hard cap. When False, external AI is never used, whatever the request says.
    external_ai_enabled: bool = False

    # Cascade order. Unconfigured or disabled extractors are skipped.
    extractors: list[ExtractorName] = Field(
        default_factory=lambda: ["rules", "ollama", "claude", "openai"]
    )

    # Pages: native text layer first, OCR only where it is missing.
    cpu_workers: int = Field(default_factory=lambda: max(1, (os.cpu_count() or 2) - 1))
    min_native_chars: int = 25
    ocr_engine: Literal["tesseract", "rapidocr", "none"] = "tesseract"
    ocr_lang: str = "spa"
    ocr_dpi: int = 300

    # Sources
    fileshare_dir: Path | None = None
    http_allowed_hosts: list[str] = Field(default_factory=list)
    http_timeout_s: float = 20.0
    blob_dsn: str | None = None
    blob_query: str = "SELECT content FROM documents WHERE id = $1"

    # LLM extractors (keys come from ANTHROPIC_API_KEY / OPENAI_API_KEY as the SDKs expect)
    claude_model: str = "claude-opus-5-5"
    openai_model: str = "gpt-6-luna"
    llm_timeout_s: float = 60.0
    llm_confidence: float = 0.9
    ollama_enabled: bool = False
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "gpt-oss:20b"


class ServiceSettings(ReaderSettings):
    """Settings of the HTTP service and worker (`docreader_api`)."""

    api_keys: dict[str, str] = Field(default_factory=dict)  # api key -> client name
    database_url: str = "postgresql://docreader:docreader@localhost:5432/docreader"
    redis_url: str = "redis://localhost:6379/0"
    queue_stream: str = "docreader:items"
    upload_dir: Path = Path("data/uploads")
    max_batch_items: int = 5_000
    worker_concurrency: int = 8
