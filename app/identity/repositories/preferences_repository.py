"""User preferences persistence."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.models import UserPreferences


class PreferencesRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_user(self, user_id: uuid.UUID) -> UserPreferences | None:
        stmt = select(UserPreferences).where(UserPreferences.user_id == user_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def update(self, prefs: UserPreferences, data: dict[str, Any]) -> UserPreferences:
        for key, value in data.items():
            setattr(prefs, key, value)
        prefs.updated_at = datetime.now(UTC)
        await self._session.flush()
        return prefs
