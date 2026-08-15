"""Vocabulary shared by every notification provider.

The distinction this module exists to enforce is **acceptance is not delivery**.
A provider returning HTTP 200 means it took the request, nothing more. Treating
that as delivered is what makes fallback logic lie: it either suppresses a needed
SMS, or sends a duplicate one for a message that actually arrived.

So `DeliveryStatus` is a ladder, and `ErrorClass` decides what a caller may do:

    PERMANENT  -> fall back to the next channel, once
    TRANSIENT  -> retry the same channel with backoff
    UNKNOWN    -> reconcile; never assume failure, the message may have arrived
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Channel(StrEnum):
    WHATSAPP = "whatsapp"
    SMS = "sms"
    EMAIL = "email"


class Provider(StrEnum):
    WHATSAPP = "whatsapp"
    MSG91 = "msg91"
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
    #: Temporary — same channel, retry with backoff.
    TRANSIENT = "transient"
    #: Definitive — this channel will never work for this recipient/message.
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

    # --- marketing ---
    MARKETING_BROADCAST = "MARKETING_BROADCAST"


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

MARKETING_TYPES = frozenset({NotificationType.MARKETING_BROADCAST})


def category_for(notification_type: NotificationType | str) -> Category:
    value = NotificationType(notification_type)
    if value in OTP_TYPES:
        return Category.OTP
    if value in MARKETING_TYPES:
        return Category.MARKETING
    return Category.TRANSACTIONAL


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
    def should_fall_back(self) -> bool:
        """Only a definitive failure earns a fallback.

        A timeout must not: the message may well have been delivered, and
        sending the SMS anyway is the duplicate we are trying to avoid.
        """
        return self.status is DeliveryStatus.FAILED and self.error_class is ErrorClass.PERMANENT

    @property
    def should_retry(self) -> bool:
        return self.error_class is ErrorClass.TRANSIENT
