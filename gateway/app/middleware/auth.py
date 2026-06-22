"""JWT validation middleware — delegates token introspection to UserService."""

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # TODO: validate JWT, call UserService /internal/validate-token
        return await call_next(request)
