"""Razorpay refunds initiated from the admin panel.

Refunding is the one admin action that moves real money, so it is deliberately
narrow: only a captured payment can be refunded, never more than what is left
un-refunded, and the row in ``commerce.refunds`` is written before the order is
marked refunded so a crash mid-flight leaves an auditable record.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_api.core.exceptions import AppError, NotFoundError, ValidationError
from app.admin_api.repositories.audit_repository import AuditRepository
from app.config import settings
from app.events.bus import get_event_bus
from app.events.types import EventType
from app.storefront.lib.razorpay_client import PaymentGatewayError, RazorpayClient

logger = logging.getLogger(__name__)


class RefundService:
    def __init__(
        self,
        session: AsyncSession,
        razorpay: RazorpayClient | None = None,
        notifications_factory=None,
    ) -> None:
        self._session = session
        self._audit = AuditRepository(session)
        self._razorpay = razorpay or RazorpayClient(settings)
        self._notifications_factory = notifications_factory

    async def refund_order(
        self,
        order_id: uuid.UUID,
        *,
        admin_id: uuid.UUID,
        amount_paise: int | None = None,
        reason: str | None = None,
        ip_address: str | None = None,
    ) -> dict:
        order_status = await self._order_status(order_id)
        payment = await self._captured_payment(order_id)
        already_refunded = await self._refunded_total(payment["id"])
        refundable = payment["amount_paise"] - already_refunded

        if refundable <= 0:
            raise ValidationError(
                "This payment has already been refunded in full.",
                code="already_refunded",
            )
        amount = amount_paise if amount_paise is not None else refundable
        if amount <= 0:
            raise ValidationError("Refund amount must be greater than zero.")
        if amount > refundable:
            raise ValidationError(
                f"Only {refundable} paise remain refundable on this payment.",
                code="refund_exceeds_payment",
            )

        try:
            result = await self._razorpay.create_refund(
                payment["provider_payment_id"], amount_paise=amount
            )
        except PaymentGatewayError as exc:
            raise AppError(exc.message, code=exc.code, status_code=502) from exc

        refund_id = await self._record_refund(
            payment_id=payment["id"],
            order_id=order_id,
            provider_refund_id=result.get("id"),
            amount_paise=amount,
            status="processed" if result.get("status") == "processed" else "pending",
            reason=reason,
            admin_id=admin_id,
        )

        full_refund = amount >= refundable
        await self._session.execute(
            text(
                """
                UPDATE commerce.orders
                SET payment_status = :payment_status,
                    status = CASE WHEN :full THEN 'refunded' ELSE status END
                WHERE id = :order_id
                """
            ),
            {
                "payment_status": "refunded" if full_refund else "partially_refunded",
                "full": full_refund,
                "order_id": order_id,
            },
        )
        await self._session.execute(
            text(
                """
                INSERT INTO commerce.order_status_history
                    (order_id, from_status, to_status, changed_by_type, changed_by_id, reason)
                VALUES (:order_id, :from_status, :to_status, 'admin', :admin_id, :reason)
                """
            ),
            {
                "order_id": order_id,
                "from_status": order_status,
                "to_status": "refunded" if full_refund else "partially_refunded",
                "admin_id": admin_id,
                "reason": reason or "Refund issued by admin",
            },
        )

        await self._audit.log(
            admin_id=admin_id,
            entity_type="order",
            entity_id=order_id,
            action="refund",
            new_data={"amount_paise": amount, "provider_refund_id": result.get("id")},
            domain="order",
            ip_address=ip_address,
        )
        await self._session.commit()

        await get_event_bus().publish(
            EventType.REFUND_INITIATED,
            {
                "order_id": str(order_id),
                "refund_id": str(refund_id),
                "amount_paise": amount,
                "full_refund": full_refund,
            },
        )

        await self._notify_refund(
            order_id=order_id,
            amount_paise=amount,
            settled=result.get("status") == "processed",
        )

        return {
            "refund_id": refund_id,
            "provider_refund_id": result.get("id"),
            "amount_paise": amount,
            "status": result.get("status", "pending"),
            "full_refund": full_refund,
        }

    async def _notify_refund(
        self, *, order_id: uuid.UUID, amount_paise: int, settled: bool
    ) -> None:
        if self._notifications_factory is None:
            return
        from app.notifications.order_notifier import OrderNotifier

        order_number = (
            await self._session.execute(
                text("SELECT order_number FROM commerce.orders WHERE id = :oid"),
                {"oid": order_id},
            )
        ).scalar_one_or_none()
        if order_number is None:
            return

        notifier = OrderNotifier(
            self._session,
            settings,
            notifications=self._notifications_factory(self._session),
        )
        if settled:
            await notifier.refund_completed(
                order_id=order_id, order_number=order_number, amount_paise=amount_paise
            )
        else:
            await notifier.refund_initiated(
                order_id=order_id, order_number=order_number, amount_paise=amount_paise
            )

    async def _order_status(self, order_id: uuid.UUID) -> str:
        result = await self._session.execute(
            text("SELECT status FROM commerce.orders WHERE id = :order_id"),
            {"order_id": order_id},
        )
        status = result.scalar_one_or_none()
        if status is None:
            raise NotFoundError("Order not found")
        return str(status)

    async def _captured_payment(self, order_id: uuid.UUID) -> dict:
        result = await self._session.execute(
            text(
                """
                SELECT id, amount_paise, provider_payment_id, status
                FROM commerce.payments
                WHERE order_id = :order_id AND status = 'captured'
                ORDER BY attempt_number DESC
                LIMIT 1
                """
            ),
            {"order_id": order_id},
        )
        row = result.mappings().first()
        if not row:
            raise NotFoundError(
                "No captured payment found for this order.", code="no_captured_payment"
            )
        if not row["provider_payment_id"]:
            raise ValidationError(
                "This payment has no Razorpay payment id to refund against.",
                code="missing_provider_payment_id",
            )
        return dict(row)

    async def _refunded_total(self, payment_id: uuid.UUID) -> int:
        result = await self._session.execute(
            text(
                """
                SELECT COALESCE(SUM(amount_paise), 0) AS total
                FROM commerce.refunds
                WHERE payment_id = :payment_id AND status <> 'failed'
                """
            ),
            {"payment_id": payment_id},
        )
        return int(result.scalar_one() or 0)

    async def _record_refund(
        self,
        *,
        payment_id: uuid.UUID,
        order_id: uuid.UUID,
        provider_refund_id: str | None,
        amount_paise: int,
        status: str,
        reason: str | None,
        admin_id: uuid.UUID,
    ) -> uuid.UUID:
        result = await self._session.execute(
            text(
                """
                INSERT INTO commerce.refunds (
                  payment_id, order_id, provider_refund_id, amount_paise,
                  status, reason, initiated_by_admin_id
                ) VALUES (
                  :payment_id, :order_id, :provider_refund_id, :amount_paise,
                  :status, :reason, :admin_id
                )
                RETURNING id
                """
            ),
            {
                "payment_id": payment_id,
                "order_id": order_id,
                "provider_refund_id": provider_refund_id,
                "amount_paise": amount_paise,
                "status": status,
                "reason": reason,
                "admin_id": admin_id,
            },
        )
        return result.scalar_one()
