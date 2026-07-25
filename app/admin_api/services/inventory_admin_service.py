"""Admin inventory adjustments."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class InventoryAdjust(BaseModel):
    quantity_on_hand: int = Field(ge=0)
    low_stock_threshold: int | None = Field(default=None, ge=0)
    warehouse_id: uuid.UUID | None = None


class InventoryRowOut(BaseModel):
    id: uuid.UUID
    warehouse_id: uuid.UUID
    product_variant_id: uuid.UUID
    quantity_on_hand: int
    quantity_reserved: int
    available: int
    low_stock_threshold: int | None
    sku: str | None = None
    product_name: str | None = None


class InventoryListOut(BaseModel):
    items: list[InventoryRowOut]
    total: int


class InventoryAdminService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _default_warehouse(self) -> uuid.UUID:
        row = (
            await self._session.execute(
                text(
                    """
                    SELECT id FROM commerce.warehouses
                    WHERE deleted_at IS NULL AND is_active = true
                    ORDER BY is_default DESC, priority DESC
                    LIMIT 1
                    """
                )
            )
        ).first()
        if row:
            return row[0]
        created = (
            await self._session.execute(
                text(
                    """
                    INSERT INTO commerce.warehouses (code, name, is_default, is_active)
                    VALUES ('WH-MAIN', 'Main Warehouse', true, true)
                    RETURNING id
                    """
                )
            )
        ).one()
        await self._session.flush()
        return created[0]

    async def adjust(
        self, variant_id: uuid.UUID, payload: InventoryAdjust, *, admin_id: uuid.UUID | None
    ) -> InventoryRowOut:
        warehouse_id = payload.warehouse_id or await self._default_warehouse()
        existing = (
            await self._session.execute(
                text(
                    """
                    SELECT id, quantity_on_hand FROM commerce.inventory
                    WHERE warehouse_id = :w AND product_variant_id = :v
                    FOR UPDATE
                    """
                ),
                {"w": str(warehouse_id), "v": str(variant_id)},
            )
        ).first()

        if existing:
            before = int(existing[1])
            await self._session.execute(
                text(
                    """
                    UPDATE commerce.inventory
                    SET quantity_on_hand = :q,
                        low_stock_threshold = COALESCE(:thr, low_stock_threshold),
                        updated_at = now()
                    WHERE id = :id
                    """
                ),
                {"q": payload.quantity_on_hand, "thr": payload.low_stock_threshold, "id": str(existing[0])},
            )
            delta = payload.quantity_on_hand - before
            if delta != 0:
                await self._session.execute(
                    text(
                        """
                        INSERT INTO commerce.inventory_movements
                          (warehouse_id, product_variant_id, movement_type, quantity_delta,
                           quantity_before, quantity_after, reference_type, reason, admin_id)
                        VALUES (:w, :v, 'adjustment', :d, :before, :after, 'admin', 'manual adjust', :a)
                        """
                    ),
                    {
                        "w": str(warehouse_id),
                        "v": str(variant_id),
                        "d": delta,
                        "before": before,
                        "after": payload.quantity_on_hand,
                        "a": str(admin_id) if admin_id else None,
                    },
                )
            inv_id = existing[0]
        else:
            created = (
                await self._session.execute(
                    text(
                        """
                        INSERT INTO commerce.inventory
                          (warehouse_id, product_variant_id, quantity_on_hand, low_stock_threshold)
                        VALUES (:w, :v, :q, :thr)
                        RETURNING id
                        """
                    ),
                    {
                        "w": str(warehouse_id),
                        "v": str(variant_id),
                        "q": payload.quantity_on_hand,
                        "thr": payload.low_stock_threshold,
                    },
                )
            ).one()
            inv_id = created[0]
            await self._session.execute(
                text(
                    """
                    INSERT INTO commerce.inventory_movements
                      (warehouse_id, product_variant_id, movement_type, quantity_delta,
                       quantity_before, quantity_after, reference_type, reason, admin_id)
                    VALUES (:w, :v, 'adjustment', :q, 0, :q, 'admin', 'initial stock', :a)
                    """
                ),
                {
                    "w": str(warehouse_id),
                    "v": str(variant_id),
                    "q": payload.quantity_on_hand,
                    "a": str(admin_id) if admin_id else None,
                },
            )

        await self._session.flush()
        return await self.get_row(inv_id)

    async def get_row(self, inventory_id: uuid.UUID) -> InventoryRowOut:
        row = (
            await self._session.execute(
                text(
                    """
                    SELECT i.*, v.sku, p.name AS product_name
                    FROM commerce.inventory i
                    JOIN commerce.product_variants v ON v.id = i.product_variant_id
                    JOIN commerce.products p ON p.id = v.product_id
                    WHERE i.id = :id
                    """
                ),
                {"id": str(inventory_id)},
            )
        ).mappings().one()
        return InventoryRowOut(
            id=row["id"],
            warehouse_id=row["warehouse_id"],
            product_variant_id=row["product_variant_id"],
            quantity_on_hand=row["quantity_on_hand"],
            quantity_reserved=row["quantity_reserved"],
            available=row["quantity_on_hand"] - row["quantity_reserved"],
            low_stock_threshold=row.get("low_stock_threshold"),
            sku=row.get("sku"),
            product_name=row.get("product_name"),
        )

    async def low_stock(self, *, limit: int = 50) -> InventoryListOut:
        rows = (
            await self._session.execute(
                text(
                    """
                    SELECT i.*, v.sku, p.name AS product_name
                    FROM commerce.inventory i
                    JOIN commerce.product_variants v ON v.id = i.product_variant_id
                    JOIN commerce.products p ON p.id = v.product_id
                    WHERE i.low_stock_threshold IS NOT NULL
                      AND (i.quantity_on_hand - i.quantity_reserved) <= i.low_stock_threshold
                    ORDER BY (i.quantity_on_hand - i.quantity_reserved) ASC
                    LIMIT :lim
                    """
                ),
                {"lim": limit},
            )
        ).mappings().all()
        items = [
            InventoryRowOut(
                id=r["id"],
                warehouse_id=r["warehouse_id"],
                product_variant_id=r["product_variant_id"],
                quantity_on_hand=r["quantity_on_hand"],
                quantity_reserved=r["quantity_reserved"],
                available=r["quantity_on_hand"] - r["quantity_reserved"],
                low_stock_threshold=r.get("low_stock_threshold"),
                sku=r.get("sku"),
                product_name=r.get("product_name"),
            )
            for r in rows
        ]
        return InventoryListOut(items=items, total=len(items))
