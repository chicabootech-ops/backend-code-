"""Resolving payments whose callback or webhook never arrived.

The previous version of this file cancelled any order that had sat `pending` for
thirty minutes. It never asked Razorpay anything. That is precisely backwards: a
delayed webhook and an abandoned cart look identical from the database alone, so
time-based cancellation quietly voids orders that customers have already paid
for, and releases the stock underneath them.

The rule here is: **never conclude anything about a payment without asking the
provider.** An order is only cancelled once Razorpay has confirmed there is no
payment against it. Anything we cannot resolve is escalated to a human rather
than guessed at.

Backoff is deliberately unhurried — 2m, 5m, 15m, 45m, 2h, 6h — because the common
case is a webhook that is merely late, and hammering the API helps nobody.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.storefront.lib.razorpay_client import (
    PaymentGatewayError,
    PaymentGatewayTimeout,
    RazorpayClient,
)
from app.storefront.repositories.order_repository import OrderRepository
from app.storefront.repositories.payment_repository import PaymentRepository
from app.storefront.services.inventory_service import InventoryService
from app.storefront.services.payment_state import (
    SETTLED,
    PaymentStatus,
    TransitionSource,
    from_provider_status,
)

logger = logging.getLogger(__name__)

#: Delay before each successive reconciliation attempt.
BACKOFF_SCHEDULE = [
    timedelta(minutes=2),
    timedelta(minutes=5),
    timedelta(minutes=15),
    timedelta(minutes=45),
    timedelta(hours=2),
    timedelta(hours=6),
]

#: After this many fruitless attempts, stop guessing and ask a human.
MAX_ATTEMPTS = len(BACKOFF_SCHEDULE)

#: An unresolved attempt older than this with no provider-side payment is a
#: genuinely abandoned checkout. Generous on purpose: the cost of holding stock
#: a little longer is far lower than the cost of voiding a paid order.
ABANDON_AFTER = timedelta(hours=2)


class ReconciliationService:
    def __init__(
        self,
        session: AsyncSession,
        razorpay: RazorpayClient | None = None,
        *,
        payment_service: Any | None = None,
    ) -> None:
        self._session = session
        self._razorpay = razorpay
        self._inventory = InventoryService(session)
        self._payments = PaymentRepository(session)
        self._orders = OrderRepository(session)
        #: Injected to reuse the state machine + settlement side effects.
        self._payment_service = payment_service

    # ------------------------------------------------------------------ #
    # Entry point
    # ------------------------------------------------------------------ #
    async def run(self, *, limit: int = 50, stale_minutes: int | None = None) -> dict:
        """One reconciliation sweep. Safe to run repeatedly and concurrently."""
        released = await self._release_expired_reservations()
        due = await self._claim_due_payments(limit=limit)

        resolved = 0
        escalated = 0
        abandoned = 0
        unchanged = 0

        for payment_id in due:
            outcome = await self._reconcile_one(payment_id)
            if outcome == "resolved":
                resolved += 1
            elif outcome == "escalated":
                escalated += 1
            elif outcome == "abandoned":
                abandoned += 1
            else:
                unchanged += 1

        return {
            "expired_reservations_released": released,
            "payments_checked": len(due),
            "payments_resolved": resolved,
            "payments_escalated": escalated,
            "orders_abandoned": abandoned,
            "payments_unchanged": unchanged,
        }

    # ------------------------------------------------------------------ #
    # Steps
    # ------------------------------------------------------------------ #
    async def _release_expired_reservations(self) -> int:
        """Give expired stock back.

        Note what this does *not* touch: it releases the reservation but leaves
        the order and the payment alone. If the payment later turns out to have
        been captured, the order survives and lands in the admin queue via
        Case 24 handling rather than having been silently cancelled.
        """
        released = await self._inventory.release_expired()
        await self._session.commit()
        return released

    async def _claim_due_payments(self, *, limit: int) -> list[uuid.UUID]:
        """Unresolved attempts whose backoff has elapsed.

        ``SKIP LOCKED`` lets several app instances sweep at once without two of
        them reconciling the same payment.
        """
        rows = (
            await self._session.execute(
                text(
                    """
                    SELECT id FROM commerce.payments
                    WHERE status IN ('created', 'pending', 'verification_required', 'authorized')
                      AND needs_admin_review = FALSE
                      AND (next_reconcile_at IS NULL OR next_reconcile_at <= now())
                    ORDER BY next_reconcile_at NULLS FIRST
                    LIMIT :lim
                    FOR UPDATE SKIP LOCKED
                    """
                ),
                {"lim": limit},
            )
        ).scalars().all()
        ids = [r if isinstance(r, uuid.UUID) else uuid.UUID(str(r)) for r in rows]
        await self._session.commit()
        return ids

    async def _reconcile_one(self, payment_id: uuid.UUID) -> str:
        payment = await self._payments.lock_by_id(payment_id)
        if payment is None:
            await self._session.rollback()
            return "unchanged"

        # Someone settled it between the claim and now.
        if PaymentStatus(payment.status) in SETTLED:
            await self._payments.schedule_reconcile(payment, next_at=None)
            await self._session.commit()
            return "unchanged"

        order = await self._orders.get_by_id(payment.order_id)
        provider_order_id = payment.provider_order_id
        attempts = int(payment.reconcile_attempts or 0) + 1

        if order is None or not provider_order_id:
            await self._escalate(payment, "Payment has no order or no provider order id")
            await self._session.commit()
            return "escalated"

        # Release the lock before any network call — never hold a row lock across
        # an external request.
        await self._session.commit()

        try:
            entities = await self._razorpay.fetch_order_payments(provider_order_id)
        except PaymentGatewayTimeout:
            logger.info(
                "payment_reconcile_deferred order=%s attempt=%s reason=gateway_timeout",
                order.order_number,
                attempts,
            )
            await self._reschedule(payment_id, attempts)
            return "unchanged"
        except PaymentGatewayError as exc:
            logger.warning(
                "payment_reconcile_error order=%s attempt=%s error=%s",
                order.order_number,
                attempts,
                exc.code,
            )
            await self._reschedule(payment_id, attempts)
            return "unchanged"

        return await self._apply_reconciliation(payment_id, entities, attempts)

    async def _apply_reconciliation(
        self, payment_id: uuid.UUID, entities: list[dict[str, Any]], attempts: int
    ) -> str:
        payment = await self._payments.lock_by_id(payment_id)
        if payment is None:
            await self._session.rollback()
            return "unchanged"
        order = await self._orders.get_by_id(payment.order_id)
        if order is None:
            await self._session.rollback()
            return "unchanged"

        # Prefer the most advanced payment Razorpay knows about: if any attempt
        # against this order captured, that is the truth that matters.
        best = _most_advanced(entities)

        if best is None:
            # Razorpay has no payment at all for this order. Only now is it safe
            # to call this abandoned — and only once it is genuinely old.
            age = datetime.now(UTC) - _aware(payment.created_at)
            if age >= ABANDON_AFTER:
                await self._abandon(order, payment)
                await self._session.commit()
                logger.info(
                    "payment_reconciled order=%s outcome=abandoned (provider has no payment)",
                    order.order_number,
                )
                return "abandoned"
            await self._payments.schedule_reconcile(
                payment, next_at=_next_at(attempts), attempts=attempts
            )
            await self._session.commit()
            return "unchanged"

        target = from_provider_status(best.get("status"))
        if target is None:
            await self._payments.schedule_reconcile(
                payment, next_at=_next_at(attempts), attempts=attempts
            )
            await self._session.commit()
            return "unchanged"

        settled = await self._payment_service._apply_provider_entity(  # noqa: SLF001
            order, payment, best, source=TransitionSource.PROVIDER_FETCH
        )
        payment.last_reconciled_at = datetime.now(UTC)

        if PaymentStatus(payment.status) in SETTLED or PaymentStatus(payment.status) in (
            PaymentStatus.FAILED,
            PaymentStatus.CANCELLED,
            PaymentStatus.EXPIRED,
        ):
            await self._payments.schedule_reconcile(payment, next_at=None, attempts=attempts)
            await self._session.commit()
            logger.info(
                "payment_reconciled order=%s outcome=%s attempts=%s",
                order.order_number,
                payment.status,
                attempts,
            )
            if settled:
                await self._payment_service._after_settlement(order)  # noqa: SLF001
            return "resolved"

        if attempts >= MAX_ATTEMPTS:
            await self._escalate(
                payment,
                f"Unresolved after {attempts} reconciliation attempts; provider status "
                f"was {best.get('status')!r}",
            )
            await self._session.commit()
            logger.error(
                "payment_reconcile_escalated order=%s attempts=%s", order.order_number, attempts
            )
            return "escalated"

        await self._payments.schedule_reconcile(
            payment, next_at=_next_at(attempts), attempts=attempts
        )
        await self._session.commit()
        return "unchanged"

    # ------------------------------------------------------------------ #
    # Outcomes
    # ------------------------------------------------------------------ #
    async def _abandon(self, order, payment) -> None:
        """Cancel a checkout the provider confirms was never paid."""
        await self._payments.mark_status(payment, status=str(PaymentStatus.CANCELLED))
        await self._payments.schedule_reconcile(payment, next_at=None)
        await self._inventory.release(order.id)
        await self._session.execute(
            text(
                """
                UPDATE commerce.orders
                SET status = 'cancelled',
                    payment_status = 'failed',
                    cancelled_at = now(),
                    cancellation_reason = 'Auto-cancelled: provider confirms no payment was made'
                WHERE id = :id AND status = 'pending'
                """
            ),
            {"id": str(order.id)},
        )
        await self._session.execute(
            text(
                """
                INSERT INTO commerce.order_status_history
                    (order_id, from_status, to_status, changed_by_type, reason)
                VALUES (:id, 'pending', 'cancelled', 'system',
                        'Auto-cancelled: provider confirms no payment was made')
                """
            ),
            {"id": str(order.id)},
        )

    async def _escalate(self, payment, reason: str) -> None:
        await self._payments.flag_for_review(payment, reason=reason)
        await self._payments.schedule_reconcile(payment, next_at=None)

    async def _reschedule(self, payment_id: uuid.UUID, attempts: int) -> None:
        payment = await self._payments.lock_by_id(payment_id)
        if payment is None:
            await self._session.rollback()
            return
        if attempts >= MAX_ATTEMPTS:
            await self._escalate(
                payment, f"Provider unreachable after {attempts} reconciliation attempts"
            )
        else:
            await self._payments.schedule_reconcile(
                payment, next_at=_next_at(attempts), attempts=attempts
            )
        payment.last_reconciled_at = datetime.now(UTC)
        await self._session.commit()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
#: Higher wins when Razorpay reports several payments for one order.
_RANK = {"failed": 0, "created": 1, "pending": 1, "authorized": 2, "captured": 3, "refunded": 4}


def _most_advanced(entities: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not entities:
        return None
    return max(entities, key=lambda e: _RANK.get(str(e.get("status", "")).lower(), 0))


def _next_at(attempts: int) -> datetime:
    index = min(max(attempts, 1), len(BACKOFF_SCHEDULE)) - 1
    return datetime.now(UTC) + BACKOFF_SCHEDULE[index]


def _aware(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    return value if value.tzinfo else value.replace(tzinfo=UTC)
