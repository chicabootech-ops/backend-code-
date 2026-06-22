"""User profile request/response schemas."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.validation import validate_date_of_birth, validate_gender, validate_phone
from app.schemas.preferences import PreferencesResponse


class UserProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    first_name: str | None = None
    last_name: str | None = None
    gender: str | None = None
    date_of_birth: date | None = None
    avatar_url: str | None = None
    referral_code: str | None = None


class OnboardingFlagsResponse(BaseModel):
    email_verified: bool
    profile_complete: bool
    has_address: bool
    preferences_reviewed: bool
    shopping_ready: bool


class ProfileUpdateRequest(BaseModel):
    first_name: str | None = Field(None, min_length=1, max_length=100)
    last_name: str | None = Field(None, min_length=1, max_length=100)
    phone: str | None = Field(None, max_length=20)
    gender: str | None = None
    date_of_birth: date | None = None
    referral_code: str | None = Field(None, max_length=50)

    @field_validator("phone")
    @classmethod
    def check_phone(cls, v: str | None) -> str | None:
        if v is None or v.strip() == "":
            return None
        return validate_phone(v)

    @field_validator("gender")
    @classmethod
    def check_gender(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return validate_gender(v)

    @field_validator("date_of_birth")
    @classmethod
    def check_dob(cls, v: date | None) -> date | None:
        if v is None:
            return None
        return validate_date_of_birth(v)


class CurrentUserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    customer_number: int
    email_verified: bool
    phone: str | None = None
    phone_verified: bool
    status: str
    last_login_at: datetime | None = None
    created_at: datetime
    profile: UserProfileResponse | None = None
    preferences: PreferencesResponse | None = None
    onboarding: OnboardingFlagsResponse | None = None

    @property
    def full_name(self) -> str | None:
        if self.profile and (self.profile.first_name or self.profile.last_name):
            parts = [self.profile.first_name or "", self.profile.last_name or ""]
            return " ".join(p for p in parts if p).strip() or None
        return None
