"""Meta WhatsApp webhook processing.

This is where `accepted` becomes `delivered`. Until a status event arrives all we
know is that Meta queued the message, so every fallback and reconciliation
decision downstream depends on this handler running correctly.

Three properties it has to hold:

*   **Authenticity.** Meta signs the raw body with the app secret
    (`X-Hub-Signature-256`). An unsigned or mis-signed delivery is recorded and
    rejected — never processed.
*   **Idempotency.** Meta retries. The `(message id, event type, status)` triple
    is unique in the database, so a redelivery loses the insert and does nothing.
*   **Monotonicity.** Events arrive out of order. `apply_delivery_signal` only
    ever upgrades a status, so a late `sent` cannot erase a `delivered`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.notifications.repository import NotificationRepository
from app.notifications.types import DeliveryStatus, ErrorClass, Provider

logger = logging.getLogger(__name__)

#: Meta status string -> our delivery ladder.
_STATUS_MAP = {
    "sent": DeliveryStatus.SENT,
    "delivered": DeliveryStatus.DELIVERED,
    "read": DeliveryStatus.READ,
    "failed": DeliveryStatus.FAILED,
}

#: Failure codes that mean this recipient will never receive on WhatsApp.
_PERMANENT_CODES = {131026, 131047, 131050, 133010, 132001, 132015, 132016}


class WhatsAppWebhookService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._repo = NotificationRepository(session)

    # ------------------------------------------------------------------ #
    # Verification
    # ------------------------------------------------------------------ #
    def verify_subscription(self, *, mode: str, token: str, challenge: str) -> str:
        """Meta's GET handshake when the webhook is first registered."""
        expected = self._settings.whatsapp_verify_token
        if mode == "subscribe" and expected and hmac.compare_digest(token, expected):
            logger.info("whatsapp_webhook_verified")
            return challenge
        logger.warning("whatsapp_webhook_verify_rejected mode=%s", mode)
        raise PermissionError("Webhook verification failed")

    def verify_signature(self, *, raw_body: bytes, signature_header: str) -> bool:
        """Validate `X-Hub-Signature-256: sha256=<hex>`."""
        secret = self._settings.whatsapp_signing_secret
        if not secret:
            logger.warning("whatsapp_webhook_secret_missing — rejecting")
            return False
        provided = (signature_header or "").removeprefix("sha256=").strip()
        if not provided:
            return False
        expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, provided)

    # ------------------------------------------------------------------ #
    # Processing
    # ------------------------------------------------------------------ #
    async def handle(
        self, *, raw_body: bytes, signature_header: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        signature_ok = self.verify_signature(
            raw_body=raw_body, signature_header=signature_header
        )

        statuses = list(_iter_statuses(payload))
        inbound = list(_iter_inbound_messages(payload))

        if not signature_ok:
            # Recorded anyway: repeated bad signatures against this endpoint are
            # a security signal, not noise to discard.
            await self._record(
                provider_message_id=None,
                event_type="signature_invalid",
                status_value=None,
                signature_valid=False,
                payload=payload,
            )
            await self._session.commit()
            logger.warning("whatsapp_webhook_signature_invalid")
            raise PermissionError("Invalid webhook signature")

        processed = 0
        duplicates = 0

        for status in statuses:
            message_id = status.get("id")
            status_value = str(status.get("status") or "").lower()
            claimed = await self._record(
                provider_message_id=message_id,
                event_type="status",
                status_value=status_value,
                signature_valid=True,
                payload=status,
            )
            if claimed is None:
                duplicates += 1
                logger.info("whatsapp_webhook_duplicate id=%s", message_id)
                continue
            await self._apply_status(claimed, message_id, status_value, status)
            processed += 1

        for message in inbound:
            # Customer-initiated messages: recorded for the admin inbox, never
            # allowed to change a notification's state.
            await self._record(
                provider_message_id=message.get("id"),
                event_type="message",
                status_value=message.get("type"),
                signature_valid=True,
                payload=message,
            )

        await self._session.commit()
        return {"processed": processed, "duplicates": duplicates, "inbound": len(inbound)}

    async def _apply_status(
        self,
        event_id: uuid.UUID,
        message_id: str | None,
        status_value: str,
        status: dict[str, Any],
    ) -> None:
        mapped = _STATUS_MAP.get(status_value)
        if mapped is None or not message_id:
            await self._finish_event(event_id, "ignored")
            return

        attempt = await self._repo.find_attempt_by_message_id(
            provider=Provider.WHATSAPP, provider_message_id=message_id
        )
        if attempt is None:
            # A status for a message we have no record of — possible if the
            # sending transaction rolled back after Meta accepted it.
            logger.warning("whatsapp_webhook_unknown_message id=%s", message_id)
            await self._finish_event(event_id, "ignored")
            return

        failure_code = None
        failure_reason = None
        error_class = None
        if mapped is DeliveryStatus.FAILED:
            errors = status.get("errors") or []
            first = errors[0] if errors else {}
            failure_code = str(first.get("code") or "")
            failure_reason = str(first.get("title") or first.get("message") or "")[:400]
            try:
                numeric = int(first.get("code"))
            except (TypeError, ValueError):
                numeric = None
            error_class = (
                ErrorClass.PERMANENT if numeric in _PERMANENT_CODES else ErrorClass.TRANSIENT
            )

        await self._repo.apply_delivery_signal(
            attempt["id"],
            status=mapped,
            failure_code=failure_code or None,
            failure_reason=failure_reason or None,
            error_class=error_class,
            at=_timestamp(status),
        )

        # Roll the signal up to the notification.
        if mapped in (DeliveryStatus.DELIVERED, DeliveryStatus.READ):
            await self._repo.set_status(
                attempt["notification_id"],
                # 'read' is a strictly stronger signal than 'delivered' and the
                # analytics read-rate depends on the difference, so it is
                # preserved here rather than flattened to delivered.
                status=(
                    DeliveryStatus.READ
                    if mapped is DeliveryStatus.READ
                    else DeliveryStatus.DELIVERED
                ),
                delivered=True,
                completed=True,
            )
            logger.info(
                "whatsapp_%s notification=%s", mapped, attempt["notification_id"]
            )
        elif mapped is DeliveryStatus.SENT:
            logger.info("whatsapp_sent notification=%s", attempt["notification_id"])
        elif mapped is DeliveryStatus.FAILED:
            logger.warning(
                "whatsapp_failed notification=%s code=%s class=%s",
                attempt["notification_id"],
                failure_code,
                error_class,
            )
            # WhatsApp is the only channel, so there is no fallback leg to hand
            # this to. The two failure classes get different treatment:
            #
            #   PERMANENT — this recipient will never receive on WhatsApp
            #               (not on WhatsApp, template disabled). Terminal.
            #   TRANSIENT — Meta rate-limited or blipped. Hand it back to the
            #               retry ladder, which re-sends under a NEW attempt
            #               number; the existing attempt row is untouched, so
            #               this cannot double-send under the same number.
            #
            # Before, a permanent failure was returned to 'pending' so the SMS
            # leg could run. With no SMS leg that would spin: the worker would
            # pick it up, find the WhatsApp attempt already exists, and put it
            # straight back.
            if error_class is ErrorClass.TRANSIENT:
                await self._schedule_retry_if_budget(attempt["notification_id"])
            else:
                await self._repo.set_status(
                    attempt["notification_id"],
                    status="failed",
                    failed=True,
                    completed=True,
                    last_error=failure_reason or failure_code,
                )

        # Campaign membership mirrors the delivery signal, so campaign analytics
        # do not have to join through notifications on every read.
        await self._sync_campaign_recipient(attempt["notification_id"], mapped)

        await self._finish_event(event_id, "processed", attempt_id=attempt["id"])

    async def _schedule_retry_if_budget(self, notification_id: uuid.UUID) -> None:
        """Re-queue a transiently-failed notification, if attempts remain."""
        row = (
            await self._session.execute(
                text(
                    "SELECT attempt_count FROM ops.notifications WHERE id = :id"
                ),
                {"id": str(notification_id)},
            )
        ).mappings().first()
        attempts = int((row or {}).get("attempt_count") or 0)
        max_attempts = self._settings.notification_max_attempts

        if attempts >= max_attempts:
            await self._repo.set_status(
                notification_id,
                status="failed",
                failed=True,
                completed=True,
                last_error="retry budget exhausted",
            )
            logger.warning("notification_retry_exhausted id=%s", notification_id)
            return

        delays = self._settings.notification_retry_delays_seconds or [300]
        delay = int(delays[min(max(attempts - 1, 0), len(delays) - 1)])
        await self._repo.schedule_retry(notification_id, delay_seconds=delay)
        logger.info(
            "notification_retry_from_webhook id=%s in=%ss", notification_id, delay
        )

    async def _sync_campaign_recipient(
        self, notification_id: uuid.UUID, status: DeliveryStatus
    ) -> None:
        """Mirror a delivery signal onto the campaign recipient row, if any.

        Guarded by the same monotonic rule as attempts: a late 'sent' must not
        pull a row back from 'read'. Non-campaign notifications match no row and
        the UPDATE is a no-op.
        """
        rank = {
            DeliveryStatus.SENT: 2,
            DeliveryStatus.FAILED: 3,
            DeliveryStatus.DELIVERED: 4,
            DeliveryStatus.READ: 5,
        }
        new_rank = rank.get(status)
        if new_rank is None:
            return

        await self._session.execute(
            text(
                """
                UPDATE ops.campaign_recipients
                SET status = CASE WHEN :new_rank > COALESCE(
                        CASE status
                            WHEN 'pending' THEN 0 WHEN 'queued' THEN 1
                            WHEN 'sent' THEN 2 WHEN 'failed' THEN 3
                            WHEN 'delivered' THEN 4 WHEN 'read' THEN 5
                            ELSE 0 END, 0)
                    THEN :status ELSE status END,
                    sent_at      = CASE WHEN :is_sent      THEN COALESCE(sent_at, now())      ELSE sent_at END,
                    delivered_at = CASE WHEN :is_delivered THEN COALESCE(delivered_at, now()) ELSE delivered_at END,
                    read_at      = CASE WHEN :is_read      THEN COALESCE(read_at, now())      ELSE read_at END,
                    failed_at    = CASE WHEN :is_failed    THEN COALESCE(failed_at, now())    ELSE failed_at END
                WHERE notification_id = :nid
                """
            ),
            {
                "nid": str(notification_id),
                "status": str(status),
                "new_rank": new_rank,
                "is_sent": status is DeliveryStatus.SENT,
                "is_delivered": status in (DeliveryStatus.DELIVERED, DeliveryStatus.READ),
                "is_read": status is DeliveryStatus.READ,
                "is_failed": status is DeliveryStatus.FAILED,
            },
        )

    async def _record(
        self,
        *,
        provider_message_id: str | None,
        event_type: str,
        status_value: str | None,
        signature_valid: bool,
        payload: dict[str, Any],
    ) -> uuid.UUID | None:
        result = await self._session.execute(
            text(
                """
                INSERT INTO ops.whatsapp_webhook_events
                    (provider_message_id, event_type, status_value, signature_valid,
                     payload, processing_status)
                VALUES (:msg_id, :event_type, :status_value, :sig_valid,
                        CAST(:payload AS JSONB), 'received')
                ON CONFLICT (provider_message_id, event_type, status_value)
                    WHERE provider_message_id IS NOT NULL
                DO NOTHING
                RETURNING id
                """
            ),
            {
                "msg_id": provider_message_id,
                "event_type": event_type,
                "status_value": status_value,
                "sig_valid": signature_valid,
                "payload": json.dumps(payload, default=str),
            },
        )
        return result.scalar_one_or_none()

    async def _finish_event(
        self, event_id: uuid.UUID, status: str, *, attempt_id: uuid.UUID | None = None
    ) -> None:
        await self._session.execute(
            text(
                """
                UPDATE ops.whatsapp_webhook_events
                SET processing_status = :status,
                    attempt_id = COALESCE(:attempt_id, attempt_id),
                    processed_at = now()
                WHERE id = :id
                """
            ),
            {
                "id": str(event_id),
                "status": status,
                "attempt_id": str(attempt_id) if attempt_id else None,
            },
        )


def _iter_statuses(payload: dict[str, Any]):
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            for status in value.get("statuses") or []:
                if isinstance(status, dict):
                    yield status


def _iter_inbound_messages(payload: dict[str, Any]):
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            for message in value.get("messages") or []:
                if isinstance(message, dict):
                    yield message


def _timestamp(status: dict[str, Any]) -> datetime | None:
    raw = status.get("timestamp")
    if raw is None:
        return None
    try:
        return datetime.fromtimestamp(int(raw), tz=UTC)
    except (TypeError, ValueError, OSError):
        return None
