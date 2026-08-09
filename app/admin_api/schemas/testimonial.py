from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class TestimonialCreate(BaseModel):
    author_name: str = Field(min_length=1, max_length=120)
    author_role: str | None = Field(default=None, max_length=160)
    avatar_r2_key: str | None = None
    quote: str = Field(min_length=1, max_length=2000)
    rating: int | None = Field(default=5, ge=1, le=5)
    product_id: UUID | None = None
    is_featured: bool = False
    status: Literal["published", "hidden"] = "published"
    sort_order: int = Field(default=0, ge=0)


class TestimonialUpdate(BaseModel):
    author_name: str | None = Field(default=None, min_length=1, max_length=120)
    author_role: str | None = Field(default=None, max_length=160)
    avatar_r2_key: str | None = None
    quote: str | None = Field(default=None, min_length=1, max_length=2000)
    rating: int | None = Field(default=None, ge=1, le=5)
    product_id: UUID | None = None
    is_featured: bool | None = None
    status: Literal["published", "hidden"] | None = None
    sort_order: int | None = Field(default=None, ge=0)


class TestimonialOut(BaseModel):
    id: UUID
    author_name: str
    author_role: str | None = None
    avatar_r2_key: str | None = None
    avatar_url: str | None = None
    quote: str
    rating: int | None = None
    product_id: UUID | None = None
    product_name: str | None = None
    is_featured: bool
    status: str
    sort_order: int
    created_at: datetime
    updated_at: datetime


class TestimonialListResponse(BaseModel):
    items: list[TestimonialOut] = Field(default_factory=list)
    total: int = 0
