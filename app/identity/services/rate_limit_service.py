"""Rate limiting via Redis."""

from __future__ import annotations

from app.identity.core.exceptions import RateLimitError
from app.identity.core.redis.client import RedisClient


class RateLimitService:
    def __init__(self, redis: RedisClient) -> None:
        self._redis = redis

    async def check(self, scope: str, identifier: str, *, limit: int, window_seconds: int) -> None:
        count, allowed = await self._redis.increment_rate_limit(
            scope,
            identifier,
            window_seconds=window_seconds,
            limit=limit,
        )
        if not allowed:
            raise RateLimitError(
                f"Rate limit exceeded for {scope}",
                code=f"rate_limit_{scope}",
            )
