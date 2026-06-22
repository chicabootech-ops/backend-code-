"""JWT access tokens and refresh token rotation."""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.exceptions import UnauthorizedError
from app.core.redis.client import RedisClient
from app.core.security.jwt import JWTManager
from app.models import RefreshToken, User
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.schemas.auth import TokenResponse


class TokenService:
    def __init__(
        self,
        settings: Settings,
        jwt_manager: JWTManager,
        redis: RedisClient,
    ) -> None:
        self._settings = settings
        self._jwt = jwt_manager
        self._redis = redis

    def issue_tokens(self, user_id: uuid.UUID) -> tuple[TokenResponse, str, RefreshToken]:
        """Return API response, refresh token string, and unsaved RefreshToken row metadata."""
        access_token, access_jti, expires_in = self._jwt.create_access_token(str(user_id))
        refresh_jti = str(uuid.uuid4())
        refresh_plain = f"{refresh_jti}.{secrets.token_urlsafe(32)}"
        expires_at = datetime.now(UTC) + timedelta(seconds=self._settings.jwt_refresh_ttl_seconds)

        token_response = TokenResponse(
            access_token=access_token,
            refresh_token=refresh_plain,
            expires_in=expires_in,
        )
        refresh_row = RefreshToken(
            id=uuid.uuid4(),
            user_id=user_id,
            token_jti=refresh_jti,
            expires_at=expires_at,
            revoked=False,
        )
        return token_response, refresh_plain, refresh_row

    async def persist_refresh_token(
        self,
        session: AsyncSession,
        refresh_row: RefreshToken,
    ) -> None:
        session.add(refresh_row)
        await session.flush()
        ttl = self._settings.jwt_refresh_ttl_seconds
        await self._redis.cache_refresh_session(refresh_row.token_jti, str(refresh_row.user_id), ttl)

    async def rotate_refresh_token(
        self,
        session: AsyncSession,
        refresh_plain: str,
    ) -> tuple[TokenResponse, User]:
        refresh_jti = self._parse_refresh_jti(refresh_plain)
        repo = RefreshTokenRepository(session)
        stored = await repo.get_by_jti(refresh_jti)
        if not stored or stored.revoked:
            raise UnauthorizedError("Invalid refresh token", code="invalid_refresh_token")

        now = datetime.now(UTC)
        expires = stored.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires <= now:
            raise UnauthorizedError("Refresh token expired", code="refresh_token_expired")

        from app.repositories.user_repository import UserRepository

        user = await UserRepository(session).get_by_id(stored.user_id)
        if not user or user.status not in ("active", "pending_verification"):
            raise UnauthorizedError("Account not active", code="account_inactive")

        await repo.revoke(stored)
        await self._redis.delete_refresh_session(stored.token_jti)

        tokens, new_plain, new_row = self.issue_tokens(user.id)
        await self.persist_refresh_token(session, new_row)
        return tokens, user

    async def revoke_refresh_token(self, session: AsyncSession, refresh_plain: str | None) -> None:
        if not refresh_plain:
            return
        refresh_jti = self._parse_refresh_jti(refresh_plain)
        repo = RefreshTokenRepository(session)
        stored = await repo.get_by_jti(refresh_jti)
        if stored and not stored.revoked:
            await repo.revoke(stored)
            await self._redis.delete_refresh_session(stored.token_jti)

    async def blacklist_access_token(self, access_token: str) -> None:
        payload = self._jwt.decode_token(access_token, expected_type="access")
        ttl = payload.exp - int(datetime.now(UTC).timestamp())
        await self._redis.blacklist_access_token(payload.jti, max(ttl, 0))

    @staticmethod
    def _parse_refresh_jti(refresh_plain: str) -> str:
        parts = refresh_plain.split(".", 1)
        if len(parts) != 2 or not parts[0]:
            raise UnauthorizedError("Malformed refresh token", code="invalid_refresh_token")
        return parts[0]
