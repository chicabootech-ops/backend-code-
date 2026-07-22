from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.product import StorefrontProductOut


class StorefrontCategoryOut(BaseModel):
    id: UUID
    name: str
    slug: str
    description: str | None = None
    image_url: str | None = None
    sort_order: int = 0
    kind: str | None = None


class StorefrontCategoryParentOut(BaseModel):
    name: str
    slug: str
    kind: str | None = None


class StorefrontCategoryDetailOut(StorefrontCategoryOut):
    parent: StorefrontCategoryParentOut | None = None
    children: list[StorefrontCategoryOut] = Field(default_factory=list)
    products: list[StorefrontProductOut] = Field(default_factory=list)
    products_total: int = 0
    products_page: int = 1
    products_page_size: int = 24


class StorefrontCategoryListResponse(BaseModel):
    items: list[StorefrontCategoryOut] = Field(default_factory=list)
