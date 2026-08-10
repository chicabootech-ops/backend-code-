from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

#: Guard rails on a single custom bouquet. Kept here so the API, the quote
#: endpoint and checkout all enforce the same limits.
MIN_STEMS = 3
MAX_STEMS = 60
MAX_STEM_GROUPS = 6


class BouquetOptionOut(BaseModel):
    id: UUID
    kind: str
    name: str
    slug: str
    description: str | None = None
    hex_code: str | None = None
    image_url: str | None = None
    price_delta_paise: int = 0


class BouquetOptionsResponse(BaseModel):
    """Everything the builder UI needs to render and explain its pricing."""

    flowers: list[BouquetOptionOut] = Field(default_factory=list)
    colors: list[BouquetOptionOut] = Field(default_factory=list)
    wraps: list[BouquetOptionOut] = Field(default_factory=list)
    base_price_paise: int = 0
    price_per_stem_paise: int = 0
    min_stems: int = MIN_STEMS
    max_stems: int = MAX_STEMS
    max_stem_groups: int = MAX_STEM_GROUPS
    #: False when no base product is flagged — the UI hides the builder entirely.
    available: bool = False


class BouquetStemIn(BaseModel):
    flower_id: UUID
    color_id: UUID
    quantity: int = Field(ge=1, le=MAX_STEMS)


class BouquetConfigIn(BaseModel):
    stems: list[BouquetStemIn] = Field(min_length=1, max_length=MAX_STEM_GROUPS)
    wrap_id: UUID | None = None
    note: str | None = Field(default=None, max_length=300)


class BouquetStemOut(BaseModel):
    flower_name: str
    color_name: str
    color_hex: str | None = None
    quantity: int
    unit_price_paise: int
    line_total_paise: int


class BouquetQuoteOut(BaseModel):
    """Server-computed price. The browser never adds up money itself."""

    stems: list[BouquetStemOut] = Field(default_factory=list)
    total_stems: int = 0
    base_price_paise: int = 0
    stems_price_paise: int = 0
    wrap_name: str | None = None
    wrap_price_paise: int = 0
    total_paise: int = 0
    summary: str = ""
