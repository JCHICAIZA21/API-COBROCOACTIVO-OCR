"""Batch worker: takes item ids from the queue, reads each document and stores the outcome.

Also runs the periodic maintenance (retention, re-enqueue of orphan items).
Run it with `python -m docreader_api.worker`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import socket
import time
import traceback
from contextlib import AbstractAsyncContextManager
from datetime import timedelta
from pathlib import Path
from typing import Protocol
from uuid import UUID

from redis.asyncio import Redis

from docreader.domain import (
    DocReaderError,
    DocumentNotFound,
    DocumentRejected,
    ReadRequest,
    ReadResult,
)
from docreader.settings import ServiceSettings
from docreader_api.jobs_pg import PostgresJobStore
from docreader_api.ports import ItemStatus, JobItem, JobStore, WorkQueue
from docreader_api.queue_redis import RedisStreamQueue

logger = logging.getLogger(__name__)

_READ_FAILED = "no se pudo leer el documento"
_INTERNAL_ERROR = "error interno"

# The queue hands a dead worker's message to another one only after the store already allows
# re-claiming its item; otherwise the new worker would find it "processing", ack it, and the
# item would stall.
_RECLAIM_MARGIN_S = 60


class Reader(Protocol):
    """What the worker needs from the core: read one request, optionally with its bytes."""

    async def read(self, request: ReadRequest, content: bytes | None = None) -> ReadResult: ...


class MaintenanceStore(Protocol):
    """Housekeeping operations of the job store (PostgresJobStore implements them)."""

    def exclusive(self) -> AbstractAsyncContextManager[bool]:
        """Hold the cluster-wide maintenance lock; yields False if another worker has it."""
        ...

    async def orphaned_pending(self, limit: int) -> list[UUID]: ...

    async def purge_jobs(self, older_than: timedelta) -> int: ...


class Worker:
    """Consumes queued items with bounded concurrency; at-least-once, acks after storing."""

    def __init__(
        self,
        *,
        reader: Reader,
        store: JobStore,
        queue: WorkQueue,
        upload_dir: Path,
        concurrency: int,
    ) -> None:
        self._reader = reader
        self._store = store
        self._queue = queue
        self._upload_dir = upload_dir
        self._concurrency = concurrency

    async def run(self, consumer: str, stop: asyncio.Event) -> None:
        """Process messages until `stop` is set, then wait for the in-flight ones."""
        inflight: set[asyncio.Task[None]] = set()
        consuming = asyncio.create_task(self._consume(consumer, inflight))
        stopping = asyncio.create_task(stop.wait())
        try:
            await asyncio.wait({consuming, stopping}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            stopping.cancel()
            consuming.cancel()
            try:
                with contextlib.suppress(asyncio.CancelledError):
                    await consuming  # re-raises a consumer failure (e.g. lost queue connection)
            finally:
                await asyncio.gather(*inflight, return_exceptions=True)

    async def _consume(self, consumer: str, inflight: set[asyncio.Task[None]]) -> None:
        slots = asyncio.Semaphore(self._concurrency)
        async for message_id, item_id in self._queue.consume(consumer):
            await slots.acquire()
            task = asyncio.create_task(self._handle(message_id, item_id))
            inflight.add(task)
            task.add_done_callback(inflight.discard)
            task.add_done_callback(lambda _: slots.release())

    async def _handle(self, message_id: str, item_id: UUID) -> None:
        """Ack only after the outcome is stored; if storing fails, leave it for redelivery."""
        try:
            item = await self._store.claim_item(item_id)
            if item is not None and item.status == ItemStatus.PROCESSING:
                await self._process(item)
            elif item is not None:  # the store failed it: no attempts left
                logger.warning("Item %s of job %s failed: attempts exhausted", item.id, item.job_id)
                await self._discard_upload(item.request)
            await self._queue.ack(message_id)
        except Exception as exc:
            logger.error(
                "Item %s (message %s) left for redelivery: %s",
                item_id,
                message_id,
                _scrubbed_traceback(exc),
            )

    async def _process(self, item: JobItem) -> None:
        try:
            upload = self._upload_path(item.request)
            content = None if upload is None else await _read_upload(upload)
            result = await self._reader.read(item.request, content)
        except DocReaderError as exc:
            await self._store.fail_item(item.id, str(exc) or _READ_FAILED)
        except Exception as exc:
            logger.error(
                "Unexpected error on item %s of job %s: %s",
                item.id,
                item.job_id,
                _scrubbed_traceback(exc),
            )
            await self._store.fail_item(item.id, _INTERNAL_ERROR)
        else:
            await self._store.complete_item(item.id, result)
        await self._discard_upload(item.request)

    async def _discard_upload(self, request: ReadRequest) -> None:
        """Delete the uploaded PDF of a finished item; invalid references are never touched."""
        with contextlib.suppress(DocumentRejected):
            upload = self._upload_path(request)
            if upload is not None:
                await asyncio.to_thread(upload.unlink, missing_ok=True)

    def _upload_path(self, request: ReadRequest) -> Path | None:
        """Where an uploaded PDF was stored; None for the other kinds of reference.

        The reference must be a bare `<uuid>.pdf` name, never a path.
        """
        if request.document.kind != "upload":
            return None
        name = request.document.value
        if not (name.endswith(".pdf") and _is_canonical_uuid(name.removesuffix(".pdf"))):
            raise DocumentRejected("referencia de archivo subido inválida")
        return self._upload_dir / name


class Maintenance:
    """Periodic housekeeping; each round runs in one worker at a time (database lock)."""

    def __init__(
        self,
        *,
        store: MaintenanceStore,
        queue: WorkQueue,
        upload_dir: Path,
        retention: timedelta | None,
        interval_s: float,
        requeue_batch: int = 1_000,
    ) -> None:
        self._store = store
        self._queue = queue
        self._upload_dir = upload_dir
        self._retention = retention  # None: keep everything
        self._interval_s = interval_s
        self._requeue_batch = requeue_batch

    async def run(self, stop: asyncio.Event) -> None:
        """A round now and then every `interval_s` until `stop`; a failed round is only logged."""
        while not stop.is_set():
            try:
                await self.run_once()
            except Exception as exc:
                logger.error("Maintenance round failed: %s", _scrubbed_traceback(exc))
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(self._interval_s):
                    await stop.wait()

    async def run_once(self) -> bool:
        """Retention, then re-enqueue of orphan pending items; False if another worker has it."""
        async with self._store.exclusive() as held:
            if not held:
                return False
            if self._retention is not None:
                jobs = await self._store.purge_jobs(self._retention)
                files = await asyncio.to_thread(_purge_uploads, self._upload_dir, self._retention)
                if jobs or files:
                    logger.info("Retention: deleted %d jobs and %d uploads", jobs, files)
            orphans = await self._store.orphaned_pending(self._requeue_batch)
            if orphans:
                await self._queue.enqueue(orphans)
                logger.info("Re-enqueued %d orphan pending items", len(orphans))
            return True


def _purge_uploads(directory: Path, older_than: timedelta) -> int:
    """Delete our `<uuid>.pdf` uploads last written before `older_than` ago; returns how many.

    Covers the files of purged jobs and those of jobs that were never created.
    """
    cutoff = time.time() - older_than.total_seconds()
    removed = 0
    for path in directory.glob("*.pdf"):
        with contextlib.suppress(FileNotFoundError):  # a worker may delete it meanwhile
            if _is_canonical_uuid(path.stem) and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
    return removed


def _is_canonical_uuid(text: str) -> bool:
    try:
        return str(UUID(text)) == text
    except ValueError:
        return False


async def _read_upload(path: Path) -> bytes:
    try:
        return await asyncio.to_thread(path.read_bytes)
    except FileNotFoundError:
        raise DocumentNotFound("el archivo subido no existe") from None


def _scrubbed_traceback(exc: BaseException) -> str:
    """Exception type and stack frames without the message, which may carry personal data."""
    return type(exc).__qualname__ + "\n" + "".join(traceback.format_tb(exc.__traceback__))


# --- Composition root of the worker process ----------------------------------------------------


async def run_worker(settings: ServiceSettings) -> None:
    """Wire PostgreSQL, Redis and the core reader; run until SIGTERM/SIGINT."""
    from docreader.factory import build_reader  # lazy: the core's composition root

    store = await PostgresJobStore.connect(
        settings.database_url,
        max_size=settings.worker_concurrency + 2,  # + the maintenance lock and its queries
        stale_after=timedelta(seconds=settings.item_stale_after_s),
        max_attempts=settings.max_attempts,
    )
    redis = Redis.from_url(settings.redis_url)
    try:
        await store.ensure_schema()
        queue = RedisStreamQueue(
            redis,
            settings.queue_stream,
            claim_idle_ms=(settings.item_stale_after_s + _RECLAIM_MARGIN_S) * 1000,
        )
        worker = Worker(
            reader=build_reader(settings),
            store=store,
            queue=queue,
            upload_dir=settings.upload_dir,
            concurrency=settings.worker_concurrency,
        )
        maintenance = Maintenance(
            store=store,
            queue=queue,
            upload_dir=settings.upload_dir,
            retention=timedelta(days=settings.retention_days) if settings.retention_days else None,
            interval_s=settings.maintenance_interval_s,
        )
        stop = asyncio.Event()
        _stop_on_signals(stop)
        consumer = f"{socket.gethostname()}-{os.getpid()}"
        logger.info("Worker %s started, concurrency %d", consumer, settings.worker_concurrency)
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(worker.run(consumer, stop))
            tasks.create_task(maintenance.run(stop))
        logger.info("Worker %s stopped", consumer)
    finally:
        await redis.aclose()
        await store.close()


def _stop_on_signals(stop: asyncio.Event) -> None:
    """SIGTERM (docker stop) and SIGINT set `stop`; on Windows Ctrl+C cancels the run instead."""
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run_worker(ServiceSettings()))


if __name__ == "__main__":
    main()
