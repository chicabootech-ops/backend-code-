"""The payment state machine — the rules the whole rebuild rests on.

These are pure-function tests: no database, no network, no Razorpay account. That
is deliberate, because the properties being asserted here are exactly the ones
that were violated in production, and they must hold regardless of environment.

Case numbers refer to the payment edge-case specification.
"""

from __future__ import annotations

import pytest

from app.storefront.services.payment_state import (
    GENERIC_FAILURE_COPY,
    RETRYABLE,
    SETTLED,
    UNRESOLVED,
    InvalidTransition,
    PaymentStatus,
    TransitionSource,
    assert_transition,
    can_transition,
    failure_copy,
    from_provider_status,
    from_webhook_event,
    project_order,
)

S = PaymentStatus
SRC = TransitionSource


# --------------------------------------------------------------------------- #
# The core principle: the browser is never the authority
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("start", sorted(UNRESOLVED))
@pytest.mark.parametrize("target", sorted(SETTLED))
def test_client_callback_can_never_settle_a_payment(start, target):
    """A frontend callback must not be able to move money into a settled state.

    The rejection reason varies — some of these edges do not exist at all, others
    exist but are gated on the source — and that distinction does not matter
    here. What matters is that no path lets the browser settle a payment.
    """
    allowed, _ = can_transition(start, target, source=SRC.CLIENT_CALLBACK)
    assert allowed is False


def test_the_settle_gate_is_specifically_about_source():
    """On an edge that genuinely exists, the browser is refused *because* of who
    it is — not because the transition is impossible."""
    allowed, reason = can_transition(S.PENDING, S.CAPTURED, source=SRC.CLIENT_CALLBACK)
    assert allowed is False
    assert reason == "source_not_authoritative"


@pytest.mark.parametrize("source", [SRC.WEBHOOK, SRC.PROVIDER_FETCH, SRC.ADMIN])
def test_authoritative_sources_may_settle(source):
    allowed, _ = can_transition(S.PENDING, S.CAPTURED, source=source)
    assert allowed is True


def test_client_callback_may_still_report_uncertainty():
    """It can say "I don't know" — that is all it is trusted to say."""
    allowed, _ = can_transition(
        S.CREATED, S.VERIFICATION_REQUIRED, source=SRC.CLIENT_CALLBACK
    )
    assert allowed is True


# --------------------------------------------------------------------------- #
# Case 29 — out-of-order webhooks must not undo a capture
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("target", [S.FAILED, S.CANCELLED, S.EXPIRED, S.PENDING])
def test_captured_is_never_downgraded(target):
    allowed, reason = can_transition(S.CAPTURED, target, source=SRC.WEBHOOK)
    assert allowed is False, f"captured must not be downgradable to {target}"
    assert reason == "transition_not_allowed"


def test_captured_may_only_move_into_refund_states():
    for target in (S.REFUND_PENDING, S.PARTIALLY_REFUNDED, S.REFUNDED):
        allowed, _ = can_transition(S.CAPTURED, target, source=SRC.WEBHOOK)
        assert allowed is True


def test_refunded_is_terminal():
    for target in list(S):
        if target is S.REFUNDED:
            continue
        allowed, reason = can_transition(S.REFUNDED, target, source=SRC.ADMIN)
        assert allowed is False
        assert reason in {"terminal_state", "no_change"}


# --------------------------------------------------------------------------- #
# Case 6 — webhook says captured after the frontend reported failure
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("start", [S.FAILED, S.CANCELLED, S.EXPIRED])
def test_late_capture_overturns_a_non_settled_negative(start):
    """The customer's money moved; whatever the browser said is irrelevant."""
    allowed, _ = can_transition(start, S.CAPTURED, source=SRC.WEBHOOK)
    assert allowed is True


# --------------------------------------------------------------------------- #
# Cases 7/8/9 — replays are a no-op, not an error and not a second effect
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("status", list(S))
def test_same_state_transition_is_a_noop_not_an_error(status):
    allowed, reason = can_transition(status, status, source=SRC.WEBHOOK)
    assert allowed is False
    assert reason == "no_change"


def test_assert_transition_raises_with_context():
    with pytest.raises(InvalidTransition) as exc:
        assert_transition(S.CAPTURED, S.FAILED, source=SRC.WEBHOOK)
    assert exc.value.current is S.CAPTURED
    assert exc.value.target is S.FAILED


# --------------------------------------------------------------------------- #
# Payment status vs order status must stay distinct
# --------------------------------------------------------------------------- #
def test_capture_confirms_the_order():
    assert project_order(S.CAPTURED) == ("confirmed", "paid")


@pytest.mark.parametrize("status", [S.FAILED, S.CANCELLED, S.EXPIRED])
def test_a_failed_payment_does_not_cancel_the_order(status):
    """Case 2 / Case 19: the order survives so a retry reuses it."""
    order_status, order_payment_status = project_order(status)
    assert order_status is None, "a failed attempt must not move the order's own status"
    assert order_payment_status == "failed"


def test_verification_required_is_not_failure():
    """Case 4/15: the customer must never be told this failed."""
    order_status, order_payment_status = project_order(S.VERIFICATION_REQUIRED)
    assert order_status is None
    assert order_payment_status == "verification_pending"
    assert order_payment_status != "failed"


@pytest.mark.parametrize("status", list(S))
def test_every_status_projects_onto_an_order(status):
    order_status, order_payment_status = project_order(status)
    assert order_payment_status in {
        "pending",
        "verification_pending",
        "authorized",
        "paid",
        "partially_refunded",
        "refunded",
        "failed",
    }
    assert order_status in {None, "confirmed", "refunded"}


# --------------------------------------------------------------------------- #
# Provider vocabulary
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("provider_value", "expected"),
    [
        ("captured", S.CAPTURED),
        ("authorized", S.AUTHORIZED),
        ("failed", S.FAILED),
        ("refunded", S.REFUNDED),
        ("created", S.PENDING),
        ("CAPTURED", S.CAPTURED),
    ],
)
def test_provider_status_mapping(provider_value, expected):
    assert from_provider_status(provider_value) is expected


def test_unknown_provider_status_maps_to_nothing():
    """Case 28: an unrecognised value must not be coerced into a state."""
    assert from_provider_status("some_new_razorpay_state") is None
    assert from_provider_status(None) is None
    assert from_provider_status("") is None


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ("payment.captured", S.CAPTURED),
        ("payment.authorized", S.AUTHORIZED),
        ("payment.failed", S.FAILED),
        ("order.paid", S.CAPTURED),
    ],
)
def test_webhook_event_mapping(event, expected):
    assert from_webhook_event(event) is expected


def test_unknown_webhook_event_is_ignored_not_guessed():
    assert from_webhook_event("payment.dispute.created") is None
    assert from_webhook_event("nonsense") is None


# --------------------------------------------------------------------------- #
# Case 17 — the incident: risk-check failure copy
# --------------------------------------------------------------------------- #
def test_risk_check_failure_has_dedicated_customer_copy():
    copy = failure_copy("payment_risk_check_failed")
    assert copy != GENERIC_FAILURE_COPY
    assert "could not be processed" in copy.lower()
    # It must not blame the order, and must not claim money was taken.
    assert "order failed" not in copy.lower()


def test_unknown_failure_code_falls_back_to_cautious_copy():
    copy = failure_copy("something_new")
    assert copy == GENERIC_FAILURE_COPY
    # The generic line must not promise a refund we cannot guarantee.
    assert "will be refunded" not in copy.lower()


def test_no_failure_code_still_returns_copy():
    assert failure_copy(None) == GENERIC_FAILURE_COPY


# --------------------------------------------------------------------------- #
# Set membership the service relies on
# --------------------------------------------------------------------------- #
def test_status_set_partitioning():
    assert S.CAPTURED in SETTLED
    assert S.VERIFICATION_REQUIRED in UNRESOLVED
    assert S.VERIFICATION_REQUIRED not in RETRYABLE, (
        "an unresolved payment must not be offered as retryable — that invites a "
        "second charge while the first is in flight (Case 18)"
    )
    assert SETTLED.isdisjoint(UNRESOLVED)
    assert SETTLED.isdisjoint(RETRYABLE)
    assert UNRESOLVED.isdisjoint(RETRYABLE)
