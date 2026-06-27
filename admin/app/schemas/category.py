from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str | None = None
    parent_id: UUID | None = None
    description: str | None = None
    image_r2_key: str | None = None
    sort_order: int = 0
    status: str = "active"
    metadata: dict[str, Any] = Field(default_factory=dict)


class CategoryUpdate(BaseModel):
    name: str | None = None
    slug: str | None = None
    parent_id: UUID | None = None
    description: str | None = None
    image_r2_key: str | None = None
    sort_order: int | None = None
    status: str | None = None
    metadata: dict[str, Any] | None = None


class CategoryOut(BaseModel):
    id: UUID
    parent_id: UUID | None
    name: str
    slug: str
    description: str | None = None
    image_r2_key: str | None = None
    sort_order: int
    status: str
    path: str | None = None
    depth: int
    metadata: dict[str, Any] = Field(default_factory=dict)
    children: list["CategoryOut"] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


CategoryOut.model_rebuild()
