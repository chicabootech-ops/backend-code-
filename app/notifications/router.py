"""Meta WhatsApp webhook endpoint.

Mounted outside `/admin` and `/api/user` so it is not caught by the admin guard,
and it takes no customer auth — authenticity comes from the Meta signature, not
from a session.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from app.config import settings
from app.notifications.webhook_service import WhatsAppWebhookService
from app.storefront.dependencies import DbSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/whatsapp", tags=["webhooks"])


@router.get("", response_class=PlainTextResponse)
async def verify_webhook(
    hub_mode: str = Query(default="", alias="hub.mode"),
    hub_verify_token: str = Query(default="", alias="hub.verify_token"),
    hub_challenge: str = Query(default="", alias="hub.challenge"),
) -> str:
    """Meta's one-time subscription handshake. Echoes the challenge back."""
    service = WhatsAppWebhookService(None, settings)  # type: ignore[arg-type]
    try:
        return service.verify_subscription(
            mode=hub_mode, token=hub_verify_token, challenge=hub_challenge
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Verification failed") from exc


@router.post("", status_code=200)
async def receive_webhook(
    request: Request,
    db: DbSession,
    x_hub_signature_256: str = Header(default=""),
) -> dict:
    """Delivery status events from Meta.

    Always answers 200 for anything we accepted, including duplicates — a
    non-2xx makes Meta retry, and retrying something already applied is wasted
    work on both sides.
    """
    raw = await request.body()
    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid body") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid body")

    service = WhatsAppWebhookService(db, settings)
    try:
        return await service.handle(
            raw_body=raw, signature_header=x_hub_signature_256, payload=payload
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Invalid signature") from exc
