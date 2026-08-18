"""WhatsAppService — the façade business logic calls.

Every method here answers "this happened, tell the customer" and nothing more.
None of them know about Meta, templates, retries or consent: they translate a
domain event into a `NotificationType` plus its template variables and hand it to
`NotificationService`, which owns delivery.

Two conventions run through the whole file, and both exist to prevent a specific
bug rather than for tidiness:

*   **Every send carries an idempotency key derived from the thing that
    happened**, e.g. `order:{id}:ORDER_SHIPPED`. Order webhooks arrive more than
    once, admins double-click, and workers re-run after a crash. The key is
    unique in the database, so the second caller loses the insert and no second
    message goes out. A key built from a timestamp or a uuid4 would defeat this
    entirely — that is why none of these build one that way.

*   **Variables are passed by name, not position.** The positional binding to
    Meta's `{{1}}`, `{{2}}` lives in `variable_order` on the template row, so
    re-ordering an approved template is a data change. A caller that passes the
    wrong *names* renders blanks; a caller that passed the wrong *order* would
    render a customer's address where the tracking number should be.

Money is formatted once, here, via `_rupees`. Paise are integers everywhere in
this codebase and the customer must never see "149900".
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.notifications.otp_service import OtpChallenge, OtpError, OtpService
from app.notifications.service import NotificationService, SendOutcome
from app.notifications.types import NotificationType

logger = logging.getLogger(__name__)

#: OTP purposes, matching the spec's LOGIN / SIGNUP / PHONE_VERIFICATION /
#: PASSWORD_RESET. Each maps to its own notification type so each can have its
#: own Meta-approved template — Meta rejects one generic "here is a code".
OTP_PURPOSES: dict[str, NotificationType] = {
    "LOGIN": NotificationType.OTP_LOGIN,
    "SIGNUP": NotificationType.OTP_REGISTRATION,
    "PHONE_VERIFICATION": NotificationType.OTP_PHONE_VERIFY,
    "PASSWORD_RESET": NotificationType.OTP_PASSWORD_RESET,
    "CHANGE_PHONE": NotificationType.OTP_CHANGE_PHONE,
}


def _rupees(paise: int | float | None) -> str:
    """Render integer paise as a customer-facing rupee string.

    Indian digit grouping (1,49,900 not 149,900) is deliberate — this goes to
    Indian customers on WhatsApp, and Western grouping reads as a typo.
    """
    if paise is None:
        return "₹0"
    whole = int(paise) // 100
    fraction = int(paise) % 100

    digits = str(abs(whole))
    if len(digits) > 3:
        # Last three digits, then pairs, which is the Indian convention.
        head, tail = digits[:-3], digits[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        grouped = ",".join([*parts, tail])
    else:
        grouped = digits

    sign = "-" if whole < 0 else ""
    if fraction:
        return f"{sign}₹{grouped}.{fraction:02d}"
    return f"{sign}₹{grouped}"


class WhatsAppService:
    """Domain-level WhatsApp messaging.

    Constructed per request/session. `notifications` is injected so tests can
    drive this without a provider, and so this class never learns how providers
    are wired.
    """

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        notifications: NotificationService,
    ) -> None:
        self._session = session
        self._settings = settings
        self._notifications = notifications
        self._otp = OtpService(session, settings)

    # ================================================================== #
    # Authentication
    # ================================================================== #
    async def send_otp(
        self,
        *,
        phone_number: str,
        purpose: str = "LOGIN",
        user_id: uuid.UUID | None = None,
        ip_address: str | None = None,
    ) -> SendOutcome:
        """Issue a code and deliver it over WhatsApp.

        The code is generated and hashed here and handed to the notification
        layer as a template variable. It is held in memory only for the duration
        of this call — never logged, never returned, never stored in plaintext.

        Raises `OtpError` for cooldown/rate-limit refusals, and for a definitive
        send failure. A send failure supersedes the challenge first, so the
        customer is not told "try again" and then refused for the cooldown
        window on a code that never reached them.
        """
        notification_type = OTP_PURPOSES.get(purpose.upper())
        if notification_type is None:
            raise OtpError(
                f"Unknown OTP purpose: {purpose}",
                code="otp_bad_purpose",
                status_code=400,
            )

        challenge: OtpChallenge = await self._otp.issue(
            purpose=purpose.upper(),
            destination=phone_number,
            destination_type="phone",
            user_id=user_id,
            ip_address=ip_address,
        )

        outcome = await self._notifications.send(
            notification_type,
            recipient=phone_number,
            variables={"otp": challenge.code},
            # The challenge id, not a timestamp: a double-submitted form reuses
            # the same challenge and therefore sends exactly once.
            idempotency_key=f"otp:{challenge.id}",
            user_id=user_id,
            reference_type="otp",
            otp_challenge_id=challenge.id,
        )

        if outcome.failed:
            # Retire the challenge so the resend cooldown does not punish the
            # customer for our failure to deliver.
            await self._otp.supersede(challenge.id)
            raise OtpError(
                "We could not send your code right now. Please try again.",
                code="otp_send_failed",
                # 503 not 502 — this is served through Vercel behind Cloudflare,
                # and both read 502/504 as "origin broken", replacing the body
                # with their own error page. See the deploy topology notes.
                status_code=503,
            )

        return outcome

    async def verify_otp(
        self, *, phone_number: str, otp: str, purpose: str = "LOGIN"
    ) -> uuid.UUID:
        """Check a submitted code. Returns the consumed challenge id.

        Verification is entirely local — the code is matched against the Argon2
        hash. It keeps working when Meta does not, which is the whole reason we
        generate our own codes instead of using a provider's OTP product.
        """
        return await self._otp.verify(
            purpose=purpose.upper(), destination=phone_number, code=otp
        )

    # ================================================================== #
    # Generic sends
    # ================================================================== #
    async def send_template_message(
        self,
        notification_type: NotificationType | str,
        *,
        recipient: str,
        variables: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        user_id: uuid.UUID | None = None,
        reference_type: str | None = None,
        reference_id: uuid.UUID | None = None,
        campaign_id: uuid.UUID | None = None,
        deliver_now: bool = True,
    ) -> SendOutcome:
        """Send any mapped template. The general form of every method below."""
        return await self._notifications.send(
            notification_type,
            recipient=recipient,
            variables=variables or {},
            idempotency_key=idempotency_key,
            user_id=user_id,
            reference_type=reference_type,
            reference_id=reference_id,
            campaign_id=campaign_id,
            deliver_now=deliver_now,
        )

    async def send_text_message(self, *, recipient: str, body: str) -> SendOutcome:
        """Free-form text.

        Meta only permits non-template text inside the 24-hour customer service
        window — that is, in reply to a message the customer sent us. Outside it
        the send is rejected with error 131047 and this returns FAILED.

        Nothing in the order or marketing flow uses this; it exists for the admin
        inbox replying to a live conversation. Everything customer-initiated by
        us goes out as an approved template.
        """
        return await self._notifications.send(
            NotificationType.MARKETING_BROADCAST,
            recipient=recipient,
            variables={"message": body, "_freeform": True},
            # No idempotency key: an operator sending the same sentence twice on
            # purpose is a legitimate thing to do in a live conversation.
            idempotency_key=None,
        )

    # ================================================================== #
    # Order lifecycle
    # ================================================================== #
    async def send_order_confirmation(
        self,
        *,
        order_id: uuid.UUID,
        order_number: str,
        recipient: str,
        customer_name: str,
        total_paise: int,
        user_id: uuid.UUID | None = None,
    ) -> SendOutcome:
        return await self.send_template_message(
            NotificationType.ORDER_CONFIRMED,
            recipient=recipient,
            variables={
                "customer_name": customer_name,
                "order_number": order_number,
                "total": _rupees(total_paise),
            },
            idempotency_key=f"order:{order_id}:ORDER_CONFIRMED",
            user_id=user_id,
            reference_type="order",
            reference_id=order_id,
        )

    async def send_order_processing(
        self,
        *,
        order_id: uuid.UUID,
        order_number: str,
        recipient: str,
        customer_name: str,
        user_id: uuid.UUID | None = None,
    ) -> SendOutcome:
        return await self.send_template_message(
            NotificationType.ORDER_PROCESSING,
            recipient=recipient,
            variables={"customer_name": customer_name, "order_number": order_number},
            idempotency_key=f"order:{order_id}:ORDER_PROCESSING",
            user_id=user_id,
            reference_type="order",
            reference_id=order_id,
        )

    async def send_order_packed(
        self,
        *,
        order_id: uuid.UUID,
        order_number: str,
        recipient: str,
        customer_name: str,
        user_id: uuid.UUID | None = None,
    ) -> SendOutcome:
        return await self.send_template_message(
            NotificationType.ORDER_PACKED,
            recipient=recipient,
            variables={"customer_name": customer_name, "order_number": order_number},
            idempotency_key=f"order:{order_id}:ORDER_PACKED",
            user_id=user_id,
            reference_type="order",
            reference_id=order_id,
        )

    async def send_payment_confirmation(
        self,
        *,
        order_id: uuid.UUID,
        order_number: str,
        recipient: str,
        amount_paise: int,
        user_id: uuid.UUID | None = None,
    ) -> SendOutcome:
        return await self.send_template_message(
            NotificationType.PAYMENT_CONFIRMED,
            recipient=recipient,
            variables={
                "order_number": order_number,
                "total": _rupees(amount_paise),
            },
            idempotency_key=f"order:{order_id}:PAYMENT_CONFIRMED",
            user_id=user_id,
            reference_type="order",
            reference_id=order_id,
        )

    async def send_payment_failed(
        self,
        *,
        order_id: uuid.UUID,
        order_number: str,
        recipient: str,
        retry_url: str = "",
        user_id: uuid.UUID | None = None,
    ) -> SendOutcome:
        """Payment failed — the order is NOT cancelled.

        A failed payment leaves the order intact so a retry reuses the same order
        number (see the payment state machine). The message says "try again", not
        "your order is gone", and the two must not drift apart.
        """
        return await self.send_template_message(
            NotificationType.PAYMENT_FAILED,
            recipient=recipient,
            variables={
                "order_number": order_number,
                "retry_url": retry_url or f"{self._settings.effective_frontend_url}/orders",
            },
            idempotency_key=f"order:{order_id}:PAYMENT_FAILED",
            user_id=user_id,
            reference_type="order",
            reference_id=order_id,
        )

    async def send_order_shipped(
        self,
        *,
        order_id: uuid.UUID,
        order_number: str,
        recipient: str,
        customer_name: str,
        tracking_number: str = "",
        courier: str = "",
        user_id: uuid.UUID | None = None,
    ) -> SendOutcome:
        return await self.send_template_message(
            NotificationType.ORDER_SHIPPED,
            recipient=recipient,
            variables={
                "customer_name": customer_name,
                "order_number": order_number,
                "tracking_number": tracking_number,
                "courier": courier,
            },
            idempotency_key=f"order:{order_id}:ORDER_SHIPPED",
            user_id=user_id,
            reference_type="order",
            reference_id=order_id,
        )

    async def send_out_for_delivery(
        self,
        *,
        order_id: uuid.UUID,
        order_number: str,
        recipient: str,
        customer_name: str,
        user_id: uuid.UUID | None = None,
    ) -> SendOutcome:
        return await self.send_template_message(
            NotificationType.OUT_FOR_DELIVERY,
            recipient=recipient,
            variables={"customer_name": customer_name, "order_number": order_number},
            idempotency_key=f"order:{order_id}:OUT_FOR_DELIVERY",
            user_id=user_id,
            reference_type="order",
            reference_id=order_id,
        )

    async def send_order_delivered(
        self,
        *,
        order_id: uuid.UUID,
        order_number: str,
        recipient: str,
        customer_name: str,
        user_id: uuid.UUID | None = None,
    ) -> SendOutcome:
        return await self.send_template_message(
            NotificationType.ORDER_DELIVERED,
            recipient=recipient,
            variables={"customer_name": customer_name, "order_number": order_number},
            idempotency_key=f"order:{order_id}:ORDER_DELIVERED",
            user_id=user_id,
            reference_type="order",
            reference_id=order_id,
        )

    async def send_order_cancelled(
        self,
        *,
        order_id: uuid.UUID,
        order_number: str,
        recipient: str,
        customer_name: str,
        reason: str = "",
        user_id: uuid.UUID | None = None,
    ) -> SendOutcome:
        return await self.send_template_message(
            NotificationType.ORDER_CANCELLED,
            recipient=recipient,
            variables={
                "customer_name": customer_name,
                "order_number": order_number,
                "reason": reason,
            },
            idempotency_key=f"order:{order_id}:ORDER_CANCELLED",
            user_id=user_id,
            reference_type="order",
            reference_id=order_id,
        )

    # ================================================================== #
    # Returns and refunds
    # ================================================================== #
    async def send_return_initiated(
        self,
        *,
        return_id: uuid.UUID,
        order_number: str,
        recipient: str,
        customer_name: str,
        user_id: uuid.UUID | None = None,
    ) -> SendOutcome:
        return await self.send_template_message(
            NotificationType.RETURN_CREATED,
            recipient=recipient,
            variables={"customer_name": customer_name, "order_number": order_number},
            idempotency_key=f"return:{return_id}:RETURN_CREATED",
            user_id=user_id,
            reference_type="return",
            reference_id=return_id,
        )

    async def send_refund_processed(
        self,
        *,
        order_id: uuid.UUID,
        order_number: str,
        recipient: str,
        amount_paise: int,
        user_id: uuid.UUID | None = None,
    ) -> SendOutcome:
        """Refund settled.

        Keyed on the amount as well as the order, because a partial refund can
        legitimately happen twice on one order and each is a separate event the
        customer needs to hear about.
        """
        return await self.send_template_message(
            NotificationType.REFUND_COMPLETED,
            recipient=recipient,
            variables={
                "order_number": order_number,
                "amount": _rupees(amount_paise),
            },
            idempotency_key=f"order:{order_id}:REFUND_COMPLETED:{int(amount_paise)}",
            user_id=user_id,
            reference_type="order",
            reference_id=order_id,
        )

    async def send_refund_initiated(
        self,
        *,
        order_id: uuid.UUID,
        order_number: str,
        recipient: str,
        amount_paise: int,
        user_id: uuid.UUID | None = None,
    ) -> SendOutcome:
        return await self.send_template_message(
            NotificationType.REFUND_INITIATED,
            recipient=recipient,
            variables={
                "order_number": order_number,
                "amount": _rupees(amount_paise),
            },
            idempotency_key=f"order:{order_id}:REFUND_INITIATED:{int(amount_paise)}",
            user_id=user_id,
            reference_type="order",
            reference_id=order_id,
        )

    # ================================================================== #
    # Abandoned cart
    # ================================================================== #
    async def send_abandoned_cart_reminder(
        self,
        *,
        cart_id: uuid.UUID,
        stage: int,
        recipient: str,
        customer_name: str,
        item_name: str,
        user_id: uuid.UUID,
        coupon_code: str = "",
        discount: str = "",
    ) -> SendOutcome:
        """One rung of the cart ladder.

        `stage` picks the template: 1 and 2 are plain nudges, 3 carries a coupon.
        The idempotency key includes the stage, so a re-running worker cannot
        send the same rung twice, while the ladder can still progress.

        Consent is checked downstream against `whatsapp_abandoned_cart` — these
        are marketing-category templates and a customer who opted out of them
        gets nothing, silently and correctly.
        """
        by_stage = {
            1: NotificationType.CART_REMINDER_FIRST,
            2: NotificationType.CART_REMINDER_SECOND,
            3: NotificationType.CART_REMINDER_COUPON,
        }
        notification_type = by_stage.get(stage)
        if notification_type is None:
            raise ValueError(f"Cart reminder stage must be 1, 2 or 3 — got {stage}")

        cart_url = f"{self._settings.effective_frontend_url}/cart"
        variables: dict[str, Any] = {
            "customer_name": customer_name,
            "item_name": item_name,
            "cart_url": cart_url,
        }
        if stage == 3:
            variables |= {"coupon_code": coupon_code, "discount": discount}

        return await self.send_template_message(
            notification_type,
            recipient=recipient,
            variables=variables,
            idempotency_key=f"cart:{cart_id}:REMINDER:{stage}",
            user_id=user_id,
            reference_type="user",
            reference_id=user_id,
        )

    # ================================================================== #
    # Marketing
    # ================================================================== #
    async def send_marketing(
        self,
        notification_type: NotificationType | str,
        *,
        recipient: str,
        user_id: uuid.UUID,
        variables: dict[str, Any],
        idempotency_key: str,
        campaign_id: uuid.UUID | None = None,
        deliver_now: bool = True,
    ) -> SendOutcome:
        """Send one marketing message.

        Consent is enforced in `NotificationService.send`, not here, so there is
        no path that reaches a provider without passing it — including this one.
        """
        return await self.send_template_message(
            notification_type,
            recipient=recipient,
            variables=variables,
            idempotency_key=idempotency_key,
            user_id=user_id,
            reference_type="campaign" if campaign_id else "user",
            reference_id=campaign_id or user_id,
            campaign_id=campaign_id,
            deliver_now=deliver_now,
        )
