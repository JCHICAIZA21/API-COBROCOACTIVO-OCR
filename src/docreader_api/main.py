"""Production wiring of the HTTP API.

Run: `uvicorn docreader_api.main:build_app --factory --host 0.0.0.0 --port 8000`
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

import asyncpg
from fastapi import FastAPI
from redis.asyncio import Redis

from docreader.factory import build_reader
from docreader.settings import ServiceSettings
from docreader.templates import YamlTemplateRepository

from .app import create_app
from .jobs_pg import PostgresJobStore
from .queue_redis import RedisStreamQueue


def build_app(settings: ServiceSettings | None = None) -> FastAPI:
    """Compose the API from settings; connections open on startup and close on shutdown."""
    s = settings or ServiceSettings()
    templates = YamlTemplateRepository(s.templates_dir)
    reader = build_reader(s, templates=templates)
    pool = asyncpg.create_pool(s.database_url, min_size=1, max_size=10)  # awaited at startup
    store = PostgresJobStore(pool, stale_after=timedelta(seconds=s.item_stale_after_s))
    redis = Redis.from_url(s.redis_url)
    queue = RedisStreamQueue(redis, s.queue_stream)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await pool
        try:
            await store.ensure_schema()
            s.upload_dir.mkdir(parents=True, exist_ok=True)
            yield
        finally:
            await reader.aclose()
            await redis.aclose()
            await pool.close()

    return create_app(
        settings=s, reader=reader, templates=templates, jobs=store, queue=queue, lifespan=lifespan
    )
