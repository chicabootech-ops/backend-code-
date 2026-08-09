"""What the background worker does with each event type.

Handlers must be idempotent — a consumer restart can redeliver an unacked entry —
and must never raise; the worker acks regardless so one poison event cannot wedge
the stream.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from app.events.bus import EventBus
from app.events.types import Event, EventType
from app.storefront.lib.catalog_cache import CatalogCache

logger = logging.getLogger(__name__)

#: Sorted set of product slugs by view count, all-time and per-day.
TRENDING_KEY = "chicaboo:trending:products"
#: Rolling counters the admin analytics endpoint can read cheaply.
STATS_KEY = "chicaboo:stats"


def _day_key(prefix: str, moment: datetime | None = None) -> str:
    stamp = (moment or datetime.now(UTC)).strftime("%Y%m%d")
    return f"{prefix}:{stamp}"


class EventHandlers:
    def __init__(self, bus: EventBus, cache: CatalogCache) -> None:
        self._bus = bus
        self._cache = cache

    @property
    def routes(self) -> dict[str, Callable[[Event], Awaitable[None]]]:
        return {
            EventType.CATALOG_CHANGED: self.on_catalog_changed,
            EventType.PRODUCT_VIEWED: self.on_product_viewed,
            EventType.ORDER_CREATED: self.on_order_created,
            EventType.PAYMENT_CAPTURED: self.on_payment_captured,
            EventType.PAYMENT_FAILED: self.on_payment_failed,
            EventType.INVENTORY_LOW: self.on_inventory_low,
            EventType.INVENTORY_OUT: self.on_inventory_low,
            EventType.NEWSLETTER_SUBSCRIBED: self.on_newsletter_subscribed,
            EventType.REVIEW_SUBMITTED: self.on_review_submitted,
        }

    async def dispatch(self, event: Event) -> None:
        handler = self.routes.get(str(event.type))
        if handler is None:
            logger.debug("No handler for event %s", event.type)
            return
        try:
            await handler(event)
        except Exception:  # noqa: BLE001 - one bad event must not stall the stream
            logger.exception("Handler for %s failed (event %s)", event.type, event.id)

    # --- catalog -------------------------------------------------------------
    async def on_catalog_changed(self, event: Event) -> None:
        await self._cache.bump()
        logger.info("Catalog cache invalidated by %s", event.payload.get("reason", "admin write"))

    async def on_product_viewed(self, event: Event) -> None:
        slug = event.payload.get("slug")
        if not slug:
            return
        await self._bus.bump_counter(TRENDING_KEY, slug)
        await self._bus.bump_counter(_day_key(TRENDING_KEY), slug)

    # --- commerce ------------------------------------------------------------
    async def on_order_created(self, event: Event) -> None:
        await self._bus.bump_counter(_day_key(STATS_KEY), "orders_created")
        logger.info(
            "Order created: %s (total_paise=%s)",
            event.payload.get("order_number"),
            event.payload.get("total_paise"),
        )

    async def on_payment_captured(self, event: Event) -> None:
        await self._bus.bump_counter(_day_key(STATS_KEY), "payments_captured")
        await self._bus.bump_counter(
            _day_key(STATS_KEY), "revenue_paise", amount=float(event.payload.get("amount_paise") or 0)
        )
        logger.info(
            "Payment captured for order %s (%s paise)",
            event.payload.get("order_number"),
            event.payload.get("amount_paise"),
        )

    async def on_payment_failed(self, event: Event) -> None:
        await self._bus.bump_counter(_day_key(STATS_KEY), "payments_failed")
        logger.warning(
            "Payment failed for order %s: %s",
            event.payload.get("order_number"),
            event.payload.get("reason"),
        )

    async def on_inventory_low(self, event: Event) -> None:
        logger.warning(
            "Low stock: sku=%s available=%s threshold=%s",
            event.payload.get("sku"),
            event.payload.get("available"),
            event.payload.get("threshold"),
        )

    async def on_newsletter_subscribed(self, event: Event) -> None:
        await self._bus.bump_counter(_day_key(STATS_KEY), "newsletter_signups")

    async def on_review_submitted(self, event: Event) -> None:
        await self._cache.bump()
        await self._bus.bump_counter(_day_key(STATS_KEY), "reviews_submitted")
