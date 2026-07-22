"""Address request/response schemas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.identity.core.validation import validate_address_type, validate_phone, validate_pincode


class AddressCreateRequest(BaseModel):
    label: str | None = Field(None, max_length=100)
    full_name: str = Field(..., min_length=1, max_length=200)
    phone: str | None = Field(None, max_length=20)
    line1: str = Field(..., min_length=1, max_length=300)
    line2: str | None = Field(None, max_length=300)
    landmark: str | None = Field(None, max_length=200)
    city: str = Field(..., min_length=1, max_length=100)
    state: str = Field(..., min_length=1, max_length=100)
    postal_code: str = Field(..., min_length=6, max_length=6)
    country: str = Field(default="IN", max_length=2)
    address_type: str = Field(default="shipping")
    custom_label: str | None = Field(None, max_length=100)
    is_default: bool = False

    @field_validator("phone")
    @classmethod
    def check_phone(cls, v: str | None) -> str | None:
        if v is None or v.strip() == "":
            return None
        return validate_phone(v)

    @field_validator("postal_code")
    @classmethod
    def check_pincode(cls, v: str) -> str:
        return validate_pincode(v)

    @field_validator("address_type")
    @classmethod
    def check_address_type(cls, v: str) -> str:
        return validate_address_type(v)

    @field_validator("country")
    @classmethod
    def check_country(cls, v: str) -> str:
        if v.upper() != "IN":
            raise ValueError("Only IN country is supported")
        return "IN"


class AddressUpdateRequest(BaseModel):
    label: str | None = Field(None, max_length=100)
    full_name: str | None = Field(None, min_length=1, max_length=200)
    phone: str | None = Field(None, max_length=20)
    line1: str | None = Field(None, min_length=1, max_length=300)
    line2: str | None = Field(None, max_length=300)
    landmark: str | None = Field(None, max_length=200)
    city: str | None = Field(None, min_length=1, max_length=100)
    state: str | None = Field(None, min_length=1, max_length=100)
    postal_code: str | None = Field(None, min_length=6, max_length=6)
    country: str | None = Field(None, max_length=2)
    address_type: str | None = None
    custom_label: str | None = Field(None, max_length=100)
    is_default: bool | None = None

    @field_validator("phone")
    @classmethod
    def check_phone(cls, v: str | None) -> str | None:
        if v is None or v.strip() == "":
            return None
        return validate_phone(v)

    @field_validator("postal_code")
    @classmethod
    def check_pincode(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return validate_pincode(v)

    @field_validator("address_type")
    @classmethod
    def check_address_type(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return validate_address_type(v)

    @field_validator("country")
    @classmethod
    def check_country(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if v.upper() != "IN":
            raise ValueError("Only IN country is supported")
        return "IN"


class AddressResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    label: str | None = None
    full_name: str
    phone: str | None = None
    line1: str
    line2: str | None = None
    landmark: str | None = None
    city: str
    state: str
    postal_code: str
    country: str
    is_default: bool
    address_type: str
    custom_label: str | None = None
    created_at: datetime
    updated_at: datetime


class AddressListResponse(BaseModel):
    items: list[AddressResponse]
    total: int
