from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.storefront.db.session import get_session
from app.storefront.lib.razorpay_client import RazorpayClient
from app.storefront.services.catalog_service import CatalogService
from app.storefront.services.category_service import CategoryService
from app.storefront.services.order_service import OrderService
from app.storefront.services.payment_service import PaymentService

bearer_scheme = HTTPBearer(auto_error=False)


async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    session_factory = request.app.state.session_factory
    async for session in get_session(session_factory):
        yield session


DbSession = Annotated[AsyncSession, Depends(get_db)]


# --- customer authentication (reuses the identity JWT + Redis blacklist) ----
async def get_current_user_id(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> uuid.UUID:
    if not credentials:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    jwt_manager = request.app.state.user_jwt_manager
    try:
        payload = jwt_manager.decode_token(credentials.credentials, expected_type="access")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=401, detail="Invalid or expired token") from exc
    redis = request.app.state.redis_client
    if await redis.is_access_token_blacklisted(payload.jti):
        raise HTTPException(status_code=401, detail="Token has been revoked")
    return uuid.UUID(payload.sub)


async def get_optional_user_id(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> uuid.UUID | None:
    if not credentials:
        return None
    try:
        return await get_current_user_id(request, credentials)
    except HTTPException:
        return None


CurrentUserId = Annotated[uuid.UUID, Depends(get_current_user_id)]
OptionalUserId = Annotated[uuid.UUID | None, Depends(get_optional_user_id)]


# --- services ----------------------------------------------------------------
async def get_category_service(db: DbSession) -> CategoryService:
    return CategoryService(db)


async def get_catalog_service(db: DbSession) -> CatalogService:
    return CatalogService(db)


async def get_order_service(db: DbSession) -> OrderService:
    return OrderService(db)


async def get_payment_service(request: Request, db: DbSession) -> PaymentService:
    email_service = getattr(request.app.state, "email_service", None)
    return PaymentService(
        db,
        razorpay=RazorpayClient(settings),
        email_service=email_service,
    )


CategoryServiceDep = Annotated[CategoryService, Depends(get_category_service)]
CatalogServiceDep = Annotated[CatalogService, Depends(get_catalog_service)]
OrderServiceDep = Annotated[OrderService, Depends(get_order_service)]
PaymentServiceDep = Annotated[PaymentService, Depends(get_payment_service)]
