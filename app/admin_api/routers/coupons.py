from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query

from app.admin_api.dependencies import CouponAdminServiceDep, CurrentAdmin
from app.admin_api.services.coupon_admin_service import (
    CouponCreate,
    CouponListOut,
    CouponOut,
    CouponUpdate,
)

router = APIRouter(prefix="/admin/coupons", tags=["admin-coupons"])


@router.get("", response_model=CouponListOut)
async def list_coupons(
    _admin: CurrentAdmin,
    service: CouponAdminServiceDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
) -> CouponListOut:
    return await service.list_coupons(page=page, page_size=page_size)


@router.post("", response_model=CouponOut, status_code=201)
async def create_coupon(
    payload: CouponCreate, _admin: CurrentAdmin, service: CouponAdminServiceDep
) -> CouponOut:
    try:
        return await service.create(payload)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/{coupon_id}", response_model=CouponOut)
async def update_coupon(
    coupon_id: uuid.UUID,
    payload: CouponUpdate,
    _admin: CurrentAdmin,
    service: CouponAdminServiceDep,
) -> CouponOut:
    try:
        return await service.update(coupon_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
