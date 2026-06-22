"""Upstream health checks."""

from __future__ import annotations

import asyncio

import httpx

from app.config import settings
from app.core.http_client import get_http_client


async def check_service(name: str, base_url: str) -> dict:
    url = f"{base_url.rstrip('/')}/health"
    client = get_http_client()
    try:
        response = await client.get(url, timeout=5.0)
        healthy = response.status_code == 200
        detail = response.json() if healthy else {"status": response.status_code}
        return {"service": name, "url": url, "healthy": healthy, "detail": detail}
    except httpx.HTTPError as exc:
        return {"service": name, "url": url, "healthy": False, "detail": str(exc)}


async def aggregate_health() -> dict:
    checks = await asyncio.gather(
        check_service("userservice", settings.user_service_url),
        check_service("backend", settings.backend_url),
        check_service("admin", settings.admin_url),
    )
    healthy = all(c["healthy"] for c in checks)
    return {"status": "ok" if healthy else "degraded", "service": "gateway", "upstream": list(checks)}
