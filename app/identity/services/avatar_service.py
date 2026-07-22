"""Avatar upload flow via Cloudflare R2."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.core.exceptions import NotFoundError, ValidationError
from app.identity.integrations.r2.client import R2Client
from app.identity.repositories.avatar_repository import AvatarRepository
from app.identity.repositories.user_repository import UserRepository
from app.identity.schemas.avatar import (
    MAX_AVATAR_BYTES,
    AvatarConfirmRequest,
    AvatarConfirmResponse,
    AvatarUploadUrlRequest,
    AvatarUploadUrlResponse,
    AvatarUrlResponse,
)
from app.identity.schemas.common import MessageResponse


class AvatarService:
    def __init__(self, r2: R2Client) -> None:
        self._r2 = r2

    async def create_upload_url(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        body: AvatarUploadUrlRequest,
    ) -> AvatarUploadUrlResponse:
        user = await self._require_user(session, user_id)
        if not user.profile:
            raise NotFoundError("Profile not found", code="profile_not_found")

        key = self._r2.avatar_key(user_id)
        upload_url, expires_at = self._r2.create_presigned_put(
            key=key,
            content_type=body.content_type,
            content_length=body.content_length,
        )
        return AvatarUploadUrlResponse(
            upload_url=upload_url,
            key=key,
            expires_at=expires_at,
            max_size_bytes=MAX_AVATAR_BYTES,
        )

    async def confirm_upload(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        body: AvatarConfirmRequest,
    ) -> AvatarConfirmResponse:
        user = await self._require_user(session, user_id)
        if not user.profile:
            raise NotFoundError("Profile not found", code="profile_not_found")

        key = self._r2.avatar_key(user_id)
        self._r2.validate_uploaded_object(key, max_size=MAX_AVATAR_BYTES)

        meta = self._r2.object_exists(key)
        if meta and int(meta.get("ContentLength", 0)) != body.content_length:
            raise ValidationError("Uploaded file size does not match declared size", code="size_mismatch")

        avatar_repo = AvatarRepository(session)
        await avatar_repo.set_key(user.profile, key)
        return AvatarConfirmResponse(key=key)

    async def get_avatar_url(self, session: AsyncSession, user_id: uuid.UUID) -> AvatarUrlResponse:
        user = await self._require_user(session, user_id)
        if not user.profile or not user.profile.avatar_url:
            raise NotFoundError("No avatar configured", code="avatar_not_found")

        key = user.profile.avatar_url
        url, expires_at = self._r2.create_presigned_get(key)
        return AvatarUrlResponse(url=url, key=key, expires_at=expires_at)

    async def delete_avatar(self, session: AsyncSession, user_id: uuid.UUID) -> MessageResponse:
        user = await self._require_user(session, user_id)
        if not user.profile or not user.profile.avatar_url:
            raise NotFoundError("No avatar configured", code="avatar_not_found")

        key = user.profile.avatar_url
        self._r2.delete_object(key)

        avatar_repo = AvatarRepository(session)
        await avatar_repo.clear(user.profile)
        return MessageResponse(message="Avatar deleted")

    async def _require_user(self, session: AsyncSession, user_id: uuid.UUID):
        user = await UserRepository(session).get_by_id(user_id)
        if not user:
            raise NotFoundError("User not found", code="user_not_found")
        return user
