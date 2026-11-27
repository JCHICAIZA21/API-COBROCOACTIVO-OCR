"""Redis Streams implementation of `WorkQueue`: one stream, one consumer group, at-least-once."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import ResponseError

logger = logging.getLogger(__name__)

_FIELD = "item"


class RedisStreamQueue:
    """Item ids in a stream shared by a consumer group; reclaims messages of dead workers."""

    def __init__(
        self,
        redis: Redis,
        stream: str,
        *,
        group: str = "workers",
        claim_idle_ms: int = 300_000,
        block_ms: int = 5_000,
        batch: int = 16,
    ) -> None:
        self._redis = redis
        self._stream = stream
        self._group = group
        self._claim_idle_ms = claim_idle_ms
        self._block_ms = block_ms
        self._batch = batch

    async def enqueue(self, item_ids: list[UUID]) -> None:
        """Add every id in a single round trip."""
        async with self._redis.pipeline(transaction=False) as pipe:
            for item_id in item_ids:
                pipe.xadd(self._stream, {_FIELD: str(item_id)})
            await pipe.execute()

    async def consume(self, consumer: str) -> AsyncIterator[tuple[str, UUID]]:
        """Yield (message_id, item_id): first messages idle too long elsewhere, then new ones."""
        await self._ensure_group()
        cursor: bytes | str = "0-0"
        while True:
            cursor, entries, *_ = await self._redis.xautoclaim(
                self._stream,
                self._group,
                consumer,
                self._claim_idle_ms,
                start_id=cursor,
                count=self._batch,
            )
            if not entries:
                fresh = await self._redis.xreadgroup(
                    self._group,
                    consumer,
                    {self._stream: ">"},
                    count=self._batch,
                    block=self._block_ms,
                )
                entries = [entry for _, messages in fresh for entry in messages]
            for entry in entries:
                message = await self._parse(entry)
                if message is not None:
                    yield message

    async def ack(self, message_id: str) -> None:
        """Acknowledge and delete, so finished messages do not pile up in memory."""
        async with self._redis.pipeline(transaction=False) as pipe:
            pipe.xack(self._stream, self._group, message_id)
            pipe.xdel(self._stream, message_id)
            await pipe.execute()

    async def _ensure_group(self) -> None:
        try:
            # id="0": items enqueued before the first worker started are delivered too.
            await self._redis.xgroup_create(self._stream, self._group, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def _parse(self, entry: tuple[Any, Any]) -> tuple[str, UUID] | None:
        """Decode one stream entry; a malformed one is logged, acked and skipped."""
        raw_id, fields = entry
        message_id = _text(raw_id)
        values = {_text(key): _text(value) for key, value in (fields or {}).items()}
        try:
            return message_id, UUID(values.get(_FIELD, ""))
        except ValueError:
            logger.warning("Discarding malformed message %s", message_id)
            await self.ack(message_id)
            return None


def _text(value: bytes | str) -> str:
    """Works with clients created with or without `decode_responses`."""
    return value.decode() if isinstance(value, bytes) else value
