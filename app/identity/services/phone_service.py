"""Phone number verification via Message Central SMS OTP."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.identity.core.exceptions import AppError, ForbiddenError, ValidationError
from app.identity.core.redis.client import RedisClient
from app.identity.core.validation import validate_phone
from app.identity.integrations.message_central import MessageCentralClient
from app.identity.repositories.user_repository import UserRepository
from app.identity.schemas.common import MessageResponse
from app.identity.services.rate_limit_service import RateLimitService

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

        await self._rate_limit.check(
            "phone_send_otp",
            str(user_id),
            limit=self._settings.rate_limit_phone_otp,
            window_seconds=900,
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
            user.updated_at = datetime.now(UTC)
            await session.flush()

        result = await self._sms.send_otp(mobile_number=national)
        # Store verification id for validate step
        key = f"phone_verify:{user_id}"
        await self._redis.raw.setex(
            key,
            result.timeout_seconds or self._settings.otp_ttl_seconds,
            f"{result.verification_id}|{national}",
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

        await self._rate_limit.check(
            "phone_verify_otp",
            str(user_id),
            limit=self._settings.rate_limit_phone_otp,
            window_seconds=900,
        )

        raw = await self._redis.raw.get(f"phone_verify:{user_id}")
        if not raw:
            raise ForbiddenError(
                "OTP expired or not requested. Please send a new code.",
                code="otp_expired",
            )
        payload = raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)
        verification_id, _, national = payload.partition("|")
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
        user.updated_at = datetime.now(UTC)
        await session.flush()
        await self._redis.raw.delete(f"phone_verify:{user_id}")

        return MessageResponse(message="Phone number verified successfully.")
