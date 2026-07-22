"""Preferences request/response schemas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.identity.core.validation import validate_currency, validate_language


class PreferencesUpdateRequest(BaseModel):
    email_marketing: bool | None = None
    sms_marketing: bool | None = None
    preferred_language: str | None = None
    currency: str | None = None
    push_notifications: bool | None = None
    analytics_tracking: bool | None = None
    order_updates_email: bool | None = None
    order_updates_sms: bool | None = None

    @field_validator("preferred_language")
    @classmethod
    def check_language(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return validate_language(v)

    @field_validator("currency")
    @classmethod
    def check_currency(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return validate_currency(v)


class PreferencesResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email_marketing: bool
    sms_marketing: bool
    preferred_language: str
    currency: str
    push_notifications: bool
    analytics_tracking: bool
    order_updates_email: bool
    order_updates_sms: bool
    updated_at: datetime
