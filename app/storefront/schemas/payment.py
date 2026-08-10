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
    """What the storefront needs to render an unambiguous payment state.

    Three separate statuses travel together on purpose: the order's own status,
    the order's derived payment status, and the status of the latest payment
    attempt. Collapsing them is what produces "your order failed" when it was
    only this attempt that failed.
    """

    order_id: UUID
    order_number: int
    #: Order lifecycle: pending | confirmed | shipped | ...
    order_status: str
    #: Derived from the payment: pending | verification_pending | paid | failed | ...
    payment_status: str
    #: The latest attempt itself: created | pending | verification_required | ...
    payment_attempt_status: str = "created"
    attempt_number: int = 0
    provider: str | None = None
    provider_payment_id: str | None = None
    amount_paise: int
    currency: str = "INR"
    invoice_number: int | None = None
    #: True while we are still establishing the truth — the UI must show
    #: "verifying", never "failed", while this holds.
    is_verification_pending: bool = False
    #: True when the customer may safely start a new attempt.
    can_retry: bool = False
    #: True when a human needs to look (duplicate capture, amount mismatch).
    needs_attention: bool = False
    #: Customer-safe explanation, present only for a genuinely failed attempt.
    failure_message: str | None = None


class PaymentConfigOut(BaseModel):
    provider: str = "razorpay"
    enabled: bool
    key_id: str | None = None
