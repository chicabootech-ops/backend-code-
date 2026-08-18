"""Unified Chic A Boo API — identity + storefront + admin."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError

from app.config import settings
from app.db.session import create_engine, create_session_factory
from app.events.bus import EventBus, set_event_bus
from app.events.worker import EventWorker
from app.notifications.admin_router import router as whatsapp_admin_router
from app.notifications.auth_router import router as otp_auth_router
from app.notifications.campaign_router import router as campaign_router
from app.notifications.providers.whatsapp import WhatsAppProvider
from app.notifications.router import router as whatsapp_webhook_router
from app.notifications.service import NotificationService
from app.notifications.types import Channel
from app.notifications.workers import WorkerSupervisor
from app.storefront.lib.catalog_cache import CatalogCache
from app.storefront.lib.razorpay_client import RazorpayClient
from app.storefront.workers.reconciliation_worker import ReconciliationWorker

from app.identity.core.exception_handlers import register_exception_handlers as register_identity_handlers
from app.identity.core.redis.client import RedisClient
from app.identity.core.security.jwt import JWTManager
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
    bouquet,
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
    testimonials,
    wishlist,
)

from app.admin_api.core.exception_handlers import register_exception_handlers as register_admin_handlers
from app.admin_api.core.security.jwt import AdminJWTManager
from app.admin_api.middleware.admin_guard import AdminGuardMiddleware
from app.admin_api.middleware.security_headers import SecurityHeadersMiddleware
from app.admin_api.routers import (
    analytics,
    auth as admin_auth,
    bouquet_options as admin_bouquet_options,
    subscribers as admin_subscribers,
    campaigns as admin_campaigns,
    categories as admin_categories,
    coupons,
    inventory,
    invoices as admin_invoices,
    maintenance as admin_maintenance,
    media as admin_media,
    orders as admin_orders,
    payments as admin_payments,
    products as admin_products,
    testimonials as admin_testimonials,
    users as admin_users,
)

logging.basicConfig(level=settings.log_level.upper())
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = create_engine(settings.database_dsn)
    session_factory = create_session_factory(engine)

    # Upstash requires TLS — the URL must be rediss://, not redis://. A plain
    # redis:// URL still opens the TCP socket, so the failure surfaces late as a
    # ConnectionError on the protocol handshake rather than as a refused connect.
    if settings.redis_url.startswith("redis://") and "upstash.io" in settings.redis_url:
        logger.error(
            "REDIS_URL uses redis:// against Upstash, which requires TLS. Every "
            "Redis call will fail. Change it to rediss://"
        )

    # protocol=2 (RESP2): redis-py 8 defaults to RESP3, whose HELLO handshake
    # Upstash closes ("Connection closed by server"). The health check + keepalive
    # + retry keep pooled connections alive against Upstash's idle-close behaviour.
    #
    # Timeouts and retries are deliberately tight. Redis here is a cache and a
    # token blacklist, and every caller already degrades gracefully without it —
    # so the cost of an outage should be measured in milliseconds, not seconds.
    # The previous settings (10s timeouts, 3 retries with backoff to 3s) meant a
    # dead Redis added ~2s to *every authenticated request*, because the token
    # blacklist check sits on that path.
    redis_raw = Redis.from_url(
        settings.redis_url,
        decode_responses=False,
        protocol=2,
        health_check_interval=30,
        socket_keepalive=True,
        socket_connect_timeout=5,
        socket_timeout=5,
        retry=Retry(ExponentialBackoff(cap=1.0, base=0.25), retries=2),
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

    notification_providers = {
        Channel.WHATSAPP: WhatsAppProvider(settings),
    }

    def build_notifications(session):
        return NotificationService(session, settings, providers=notification_providers)

    app.state.notification_providers = notification_providers
    app.state.build_notifications = build_notifications

    phone_service = PhoneService(
        settings,
        redis_client,
        rate_limit_service,
        notifications_factory=build_notifications,
    )

    # An empty HMAC secret means anyone can forge an admin token, so this is a
    # hard stop in production rather than a warning.
    if not settings.admin_jwt_secret:
        if settings.is_production:
            raise RuntimeError("ADMIN_JWT_SECRET must be set in production")
        logger.warning("ADMIN_JWT_SECRET is empty — set it in .env before deploying")
    elif len(settings.admin_jwt_secret) < 32 and settings.is_production:
        raise RuntimeError("ADMIN_JWT_SECRET must be at least 32 characters in production")
    if not settings.whatsapp_configured:
        logger.error(
            "WhatsApp is not configured — no OTP or order update can be delivered. "
            "Set WHATSAPP_ENABLED=true, WHATSAPP_ACCESS_TOKEN and "
            "WHATSAPP_PHONE_NUMBER_ID"
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
    app.state.rate_limit_service = rate_limit_service

    event_bus = EventBus(redis_raw)
    set_event_bus(event_bus)
    app.state.event_bus = event_bus
    event_worker = EventWorker(event_bus, CatalogCache(redis_client))
    await event_worker.start()
    app.state.event_worker = event_worker

    # Resolves payments whose webhook or callback never arrived. Without this a
    # debited customer waits on a human noticing.
    reconciliation_worker = ReconciliationWorker(
        session_factory, RazorpayClient(settings), email_service=email_service
    )
    await reconciliation_worker.start()
    app.state.reconciliation_worker = reconciliation_worker

    notification_workers = WorkerSupervisor(
        session_factory, settings, build_notifications=build_notifications
    )
    await notification_workers.start()
    app.state.notification_workers = notification_workers

    logger.info("Chic A Boo API started (env=%s)", settings.app_env)
    yield

    await notification_workers.stop()
    await event_worker.stop()
    await reconciliation_worker.stop()
    await WhatsAppProvider.aclose()
    await RazorpayClient.aclose()
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

# `allow_credentials=True` with a wildcard origin makes Starlette echo back
# whichever Origin asked — any site could then read authenticated responses.
# Production must name its origins; only local dev falls back to localhost.
_cors_origins = settings.cors_origin_list
if not _cors_origins:
    if settings.is_production:
        raise RuntimeError(
            "CORS_ORIGINS must list the storefront and admin origins in production."
        )
    _cors_origins = ["http://localhost:3000", "http://localhost:3001"]
    logger.warning("CORS_ORIGINS not set — defaulting to localhost dev origins")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "X-Requested-With"],
    max_age=600,
)
app.add_middleware(SecurityHeadersMiddleware)
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
app.include_router(testimonials.router)
app.include_router(bouquet.router)

# Admin
app.include_router(admin_auth.router)
app.include_router(admin_categories.router)
app.include_router(admin_products.router)
app.include_router(admin_users.router)
app.include_router(admin_orders.router)
app.include_router(admin_payments.router)

# Provider webhooks (no auth — authenticity comes from the Meta signature)
app.include_router(whatsapp_webhook_router)
app.include_router(otp_auth_router)
app.include_router(whatsapp_admin_router)
app.include_router(campaign_router)
app.include_router(admin_invoices.router)
app.include_router(admin_maintenance.router)
app.include_router(admin_media.router)
app.include_router(admin_testimonials.router)
app.include_router(admin_bouquet_options.router)
app.include_router(admin_subscribers.router)
app.include_router(admin_campaigns.router)
app.include_router(inventory.router)
app.include_router(analytics.router)
app.include_router(coupons.router)


def _redis_target(url: str) -> str:
    """`scheme://host:port` with any credentials removed."""
    try:
        parsed = urlsplit(url)
        return f"{parsed.scheme}://{parsed.hostname or '?'}:{parsed.port or '?'}"
    except ValueError:
        return "unparseable"


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
        # Which host it failed against is the difference between "REDIS_URL was
        # never set on the host" and "the real Redis is down"; credentials are
        # stripped so this stays safe to expose.
        checks["redis_target"] = _redis_target(settings.redis_url)

    return JSONResponse(
        status_code=200 if ok else 503,
        content={"status": "ok" if ok else "degraded", "checks": checks},
    )
