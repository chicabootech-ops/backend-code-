from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from app.config import settings
from app.core.exception_handlers import register_exception_handlers
from app.core.redis.client import RedisClient
from app.core.security.jwt import JWTManager
from app.db.session import create_engine, create_session_factory
from app.middleware.request_context import RequestContextMiddleware
from app.integrations.r2.client import R2Client
from app.routers import addresses, auth, avatar, internal, preferences, security, user
from app.services.account_service import AccountService
from app.services.avatar_service import AvatarService
from app.services.auth_service import AuthService
from app.services.email_service import EmailService
from app.services.rate_limit_service import RateLimitService
from app.services.token_service import TokenService

logging.basicConfig(level=settings.log_level.upper())
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)

    redis_raw = Redis.from_url(settings.redis_url, decode_responses=False)
    redis_client = RedisClient(redis_raw)

    jwt_manager = JWTManager(
        settings.jwt_private_key_pem,
        settings.jwt_public_key_pem,
        access_ttl_seconds=settings.jwt_access_ttl_seconds,
    )
    email_service = EmailService(settings)
    token_service = TokenService(settings, jwt_manager, redis_client)
    rate_limit_service = RateLimitService(redis_client)
    auth_service = AuthService(
        settings,
        token_service,
        email_service,
        rate_limit_service,
        jwt_manager,
        redis_client,
    )
    account_service = AccountService()
    r2_client = R2Client(
        endpoint_url=settings.r2_endpoint,
        access_key_id=settings.r2_access_key_id,
        secret_access_key=settings.r2_secret_access_key,
        bucket_name=settings.r2_bucket_name,
        upload_ttl_seconds=settings.avatar_upload_url_ttl_seconds,
        get_ttl_seconds=settings.avatar_get_url_ttl_seconds,
    )
    avatar_service = AvatarService(r2_client)

    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.redis_client = redis_client
    app.state.jwt_manager = jwt_manager
    app.state.auth_service = auth_service
    app.state.account_service = account_service
    app.state.avatar_service = avatar_service

    logger.info("UserService started (env=%s)", settings.app_env)
    yield

    await redis_client.close()
    await engine.dispose()
    logger.info("UserService shutdown complete")


app = FastAPI(
    title="Chic A Boo UserService",
    description=(
        "Authentication, account management, addresses, preferences, security, and avatar (R2) APIs. "
        "All routes are prefixed with `/api/user`."
    ),
    version="2.0.0",
    lifespan=lifespan,
    openapi_tags=[
        {"name": "auth", "description": "Registration, login, tokens, password reset"},
        {"name": "user", "description": "Profile and onboarding (`/me`)"},
        {"name": "addresses", "description": "Delivery and billing addresses"},
        {"name": "preferences", "description": "Notification and locale preferences"},
        {"name": "security", "description": "Devices, login history, session revocation"},
        {"name": "avatar", "description": "Cloudflare R2 presigned avatar upload flow"},
        {"name": "internal", "description": "Service-to-service endpoints"},
    ],
)

register_exception_handlers(app)
app.add_middleware(RequestContextMiddleware)

app.include_router(auth.router)
app.include_router(user.router)
app.include_router(addresses.router)
app.include_router(preferences.router)
app.include_router(security.router)
app.include_router(avatar.router)
app.include_router(internal.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "userservice"}
