"""Vocabulary shared by every notification provider.

The distinction this module exists to enforce is **acceptance is not delivery**.
A provider returning HTTP 200 means it took the request, nothing more. Treating
that as delivered is what makes the retry ladder lie: it either abandons a
message that never arrived, or sends a second copy of one that did.

So `DeliveryStatus` is a ladder, and `ErrorClass` decides what a caller may do:

    PERMANENT  -> stop; this will never work for this recipient
    TRANSIENT  -> retry on WhatsApp with backoff
    UNKNOWN    -> reconcile; never assume failure, the message may have arrived
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Channel(StrEnum):
    """Delivery channels. Only WHATSAPP has a registered provider."""

    WHATSAPP = "whatsapp"
    SMS = "sms"
    EMAIL = "email"


class Provider(StrEnum):
    """Concrete vendors. Retired ones are absent — the column is read as text."""

    WHATSAPP = "whatsapp"
    RESEND = "resend"
    SMTP = "smtp"


class Category(StrEnum):
    """Drives consent rules and whether fallback is allowed at all."""

    TRANSACTIONAL = "transactional"
    OTP = "otp"
    MARKETING = "marketing"


class DeliveryStatus(StrEnum):
    """The delivery ladder. Each rung is a strictly stronger signal."""

    REQUESTED = "requested"
    #: Provider took the request. NOT delivered.
    ACCEPTED = "accepted"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"
    #: Accepted somewhere, but no signal ever arrived.
    UNKNOWN = "unknown"


#: Statuses that prove the message reached the handset.
CONFIRMED_DELIVERY = frozenset({DeliveryStatus.DELIVERED, DeliveryStatus.READ})

#: Statuses where a channel is definitively finished and failed.
TERMINAL_FAILURE = frozenset({DeliveryStatus.FAILED})


class ErrorClass(StrEnum):
    #: Temporary — retry with backoff.
    TRANSIENT = "transient"
    #: Definitive — this will never work for this recipient/message.
    PERMANENT = "permanent"
    #: We do not know. Reconcile; do not treat as failure.
    UNKNOWN = "unknown"


class NotificationType(StrEnum):
    """Every notification the application can emit."""

    # --- identity / OTP ---
    OTP_PHONE_VERIFY = "OTP_PHONE_VERIFY"
    OTP_LOGIN = "OTP_LOGIN"
    OTP_REGISTRATION = "OTP_REGISTRATION"
    OTP_PASSWORD_RESET = "OTP_PASSWORD_RESET"
    OTP_CHANGE_PHONE = "OTP_CHANGE_PHONE"

    # --- order lifecycle ---
    ORDER_CONFIRMED = "ORDER_CONFIRMED"
    ORDER_PROCESSING = "ORDER_PROCESSING"
    ORDER_PACKED = "ORDER_PACKED"
    PAYMENT_CONFIRMED = "PAYMENT_CONFIRMED"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    ORDER_SHIPPED = "ORDER_SHIPPED"
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"
    ORDER_DELIVERED = "ORDER_DELIVERED"
    ORDER_CANCELLED = "ORDER_CANCELLED"

    # --- money back ---
    REFUND_INITIATED = "REFUND_INITIATED"
    REFUND_COMPLETED = "REFUND_COMPLETED"

    # --- returns / exchanges ---
    RETURN_CREATED = "RETURN_CREATED"
    RETURN_APPROVED = "RETURN_APPROVED"
    RETURN_REJECTED = "RETURN_REJECTED"
    EXCHANGE_CREATED = "EXCHANGE_CREATED"
    EXCHANGE_COMPLETED = "EXCHANGE_COMPLETED"

    # --- abandoned cart ---
    # Three rungs rather than one type, because each is a separate Meta template
    # with its own approved copy, and the ladder must be able to tell which rung
    # a customer already received.
    CART_REMINDER_FIRST = "CART_REMINDER_FIRST"
    CART_REMINDER_SECOND = "CART_REMINDER_SECOND"
    CART_REMINDER_COUPON = "CART_REMINDER_COUPON"

    # --- marketing ---
    MARKETING_BROADCAST = "MARKETING_BROADCAST"
    WELCOME_OFFER = "WELCOME_OFFER"
    FIRST_PURCHASE_COUPON = "FIRST_PURCHASE_COUPON"
    FESTIVAL_SALE = "FESTIVAL_SALE"
    FLASH_SALE = "FLASH_SALE"
    LIMITED_OFFER = "LIMITED_OFFER"
    NEW_COLLECTION = "NEW_COLLECTION"
    COUPON_EXPIRING = "COUPON_EXPIRING"
    PRICE_DROP = "PRICE_DROP"
    RESTOCKED_ITEM = "RESTOCKED_ITEM"


#: Which notification types are OTP. These get the authentication template
#: category and are the only ones permitted to carry a code.
OTP_TYPES = frozenset(
    {
        NotificationType.OTP_PHONE_VERIFY,
        NotificationType.OTP_LOGIN,
        NotificationType.OTP_REGISTRATION,
        NotificationType.OTP_PASSWORD_RESET,
        NotificationType.OTP_CHANGE_PHONE,
    }
)

#: Cart reminders. Meta classifies these as MARKETING templates, and they are
#: consent-gated — but on their own preference flag, because a customer who wants
#: order updates and no promos may still want to be told they left something in
#: the basket. Kept out of MARKETING_TYPES so the two consents stay independent.
CART_TYPES = frozenset(
    {
        NotificationType.CART_REMINDER_FIRST,
        NotificationType.CART_REMINDER_SECOND,
        NotificationType.CART_REMINDER_COUPON,
    }
)

MARKETING_TYPES = frozenset(
    {
        NotificationType.MARKETING_BROADCAST,
        NotificationType.WELCOME_OFFER,
        NotificationType.FIRST_PURCHASE_COUPON,
        NotificationType.FESTIVAL_SALE,
        NotificationType.FLASH_SALE,
        NotificationType.LIMITED_OFFER,
        NotificationType.NEW_COLLECTION,
        NotificationType.COUPON_EXPIRING,
        NotificationType.PRICE_DROP,
        NotificationType.RESTOCKED_ITEM,
    }
)


def category_for(notification_type: NotificationType | str) -> Category:
    value = NotificationType(notification_type)
    if value in OTP_TYPES:
        return Category.OTP
    if value in MARKETING_TYPES or value in CART_TYPES:
        return Category.MARKETING
    return Category.TRANSACTIONAL


#: Which `public.user_preferences` column gates each category. Transactional and
#: OTP are service messages and are not gated at all — a customer who placed an
#: order asked for its updates.
def consent_column_for(notification_type: NotificationType | str) -> str | None:
    """Return the consent column that must be TRUE, or None if ungated."""
    value = NotificationType(notification_type)
    if value in CART_TYPES:
        return "whatsapp_abandoned_cart"
    if value in MARKETING_TYPES:
        return "whatsapp_marketing"
    return None


@dataclass(slots=True)
class TemplateBinding:
    """A resolved provider template for one notification type + channel."""

    provider_template_name: str | None
    provider_template_id: str | None
    language: str
    category: str
    #: Ordered variable names bound to {{1}}, {{2}}, … in the WhatsApp template.
    variable_order: list[str] = field(default_factory=list)
    #: Plain body with {placeholders}, for SMS/email.
    body_text: str | None = None


@dataclass(slots=True)
class OutboundMessage:
    """Everything a provider needs, with nothing provider-specific in it."""

    notification_type: NotificationType
    category: Category
    #: E.164 for phone channels, address for email.
    recipient: str
    variables: dict[str, Any]
    template: TemplateBinding | None = None
    #: Correlates provider logs with our notification row.
    reference: str | None = None


@dataclass(slots=True)
class ProviderResult:
    """What a provider reports back. Never assume more than this says."""

    status: DeliveryStatus
    provider: Provider
    channel: Channel
    provider_message_id: str | None = None
    error_class: ErrorClass | None = None
    failure_code: str | None = None
    failure_reason: str | None = None
    #: Trimmed provider response. Must never contain an OTP or a credential.
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def accepted(self) -> bool:
        return self.status in {
            DeliveryStatus.ACCEPTED,
            DeliveryStatus.SENT,
            DeliveryStatus.DELIVERED,
            DeliveryStatus.READ,
        }

    @property
    def should_retry(self) -> bool:
        return self.error_class is ErrorClass.TRANSIENT
