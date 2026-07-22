"""Email verification OTP persistence."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.models import EmailVerification
from app.identity.repositories.user_repository import normalize_email


class EmailVerificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        email: str,
        otp_hash: str,
        expires_at: datetime,
        purpose: str = "registration",
        max_attempts: int = 3,
    ) -> EmailVerification:
        email_norm = normalize_email(email)
        row = EmailVerification(
            id=uuid.uuid4(),
            email=email.strip(),
            email_normalized=email_norm,
            otp_hash=otp_hash,
            expires_at=expires_at,
            purpose=purpose,
            max_attempts=max_attempts,
            verified=False,
            attempts=0,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_active(
        self,
        *,
        email_normalized: str,
        purpose: str,
    ) -> EmailVerification | None:
        now = datetime.now(UTC)
        stmt = (
            select(EmailVerification)
            .where(
                EmailVerification.email_normalized == email_normalized,
                EmailVerification.purpose == purpose,
                EmailVerification.verified.is_(False),
                EmailVerification.verified_at.is_(None),
                EmailVerification.expires_at > now,
            )
            .order_by(EmailVerification.created_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def mark_verified(self, row: EmailVerification) -> None:
        row.verified = True
        row.verified_at = datetime.now(UTC)

    async def increment_attempts(self, row: EmailVerification) -> None:
        row.attempts += 1
