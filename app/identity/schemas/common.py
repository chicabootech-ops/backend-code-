"""Shared Pydantic schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class MessageResponse(BaseModel):
    message: str


class ErrorResponse(BaseModel):
    error: str
    code: str
    detail: str | None = None


class ClientContext(BaseModel):
    ip_address: str | None = None
    user_agent: str | None = None
    request_id: str | None = None
    device_name: str | None = None
    device_type: str = "unknown"
