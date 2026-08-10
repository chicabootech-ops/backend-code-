from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, Header, Request

from app.config import settings
from app.storefront.dependencies import CurrentUserId, OptionalUserId, PaymentServiceDep
from app.storefront.schemas.order import CheckoutRequest
from app.storefront.schemas.payment import (
    CheckoutResponse,
    PaymentConfigOut,
    PaymentStatusOut,
    VerifyPaymentRequest,
)
from app.storefront.services.payment_service import CheckoutError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/payments", tags=["payments"])


@router.get("/config", response_model=PaymentConfigOut)
async def payment_config() -> PaymentConfigOut:
    """Public: lets the storefront know whether online payment is available."""
    return PaymentConfigOut(
        provider="razorpay",
        enabled=settings.razorpay_configured,
        key_id=settings.razorpay_key_id or None,
    )


@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout(
    payload: CheckoutRequest,
    user_id: CurrentUserId,
    service: PaymentServiceDep,
) -> CheckoutResponse:
    return await service.create_checkout(user_id=user_id, request=payload)


@router.post("/verify", response_model=PaymentStatusOut)
async def verify_payment(
    payload: VerifyPaymentRequest,
    user_id: CurrentUserId,
    service: PaymentServiceDep,
) -> PaymentStatusOut:
    """Browser callback. A hint that something happened — never the verdict.

    May answer 202 with ``code: verification_required``, which the storefront
    must render as "verifying", not as failure.
    """
    return await service.verify_payment(user_id=user_id, payload=payload)


@router.post("/{order_id}/retry", response_model=CheckoutResponse)
async def retry_payment(
    order_id: uuid.UUID,
    user_id: CurrentUserId,
    service: PaymentServiceDep,
) -> CheckoutResponse:
    """Start a fresh attempt on an existing order — never a duplicate order."""
    return await service.retry_payment(order_id=order_id, user_id=user_id)


@router.get("/{order_id}", response_model=PaymentStatusOut)
async def payment_status(
    order_id: uuid.UUID,
    user_id: OptionalUserId,
    service: PaymentServiceDep,
) -> PaymentStatusOut:
    """Authoritative payment state for an order.

    The storefront polls this on return from Razorpay, on page load and on
    refresh, so client state is never what decides what the customer is told.
    """
    return await service.get_status(order_id=order_id, user_id=user_id)


@router.post("/webhook", status_code=200)
async def payment_webhook(
    request: Request,
    service: PaymentServiceDep,
    x_razorpay_signature: str = Header(default=""),
    x_razorpay_event_id: str = Header(default=""),
) -> dict[str, str]:
    """Razorpay server-to-server events — the authoritative payment source.

    ``X-Razorpay-Event-Id`` is the idempotency key: it is stable across Razorpay's
    retries of the same event, so it is what stops a redelivery re-running the
    business side effects.
    """
    raw = await request.body()
    try:
        event = json.loads(raw or b"{}")
    except json.JSONDecodeError as exc:
        # Malformed bodies are not retried into oblivion — tell Razorpay it was
        # a bad request and stop.
        logger.warning("webhook_malformed_body")
        raise CheckoutError("Invalid webhook body.", status_code=400) from exc

    if not isinstance(event, dict):
        raise CheckoutError("Invalid webhook body.", status_code=400)

    return await service.handle_webhook(
        raw_body=raw,
        signature=x_razorpay_signature,
        event=event,
        event_id=x_razorpay_event_id or None,
    )
