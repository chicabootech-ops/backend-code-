from __future__ import annotations

from fastapi import APIRouter

from app.admin_api.dependencies import AuthServiceDep, CurrentAdmin
from app.admin_api.schemas.auth import AdminLoginRequest, AdminLoginResponse, AdminProfile

router = APIRouter(prefix="/admin/auth", tags=["admin-auth"])


@router.post("/login", response_model=AdminLoginResponse)
async def login(payload: AdminLoginRequest, service: AuthServiceDep):
    return await service.login(payload.email, payload.password)


@router.get("/me", response_model=AdminProfile)
async def me(admin: CurrentAdmin, service: AuthServiceDep):
    return await service.get_profile(admin.sub)
