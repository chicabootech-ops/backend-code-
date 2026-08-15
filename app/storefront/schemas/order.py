from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.storefront.schemas.bouquet import BouquetConfigIn


class CheckoutItemIn(BaseModel):
    """A line the customer wants to buy. Identify by variant, product, or slug."""

    variant_id: UUID | None = None
    product_id: UUID | None = None
    slug: str | None = None
    quantity: int = Field(default=1, ge=1, le=100)
    #: Set for a made-to-order bouquet. The server resolves the base product and
    #: prices the configuration itself — no price ever comes from the client.
    custom_bouquet: BouquetConfigIn | None = None

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
    coupon_code: str | None = Field(default=None, max_length=64)
    email: str | None = None  # for guest checkout
    # Client-generated key so a double-submit / retry / refresh reuses the same
    # pending order + Razorpay order instead of creating duplicates.
    idempotency_key: str | None = Field(default=None, max_length=100)


class OrderItemOut(BaseModel):
    product_id: UUID
    product_name: str
    variant_title: str
    sku: str
    quantity: int
    unit_price_paise: int
    line_total_paise: int
    hsn_code: str | None = None
    tax_rate_bps: int | None = None
    #: Resolved from the product at read time, not snapshotted at checkout.
    #: Snapshotting would be more historically faithful, but every order placed
    #: before this field existed would show nothing. None when the product was
    #: deleted or never had an image.
    image_url: str | None = None
    #: Present for made-to-order bouquets, whose configuration is snapshotted
    #: onto the line rather than living on a product row.
    slug: str | None = None


class OrderStatusEventOut(BaseModel):
    """One step on the order timeline.

    Read from commerce.order_status_history, which has always been written on
    every transition but was never exposed — the UI could show a current status
    and nothing about how the order got there.
    """

    from_status: str | None = None
    to_status: str
    changed_by_type: str
    reason: str | None = None
    created_at: datetime


class OrderItemPreviewOut(BaseModel):
    """Just enough of a line to render a list row: a thumbnail and a name."""

    product_name: str
    image_url: str | None = None
    quantity: int


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
    status_history: list[OrderStatusEventOut] = Field(default_factory=list)


class OrderListItemOut(BaseModel):
    id: UUID
    order_number: int
    status: str
    payment_status: str
    grand_total_paise: int
    item_count: int
    created_at: datetime
    items_preview: list[OrderItemPreviewOut] = Field(default_factory=list)


class OrderListResponse(BaseModel):
    items: list[OrderListItemOut]
    total: int
    page: int
    page_size: int


class CancelOrderRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)
