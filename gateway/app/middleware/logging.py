"""Structured request/response logging for proxied traffic."""

from __future__ import annotations

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("gateway.access")


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()
        request_id = getattr(request.state, "request_id", request.headers.get("X-Request-ID", "-"))
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "%s %s %s %.1fms rid=%s svc=%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            request_id,
            response.headers.get("X-Gateway-Service", "-"),
        )
        return response
