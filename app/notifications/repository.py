"""Persistence for notifications, attempts and template bindings.

The idempotency guarantee lives here, in the same shape as the payment work:
`INSERT … ON CONFLICT DO NOTHING RETURNING id`. A duplicate order event, a
webhook retry and a crashed worker re-running all race at the unique index, and
exactly one of them gets a row back. Nothing relies on an application-level
"have we already sent this?" check, which two workers can both pass.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.notifications.types import (
    Channel,
    DeliveryStatus,
    ErrorClass,
    Provider,
    ProviderResult,
    TemplateBinding,
)


class NotificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ #
    # Notifications
    # ------------------------------------------------------------------ #
    async def claim(
        self,
        *,
        notification_type: str,
        category: str,
        recipient: str,
        channel_preference: str,
        fallback_allowed: bool,
        idempotency_key: str | None,
        user_id: uuid.UUID | None = None,
        variables: dict[str, Any] | None = None,
        reference_type: str | None = None,
        reference_id: uuid.UUID | None = None,
        otp_challenge_id: uuid.UUID | None = None,
        campaign_id: uuid.UUID | None = None,
    ) -> uuid.UUID | None:
        """Create the notification, or return None if this key already exists.

        None means "somebody else owns this send" — the caller must do nothing,
        not retry and not send anyway.
        """
        result = await self._session.execute(
            text(
                """
                INSERT INTO ops.notifications
                    (user_id, notification_type, category, recipient, status,
                     channel_preference, fallback_allowed, idempotency_key,
                     variables, reference_type, reference_id, otp_challenge_id,
                     campaign_id, channel, template, provider)
                VALUES
                    (:user_id, :ntype, :category, :recipient, 'pending',
                     :channel_pref, :fallback, :idem,
                     CAST(:variables AS JSONB), :ref_type, :ref_id, :otp_id,
                     :campaign_id, NULL, NULL, NULL)
                ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL
                DO NOTHING
                RETURNING id
                """
            ),
            {
                "user_id": str(user_id) if user_id else None,
                "ntype": notification_type,
                "category": category,
                "recipient": recipient,
                "channel_pref": channel_preference,
                "fallback": fallback_allowed,
                "idem": idempotency_key,
                "variables": json.dumps(variables or {}, default=str),
                "ref_type": reference_type,
                "ref_id": str(reference_id) if reference_id else None,
                "otp_id": str(otp_challenge_id) if otp_challenge_id else None,
                "campaign_id": str(campaign_id) if campaign_id else None,
            },
        )
        return result.scalar_one_or_none()

    async def get(self, notification_id: uuid.UUID) -> dict | None:
        row = (
            await self._session.execute(
                text("SELECT * FROM ops.notifications WHERE id = :id"),
                {"id": str(notification_id)},
            )
        ).mappings().first()
        return dict(row) if row else None

    async def lock(self, notification_id: uuid.UUID) -> dict | None:
        """Row lock, so two workers cannot process one notification at once."""
        row = (
            await self._session.execute(
                text("SELECT * FROM ops.notifications WHERE id = :id FOR UPDATE"),
                {"id": str(notification_id)},
            )
        ).mappings().first()
        return dict(row) if row else None

    async def set_status(
        self,
        notification_id: uuid.UUID,
        *,
        status: DeliveryStatus | str,
        delivered: bool = False,
        failed: bool = False,
        completed: bool = False,
    ) -> None:
        await self._session.execute(
            text(
                """
                UPDATE ops.notifications
                SET status = :status,
                    delivered_at = CASE WHEN :delivered THEN COALESCE(delivered_at, now()) ELSE delivered_at END,
                    failed_at    = CASE WHEN :failed    THEN COALESCE(failed_at, now())    ELSE failed_at END,
                    completed_at = CASE WHEN :completed THEN COALESCE(completed_at, now()) ELSE completed_at END
                WHERE id = :id
                """
            ),
            {
                "id": str(notification_id),
                "status": str(status),
                "delivered": delivered,
                "failed": failed,
                "completed": completed,
            },
        )
        await self._session.flush()

    async def pending_batch(self, limit: int = 50) -> list[uuid.UUID]:
        rows = (
            await self._session.execute(
                text(
                    """
                    SELECT id FROM ops.notifications
                    WHERE status = 'pending'
                    ORDER BY created_at
                    LIMIT :lim
                    FOR UPDATE SKIP LOCKED
                    """
                ),
                {"lim": limit},
            )
        ).scalars().all()
        return [r if isinstance(r, uuid.UUID) else uuid.UUID(str(r)) for r in rows]

    # ------------------------------------------------------------------ #
    # Attempts
    # ------------------------------------------------------------------ #
    async def claim_attempt(
        self,
        notification_id: uuid.UUID,
        *,
        provider: Provider | str,
        channel: Channel | str,
        attempt_number: int = 1,
        template_name: str | None = None,
    ) -> uuid.UUID | None:
        """Reserve the right to send on this channel.

        None means an attempt already exists for this (notification, channel,
        attempt_number) — which is exactly what stops a re-running worker from
        sending the same WhatsApp message a second time.
        """
        result = await self._session.execute(
            text(
                """
                INSERT INTO ops.notification_attempts
                    (notification_id, attempt_number, provider, channel, status, template_name)
                VALUES (:nid, :num, :provider, :channel, 'requested', :template)
                ON CONFLICT (notification_id, channel, attempt_number) DO NOTHING
                RETURNING id
                """
            ),
            {
                "nid": str(notification_id),
                "num": attempt_number,
                "provider": str(provider),
                "channel": str(channel),
                "template": template_name,
            },
        )
        return result.scalar_one_or_none()

    async def record_result(self, attempt_id: uuid.UUID, result: ProviderResult) -> None:
        await self._session.execute(
            text(
                """
                UPDATE ops.notification_attempts
                SET status = :status,
                    provider_message_id = COALESCE(:msg_id, provider_message_id),
                    failure_code = :failure_code,
                    failure_reason = :failure_reason,
                    error_class = :error_class,
                    accepted_at = CASE WHEN :accepted THEN COALESCE(accepted_at, now()) ELSE accepted_at END,
                    failed_at   = CASE WHEN :failed   THEN COALESCE(failed_at, now())   ELSE failed_at END,
                    raw_response = CAST(:raw AS JSONB)
                WHERE id = :id
                """
            ),
            {
                "id": str(attempt_id),
                "status": str(result.status),
                "msg_id": result.provider_message_id,
                "failure_code": result.failure_code,
                "failure_reason": (result.failure_reason or "")[:400] or None,
                "error_class": str(result.error_class) if result.error_class else None,
                "accepted": result.accepted,
                "failed": result.status is DeliveryStatus.FAILED,
                "raw": json.dumps(result.raw or {}, default=str),
            },
        )
        await self._session.flush()

    async def attempts_for(self, notification_id: uuid.UUID) -> list[dict]:
        rows = (
            await self._session.execute(
                text(
                    """
                    SELECT * FROM ops.notification_attempts
                    WHERE notification_id = :nid
                    ORDER BY requested_at ASC
                    """
                ),
                {"nid": str(notification_id)},
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def find_attempt_by_message_id(
        self, *, provider: Provider | str, provider_message_id: str
    ) -> dict | None:
        row = (
            await self._session.execute(
                text(
                    """
                    SELECT * FROM ops.notification_attempts
                    WHERE provider = :provider AND provider_message_id = :msg_id
                    """
                ),
                {"provider": str(provider), "msg_id": provider_message_id},
            )
        ).mappings().first()
        return dict(row) if row else None

    async def apply_delivery_signal(
        self,
        attempt_id: uuid.UUID,
        *,
        status: DeliveryStatus,
        failure_code: str | None = None,
        failure_reason: str | None = None,
        error_class: ErrorClass | None = None,
        at: datetime | None = None,
    ) -> None:
        """Upgrade an attempt from a webhook.

        Ordering is not guaranteed, so a weaker signal must never overwrite a
        stronger one: a late `sent` cannot undo a `delivered`, and `read` is the
        top of the ladder.
        """
        rank = {
            DeliveryStatus.REQUESTED: 0,
            DeliveryStatus.ACCEPTED: 1,
            DeliveryStatus.SENT: 2,
            DeliveryStatus.UNKNOWN: 2,
            DeliveryStatus.FAILED: 3,
            DeliveryStatus.DELIVERED: 4,
            DeliveryStatus.READ: 5,
        }
        await self._session.execute(
            text(
                """
                UPDATE ops.notification_attempts
                SET status = CASE WHEN :new_rank > COALESCE(
                        CASE status
                            WHEN 'requested' THEN 0 WHEN 'accepted' THEN 1
                            WHEN 'sent' THEN 2 WHEN 'unknown' THEN 2
                            WHEN 'failed' THEN 3 WHEN 'delivered' THEN 4
                            WHEN 'read' THEN 5 ELSE 0 END, 0)
                        THEN :status ELSE status END,
                    delivered_at = CASE WHEN :is_delivered THEN COALESCE(delivered_at, :at) ELSE delivered_at END,
                    read_at      = CASE WHEN :is_read      THEN COALESCE(read_at, :at)      ELSE read_at END,
                    failed_at    = CASE WHEN :is_failed    THEN COALESCE(failed_at, :at)    ELSE failed_at END,
                    failure_code = COALESCE(:failure_code, failure_code),
                    failure_reason = COALESCE(:failure_reason, failure_reason),
                    error_class = COALESCE(:error_class, error_class)
                WHERE id = :id
                """
            ),
            {
                "id": str(attempt_id),
                "status": str(status),
                "new_rank": rank.get(status, 0),
                "is_delivered": status in (DeliveryStatus.DELIVERED, DeliveryStatus.READ),
                "is_read": status is DeliveryStatus.READ,
                "is_failed": status is DeliveryStatus.FAILED,
                "failure_code": failure_code,
                "failure_reason": (failure_reason or "")[:400] or None,
                "error_class": str(error_class) if error_class else None,
                "at": at,
            },
        )
        await self._session.flush()

    # ------------------------------------------------------------------ #
    # Templates
    # ------------------------------------------------------------------ #
    async def resolve_template(
        self,
        *,
        notification_type: str,
        channel: Channel | str,
        provider: Provider | str,
        language: str = "en",
    ) -> TemplateBinding | None:
        """Look up the provider template. Falls back to the default language."""
        row = (
            await self._session.execute(
                text(
                    """
                    SELECT provider_template_name, provider_template_id, language,
                           category, variable_order, body_text
                    FROM ops.notification_templates
                    WHERE notification_type = :ntype
                      AND channel = :channel
                      AND provider = :provider
                      AND is_active
                    ORDER BY (language = :lang) DESC, language
                    LIMIT 1
                    """
                ),
                {
                    "ntype": notification_type,
                    "channel": str(channel),
                    "provider": str(provider),
                    "lang": language,
                },
            )
        ).mappings().first()
        if row is None:
            return None
        order = row["variable_order"]
        if isinstance(order, str):
            order = json.loads(order or "[]")
        return TemplateBinding(
            provider_template_name=row["provider_template_name"],
            provider_template_id=row["provider_template_id"],
            language=row["language"],
            category=row["category"],
            variable_order=list(order or []),
            body_text=row["body_text"],
        )
