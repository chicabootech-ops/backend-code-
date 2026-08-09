"""Domain event names and the envelope that travels on the stream."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class EventType(StrEnum):
    # Catalog — anything that changes what the storefront should render.
    CATALOG_CHANGED = "catalog.changed"
    PRODUCT_VIEWED = "product.viewed"

    # Checkout / orders
    CART_ITEM_ADDED = "cart.item_added"
    ORDER_CREATED = "order.created"
    ORDER_CANCELLED = "order.cancelled"
    ORDER_STATUS_CHANGED = "order.status_changed"

    # Payments
    PAYMENT_CAPTURED = "payment.captured"
    PAYMENT_FAILED = "payment.failed"
    REFUND_INITIATED = "refund.initiated"

    # Inventory
    INVENTORY_LOW = "inventory.low"
    INVENTORY_OUT = "inventory.out"

    # Lifecycle / marketing
    NEWSLETTER_SUBSCRIBED = "newsletter.subscribed"
    REVIEW_SUBMITTED = "review.submitted"


@dataclass(slots=True)
class Event:
    """One published event. `payload` must be JSON-serialisable."""

    type: EventType | str
    payload: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    #: Stream entry id, set by the consumer — never by the publisher.
    stream_id: str | None = None

    def to_wire(self) -> dict[str, str]:
        import json

        return {
            "id": self.id,
            "type": str(self.type),
            "occurred_at": self.occurred_at.isoformat(),
            "payload": json.dumps(self.payload, default=str),
        }

    @classmethod
    def from_wire(cls, fields: dict[bytes | str, bytes | str], stream_id: str) -> "Event":
        import json

        def get(key: str) -> str:
            value = fields.get(key) or fields.get(key.encode())
            if isinstance(value, bytes):
                return value.decode()
            return value or ""

        try:
            payload = json.loads(get("payload") or "{}")
        except ValueError:
            payload = {}
        try:
            occurred_at = datetime.fromisoformat(get("occurred_at"))
        except ValueError:
            occurred_at = datetime.now(UTC)

        return cls(
            type=get("type"),
            payload=payload if isinstance(payload, dict) else {},
            id=get("id") or str(uuid.uuid4()),
            occurred_at=occurred_at,
            stream_id=stream_id,
        )
