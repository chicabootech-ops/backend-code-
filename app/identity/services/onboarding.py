"""Onboarding progress computation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.identity.models import User, UserAddress


def _metadata_onboarding(profile_metadata: dict[str, Any]) -> dict[str, Any]:
    onboarding = profile_metadata.get("onboarding")
    if isinstance(onboarding, dict):
        return onboarding
    return {}


def compute_onboarding_state(
    user: User,
    *,
    address_count: int,
) -> dict[str, Any]:
    profile = user.profile
    first_name = profile.first_name if profile else None
    last_name = profile.last_name if profile else None
    metadata = profile.metadata_ if profile else {}

    profile_complete = bool(first_name and last_name)
    email_verified = user.email_verified and user.status == "active"
    has_address = address_count > 0

    onboarding_meta = _metadata_onboarding(metadata)
    preferences_reviewed = onboarding_meta.get("preferences_reviewed_at") is not None

    shopping_ready = email_verified and profile_complete and has_address

    return {
        "email_verified": email_verified,
        "profile_complete": profile_complete,
        "has_address": has_address,
        "preferences_reviewed": preferences_reviewed,
        "shopping_ready": shopping_ready,
        "profile_completed_at": onboarding_meta.get("profile_completed_at"),
        "address_added_at": onboarding_meta.get("address_added_at"),
        "preferences_reviewed_at": onboarding_meta.get("preferences_reviewed_at"),
        "shopping_ready_at": onboarding_meta.get("shopping_ready_at"),
    }


def touch_onboarding_metadata(
    profile_metadata: dict[str, Any],
    *,
    profile_complete: bool,
    has_address: bool,
    preferences_reviewed: bool,
    shopping_ready: bool,
) -> dict[str, Any]:
    metadata = dict(profile_metadata)
    onboarding = dict(_metadata_onboarding(metadata))
    now = datetime.now(UTC).isoformat()

    if profile_complete and not onboarding.get("profile_completed_at"):
        onboarding["profile_completed_at"] = now
    if has_address and not onboarding.get("address_added_at"):
        onboarding["address_added_at"] = now
    if preferences_reviewed and not onboarding.get("preferences_reviewed_at"):
        onboarding["preferences_reviewed_at"] = now
    if shopping_ready and not onboarding.get("shopping_ready_at"):
        onboarding["shopping_ready_at"] = now

    metadata["onboarding"] = onboarding
    return metadata
