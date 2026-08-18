"""Lean customer return requests."""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class ReturnError(Exception):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        self.message = message
        self.status_code = status_code


class ReturnService:
    def __init__(self, session: AsyncSession, notifications_factory=None) -> None:
        self._session = session
        self._notifications_factory = notifications_factory

    async def list_for_user(self, user_id: uuid.UUID) -> list[dict]:
        result = await self._session.execute(
            text(
                """
                SELECT r.id, r.return_number, r.order_id, o.order_number, r.status,
                       r.reason, r.customer_note, r.created_at, r.updated_at
                FROM commerce.returns r
                JOIN commerce.orders o ON o.id = r.order_id
                WHERE r.user_id = :user_id AND r.deleted_at IS NULL
                ORDER BY r.created_at DESC
                """
            ),
            {"user_id": str(user_id)},
        )
        return [dict(row) for row in result.mappings().all()]

    async def create(
        self,
        user_id: uuid.UUID,
        *,
        order_id: uuid.UUID,
        reason: str,
        note: str | None,
    ) -> dict:
        order = (
            await self._session.execute(
                text(
                    """
                    SELECT id FROM commerce.orders
                    WHERE id = :order_id AND user_id = :user_id
                    """
                ),
                {"order_id": str(order_id), "user_id": str(user_id)},
            )
        ).first()
        if not order:
            raise ReturnError("Order not found", status_code=404)

        result = await self._session.execute(
            text(
                """
                INSERT INTO commerce.returns (order_id, user_id, reason, customer_note)
                VALUES (:order_id, :user_id, :reason, :note)
                RETURNING id, return_number, order_id, status, reason, customer_note,
                          created_at, updated_at
                """
            ),
            {
                "order_id": str(order_id),
                "user_id": str(user_id),
                "reason": reason,
                "note": note,
            },
        )
        row = dict(result.mappings().one())
        order_number = (
            await self._session.execute(
                text("SELECT order_number FROM commerce.orders WHERE id = :order_id"),
                {"order_id": str(order_id)},
            )
        ).scalar_one()
        row["order_number"] = order_number
        await self._session.commit()
        await self._notify_created(
            order_id=order_id, return_id=row["id"], order_number=order_number
        )
        return row

    async def _notify_created(
        self, *, order_id: uuid.UUID, return_id: uuid.UUID, order_number: int
    ) -> None:
        if self._notifications_factory is None:
            return
        from app.config import settings
        from app.notifications.order_notifier import OrderNotifier

        notifier = OrderNotifier(
            self._session,
            settings,
            notifications=self._notifications_factory(self._session),
        )
        await notifier.return_created(
            order_id=order_id, return_id=return_id, order_number=order_number
        )
