from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_api.core.exceptions import NotFoundError, UnauthorizedError, ValidationError
from app.admin_api.core.security.jwt import AdminJWTManager
from app.admin_api.core.security.password import verify_password, waste_verification_time
from app.admin_api.repositories.admin_auth_repository import AdminAuthRepository
from app.admin_api.schemas.auth import AdminLoginResponse, AdminProfile
from app.identity.services.rate_limit_service import RateLimitService, by_email, by_ip

#: Per-IP and per-email attempt caps on the admin sign-in form. The admin panel
#: is a single high-value account, so this is deliberately tighter than the
#: storefront's customer login limit.
LOGIN_ATTEMPT_LIMIT = 8
LOGIN_WINDOW_SECONDS = 900


class AdminAuthService:
    def __init__(
        self,
        session: AsyncSession,
        jwt_manager: AdminJWTManager,
        rate_limit: RateLimitService | None = None,
    ) -> None:
        self._session = session
        self._jwt = jwt_manager
        self._repo = AdminAuthRepository(session)
        self._rate_limit = rate_limit

    async def login(
        self, email: str, password: str, *, ip_address: str | None = None
    ) -> AdminLoginResponse:
        await self._throttle(email, ip_address)

        row = await self._repo.get_by_email(email)
        if not row:
            # Spend the same time as a real verification before failing.
            waste_verification_time()
            raise UnauthorizedError("Invalid email or password", code="invalid_credentials")
        if not verify_password(password, row[0].password_hash):
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

    async def _throttle(self, email: str, ip_address: str | None) -> None:
        """Cap sign-in attempts per email and per source IP.

        Without Redis wired up this is a no-op — the panel must stay usable in
        local dev, where there is no attacker to throttle.
        """
        if self._rate_limit is None:
            return
        rules = [
            by_email(
                "admin_login_email",
                email,
                limit=LOGIN_ATTEMPT_LIMIT,
                window_seconds=LOGIN_WINDOW_SECONDS,
            )
        ]
        if ip_address:
            rules.append(
                by_ip(
                    "admin_login_ip",
                    ip_address,
                    limit=LOGIN_ATTEMPT_LIMIT * 2,
                    window_seconds=LOGIN_WINDOW_SECONDS,
                )
            )
        await self._rate_limit.check_rules(rules)

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
