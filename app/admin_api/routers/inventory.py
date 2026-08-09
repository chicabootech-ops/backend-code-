from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query

from app.admin_api.core.security.permissions import InventoryWriter
from app.admin_api.dependencies import CurrentAdmin, InventoryAdminServiceDep
from app.admin_api.services.inventory_admin_service import (
    InventoryAdjust,
    InventoryListOut,
    InventoryRowOut,
)

router = APIRouter(prefix="/admin/inventory", tags=["admin-inventory"])


@router.patch("/{variant_id}", response_model=InventoryRowOut)
async def adjust_inventory(
    variant_id: uuid.UUID,
    payload: InventoryAdjust,
    admin: InventoryWriter,
    service: InventoryAdminServiceDep,
) -> InventoryRowOut:
    try:
        return await service.adjust(variant_id, payload, admin_id=uuid.UUID(admin.sub))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/low-stock", response_model=InventoryListOut)
async def low_stock_dashboard(
    _admin: CurrentAdmin,
    service: InventoryAdminServiceDep,
    limit: int = Query(50, ge=1, le=200),
) -> InventoryListOut:
    return await service.low_stock(limit=limit)
