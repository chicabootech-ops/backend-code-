"""Async Redis client wrapper."""

from __future__ import annotations

from redis.asyncio import Redis

from app.identity.core.redis import keys


class RedisClient:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    @property
    def raw(self) -> Redis:
        return self._redis

    async def ping(self) -> bool:
        return bool(await self._redis.ping())

    async def close(self) -> None:
        await self._redis.aclose()

    # --- OTP ---
    async def set_otp(self, purpose: str, email_normalized: str, otp: str, ttl_seconds: int) -> None:
        await self._redis.setex(keys.otp_key(purpose, email_normalized), ttl_seconds, otp)

    async def get_otp(self, purpose: str, email_normalized: str) -> str | None:
        value = await self._redis.get(keys.otp_key(purpose, email_normalized))
        return value.decode() if value else None

    async def delete_otp(self, purpose: str, email_normalized: str) -> None:
        await self._redis.delete(keys.otp_key(purpose, email_normalized))

    # --- Access token blacklist ---
    async def blacklist_access_token(self, jti: str, ttl_seconds: int) -> None:
        if ttl_seconds > 0:
            await self._redis.setex(keys.access_blacklist_key(jti), ttl_seconds, "1")

    async def is_access_token_blacklisted(self, jti: str) -> bool:
        return bool(await self._redis.exists(keys.access_blacklist_key(jti)))

    # --- Refresh session cache (optional fast path) ---
    async def cache_refresh_session(self, jti: str, user_id: str, ttl_seconds: int) -> None:
        await self._redis.setex(keys.refresh_session_key(jti), ttl_seconds, user_id)

    async def get_refresh_session_user(self, jti: str) -> str | None:
        value = await self._redis.get(keys.refresh_session_key(jti))
        return value.decode() if value else None

    async def delete_refresh_session(self, jti: str) -> None:
        await self._redis.delete(keys.refresh_session_key(jti))

    # --- Rate limiting ---
    async def increment_rate_limit(
        self,
        scope: str,
        identifier: str,
        *,
        window_seconds: int,
        limit: int,
    ) -> tuple[int, bool]:
        """Return (current_count, is_allowed)."""
        key = keys.rate_limit_key(scope, identifier)
        pipe = self._redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, window_seconds, nx=True)
        count, _ = await pipe.execute()
        return int(count), int(count) <= limit
