"""Cart request/response schemas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class CartItemAdd(BaseModel):
    variant_id: UUID | None = None
    product_id: UUID | None = None
    slug: str | None = None
    quantity: int = Field(default=1, ge=1, le=100)


class CartItemUpdate(BaseModel):
    quantity: int = Field(ge=1, le=100)


class CartApplyCoupon(BaseModel):
    code: str = Field(min_length=1, max_length=64)


class CartItemOut(BaseModel):
    id: UUID
    product_id: UUID
    variant_id: UUID
    product_name: str
    variant_title: str
    slug: str
    sku: str
    quantity: int
    unit_price_paise: int
    line_total_paise: int
    image_url: str | None = None


class CartOut(BaseModel):
    id: UUID
    status: str
    currency: str
    subtotal_paise: int
    discount_paise: int
    coupon_code: str | None = None
    item_count: int
    items: list[CartItemOut]
    updated_at: datetime | None = None


class WishlistAdd(BaseModel):
    product_id: UUID | None = None
    slug: str | None = None
    variant_id: UUID | None = None


class WishlistItemOut(BaseModel):
    id: UUID
    product_id: UUID
    variant_id: UUID | None
    product_name: str
    slug: str
    price_paise: int
    image_url: str | None = None
    created_at: datetime


class WishlistOut(BaseModel):
    items: list[WishlistItemOut]
    total: int
