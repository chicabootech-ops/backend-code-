"""Onboarding state computation tests."""

from datetime import date
from types import SimpleNamespace

from app.services.onboarding import compute_onboarding_state, touch_onboarding_metadata


def _user(**kwargs):
    defaults = {
        "email_verified": True,
        "status": "active",
        "profile": SimpleNamespace(
            first_name="A",
            last_name="B",
            metadata_={},
        ),
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_shopping_ready_requires_email_profile_address():
    user = _user()
    state = compute_onboarding_state(user, address_count=0)
    assert state["profile_complete"] is True
    assert state["has_address"] is False
    assert state["shopping_ready"] is False

    state = compute_onboarding_state(user, address_count=1)
    assert state["shopping_ready"] is True


def test_email_verified_requires_active_status():
    user = _user(email_verified=True, status="pending_verification")
    state = compute_onboarding_state(user, address_count=1)
    assert state["email_verified"] is False
    assert state["shopping_ready"] is False


def test_touch_onboarding_metadata_sets_timestamps():
    metadata = touch_onboarding_metadata(
        {},
        profile_complete=True,
        has_address=True,
        preferences_reviewed=True,
        shopping_ready=True,
    )
    onboarding = metadata["onboarding"]
    assert onboarding["profile_completed_at"]
    assert onboarding["address_added_at"]
    assert onboarding["preferences_reviewed_at"]
    assert onboarding["shopping_ready_at"]
