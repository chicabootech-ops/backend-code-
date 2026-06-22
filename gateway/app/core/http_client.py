"""Shared httpx client for upstream proxying."""

from __future__ import annotations

import httpx

from app.config import settings

_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=settings.proxy_connect_timeout_seconds,
                read=settings.proxy_read_timeout_seconds,
                write=settings.proxy_write_timeout_seconds,
                pool=settings.proxy_connect_timeout_seconds,
            ),
            follow_redirects=False,
        )
    return _client


async def close_http_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
