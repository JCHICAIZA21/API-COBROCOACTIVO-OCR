"""Batch worker: takes item ids from the queue, reads each document and stores the outcome."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import traceback
from pathlib import Path
from typing import Protocol
from uuid import UUID

from docreader.domain import (
    DocReaderError,
    DocumentNotFound,
    DocumentRejected,
    ReadRequest,
    ReadResult,
)
from docreader_api.ports import JobItem, JobStore, WorkQueue

logger = logging.getLogger(__name__)

_READ_FAILED = "no se pudo leer el documento"
_INTERNAL_ERROR = "error interno"


def _scrubbed_traceback(exc: BaseException) -> str:
    """Exception type and stack frames without the message, which may carry personal data."""
    return type(exc).__qualname__ + "\n" + "".join(traceback.format_tb(exc.__traceback__))


class Reader(Protocol):
    """What the worker needs from the core: read one request, optionally with its bytes."""

    async def read(self, request: ReadRequest, content: bytes | None = None) -> ReadResult: ...


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
        """Ack only after the outcome is stored; on store or queue failure, leave it for redelivery."""
        try:
            item = await self._store.claim_item(item_id)
            if item is not None:
                await self._process(item)
            await self._queue.ack(message_id)
        except Exception as exc:
            logger.error(
                "Item %s (message %s) left for redelivery: %s",
                item_id,
                message_id,
                _scrubbed_traceback(exc),
            )

    async def _process(self, item: JobItem) -> None:
        upload: Path | None = None
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
