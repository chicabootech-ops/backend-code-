"""API route smoke tests with dependency overrides."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.dependencies import get_current_user_id, get_db
from app.main import app
from app.services.account_service import AccountService
from app.schemas.address import AddressListResponse, AddressResponse
from app.schemas.onboarding import OnboardingResponse, OnboardingStep
from app.schemas.preferences import PreferencesResponse
from app.schemas.security import DeviceListResponse, LoginHistoryListResponse
from app.schemas.user import CurrentUserResponse, OnboardingFlagsResponse, UserProfileResponse


@pytest.fixture
def user_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
async def client(user_id: uuid.UUID):
    app.state.account_service = AccountService()

    async def override_user_id():
        return user_id

    async def override_db():
        session = AsyncMock()
        yield session

    app.dependency_overrides[get_current_user_id] = override_user_id
    app.dependency_overrides[get_db] = override_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()


def _me_response(user_id: uuid.UUID) -> CurrentUserResponse:
    return CurrentUserResponse(
        id=user_id,
        email="test@example.com",
        customer_number=1,
        email_verified=True,
        phone=None,
        phone_verified=False,
        status="active",
        last_login_at=None,
        created_at=datetime.now(UTC),
        profile=UserProfileResponse(first_name="T", last_name="U"),
        preferences=None,
        onboarding=OnboardingFlagsResponse(
            email_verified=True,
            profile_complete=True,
            has_address=False,
            preferences_reviewed=False,
            shopping_ready=False,
        ),
    )


@pytest.mark.asyncio
async def test_get_me(client, user_id):
    with patch.object(app.state.account_service, "get_me", new=AsyncMock(return_value=_me_response(user_id))):
        resp = await client.get("/api/user/me", headers={"Authorization": "Bearer fake"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "test@example.com"


@pytest.mark.asyncio
async def test_get_onboarding(client, user_id):
    onboarding = OnboardingResponse(
        email_verified=True,
        profile_complete=True,
        has_address=False,
        preferences_reviewed=False,
        shopping_ready=False,
        steps=[OnboardingStep(key="email_verified", label="Verify email", completed=True)],
        completion_percent=50,
    )
    with patch.object(app.state.account_service, "get_onboarding", new=AsyncMock(return_value=onboarding)):
        resp = await client.get("/api/user/me/onboarding", headers={"Authorization": "Bearer fake"})
    assert resp.status_code == 200
    assert resp.json()["completion_percent"] == 50


@pytest.mark.asyncio
async def test_list_addresses(client):
    listing = AddressListResponse(items=[], total=0)
    with patch.object(app.state.account_service, "list_addresses", new=AsyncMock(return_value=listing)):
        resp = await client.get("/api/user/me/addresses", headers={"Authorization": "Bearer fake"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_get_preferences(client, user_id):
    prefs = PreferencesResponse(
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
    with patch.object(app.state.account_service, "get_preferences", new=AsyncMock(return_value=prefs)):
        resp = await client.get("/api/user/me/preferences", headers={"Authorization": "Bearer fake"})
    assert resp.status_code == 200
    assert resp.json()["currency"] == "INR"


@pytest.mark.asyncio
async def test_security_devices(client):
    devices = DeviceListResponse(items=[], total=0)
    with patch.object(app.state.account_service, "list_devices", new=AsyncMock(return_value=devices)):
        resp = await client.get("/api/user/me/security/devices", headers={"Authorization": "Bearer fake"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_security_logins(client):
    logins = LoginHistoryListResponse(items=[], total=0)
    with patch.object(app.state.account_service, "list_login_history", new=AsyncMock(return_value=logins)):
        resp = await client.get("/api/user/me/security/logins", headers={"Authorization": "Bearer fake"})
    assert resp.status_code == 200
