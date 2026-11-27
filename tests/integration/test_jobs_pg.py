"""PostgresJobStore against a real PostgreSQL (DOCREADER_TEST_PG=postgresql://...)."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import asyncpg
import pytest

from docreader.domain import (
    DocumentRef,
    FieldResult,
    ReadRequest,
    ReadResult,
    ReadStatus,
    Verdict,
)
from docreader_api.jobs_pg import PostgresJobStore
from docreader_api.ports import ItemStatus, JobStore

DSN = os.environ.get("DOCREADER_TEST_PG")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not DSN, reason="DOCREADER_TEST_PG no está configurada"),
]


@pytest.fixture
async def store() -> AsyncIterator[PostgresJobStore]:
    assert DSN is not None
    s = await PostgresJobStore.connect(DSN)
    await s.ensure_schema()
    yield s
    await s.close()


@pytest.fixture
def client() -> str:
    """A fresh client per test isolates its jobs without cleaning the tables."""
    return f"client-{uuid4()}"


def make_request(n: int = 0) -> ReadRequest:
    return ReadRequest(
        template_id="comparendo",
        document=DocumentRef(kind="path", value=f"lote/{n}.pdf"),
        expected={"identificacion": f"10234567{n:02d}"},
        reference=f"ref-{n}",
    )


def make_result(status: ReadStatus) -> ReadResult:
    return ReadResult(
        template_id="comparendo",
        template_version="1",
        document_sha256="ab" * 32,
        status=status,
        fields=(
            FieldResult(
                name="identificacion",
                value="1023456789",
                confidence=0.97,
                engine="rules",
                page=1,
                snippet="C.C. 1.023.456.789",
                expected="1023456789",
                verdict=Verdict.MATCH,
            ),
        ),
        pages=2,
        ocr_pages=(2,),
        engines=("rules",),
        elapsed_ms=120,
        reference="ref-0",
    )


async def test_is_a_job_store(store: PostgresJobStore) -> None:
    assert isinstance(store, JobStore)


async def test_new_job_has_all_items_pending(store: PostgresJobStore, client: str) -> None:
    before = datetime.now(UTC)
    job_id, item_ids = await store.create_job(client, [make_request(n) for n in range(3)])

    job = await store.get_job(job_id, client)

    assert len(item_ids) == 3
    assert job is not None
    assert (job.id, job.client) == (job_id, client)
    assert (job.total, job.pending, job.processing, job.done, job.failed) == (3, 3, 0, 0, 0)
    assert job.needs_review == 0
    assert job.created_at >= before.replace(microsecond=0)
    assert not job.finished


async def test_jobs_of_other_clients_are_invisible(store: PostgresJobStore, client: str) -> None:
    job_id, _ = await store.create_job(client, [make_request()])

    assert await store.get_job(job_id, "otro-cliente") is None
    assert await store.list_items(job_id, "otro-cliente", 0, 10) == []
    assert await store.get_job(uuid4(), client) is None


async def test_lists_items_in_submission_order_with_paging(
    store: PostgresJobStore, client: str
) -> None:
    requests = [make_request(n) for n in range(5)]
    job_id, item_ids = await store.create_job(client, requests)

    page = await store.list_items(job_id, client, offset=1, limit=3)

    assert [item.id for item in page] == item_ids[1:4]
    assert [item.request for item in page] == requests[1:4]
    assert [item.position for item in page] == [1, 2, 3]
    assert all(item.status == ItemStatus.PENDING and item.result is None for item in page)


async def test_an_item_is_claimed_only_once(store: PostgresJobStore, client: str) -> None:
    request = make_request()
    job_id, [item_id] = await store.create_job(client, [request])

    claimed = await store.claim_item(item_id)

    assert claimed is not None
    assert (claimed.id, claimed.job_id, claimed.request) == (item_id, job_id, request)
    assert claimed.status == ItemStatus.PROCESSING
    assert await store.claim_item(item_id) is None
    job = await store.get_job(job_id, client)
    assert job is not None and (job.pending, job.processing) == (0, 1)


async def test_a_stale_processing_item_can_be_claimed_again(
    store: PostgresJobStore, client: str
) -> None:
    assert DSN is not None
    impatient = await PostgresJobStore.connect(DSN, stale_after=timedelta(0))
    try:
        _, [item_id] = await store.create_job(client, [make_request()])
        assert await store.claim_item(item_id) is not None  # its worker died

        again = await impatient.claim_item(item_id)
    finally:
        await impatient.close()

    assert again is not None and again.status == ItemStatus.PROCESSING


async def test_an_item_that_used_all_its_attempts_fails_instead_of_being_claimed(
    client: str,
) -> None:
    assert DSN is not None
    store = await PostgresJobStore.connect(DSN, stale_after=timedelta(0), max_attempts=2)
    try:
        job_id, [item_id] = await store.create_job(client, [make_request()])
        first = await store.claim_item(item_id)  # attempt 1: the worker crashed
        second = await store.claim_item(item_id)  # attempt 2: crashed again

        exhausted = await store.claim_item(item_id)
        job = await store.get_job(job_id, client)
        [item] = await store.list_items(job_id, client, 0, 10)
        after = await store.claim_item(item_id)
    finally:
        await store.close()

    assert first is not None and second is not None
    assert second.status == ItemStatus.PROCESSING
    assert exhausted is not None and exhausted.status == ItemStatus.FAILED  # do not process it
    assert item.status == ItemStatus.FAILED
    assert item.error == "se agotaron los reintentos"
    assert job is not None and (job.failed, job.processing) == (1, 0)
    assert after is None


async def test_completed_items_count_as_done_and_needs_review(
    store: PostgresJobStore, client: str
) -> None:
    job_id, item_ids = await store.create_job(client, [make_request(n) for n in range(2)])
    review = make_result(ReadStatus.NEEDS_REVIEW)
    for item_id, result in zip(item_ids, [make_result(ReadStatus.OK), review], strict=True):
        await store.claim_item(item_id)
        await store.complete_item(item_id, result)

    job = await store.get_job(job_id, client)
    items = await store.list_items(job_id, client, 0, 10)

    assert job is not None
    assert (job.done, job.needs_review, job.pending, job.processing) == (2, 1, 0, 0)
    assert job.finished
    assert items[1].status == ItemStatus.DONE
    assert items[1].result == review
    assert await store.claim_item(item_ids[0]) is None


async def test_failed_items_keep_their_error(store: PostgresJobStore, client: str) -> None:
    job_id, [item_id] = await store.create_job(client, [make_request()])
    await store.claim_item(item_id)

    await store.fail_item(item_id, "Plantilla no encontrada: comparendo")

    job = await store.get_job(job_id, client)
    [item] = await store.list_items(job_id, client, 0, 10)
    assert job is not None and (job.failed, job.done) == (1, 0)
    assert item.status == ItemStatus.FAILED
    assert item.error == "Plantilla no encontrada: comparendo"


async def test_a_finished_item_is_not_overwritten_by_a_late_duplicate(
    store: PostgresJobStore, client: str
) -> None:
    job_id, [item_id] = await store.create_job(client, [make_request()])
    await store.claim_item(item_id)
    await store.complete_item(item_id, make_result(ReadStatus.OK))

    await store.fail_item(item_id, "error interno")

    [item] = await store.list_items(job_id, client, 0, 10)
    assert item.status == ItemStatus.DONE
    assert item.error is None


async def test_creates_a_full_batch_of_5000_items(store: PostgresJobStore, client: str) -> None:
    job_id, item_ids = await store.create_job(client, [make_request(n % 100) for n in range(5000)])

    job = await store.get_job(job_id, client)

    assert len(set(item_ids)) == 5000
    assert job is not None and (job.total, job.pending) == (5000, 5000)


async def test_ensure_schema_is_idempotent(store: PostgresJobStore) -> None:
    await store.ensure_schema()
    await store.ensure_schema()


# --- Maintenance: these scan every job, so each test gets its own empty database ---------------


@pytest.fixture
async def own_db() -> AsyncIterator[str]:
    assert DSN is not None
    name = f"test_{uuid4().hex}"
    admin = await asyncpg.connect(DSN)
    await admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield urlsplit(DSN)._replace(path=f"/{name}").geturl()
    finally:
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()


async def connect(dsn: str, **options: Any) -> PostgresJobStore:
    s = await PostgresJobStore.connect(dsn, **options)
    await s.ensure_schema()
    return s


async def test_only_one_holder_of_the_maintenance_lock_at_a_time(own_db: str) -> None:
    first, second = await connect(own_db), await connect(own_db)
    try:
        async with first.exclusive() as held, second.exclusive() as also_held:
            assert held and not also_held
        async with second.exclusive() as held_later:
            assert held_later
    finally:
        await first.close()
        await second.close()


async def test_hands_out_long_pending_items_in_bounded_batches(own_db: str, client: str) -> None:
    store = await connect(own_db, stale_after=timedelta(milliseconds=500))
    try:
        _, item_ids = await store.create_job(client, [make_request(n) for n in range(4)])
        await store.claim_item(item_ids[3])  # being processed: not an orphan
        assert await store.orphaned_pending(limit=10) == []  # too recent

        await asyncio.sleep(0.6)
        first = await store.orphaned_pending(limit=2)
        second = await store.orphaned_pending(limit=2)
        third = await store.orphaned_pending(limit=2)
    finally:
        await store.close()

    assert len(first) == 2
    assert len(second) == 1  # the first two were just handed out: not again until stale
    assert set(first + second) == set(item_ids[:3])
    assert third == []


async def test_purges_jobs_older_than_the_retention_with_their_items(
    own_db: str, client: str
) -> None:
    store = await connect(own_db)
    try:
        old_job, _ = await store.create_job(client, [make_request(n) for n in range(3)])
        await asyncio.sleep(0.6)
        new_job, _ = await store.create_job(client, [make_request()])

        purged = await store.purge_jobs(older_than=timedelta(milliseconds=500))

        assert purged == 1
        assert await store.get_job(old_job, client) is None
        assert await store.list_items(old_job, client, 0, 10) == []
        assert await store.get_job(new_job, client) is not None
        assert await store.purge_jobs(older_than=timedelta(milliseconds=500)) == 0
    finally:
        await store.close()
