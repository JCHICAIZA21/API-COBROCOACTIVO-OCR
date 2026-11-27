"""Worker and Maintenance behavior through their public `run`, with in-memory doubles."""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections.abc import AsyncIterator, Callable
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from docreader.domain import (
    DocumentRef,
    DocumentRejected,
    ReadRequest,
    ReadResult,
    ReadStatus,
    TemplateNotFound,
)
from docreader_api.ports import ItemStatus, JobItem, JobSummary
from docreader_api.worker import Maintenance, Worker

# --- Test doubles -----------------------------------------------------------------------------


class MemoryStore:
    """JobStore double: keeps items in a dict and records what the worker stored."""

    def __init__(self) -> None:
        self.items: dict[UUID, JobItem] = {}

    def add(self, request: ReadRequest, status: ItemStatus = ItemStatus.PENDING) -> UUID:
        item = JobItem(id=uuid4(), job_id=uuid4(), position=0, request=request, status=status)
        self.items[item.id] = item
        return item.id

    async def create_job(self, client: str, requests: list[ReadRequest]) -> tuple[UUID, list[UUID]]:
        raise NotImplementedError

    async def get_job(self, job_id: UUID, client: str) -> JobSummary | None:
        raise NotImplementedError

    async def list_items(self, job_id: UUID, client: str, offset: int, limit: int) -> list[JobItem]:
        raise NotImplementedError

    async def claim_item(self, item_id: UUID) -> JobItem | None:
        item = self.items[item_id]
        if item.status != ItemStatus.PENDING:
            return None
        self.items[item_id] = item.model_copy(update={"status": ItemStatus.PROCESSING})
        return self.items[item_id]

    async def complete_item(self, item_id: UUID, result: ReadResult) -> None:
        self._finish(item_id, status=ItemStatus.DONE, result=result)

    async def fail_item(self, item_id: UUID, error: str) -> None:
        self._finish(item_id, status=ItemStatus.FAILED, error=error)

    def _finish(self, item_id: UUID, **update: object) -> None:
        self.items[item_id] = self.items[item_id].model_copy(update=update)


class MemoryQueue:
    """WorkQueue double: an asyncio.Queue that records acknowledged message ids."""

    def __init__(self) -> None:
        self._messages: asyncio.Queue[tuple[str, UUID]] = asyncio.Queue()
        self.acked: list[str] = []
        self.changed = asyncio.Condition()

    async def enqueue(self, item_ids: list[UUID]) -> None:
        for item_id in item_ids:
            self._messages.put_nowait((f"msg-{item_id}", item_id))

    async def consume(self, consumer: str) -> AsyncIterator[tuple[str, UUID]]:
        while True:
            yield await self._messages.get()

    async def ack(self, message_id: str) -> None:
        async with self.changed:
            self.acked.append(message_id)
            self.changed.notify_all()


class FakeReader:
    """Reader double: returns a fixed result, or runs `behavior` to fail or block."""

    def __init__(self, behavior: Callable[[ReadRequest], object] | None = None) -> None:
        self.calls: list[tuple[ReadRequest, bytes | None]] = []
        self.changed = asyncio.Condition()
        self._behavior = behavior

    async def read(self, request: ReadRequest, content: bytes | None = None) -> ReadResult:
        async with self.changed:
            self.calls.append((request, content))
            self.changed.notify_all()
        if self._behavior is not None:
            outcome = self._behavior(request)
            if asyncio.iscoroutine(outcome):
                await outcome
        return make_result(request)


def make_request(kind: str = "path", value: str = "comparendos/a.pdf") -> ReadRequest:
    return ReadRequest(
        template_id="comparendo",
        document=DocumentRef(kind=kind, value=value),  # type: ignore[arg-type]
        expected={"identificacion": "1023456789"},
        reference="ref-1",
    )


def make_result(request: ReadRequest) -> ReadResult:
    return ReadResult(
        template_id=request.template_id,
        template_version="1",
        document_sha256="abc",
        status=ReadStatus.OK,
        fields=(),
        pages=1,
        ocr_pages=(),
        engines=("rules",),
        elapsed_ms=5,
        reference=request.reference,
    )


# --- Harness ----------------------------------------------------------------------------------


class Harness:
    def __init__(
        self,
        tmp_path: Path,
        reader: FakeReader | None = None,
        concurrency: int = 4,
        store: MemoryStore | None = None,
    ) -> None:
        self.store = store or MemoryStore()
        self.queue = MemoryQueue()
        self.reader = reader or FakeReader()
        self.upload_dir = tmp_path / "uploads"
        self.upload_dir.mkdir()
        self.worker = Worker(
            reader=self.reader,
            store=self.store,
            queue=self.queue,
            upload_dir=self.upload_dir,
            concurrency=concurrency,
        )
        self.stop = asyncio.Event()
        self.task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self.task = asyncio.create_task(self.worker.run("worker-1", self.stop))

    async def submit(self, *requests: ReadRequest) -> list[UUID]:
        ids = [self.store.add(request) for request in requests]
        await self.queue.enqueue(ids)
        return ids

    async def wait_acked(self, count: int) -> None:
        async with asyncio.timeout(2), self.queue.changed:
            await self.queue.changed.wait_for(lambda: len(self.queue.acked) >= count)

    async def wait_reads(self, count: int) -> None:
        async with asyncio.timeout(2), self.reader.changed:
            await self.reader.changed.wait_for(lambda: len(self.reader.calls) >= count)

    async def shutdown(self) -> None:
        self.stop.set()
        assert self.task is not None
        async with asyncio.timeout(2):
            await self.task


@pytest.fixture
async def harness(tmp_path: Path) -> AsyncIterator[Harness]:
    h = Harness(tmp_path)
    h.start()
    yield h
    await h.shutdown()


# --- Behavior ---------------------------------------------------------------------------------


async def test_reads_a_queued_item_and_stores_its_result(harness: Harness) -> None:
    request = make_request()
    [item_id] = await harness.submit(request)

    await harness.wait_acked(1)

    item = harness.store.items[item_id]
    assert item.status == ItemStatus.DONE
    assert item.result == make_result(request)
    assert harness.reader.calls == [(request, None)]
    assert harness.queue.acked == [f"msg-{item_id}"]


async def test_skips_and_acks_an_item_that_is_already_finished(harness: Harness) -> None:
    item_id = harness.store.add(make_request(), status=ItemStatus.DONE)
    await harness.queue.enqueue([item_id])

    await harness.wait_acked(1)

    assert harness.reader.calls == []
    assert harness.store.items[item_id].status == ItemStatus.DONE


async def test_library_error_fails_the_item_with_its_spanish_message(tmp_path: Path) -> None:
    def reject(_: ReadRequest) -> None:
        raise TemplateNotFound("Plantilla no encontrada: comparendo")

    h = Harness(tmp_path, FakeReader(reject))
    h.start()
    [item_id] = await h.submit(make_request())

    await h.wait_acked(1)
    await h.shutdown()

    item = h.store.items[item_id]
    assert item.status == ItemStatus.FAILED
    assert item.error == "Plantilla no encontrada: comparendo"


async def test_unexpected_error_fails_the_item_and_logs_only_ids(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def crash(request: ReadRequest) -> None:
        raise RuntimeError(f"boom {request.expected['identificacion']}")

    h = Harness(tmp_path, FakeReader(crash))
    h.start()
    [item_id] = await h.submit(make_request())

    await h.wait_acked(1)
    await h.shutdown()

    item = h.store.items[item_id]
    assert item.status == ItemStatus.FAILED
    assert item.error == "error interno"
    assert str(item_id) in caplog.text
    assert str(item.job_id) in caplog.text
    assert "1023456789" not in caplog.text  # never personal data in logs


async def test_reads_an_uploaded_pdf_and_deletes_it_when_done(harness: Harness) -> None:
    name = f"{uuid4()}.pdf"
    (harness.upload_dir / name).write_bytes(b"%PDF-1.7 bytes")
    request = make_request(kind="upload", value=name)
    [item_id] = await harness.submit(request)

    await harness.wait_acked(1)

    assert harness.reader.calls == [(request, b"%PDF-1.7 bytes")]
    assert harness.store.items[item_id].status == ItemStatus.DONE
    assert not (harness.upload_dir / name).exists()


@pytest.mark.parametrize(
    "name",
    [
        "../secret.pdf",
        f"../{uuid4()}.pdf",
        f"sub/{uuid4()}.pdf",
        f"{str(uuid4()).upper()}.pdf",
        f"{uuid4()}.txt",
        f"{uuid4().hex}.pdf",
        "",
    ],
)
async def test_rejects_an_upload_reference_that_is_not_a_bare_uuid_pdf_name(
    harness: Harness, name: str
) -> None:
    outside = harness.upload_dir.parent / "secret.pdf"
    outside.write_bytes(b"%PDF-1.7 not yours")
    [item_id] = await harness.submit(make_request(kind="upload", value=name))

    await harness.wait_acked(1)

    item = harness.store.items[item_id]
    assert item.status == ItemStatus.FAILED
    assert item.error == "referencia de archivo subido inválida"
    assert harness.reader.calls == []
    assert outside.exists()


async def test_fails_the_item_when_the_uploaded_pdf_is_missing(harness: Harness) -> None:
    [item_id] = await harness.submit(make_request(kind="upload", value=f"{uuid4()}.pdf"))

    await harness.wait_acked(1)

    item = harness.store.items[item_id]
    assert item.status == ItemStatus.FAILED
    assert item.error == "el archivo subido no existe"
    assert harness.reader.calls == []


async def test_deletes_the_uploaded_pdf_when_the_item_fails(tmp_path: Path) -> None:
    def reject(_: ReadRequest) -> None:
        raise DocumentRejected("el PDF excede el tamaño máximo")

    h = Harness(tmp_path, FakeReader(reject))
    h.start()
    name = f"{uuid4()}.pdf"
    (h.upload_dir / name).write_bytes(b"%PDF-1.7")
    [item_id] = await h.submit(make_request(kind="upload", value=name))

    await h.wait_acked(1)
    await h.shutdown()

    assert h.store.items[item_id].status == ItemStatus.FAILED
    assert not (h.upload_dir / name).exists()


async def test_reads_at_most_concurrency_items_at_once(tmp_path: Path) -> None:
    gate = asyncio.Event()
    running = peak = 0

    async def slow(_: ReadRequest) -> None:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await gate.wait()
        running -= 1

    h = Harness(tmp_path, FakeReader(slow), concurrency=2)
    h.start()
    await h.submit(*[make_request() for _ in range(5)])

    await h.wait_reads(2)
    await asyncio.sleep(0.05)
    assert len(h.reader.calls) == 2
    gate.set()
    await h.wait_acked(5)
    await h.shutdown()

    assert peak == 2


async def test_stop_finishes_in_flight_items_and_takes_no_new_ones(tmp_path: Path) -> None:
    gate = asyncio.Event()

    async def blocked(_: ReadRequest) -> None:
        await gate.wait()

    h = Harness(tmp_path, FakeReader(blocked), concurrency=1)
    h.start()
    first, second = await h.submit(make_request(), make_request())
    await h.wait_reads(1)

    h.stop.set()
    await asyncio.sleep(0.05)
    assert h.task is not None and not h.task.done()  # waits for the in-flight read
    gate.set()
    await h.shutdown()

    assert h.store.items[first].status == ItemStatus.DONE
    assert h.queue.acked == [f"msg-{first}"]
    assert h.store.items[second].status == ItemStatus.PENDING  # redelivered to another worker
    assert len(h.reader.calls) == 1


class ExhaustedStore(MemoryStore):
    """Every claim finds the item out of attempts, as PostgresJobStore reports it."""

    async def claim_item(self, item_id: UUID) -> JobItem | None:
        self._finish(item_id, status=ItemStatus.FAILED, error="se agotaron los reintentos")
        return self.items[item_id]


async def test_an_item_out_of_attempts_is_not_read_and_its_upload_is_deleted(
    tmp_path: Path,
) -> None:
    h = Harness(tmp_path, store=ExhaustedStore())
    h.start()
    name = f"{uuid4()}.pdf"
    (h.upload_dir / name).write_bytes(b"%PDF-1.7 crashes the worker")
    [item_id] = await h.submit(make_request(kind="upload", value=name))

    await h.wait_acked(1)
    await h.shutdown()

    assert h.reader.calls == []
    assert h.store.items[item_id].error == "se agotaron los reintentos"
    assert not (h.upload_dir / name).exists()


class BrokenStore(MemoryStore):
    async def complete_item(self, item_id: UUID, result: ReadResult) -> None:
        raise ConnectionError("database unavailable")


async def test_leaves_the_message_for_redelivery_when_the_store_fails(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    h = Harness(tmp_path, store=BrokenStore())
    h.start()
    name = f"{uuid4()}.pdf"
    (h.upload_dir / name).write_bytes(b"%PDF-1.7")
    [item_id] = await h.submit(make_request(kind="upload", value=name))

    await h.wait_reads(1)
    await h.shutdown()

    assert h.queue.acked == []
    assert (h.upload_dir / name).exists()  # kept for the retry
    assert str(item_id) in caplog.text


# --- Maintenance ------------------------------------------------------------------------------


class MemoryMaintenanceStore:
    """MaintenanceStore double: orphan ids to hand out, a lock that may be taken elsewhere."""

    def __init__(self, orphans: list[UUID] | None = None, locked_elsewhere: bool = False) -> None:
        self.orphans = orphans or []
        self.locked_elsewhere = locked_elsewhere
        self.purged_before: list[timedelta] = []
        self.rounds = 0
        self.changed = asyncio.Condition()

    @contextlib.asynccontextmanager
    async def exclusive(self) -> AsyncIterator[bool]:
        yield not self.locked_elsewhere

    async def orphaned_pending(self, limit: int) -> list[UUID]:
        async with self.changed:
            self.rounds += 1
            self.changed.notify_all()
        handed, self.orphans = self.orphans[:limit], self.orphans[limit:]
        return handed

    async def purge_jobs(self, older_than: timedelta) -> int:
        self.purged_before.append(older_than)
        return 0


def make_maintenance(
    tmp_path: Path,
    store: MemoryMaintenanceStore,
    queue: MemoryQueue,
    *,
    retention: timedelta | None = timedelta(days=365),
    interval_s: float = 300,
) -> Maintenance:
    return Maintenance(
        store=store,
        queue=queue,
        upload_dir=tmp_path,
        retention=retention,
        interval_s=interval_s,
        requeue_batch=2,
    )


def write_upload(directory: Path, name: str, age_days: float) -> Path:
    path = directory / name
    path.write_bytes(b"%PDF-1.7")
    stamp = time.time() - age_days * 86_400
    os.utime(path, (stamp, stamp))
    return path


async def test_a_round_enqueues_long_pending_items_again_in_bounded_batches(
    tmp_path: Path,
) -> None:
    orphans = [uuid4() for _ in range(3)]
    store, queue = MemoryMaintenanceStore(orphans), MemoryQueue()
    maintenance = make_maintenance(tmp_path, store, queue)

    assert await maintenance.run_once()

    consumed = queue.consume("w")
    delivered = [await anext(consumed) for _ in range(2)]
    assert [item_id for _, item_id in delivered] == orphans[:2]  # the rest, next round


async def test_a_round_deletes_jobs_and_uploads_older_than_the_retention(tmp_path: Path) -> None:
    store = MemoryMaintenanceStore()
    old = write_upload(tmp_path, f"{uuid4()}.pdf", age_days=400)
    recent = write_upload(tmp_path, f"{uuid4()}.pdf", age_days=10)
    not_ours = write_upload(tmp_path, "notas.pdf", age_days=400)

    await make_maintenance(tmp_path, store, MemoryQueue()).run_once()

    assert store.purged_before == [timedelta(days=365)]
    assert not old.exists()
    assert recent.exists()
    assert not_ours.exists()


async def test_without_retention_nothing_is_deleted(tmp_path: Path) -> None:
    store = MemoryMaintenanceStore()
    old = write_upload(tmp_path, f"{uuid4()}.pdf", age_days=4000)

    await make_maintenance(tmp_path, store, MemoryQueue(), retention=None).run_once()

    assert store.purged_before == []
    assert old.exists()


async def test_a_round_is_skipped_while_another_worker_runs_it(tmp_path: Path) -> None:
    store = MemoryMaintenanceStore([uuid4()], locked_elsewhere=True)
    old = write_upload(tmp_path, f"{uuid4()}.pdf", age_days=400)

    assert not await make_maintenance(tmp_path, store, MemoryQueue()).run_once()

    assert (store.rounds, store.purged_before) == (0, [])
    assert old.exists()


class FlakyMaintenanceStore(MemoryMaintenanceStore):
    async def orphaned_pending(self, limit: int) -> list[UUID]:
        handed = await super().orphaned_pending(limit)
        if self.rounds == 1:
            raise ConnectionError("database unavailable")
        return handed


async def test_runs_every_interval_until_stopped_and_survives_a_failed_round(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = FlakyMaintenanceStore()
    maintenance = make_maintenance(tmp_path, store, MemoryQueue(), interval_s=0.01)
    stop = asyncio.Event()
    running = asyncio.create_task(maintenance.run(stop))

    async with asyncio.timeout(2), store.changed:
        await store.changed.wait_for(lambda: store.rounds >= 3)
    stop.set()
    async with asyncio.timeout(1):
        await running

    assert "ConnectionError" in caplog.text
