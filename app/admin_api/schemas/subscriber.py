from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class SubscriberOut(BaseModel):
    id: UUID
    email: str
    #: pending | confirmed | unsubscribed
    status: str
    confirmed_at: datetime | None = None
    unsubscribed_at: datetime | None = None
    created_at: datetime


class SubscriberStats(BaseModel):
    total: int
    #: The only figure that matters before a send — the mailable audience.
    confirmed: int
    pending: int
    unsubscribed: int
    new_30d: int


class SubscriberListResponse(BaseModel):
    items: list[SubscriberOut] = Field(default_factory=list)
    meta: dict
