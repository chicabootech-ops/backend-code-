from __future__ import annotations

import pytest

from app.admin_api.schemas.order import ORDER_STATUSES
from app.notifications.order_notifier import STATUS_NOTIFICATIONS, _e164
from app.notifications.types import Category, NotificationType, category_for


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ("9876543210", "+919876543210"),
        ("+919876543210", "+919876543210"),
        ("919876543210", "+919876543210"),
        ("+91 98765 43210", "+919876543210"),
        ("098765 43210", "+919876543210"),
        ("(98765) 43210", "+919876543210"),
        ("+14155552671", "+14155552671"),
    ],
)
def test_order_phone_normalises_to_e164(stored, expected):
    assert _e164(stored, "91") == expected


@pytest.mark.parametrize("stored", [None, "", "   ", "12345", "abcd", "0", "9" * 20])
def test_unusable_order_phone_yields_none(stored):
    assert _e164(stored, "91") is None


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("confirmed", NotificationType.ORDER_CONFIRMED),
        ("processing", NotificationType.ORDER_PROCESSING),
        ("packed", NotificationType.ORDER_PACKED),
        ("shipped", NotificationType.ORDER_SHIPPED),
        ("out_for_delivery", NotificationType.OUT_FOR_DELIVERY),
        ("delivered", NotificationType.ORDER_DELIVERED),
        ("cancelled", NotificationType.ORDER_CANCELLED),
    ],
)
def test_customer_facing_statuses_map_to_a_notification(status, expected):
    assert STATUS_NOTIFICATIONS[status] is expected


@pytest.mark.parametrize("status", ["pending", "completed", "returned", "refunded"])
def test_internal_statuses_send_nothing(status):
    assert status not in STATUS_NOTIFICATIONS


def test_every_mapped_status_is_a_real_order_status():
    assert set(STATUS_NOTIFICATIONS) <= set(ORDER_STATUSES)


def test_order_updates_are_never_consent_gated():
    for notification_type in STATUS_NOTIFICATIONS.values():
        assert category_for(notification_type) is Category.TRANSACTIONAL
