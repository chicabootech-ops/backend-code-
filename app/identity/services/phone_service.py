"""Phone number verification via Message Central SMS OTP."""

from __future__ import annotations

import logging
import re
import time
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.identity.core.exceptions import AppError, ForbiddenError, ValidationError
from app.identity.core.redis.bundle import DeleteValue, ReadValue, WriteValue
from app.identity.core.redis.client import (
    RedisClient,
    RedisUnavailableError,
    required,
    unavailable_ok,
)
from app.identity.core.redis.keys import PHONE_VERIFY_FIELD, bundle_user_key
from app.identity.core.validation import validate_phone
from app.identity.integrations.message_central import MessageCentralClient
from app.identity.repositories.user_repository import UserRepository
from app.identity.schemas.common import MessageResponse
from app.identity.services.rate_limit_service import RateLimitService, by_user

logger = logging.getLogger(__name__)


def _digits_only_national(phone: str, country_code: str = "91") -> str:
    normalized = validate_phone(phone)
    digits = re.sub(r"\D", "", normalized)
    if digits.startswith(country_code) and len(digits) > 10:
        digits = digits[len(country_code) :]
    if digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    return digits


class PhoneService:
    def __init__(
        self,
        settings: Settings,
        redis: RedisClient,
        rate_limit: RateLimitService,
        sms: MessageCentralClient,
    ) -> None:
        self._settings = settings
        self._redis = redis
        self._rate_limit = rate_limit
        self._sms = sms

    async def send_otp(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        *,
        phone: str | None,
    ) -> MessageResponse:
        if not self._sms.configured:
            raise AppError(
                "Phone verification is not configured yet. Add Message Central credentials.",
                code="sms_not_configured",
                status_code=503,
            )

        await self._rate_limit.check_rules(
            [
                by_user(
                    "phone_send_otp",
                    str(user_id),
                    limit=self._settings.rate_limit_phone_otp,
                    window_seconds=900,
                )
            ]
        )

        users = UserRepository(session)
        user = await users.get_by_id(user_id)
        if not user:
            raise AppError("User not found", code="user_not_found", status_code=404)

        target = phone or user.phone
        if not target:
            raise ValidationError("Phone number is required", code="phone_required")

        national = _digits_only_national(target, self._settings.message_central_country_code)
        if len(national) != 10:
            raise ValidationError("Invalid Indian mobile number", code="invalid_phone")

        # Persist phone on user (unverified until OTP succeeds)
        e164_like = f"+{self._settings.message_central_country_code}{national}"
        if user.phone != e164_like and user.phone != national:
            user.phone = e164_like
            user.phone_verified = False
            user.updated_at = datetime.now(UTC).replace(tzinfo=None)
            await session.flush()

        try:
            result = await self._sms.send_otp(mobile_number=national)
        except AppError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unexpected SMS send failure")
            raise AppError(
                "Failed to send SMS OTP. Please try again.",
                code="sms_send_failed",
                status_code=503,
            ) from exc

        # Store verification id for the validate step. Unlike the email OTP this
        # has no database mirror — Message Central holds the code and we only
        # keep the handle — so a Redis failure has to surface, not be swallowed.
        ttl = max(30, int(result.timeout_seconds or self._settings.otp_ttl_seconds))
        await self._phone_session_write(
            user_id,
            WriteValue(
                key=bundle_user_key(str(user_id)),
                field=PHONE_VERIFY_FIELD,
                value=f"{result.verification_id}|{national}",
                ttl_seconds=ttl,
            ),
        )

        return MessageResponse(message="OTP sent to your phone number.")

    async def verify_otp(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        *,
        otp: str,
    ) -> MessageResponse:
        if not self._sms.configured:
            raise AppError(
                "Phone verification is not configured yet.",
                code="sms_not_configured",
                status_code=503,
            )

        await self._rate_limit.check_rules(
            [
                by_user(
                    "phone_verify_otp",
                    str(user_id),
                    limit=self._settings.rate_limit_phone_otp,
                    window_seconds=900,
                )
            ]
        )

        stored = await self._phone_session_read(user_id)
        if not stored:
            raise ForbiddenError(
                "OTP expired or not requested. Please send a new code.",
                code="otp_expired",
            )
        verification_id, _, national = stored.partition("|")
        if not verification_id:
            raise ForbiddenError("OTP session invalid. Please send a new code.", code="otp_invalid")

        ok = await self._sms.validate_otp(verification_id=verification_id, code=otp.strip())
        if not ok:
            raise ForbiddenError("Invalid OTP", code="otp_invalid")

        users = UserRepository(session)
        user = await users.get_by_id(user_id)
        if not user:
            raise AppError("User not found", code="user_not_found", status_code=404)

        user.phone = f"+{self._settings.message_central_country_code}{national}"
        user.phone_verified = True
        user.updated_at = datetime.now(UTC).replace(tzinfo=None)
        await session.flush()
        await unavailable_ok(
            self._redis.bundle(
                [DeleteValue(key=bundle_user_key(str(user_id)), field=PHONE_VERIFY_FIELD)],
                now=int(time.time()),
            ),
            default=[],
            what="phone OTP session delete",
        )

        return MessageResponse(message="Phone number verified successfully.")

    async def _phone_session_write(self, user_id: uuid.UUID, op: WriteValue) -> None:
        try:
            await required(
                self._redis.bundle([op], now=int(time.time())),
                what="phone OTP session write",
            )
        except RedisUnavailableError as exc:
            raise AppError(
                "Phone verification is temporarily unavailable. Please try again.",
                code="otp_store_unavailable",
                status_code=503,
            ) from exc

    async def _phone_session_read(self, user_id: uuid.UUID) -> str | None:
        try:
            results = await required(
                self._redis.bundle(
                    [ReadValue(key=bundle_user_key(str(user_id)), field=PHONE_VERIFY_FIELD)],
                    now=int(time.time()),
                ),
                what="phone OTP session read",
            )
        except RedisUnavailableError as exc:
            raise AppError(
                "Phone verification is temporarily unavailable. Please try again.",
                code="otp_store_unavailable",
                status_code=503,
            ) from exc
        value = results[0] if results else None
        return value if isinstance(value, str) else None
