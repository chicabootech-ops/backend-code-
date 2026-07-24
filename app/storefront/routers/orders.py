from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Response

from app.storefront.dependencies import CurrentUserId, OrderServiceDep
from app.storefront.schemas.order import (
    CancelOrderRequest,
    OrderListResponse,
    OrderOut,
)

router = APIRouter(prefix="/api/orders", tags=["orders"])


@router.get("", response_model=OrderListResponse)
async def list_orders(
    user_id: CurrentUserId,
    service: OrderServiceDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> OrderListResponse:
    return await service.list_orders(user_id, page=page, page_size=page_size)


@router.get("/{order_id}", response_model=OrderOut)
async def get_order(
    order_id: uuid.UUID, user_id: CurrentUserId, service: OrderServiceDep
) -> OrderOut:
    return await service.get_order(order_id, user_id)


@router.post("/{order_id}/cancel", response_model=OrderOut)
async def cancel_order(
    order_id: uuid.UUID,
    payload: CancelOrderRequest,
    user_id: CurrentUserId,
    service: OrderServiceDep,
) -> OrderOut:
    return await service.cancel_order(order_id, user_id, reason=payload.reason)


@router.get("/{order_id}/invoice")
async def download_invoice(
    order_id: uuid.UUID, user_id: CurrentUserId, service: OrderServiceDep
) -> Response:
    pdf, filename = await service.get_invoice(order_id, user_id)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
