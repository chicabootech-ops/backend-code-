"""Self-issued OTP challenges.

The code is generated, hashed and verified here — never by a messaging vendor.
WhatsApp needs the code as a template variable, so a provider that generates and
validates it itself cannot deliver over WhatsApp at all.

Security posture: the code is generated with `secrets`, stored only as an Argon2
hash, never logged, and never returned in an API response. Verification is
attempt-limited and single-use, and a resend supersedes the previous challenge so
an old code stops working the moment a new one goes out.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.identity.core.security.password import hash_otp, verify_otp

logger = logging.getLogger(__name__)


class OtpError(Exception):
    def __init__(self, message: str, *, code: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


@dataclass(slots=True)
class OtpChallenge:
    id: uuid.UUID
    #: The raw code. Held in memory only, for the duration of the send. Never
    #: persisted, never logged, never serialised into a response.
    code: str
    expires_at: datetime


class OtpService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    # ------------------------------------------------------------------ #
    # Issue
    # ------------------------------------------------------------------ #
    async def issue(
        self,
        *,
        purpose: str,
        destination: str,
        destination_type: str = "phone",
        user_id: uuid.UUID | None = None,
        ip_address: str | None = None,
    ) -> OtpChallenge:
        """Create a challenge, superseding any live one for this destination."""
        await self._enforce_cooldown(destination, purpose)
        await self._enforce_rate_limits(destination, ip_address)

        length = self._settings.otp_length
        code = f"{secrets.randbelow(10**length):0{length}d}"
        expires_at = datetime.now(UTC) + timedelta(seconds=self._settings.otp_ttl_seconds)

        # Supersede first: a resend must invalidate the previous code, otherwise
        # two codes are simultaneously valid for one destination.
        await self._session.execute(
            text(
                """
                UPDATE identity.otp_challenges
                SET superseded_at = now()
                WHERE destination = :dest AND purpose = :purpose
                  AND consumed_at IS NULL AND superseded_at IS NULL
                """
            ),
            {"dest": destination, "purpose": purpose},
        )

        challenge_id = (
            await self._session.execute(
                text(
                    """
                    INSERT INTO identity.otp_challenges
                        (user_id, purpose, destination_type, destination, otp_hash,
                         expires_at, max_attempts)
                    VALUES (:uid, :purpose, :dtype, :dest, :hash, :expires, :max_attempts)
                    RETURNING id
                    """
                ),
                {
                    "uid": str(user_id) if user_id else None,
                    "purpose": purpose,
                    "dtype": destination_type,
                    "dest": destination,
                    "hash": hash_otp(code),
                    "expires": expires_at,
                    "max_attempts": self._settings.otp_max_verify_attempts,
                },
            )
        ).scalar_one()
        await self._session.commit()

        # Note the absence of the code in this log line, deliberately.
        logger.info(
            "otp_challenge_created id=%s purpose=%s type=%s",
            challenge_id,
            purpose,
            destination_type,
        )
        return OtpChallenge(id=challenge_id, code=code, expires_at=expires_at)

    async def supersede(self, challenge_id: uuid.UUID) -> None:
        """Retire a challenge whose code never made it out.

        Without this, a failed send still starts the resend cooldown, so the user
        is told "please try again" and then refused for the next minute.
        """
        await self._session.execute(
            text(
                """
                UPDATE identity.otp_challenges
                SET superseded_at = now()
                WHERE id = :id AND consumed_at IS NULL AND superseded_at IS NULL
                """
            ),
            {"id": str(challenge_id)},
        )
        await self._session.commit()
        logger.info("otp_challenge_superseded id=%s reason=send_failed", challenge_id)

    # ------------------------------------------------------------------ #
    # Verify
    # ------------------------------------------------------------------ #
    async def verify(self, *, purpose: str, destination: str, code: str) -> uuid.UUID:
        """Consume the live challenge. Returns its id, or raises."""
        row = (
            await self._session.execute(
                text(
                    """
                    SELECT id, otp_hash, expires_at, attempts, max_attempts, user_id
                    FROM identity.otp_challenges
                    WHERE destination = :dest AND purpose = :purpose
                      AND consumed_at IS NULL AND superseded_at IS NULL
                    ORDER BY created_at DESC
                    LIMIT 1
                    FOR UPDATE
                    """
                ),
                {"dest": destination, "purpose": purpose},
            )
        ).mappings().first()

        if row is None:
            raise OtpError(
                "No verification code is pending. Please request a new one.",
                code="otp_not_found",
                status_code=400,
            )

        if _aware(row["expires_at"]) < datetime.now(UTC):
            await self._session.commit()
            raise OtpError(
                "This code has expired. Please request a new one.",
                code="otp_expired",
                status_code=400,
            )

        if int(row["attempts"]) >= int(row["max_attempts"]):
            await self._session.commit()
            raise OtpError(
                "Too many incorrect attempts. Please request a new code.",
                code="otp_max_attempts",
                status_code=429,
            )

        # Count the attempt before checking, so a crash mid-verify cannot be used
        # to get a free guess.
        await self._session.execute(
            text("UPDATE identity.otp_challenges SET attempts = attempts + 1 WHERE id = :id"),
            {"id": str(row["id"])},
        )

        if not verify_otp(code.strip(), row["otp_hash"]):
            await self._session.commit()
            logger.info("otp_verify_failed id=%s", row["id"])
            raise OtpError("Invalid code.", code="otp_invalid", status_code=400)

        # Single use: consuming it here is what stops replay.
        await self._session.execute(
            text("UPDATE identity.otp_challenges SET consumed_at = now() WHERE id = :id"),
            {"id": str(row["id"])},
        )
        await self._session.commit()
        logger.info("otp_verify_success id=%s purpose=%s", row["id"], purpose)
        return row["id"]

    # ------------------------------------------------------------------ #
    # Limits
    # ------------------------------------------------------------------ #
    async def _enforce_cooldown(self, destination: str, purpose: str) -> None:
        # Superseded challenges are excluded on purpose. `issue` supersedes the
        # previous challenge *after* this check, so a genuine resend is still
        # rate-limited; the only rows this skips are the ones `supersede` retired
        # because their code never reached the customer. The hourly per-phone cap
        # still counts those, so this cannot be used to send without limit.

        last = (
            await self._session.execute(
                text(
                    """
                    SELECT created_at FROM identity.otp_challenges
                    WHERE destination = :dest AND purpose = :purpose
                      AND superseded_at IS NULL
                    ORDER BY created_at DESC LIMIT 1
                    """
                ),
                {"dest": destination, "purpose": purpose},
            )
        ).scalar_one_or_none()
        if last is None:
            return
        elapsed = (datetime.now(UTC) - _aware(last)).total_seconds()
        cooldown = self._settings.otp_resend_cooldown_seconds
        if elapsed < cooldown:
            raise OtpError(
                f"Please wait {int(cooldown - elapsed)}s before requesting another code.",
                code="otp_cooldown",
                status_code=429,
            )

    async def _enforce_rate_limits(self, destination: str, ip_address: str | None) -> None:
        per_phone = (
            await self._session.execute(
                text(
                    """
                    SELECT COUNT(*) FROM identity.otp_challenges
                    WHERE destination = :dest AND created_at > now() - interval '1 hour'
                    """
                ),
                {"dest": destination},
            )
        ).scalar_one()
        if int(per_phone) >= self._settings.rate_limit_otp_per_phone_hourly:
            raise OtpError(
                "Too many codes requested for this number. Please try again later.",
                code="otp_rate_limited",
                status_code=429,
            )
        # Per-IP limiting lives in the Redis rate limiter used by the routers;
        # this table only knows destinations. Both apply.


def _aware(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    return value if value.tzinfo else value.replace(tzinfo=UTC)
