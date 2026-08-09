from __future__ import annotations

from fastapi import APIRouter, Request

from app.admin_api.dependencies import AuthServiceDep, CurrentAdmin
from app.admin_api.schemas.auth import AdminLoginRequest, AdminLoginResponse, AdminProfile

router = APIRouter(prefix="/admin/auth", tags=["admin-auth"])


def _ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    return forwarded.split(",")[0].strip() if forwarded else (
        request.client.host if request.client else None
    )


@router.post("/login", response_model=AdminLoginResponse)
async def login(payload: AdminLoginRequest, service: AuthServiceDep, request: Request):
    return await service.login(payload.email, payload.password, ip_address=_ip(request))


@router.get("/me", response_model=AdminProfile)
async def me(admin: CurrentAdmin, service: AuthServiceDep):
    return await service.get_profile(admin.sub)
