"""Service-level ports: batch job persistence and the work queue."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from docreader.domain import ReadRequest, ReadResult


class ItemStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class JobSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    client: str
    created_at: datetime
    total: int
    pending: int
    processing: int
    done: int
    failed: int
    needs_review: int

    @property
    def finished(self) -> bool:
        return self.pending == 0 and self.processing == 0


class JobItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    job_id: UUID
    position: int
    request: ReadRequest
    status: ItemStatus
    result: ReadResult | None = None
    error: str | None = None


@runtime_checkable
class JobStore(Protocol):
    """Durable state of batch jobs and their items (PostgreSQL in production)."""

    async def create_job(
        self, client: str, requests: list[ReadRequest]
    ) -> tuple[UUID, list[UUID]]: ...

    async def get_job(self, job_id: UUID, client: str) -> JobSummary | None: ...

    async def list_items(
        self, job_id: UUID, client: str, offset: int, limit: int
    ) -> list[JobItem]: ...

    async def claim_item(self, item_id: UUID) -> JobItem | None:
        """Mark a pending (or stale processing) item as processing; None if already finished."""
        ...

    async def complete_item(self, item_id: UUID, result: ReadResult) -> None: ...

    async def fail_item(self, item_id: UUID, error: str) -> None: ...


@runtime_checkable
class WorkQueue(Protocol):
    """At-least-once delivery of item ids to workers (Redis Streams in production)."""

    async def enqueue(self, item_ids: list[UUID]) -> None: ...

    def consume(self, consumer: str) -> AsyncIterator[tuple[str, UUID]]:
        """Yield (message_id, item_id); reclaims messages abandoned by dead consumers."""
        ...

    async def ack(self, message_id: str) -> None: ...
