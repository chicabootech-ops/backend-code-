from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_api.core.exceptions import UnauthorizedError
from app.admin_api.core.security.jwt import AdminJWTManager, AdminTokenPayload
from app.admin_api.db.session import get_session
from app.storefront.lib.catalog_cache import CatalogCache
from app.admin_api.services.auth_service import AdminAuthService
from app.admin_api.services.category_service import CategoryService
from app.admin_api.services.coupon_admin_service import CouponAdminService
from app.admin_api.services.inventory_admin_service import InventoryAdminService
from app.admin_api.services.invoice_admin_service import InvoiceAdminService
from app.admin_api.services.order_admin_service import OrderAdminService
from app.admin_api.services.product_service import ProductService
from app.admin_api.services.user_admin_service import UserAdminService

bearer_scheme = HTTPBearer(auto_error=False)


async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    session_factory = request.app.state.session_factory
    async for session in get_session(session_factory):
        yield session


def get_jwt_manager(request: Request) -> AdminJWTManager:
    return request.app.state.admin_jwt_manager


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


async def get_current_admin(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    jwt_manager: Annotated[AdminJWTManager, Depends(get_jwt_manager)],
) -> AdminTokenPayload:
    if not credentials:
        raise UnauthorizedError("Missing authorization header", code="missing_token")
    return jwt_manager.decode_token(credentials.credentials)


async def get_auth_service(
    db: Annotated[AsyncSession, Depends(get_db)],
    jwt_manager: Annotated[AdminJWTManager, Depends(get_jwt_manager)],
) -> AdminAuthService:
    return AdminAuthService(db, jwt_manager)


async def get_category_service(db: Annotated[AsyncSession, Depends(get_db)]) -> CategoryService:
    return CategoryService(db)


async def get_product_service(db: Annotated[AsyncSession, Depends(get_db)]) -> ProductService:
    return ProductService(db)


async def get_user_admin_service(db: Annotated[AsyncSession, Depends(get_db)]) -> UserAdminService:
    return UserAdminService(db)


async def get_order_admin_service(db: Annotated[AsyncSession, Depends(get_db)]) -> OrderAdminService:
    return OrderAdminService(db)


async def get_invoice_admin_service(db: Annotated[AsyncSession, Depends(get_db)]) -> InvoiceAdminService:
    return InvoiceAdminService(db)


async def get_coupon_admin_service(db: Annotated[AsyncSession, Depends(get_db)]) -> CouponAdminService:
    return CouponAdminService(db)


async def get_inventory_admin_service(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> InventoryAdminService:
    return InventoryAdminService(db)


def get_catalog_cache(request: Request) -> CatalogCache:
    return CatalogCache(getattr(request.app.state, "redis_client", None))


DbSession = Annotated[AsyncSession, Depends(get_db)]
CurrentAdmin = Annotated[AdminTokenPayload, Depends(get_current_admin)]
CategoryServiceDep = Annotated[CategoryService, Depends(get_category_service)]
ProductServiceDep = Annotated[ProductService, Depends(get_product_service)]
UserAdminServiceDep = Annotated[UserAdminService, Depends(get_user_admin_service)]
OrderAdminServiceDep = Annotated[OrderAdminService, Depends(get_order_admin_service)]
InvoiceAdminServiceDep = Annotated[InvoiceAdminService, Depends(get_invoice_admin_service)]
CouponAdminServiceDep = Annotated[CouponAdminService, Depends(get_coupon_admin_service)]
InventoryAdminServiceDep = Annotated[InventoryAdminService, Depends(get_inventory_admin_service)]
CatalogCacheDep = Annotated[CatalogCache, Depends(get_catalog_cache)]
AuthServiceDep = Annotated[AdminAuthService, Depends(get_auth_service)]
