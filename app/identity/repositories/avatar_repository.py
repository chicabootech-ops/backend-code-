"""Avatar key persistence on user profile."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.models import UserProfile


class AvatarRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def set_key(self, profile: UserProfile, key: str) -> UserProfile:
        profile.avatar_url = key
        profile.updated_at = datetime.now(UTC)
        await self._session.flush()
        return profile

    async def clear(self, profile: UserProfile) -> UserProfile:
        profile.avatar_url = None
        profile.updated_at = datetime.now(UTC)
        await self._session.flush()
        return profile
