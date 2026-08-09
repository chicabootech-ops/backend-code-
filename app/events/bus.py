"""Redis Streams event bus.

A single stream (``chicaboo:events``) carries every domain event. Consumers read
through a consumer group, so an event is delivered once per group and survives a
restart — unlike pub/sub, where anything published while the worker is down is
lost. The stream is capped so it can never grow without bound.

Publishing is best-effort: a Redis hiccup must never fail a checkout. Every
publish failure is logged and swallowed.
"""

from __future__ import annotations

import logging
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import ResponseError

from app.events.types import Event, EventType

logger = logging.getLogger(__name__)

STREAM_KEY = "chicaboo:events"
CONSUMER_GROUP = "chicaboo-workers"
#: Approximate cap — Redis trims to the nearest node boundary, which is cheap.
MAX_STREAM_LENGTH = 10_000


class EventBus:
    def __init__(self, redis: Redis | None) -> None:
        self._redis = redis

    @property
    def enabled(self) -> bool:
        return self._redis is not None

    async def publish(
        self, event_type: EventType | str, payload: dict[str, Any] | None = None
    ) -> str | None:
        """Append an event to the stream. Returns the stream id, or None if dropped."""
        if self._redis is None:
            return None
        event = Event(type=event_type, payload=payload or {})
        try:
            stream_id = await self._redis.xadd(
                STREAM_KEY,
                event.to_wire(),
                maxlen=MAX_STREAM_LENGTH,
                approximate=True,
            )
        except Exception:  # noqa: BLE001 - events must never break the request
            logger.warning("Failed to publish %s", event_type, exc_info=True)
            return None
        return stream_id.decode() if isinstance(stream_id, bytes) else str(stream_id)

    async def ensure_group(self, group: str = CONSUMER_GROUP) -> bool:
        """Create the consumer group (and the stream) if they don't exist yet."""
        if self._redis is None:
            return False
        try:
            await self._redis.xgroup_create(STREAM_KEY, group, id="$", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                logger.warning("Could not create consumer group %s: %s", group, exc)
                return False
        except Exception:  # noqa: BLE001
            logger.warning("Could not reach Redis to create group %s", group, exc_info=True)
            return False
        return True

    async def read(
        self,
        *,
        group: str = CONSUMER_GROUP,
        consumer: str,
        count: int = 32,
        block_ms: int = 5_000,
    ) -> list[Event]:
        """Block for up to `block_ms` waiting for undelivered events."""
        if self._redis is None:
            return []
        try:
            response = await self._redis.xreadgroup(
                group, consumer, {STREAM_KEY: ">"}, count=count, block=block_ms
            )
        except ResponseError as exc:
            if "NOGROUP" in str(exc):
                await self.ensure_group(group)
                return []
            raise

        events: list[Event] = []
        for _stream, entries in response or []:
            for stream_id, fields in entries:
                sid = stream_id.decode() if isinstance(stream_id, bytes) else str(stream_id)
                events.append(Event.from_wire(fields, sid))
        return events

    async def ack(self, event: Event, *, group: str = CONSUMER_GROUP) -> None:
        if self._redis is None or not event.stream_id:
            return
        try:
            await self._redis.xack(STREAM_KEY, group, event.stream_id)
        except Exception:  # noqa: BLE001
            logger.debug("Failed to ack %s", event.stream_id, exc_info=True)

    # --- lightweight counters used by the storefront (trending, view counts) ---
    async def bump_counter(self, key: str, member: str, *, amount: float = 1.0) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.zincrby(key, amount, member)
        except Exception:  # noqa: BLE001
            logger.debug("Failed to bump counter %s/%s", key, member, exc_info=True)

    async def top_members(self, key: str, limit: int = 10) -> list[tuple[str, float]]:
        if self._redis is None:
            return []
        try:
            rows = await self._redis.zrevrange(key, 0, limit - 1, withscores=True)
        except Exception:  # noqa: BLE001
            return []
        return [
            (member.decode() if isinstance(member, bytes) else str(member), float(score))
            for member, score in rows
        ]


_bus: EventBus | None = None


def set_event_bus(bus: EventBus) -> None:
    """Called once from the app lifespan so services can publish without a request."""
    global _bus
    _bus = bus


def get_event_bus() -> EventBus:
    """Always returns a bus; a disabled one when Redis was never wired up."""
    return _bus if _bus is not None else EventBus(None)
