"""Checks admin JWT on /admin routes only."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

PUBLIC_PREFIXES = (
    "/admin/auth/login",
)


class AdminGuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if request.method == "OPTIONS":
            return await call_next(request)

        # Only guard admin API space; storefront + identity are unaffected.
        if not path.startswith("/admin"):
            return await call_next(request)

        if any(path.startswith(p) for p in PUBLIC_PREFIXES):
            return await call_next(request)

        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"error": "Admin authorization required", "code": "missing_token"},
            )

        jwt_manager = getattr(request.app.state, "admin_jwt_manager", None)
        if jwt_manager is None:
            return await call_next(request)

        try:
            jwt_manager.decode_token(auth.removeprefix("Bearer ").strip())
        except Exception:
            return JSONResponse(
                status_code=401,
                content={"error": "Invalid admin token", "code": "invalid_token"},
            )

        return await call_next(request)
