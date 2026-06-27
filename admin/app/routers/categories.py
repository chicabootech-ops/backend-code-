from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request

from app.dependencies import CategoryServiceDep, CurrentAdmin
from app.schemas.category import CategoryCreate, CategoryOut, CategoryUpdate

router = APIRouter(prefix="/admin/categories", tags=["admin-categories"])


def _ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    return forwarded.split(",")[0].strip() if forwarded else (
        request.client.host if request.client else None
    )


@router.get("", response_model=list[CategoryOut])
async def list_categories(
    _admin: CurrentAdmin,
    service: CategoryServiceDep,
    include_inactive: bool = Query(default=True),
):
    return await service.list_tree(include_inactive=include_inactive)


@router.get("/{category_id}", response_model=CategoryOut)
async def get_category(category_id: uuid.UUID, _admin: CurrentAdmin, service: CategoryServiceDep):
    return await service.get(category_id)


@router.post("", response_model=CategoryOut, status_code=201)
async def create_category(
    payload: CategoryCreate,
    admin: CurrentAdmin,
    service: CategoryServiceDep,
    request: Request,
):
    return await service.create(payload, admin_id=admin.sub, ip_address=_ip(request))


@router.patch("/{category_id}", response_model=CategoryOut)
async def update_category(
    category_id: uuid.UUID,
    payload: CategoryUpdate,
    admin: CurrentAdmin,
    service: CategoryServiceDep,
    request: Request,
):
    return await service.update(category_id, payload, admin_id=admin.sub, ip_address=_ip(request))


@router.delete("/{category_id}", status_code=204)
async def delete_category(
    category_id: uuid.UUID,
    admin: CurrentAdmin,
    service: CategoryServiceDep,
    request: Request,
):
    await service.delete(category_id, admin_id=admin.sub, ip_address=_ip(request))
