"""PostgresJobStore against a real PostgreSQL (DOCREADER_TEST_PG=postgresql://...)."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from docreader.domain import DocumentRef, ReadRequest
from docreader_api.jobs_pg import PostgresJobStore
from docreader_api.ports import JobStore

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
