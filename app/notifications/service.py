"""NotificationService — the only thing business logic talks to.

Callers say *what happened*, not *how to tell anyone*:

    await notifications.send(
        NotificationType.ORDER_CONFIRMED,
        recipient="+919876543210",
        variables={"order_number": "1042", "total": "Rs. 1,499"},
        idempotency_key=f"order:{order_id}:ORDER_CONFIRMED",
    )

Everything else — which channel, which template, whether the customer consented,
whether this was already sent, and whether a failure earns an SMS — is decided
here.

The fallback rule, stated once because it is the part most easily got wrong:

    delivered/accepted  -> stop
    PERMANENT failure   -> try the fallback channel, exactly once
    TRANSIENT failure   -> leave for retry on the same channel
    UNKNOWN             -> stop and reconcile; the message may have arrived

An UNKNOWN never becomes an SMS on the spot. That is the whole reason a WhatsApp
timeout does not produce two OTP messages.
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
)

logger = logging.getLogger(__name__)

#: Which provider serves which channel.
_PROVIDER_FOR_CHANNEL = {
    Channel.WHATSAPP: Provider.WHATSAPP,
    Channel.SMS: Provider.MESSAGE_CENTRAL,
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
        # gated; marketing requires an explicit opt-in.
        if category is Category.MARKETING and user_id is not None:
            if not await self._marketing_allowed(user_id, primary):
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
        """Run the channel ladder for one notification. Safe to re-run."""
        notification = await self._repo.lock(notification_id)
        if notification is None:
            await self._session.rollback()
            return DeliveryStatus.FAILED

        # Already resolved by another worker or an earlier run.
        if notification["status"] in ("delivered", "read", "sent", "failed", "cancelled"):
            await self._session.commit()
            return DeliveryStatus(notification["status"])

        await self._repo.set_status(notification_id, status="sending")
        await self._session.commit()

        primary = Channel(notification["channel_preference"])
        result = await self._attempt(notification, primary, attempt_number=1)

        if result is not None and result.accepted:
            await self._finish(notification_id, result)
            return result.status

        # UNKNOWN stops here on purpose. Reconciliation or the webhook resolves
        # it; sending the fallback now is how duplicates happen.
        if result is not None and result.status is DeliveryStatus.UNKNOWN:
            await self._repo.set_status(notification_id, status="unknown")
            await self._session.commit()
            logger.info("notification_unknown id=%s awaiting_signal", notification_id)
            return DeliveryStatus.UNKNOWN

        if result is not None and result.should_retry:
            # Leave pending; the worker picks it up again with backoff.
            await self._repo.set_status(notification_id, status="pending")
            await self._session.commit()
            return DeliveryStatus.REQUESTED

        # Definitive failure on the primary channel.
        fallback = self._fallback_channel(Category(notification["category"]))
        if not notification["fallback_allowed"] or fallback is None or fallback == primary:
            await self._repo.set_status(
                notification_id, status="failed", failed=True, completed=True
            )
            await self._session.commit()
            logger.warning("notification_failed id=%s no_fallback", notification_id)
            return DeliveryStatus.FAILED

        logger.info(
            "sms_fallback_triggered id=%s from=%s to=%s", notification_id, primary, fallback
        )
        fallback_result = await self._attempt(notification, fallback, attempt_number=1)

        if fallback_result is not None and fallback_result.accepted:
            await self._finish(notification_id, fallback_result)
            return fallback_result.status

        status = (
            DeliveryStatus.UNKNOWN
            if fallback_result is not None
            and fallback_result.status is DeliveryStatus.UNKNOWN
            else DeliveryStatus.FAILED
        )
        await self._repo.set_status(
            notification_id,
            status=str(status),
            failed=status is DeliveryStatus.FAILED,
            completed=True,
        )
        await self._session.commit()
        logger.warning("notification_completed id=%s status=%s", notification_id, status)
        return status

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
        if category is Category.OTP:
            return Channel(self._settings.otp_primary_channel)
        if category is Category.MARKETING:
            return Channel(self._settings.marketing_primary_channel)
        return Channel(self._settings.transactional_primary_channel)

    def _fallback_channel(self, category: Category) -> Channel | None:
        if category is Category.OTP:
            if not self._settings.otp_whatsapp_fallback_enabled:
                return None
            return Channel(self._settings.otp_fallback_channel)
        if category is Category.MARKETING:
            # Marketing never silently becomes SMS. A campaign that wants it
            # passes allow_fallback=True explicitly.
            return Channel.SMS if self._settings.marketing_sms_fallback else None
        return Channel(self._settings.transactional_fallback_channel)

    def _fallback_allowed(self, category: Category) -> bool:
        if category is Category.MARKETING:
            return bool(self._settings.marketing_sms_fallback)
        if category is Category.OTP:
            return bool(self._settings.otp_whatsapp_fallback_enabled)
        return True

    async def _marketing_allowed(self, user_id: uuid.UUID, channel: Channel) -> bool:
        column = {
            Channel.WHATSAPP: "whatsapp_marketing",
            Channel.SMS: "sms_marketing",
            Channel.EMAIL: "email_marketing",
        }.get(channel)
        if column is None:
            return False
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
