from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

from app.storefront.schemas.category import StorefrontCategoryOut
from app.storefront.schemas.product import StorefrontProductOut


class StorefrontSectionOut(BaseModel):
    id: UUID
    name: str
    slug: str
    description: str | None = None
    image_url: str | None = None
    sort_order: int = 0
    products: list[StorefrontProductOut] = Field(default_factory=list)
    categories: list[StorefrontCategoryOut] = Field(default_factory=list)


class StorefrontSectionListResponse(BaseModel):
    items: list[StorefrontSectionOut] = Field(default_factory=list)


class StorefrontSectionDetailOut(StorefrontSectionOut):
    pass
