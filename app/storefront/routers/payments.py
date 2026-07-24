from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Header, Request

from app.config import settings
from app.storefront.dependencies import CurrentUserId, PaymentServiceDep
from app.storefront.schemas.order import CheckoutRequest
from app.storefront.schemas.payment import (
    CheckoutResponse,
    PaymentConfigOut,
    PaymentStatusOut,
    VerifyPaymentRequest,
)
from app.storefront.services.payment_service import CheckoutError

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
    return await service.verify_payment(user_id=user_id, payload=payload)


@router.get("/{order_id}", response_model=PaymentStatusOut)
async def payment_status(
    order_id: uuid.UUID,
    user_id: CurrentUserId,
    service: PaymentServiceDep,
) -> PaymentStatusOut:
    return await service.get_status(order_id=order_id, user_id=user_id)


@router.post("/webhook", status_code=200)
async def payment_webhook(
    request: Request,
    service: PaymentServiceDep,
    x_razorpay_signature: str = Header(default=""),
) -> dict[str, str]:
    raw = await request.body()
    try:
        event = json.loads(raw or b"{}")
    except json.JSONDecodeError as exc:
        raise CheckoutError("Invalid webhook body.", status_code=400) from exc
    await service.handle_webhook(raw_body=raw, signature=x_razorpay_signature, event=event)
    return {"status": "ok"}
