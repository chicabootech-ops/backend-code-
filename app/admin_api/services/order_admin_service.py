from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_api.core.exceptions import NotFoundError
from app.admin_api.models.commerce import Order, OrderStatusHistory
from app.admin_api.repositories.audit_repository import AuditRepository
from app.admin_api.repositories.order_repository import OrderRepository
from app.admin_api.schemas.order import AdminOrderOut, OrderListResponse, OrderStatusUpdate, OrderTrackingEvent


def _order_out(order: Order, tracking: list[OrderStatusHistory] | None = None) -> AdminOrderOut:
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
            OrderTrackingEvent(status=h.status, note=h.note, created_at=h.created_at)
            for h in (tracking or [])
        ],
    )


class OrderAdminService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = OrderRepository(session)
        self._audit = AuditRepository(session)

    async def list_orders(self, **kwargs) -> OrderListResponse:
        orders, total = await self._repo.list_orders(**kwargs)
        page = kwargs.get("page", 1)
        page_size = kwargs.get("page_size", 20)
        items = [_order_out(o) for o in orders]
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
        return _order_out(order, tracking)

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
