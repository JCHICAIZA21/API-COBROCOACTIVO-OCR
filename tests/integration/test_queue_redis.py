"""RedisStreamQueue against a real Redis (DOCREADER_TEST_REDIS=redis://...)."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from redis.asyncio import Redis

from docreader_api.ports import WorkQueue
from docreader_api.queue_redis import RedisStreamQueue

URL = os.environ.get("DOCREADER_TEST_REDIS")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not URL, reason="DOCREADER_TEST_REDIS no está configurada"),
]


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    assert URL is not None
    client = Redis.from_url(URL)
    yield client
    await client.aclose()


@pytest.fixture
async def stream(redis: Redis) -> AsyncIterator[str]:
    name = f"test:{uuid4()}"
    yield name
    await redis.delete(name)


def make_queue(redis: Redis, stream: str, **options: int) -> RedisStreamQueue:
    return RedisStreamQueue(redis, stream, block_ms=100, **options)


async def take(queue: RedisStreamQueue, consumer: str, count: int) -> list[tuple[str, UUID]]:
    """The next `count` messages for `consumer`; fails if they do not arrive in time."""
    messages: list[tuple[str, UUID]] = []
    consuming = queue.consume(consumer)
    async with asyncio.timeout(3):
        async for message in consuming:
            messages.append(message)
            if len(messages) == count:
                break
    await consuming.aclose()  # type: ignore[attr-defined]
    return messages


async def nothing_for(queue: RedisStreamQueue, consumer: str) -> bool:
    consuming = queue.consume(consumer)
    try:
        await asyncio.wait_for(anext(consuming), timeout=0.5)
    except TimeoutError:
        return True
    return False


async def test_is_a_work_queue(redis: Redis, stream: str) -> None:
    assert isinstance(make_queue(redis, stream), WorkQueue)


async def test_delivers_enqueued_items_in_order(redis: Redis, stream: str) -> None:
    queue = make_queue(redis, stream)
    item_ids = [uuid4() for _ in range(3)]
    await queue.enqueue(item_ids)  # before any consumer group exists

    messages = await take(queue, "worker-1", 3)

    assert [item_id for _, item_id in messages] == item_ids
    assert len({message_id for message_id, _ in messages}) == 3


async def test_each_message_goes_to_one_consumer(redis: Redis, stream: str) -> None:
    queue = make_queue(redis, stream, batch=1)  # a consumer owns every message of its batch
    await queue.enqueue([uuid4(), uuid4()])

    [(_, first)] = await take(queue, "worker-1", 1)
    [(_, second)] = await take(queue, "worker-2", 1)

    assert first != second
    assert await nothing_for(queue, "worker-3")


async def test_reclaims_messages_abandoned_by_a_dead_consumer(redis: Redis, stream: str) -> None:
    queue = make_queue(redis, stream, claim_idle_ms=200)
    [item_id] = [uuid4()]
    await queue.enqueue([item_id])
    [(message_id, _)] = await take(queue, "dead-worker", 1)  # never acked

    await asyncio.sleep(0.3)
    reclaimed = await take(queue, "worker-2", 1)

    assert reclaimed == [(message_id, item_id)]


async def test_acked_messages_are_never_delivered_again(redis: Redis, stream: str) -> None:
    queue = make_queue(redis, stream, claim_idle_ms=0)
    await queue.enqueue([uuid4()])
    [(message_id, _)] = await take(queue, "worker-1", 1)

    await queue.ack(message_id)

    assert await nothing_for(queue, "worker-2")
    assert await redis.xlen(stream) == 0  # acked entries do not pile up in memory


async def test_skips_and_drops_a_malformed_message(redis: Redis, stream: str) -> None:
    queue = make_queue(redis, stream)
    await redis.xadd(stream, {"item": "no-es-un-uuid"})
    item_id = uuid4()
    await queue.enqueue([item_id])

    [(_, delivered)] = await take(queue, "worker-1", 1)

    assert delivered == item_id
    assert await redis.xlen(stream) == 1  # only the valid, still unacked, message remains
