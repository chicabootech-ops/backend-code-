"""Baseline security response headers for every API response.

The API serves JSON (and invoice PDFs), never HTML that a browser will render as
a document, so the policy is deliberately restrictive: a locked-down CSP plus
the headers that stop MIME sniffing, framing and referrer leakage.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.config import settings

# No HTML is served from this origin, so nothing legitimate needs to load.
_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"

BASE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": _CSP,
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-site",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=()",
}

#: Two years, matching the preload-list requirement. Only sent over HTTPS in
#: production — setting it in local dev would pin localhost to https.
HSTS = "max-age=63072000; includeSubDomains"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._headers = dict(BASE_HEADERS)
        if settings.is_production:
            self._headers["Strict-Transport-Security"] = HSTS

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        for header, value in self._headers.items():
            response.headers.setdefault(header, value)
        # Never let a proxy or browser cache an authenticated response.
        if request.url.path.startswith(("/admin", "/api/user", "/api/orders", "/api/cart")):
            response.headers.setdefault("Cache-Control", "no-store, private")
        return response
