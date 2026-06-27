from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class AdminLoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)


class AdminLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    admin: "AdminProfile"


class AdminProfile(BaseModel):
    id: UUID
    email: str
    full_name: str
    role: str


AdminLoginResponse.model_rebuild()
