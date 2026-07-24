from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.storefront.models.commerce import Payment, PaymentTransaction


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
        result = await self._session.execute(
            select(Payment)
            .where(Payment.order_id == order_id)
            .order_by(Payment.attempt_number.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

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

    async def mark_status(
        self,
        payment: Payment,
        *,
        status: str,
        provider_payment_id: str | None = None,
        method: str | None = None,
        failure_reason: str | None = None,
    ) -> None:
        payment.status = status
        if provider_payment_id:
            payment.provider_payment_id = provider_payment_id
        if method:
            payment.method = method
        if failure_reason:
            payment.failure_reason = failure_reason
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
