"""Shared test fixtures."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.dependencies import get_current_user_id, get_db
from app.main import app


@pytest.fixture
def sample_user_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def sample_user(sample_user_id: uuid.UUID):
    profile = SimpleNamespace(
        first_name="Jane",
        last_name="Doe",
        gender="female",
        date_of_birth=date(1995, 5, 15),
        avatar_url=None,
        metadata_={"referral_code": "REF123", "onboarding": {}},
        updated_at=datetime.now(UTC),
    )
    preferences = SimpleNamespace(
        id=uuid.uuid4(),
        email_marketing=False,
        sms_marketing=False,
        preferred_language="en",
        currency="INR",
        push_notifications=True,
        analytics_tracking=True,
        order_updates_email=True,
        order_updates_sms=False,
        updated_at=datetime.now(UTC),
    )
    return SimpleNamespace(
        id=sample_user_id,
        email="jane@example.com",
        customer_number=10001,
        email_verified=True,
        phone="9876543210",
        phone_verified=False,
        status="active",
        last_login_at=None,
        created_at=datetime.now(UTC),
        profile=profile,
        preferences=preferences,
    )


@pytest.fixture
async def authenticated_client(sample_user_id: uuid.UUID):
    async def override_user_id():
        return sample_user_id

    async def override_db():
        session = AsyncMock()
        session.commit = AsyncMock()
        session.rollback = AsyncMock()
        session.refresh = AsyncMock()
        session.flush = AsyncMock()
        yield session

    app.dependency_overrides[get_current_user_id] = override_user_id
    app.dependency_overrides[get_db] = override_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()
