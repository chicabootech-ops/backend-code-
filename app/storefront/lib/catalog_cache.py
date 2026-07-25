"""Redis-backed catalog response cache.

Version-keyed: every cache key embeds a monotonically increasing version counter.
Admin catalog writes call ``bump()`` which increments the version, instantly
orphaning every previously cached entry (they expire on their own TTL). This gives
immediate invalidation without SCAN/DELETE (Upstash-friendly). All failures are
swallowed — the cache can never break the catalog.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from pydantic import BaseModel

logger = logging.getLogger(__name__)

VERSION_KEY = "catalog:ver"
T = TypeVar("T")


class CatalogCache:
    def __init__(self, redis: Any | None = None) -> None:
        self._redis = redis

    @property
    def enabled(self) -> bool:
        return self._redis is not None

    async def get_or_set(
        self,
        name: str,
        ttl_seconds: int,
        producer: Callable[[], Awaitable[T]],
        *,
        model: type[BaseModel] | None = None,
    ) -> T | Any:
        """`model` rehydrates a hit into the same type a miss returns, so callers can
        always touch attributes. A schema change just fails validation and falls back
        to the producer."""
        if self._redis is None:
            return await producer()
        key = None
        try:
            ver = await self._redis.get_int(VERSION_KEY)
            key = f"catalog:{ver}:{name}"
            cached = await self._redis.cache_get(key)
            if cached is not None:
                payload = json.loads(cached)
                return model.model_validate(payload) if model is not None else payload
        except Exception:  # noqa: BLE001
            return await producer()

        result = await producer()
        if result is None:  # don't cache "not found"
            return result
        try:
            payload = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
            await self._redis.cache_set(key, json.dumps(payload).encode("utf-8"), ttl_seconds)
        except Exception:  # noqa: BLE001
            logger.debug("catalog cache set failed for %s", name, exc_info=True)
        return result

    async def bump(self) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.incr(VERSION_KEY)
        except Exception:  # noqa: BLE001
            logger.debug("catalog cache bump failed", exc_info=True)
