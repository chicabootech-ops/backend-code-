from __future__ import annotations

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_api.models.commerce import Order, OrderStatusHistory


class OrderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_orders(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
        user_id: uuid.UUID | None = None,
        search: str | None = None,
    ) -> tuple[list[Order], int]:
        stmt = select(Order)
        count_stmt = select(func.count()).select_from(Order)

        if status:
            stmt = stmt.where(Order.status == status)
            count_stmt = count_stmt.where(Order.status == status)
        if user_id:
            stmt = stmt.where(Order.user_id == user_id)
            count_stmt = count_stmt.where(Order.user_id == user_id)
        if search:
            if search.isdigit():
                stmt = stmt.where(Order.order_number == int(search))
                count_stmt = count_stmt.where(Order.order_number == int(search))
            else:
                pattern = f"%{search}%"
                stmt = stmt.where(Order.guest_email.ilike(pattern))
                count_stmt = count_stmt.where(Order.guest_email.ilike(pattern))

        total = int((await self._session.execute(count_stmt)).scalar_one())
        offset = (page - 1) * page_size
        stmt = stmt.order_by(Order.created_at.desc()).offset(offset).limit(page_size)
        orders = list((await self._session.execute(stmt)).scalars().all())
        return orders, total

    async def get_by_id(self, order_id: uuid.UUID) -> Order | None:
        result = await self._session.execute(select(Order).where(Order.id == order_id))
        return result.scalar_one_or_none()

    async def get_tracking(self, order_id: uuid.UUID) -> list[OrderStatusHistory]:
        result = await self._session.execute(
            select(OrderStatusHistory)
            .where(OrderStatusHistory.order_id == order_id)
            .order_by(OrderStatusHistory.created_at.asc())
        )
        return list(result.scalars().all())

    async def update_status(
        self,
        order_id: uuid.UUID,
        status: str,
        note: str | None,
    ) -> Order | None:
        result = await self._session.execute(
            update(Order)
            .where(Order.id == order_id)
            .values(status=status)
            .returning(Order)
        )
        order = result.scalar_one_or_none()
        if not order:
            return None
        history = OrderStatusHistory(order_id=order_id, status=status, note=note)
        self._session.add(history)
        await self._session.flush()
        await self._session.refresh(order)
        return order
