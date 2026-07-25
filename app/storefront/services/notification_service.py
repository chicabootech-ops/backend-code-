"""Customer notifications derived from order status history."""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class NotificationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_user(self, user_id: uuid.UUID) -> list[dict]:
        result = await self._session.execute(
            text(
                """
                SELECT h.id, h.order_id, o.order_number, h.from_status, h.to_status,
                       h.reason, h.created_at
                FROM commerce.order_status_history h
                JOIN commerce.orders o ON o.id = h.order_id
                WHERE o.user_id = :user_id
                ORDER BY h.created_at DESC
                LIMIT 20
                """
            ),
            {"user_id": str(user_id)},
        )
        return [
            {
                **dict(row),
                "title": f"Order #{row['order_number']} is {str(row['to_status']).replace('_', ' ')}",
            }
            for row in result.mappings().all()
        ]
