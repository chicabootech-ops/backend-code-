"""Background consumer that drains the event stream for the lifetime of the app."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket

from app.events.bus import CONSUMER_GROUP, EventBus
from app.events.handlers import EventHandlers
from app.storefront.lib.catalog_cache import CatalogCache

logger = logging.getLogger(__name__)

#: How long a read blocks before looping — keeps shutdown responsive.
BLOCK_MS = 5_000
#: Back-off after an unexpected failure so a dead Redis doesn't spin the CPU.
ERROR_BACKOFF_SECONDS = 5.0


def _consumer_name() -> str:
    return f"{socket.gethostname()}-{os.getpid()}"


class EventWorker:
    def __init__(self, bus: EventBus, cache: CatalogCache) -> None:
        self._bus = bus
        self._handlers = EventHandlers(bus, cache)
        self._task: asyncio.Task | None = None
        self._consumer = _consumer_name()

    async def start(self) -> None:
        if not self._bus.enabled:
            logger.info("Event worker disabled — no Redis connection")
            return
        if not await self._bus.ensure_group(CONSUMER_GROUP):
            logger.warning("Event worker not started — consumer group unavailable")
            return
        self._task = asyncio.create_task(self._run(), name="chicaboo-event-worker")
        logger.info("Event worker started as %s", self._consumer)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        logger.info("Event worker stopped")

    async def _run(self) -> None:
        while True:
            try:
                events = await self._bus.read(
                    group=CONSUMER_GROUP, consumer=self._consumer, block_ms=BLOCK_MS
                )
                for event in events:
                    await self._handlers.dispatch(event)
                    await self._bus.ack(event)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("Event worker loop failed; backing off")
                await asyncio.sleep(ERROR_BACKOFF_SECONDS)
