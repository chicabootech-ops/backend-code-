from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class OrderStatusUpdate(BaseModel):
    status: str
    note: str | None = None


class OrderTrackingEvent(BaseModel):
    status: str
    note: str | None = None
    created_at: datetime


class AdminOrderItemOut(BaseModel):
    """A line as the admin needs to see it: what was bought, and what it looked like."""

    product_id: UUID
    product_name: str
    variant_title: str
    sku: str
    quantity: int
    unit_price_paise: int
    line_total_paise: int
    #: Resolved from the product at read time. None when the product was
    #: deleted or never had an image — order history must survive both.
    image_url: str | None = None


class AdminOrderOut(BaseModel):
    id: UUID
    order_number: int
    user_id: UUID | None = None
    guest_email: str | None = None
    status: str
    payment_status: str
    fulfillment_status: str
    grand_total_paise: int
    shipping_address: dict[str, Any] = Field(default_factory=dict)
    admin_note: str | None = None
    created_at: datetime
    updated_at: datetime
    tracking: list[OrderTrackingEvent] = Field(default_factory=list)
    items: list[AdminOrderItemOut] = Field(default_factory=list)
    item_count: int = 0


class OrderListResponse(BaseModel):
    items: list[AdminOrderOut]
    meta: dict
