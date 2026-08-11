"""Fallback, classification and channel-policy rules.

The rule these exist to protect: a WhatsApp **timeout** must never produce an
SMS. A timeout means "we don't know", and the message may well have arrived —
falling back on it is exactly the duplicate the spec forbids.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.notifications.providers.message_central import MessageCentralProvider
from app.notifications.providers.whatsapp import WhatsAppProvider
from app.notifications.types import (
    Category,
    Channel,
    DeliveryStatus,
    ErrorClass,
    NotificationType,
    OutboundMessage,
    Provider,
    ProviderResult,
    TemplateBinding,
    category_for,
)


def result(status, error_class=None):
    return ProviderResult(
        status=status,
        provider=Provider.WHATSAPP,
        channel=Channel.WHATSAPP,
        error_class=error_class,
    )


# --------------------------------------------------------------------------- #
# The central rule
# --------------------------------------------------------------------------- #
def test_timeout_never_triggers_fallback():
    """A WhatsApp timeout is ambiguous — it must not become an SMS."""
    r = result(DeliveryStatus.UNKNOWN, ErrorClass.UNKNOWN)
    assert r.should_fall_back is False
    assert r.should_retry is False
    assert r.accepted is False


def test_permanent_failure_triggers_fallback():
    assert result(DeliveryStatus.FAILED, ErrorClass.PERMANENT).should_fall_back is True


def test_transient_failure_retries_rather_than_falling_back():
    r = result(DeliveryStatus.FAILED, ErrorClass.TRANSIENT)
    assert r.should_fall_back is False
    assert r.should_retry is True


def test_failed_without_classification_does_not_fall_back():
    """An unclassified failure is not evidence enough to spend an SMS."""
    assert result(DeliveryStatus.FAILED, None).should_fall_back is False


@pytest.mark.parametrize(
    "status", [DeliveryStatus.ACCEPTED, DeliveryStatus.SENT, DeliveryStatus.DELIVERED, DeliveryStatus.READ]
)
def test_accepted_states_stop_the_ladder(status):
    r = result(status)
    assert r.accepted is True
    assert r.should_fall_back is False


def test_accepted_is_not_delivered():
    """HTTP 200 means queued, not received. Conflating them breaks fallback."""
    assert DeliveryStatus.ACCEPTED != DeliveryStatus.DELIVERED
    assert result(DeliveryStatus.ACCEPTED).accepted is True


# --------------------------------------------------------------------------- #
# WhatsApp error classification
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("code", "http", "expected"),
    [
        (131026, 400, ErrorClass.PERMANENT),   # not a WhatsApp user
        (132001, 400, ErrorClass.PERMANENT),   # template not approved
        (190, 401, ErrorClass.PERMANENT),      # bad token
        (130429, 429, ErrorClass.TRANSIENT),   # rate limited
        (None, 503, ErrorClass.TRANSIENT),     # Meta outage
        (None, 500, ErrorClass.TRANSIENT),
    ],
)
def test_whatsapp_classification(code, http, expected):
    provider = WhatsAppProvider(SimpleNamespace())
    assert provider._classify(code, http) is expected


def test_unrecognised_whatsapp_code_is_not_wrongly_permanent():
    """Unknown 5xx must stay retryable, not burn the fallback."""
    provider = WhatsAppProvider(SimpleNamespace())
    assert provider._classify(999999, 502) is ErrorClass.TRANSIENT


# --------------------------------------------------------------------------- #
# Message Central classification
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("text", "http", "expected"),
    [
        ("INVALID_MOBILE", 400, ErrorClass.PERMANENT),
        ("DND REGISTERED", 400, ErrorClass.PERMANENT),
        ("RATE_LIMIT EXCEEDED", 429, ErrorClass.TRANSIENT),
        ("", 503, ErrorClass.TRANSIENT),
        ("", 401, ErrorClass.PERMANENT),
    ],
)
def test_message_central_classification(text, http, expected):
    provider = MessageCentralProvider(SimpleNamespace(message_central_enabled=True))
    assert provider._classify(text, http) is expected


# --------------------------------------------------------------------------- #
# Category routing
# --------------------------------------------------------------------------- #
def test_otp_types_are_categorised_as_otp():
    for t in (
        NotificationType.OTP_LOGIN,
        NotificationType.OTP_PHONE_VERIFY,
        NotificationType.OTP_PASSWORD_RESET,
        NotificationType.OTP_CHANGE_PHONE,
        NotificationType.OTP_REGISTRATION,
    ):
        assert category_for(t) is Category.OTP


def test_order_types_are_transactional():
    for t in (
        NotificationType.ORDER_CONFIRMED,
        NotificationType.PAYMENT_CONFIRMED,
        NotificationType.ORDER_SHIPPED,
        NotificationType.REFUND_INITIATED,
    ):
        assert category_for(t) is Category.TRANSACTIONAL


def test_marketing_is_its_own_category():
    assert category_for(NotificationType.MARKETING_BROADCAST) is Category.MARKETING


# --------------------------------------------------------------------------- #
# SMS body rendering — the same OTP must reach the SMS template
# --------------------------------------------------------------------------- #
def test_sms_body_renders_the_supplied_otp():
    provider = MessageCentralProvider(SimpleNamespace(message_central_enabled=True))
    message = OutboundMessage(
        notification_type=NotificationType.OTP_PHONE_VERIFY,
        category=Category.OTP,
        recipient="+919876543210",
        variables={"otp": "483921"},
        template=TemplateBinding(
            provider_template_name=None,
            provider_template_id=None,
            language="en",
            category="authentication",
            body_text="Your Chic A Boo verification code is {otp}.",
        ),
    )
    assert provider._render(message) == "Your Chic A Boo verification code is 483921."


def test_sms_render_refuses_rather_than_sending_a_broken_placeholder():
    provider = MessageCentralProvider(SimpleNamespace(message_central_enabled=True))
    message = OutboundMessage(
        notification_type=NotificationType.ORDER_CONFIRMED,
        category=Category.TRANSACTIONAL,
        recipient="+919876543210",
        variables={},  # missing order_number
        template=TemplateBinding(
            provider_template_name=None, provider_template_id=None, language="en",
            category="utility", body_text="Order #{order_number} confirmed.",
        ),
    )
    assert provider._render(message) is None


def test_whatsapp_payload_orders_variables_by_template_binding():
    provider = WhatsAppProvider(
        SimpleNamespace(whatsapp_default_language="en", whatsapp_phone_number_id="1", whatsapp_api_version="v21.0")
    )
    message = OutboundMessage(
        notification_type=NotificationType.ORDER_CONFIRMED,
        category=Category.TRANSACTIONAL,
        recipient="+91 98765 43210",
        variables={"customer_name": "Asha", "order_number": "1042", "total": "Rs. 1,499"},
        template=TemplateBinding(
            provider_template_name="chicaboo_order_confirmed",
            provider_template_id=None, language="en", category="utility",
            variable_order=["customer_name", "order_number", "total"],
        ),
    )
    payload = provider._build_payload(message)
    body = next(c for c in payload["template"]["components"] if c["type"] == "body")
    assert [p["text"] for p in body["parameters"]] == ["Asha", "1042", "Rs. 1,499"]
    # Meta wants bare digits.
    assert payload["to"] == "919876543210"


def test_authentication_template_adds_the_copy_code_button():
    provider = WhatsAppProvider(
        SimpleNamespace(whatsapp_default_language="en", whatsapp_phone_number_id="1", whatsapp_api_version="v21.0")
    )
    message = OutboundMessage(
        notification_type=NotificationType.OTP_LOGIN,
        category=Category.OTP,
        recipient="+919876543210",
        variables={"otp": "483921"},
        template=TemplateBinding(
            provider_template_name="chicaboo_otp_login", provider_template_id=None,
            language="en", category="authentication", variable_order=["otp"],
        ),
    )
    payload = provider._build_payload(message)
    button = next(c for c in payload["template"]["components"] if c["type"] == "button")
    assert button["parameters"][0]["text"] == "483921"
