"""Unified Chic A Boo API — identity + storefront + admin."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError

from app.config import settings
from app.db.session import create_engine, create_session_factory

from app.identity.core.exception_handlers import register_exception_handlers as register_identity_handlers
from app.identity.core.redis.client import RedisClient
from app.identity.core.security.jwt import JWTManager
from app.identity.integrations.message_central import MessageCentralClient
from app.identity.integrations.r2.client import R2Client
from app.identity.middleware.request_context import RequestContextMiddleware
from app.identity.routers import (
    addresses,
    auth as identity_auth,
    avatar,
    internal,
    phone,
    preferences,
    security,
    user,
)
from app.identity.services.account_service import AccountService
from app.identity.services.auth_service import AuthService
from app.identity.services.avatar_service import AvatarService
from app.identity.services.email_service import EmailService
from app.identity.services.phone_service import PhoneService
from app.identity.services.rate_limit_service import RateLimitService
from app.identity.services.token_service import TokenService

from app.storefront.routers import (
    cart,
    categories,
    newsletter,
    notifications,
    orders,
    payments,
    products,
    returns,
    reviews,
    search,
    sections,
    wishlist,
)

from app.admin_api.core.exception_handlers import register_exception_handlers as register_admin_handlers
from app.admin_api.core.security.jwt import AdminJWTManager
from app.admin_api.middleware.admin_guard import AdminGuardMiddleware
from app.admin_api.routers import (
    analytics,
    auth as admin_auth,
    categories as admin_categories,
    coupons,
    inventory,
    invoices as admin_invoices,
    maintenance as admin_maintenance,
    orders as admin_orders,
    products as admin_products,
    users as admin_users,
)

logging.basicConfig(level=settings.log_level.upper())
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = create_engine(settings.database_dsn)
    session_factory = create_session_factory(engine)

    # protocol=2 (RESP2): redis-py 8 defaults to RESP3, whose HELLO handshake
    # Upstash closes ("Connection closed by server"). The health check + keepalive
    # + retry keep pooled connections alive against Upstash's idle-close behaviour.
    redis_raw = Redis.from_url(
        settings.redis_url,
        decode_responses=False,
        protocol=2,
        health_check_interval=30,
        socket_keepalive=True,
        socket_connect_timeout=10,
        socket_timeout=10,
        retry=Retry(ExponentialBackoff(cap=3, base=0.1), retries=3),
        retry_on_error=[RedisConnectionError, RedisTimeoutError],
    )
    redis_client = RedisClient(redis_raw)

    user_jwt_manager = JWTManager(
        settings.jwt_private_key_pem,
        settings.jwt_public_key_pem,
        access_ttl_seconds=settings.jwt_access_ttl_seconds,
    )
    email_service = EmailService(settings)
    token_service = TokenService(settings, user_jwt_manager, redis_client)
    rate_limit_service = RateLimitService(redis_client)
    auth_service = AuthService(
        settings,
        token_service,
        email_service,
        rate_limit_service,
        user_jwt_manager,
        redis_client,
    )
    account_service = AccountService()
    r2_client = R2Client(
        endpoint_url=settings.r2_endpoint,
        access_key_id=settings.effective_r2_access_key_id,
        secret_access_key=settings.effective_r2_secret_access_key,
        bucket_name=settings.effective_r2_bucket_name,
        upload_ttl_seconds=settings.avatar_upload_url_ttl_seconds,
        get_ttl_seconds=settings.avatar_get_url_ttl_seconds,
    )
    avatar_service = AvatarService(r2_client)
    message_central = MessageCentralClient(settings)
    phone_service = PhoneService(settings, redis_client, rate_limit_service, message_central)

    if not settings.admin_jwt_secret:
        logger.warning("ADMIN_JWT_SECRET is empty — set it in .env")
    if not message_central.configured:
        logger.warning(
            "Message Central not configured — phone OTP disabled until "
            "MESSAGE_CENTRAL_CUSTOMER_ID / EMAIL / PASSWORD are set"
        )
    if not settings.resend_api_key and not (
        settings.smtp_host and settings.smtp_user and settings.smtp_pass
    ):
        logger.warning("No email provider configured — set RESEND_API_KEY or SMTP_*")

    admin_jwt_manager = AdminJWTManager(settings.admin_jwt_secret, settings.admin_jwt_ttl_seconds)

    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.redis_client = redis_client
    app.state.user_jwt_manager = user_jwt_manager
    app.state.admin_jwt_manager = admin_jwt_manager
    app.state.auth_service = auth_service
    app.state.account_service = account_service
    app.state.avatar_service = avatar_service
    app.state.phone_service = phone_service
    app.state.email_service = email_service

    logger.info("Chic A Boo API started (env=%s)", settings.app_env)
    yield

    await redis_client.close()
    await engine.dispose()
    logger.info("Chic A Boo API shutdown complete")


app = FastAPI(
    title="Chic A Boo API",
    description="Unified API — identity, storefront catalog/commerce, and admin",
    version="1.0.0",
    lifespan=lifespan,
)

register_identity_handlers(app)
register_admin_handlers(app)


from app.storefront.lib.razorpay_client import PaymentGatewayError
from app.storefront.services.order_service import OrderError
from app.storefront.services.payment_service import CheckoutError


@app.exception_handler(CheckoutError)
async def _handle_checkout_error(_request: Request, exc: CheckoutError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


@app.exception_handler(OrderError)
async def _handle_order_error(_request: Request, exc: OrderError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


@app.exception_handler(PaymentGatewayError)
async def _handle_gateway_error(_request: Request, exc: PaymentGatewayError) -> JSONResponse:
    return JSONResponse(
        status_code=502,
        content={"error": {"code": exc.code, "message": exc.message}},
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(AdminGuardMiddleware)

# Identity
app.include_router(identity_auth.router)
app.include_router(user.router)
app.include_router(phone.router)
app.include_router(addresses.router)
app.include_router(preferences.router)
app.include_router(security.router)
app.include_router(avatar.router)
app.include_router(internal.router)

# Storefront
app.include_router(products.router)
app.include_router(categories.router)
app.include_router(sections.router)
app.include_router(search.router)
app.include_router(cart.router)
app.include_router(wishlist.router)
app.include_router(orders.router)
app.include_router(payments.router)
app.include_router(reviews.router)
app.include_router(returns.router)
app.include_router(newsletter.router)
app.include_router(notifications.router)

# Admin
app.include_router(admin_auth.router)
app.include_router(admin_categories.router)
app.include_router(admin_products.router)
app.include_router(admin_users.router)
app.include_router(admin_orders.router)
app.include_router(admin_invoices.router)
app.include_router(admin_maintenance.router)
app.include_router(inventory.router)
app.include_router(analytics.router)
app.include_router(coupons.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "chicaboo-api"}


@app.get("/health/ready")
async def health_ready(request: Request):
    """JWT signing + Redis readiness (no secrets returned)."""
    checks: dict[str, str] = {}
    ok = True
    try:
        token, _, _ = request.app.state.user_jwt_manager.create_access_token(
            "00000000-0000-0000-0000-000000000000"
        )
        checks["jwt"] = "ok" if token else "empty"
    except Exception as exc:  # noqa: BLE001
        ok = False
        checks["jwt"] = f"error:{type(exc).__name__}"

    try:
        await request.app.state.redis_client.ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        ok = False
        checks["redis"] = f"error:{type(exc).__name__}"

    return JSONResponse(
        status_code=200 if ok else 503,
        content={"status": "ok" if ok else "degraded", "checks": checks},
    )
