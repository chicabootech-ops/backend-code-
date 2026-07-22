"""Avatar upload / R2 schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

ALLOWED_AVATAR_CONTENT_TYPES = frozenset({"image/webp", "image/jpeg", "image/png"})
MAX_AVATAR_BYTES = 5 * 1024 * 1024


class AvatarUploadUrlRequest(BaseModel):
    content_type: str = Field(..., description="MIME type of the image to upload")
    content_length: int = Field(..., gt=0, le=MAX_AVATAR_BYTES)

    @field_validator("content_type")
    @classmethod
    def validate_content_type(cls, v: str) -> str:
        normalized = v.strip().lower()
        if normalized not in ALLOWED_AVATAR_CONTENT_TYPES:
            raise ValueError("content_type must be image/webp, image/jpeg, or image/png")
        return normalized


class AvatarUploadUrlResponse(BaseModel):
    upload_url: str
    key: str
    expires_at: datetime
    max_size_bytes: int = MAX_AVATAR_BYTES


class AvatarConfirmRequest(BaseModel):
    content_length: int = Field(..., gt=0, le=MAX_AVATAR_BYTES)


class AvatarConfirmResponse(BaseModel):
    key: str
    message: str = "Avatar confirmed"


class AvatarUrlResponse(BaseModel):
    url: str
    key: str | None = None
    expires_at: datetime
