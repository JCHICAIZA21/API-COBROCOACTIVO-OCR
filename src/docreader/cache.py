"""In-process extraction cache (LRU). One per process; use a shared cache across workers."""

from __future__ import annotations

from collections import OrderedDict

from .domain import Extraction


class MemoryCache:
    """`ExtractionCache` that keeps the `max_items` most recently used extractions."""

    def __init__(self, max_items: int) -> None:
        self._max_items = max_items
        self._items: OrderedDict[str, Extraction] = OrderedDict()

    async def get(self, key: str) -> Extraction | None:
        extraction = self._items.get(key)
        if extraction is not None:
            self._items.move_to_end(key)
        return extraction

    async def put(self, key: str, extraction: Extraction) -> None:
        self._items[key] = extraction
        self._items.move_to_end(key)
        while len(self._items) > self._max_items:
            self._items.popitem(last=False)
