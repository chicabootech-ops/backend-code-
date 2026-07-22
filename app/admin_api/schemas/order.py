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


class OrderListResponse(BaseModel):
    items: list[AdminOrderOut]
    meta: dict
