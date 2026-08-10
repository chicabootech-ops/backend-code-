"""The payment state machine.

Every transition of ``commerce.payments.status`` goes through here. The point is
to make two classes of bug structurally impossible:

*   **Downgrading a settled payment.** Razorpay does not guarantee webhook
    ordering, so a ``payment.failed`` for an earlier attempt can land after a
    ``payment.captured``. ``CAPTURED`` therefore has no edge back to ``FAILED``.

*   **Calling "unknown" a failure.** A lost callback, a gateway timeout and a
    signature we could not check are all *absence of information*, not evidence
    of failure. They land in ``VERIFICATION_REQUIRED``, which the reconciler
    resolves against Razorpay and which the storefront renders as "verifying".

The provider is authoritative; the browser is only a hint. Callers pass a
``TransitionSource`` so an unreliable source can never do something a reliable
one should.
"""

from __future__ import annotations

from enum import StrEnum


class PaymentStatus(StrEnum):
    #: Razorpay order exists; the customer has not acted.
    CREATED = "created"
    #: Customer acted; the provider has not reached a terminal answer.
    PENDING = "pending"
    #: We hold a signal we could not confirm. Never shown as failure.
    VERIFICATION_REQUIRED = "verification_required"
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    FAILED = "failed"
    #: Checkout closed and the provider has no payment for the order.
    CANCELLED = "cancelled"
    #: Razorpay order aged out without a payment.
    EXPIRED = "expired"
    REFUND_PENDING = "refund_pending"
    PARTIALLY_REFUNDED = "partially_refunded"
    REFUNDED = "refunded"


class TransitionSource(StrEnum):
    #: Razorpay Checkout's browser callback. A hint, never proof.
    CLIENT_CALLBACK = "client_callback"
    #: Signed server-to-server webhook. Authoritative.
    WEBHOOK = "webhook"
    #: Our own fetch against the Razorpay REST API. Authoritative.
    PROVIDER_FETCH = "provider_fetch"
    #: Reconciler / expiry sweeps.
    SYSTEM = "system"
    #: A human in the admin panel.
    ADMIN = "admin"


#: Sources we will let move a payment into a settled money state.
AUTHORITATIVE_SOURCES = frozenset(
    {TransitionSource.WEBHOOK, TransitionSource.PROVIDER_FETCH, TransitionSource.ADMIN}
)

#: Terminal — nothing may leave these.
TERMINAL = frozenset({PaymentStatus.REFUNDED})

#: Money is settled here; only refund flows may follow.
SETTLED = frozenset(
    {
        PaymentStatus.CAPTURED,
        PaymentStatus.REFUND_PENDING,
        PaymentStatus.PARTIALLY_REFUNDED,
        PaymentStatus.REFUNDED,
    }
)

#: Not yet resolved — the reconciler's work queue.
UNRESOLVED = frozenset(
    {
        PaymentStatus.CREATED,
        PaymentStatus.PENDING,
        PaymentStatus.VERIFICATION_REQUIRED,
        PaymentStatus.AUTHORIZED,
    }
)

#: A customer may start a fresh attempt from these.
RETRYABLE = frozenset(
    {PaymentStatus.FAILED, PaymentStatus.CANCELLED, PaymentStatus.EXPIRED}
)

_S = PaymentStatus

#: Allowed edges. Note deliberately *absent* edges:
#:   CAPTURED -> FAILED       (out-of-order webhook must not undo a capture)
#:   REFUNDED -> anything     (terminal)
_TRANSITIONS: dict[PaymentStatus, frozenset[PaymentStatus]] = {
    _S.CREATED: frozenset(
        {
            _S.PENDING,
            _S.VERIFICATION_REQUIRED,
            _S.AUTHORIZED,
            _S.CAPTURED,
            _S.FAILED,
            _S.CANCELLED,
            _S.EXPIRED,
        }
    ),
    _S.PENDING: frozenset(
        {
            _S.VERIFICATION_REQUIRED,
            _S.AUTHORIZED,
            _S.CAPTURED,
            _S.FAILED,
            _S.CANCELLED,
            _S.EXPIRED,
        }
    ),
    _S.VERIFICATION_REQUIRED: frozenset(
        {_S.AUTHORIZED, _S.CAPTURED, _S.FAILED, _S.CANCELLED, _S.EXPIRED}
    ),
    # An authorization can still be voided or expire, but never "fails" into a
    # capture; capture is the only forward move.
    _S.AUTHORIZED: frozenset({_S.CAPTURED, _S.FAILED, _S.EXPIRED}),
    _S.CAPTURED: frozenset({_S.REFUND_PENDING, _S.PARTIALLY_REFUNDED, _S.REFUNDED}),
    # A late capture must be able to overturn any of these — the customer's money
    # moved, whatever the browser reported at the time (Case 6).
    _S.FAILED: frozenset({_S.VERIFICATION_REQUIRED, _S.CAPTURED, _S.AUTHORIZED}),
    _S.CANCELLED: frozenset({_S.VERIFICATION_REQUIRED, _S.CAPTURED, _S.AUTHORIZED}),
    _S.EXPIRED: frozenset({_S.VERIFICATION_REQUIRED, _S.CAPTURED, _S.AUTHORIZED}),
    # A refund that the provider rejects returns the payment to captured.
    _S.REFUND_PENDING: frozenset(
        {_S.PARTIALLY_REFUNDED, _S.REFUNDED, _S.CAPTURED, _S.FAILED}
    ),
    _S.PARTIALLY_REFUNDED: frozenset({_S.REFUND_PENDING, _S.REFUNDED}),
    _S.REFUNDED: frozenset(),
}


class InvalidTransition(Exception):
    """Raised when a caller asks for an edge the machine does not have."""

    def __init__(
        self,
        current: PaymentStatus,
        target: PaymentStatus,
        source: TransitionSource,
        reason: str = "",
    ) -> None:
        self.current = current
        self.target = target
        self.source = source
        detail = f" ({reason})" if reason else ""
        super().__init__(f"{current} -> {target} rejected for {source}{detail}")


def can_transition(
    current: PaymentStatus | str,
    target: PaymentStatus | str,
    *,
    source: TransitionSource = TransitionSource.SYSTEM,
) -> tuple[bool, str]:
    """``(allowed, reason)`` — reason is empty when allowed.

    Kept side-effect free so callers can probe an edge (and log why it was
    refused) without catching an exception.
    """
    current = PaymentStatus(current)
    target = PaymentStatus(target)

    if current == target:
        # Idempotent replays are normal: a duplicate webhook re-asserting
        # CAPTURED is not an error, it just has nothing to do.
        return False, "no_change"

    if current in TERMINAL:
        return False, "terminal_state"

    allowed = _TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        return False, "transition_not_allowed"

    # The browser may report progress, but it may not be the thing that decides
    # money moved. Only a signed webhook or our own provider fetch may settle.
    if target in SETTLED and source not in AUTHORITATIVE_SOURCES:
        return False, "source_not_authoritative"

    return True, ""


def assert_transition(
    current: PaymentStatus | str,
    target: PaymentStatus | str,
    *,
    source: TransitionSource = TransitionSource.SYSTEM,
) -> None:
    allowed, reason = can_transition(current, target, source=source)
    if not allowed:
        raise InvalidTransition(
            PaymentStatus(current), PaymentStatus(target), source, reason
        )


# --------------------------------------------------------------------------- #
# Projection onto the order
# --------------------------------------------------------------------------- #
#: (order.status, order.payment_status) for a given payment status.
#:
#: Note what a failed payment does *not* do: it does not cancel the order. The
#: order stays 'pending' so the customer can retry onto the same order number
#: rather than us minting a duplicate (Case 2 / Case 19).
_ORDER_PROJECTION: dict[PaymentStatus, tuple[str | None, str]] = {
    _S.CREATED: (None, "pending"),
    _S.PENDING: (None, "pending"),
    _S.VERIFICATION_REQUIRED: (None, "verification_pending"),
    _S.AUTHORIZED: (None, "authorized"),
    _S.CAPTURED: ("confirmed", "paid"),
    _S.FAILED: (None, "failed"),
    _S.CANCELLED: (None, "failed"),
    _S.EXPIRED: (None, "failed"),
    _S.REFUND_PENDING: (None, "paid"),
    _S.PARTIALLY_REFUNDED: (None, "partially_refunded"),
    _S.REFUNDED: ("refunded", "refunded"),
}


def project_order(status: PaymentStatus | str) -> tuple[str | None, str]:
    """Order ``(status, payment_status)`` for a payment status.

    A ``None`` order status means "leave the order's own status alone" — an
    order that is already `shipped` must not be dragged back to `confirmed` by a
    late refund-pending transition.
    """
    return _ORDER_PROJECTION[PaymentStatus(status)]


# --------------------------------------------------------------------------- #
# Razorpay vocabulary -> ours
# --------------------------------------------------------------------------- #
#: Razorpay payment entity `status` field.
_PROVIDER_PAYMENT_STATUS: dict[str, PaymentStatus] = {
    "created": PaymentStatus.PENDING,
    "authorized": PaymentStatus.AUTHORIZED,
    "captured": PaymentStatus.CAPTURED,
    "refunded": PaymentStatus.REFUNDED,
    "failed": PaymentStatus.FAILED,
}

#: Razorpay webhook event name -> the status it asserts.
_WEBHOOK_EVENT_STATUS: dict[str, PaymentStatus] = {
    "payment.authorized": PaymentStatus.AUTHORIZED,
    "payment.captured": PaymentStatus.CAPTURED,
    "payment.failed": PaymentStatus.FAILED,
    "order.paid": PaymentStatus.CAPTURED,
}

#: Events we understand but that carry no payment-status assertion.
REFUND_EVENTS = frozenset(
    {"refund.created", "refund.processed", "refund.failed", "refund.speed_changed"}
)


def from_provider_status(value: str | None) -> PaymentStatus | None:
    """Map a Razorpay payment entity status onto ours, or None if unknown."""
    if not value:
        return None
    return _PROVIDER_PAYMENT_STATUS.get(value.lower())


def from_webhook_event(event_type: str) -> PaymentStatus | None:
    return _WEBHOOK_EVENT_STATUS.get(event_type)


# --------------------------------------------------------------------------- #
# Customer-facing failure copy
# --------------------------------------------------------------------------- #
#: Razorpay failure reasons we translate deliberately. Anything unmapped gets the
#: generic line — we would rather be vague than tell a customer something wrong.
_FAILURE_COPY: dict[str, str] = {
    "payment_risk_check_failed": (
        "Payment could not be processed at this time. No money has been taken. "
        "Please try again, or use a different payment method."
    ),
    "payment_failed": "The payment did not go through. Please try again.",
    "gateway_error": (
        "The payment provider could not complete this payment. Please try again."
    ),
    "BAD_REQUEST_ERROR": (
        "Payment could not be processed at this time. Please try again, or use a "
        "different payment method."
    ),
}

GENERIC_FAILURE_COPY = (
    "The payment could not be completed. If money was debited it will be "
    "reconciled with your bank automatically — please do not pay again until "
    "this attempt is resolved."
)


def failure_copy(code: str | None) -> str:
    """Customer-safe sentence for a provider failure code."""
    if not code:
        return GENERIC_FAILURE_COPY
    return _FAILURE_COPY.get(code, GENERIC_FAILURE_COPY)
