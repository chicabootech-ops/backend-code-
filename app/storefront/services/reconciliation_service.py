"""Reconciliation for abandoned checkouts.

Handles: 'payment pending forever', 'order created but never paid', and
'reserved inventory expires'. Safe to run repeatedly (idempotent) from a cron.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.storefront.services.inventory_service import InventoryService


class ReconciliationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._inventory = InventoryService(session)

    async def run(self, *, stale_minutes: int = 30) -> dict:
        # 1. Release reservations that have passed their expiry window.
        expired_released = await self._inventory.release_expired()

        # 2. Cancel orders still pending + unpaid past the stale window,
        #    releasing any stock they were holding.
        stale = (
            await self._session.execute(
                text(
                    """
                    SELECT id FROM commerce.orders
                    WHERE status = 'pending' AND payment_status = 'pending'
                      AND created_at < now() - (:m || ' minutes')::interval
                    FOR UPDATE SKIP LOCKED
                    """
                ),
                {"m": str(stale_minutes)},
            )
        ).scalars().all()

        for order_id in stale:
            await self._inventory.release(order_id)
            await self._session.execute(
                text(
                    """
                    UPDATE commerce.orders
                    SET status = 'cancelled', cancelled_at = now(),
                        cancellation_reason = 'Auto-cancelled: payment not completed'
                    WHERE id = :id
                    """
                ),
                {"id": str(order_id)},
            )
            await self._session.execute(
                text(
                    """
                    INSERT INTO commerce.order_status_history
                        (order_id, from_status, to_status, changed_by_type, reason)
                    VALUES (:id, 'pending', 'cancelled', 'system', 'Auto-cancelled: payment not completed')
                    """
                ),
                {"id": str(order_id)},
            )

        await self._session.commit()
        return {"expired_reservations_released": expired_released, "stale_orders_cancelled": len(stale)}
