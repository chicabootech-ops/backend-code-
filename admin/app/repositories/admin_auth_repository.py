from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.commerce import AdminUser, Role


class AdminAuthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_email(self, email: str) -> tuple[AdminUser, str] | None:
        result = await self._session.execute(
            select(AdminUser, Role.name)
            .join(Role, Role.id == AdminUser.role_id)
            .where(
                AdminUser.email == email.lower().strip(),
                AdminUser.deleted_at.is_(None),
                AdminUser.status == "active",
            )
        )
        row = result.one_or_none()
        if not row:
            return None
        return row[0], row[1]
