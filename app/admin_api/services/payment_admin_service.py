"""Read models for the admin payment reconciliation screen.

Deliberately read-only apart from triggering a reconcile. There is no
"mark as paid": the whole point of the rebuild is that payment state comes from
the provider, and an admin override would be a hole straight through that. An
admin who believes a payment settled can force a reconcile, which asks Razorpay
and applies the real answer through the same state machine as everything else.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class PaymentAttemptOut(BaseModel):
    id: uuid.UUID
    attempt_number: int
    provider: str
    provider_order_id: str | None
    provider_payment_id: str | None
    status: str
    method: str | None
    amount_paise: int
    currency: str
    failure_reason: str | None
    failure_code: str | None
    verified_at: datetime | None
    captured_at: datetime | None
    reconcile_attempts: int
    last_reconciled_at: datetime | None
    next_reconcile_at: datetime | None
    needs_admin_review: bool
    admin_review_reason: str | None
    created_at: datetime
    updated_at: datetime | None


class PaymentTimelineEntry(BaseModel):
    """One thing that happened, from either the payment log or the webhook log."""

    at: datetime
    kind: str
    detail: str
    source: str


class WebhookEventOut(BaseModel):
    id: uuid.UUID
    event_type: str
    provider_event_id: str | None
    signature_valid: bool
    processing_status: str
    error: str | None
    created_at: datetime
    processed_at: datetime | None


class RefundOut(BaseModel):
    id: uuid.UUID
    provider_refund_id: str | None
    amount_paise: int
    status: str
    provider_status: str | None
    reason: str | None
    created_at: datetime
    processed_at: datetime | None


class PaymentDetailOut(BaseModel):
    order_id: uuid.UUID
    order_number: int
    order_status: str
    order_payment_status: str
    grand_total_paise: int
    currency: str
    customer_email: str | None
    created_at: datetime
    attempts: list[PaymentAttemptOut]
    refunds: list[RefundOut]
    webhook_events: list[WebhookEventOut]
    timeline: list[PaymentTimelineEntry]


class PaymentQueueRow(BaseModel):
    order_id: uuid.UUID
    order_number: int
    payment_id: uuid.UUID
    attempt_number: int
    status: str
    order_status: str
    order_payment_status: str
    amount_paise: int
    provider_order_id: str | None
    provider_payment_id: str | None
    needs_admin_review: bool
    admin_review_reason: str | None
    reconcile_attempts: int
    created_at: datetime
    updated_at: datetime | None


class PaymentQueueOut(BaseModel):
    items: list[PaymentQueueRow]
    total: int


class PaymentAdminService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def queue(
        self, *, scope: str = "attention", page: int = 1, page_size: int = 25
    ) -> PaymentQueueOut:
        """Payments needing a human, or every unresolved payment.

        `scope`:
          attention  — flagged for review (duplicate capture, amount mismatch,
                       reconciliation exhausted)
          unresolved — anything not yet settled, including healthy in-flight ones
          all        — everything, newest first
        """
        where = {
            "attention": "p.needs_admin_review = TRUE",
            "unresolved": (
                "p.status IN ('created','pending','verification_required','authorized')"
            ),
            "all": "TRUE",
        }.get(scope, "p.needs_admin_review = TRUE")

        offset = max(0, (page - 1) * page_size)
        rows = (
            await self._session.execute(
                text(
                    f"""
                    SELECT p.id AS payment_id, p.attempt_number, p.status,
                           p.amount_paise, p.provider_order_id, p.provider_payment_id,
                           p.needs_admin_review, p.admin_review_reason,
                           p.reconcile_attempts, p.created_at, p.updated_at,
                           o.id AS order_id, o.order_number,
                           o.status AS order_status,
                           o.payment_status AS order_payment_status
                    FROM commerce.payments p
                    JOIN commerce.orders o ON o.id = p.order_id
                    WHERE {where}
                    ORDER BY p.needs_admin_review DESC, p.created_at DESC
                    LIMIT :lim OFFSET :off
                    """  # noqa: S608 - `where` is a fixed literal chosen above
                ),
                {"lim": page_size, "off": offset},
            )
        ).mappings().all()

        total = (
            await self._session.execute(
                text(
                    f"SELECT COUNT(*) FROM commerce.payments p WHERE {where}"  # noqa: S608
                )
            )
        ).scalar_one()

        return PaymentQueueOut(
            items=[PaymentQueueRow(**dict(r)) for r in rows], total=int(total)
        )

    async def detail(self, order_id: uuid.UUID) -> PaymentDetailOut | None:
        order = (
            await self._session.execute(
                text(
                    """
                    SELECT o.id, o.order_number, o.status, o.payment_status,
                           o.grand_total_paise, o.currency, o.created_at,
                           COALESCE(o.guest_email, u.email) AS customer_email
                    FROM commerce.orders o
                    LEFT JOIN identity.users u ON u.id = o.user_id
                    WHERE o.id = :oid
                    """
                ),
                {"oid": str(order_id)},
            )
        ).mappings().first()
        if order is None:
            return None

        attempts = (
            await self._session.execute(
                text(
                    """
                    SELECT id, attempt_number, provider, provider_order_id,
                           provider_payment_id, status, method, amount_paise, currency,
                           failure_reason, failure_code, verified_at, captured_at,
                           reconcile_attempts, last_reconciled_at, next_reconcile_at,
                           needs_admin_review, admin_review_reason, created_at, updated_at
                    FROM commerce.payments
                    WHERE order_id = :oid
                    ORDER BY attempt_number ASC
                    """
                ),
                {"oid": str(order_id)},
            )
        ).mappings().all()

        refunds = (
            await self._session.execute(
                text(
                    """
                    SELECT id, provider_refund_id, amount_paise, status, provider_status,
                           reason, created_at, processed_at
                    FROM commerce.refunds
                    WHERE order_id = :oid
                    ORDER BY created_at ASC
                    """
                ),
                {"oid": str(order_id)},
            )
        ).mappings().all()

        webhooks = (
            await self._session.execute(
                text(
                    """
                    SELECT id, event_type, provider_event_id, signature_valid,
                           processing_status, error, created_at, processed_at
                    FROM commerce.webhook_events
                    WHERE order_id = :oid
                       OR provider_order_id IN (
                            SELECT provider_order_id FROM commerce.payments
                            WHERE order_id = :oid AND provider_order_id IS NOT NULL
                          )
                    ORDER BY created_at DESC
                    LIMIT 100
                    """
                ),
                {"oid": str(order_id)},
            )
        ).mappings().all()

        timeline = await self._timeline(order_id)

        return PaymentDetailOut(
            order_id=order["id"],
            order_number=order["order_number"],
            order_status=order["status"],
            order_payment_status=order["payment_status"],
            grand_total_paise=order["grand_total_paise"],
            currency=order["currency"],
            customer_email=order["customer_email"],
            created_at=order["created_at"],
            attempts=[PaymentAttemptOut(**dict(a)) for a in attempts],
            refunds=[RefundOut(**dict(r)) for r in refunds],
            webhook_events=[WebhookEventOut(**dict(w)) for w in webhooks],
            timeline=timeline,
        )

    async def _timeline(self, order_id: uuid.UUID) -> list[PaymentTimelineEntry]:
        """Merge payment transactions, order history and webhooks into one story."""
        rows = (
            await self._session.execute(
                text(
                    """
                    SELECT t.created_at AS at,
                           'payment' AS kind,
                           t.transaction_type || ' · ' || t.status AS detail,
                           COALESCE(t.raw_payload->>'source', 'system') AS source
                    FROM commerce.payment_transactions t
                    JOIN commerce.payments p ON p.id = t.payment_id
                    WHERE p.order_id = :oid

                    UNION ALL

                    SELECT h.created_at AS at,
                           'order' AS kind,
                           COALESCE(h.from_status, 'new') || ' -> ' || h.to_status AS detail,
                           h.changed_by_type AS source
                    FROM commerce.order_status_history h
                    WHERE h.order_id = :oid

                    UNION ALL

                    SELECT w.created_at AS at,
                           'webhook' AS kind,
                           w.event_type || ' · ' || w.processing_status AS detail,
                           'razorpay' AS source
                    FROM commerce.webhook_events w
                    WHERE w.order_id = :oid

                    ORDER BY at ASC
                    LIMIT 200
                    """
                ),
                {"oid": str(order_id)},
            )
        ).mappings().all()
        return [PaymentTimelineEntry(**dict(r)) for r in rows]
