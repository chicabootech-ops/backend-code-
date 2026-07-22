from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_api.core.exceptions import NotFoundError, UnauthorizedError, ValidationError
from app.admin_api.core.security.jwt import AdminJWTManager
from app.admin_api.core.security.password import verify_password
from app.admin_api.repositories.admin_auth_repository import AdminAuthRepository
from app.admin_api.schemas.auth import AdminLoginResponse, AdminProfile


class AdminAuthService:
    def __init__(self, session: AsyncSession, jwt_manager: AdminJWTManager) -> None:
        self._session = session
        self._jwt = jwt_manager
        self._repo = AdminAuthRepository(session)

    async def login(self, email: str, password: str) -> AdminLoginResponse:
        row = await self._repo.get_by_email(email)
        if not row or not verify_password(password, row[0].password_hash):
            raise UnauthorizedError("Invalid email or password", code="invalid_credentials")

        admin, role_name = row
        token = self._jwt.create_access_token(admin.id, admin.email, role_name)
        return AdminLoginResponse(
            access_token=token,
            admin=AdminProfile(
                id=admin.id,
                email=admin.email,
                full_name=admin.full_name,
                role=role_name,
            ),
        )

    async def get_profile(self, admin_id: uuid.UUID) -> AdminProfile:
        from sqlalchemy import select

        from app.admin_api.models.commerce import AdminUser, Role

        result = await self._session.execute(
            select(AdminUser, Role.name)
            .join(Role, Role.id == AdminUser.role_id)
            .where(AdminUser.id == admin_id, AdminUser.deleted_at.is_(None))
        )
        row = result.one_or_none()
        if not row:
            raise NotFoundError("Admin not found")
        admin, role_name = row
        if admin.status != "active":
            raise ValidationError("Admin account is not active")
        return AdminProfile(
            id=admin.id,
            email=admin.email,
            full_name=admin.full_name,
            role=role_name,
        )
