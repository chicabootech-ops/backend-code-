from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class UserStatusUpdate(BaseModel):
    status: str = Field(pattern="^(active|suspended|blocked)$")
    status_reason: str | None = Field(default=None, max_length=500)


class AdminUserOut(BaseModel):
    id: UUID
    email: str
    phone: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    status: str
    status_reason: str | None = None
    email_verified: bool
    customer_number: int
    last_login_at: datetime | None = None
    created_at: datetime
    order_count: int = 0


class AdminAddressOut(BaseModel):
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
    created_at: datetime | None = None


class AdminPreferencesOut(BaseModel):
    email_marketing: bool
    sms_marketing: bool
    preferred_language: str
    currency: str
    push_notifications: bool
    analytics_tracking: bool
    order_updates_email: bool
    order_updates_sms: bool
    updated_at: datetime | None = None


class AdminProfileOut(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    gender: str | None = None
    date_of_birth: date | None = None
    avatar_key: str | None = None
    avatar_url: str | None = None
    loyalty_points: int = 0
    onboarding: dict | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AdminUserOrderOut(BaseModel):
    id: UUID
    order_number: int
    status: str
    payment_status: str
    fulfillment_status: str
    grand_total_paise: int
    created_at: datetime


class AdminUserDetailOut(BaseModel):
    id: UUID
    email: str
    phone: str | None = None
    status: str
    status_reason: str | None = None
    email_verified: bool
    phone_verified: bool
    customer_number: int
    failed_login_attempts: int
    locked_until: datetime | None = None
    last_login_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None
    order_count: int = 0
    profile: AdminProfileOut | None = None
    preferences: AdminPreferencesOut | None = None
    addresses: list[AdminAddressOut] = Field(default_factory=list)
    recent_orders: list[AdminUserOrderOut] = Field(default_factory=list)


class UserListResponse(BaseModel):
    items: list[AdminUserOut]
    meta: dict
