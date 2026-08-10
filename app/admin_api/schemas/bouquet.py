from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

OptionKind = Literal["flower", "color", "wrap"]

#: Matches the DB CHECK constraint so a bad swatch is rejected before the insert.
HEX_PATTERN = r"^#[0-9a-fA-F]{6}$"


class BouquetOptionCreate(BaseModel):
    kind: OptionKind
    name: str = Field(min_length=1, max_length=80)
    slug: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=300)
    hex_code: str | None = Field(default=None, pattern=HEX_PATTERN)
    image_r2_key: str | None = None
    price_delta_paise: int = Field(default=0, ge=0)
    status: Literal["active", "inactive"] = "active"
    sort_order: int = Field(default=0, ge=0)


class BouquetOptionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=300)
    hex_code: str | None = Field(default=None, pattern=HEX_PATTERN)
    image_r2_key: str | None = None
    price_delta_paise: int | None = Field(default=None, ge=0)
    status: Literal["active", "inactive"] | None = None
    sort_order: int | None = Field(default=None, ge=0)


class BouquetOptionOut(BaseModel):
    id: UUID
    kind: str
    name: str
    slug: str
    description: str | None = None
    hex_code: str | None = None
    image_r2_key: str | None = None
    image_url: str | None = None
    price_delta_paise: int
    status: str
    sort_order: int
    created_at: datetime
    updated_at: datetime


class BouquetOptionListResponse(BaseModel):
    items: list[BouquetOptionOut] = Field(default_factory=list)
    total: int = 0
