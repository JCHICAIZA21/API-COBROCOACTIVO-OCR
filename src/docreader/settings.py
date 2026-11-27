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
    # CPU budget is about cpu_workers * ocr_threads; measured sweet spot: 2 threads per process.
    cpu_workers: int = Field(default_factory=lambda: max(1, (os.cpu_count() or 2) // 2))
    min_native_chars: int = 25
    ocr_engine: Literal["tesseract", "rapidocr", "none"] = "rapidocr"
    ocr_lang: str = "spa"
    ocr_dpi: int = 200
    ocr_threads: int = 2
    # RapidOCR detection size: 1280 is ~25 % faster than 1600 with the same accuracy (E2).
    ocr_det_max_side: int = 1280
    ocr_models_dir: Path | None = None  # tessdata / RapidOCR models baked into the image

    # Sources
    fileshare_dir: Path | None = None
    http_allowed_hosts: list[str] = Field(default_factory=list)
    http_timeout_s: float = 20.0
    blob_dsn: str | None = None
    blob_query: str = "SELECT content FROM documents WHERE id = $1"

    # LLM extractors (keys come from ANTHROPIC_API_KEY / OPENAI_API_KEY as the SDKs expect).
    # Synchronous calls only: provider batch APIs are not zero-retention (decision 15).
    claude_model: str = "claude-opus-5-5"
    claude_effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None  # model default
    openai_model: str = "gpt-6.2-sol"
    openai_reasoning_effort: Literal["low", "medium", "high"] | None = None  # model default
    llm_max_tokens: int = 4096  # thinking counts toward it: don't lowball
    llm_timeout_s: float = 60.0
    llm_confidence: float = 0.9
    ollama_enabled: bool = False
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "gpt-oss:20b"
    ollama_timeout_s: float = 180.0


class ServiceSettings(ReaderSettings):
    """Settings of the HTTP service and worker (`docreader_api`)."""

    api_keys: dict[str, str] = Field(default_factory=dict)  # api key -> client name
    database_url: str = "postgresql://docreader:docreader@localhost:5432/docreader"
    redis_url: str = "redis://localhost:6379/0"
    queue_stream: str = "docreader:items"
    upload_dir: Path = Path("data/uploads")
    max_batch_items: int = 5_000
    max_upload_batch_mb: int = 512  # total multipart body of POST /v1/jobs/upload
    worker_concurrency: int = 8

    # Batch robustness (decision 15)
    max_attempts: int = 3  # an item fails for good on its 3rd failed attempt
    item_stale_after_s: int = 600  # a "processing" item older than this is claimable again
    retention_days: int = 365  # jobs, results and uploads older than this are deleted; 0 = never
    maintenance_interval_s: int = 300  # retention sweep + re-enqueue of orphan pending items
