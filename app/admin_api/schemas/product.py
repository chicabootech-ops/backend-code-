from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ProductVariantIn(BaseModel):
    sku: str | None = None
    title: str = "Default"
    option_values: dict[str, Any] = Field(default_factory=dict)
    price_paise: int = Field(ge=0)
    compare_at_price_paise: int | None = None
    status: str = "active"


class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    slug: str | None = None
    primary_category_id: UUID
    description: str | None = None
    short_description: str | None = None
    brand: str | None = None
    status: str = "draft"
    is_featured: bool = False
    image_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    variant: ProductVariantIn | None = None


class ProductUpdate(BaseModel):
    name: str | None = None
    slug: str | None = None
    primary_category_id: UUID | None = None
    description: str | None = None
    short_description: str | None = None
    brand: str | None = None
    status: str | None = None
    is_featured: bool | None = None
    image_url: str | None = None
    metadata: dict[str, Any] | None = None
    variant: ProductVariantIn | None = None


class ProductVariantOut(BaseModel):
    id: UUID
    sku: str
    title: str
    option_values: dict[str, Any]
    price_paise: int
    compare_at_price_paise: int | None = None
    status: str


class ProductOut(BaseModel):
    id: UUID
    name: str
    slug: str
    primary_category_id: UUID
    description: str | None = None
    short_description: str | None = None
    brand: str | None = None
    status: str
    is_featured: bool
    image_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    variants: list[ProductVariantOut] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
