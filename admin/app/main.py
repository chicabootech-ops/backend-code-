from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.core.exception_handlers import register_exception_handlers
from app.core.security.jwt import AdminJWTManager
from app.db.session import create_engine, create_session_factory
from app.middleware.admin_guard import AdminGuardMiddleware
from app.routers import analytics, auth, categories, coupons, inventory, orders, products, users

logging.basicConfig(level=settings.log_level.upper())
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.admin_jwt_secret:
        logger.warning("ADMIN_JWT_SECRET is empty — set it in admin/.env")

    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    jwt_manager = AdminJWTManager(settings.admin_jwt_secret, settings.admin_jwt_ttl_seconds)

    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.jwt_manager = jwt_manager

    logger.info("Admin service started (env=%s)", settings.app_env)
    yield

    await engine.dispose()
    logger.info("Admin service shutdown complete")


app = FastAPI(
    title="Chic A Boo Admin",
    description="Internal dashboard — categories, products, users, orders",
    version="0.2.0",
    lifespan=lifespan,
)

register_exception_handlers(app)
app.add_middleware(AdminGuardMiddleware)

app.include_router(auth.router)
app.include_router(categories.router)
app.include_router(products.router)
app.include_router(users.router)
app.include_router(orders.router)
app.include_router(inventory.router)
app.include_router(analytics.router)
app.include_router(coupons.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "admin"}
