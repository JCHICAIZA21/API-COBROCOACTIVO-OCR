"""In-memory fakes of the API collaborators: reader, template repository, job store, queue."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from docreader.domain import (
    DocumentTemplate,
    FieldSpec,
    ReadRequest,
    ReadResult,
    ReadStatus,
    TemplateNotFound,
)
from docreader_api.ports import ItemStatus, JobItem, JobSummary


def make_template(template_id: str = "comparendo", version: str = "1") -> DocumentTemplate:
    return DocumentTemplate(
        id=template_id,
        version=version,
        description="Comparendo de tránsito",
        fields=(
            FieldSpec(name="identificacion", kind="id_number", key=True, description="Cédula"),
            FieldSpec(name="fecha", kind="date", required=False),
        ),
    )


def make_result(request: ReadRequest, status: ReadStatus = ReadStatus.OK) -> ReadResult:
    return ReadResult(
        template_id=request.template_id,
        template_version="1",
        document_sha256="0" * 64,
        status=status,
        fields=(),
        pages=1,
        ocr_pages=(),
        engines=("rules",),
        elapsed_ms=5,
        reference=request.reference,
    )


class FakeReader:
    """Records every call; returns a canned result or raises the configured error."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[ReadRequest, bytes | None]] = []

    async def read(self, request: ReadRequest, content: bytes | None = None) -> ReadResult:
        self.calls.append((request, content))
        if self.error is not None:
            raise self.error
        return make_result(request)


class InMemoryTemplates:
    def __init__(self, *templates: DocumentTemplate) -> None:
        self._by_id = {t.id: t for t in templates}

    def get(self, template_id: str) -> DocumentTemplate:
        try:
            return self._by_id[template_id]
        except KeyError:
            raise TemplateNotFound(template_id) from None

    def all(self) -> list[DocumentTemplate]:
        return list(self._by_id.values())


@dataclass
class _Job:
    id: UUID
    client: str
    created_at: datetime
    item_ids: list[UUID]


class InMemoryJobStore:
    def __init__(self) -> None:
        self.jobs: dict[UUID, _Job] = {}
        self.items: dict[UUID, JobItem] = {}

    async def create_job(self, client: str, requests: list[ReadRequest]) -> tuple[UUID, list[UUID]]:
        job_id, item_ids = uuid4(), [uuid4() for _ in requests]
        self.jobs[job_id] = _Job(job_id, client, datetime.now(UTC), item_ids)
        for position, (item_id, request) in enumerate(zip(item_ids, requests, strict=True)):
            self.items[item_id] = JobItem(
                id=item_id,
                job_id=job_id,
                position=position,
                request=request,
                status=ItemStatus.PENDING,
            )
        return job_id, item_ids

    async def get_job(self, job_id: UUID, client: str) -> JobSummary | None:
        job = self._owned(job_id, client)
        if job is None:
            return None
        items = [self.items[i] for i in job.item_ids]

        def count(status: ItemStatus) -> int:
            return sum(1 for item in items if item.status == status)

        return JobSummary(
            id=job.id,
            client=job.client,
            created_at=job.created_at,
            total=len(items),
            pending=count(ItemStatus.PENDING),
            processing=count(ItemStatus.PROCESSING),
            done=count(ItemStatus.DONE),
            failed=count(ItemStatus.FAILED),
            needs_review=sum(
                1
                for item in items
                if item.result is not None and item.result.status == ReadStatus.NEEDS_REVIEW
            ),
        )

    async def list_items(self, job_id: UUID, client: str, offset: int, limit: int) -> list[JobItem]:
        job = self._owned(job_id, client)
        if job is None:
            return []
        return [self.items[i] for i in job.item_ids[offset : offset + limit]]

    async def claim_item(self, item_id: UUID) -> JobItem | None:
        item = self.items.get(item_id)
        if item is None or item.status in (ItemStatus.DONE, ItemStatus.FAILED):
            return None
        return self._update(item_id, status=ItemStatus.PROCESSING)

    async def complete_item(self, item_id: UUID, result: ReadResult) -> None:
        self._update(item_id, status=ItemStatus.DONE, result=result)

    async def fail_item(self, item_id: UUID, error: str) -> None:
        self._update(item_id, status=ItemStatus.FAILED, error=error)

    def _owned(self, job_id: UUID, client: str) -> _Job | None:
        job = self.jobs.get(job_id)
        return job if job is not None and job.client == client else None

    def _update(self, item_id: UUID, **changes: object) -> JobItem:
        self.items[item_id] = self.items[item_id].model_copy(update=changes)
        return self.items[item_id]


class InMemoryQueue:
    def __init__(self) -> None:
        self.enqueued: list[UUID] = []
        self.acked: list[str] = []

    async def enqueue(self, item_ids: list[UUID]) -> None:
        self.enqueued.extend(item_ids)

    async def consume(self, consumer: str) -> AsyncIterator[tuple[str, UUID]]:
        for number, item_id in enumerate(list(self.enqueued)):
            yield str(number), item_id

    async def ack(self, message_id: str) -> None:
        self.acked.append(message_id)
