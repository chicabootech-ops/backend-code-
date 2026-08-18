"""Turns order, payment and refund events into WhatsApp messages."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.notifications.service import NotificationService
from app.notifications.types import NotificationType
from app.notifications.whatsapp_service import WhatsAppService

logger = logging.getLogger(__name__)

STATUS_NOTIFICATIONS: dict[str, NotificationType] = {
    "confirmed": NotificationType.ORDER_CONFIRMED,
    "processing": NotificationType.ORDER_PROCESSING,
    "packed": NotificationType.ORDER_PACKED,
    "shipped": NotificationType.ORDER_SHIPPED,
    "out_for_delivery": NotificationType.OUT_FOR_DELIVERY,
    "delivered": NotificationType.ORDER_DELIVERED,
    "cancelled": NotificationType.ORDER_CANCELLED,
}

_RECIPIENT_SQL = text(
    """
    SELECT o.user_id,
           o.shipping_address,
           u.phone          AS account_phone,
           u.phone_verified AS account_phone_verified,
           u.first_name     AS account_first_name
    FROM commerce.orders o
    LEFT JOIN identity.users u ON u.id = o.user_id
    WHERE o.id = :oid
    """
)


@dataclass(slots=True)
class OrderRecipient:
    phone: str
    name: str
    user_id: uuid.UUID | None


def _e164(raw: str | None, country_code: str) -> str | None:
    if not raw:
        return None
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if digits.startswith("0"):
        digits = digits.lstrip("0")
    if len(digits) == 10:
        digits = f"{country_code}{digits}"
    if len(digits) < 11 or len(digits) > 15:
        return None
    return f"+{digits}"


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        import json

        try:
            parsed = json.loads(value or "{}")
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


class OrderNotifier:
    """Order lifecycle notifications. No method here raises."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        notifications: NotificationService,
    ) -> None:
        self._session = session
        self._settings = settings
        self._whatsapp = WhatsAppService(session, settings, notifications=notifications)

    async def recipient_for(self, order_id: uuid.UUID) -> OrderRecipient | None:
        """Resolve who to message about an order, or None if nobody is reachable."""
        row = (
            await self._session.execute(_RECIPIENT_SQL, {"oid": str(order_id)})
        ).mappings().first()
        if row is None:
            return None

        address = _as_dict(row["shipping_address"])
        country = self._settings.phone_country_code

        phone = _e164(address.get("phone"), country)
        if phone is None and row["account_phone_verified"]:
            phone = _e164(row["account_phone"], country)
        if phone is None:
            logger.info("order_notify_skipped_no_phone order=%s", order_id)
            return None

        name = address.get("full_name") or row["account_first_name"] or "there"
        return OrderRecipient(
            phone=phone,
            name=str(name).split(" ")[0],
            user_id=row["user_id"],
        )

    async def order_confirmed(
        self, *, order_id: uuid.UUID, order_number: Any, total_paise: int
    ) -> None:
        await self._safe(
            "ORDER_CONFIRMED",
            order_id,
            lambda who: self._whatsapp.send_order_confirmation(
                order_id=order_id,
                order_number=str(order_number),
                recipient=who.phone,
                customer_name=who.name,
                total_paise=total_paise,
                user_id=who.user_id,
            ),
        )

    async def payment_confirmed(
        self, *, order_id: uuid.UUID, order_number: Any, amount_paise: int
    ) -> None:
        await self._safe(
            "PAYMENT_CONFIRMED",
            order_id,
            lambda who: self._whatsapp.send_payment_confirmation(
                order_id=order_id,
                order_number=str(order_number),
                recipient=who.phone,
                amount_paise=amount_paise,
                user_id=who.user_id,
            ),
        )

    async def payment_failed(
        self, *, order_id: uuid.UUID, order_number: Any, retry_url: str = ""
    ) -> None:
        await self._safe(
            "PAYMENT_FAILED",
            order_id,
            lambda who: self._whatsapp.send_payment_failed(
                order_id=order_id,
                order_number=str(order_number),
                recipient=who.phone,
                retry_url=retry_url,
                user_id=who.user_id,
            ),
        )

    async def refund_initiated(
        self, *, order_id: uuid.UUID, order_number: Any, amount_paise: int
    ) -> None:
        await self._safe(
            "REFUND_INITIATED",
            order_id,
            lambda who: self._whatsapp.send_refund_initiated(
                order_id=order_id,
                order_number=str(order_number),
                recipient=who.phone,
                amount_paise=amount_paise,
                user_id=who.user_id,
            ),
        )

    async def refund_completed(
        self, *, order_id: uuid.UUID, order_number: Any, amount_paise: int
    ) -> None:
        await self._safe(
            "REFUND_COMPLETED",
            order_id,
            lambda who: self._whatsapp.send_refund_processed(
                order_id=order_id,
                order_number=str(order_number),
                recipient=who.phone,
                amount_paise=amount_paise,
                user_id=who.user_id,
            ),
        )

    async def return_created(
        self, *, order_id: uuid.UUID, return_id: uuid.UUID, order_number: Any
    ) -> None:
        await self._safe(
            "RETURN_CREATED",
            order_id,
            lambda who: self._whatsapp.send_return_initiated(
                return_id=return_id,
                order_number=str(order_number),
                recipient=who.phone,
                customer_name=who.name,
                user_id=who.user_id,
            ),
        )

    async def status_changed(
        self,
        *,
        order_id: uuid.UUID,
        order_number: Any,
        status: str,
        tracking_number: str = "",
        courier: str = "",
        reason: str = "",
    ) -> None:
        """Send the update for an order's new status, if that status has one."""
        notification_type = STATUS_NOTIFICATIONS.get(status)
        if notification_type is None:
            return

        await self._safe(
            str(notification_type),
            order_id,
            lambda who: self._send_for_status(
                notification_type,
                who,
                order_id=order_id,
                order_number=str(order_number),
                tracking_number=tracking_number,
                courier=courier,
                reason=reason,
            ),
        )

    def _send_for_status(
        self,
        notification_type: NotificationType,
        who: OrderRecipient,
        *,
        order_id: uuid.UUID,
        order_number: str,
        tracking_number: str,
        courier: str,
        reason: str,
    ):
        common = {
            "order_id": order_id,
            "order_number": order_number,
            "recipient": who.phone,
            "customer_name": who.name,
            "user_id": who.user_id,
        }
        match notification_type:
            case NotificationType.ORDER_CONFIRMED:
                return self._whatsapp.send_order_confirmation(**common, total_paise=0)
            case NotificationType.ORDER_PROCESSING:
                return self._whatsapp.send_order_processing(**common)
            case NotificationType.ORDER_PACKED:
                return self._whatsapp.send_order_packed(**common)
            case NotificationType.ORDER_SHIPPED:
                return self._whatsapp.send_order_shipped(
                    **common, tracking_number=tracking_number, courier=courier
                )
            case NotificationType.OUT_FOR_DELIVERY:
                return self._whatsapp.send_out_for_delivery(**common)
            case NotificationType.ORDER_DELIVERED:
                return self._whatsapp.send_order_delivered(**common)
            case _:
                return self._whatsapp.send_order_cancelled(**common, reason=reason)

    async def _safe(self, label: str, order_id: uuid.UUID, send) -> None:
        try:
            who = await self.recipient_for(order_id)
            if who is None:
                return
            await send(who)
        except Exception:  # noqa: BLE001
            logger.exception("order_notify_failed order=%s type=%s", order_id, label)
