from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class StorefrontProductOut(BaseModel):
    id: UUID
    name: str
    slug: str
    short_description: str | None = None
    description: str | None = None
    image_url: str | None = None
    price_paise: int = 0
    compare_at_price_paise: int | None = None
    primary_category_id: UUID
    category_slug: str | None = None
    category_name: str | None = None


class StorefrontProductDetailOut(StorefrontProductOut):
    brand: str | None = None
    is_featured: bool = False


class StorefrontProductListResponse(BaseModel):
    items: list[StorefrontProductOut] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 24
