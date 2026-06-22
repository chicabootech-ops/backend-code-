"""Rate limiting middleware — per IP and per user."""

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # TODO: Redis-backed rate limiting
        return await call_next(request)
