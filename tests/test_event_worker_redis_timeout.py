from types import SimpleNamespace

import pytest
from redis.exceptions import TimeoutError

from app.events.bus import EventBus


class _DummyRedis:
    async def xreadgroup(self, *args, **kwargs):
        raise TimeoutError("timed out")

    async def ping(self):
        raise TimeoutError("timed out")


@pytest.mark.asyncio
async def test_event_bus_read_ignores_redis_timeout():
    bus = EventBus(_DummyRedis())
    assert await bus.read(group="g", consumer="c", block_ms=10) == []
