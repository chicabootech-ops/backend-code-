"""Security center schemas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class DeviceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    device_name: str | None = None
    device_type: str
    ip_address: str | None = None
    user_agent: str | None = None
    last_seen_at: datetime
    created_at: datetime
    is_current: bool = False


class DeviceListResponse(BaseModel):
    items: list[DeviceResponse]
    total: int


class LoginHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    success: bool
    failure_reason: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    device_id: UUID | None = None
    created_at: datetime


class LoginHistoryListResponse(BaseModel):
    items: list[LoginHistoryResponse]
    total: int
