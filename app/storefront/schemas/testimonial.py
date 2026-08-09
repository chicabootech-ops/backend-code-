from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class StorefrontTestimonialOut(BaseModel):
    id: UUID
    author_name: str
    author_role: str | None = None
    avatar_url: str | None = None
    quote: str
    rating: int | None = None
    is_featured: bool = False
    product_slug: str | None = None
    product_name: str | None = None


class StorefrontTestimonialListResponse(BaseModel):
    items: list[StorefrontTestimonialOut] = Field(default_factory=list)
    total: int = 0
    average_rating: float | None = None
