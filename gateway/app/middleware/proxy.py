"""Reverse proxy with timeouts, header forwarding, and error propagation."""

from __future__ import annotations

import json
import logging

import httpx
from fastapi import Request, Response

from app.core.http_client import get_http_client
from app.routing.route_map import resolve_upstream

logger = logging.getLogger(__name__)

HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
    }
)

FORWARDED_REQUEST_HEADERS = frozenset(
    {
        "authorization",
        "content-type",
        "accept",
        "accept-language",
        "user-agent",
        "x-device-name",
        "x-device-type",
        "x-request-id",
        "cookie",
    }
)


def _build_forward_headers(request: Request) -> dict[str, str]:
    headers: dict[str, str] = {}
    for name, value in request.headers.items():
        lower = name.lower()
        if lower in HOP_BY_HOP_HEADERS:
            continue
        if lower in FORWARDED_REQUEST_HEADERS or lower.startswith("x-"):
            headers[name] = value

    request_id = getattr(request.state, "request_id", None)
    if request_id:
        headers["X-Request-ID"] = request_id

    return headers


def _filter_response_headers(upstream_headers: httpx.Headers) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, value in upstream_headers.items():
        if name.lower() not in HOP_BY_HOP_HEADERS:
            out[name] = value
    return out


def _error_response(*, status_code: int, code: str, message: str, request_id: str | None) -> Response:
    body = {"error": message, "code": code}
    headers = {"Content-Type": "application/json"}
    if request_id:
        headers["X-Request-ID"] = request_id
    return Response(content=json.dumps(body), status_code=status_code, headers=headers)


async def proxy_request(request: Request) -> Response:
    path = request.url.path
    owner = resolve_upstream(path)
    target_url = f"{owner.base_url.rstrip('/')}{path}"
    if request.url.query:
        target_url = f"{target_url}?{request.url.query}"

    headers = _build_forward_headers(request)
    request_id = headers.get("X-Request-ID")

    logger.debug("Proxy %s %s -> %s (%s)", request.method, path, target_url, owner.service)

    client = get_http_client()
    try:
        upstream = await client.request(
            method=request.method,
            url=target_url,
            headers=headers,
            content=await request.body(),
        )
    except httpx.TimeoutException:
        logger.warning("Upstream timeout: %s %s", request.method, target_url)
        return _error_response(
            status_code=504,
            code="upstream_timeout",
            message=f"Upstream service timed out ({owner.service})",
            request_id=request_id,
        )
    except httpx.RequestError as exc:
        logger.warning("Upstream error: %s %s — %s", request.method, target_url, exc)
        return _error_response(
            status_code=502,
            code="upstream_unavailable",
            message=f"Upstream service unavailable ({owner.service})",
            request_id=request_id,
        )

    response_headers = _filter_response_headers(upstream.headers)
    if request_id:
        response_headers["X-Request-ID"] = request_id
    response_headers["X-Gateway-Service"] = owner.service

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=response_headers,
    )
