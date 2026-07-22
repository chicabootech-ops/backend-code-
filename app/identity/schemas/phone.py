"""Phone verification request schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.identity.core.validation import validate_phone


class SendPhoneOtpRequest(BaseModel):
    phone: str | None = Field(default=None, max_length=20)

    @field_validator("phone")
    @classmethod
    def check_phone(cls, v: str | None) -> str | None:
        if v is None or not str(v).strip():
            return None
        return validate_phone(v)


class VerifyPhoneOtpRequest(BaseModel):
    otp: str = Field(min_length=4, max_length=8, pattern=r"^\d+$")
