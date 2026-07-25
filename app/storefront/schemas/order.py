from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class CheckoutItemIn(BaseModel):
    """A line the customer wants to buy. Identify by variant, product, or slug."""

    variant_id: UUID | None = None
    product_id: UUID | None = None
    slug: str | None = None
    quantity: int = Field(default=1, ge=1, le=100)

    @field_validator("quantity")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("quantity must be at least 1")
        return v


class CheckoutAddressIn(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    phone: str | None = Field(default=None, max_length=20)
    line1: str = Field(min_length=1, max_length=300)
    line2: str | None = Field(default=None, max_length=300)
    landmark: str | None = Field(default=None, max_length=200)
    city: str = Field(min_length=1, max_length=120)
    state: str = Field(min_length=1, max_length=120)
    postal_code: str = Field(min_length=3, max_length=12)
    country: str = Field(default="IN", max_length=2)
    state_code: str | None = Field(default=None, max_length=2)

    def as_snapshot(self) -> dict[str, Any]:
        return self.model_dump()


class CheckoutRequest(BaseModel):
    items: list[CheckoutItemIn] = Field(min_length=1)
    address_id: UUID | None = None
    shipping_address: CheckoutAddressIn | None = None
    billing_address: CheckoutAddressIn | None = None
    customer_note: str | None = Field(default=None, max_length=1000)
    gstin: str | None = Field(default=None, max_length=20)
    email: str | None = None  # for guest checkout
    # Client-generated key so a double-submit / retry / refresh reuses the same
    # pending order + Razorpay order instead of creating duplicates.
    idempotency_key: str | None = Field(default=None, max_length=100)


class OrderItemOut(BaseModel):
    product_name: str
    variant_title: str
    sku: str
    quantity: int
    unit_price_paise: int
    line_total_paise: int
    hsn_code: str | None = None
    tax_rate_bps: int | None = None


class OrderInvoiceOut(BaseModel):
    invoice_number: int
    has_pdf: bool
    issued_at: datetime | None = None


class OrderOut(BaseModel):
    id: UUID
    order_number: int
    status: str
    payment_status: str
    fulfillment_status: str
    currency: str
    subtotal_paise: int
    discount_paise: int
    tax_paise: int
    shipping_paise: int
    grand_total_paise: int
    shipping_address: dict[str, Any] = Field(default_factory=dict)
    billing_address: dict[str, Any] = Field(default_factory=dict)
    customer_note: str | None = None
    created_at: datetime
    items: list[OrderItemOut] = Field(default_factory=list)
    invoice: OrderInvoiceOut | None = None


class OrderListItemOut(BaseModel):
    id: UUID
    order_number: int
    status: str
    payment_status: str
    grand_total_paise: int
    item_count: int
    created_at: datetime


class OrderListResponse(BaseModel):
    items: list[OrderListItemOut]
    total: int
    page: int
    page_size: int


class CancelOrderRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)
