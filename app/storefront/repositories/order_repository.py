from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.storefront.models.commerce import (
    Order,
    OrderItem,
    OrderStatusHistory,
    OrderTaxLine,
)


class OrderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_order(self, order: Order) -> Order:
        self._session.add(order)
        await self._session.flush()
        await self._session.refresh(order)
        return order

    async def add_items(self, items: list[OrderItem]) -> None:
        self._session.add_all(items)
        await self._session.flush()

    async def add_tax_lines(self, tax_lines: list[OrderTaxLine]) -> None:
        if tax_lines:
            self._session.add_all(tax_lines)
            await self._session.flush()

    async def add_status_history(
        self,
        order_id: uuid.UUID,
        *,
        to_status: str,
        from_status: str | None,
        changed_by_type: str = "system",
        changed_by_id: uuid.UUID | None = None,
        reason: str | None = None,
    ) -> None:
        self._session.add(
            OrderStatusHistory(
                order_id=order_id,
                from_status=from_status,
                to_status=to_status,
                changed_by_type=changed_by_type,
                changed_by_id=changed_by_id,
                reason=reason,
            )
        )
        await self._session.flush()

    async def get_by_id(
        self, order_id: uuid.UUID, *, user_id: uuid.UUID | None = None
    ) -> Order | None:
        stmt = select(Order).where(Order.id == order_id)
        if user_id is not None:
            stmt = stmt.where(Order.user_id == user_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_items(self, order_id: uuid.UUID) -> list[OrderItem]:
        result = await self._session.execute(
            select(OrderItem).where(OrderItem.order_id == order_id).order_by(OrderItem.created_at)
        )
        return list(result.scalars().all())

    async def get_tax_lines(self, order_id: uuid.UUID) -> list[OrderTaxLine]:
        result = await self._session.execute(
            select(OrderTaxLine).where(OrderTaxLine.order_id == order_id)
        )
        return list(result.scalars().all())

    async def list_by_user(
        self, user_id: uuid.UUID, *, page: int = 1, page_size: int = 20
    ) -> tuple[list[Order], int]:
        base = select(Order).where(Order.user_id == user_id)
        total = (
            await self._session.execute(
                select(func.count()).select_from(base.subquery())
            )
        ).scalar_one()
        result = await self._session.execute(
            base.order_by(Order.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), int(total)

    async def set_payment_status(
        self, order: Order, *, payment_status: str, status: str | None = None
    ) -> None:
        order.payment_status = payment_status
        if status is not None:
            order.status = status
        await self._session.flush()

    async def cancel(self, order: Order, *, reason: str | None) -> None:
        order.status = "cancelled"
        order.cancelled_at = datetime.now(timezone.utc)
        order.cancellation_reason = reason
        await self._session.flush()
