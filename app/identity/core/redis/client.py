"""Async Redis client wrapper.

Redis here is a cache and a rate-limit store, never the system of record: OTPs
are also rows in ``email_verification``, refresh tokens are also rows in
``refresh_tokens``, and the catalog cache always has a producer behind it. So
with one exception (the phone-OTP session, which has no database mirror) a
Redis outage must degrade the request, not fail it — see ``unavailable_ok``.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable
from typing import TypeVar

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.identity.core.redis import keys
from app.identity.core.redis.bundle import BundleStore, CounterResult, Op

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: Raised when Redis itself is unreachable or misbehaving, as opposed to
#: answering normally. `OSError` covers socket failures that escape redis-py's
#: own wrapping, and `TimeoutError` is the builtin asyncio one.
REDIS_FAILURES = (RedisError, OSError, TimeoutError)


class RedisUnavailableError(RuntimeError):
    """Redis was needed for a request that has no degraded path."""


# Read the catalog version and the versioned payload in one command instead of
# two round trips — the version key is only ever bumped by admin writes.
_CATALOG_READ_LUA = """
local ver = redis.call('GET', KEYS[1])
if not ver then ver = '0' end
local cached = redis.call('GET', 'catalog:' .. ver .. ':' .. ARGV[1])
if not cached then return { ver, false } end
return { ver, cached }
"""


class RedisClient:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._bundle = BundleStore(redis)
        self._catalog_read = redis.register_script(_CATALOG_READ_LUA)

    @property
    def raw(self) -> Redis:
        return self._redis

    async def ping(self) -> bool:
        return bool(await self._redis.ping())

    async def close(self) -> None:
        await self._redis.aclose()

    # --- Bundled identity state (one key, one command) ---
    async def bundle(self, ops: list[Op], *, now: int) -> list[CounterResult | str | None]:
        return await self._bundle.execute(ops, now=now)

    # --- Access token blacklist ---
    async def blacklist_access_token(self, jti: str, ttl_seconds: int) -> None:
        if ttl_seconds > 0:
            await self._redis.setex(keys.access_blacklist_key(jti), ttl_seconds, "1")

    async def is_access_token_blacklisted(self, jti: str) -> bool:
        return bool(await self._redis.exists(keys.access_blacklist_key(jti)))

    # --- Generic cache (catalog response caching) ---
    async def catalog_read(self, version_key: str, name: str) -> tuple[int, bytes | None]:
        """Return (current version, cached payload for `name` at that version)."""
        ver, cached = await self._catalog_read(keys=[version_key], args=[name])
        try:
            version = int(ver)
        except (TypeError, ValueError):
            version = 0
        return version, cached if cached else None

    async def cache_set(self, key: str, value: bytes, ttl_seconds: int) -> None:
        await self._redis.setex(key, ttl_seconds, value)

    async def incr(self, key: str) -> int:
        return int(await self._redis.incr(key))


async def unavailable_ok(operation: Awaitable[T], *, default: T, what: str) -> T:
    """Run a Redis call, falling back to `default` if Redis is unreachable.

    Every caller of this has a database behind it or is a pure cache, so a Redis
    outage costs performance or a layer of defence, never correctness. Logged at
    warning because a persistently degraded cache still needs to be noticed.
    """
    try:
        return await operation
    except REDIS_FAILURES:
        logger.warning("Redis unavailable during %s — degrading", what, exc_info=True)
        return default


async def is_token_revoked(redis: RedisClient, jti: str) -> bool:
    """Blacklist lookup that treats an unreachable Redis as "not revoked".

    Failing closed would 401 every signed-in customer and admin the moment the
    cache blips. Failing open re-honours tokens that were explicitly logged out,
    but only until they expire on their own — which the short access TTL bounds.
    """
    return await unavailable_ok(
        redis.is_access_token_blacklisted(jti),
        default=False,
        what="access token blacklist check",
    )


async def required(operation: Awaitable[T], *, what: str) -> T:
    """Run a Redis call that has no degraded path, as a clean 503 rather than a 500."""
    try:
        return await operation
    except REDIS_FAILURES as exc:
        logger.error("Redis unavailable during %s", what, exc_info=True)
        raise RedisUnavailableError(what) from exc
