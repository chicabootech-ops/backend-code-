"""Signature verification, amount tampering, and reconciliation selection.

These cover the parts where getting it wrong costs money rather than UX.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.storefront.lib.razorpay_client import RazorpayClient
from app.storefront.services.reconciliation_service import (
    BACKOFF_SCHEDULE,
    MAX_ATTEMPTS,
    _most_advanced,
    _next_at,
)

SECRET = "test_secret_value"


def _client() -> RazorpayClient:
    return RazorpayClient(
        SimpleNamespace(
            razorpay_key_id="rzp_test_key",
            razorpay_key_secret=SECRET,
            razorpay_webhook_secret=SECRET,
            razorpay_configured=True,
        )
    )


# --------------------------------------------------------------------------- #
# Case 27 — webhook signatures
# --------------------------------------------------------------------------- #
def test_valid_webhook_signature_is_accepted():
    body = b'{"event":"payment.captured"}'
    sig = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert _client().verify_webhook_signature(raw_body=body, signature=sig) is True


def test_tampered_webhook_body_is_rejected():
    body = b'{"event":"payment.captured"}'
    sig = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    tampered = b'{"event":"payment.captured","amount":999999}'
    assert _client().verify_webhook_signature(raw_body=tampered, signature=sig) is False


@pytest.mark.parametrize("sig", ["", "deadbeef", "0" * 64])
def test_forged_webhook_signatures_are_rejected(sig):
    assert _client().verify_webhook_signature(raw_body=b"{}", signature=sig) is False


def test_missing_webhook_secret_rejects_everything():
    """Fail closed: an unconfigured secret must not mean 'accept all'."""
    client = RazorpayClient(
        SimpleNamespace(
            razorpay_key_id="k",
            razorpay_key_secret=SECRET,
            razorpay_webhook_secret="",
            razorpay_configured=True,
        )
    )
    body = b"{}"
    sig = hmac.new(b"", body, hashlib.sha256).hexdigest()
    assert client.verify_webhook_signature(raw_body=body, signature=sig) is False


# --------------------------------------------------------------------------- #
# Checkout callback signatures
# --------------------------------------------------------------------------- #
def test_valid_checkout_signature_is_accepted():
    order_id, payment_id = "order_ABC", "pay_XYZ"
    sig = hmac.new(
        SECRET.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256
    ).hexdigest()
    assert (
        _client().verify_checkout_signature(
            order_id=order_id, payment_id=payment_id, signature=sig
        )
        is True
    )


def test_checkout_signature_bound_to_the_id_pair():
    """A signature for one payment must not validate another (replay defence)."""
    sig = hmac.new(
        SECRET.encode(), b"order_ABC|pay_XYZ", hashlib.sha256
    ).hexdigest()
    assert (
        _client().verify_checkout_signature(
            order_id="order_ABC", payment_id="pay_DIFFERENT", signature=sig
        )
        is False
    )
    assert (
        _client().verify_checkout_signature(
            order_id="order_OTHER", payment_id="pay_XYZ", signature=sig
        )
        is False
    )


# --------------------------------------------------------------------------- #
# Amount tampering — PaymentService._amount_mismatch
# --------------------------------------------------------------------------- #
def _mismatch(expected_paise: int, entity: dict, currency: str = "INR"):
    from app.storefront.services.payment_service import PaymentService

    payment = SimpleNamespace(amount_paise=expected_paise, currency=currency)
    return PaymentService._amount_mismatch(None, payment, entity)  # noqa: SLF001


def test_matching_amount_passes():
    assert _mismatch(50000, {"amount": 50000, "currency": "INR"}) is None


def test_underpayment_is_caught():
    result = _mismatch(50000, {"amount": 100, "currency": "INR"})
    assert result is not None and "Amount mismatch" in result


def test_overpayment_is_caught():
    result = _mismatch(50000, {"amount": 500000, "currency": "INR"})
    assert result is not None and "Amount mismatch" in result


def test_currency_swap_is_caught():
    result = _mismatch(50000, {"amount": 50000, "currency": "USD"})
    assert result is not None and "Currency mismatch" in result


def test_absent_amount_is_not_treated_as_mismatch():
    """Some entities omit amount; that is unknown, not wrong."""
    assert _mismatch(50000, {"currency": "INR"}) is None


# --------------------------------------------------------------------------- #
# Case 20 — picking the truth when several payments exist for one order
# --------------------------------------------------------------------------- #
def test_capture_wins_over_failures():
    chosen = _most_advanced(
        [
            {"id": "pay_1", "status": "failed"},
            {"id": "pay_2", "status": "captured"},
            {"id": "pay_3", "status": "failed"},
        ]
    )
    assert chosen["id"] == "pay_2"


def test_authorized_wins_over_pending():
    chosen = _most_advanced(
        [{"id": "a", "status": "created"}, {"id": "b", "status": "authorized"}]
    )
    assert chosen["id"] == "b"


def test_no_payments_means_no_verdict():
    """Case 10: absence of a payment is what licenses cancelling — nothing else."""
    assert _most_advanced([]) is None


def test_unknown_status_does_not_outrank_a_capture():
    chosen = _most_advanced(
        [{"id": "a", "status": "captured"}, {"id": "b", "status": "brand_new_status"}]
    )
    assert chosen["id"] == "a"


# --------------------------------------------------------------------------- #
# Case 16 — backoff must actually back off
# --------------------------------------------------------------------------- #
def test_backoff_is_monotonically_increasing():
    assert BACKOFF_SCHEDULE == sorted(BACKOFF_SCHEDULE)
    assert BACKOFF_SCHEDULE[0] >= timedelta(minutes=1), "do not hammer the gateway"


def test_next_attempt_time_grows_with_attempts():
    before = datetime.now(UTC)
    first = _next_at(1) - before
    last = _next_at(MAX_ATTEMPTS) - before
    assert last > first


def test_backoff_is_clamped_beyond_the_schedule():
    """An attempt count past the end must not index out of range."""
    assert _next_at(MAX_ATTEMPTS + 50) > datetime.now(UTC)
