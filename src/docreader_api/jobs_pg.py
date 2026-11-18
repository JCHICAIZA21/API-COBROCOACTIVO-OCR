"""PostgreSQL implementation of `JobStore` (asyncpg). One round trip per operation."""

from __future__ import annotations

from datetime import timedelta
from importlib import resources
from typing import Self
from uuid import UUID, uuid4

import asyncpg

from docreader.domain import ReadRequest, ReadResult
from docreader_api.ports import ItemStatus, JobItem, JobSummary

_SCHEMA = resources.files("docreader_api").joinpath("schema.sql").read_text(encoding="utf-8")
_SCHEMA_LOCK = 0x646F6372  # advisory lock: api and workers starting together apply it once

_ITEM_COLUMNS = "i.id, i.job_id, i.position, i.request, i.status, i.result, i.error"

_JOB_SUMMARY = """
SELECT j.id, j.client, j.created_at,
       count(i.id)                                                 AS total,
       count(*) FILTER (WHERE i.status = 'pending')                AS pending,
       count(*) FILTER (WHERE i.status = 'processing')             AS processing,
       count(*) FILTER (WHERE i.status = 'done')                   AS done,
       count(*) FILTER (WHERE i.status = 'failed')                 AS failed,
       count(*) FILTER (WHERE i.result ->> 'status' = 'needs_review') AS needs_review
FROM reading_jobs j
LEFT JOIN reading_items i ON i.job_id = j.id
WHERE j.id = $1 AND j.client = $2
GROUP BY j.id
"""

_LIST_ITEMS = f"""
SELECT {_ITEM_COLUMNS}
FROM reading_items i
JOIN reading_jobs j ON j.id = i.job_id
WHERE i.job_id = $1 AND j.client = $2
ORDER BY i.position
OFFSET $3 LIMIT $4
"""  # noqa: S608 - constant column list, values are bound parameters

_CLAIM_ITEM = f"""
UPDATE reading_items AS i
SET status = 'processing', attempts = attempts + 1, updated_at = now()
WHERE id = $1
  AND (status = 'pending' OR (status = 'processing' AND updated_at < now() - $2::interval))
RETURNING {_ITEM_COLUMNS}
"""  # noqa: S608 - constant column list, values are bound parameters

# Only the claimed item changes: a finished item is never overwritten by a late duplicate.
_COMPLETE_ITEM = """
UPDATE reading_items SET status = 'done', result = $2, error = NULL, updated_at = now()
WHERE id = $1 AND status = 'processing'
"""

_FAIL_ITEM = """
UPDATE reading_items SET status = 'failed', error = $2, updated_at = now()
WHERE id = $1 AND status = 'processing'
"""


class PostgresJobStore:
    """Jobs and their items in `reading_jobs` / `reading_items`; reads are scoped by client."""

    def __init__(
        self, pool: asyncpg.Pool, *, stale_after: timedelta = timedelta(minutes=10)
    ) -> None:
        self._pool = pool
        self._stale_after = stale_after  # a "processing" item older than this may be re-claimed

    @classmethod
    async def connect(
        cls,
        dsn: str,
        *,
        min_size: int = 1,
        max_size: int = 10,
        stale_after: timedelta = timedelta(minutes=10),
    ) -> Self:
        pool = await asyncpg.create_pool(dsn, min_size=min_size, max_size=max_size)
        return cls(pool, stale_after=stale_after)

    async def ensure_schema(self) -> None:
        """Create tables and indexes if missing (idempotent, safe under concurrent startups)."""
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", _SCHEMA_LOCK)
            await conn.execute(_SCHEMA)

    async def close(self) -> None:
        await self._pool.close()

    async def create_job(
        self, client: str, requests: list[ReadRequest]
    ) -> tuple[UUID, list[UUID]]:
        job_id = uuid4()
        item_ids = [uuid4() for _ in requests]
        records = [
            (item_id, job_id, position, request.model_dump_json())
            for position, (item_id, request) in enumerate(zip(item_ids, requests, strict=True))
        ]
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO reading_jobs (id, client) VALUES ($1, $2)", job_id, client
            )
            await conn.copy_records_to_table(
                "reading_items", records=records, columns=("id", "job_id", "position", "request")
            )
        return job_id, item_ids

    async def get_job(self, job_id: UUID, client: str) -> JobSummary | None:
        row = await self._pool.fetchrow(_JOB_SUMMARY, job_id, client)
        return None if row is None else JobSummary(**dict(row))

    async def list_items(
        self, job_id: UUID, client: str, offset: int, limit: int
    ) -> list[JobItem]:
        rows = await self._pool.fetch(_LIST_ITEMS, job_id, client, offset, limit)
        return [_to_item(row) for row in rows]

    async def claim_item(self, item_id: UUID) -> JobItem | None:
        row = await self._pool.fetchrow(_CLAIM_ITEM, item_id, self._stale_after)
        return None if row is None else _to_item(row)

    async def complete_item(self, item_id: UUID, result: ReadResult) -> None:
        await self._pool.execute(_COMPLETE_ITEM, item_id, result.model_dump_json())

    async def fail_item(self, item_id: UUID, error: str) -> None:
        await self._pool.execute(_FAIL_ITEM, item_id, error)


def _to_item(row: asyncpg.Record) -> JobItem:
    result = row["result"]
    return JobItem(
        id=row["id"],
        job_id=row["job_id"],
        position=row["position"],
        request=ReadRequest.model_validate_json(row["request"]),
        status=ItemStatus(row["status"]),
        result=None if result is None else ReadResult.model_validate_json(result),
        error=row["error"],
    )
