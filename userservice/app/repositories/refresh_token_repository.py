"""Refresh token persistence."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RefreshToken


class RefreshTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, *, user_id: uuid.UUID, token_jti: str, expires_at: datetime) -> RefreshToken:
        row = RefreshToken(
            id=uuid.uuid4(),
            user_id=user_id,
            token_jti=token_jti,
            expires_at=expires_at,
            revoked=False,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_by_jti(self, token_jti: str) -> RefreshToken | None:
        stmt = select(RefreshToken).where(RefreshToken.token_jti == token_jti)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def revoke(self, token: RefreshToken) -> None:
        token.revoked = True

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> None:
        stmt = (
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked.is_(False))
            .values(revoked=True)
        )
        await self._session.execute(stmt)
