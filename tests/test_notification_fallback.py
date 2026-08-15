"""Fallback, classification and channel-policy rules.

The rule these exist to protect: a WhatsApp **timeout** must never produce an
SMS. A timeout means "we don't know", and the message may well have arrived —
falling back on it is exactly the duplicate the spec forbids.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.notifications.providers.msg91 import Msg91Provider, _msisdn, _stringify
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
# MSG91 classification
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("text", "http", "expected"),
    [
        ("INVALID NUMBER", 400, ErrorClass.PERMANENT),
        ("DND REGISTERED", 400, ErrorClass.PERMANENT),
        ("INVALID AUTHKEY", 401, ErrorClass.PERMANENT),
        ("INSUFFICIENT BALANCE", 400, ErrorClass.PERMANENT),
        ("RATE LIMIT EXCEEDED", 429, ErrorClass.TRANSIENT),
        ("", 503, ErrorClass.TRANSIENT),
        ("", 401, ErrorClass.PERMANENT),
        # A 200 whose body says "error" with no phrase we recognise must not be
        # called a definitive failure.
        ("SOMETHING WE HAVE NOT SEEN", 200, ErrorClass.UNKNOWN),
    ],
)
def test_msg91_classification(text, http, expected):
    provider = Msg91Provider(_msg91_settings())
    assert provider._classify(text, http) is expected


def test_msg91_transient_wins_over_permanent_substring():
    """"TRY AGAIN" contains no permanent fragment, but "TIMEOUT" must not lose.

    Ordering matters here: several permanent fragments are short words that also
    appear inside transient messages, so transient is checked first.
    """
    provider = Msg91Provider(_msg91_settings())
    assert provider._classify("REQUEST TIMEOUT, TRY AGAIN", 500) is ErrorClass.TRANSIENT


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
# MSG91 template resolution and recipient normalisation
# --------------------------------------------------------------------------- #
def _msg91_settings(template_id="DEFAULT_TPL", country="91"):
    return SimpleNamespace(
        msg91_enabled=True,
        msg91_auth_key="k",
        msg91_template_id=template_id,
        msg91_sender_id="CHICAB",
        msg91_base_url="https://control.msg91.com",
        msg91_flow_path="/api/v5/flow",
        sms_country_code=country,
    )


def _otp_message(template=None):
    return OutboundMessage(
        notification_type=NotificationType.OTP_PHONE_VERIFY,
        category=Category.OTP,
        recipient="+919876543210",
        variables={"otp": "483921"},
        template=template,
    )


def test_row_template_id_wins_over_the_configured_default():
    provider = Msg91Provider(_msg91_settings())
    message = _otp_message(
        TemplateBinding(
            provider_template_name=None,
            provider_template_id="ROW_TPL",
            language="en",
            category="authentication",
            body_text=None,
        )
    )
    assert provider._template_id(message) == "ROW_TPL"


def test_falls_back_to_configured_template_when_the_row_has_none():
    provider = Msg91Provider(_msg91_settings())
    message = _otp_message(
        TemplateBinding(
            provider_template_name=None,
            provider_template_id=None,
            language="en",
            category="authentication",
            body_text=None,
        )
    )
    assert provider._template_id(message) == "DEFAULT_TPL"


def test_no_template_anywhere_resolves_to_none_rather_than_empty_string():
    """An empty template id would be sent to MSG91 and rejected opaquely."""
    provider = Msg91Provider(_msg91_settings(template_id=""))
    assert provider._template_id(_otp_message(None)) is None


@pytest.mark.parametrize(
    ("recipient", "expected"),
    [
        ("+919876543210", "919876543210"),
        ("919876543210", "919876543210"),
        ("9876543210", "919876543210"),  # bare national number gets the prefix
        ("+91 98765 43210", "919876543210"),
        ("", None),
        ("12", None),  # too short to be a number
    ],
)
def test_msisdn_normalisation(recipient, expected):
    assert _msisdn(recipient, "91") == expected


def test_variables_are_stringified_because_msg91_rejects_non_strings():
    assert _stringify({"otp": 483921, "n": None, "name": "Ragini"}) == {
        "otp": "483921",
        "name": "Ragini",
    }


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
