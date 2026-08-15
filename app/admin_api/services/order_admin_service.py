from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_api.core.exceptions import NotFoundError
from app.admin_api.models.commerce import Order, OrderStatusHistory
from app.admin_api.repositories.audit_repository import AuditRepository
from app.admin_api.repositories.order_repository import OrderRepository
from app.admin_api.schemas.order import (
    AdminOrderItemOut,
    AdminOrderOut,
    OrderListResponse,
    OrderStatusUpdate,
    OrderTrackingEvent,
)
from app.storefront.lib.media import product_image_url

#: Items joined to their product for the image. Raw SQL rather than an ORM model
#: because admin_api has no OrderItem mapping and importing the storefront's
#: would cross an app boundary for one read. Parameterised — `:ids` is bound.
_ITEMS_SQL = text(
    """
    SELECT oi.order_id,
           oi.product_id,
           oi.product_name,
           oi.variant_title,
           oi.sku,
           oi.quantity,
           oi.unit_price_paise,
           oi.line_total_paise,
           p.metadata AS product_metadata
      FROM commerce.order_items oi
      LEFT JOIN commerce.products p ON p.id = oi.product_id
     WHERE oi.order_id = ANY(:ids)
     ORDER BY oi.created_at
    """
)


def _order_out(
    order: Order,
    tracking: list[OrderStatusHistory] | None = None,
    items: list[AdminOrderItemOut] | None = None,
) -> AdminOrderOut:
    return AdminOrderOut(
        id=order.id,
        order_number=order.order_number,
        user_id=order.user_id,
        guest_email=order.guest_email,
        status=order.status,
        payment_status=order.payment_status,
        fulfillment_status=order.fulfillment_status,
        grand_total_paise=order.grand_total_paise,
        shipping_address=order.shipping_address or {},
        admin_note=order.admin_note,
        created_at=order.created_at,
        updated_at=order.updated_at,
        tracking=[
            OrderTrackingEvent(status=h.to_status, note=h.reason, created_at=h.created_at)
            for h in (tracking or [])
        ],
        items=items or [],
        item_count=sum(i.quantity for i in (items or [])),
    )


class OrderAdminService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = OrderRepository(session)
        self._audit = AuditRepository(session)

    async def _items_by_order(
        self, order_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[AdminOrderItemOut]]:
        """Load every line for these orders in one query, grouped by order.

        Batched on purpose: the list page shows a thumbnail per row, and doing
        this per order would turn one screen into a query per order plus a query
        per product.
        """
        if not order_ids:
            return {}
        rows = (await self._session.execute(_ITEMS_SQL, {"ids": order_ids})).mappings().all()
        grouped: dict[uuid.UUID, list[AdminOrderItemOut]] = defaultdict(list)
        for row in rows:
            grouped[row["order_id"]].append(
                AdminOrderItemOut(
                    product_id=row["product_id"],
                    product_name=row["product_name"],
                    variant_title=row["variant_title"],
                    sku=row["sku"],
                    quantity=row["quantity"],
                    unit_price_paise=row["unit_price_paise"],
                    line_total_paise=row["line_total_paise"],
                    image_url=product_image_url(row["product_metadata"]),
                )
            )
        return grouped

    async def list_orders(self, **kwargs) -> OrderListResponse:
        orders, total = await self._repo.list_orders(**kwargs)
        page = kwargs.get("page", 1)
        page_size = kwargs.get("page_size", 20)
        by_order = await self._items_by_order([o.id for o in orders])
        items = [_order_out(o, items=by_order.get(o.id, [])) for o in orders]
        return OrderListResponse(
            items=items,
            meta={
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": max(1, (total + page_size - 1) // page_size),
            },
        )

    async def get_order(self, order_id: uuid.UUID) -> AdminOrderOut:
        order = await self._repo.get_by_id(order_id)
        if not order:
            raise NotFoundError("Order not found")
        tracking = await self._repo.get_tracking(order_id)
        by_order = await self._items_by_order([order_id])
        return _order_out(order, tracking, items=by_order.get(order_id, []))

    async def update_status(
        self,
        order_id: uuid.UUID,
        payload: OrderStatusUpdate,
        *,
        admin_id: uuid.UUID,
        ip_address: str | None = None,
    ) -> AdminOrderOut:
        order = await self._repo.get_by_id(order_id)
        if not order:
            raise NotFoundError("Order not found")

        updated = await self._repo.update_status(order_id, payload.status, payload.note)
        if not updated:
            raise NotFoundError("Order not found")

        await self._audit.log(
            admin_id=admin_id,
            entity_type="order",
            entity_id=order_id,
            action="status_update",
            old_data={"status": order.status},
            new_data={"status": payload.status, "note": payload.note},
            domain="order",
            target_user_id=order.user_id,
            ip_address=ip_address,
        )
        tracking = await self._repo.get_tracking(order_id)
        return _order_out(updated, tracking)

    async def track(self, order_id: uuid.UUID) -> AdminOrderOut:
        return await self.get_order(order_id)
