from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.storefront.db.session import get_session
from app.storefront.lib.catalog_cache import CatalogCache
from app.storefront.lib.razorpay_client import RazorpayClient
from app.storefront.services.cart_service import CartService
from app.storefront.services.cached_catalog_service import CachedCatalogService
from app.storefront.services.catalog_service import CatalogService
from app.storefront.services.category_service import CategoryService
from app.storefront.services.newsletter_service import NewsletterService
from app.storefront.services.notification_service import NotificationService
from app.storefront.services.order_service import OrderService
from app.storefront.services.payment_service import PaymentService
from app.storefront.services.return_service import ReturnService
from app.storefront.services.review_service import ReviewService
from app.storefront.services.search_service import SearchService
from app.storefront.services.testimonial_service import TestimonialService
from app.storefront.services.wishlist_service import WishlistService

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


async def get_catalog_service(request: Request, db: DbSession) -> CachedCatalogService:
    redis = getattr(request.app.state, "redis_client", None)
    return CachedCatalogService(CatalogService(db), CatalogCache(redis))


async def get_order_service(db: DbSession) -> OrderService:
    return OrderService(db)


async def get_payment_service(request: Request, db: DbSession) -> PaymentService:
    email_service = getattr(request.app.state, "email_service", None)
    return PaymentService(
        db,
        razorpay=RazorpayClient(settings),
        email_service=email_service,
    )


async def get_cart_service(db: DbSession) -> CartService:
    return CartService(db)


async def get_wishlist_service(db: DbSession) -> WishlistService:
    return WishlistService(db)


async def get_search_service(db: DbSession) -> SearchService:
    return SearchService(db)


async def get_review_service(db: DbSession) -> ReviewService:
    return ReviewService(db)


async def get_return_service(db: DbSession) -> ReturnService:
    return ReturnService(db)


async def get_newsletter_service(request: Request, db: DbSession) -> NewsletterService:
    return NewsletterService(db, getattr(request.app.state, "email_service", None))


async def get_notification_service(db: DbSession) -> NotificationService:
    return NotificationService(db)


async def get_testimonial_service(db: DbSession) -> TestimonialService:
    return TestimonialService(db)


async def get_catalog_cache(request: Request) -> CatalogCache:
    return CatalogCache(getattr(request.app.state, "redis_client", None))


CategoryServiceDep = Annotated[CategoryService, Depends(get_category_service)]
CatalogServiceDep = Annotated[CachedCatalogService, Depends(get_catalog_service)]
OrderServiceDep = Annotated[OrderService, Depends(get_order_service)]
PaymentServiceDep = Annotated[PaymentService, Depends(get_payment_service)]
CartServiceDep = Annotated[CartService, Depends(get_cart_service)]
WishlistServiceDep = Annotated[WishlistService, Depends(get_wishlist_service)]
SearchServiceDep = Annotated[SearchService, Depends(get_search_service)]
ReviewServiceDep = Annotated[ReviewService, Depends(get_review_service)]
ReturnServiceDep = Annotated[ReturnService, Depends(get_return_service)]
NewsletterServiceDep = Annotated[NewsletterService, Depends(get_newsletter_service)]
NotificationServiceDep = Annotated[NotificationService, Depends(get_notification_service)]
TestimonialServiceDep = Annotated[TestimonialService, Depends(get_testimonial_service)]
CatalogCacheDep = Annotated[CatalogCache, Depends(get_catalog_cache)]
