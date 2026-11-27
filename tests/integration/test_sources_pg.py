"""PostgresBlobSource against a real PostgreSQL. Set DOCREADER_TEST_PG_DSN to run it, e.g.

docker run -d --rm --name docreader-pg -e POSTGRES_PASSWORD=x -p 55432:5432 postgres:17-alpine
DOCREADER_TEST_PG_DSN=postgresql://postgres:x@localhost:55432/postgres
"""

import os
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from docreader.adapters.sources import PostgresBlobSource
from docreader.domain import DocumentNotFound, DocumentRejected

pytestmark = pytest.mark.integration

DSN = os.environ.get("DOCREADER_TEST_PG_DSN", "")
TABLE = "docreader_test_sources_pg"
PDF = b"%PDF-1.7 \x00\xff binary"
DOC_UUID = uuid.uuid4()


@pytest.fixture
async def documents() -> AsyncIterator[None]:
    if not DSN:
        pytest.skip("DOCREADER_TEST_PG_DSN is not set")
    conn = await asyncpg.connect(DSN)
    await conn.execute(f"DROP TABLE IF EXISTS {TABLE}")
    await conn.execute(f"CREATE TABLE {TABLE} (id text PRIMARY KEY, uid uuid, content bytea)")
    await conn.execute(f"INSERT INTO {TABLE} VALUES ('doc-1', $1, $2)", DOC_UUID, PDF)
    yield
    await conn.execute(f"DROP TABLE IF EXISTS {TABLE}")
    await conn.close()


async def test_reads_a_blob_by_text_id(documents: None) -> None:
    source = PostgresBlobSource(DSN, f"SELECT content FROM {TABLE} WHERE id = $1", max_bytes=1024)
    try:
        assert await source.fetch("doc-1") == PDF
        with pytest.raises(DocumentNotFound):
            await source.fetch("doc-2")
        with pytest.raises(DocumentNotFound):  # treated as data, never as SQL
            await source.fetch("doc-1' OR '1'='1")
    finally:
        await source.aclose()


async def test_enforces_max_bytes(documents: None) -> None:
    query = f"SELECT content FROM {TABLE} WHERE id = $1"
    source = PostgresBlobSource(DSN, query, max_bytes=len(PDF) - 1)
    try:
        with pytest.raises(DocumentRejected):
            await source.fetch("doc-1")
    finally:
        await source.aclose()


async def test_rejects_a_ref_that_does_not_fit_the_column_type(documents: None) -> None:
    source = PostgresBlobSource(DSN, f"SELECT content FROM {TABLE} WHERE uid = $1", max_bytes=1024)
    try:
        assert await source.fetch(str(DOC_UUID)) == PDF
        with pytest.raises(DocumentRejected):
            await source.fetch("not-a-uuid")
    finally:
        await source.aclose()
