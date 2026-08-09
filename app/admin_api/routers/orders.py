from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request

from pydantic import BaseModel, Field

from app.admin_api.core.security.permissions import OrderWriter, RefundWriter
from app.admin_api.dependencies import CurrentAdmin, OrderAdminServiceDep, RefundServiceDep
from app.admin_api.schemas.order import AdminOrderOut, OrderListResponse, OrderStatusUpdate

router = APIRouter(prefix="/admin/orders", tags=["admin-orders"])


def _ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    return forwarded.split(",")[0].strip() if forwarded else (
        request.client.host if request.client else None
    )


@router.get("", response_model=OrderListResponse)
async def list_orders(
    _admin: CurrentAdmin,
    service: OrderAdminServiceDep,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: str | None = None,
    user_id: uuid.UUID | None = None,
    search: str | None = None,
):
    return await service.list_orders(
        page=page, page_size=page_size, status=status, user_id=user_id, search=search
    )


@router.get("/{order_id}", response_model=AdminOrderOut)
async def get_order(order_id: uuid.UUID, _admin: CurrentAdmin, service: OrderAdminServiceDep):
    return await service.get_order(order_id)


@router.get("/{order_id}/track", response_model=AdminOrderOut)
async def track_order(order_id: uuid.UUID, _admin: CurrentAdmin, service: OrderAdminServiceDep):
    return await service.track(order_id)


@router.patch("/{order_id}/status", response_model=AdminOrderOut)
async def update_order_status(
    order_id: uuid.UUID,
    payload: OrderStatusUpdate,
    admin: OrderWriter,
    service: OrderAdminServiceDep,
    request: Request,
):
    return await service.update_status(
        order_id, payload, admin_id=admin.sub, ip_address=_ip(request)
    )


class RefundRequest(BaseModel):
    """Omit `amount_paise` to refund whatever is still outstanding."""

    amount_paise: int | None = Field(default=None, gt=0)
    reason: str | None = Field(default=None, max_length=500)


@router.post("/{order_id}/refund")
async def refund_order(
    order_id: uuid.UUID,
    payload: RefundRequest,
    admin: RefundWriter,
    service: RefundServiceDep,
    request: Request,
):
    """Issue a Razorpay refund against this order's captured payment."""
    return await service.refund_order(
        order_id,
        admin_id=admin.sub,
        amount_paise=payload.amount_paise,
        reason=payload.reason,
        ip_address=_ip(request),
    )
