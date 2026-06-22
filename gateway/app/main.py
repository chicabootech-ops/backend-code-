from contextlib import asynccontextmanager
import logging
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.core.http_client import close_http_client
from app.health import aggregate_health
from app.middleware.auth import AuthMiddleware
from app.middleware.logging import LoggingMiddleware
from app.middleware.proxy import proxy_request
from app.middleware.rate_limit import RateLimitMiddleware
from app.routing.route_map import build_route_map

logging.basicConfig(level=settings.log_level.upper())
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Gateway started (env=%s)", settings.app_env)
    yield
    await close_http_client()
    logger.info("Gateway shutdown complete")


app = FastAPI(
    title="Chic A Boo Gateway",
    description=(
        "Single entry point for external traffic. Proxies `/api/user/*` to UserService, "
        "`/api/*` to commerce backend, `/admin` to admin service."
    ),
    version="1.0.0",
    lifespan=lifespan,
    openapi_tags=[
        {"name": "gateway", "description": "Health and routing metadata"},
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(LoggingMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(AuthMiddleware)


@app.middleware("http")
async def attach_request_id(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid4()))
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.get("/health", tags=["gateway"])
async def health():
    return {"status": "ok", "service": "gateway"}


@app.get("/health/upstream", tags=["gateway"])
async def health_upstream():
    return await aggregate_health()


@app.get("/health/routes", tags=["gateway"])
async def health_routes():
    routes = [
        {
            "prefix": r.prefix,
            "service": r.service,
            "base_url": r.base_url,
            "description": r.description,
        }
        for r in build_route_map()
    ]
    return {"routes": routes}


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
async def gateway_proxy(request: Request):
    return await proxy_request(request)
