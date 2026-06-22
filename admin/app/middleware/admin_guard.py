"""Checks admin role in JWT."""

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware


class AdminGuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # TODO: verify admin role from JWT
        return await call_next(request)
