"""NotificationService — the only thing business logic talks to.

Callers say *what happened*, not *how to tell anyone*:

    await notifications.send(
        NotificationType.ORDER_CONFIRMED,
        recipient="+919876543210",
        variables={"order_number": "1042", "total": "Rs. 1,499"},
        idempotency_key=f"order:{order_id}:ORDER_CONFIRMED",
    )

Everything else — which template, whether the customer consented, whether this was
already sent, and what a failure earns — is decided here.

**WhatsApp is the only channel.** There is no SMS vendor to fall back to, so a
failure is resolved on WhatsApp or not at all:

    delivered/accepted  -> stop
    TRANSIENT failure   -> schedule a retry on WhatsApp (5 min, then 15 min)
    PERMANENT failure   -> stop; this will never work for this recipient
    UNKNOWN             -> stop and reconcile; the message may have arrived

The UNKNOWN rule survived the removal of SMS and matters more now, not less. A
timeout is not a failure: Meta may well have delivered the message, and a retry
would send the customer a second copy of the same OTP and bill a second
conversation. UNKNOWN waits for the webhook; the reconciler resolves it if no
signal ever arrives.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.notifications.providers.base import NotificationProvider
from app.notifications.repository import NotificationRepository
from app.notifications.types import (
    Category,
    Channel,
    DeliveryStatus,
    NotificationType,
    OutboundMessage,
    Provider,
    ProviderResult,
    category_for,
    consent_column_for,
)

logger = logging.getLogger(__name__)

#: Which provider serves which channel. WhatsApp is the only live transport;
#: this mapping is the single place a future one would be added.
_PROVIDER_FOR_CHANNEL = {
    Channel.WHATSAPP: Provider.WHATSAPP,
}


@dataclass(slots=True)
class SendOutcome:
    """The result of `send()` — the row it created *and* how delivery went.

    These are two different questions and callers need both. An OTP caller in
    particular must not report "code sent" when the whole channel ladder failed;
    returning only the id made that mistake the easy one to make.
    """

    notification_id: uuid.UUID | None
    #: None when nothing was delivered inline — a duplicate was suppressed,
    #: consent was missing, or the caller passed deliver_now=False.
    status: DeliveryStatus | None

    @property
    def failed(self) -> bool:
        """True only for a definitive failure. UNKNOWN is deliberately not one."""
        return self.status is DeliveryStatus.FAILED


class NotificationService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        providers: dict[Channel, NotificationProvider],
    ) -> None:
        self._session = session
        self._settings = settings
        self._repo = NotificationRepository(session)
        self._providers = providers

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    async def send(
        self,
        notification_type: NotificationType | str,
        *,
        recipient: str,
        variables: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        user_id: uuid.UUID | None = None,
        reference_type: str | None = None,
        reference_id: uuid.UUID | None = None,
        otp_challenge_id: uuid.UUID | None = None,
        campaign_id: uuid.UUID | None = None,
        allow_fallback: bool | None = None,
        deliver_now: bool = True,
    ) -> SendOutcome:
        """Create and (by default) deliver a notification.

        The outcome carries the notification id — None when this exact
        notification was already created, so a duplicate order event produces no
        second message — and the delivery status, so a caller can tell the
        difference between "sent" and "every channel failed".
        """
        ntype = NotificationType(notification_type)
        category = category_for(ntype)
        primary = self._primary_channel(category)
        fallback_allowed = (
            allow_fallback if allow_fallback is not None else self._fallback_allowed(category)
        )

        # Consent gate. Transactional and OTP are service messages and are not
        # gated — a customer who placed an order asked for its updates, and
        # someone requesting a login code asked for that code. Marketing and cart
        # reminders each require their own explicit opt-in.
        #
        # An anonymous recipient (user_id=None) cannot be consent-checked, so
        # marketing to one is refused rather than assumed. Campaigns always
        # resolve to a user id; this guards ad-hoc callers.
        if category is Category.MARKETING:
            if user_id is None:
                logger.warning("notification_suppressed_anonymous_marketing type=%s", ntype)
                return SendOutcome(notification_id=None, status=None)
            if not await self._marketing_allowed(user_id, ntype):
                logger.info("notification_suppressed_no_consent type=%s", ntype)
                return SendOutcome(notification_id=None, status=None)

        notification_id = await self._repo.claim(
            notification_type=str(ntype),
            category=str(category),
            recipient=recipient,
            channel_preference=str(primary),
            fallback_allowed=fallback_allowed,
            idempotency_key=idempotency_key,
            user_id=user_id,
            variables=variables or {},
            reference_type=reference_type,
            reference_id=reference_id,
            otp_challenge_id=otp_challenge_id,
            campaign_id=campaign_id,
        )
        if notification_id is None:
            logger.info("notification_duplicate_suppressed key=%s", idempotency_key)
            return SendOutcome(notification_id=None, status=None)

        logger.info(
            "notification_created type=%s id=%s channel=%s", ntype, notification_id, primary
        )
        await self._session.commit()

        status = await self.deliver(notification_id) if deliver_now else None
        return SendOutcome(notification_id=notification_id, status=status)

    async def deliver(self, notification_id: uuid.UUID) -> DeliveryStatus:
        """Send one notification on WhatsApp. Safe to re-run.

        Re-running is safe because the attempt row is claimed against a unique
        index before the provider is called: a worker that crashes after Meta
        accepted the message cannot send it again, it can only observe that the
        attempt already exists.
        """
        notification = await self._repo.lock(notification_id)
        if notification is None:
            await self._session.rollback()
            return DeliveryStatus.FAILED

        # Already resolved by another worker or an earlier run.
        if notification["status"] in ("delivered", "read", "sent", "failed", "cancelled"):
            await self._session.commit()
            return DeliveryStatus(notification["status"])

        # Attempt numbering starts at 1 and increments per retry. The attempt
        # row's unique key includes it, so retry N cannot collide with retry N-1.
        attempt_number = int(notification["attempt_count"] or 0) + 1
        max_attempts = self._settings.notification_max_attempts

        await self._repo.set_status(notification_id, status="sending")
        await self._repo.set_attempt_count(notification_id, attempt_number)
        await self._session.commit()

        result = await self._attempt(
            notification, Channel.WHATSAPP, attempt_number=attempt_number
        )

        if result is not None and result.accepted:
            await self._finish(notification_id, result)
            return result.status

        # UNKNOWN stops here on purpose, and is deliberately NOT retried. Meta
        # may already have delivered it; retrying sends a second OTP and bills a
        # second conversation. The webhook or the reconciler resolves this.
        if result is not None and result.status is DeliveryStatus.UNKNOWN:
            await self._repo.set_status(notification_id, status="unknown")
            await self._session.commit()
            logger.info("notification_unknown id=%s awaiting_signal", notification_id)
            return DeliveryStatus.UNKNOWN

        # No provider registered, or the channel is unconfigured. Not a provider
        # failure, so it is not counted as an attempt against the retry budget.
        if result is None:
            await self._repo.set_attempt_count(notification_id, attempt_number - 1)
            await self._repo.set_status(
                notification_id,
                status="failed",
                failed=True,
                completed=True,
                last_error="whatsapp_unavailable",
            )
            await self._session.commit()
            logger.error("notification_no_provider id=%s", notification_id)
            return DeliveryStatus.FAILED

        # TRANSIENT — retry on WhatsApp itself, if there is budget left.
        if result.should_retry and attempt_number < max_attempts:
            delay = self._retry_delay(attempt_number)
            await self._repo.schedule_retry(
                notification_id,
                delay_seconds=delay,
                last_error=result.failure_reason or result.failure_code,
            )
            await self._session.commit()
            logger.info(
                "notification_retry_scheduled id=%s attempt=%s/%s in=%ss",
                notification_id,
                attempt_number,
                max_attempts,
                delay,
            )
            return DeliveryStatus.REQUESTED

        # PERMANENT, or transient with the budget exhausted. Either way this is
        # the end of the road — there is no other channel to try.
        await self._repo.set_status(
            notification_id,
            status="failed",
            failed=True,
            completed=True,
            last_error=result.failure_reason or result.failure_code,
        )
        await self._session.commit()
        logger.warning(
            "notification_failed id=%s attempts=%s class=%s code=%s",
            notification_id,
            attempt_number,
            result.error_class,
            result.failure_code,
        )
        return DeliveryStatus.FAILED

    def _retry_delay(self, attempt_number: int) -> int:
        """Delay before the next attempt.

        `notification_retry_delays_seconds` holds the gaps *after* attempt 1, so
        attempt 1 reads index 0. A ladder shorter than the attempt budget reuses
        its last entry rather than crashing on an index error.
        """
        delays = self._settings.notification_retry_delays_seconds or [300]
        index = min(attempt_number - 1, len(delays) - 1)
        return int(delays[max(index, 0)])

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    async def _attempt(
        self, notification: dict, channel: Channel, *, attempt_number: int
    ) -> ProviderResult | None:
        provider_key = _PROVIDER_FOR_CHANNEL.get(channel)
        provider = self._providers.get(channel)
        if provider is None or provider_key is None or not provider.configured:
            logger.info(
                "notification_channel_unavailable id=%s channel=%s",
                notification["id"],
                channel,
            )
            return None

        template = await self._repo.resolve_template(
            notification_type=notification["notification_type"],
            channel=channel,
            provider=provider_key,
            language=self._settings.whatsapp_default_language,
        )

        attempt_id = await self._repo.claim_attempt(
            notification["id"],
            provider=provider_key,
            channel=channel,
            attempt_number=attempt_number,
            template_name=template.provider_template_name if template else None,
        )
        if attempt_id is None:
            # Someone already sent on this channel — a worker retry after a
            # crash that happened *after* the provider call. Do not resend.
            logger.info(
                "notification_attempt_exists id=%s channel=%s — not resending",
                notification["id"],
                channel,
            )
            return None
        await self._session.commit()

        message = OutboundMessage(
            notification_type=NotificationType(notification["notification_type"]),
            category=Category(notification["category"]),
            recipient=notification["recipient"],
            variables=_variables(notification["variables"]),
            template=template,
            reference=str(notification["id"]),
        )

        logger.info(
            "notification_attempted id=%s channel=%s", notification["id"], channel
        )
        result = await provider.send(message)

        await self._repo.record_result(attempt_id, result)
        await self._session.commit()
        return result

    async def _finish(self, notification_id: uuid.UUID, result: ProviderResult) -> None:
        # 'sent' not 'delivered': the provider accepted it, which is not proof it
        # arrived. The webhook upgrades this when a real signal shows up.
        status = (
            DeliveryStatus.DELIVERED
            if result.status in (DeliveryStatus.DELIVERED, DeliveryStatus.READ)
            else DeliveryStatus.SENT
        )
        await self._repo.set_status(
            notification_id,
            status=str(status),
            delivered=status is DeliveryStatus.DELIVERED,
        )
        await self._session.commit()

    def _primary_channel(self, category: Category) -> Channel:
        """Always WhatsApp today, but read from settings rather than hardcoded.

        The per-category settings are kept so that adding a transport back is a
        config change plus a provider class. They are validated on the way out:
        a stray `OTP_PRIMARY_CHANNEL=sms` in the environment would otherwise
        route at a provider that is not registered, and the notification would
        sit unsendable instead of failing loudly.
        """
        if category is Category.OTP:
            configured = self._settings.otp_primary_channel
        elif category is Category.MARKETING:
            configured = self._settings.marketing_primary_channel
        else:
            configured = self._settings.transactional_primary_channel

        try:
            channel = Channel(configured)
        except ValueError:
            logger.error("channel_unknown configured=%s — routing to whatsapp", configured)
            return Channel.WHATSAPP
        if channel not in self._providers:
            logger.error(
                "channel_not_registered configured=%s — routing to whatsapp", configured
            )
            return Channel.WHATSAPP
        return channel

    def _fallback_allowed(self, category: Category) -> bool:
        """Never. WhatsApp is the only channel; there is nothing to fall back to.

        Kept as a method rather than deleted because `fallback_allowed` is still
        a column on every notification row, and a second transport would make
        this a real decision again.
        """
        return False

    async def _marketing_allowed(
        self, user_id: uuid.UUID, notification_type: NotificationType
    ) -> bool:
        """Check the opt-in that governs this notification type.

        Cart reminders and promotional blasts are gated by different columns, so
        the column comes from the type rather than the channel — opting out of
        promos should not silence "you left something in your basket", and vice
        versa.
        """
        column = consent_column_for(notification_type)
        if column is None:
            return True
        # The column name comes from `consent_column_for`, which returns one of
        # two hardcoded literals — never from a request. No user input reaches
        # this string.
        row = (
            await self._session.execute(
                text(
                    f"SELECT {column} FROM public.user_preferences WHERE user_id = :uid"  # noqa: S608
                ),
                {"uid": str(user_id)},
            )
        ).scalar_one_or_none()
        # No preferences row means no recorded opt-in, so marketing does not go.
        return bool(row)


def _variables(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        import json

        try:
            parsed = json.loads(raw or "{}")
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            return {}
    return {}
