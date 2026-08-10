from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.storefront.models.commerce import (
    NotificationLog,
    Payment,
    PaymentTransaction,
    WebhookEvent,
)


class PaymentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def next_attempt_number(self, order_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.coalesce(func.max(Payment.attempt_number), 0)).where(
                Payment.order_id == order_id
            )
        )
        return int(result.scalar_one()) + 1

    async def create_payment(self, payment: Payment) -> Payment:
        self._session.add(payment)
        await self._session.flush()
        await self._session.refresh(payment)
        return payment

    async def get_by_order(self, order_id: uuid.UUID) -> Payment | None:
        """The most recent attempt for an order."""
        result = await self._session.execute(
            select(Payment)
            .where(Payment.order_id == order_id)
            .order_by(Payment.attempt_number.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_by_order(self, order_id: uuid.UUID) -> list[Payment]:
        result = await self._session.execute(
            select(Payment)
            .where(Payment.order_id == order_id)
            .order_by(Payment.attempt_number.asc())
        )
        return list(result.scalars().all())

    async def get_by_provider_order_id(self, provider_order_id: str) -> Payment | None:
        result = await self._session.execute(
            select(Payment).where(Payment.provider_order_id == provider_order_id)
        )
        return result.scalar_one_or_none()

    async def get_by_provider_payment_id(self, provider_payment_id: str) -> Payment | None:
        result = await self._session.execute(
            select(Payment).where(Payment.provider_payment_id == provider_payment_id)
        )
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------ #
    # Locking
    # ------------------------------------------------------------------ #
    async def lock_by_provider_order_id(self, provider_order_id: str) -> Payment | None:
        """Take a row lock before deciding whether to settle a payment.

        Without this the capture path is check-then-act: two concurrent
        deliveries (webhook + browser callback, or a webhook redelivery) both
        read ``status != 'captured'`` and both go on to commit inventory, record
        coupon usage and send an email. The lock serialises them so the second
        one sees the first one's result.
        """
        result = await self._session.execute(
            select(Payment)
            .where(Payment.provider_order_id == provider_order_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def lock_by_id(self, payment_id: uuid.UUID) -> Payment | None:
        result = await self._session.execute(
            select(Payment).where(Payment.id == payment_id).with_for_update()
        )
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------ #
    # Mutation
    # ------------------------------------------------------------------ #
    async def mark_status(
        self,
        payment: Payment,
        *,
        status: str,
        provider_payment_id: str | None = None,
        method: str | None = None,
        failure_reason: str | None = None,
        failure_code: str | None = None,
        verified_at: datetime | None = None,
        captured_at: datetime | None = None,
    ) -> None:
        payment.status = status
        if provider_payment_id:
            payment.provider_payment_id = provider_payment_id
        if method:
            payment.method = method
        if failure_reason:
            payment.failure_reason = failure_reason
        if failure_code:
            payment.failure_code = failure_code
        if verified_at is not None:
            payment.verified_at = verified_at
        if captured_at is not None:
            payment.captured_at = captured_at
        await self._session.flush()

    async def flag_for_review(self, payment: Payment, *, reason: str) -> None:
        payment.needs_admin_review = True
        payment.admin_review_reason = reason
        await self._session.flush()

    async def schedule_reconcile(
        self, payment: Payment, *, next_at: datetime | None, attempts: int | None = None
    ) -> None:
        payment.next_reconcile_at = next_at
        if attempts is not None:
            payment.reconcile_attempts = attempts
        await self._session.flush()

    async def add_transaction(
        self,
        payment_id: uuid.UUID,
        *,
        transaction_type: str,
        status: str,
        amount_paise: int,
        provider_transaction_id: str | None = None,
        raw_payload: dict[str, Any] | None = None,
    ) -> None:
        self._session.add(
            PaymentTransaction(
                payment_id=payment_id,
                transaction_type=transaction_type,
                status=status,
                amount_paise=amount_paise,
                provider_transaction_id=provider_transaction_id,
                raw_payload=raw_payload or {},
            )
        )
        await self._session.flush()

    async def list_transactions(self, payment_id: uuid.UUID) -> list[PaymentTransaction]:
        result = await self._session.execute(
            select(PaymentTransaction)
            .where(PaymentTransaction.payment_id == payment_id)
            .order_by(PaymentTransaction.created_at.asc())
        )
        return list(result.scalars().all())


class WebhookEventRepository:
    """Provider webhook deliveries, deduplicated by the database."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim(
        self,
        *,
        provider: str,
        provider_event_id: str | None,
        event_type: str,
        signature_valid: bool,
        payload: dict[str, Any],
        provider_order_id: str | None = None,
        provider_payment_id: str | None = None,
    ) -> uuid.UUID | None:
        """Insert the delivery, or return ``None`` if it was already claimed.

        ``ON CONFLICT DO NOTHING`` against the partial unique index is the
        idempotency guarantee: two concurrent redeliveries race at the database,
        exactly one wins the insert, and the loser is told to do nothing. An
        application-level "have we seen this?" SELECT cannot make that promise.

        A delivery with no event id (malformed, or an unsigned probe) is always
        inserted — it is recorded for forensics but never dedup-keyed.
        """
        result = await self._session.execute(
            text(
                """
                INSERT INTO commerce.webhook_events
                    (provider, provider_event_id, event_type, signature_valid,
                     payload, provider_order_id, provider_payment_id, processing_status)
                VALUES
                    (:provider, :event_id, :event_type, :sig_valid,
                     CAST(:payload AS JSONB), :rp_order, :rp_payment, 'received')
                ON CONFLICT (provider, provider_event_id)
                    WHERE provider_event_id IS NOT NULL
                DO NOTHING
                RETURNING id
                """
            ),
            {
                "provider": provider,
                "event_id": provider_event_id,
                "event_type": event_type,
                "sig_valid": signature_valid,
                "payload": _json(payload),
                "rp_order": provider_order_id,
                "rp_payment": provider_payment_id,
            },
        )
        return result.scalar_one_or_none()

    async def finish(
        self,
        event_id: uuid.UUID,
        *,
        processing_status: str,
        payment_id: uuid.UUID | None = None,
        order_id: uuid.UUID | None = None,
        error: str | None = None,
    ) -> None:
        await self._session.execute(
            text(
                """
                UPDATE commerce.webhook_events
                SET processing_status = :status,
                    payment_id = COALESCE(:payment_id, payment_id),
                    order_id = COALESCE(:order_id, order_id),
                    error = :error,
                    processed_at = now()
                WHERE id = :id
                """
            ),
            {
                "id": str(event_id),
                "status": processing_status,
                "payment_id": str(payment_id) if payment_id else None,
                "order_id": str(order_id) if order_id else None,
                "error": error,
            },
        )
        await self._session.flush()

    async def list_for_order(self, order_id: uuid.UUID, limit: int = 50) -> list[WebhookEvent]:
        result = await self._session.execute(
            select(WebhookEvent)
            .where(WebhookEvent.order_id == order_id)
            .order_by(WebhookEvent.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


class NotificationLogRepository:
    """Send-exactly-once guard for order notifications."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim(
        self,
        *,
        order_id: uuid.UUID,
        kind: str,
        channel: str = "email",
        recipient: str | None = None,
    ) -> uuid.UUID | None:
        """Reserve the right to send. ``None`` means someone already sent it.

        Claim first, send second: if the process dies between the two, the
        customer misses a mail — which is recoverable. Doing it the other way
        round risks sending twice, which is not.
        """
        result = await self._session.execute(
            text(
                """
                INSERT INTO commerce.notification_log
                    (order_id, kind, channel, recipient, status)
                VALUES (:order_id, :kind, :channel, :recipient, 'sent')
                ON CONFLICT (order_id, kind, channel) WHERE status = 'sent'
                DO NOTHING
                RETURNING id
                """
            ),
            {
                "order_id": str(order_id),
                "kind": kind,
                "channel": channel,
                "recipient": recipient,
            },
        )
        return result.scalar_one_or_none()

    async def mark_failed(self, log_id: uuid.UUID, *, error: str) -> None:
        """Release the slot so a later attempt (or the reconciler) can retry."""
        await self._session.execute(
            text(
                """
                UPDATE commerce.notification_log
                SET status = 'failed', error = :error
                WHERE id = :id
                """
            ),
            {"id": str(log_id), "error": error[:500]},
        )
        await self._session.flush()


def _json(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, default=str)
