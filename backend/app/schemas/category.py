from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class StorefrontCategoryOut(BaseModel):
    id: UUID
    name: str
    slug: str
    description: str | None = None
    image_url: str | None = None
    sort_order: int = 0


class StorefrontCategoryParentOut(BaseModel):
    name: str
    slug: str


class StorefrontCategoryDetailOut(StorefrontCategoryOut):
    parent: StorefrontCategoryParentOut | None = None
    children: list[StorefrontCategoryOut] = Field(default_factory=list)


class StorefrontCategoryListResponse(BaseModel):
    items: list[StorefrontCategoryOut] = Field(default_factory=list)
