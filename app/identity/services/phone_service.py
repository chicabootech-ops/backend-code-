"""Phone number verification.

The OTP is now **issued by us**, not by Message Central VerifyNow. That change is
what makes WhatsApp-primary delivery possible: WhatsApp needs the code as a
template variable, and VerifyNow never exposed it. One code is generated, hashed
into `identity.otp_challenges`, and the same code goes out over WhatsApp and — if
and only if WhatsApp definitively fails — over SMS.

It also removes a hard dependency: the challenge used to live only in Redis, so
this endpoint 503'd whenever Redis blipped. It is in Postgres now.
"""

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
from app.notifications.otp_service import OtpError, OtpService
from app.notifications.service import NotificationService
from app.notifications.types import NotificationType

#: Purpose recorded on the challenge; also scopes the resend cooldown.
OTP_PURPOSE_PHONE_VERIFY = "phone_verify"

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
        notifications_factory=None,
    ) -> None:
        self._settings = settings
        self._redis = redis
        self._rate_limit = rate_limit
        #: Retained so `configured` still reflects Message Central credentials,
        #: and so the legacy VerifyNow client stays reachable if ever needed.
        self._sms = sms
        #: Callable(session) -> NotificationService. Injected so the identity
        #: layer does not have to know how providers are wired.
        self._notifications_factory = notifications_factory

    async def send_otp(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        *,
        phone: str | None,
    ) -> MessageResponse:
        # A phone OTP can ride either channel, so refuse only when *neither* can
        # carry it. Gating on Message Central alone was a VerifyNow leftover: it
        # rejects every OTP in a WhatsApp-only deployment, and it short-circuits
        # ahead of the notification ladder — which is why the fallback logic
        # below could never run while SMS credentials were missing.
        if not (self._settings.whatsapp_configured or self._sms.configured):
            raise AppError(
                "Phone verification is not configured yet.",
                code="phone_channel_not_configured",
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

        # One code, generated and hashed by us. It is held in memory only long
        # enough to hand to the notification layer.
        otp_service = OtpService(session, self._settings)
        try:
            challenge = await otp_service.issue(
                purpose=OTP_PURPOSE_PHONE_VERIFY,
                destination=e164_like,
                destination_type="phone",
                user_id=user_id,
            )
        except OtpError as exc:
            raise AppError(exc.message, code=exc.code, status_code=exc.status_code) from exc

        notifications = self._notifications(session)
        if notifications is None:
            raise AppError(
                "Phone verification is not configured yet.",
                code="sms_not_configured",
                status_code=503,
            )

        # WhatsApp first, SMS only on a definitive WhatsApp failure. The
        # challenge id is the idempotency key, so a double-submit cannot send
        # the same code twice.
        outcome = await notifications.send(
            NotificationType.OTP_PHONE_VERIFY,
            recipient=e164_like,
            variables={"otp": challenge.code},
            idempotency_key=f"otp:{challenge.id}",
            user_id=user_id,
            reference_type="otp",
            otp_challenge_id=challenge.id,
        )

        # Every channel failed definitively. Reporting success here is how a
        # dead SMS provider looks like a customer problem: the API says the code
        # was sent, and nobody finds out otherwise until someone reads the logs.
        # UNKNOWN is deliberately not treated as a failure — the message may well
        # have arrived, and telling the user to retry would duplicate the code.
        if outcome.failed:
            await otp_service.supersede(challenge.id)
            raise AppError(
                "We could not send your verification code right now. Please try again.",
                code="otp_send_failed",
                status_code=502,
            )

        return MessageResponse(message="OTP sent to your phone number.")

    async def verify_otp(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        *,
        otp: str,
    ) -> MessageResponse:
        # No provider check here, deliberately. Verification is entirely local
        # now — the code is matched against the Argon2 hash in
        # identity.otp_challenges. Gating it on Message Central was correct only
        # while VerifyNow validated the code for us; keeping it would reject a
        # perfectly good code whenever the SMS provider happened to be
        # unconfigured or down.
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

        users = UserRepository(session)
        user = await users.get_by_id(user_id)
        if not user:
            raise AppError("User not found", code="user_not_found", status_code=404)
        if not user.phone:
            raise ForbiddenError(
                "No verification is pending. Please request a new code.",
                code="otp_expired",
            )

        # Verified against our own hashed challenge, not the provider's. Attempt
        # counting, expiry and single-use all live in OtpService.
        otp_service = OtpService(session, self._settings)
        try:
            await otp_service.verify(
                purpose=OTP_PURPOSE_PHONE_VERIFY,
                destination=user.phone,
                code=otp,
            )
        except OtpError as exc:
            # Preserve the previous contract: a bad/expired code is a 403 here.
            if exc.status_code == 429:
                raise AppError(exc.message, code=exc.code, status_code=429) from exc
            raise ForbiddenError(exc.message, code=exc.code) from exc

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

    def _notifications(self, session: AsyncSession):
        """Build the notification service for this request's session.

        Injected as a factory rather than constructed here so the identity layer
        stays unaware of which providers exist or how they are configured.
        """
        if self._notifications_factory is None:
            return None
        return self._notifications_factory(session)

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
