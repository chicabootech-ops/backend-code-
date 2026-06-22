"""Avatar service unit tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.schemas.avatar import AvatarConfirmRequest, AvatarUploadUrlRequest
from app.services.avatar_service import AvatarService


@pytest.fixture
def user_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def mock_r2():
    r2 = MagicMock()
    r2.avatar_key.return_value = "avatars/test.webp"
    r2.create_presigned_put.return_value = ("https://r2.example/upload", datetime.now(UTC))
    r2.create_presigned_get.return_value = ("https://r2.example/get", datetime.now(UTC))
    r2.object_exists.return_value = {"ContentLength": 1024}
    r2.validate_uploaded_object.return_value = None
    return r2


@pytest.fixture
def mock_session(user_id):
    profile = SimpleNamespace(avatar_url=None, updated_at=datetime.now(UTC))
    user = SimpleNamespace(id=user_id, profile=profile)
    session = AsyncMock()
    return session, user


@pytest.mark.asyncio
async def test_create_upload_url(mock_r2, mock_session, user_id):
    session, user = mock_session
    service = AvatarService(mock_r2)
    service._require_user = AsyncMock(return_value=user)  # type: ignore[method-assign]

    body = AvatarUploadUrlRequest(content_type="image/webp", content_length=2048)
    result = await service.create_upload_url(session, user_id, body)

    assert result.key == "avatars/test.webp"
    assert "https://r2.example/upload" in result.upload_url
    mock_r2.create_presigned_put.assert_called_once()


@pytest.mark.asyncio
async def test_confirm_upload_sets_key(mock_r2, mock_session, user_id):
    session, user = mock_session
    service = AvatarService(mock_r2)
    service._require_user = AsyncMock(return_value=user)  # type: ignore[method-assign]

    body = AvatarConfirmRequest(content_length=1024)
    result = await service.confirm_upload(session, user_id, body)

    assert result.key == "avatars/test.webp"
    assert user.profile.avatar_url == "avatars/test.webp"
