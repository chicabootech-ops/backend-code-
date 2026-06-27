from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request

from app.dependencies import CurrentAdmin, UserAdminServiceDep
from app.schemas.user import AdminUserDetailOut, AdminUserOut, UserListResponse, UserStatusUpdate

router = APIRouter(prefix="/admin/users", tags=["admin-users"])


def _ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    return forwarded.split(",")[0].strip() if forwarded else (
        request.client.host if request.client else None
    )


@router.get("", response_model=UserListResponse)
async def list_users(
    _admin: CurrentAdmin,
    service: UserAdminServiceDep,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    search: str | None = None,
    status: str | None = None,
):
    return await service.list_users(page=page, page_size=page_size, search=search, status=status)


@router.get("/{user_id}", response_model=AdminUserDetailOut)
async def get_user(user_id: uuid.UUID, _admin: CurrentAdmin, service: UserAdminServiceDep):
    return await service.get_user(user_id)


@router.patch("/{user_id}/status", response_model=AdminUserOut)
async def update_user_status(
    user_id: uuid.UUID,
    payload: UserStatusUpdate,
    admin: CurrentAdmin,
    service: UserAdminServiceDep,
    request: Request,
):
    return await service.update_status(
        user_id, payload, admin_id=admin.sub, ip_address=_ip(request)
    )


@router.patch("/{user_id}/ban", response_model=AdminUserOut)
async def ban_user(
    user_id: uuid.UUID,
    admin: CurrentAdmin,
    service: UserAdminServiceDep,
    request: Request,
    reason: str | None = None,
):
    return await service.ban(user_id, admin_id=admin.sub, ip_address=_ip(request), reason=reason)


@router.patch("/{user_id}/suspend", response_model=AdminUserOut)
async def suspend_user(
    user_id: uuid.UUID,
    admin: CurrentAdmin,
    service: UserAdminServiceDep,
    request: Request,
    reason: str | None = None,
):
    return await service.suspend(
        user_id, admin_id=admin.sub, ip_address=_ip(request), reason=reason
    )


@router.patch("/{user_id}/activate", response_model=AdminUserOut)
async def activate_user(
    user_id: uuid.UUID,
    admin: CurrentAdmin,
    service: UserAdminServiceDep,
    request: Request,
):
    return await service.activate(user_id, admin_id=admin.sub, ip_address=_ip(request))
