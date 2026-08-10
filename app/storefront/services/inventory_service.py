"""Stock reservation for checkout — prevents overselling.

Opt-in per variant: a variant with no `commerce.inventory` row is treated as
untracked (unlimited) so the catalog keeps working until admin adds stock. Once a
variant is tracked, reservation is atomic (row lock + availability check) so two
buyers can never both take the last unit.

Lifecycle:
  reserve()          at checkout    -> quantity_reserved += qty (+ reservation row)
  commit()           on payment ok  -> quantity_on_hand -= qty, reserved -= qty
  release()          on cancel/fail -> quantity_reserved -= qty
  release_expired()  reconciliation -> release stale active reservations
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class OutOfStockError(Exception):
    def __init__(self, unavailable: list[str]) -> None:
        self.unavailable = unavailable
        names = ", ".join(unavailable) or "some items"
        super().__init__(f"Out of stock: {names}")


class InventoryService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _is_tracked(self, variant_id: uuid.UUID) -> bool:
        row = await self._session.execute(
            text("SELECT 1 FROM commerce.inventory WHERE product_variant_id = :v LIMIT 1"),
            {"v": str(variant_id)},
        )
        return row.first() is not None

    async def reserve(
        self, *, order_id: uuid.UUID, items: list[dict], ttl_minutes: int = 30
    ) -> None:
        """Reserve stock for each tracked item. Raises OutOfStockError if any can't be met.

        Runs in the caller's transaction, so a raise here rolls back all prior
        reservations automatically — nothing is left half-reserved.
        """
        unavailable: list[str] = []
        for it in items:
            raw_vid = it["product_variant_id"]
            vid = raw_vid if isinstance(raw_vid, uuid.UUID) else uuid.UUID(str(raw_vid))
            qty = int(it["quantity"])
            if not await self._is_tracked(vid):
                continue  # untracked variant -> unlimited

            # Atomically pick the warehouse row with the most availability and reserve it.
            picked = await self._session.execute(
                text(
                    """
                    WITH pick AS (
                        SELECT id FROM commerce.inventory
                        WHERE product_variant_id = :v
                          AND quantity_on_hand - quantity_reserved >= :q
                        ORDER BY quantity_on_hand - quantity_reserved DESC
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    UPDATE commerce.inventory i
                    SET quantity_reserved = quantity_reserved + :q, updated_at = now()
                    FROM pick
                    WHERE i.id = pick.id
                    RETURNING i.warehouse_id
                    """
                ),
                {"v": str(vid), "q": qty},
            )
            wh = picked.first()
            if wh is None:
                unavailable.append(str(it.get("product_name") or vid))
                continue
            await self._session.execute(
                text(
                    """
                    INSERT INTO commerce.stock_reservations
                        (warehouse_id, product_variant_id, order_id, quantity, status, expires_at)
                    VALUES (:w, :v, :o, :q, 'active', now() + (:ttl || ' minutes')::interval)
                    """
                ),
                {"w": str(wh[0]), "v": str(vid), "o": str(order_id), "q": qty, "ttl": str(ttl_minutes)},
            )

        if unavailable:
            raise OutOfStockError(unavailable)

    async def commit(self, order_id: uuid.UUID) -> None:
        """Convert active reservations to a real stock deduction + movement ledger.

        ``FOR UPDATE`` is load-bearing, not defensive: a webhook and a browser
        callback can settle the same order concurrently, and without the lock
        both transactions read the same rows as 'active' and both deduct — the
        customer's stock goes down twice for one sale. The lock makes the second
        caller wait and then find nothing left to commit.
        """
        reservations = (
            await self._session.execute(
                text(
                    """
                    SELECT id, warehouse_id, product_variant_id, quantity
                    FROM commerce.stock_reservations
                    WHERE order_id = :o AND status = 'active'
                    FOR UPDATE
                    """
                ),
                {"o": str(order_id)},
            )
        ).mappings().all()

        for r in reservations:
            updated = await self._session.execute(
                text(
                    """
                    UPDATE commerce.inventory
                    SET quantity_on_hand = quantity_on_hand - :q,
                        quantity_reserved = GREATEST(quantity_reserved - :q, 0),
                        updated_at = now()
                    WHERE warehouse_id = :w AND product_variant_id = :v
                    RETURNING quantity_on_hand
                    """
                ),
                {"q": r["quantity"], "w": str(r["warehouse_id"]), "v": str(r["product_variant_id"])},
            )
            after = updated.scalar_one_or_none()
            if after is not None:
                await self._session.execute(
                    text(
                        """
                        INSERT INTO commerce.inventory_movements
                            (warehouse_id, product_variant_id, movement_type, quantity_delta,
                             quantity_before, quantity_after, reference_type, reference_id, reason)
                        VALUES (:w, :v, 'sale', :d, :before, :after, 'order', :o, 'Order payment captured')
                        """
                    ),
                    {
                        "w": str(r["warehouse_id"]),
                        "v": str(r["product_variant_id"]),
                        "d": -r["quantity"],
                        "before": after + r["quantity"],
                        "after": after,
                        "o": str(order_id),
                    },
                )
            await self._session.execute(
                text("UPDATE commerce.stock_reservations SET status='committed', updated_at=now() WHERE id=:i"),
                {"i": str(r["id"])},
            )

    async def release(self, order_id: uuid.UUID) -> None:
        """Release active reservations (order cancelled or payment failed).

        Locked for the same reason as :meth:`commit` — a release racing a commit
        must not both act on one reservation.
        """
        reservations = (
            await self._session.execute(
                text(
                    """
                    SELECT id, warehouse_id, product_variant_id, quantity
                    FROM commerce.stock_reservations
                    WHERE order_id = :o AND status = 'active'
                    FOR UPDATE
                    """
                ),
                {"o": str(order_id)},
            )
        ).mappings().all()
        for r in reservations:
            await self._session.execute(
                text(
                    """
                    UPDATE commerce.inventory
                    SET quantity_reserved = GREATEST(quantity_reserved - :q, 0), updated_at = now()
                    WHERE warehouse_id = :w AND product_variant_id = :v
                    """
                ),
                {"q": r["quantity"], "w": str(r["warehouse_id"]), "v": str(r["product_variant_id"])},
            )
            await self._session.execute(
                text("UPDATE commerce.stock_reservations SET status='released', updated_at=now() WHERE id=:i"),
                {"i": str(r["id"])},
            )

    async def release_expired(self) -> int:
        """Release reservations past their expiry. Returns count released."""
        rows = (
            await self._session.execute(
                text(
                    """
                    SELECT id, warehouse_id, product_variant_id, quantity
                    FROM commerce.stock_reservations
                    WHERE status = 'active' AND expires_at IS NOT NULL AND expires_at < now()
                    FOR UPDATE SKIP LOCKED
                    """
                )
            )
        ).mappings().all()
        for r in rows:
            await self._session.execute(
                text(
                    """
                    UPDATE commerce.inventory
                    SET quantity_reserved = GREATEST(quantity_reserved - :q, 0), updated_at = now()
                    WHERE warehouse_id = :w AND product_variant_id = :v
                    """
                ),
                {"q": r["quantity"], "w": str(r["warehouse_id"]), "v": str(r["product_variant_id"])},
            )
            await self._session.execute(
                text("UPDATE commerce.stock_reservations SET status='expired', updated_at=now() WHERE id=:i"),
                {"i": str(r["id"])},
            )
        return len(rows)
