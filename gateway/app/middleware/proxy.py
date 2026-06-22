"""Reverse proxy — forwards requests to internal services."""

import httpx
from fastapi import Request, Response

from app.config import settings

ROUTE_MAP = [
    ("/api/user", settings.user_service_url),
    ("/admin", settings.admin_url),
    ("/api", settings.backend_url),
]


async def proxy_request(request: Request) -> Response:
    path = request.url.path
    target_base = settings.backend_url

    for prefix, base_url in ROUTE_MAP:
        if path.startswith(prefix):
            target_base = base_url
            break

    target_url = f"{target_base}{path}"
    if request.url.query:
        target_url = f"{target_url}?{request.url.query}"

    headers = dict(request.headers)
    headers.pop("host", None)

    async with httpx.AsyncClient() as client:
        upstream = await client.request(
            method=request.method,
            url=target_url,
            headers=headers,
            content=await request.body(),
        )

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=dict(upstream.headers),
    )
