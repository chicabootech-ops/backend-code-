from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class CampaignOut(BaseModel):
    id: UUID
    name: str
    subject: str | None = None
    body_html: str | None = None
    status: str
    channel: str
    total_recipients: int
    sent_count: int
    failed_count: int
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime


class CampaignSendRequest(BaseModel):
    name: str = Field(default="", max_length=200)
    subject: str = Field(min_length=1, max_length=200)
    body_html: str = Field(min_length=1, max_length=100_000)


class CampaignTestRequest(CampaignSendRequest):
    to_email: str = Field(min_length=3, max_length=320)


class CampaignSendResult(BaseModel):
    campaign_id: UUID
    total_recipients: int
    sent: int
    failed: int


class AudienceOut(BaseModel):
    #: Confirmed subscribers only — the number that will actually be mailed.
    confirmed: int
