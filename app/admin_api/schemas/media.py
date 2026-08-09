from __future__ import annotations

from pydantic import BaseModel, Field


class MediaUploadOut(BaseModel):
    """One uploaded object. `key` is what gets persisted, `url` is for previewing."""

    key: str
    url: str | None = None
    content_type: str
    byte_size: int
    width: int | None = None
    height: int | None = None


class MediaUploadListOut(BaseModel):
    items: list[MediaUploadOut] = Field(default_factory=list)
