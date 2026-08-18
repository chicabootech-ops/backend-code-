"""Delivery classification, the retry ladder and channel policy."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.notifications.providers.whatsapp import WhatsAppProvider
from app.notifications.service import SendOutcome
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
    consent_column_for,
)


def result(status, error_class=None):
    return ProviderResult(
        status=status,
        provider=Provider.WHATSAPP,
        channel=Channel.WHATSAPP,
        error_class=error_class,
    )


def _whatsapp_settings(**overrides):
    base = {
        "whatsapp_default_language": "en",
        "whatsapp_phone_number_id": "1",
        "whatsapp_api_version": "v21.0",
    }
    return SimpleNamespace(**(base | overrides))


def test_timeout_is_never_retried():
    r = result(DeliveryStatus.UNKNOWN, ErrorClass.UNKNOWN)
    assert r.should_retry is False
    assert r.accepted is False


def test_transient_failure_is_retried():
    assert result(DeliveryStatus.FAILED, ErrorClass.TRANSIENT).should_retry is True


def test_permanent_failure_is_not_retried():
    assert result(DeliveryStatus.FAILED, ErrorClass.PERMANENT).should_retry is False


def test_failed_without_classification_is_not_retried():
    assert result(DeliveryStatus.FAILED, None).should_retry is False


@pytest.mark.parametrize(
    "status",
    [
        DeliveryStatus.ACCEPTED,
        DeliveryStatus.SENT,
        DeliveryStatus.DELIVERED,
        DeliveryStatus.READ,
    ],
)
def test_accepted_states_stop_the_ladder(status):
    r = result(status)
    assert r.accepted is True
    assert r.should_retry is False


def test_accepted_is_not_delivered():
    assert DeliveryStatus.ACCEPTED != DeliveryStatus.DELIVERED
    assert result(DeliveryStatus.ACCEPTED).accepted is True
    assert DeliveryStatus.ACCEPTED not in (DeliveryStatus.DELIVERED, DeliveryStatus.READ)


@pytest.mark.parametrize(
    ("code", "http", "expected"),
    [
        (131026, 400, ErrorClass.PERMANENT),
        (132001, 400, ErrorClass.PERMANENT),
        (132015, 400, ErrorClass.PERMANENT),
        (190, 401, ErrorClass.PERMANENT),
        (130429, 429, ErrorClass.TRANSIENT),
        (131048, 400, ErrorClass.TRANSIENT),
        (None, 503, ErrorClass.TRANSIENT),
        (None, 500, ErrorClass.TRANSIENT),
    ],
)
def test_whatsapp_classification(code, http, expected):
    assert WhatsAppProvider(_whatsapp_settings())._classify(code, http) is expected


def test_unrecognised_whatsapp_code_stays_retryable():
    assert WhatsAppProvider(_whatsapp_settings())._classify(999999, 502) is ErrorClass.TRANSIENT


def test_transient_code_wins_over_a_4xx_status():
    assert WhatsAppProvider(_whatsapp_settings())._classify(131048, 400) is ErrorClass.TRANSIENT


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
        NotificationType.OUT_FOR_DELIVERY,
        NotificationType.ORDER_DELIVERED,
        NotificationType.REFUND_INITIATED,
    ):
        assert category_for(t) is Category.TRANSACTIONAL


def test_marketing_is_its_own_category():
    assert category_for(NotificationType.MARKETING_BROADCAST) is Category.MARKETING


def test_service_messages_are_not_consent_gated():
    assert consent_column_for(NotificationType.OTP_LOGIN) is None
    assert consent_column_for(NotificationType.ORDER_SHIPPED) is None


def test_cart_and_marketing_consents_are_independent():
    cart = consent_column_for(NotificationType.CART_REMINDER_FIRST)
    promo = consent_column_for(NotificationType.FLASH_SALE)
    assert cart == "whatsapp_abandoned_cart"
    assert promo == "whatsapp_marketing"
    assert cart != promo


def test_cart_reminders_are_marketing_category_but_their_own_consent():
    assert category_for(NotificationType.CART_REMINDER_COUPON) is Category.MARKETING


def test_whatsapp_payload_orders_variables_by_template_binding():
    provider = WhatsAppProvider(_whatsapp_settings())
    message = OutboundMessage(
        notification_type=NotificationType.ORDER_CONFIRMED,
        category=Category.TRANSACTIONAL,
        recipient="+91 98765 43210",
        variables={"customer_name": "Asha", "order_number": "1042", "total": "₹1,499"},
        template=TemplateBinding(
            provider_template_name="chicaboo_order_confirmed",
            provider_template_id=None,
            language="en",
            category="utility",
            variable_order=["customer_name", "order_number", "total"],
        ),
    )
    payload = provider._build_payload(message)
    body = next(c for c in payload["template"]["components"] if c["type"] == "body")
    assert [p["text"] for p in body["parameters"]] == ["Asha", "1042", "₹1,499"]
    assert payload["to"] == "919876543210"


def test_variable_order_not_dict_order_decides_binding():
    provider = WhatsAppProvider(_whatsapp_settings())
    message = OutboundMessage(
        notification_type=NotificationType.ORDER_SHIPPED,
        category=Category.TRANSACTIONAL,
        recipient="+919876543210",
        variables={"courier": "BlueDart", "order_number": "1042", "tracking_number": "AWB77"},
        template=TemplateBinding(
            provider_template_name="chicaboo_order_shipped",
            provider_template_id=None,
            language="en",
            category="utility",
            variable_order=["order_number", "tracking_number", "courier"],
        ),
    )
    body = next(
        c for c in provider._build_payload(message)["template"]["components"]
        if c["type"] == "body"
    )
    assert [p["text"] for p in body["parameters"]] == ["1042", "AWB77", "BlueDart"]


def test_missing_variable_renders_blank_rather_than_raising():
    provider = WhatsAppProvider(_whatsapp_settings())
    message = OutboundMessage(
        notification_type=NotificationType.ORDER_SHIPPED,
        category=Category.TRANSACTIONAL,
        recipient="+919876543210",
        variables={"order_number": "1042"},
        template=TemplateBinding(
            provider_template_name="chicaboo_order_shipped",
            provider_template_id=None,
            language="en",
            category="utility",
            variable_order=["order_number", "tracking_number"],
        ),
    )
    body = next(
        c for c in provider._build_payload(message)["template"]["components"]
        if c["type"] == "body"
    )
    assert [p["text"] for p in body["parameters"]] == ["1042", ""]


def test_authentication_template_adds_the_copy_code_button():
    provider = WhatsAppProvider(_whatsapp_settings())
    message = OutboundMessage(
        notification_type=NotificationType.OTP_LOGIN,
        category=Category.OTP,
        recipient="+919876543210",
        variables={"otp": "483921"},
        template=TemplateBinding(
            provider_template_name="chicaboo_otp_login",
            provider_template_id=None,
            language="en",
            category="authentication",
            variable_order=["otp"],
        ),
    )
    payload = provider._build_payload(message)
    button = next(c for c in payload["template"]["components"] if c["type"] == "button")
    assert button["parameters"][0]["text"] == "483921"


def test_non_authentication_template_has_no_button_component():
    provider = WhatsAppProvider(_whatsapp_settings())
    message = OutboundMessage(
        notification_type=NotificationType.ORDER_DELIVERED,
        category=Category.TRANSACTIONAL,
        recipient="+919876543210",
        variables={"order_number": "1042"},
        template=TemplateBinding(
            provider_template_name="chicaboo_order_delivered",
            provider_template_id=None,
            language="en",
            category="utility",
            variable_order=["order_number"],
        ),
    )
    components = provider._build_payload(message)["template"]["components"]
    assert all(c["type"] != "button" for c in components)


def test_unconfigured_whatsapp_fails_permanently_rather_than_unknown():
    provider = WhatsAppProvider(_whatsapp_settings())
    r = provider._fail("whatsapp_not_configured", "nope", ErrorClass.PERMANENT)
    assert r.status is DeliveryStatus.FAILED
    assert r.should_retry is False


def test_missing_delivery_status_counts_as_failure():
    outcome = SendOutcome(notification_id=None, status=None)
    assert outcome.failed is True
