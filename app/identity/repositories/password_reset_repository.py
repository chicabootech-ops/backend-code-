"""Password reset token persistence."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.models import PasswordReset


class PasswordResetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        token_hash: str,
        expires_at: datetime,
    ) -> PasswordReset:
        row = PasswordReset(
            id=uuid.uuid4(),
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            used=False,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_valid_by_token_hash(self, token_hash: str) -> PasswordReset | None:
        now = datetime.now(UTC)
        stmt = select(PasswordReset).where(
            PasswordReset.token_hash == token_hash,
            PasswordReset.used.is_(False),
            PasswordReset.expires_at > now,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def mark_used(self, row: PasswordReset) -> None:
        row.used = True
