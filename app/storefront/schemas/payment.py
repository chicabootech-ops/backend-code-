from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class RazorpayCheckoutOut(BaseModel):
    """Everything the Razorpay Checkout widget needs on the browser."""

    key_id: str
    razorpay_order_id: str
    amount_paise: int
    currency: str = "INR"
    name: str = "Chic A Boo"
    description: str
    prefill_name: str | None = None
    prefill_email: str | None = None
    prefill_contact: str | None = None


class CheckoutResponse(BaseModel):
    order_id: UUID
    order_number: int
    grand_total_paise: int
    payment_status: str
    razorpay: RazorpayCheckoutOut | None = None


class VerifyPaymentRequest(BaseModel):
    order_id: UUID
    razorpay_order_id: str = Field(min_length=1)
    razorpay_payment_id: str = Field(min_length=1)
    razorpay_signature: str = Field(min_length=1)


class PaymentStatusOut(BaseModel):
    order_id: UUID
    order_number: int
    payment_status: str
    order_status: str
    provider: str | None = None
    provider_payment_id: str | None = None
    amount_paise: int
    invoice_number: int | None = None


class PaymentConfigOut(BaseModel):
    provider: str = "razorpay"
    enabled: bool
    key_id: str | None = None
