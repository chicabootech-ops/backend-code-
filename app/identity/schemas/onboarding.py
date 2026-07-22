"""Onboarding response schemas."""

from __future__ import annotations

from pydantic import BaseModel


class OnboardingStep(BaseModel):
    key: str
    label: str
    completed: bool
    required: bool = True


class OnboardingResponse(BaseModel):
    email_verified: bool
    profile_complete: bool
    has_address: bool
    preferences_reviewed: bool
    shopping_ready: bool
    profile_completed_at: str | None = None
    address_added_at: str | None = None
    preferences_reviewed_at: str | None = None
    shopping_ready_at: str | None = None
    steps: list[OnboardingStep]
    completion_percent: int
