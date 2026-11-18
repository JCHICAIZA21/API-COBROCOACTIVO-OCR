from docreader.cache import MemoryCache
from docreader.domain import Extraction
from docreader.ports import ExtractionCache


def extraction(sha: str) -> Extraction:
    return Extraction(
        document_sha256=sha, template_id="t", template_version="1", candidates={}, pages=1
    )


async def test_returns_what_was_put_and_none_when_missing() -> None:
    cache = MemoryCache(max_items=2)

    await cache.put("a", extraction("a"))

    assert await cache.get("a") == extraction("a")
    assert await cache.get("b") is None


async def test_evicts_least_recently_used_beyond_capacity() -> None:
    cache = MemoryCache(max_items=2)
    await cache.put("a", extraction("a"))
    await cache.put("b", extraction("b"))
    await cache.get("a")  # "a" is now the most recently used

    await cache.put("c", extraction("c"))

    assert await cache.get("b") is None
    assert await cache.get("a") == extraction("a")
    assert await cache.get("c") == extraction("c")


async def test_put_existing_key_replaces_and_refreshes_it() -> None:
    cache = MemoryCache(max_items=2)
    await cache.put("a", extraction("a"))
    await cache.put("b", extraction("b"))

    await cache.put("a", extraction("a2"))
    await cache.put("c", extraction("c"))

    assert await cache.get("a") == extraction("a2")
    assert await cache.get("b") is None


def test_implements_extraction_cache() -> None:
    assert isinstance(MemoryCache(max_items=1), ExtractionCache)
