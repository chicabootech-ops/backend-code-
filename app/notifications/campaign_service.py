"""WhatsApp campaigns — create, schedule, run, pause, measure.

A campaign runs in two distinct phases, and keeping them separate is what makes
the whole thing restartable:

    1. RESOLVE — the segment is materialised into ops.campaign_recipients, one
       row per person, inserted ON CONFLICT DO NOTHING against a unique index.
    2. SEND    — the worker claims pending recipient rows in batches and sends.

Because phase 1 is idempotent, re-resolving a campaign that was paused mid-flight
adds only genuinely new members. Because phase 2 claims rows by flipping their
status inside a transaction, two workers cannot both take the same recipient.
A campaign can therefore be paused, resumed, and re-run after a crash without
anybody receiving it twice.

Pacing is deliberate. Meta throttles per phone number id and scores the number's
quality rating on how recipients react. That rating is shared with OTP delivery —
so a badly-behaved marketing blast degrades customers' ability to log in. The
batch pause is not politeness, it protects the auth channel.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.notifications.segmentation import Segment, SegmentationService
from app.notifications.service import NotificationService
from app.notifications.types import NotificationType
from app.notifications.whatsapp_service import WhatsAppService

logger = logging.getLogger(__name__)

#: Campaign types the spec defines, mapped to the notification type each sends
#: by default. A campaign may override this with its own `notification_type`.
CAMPAIGN_TYPE_DEFAULTS: dict[str, NotificationType] = {
    "MARKETING": NotificationType.MARKETING_BROADCAST,
    "PROMOTIONAL": NotificationType.LIMITED_OFFER,
    "BROADCAST": NotificationType.MARKETING_BROADCAST,
    "ABANDONED_CART": NotificationType.CART_REMINDER_FIRST,
    "FLASH_SALE": NotificationType.FLASH_SALE,
}

#: Statuses a campaign may be started from.
_STARTABLE = frozenset({"draft", "scheduled", "paused"})


class CampaignError(Exception):
    def __init__(self, message: str, *, code: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


@dataclass(slots=True)
class CampaignProgress:
    campaign_id: uuid.UUID
    resolved: int
    sent: int
    failed: int
    skipped: int
    remaining: int
    status: str


class CampaignService:
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
        self._segments = SegmentationService(session)
        self._whatsapp = WhatsAppService(session, settings, notifications=notifications)

    # ------------------------------------------------------------------ #
    # Create / schedule
    # ------------------------------------------------------------------ #
    async def create(
        self,
        *,
        name: str,
        campaign_type: str = "MARKETING",
        notification_type: NotificationType | str | None = None,
        audience_filter: dict[str, Any] | None = None,
        variables: dict[str, Any] | None = None,
        scheduled_at: datetime | None = None,
        created_by: uuid.UUID | None = None,
    ) -> uuid.UUID:
        """Draft a campaign.

        The segment is validated now, at authoring time, so a malformed audience
        surfaces to the admin who wrote it rather than to a worker at 3am.
        """
        campaign_type = campaign_type.upper()
        if campaign_type not in CAMPAIGN_TYPE_DEFAULTS:
            raise CampaignError(
                f"Unknown campaign type: {campaign_type}", code="campaign_bad_type"
            )

        try:
            Segment.parse(audience_filter)
        except ValueError as exc:
            raise CampaignError(str(exc), code="campaign_bad_segment") from exc

        resolved_type = str(
            notification_type or CAMPAIGN_TYPE_DEFAULTS[campaign_type]
        )

        campaign_id = (
            await self._session.execute(
                text(
                    """
                    INSERT INTO ops.notification_campaigns
                        (name, campaign_type, notification_type, channel, status,
                         audience_filter, variables, scheduled_at, created_by,
                         sms_fallback_enabled)
                    VALUES
                        (:name, :ctype, :ntype, 'whatsapp',
                         CASE WHEN CAST(:scheduled AS TIMESTAMPTZ) IS NULL
                              THEN 'draft' ELSE 'scheduled' END,
                         CAST(:audience AS JSONB), CAST(:variables AS JSONB),
                         CAST(:scheduled AS TIMESTAMPTZ), :created_by,
                         -- No SMS exists to fall back to.
                         FALSE)
                    RETURNING id
                    """
                ),
                {
                    "name": name.strip(),
                    "ctype": campaign_type,
                    "ntype": resolved_type,
                    "audience": _json(audience_filter or {}),
                    "variables": _json(variables or {}),
                    "scheduled": scheduled_at,
                    "created_by": str(created_by) if created_by else None,
                },
            )
        ).scalar_one()
        await self._session.commit()

        logger.info(
            "campaign_created id=%s type=%s scheduled=%s",
            campaign_id,
            campaign_type,
            scheduled_at,
        )
        return campaign_id

    async def schedule(self, campaign_id: uuid.UUID, *, when: datetime) -> None:
        """Move a draft to 'scheduled'. The worker picks it up when due."""
        campaign = await self._require(campaign_id)
        if campaign["status"] not in ("draft", "scheduled", "paused"):
            raise CampaignError(
                f"A {campaign['status']} campaign cannot be scheduled",
                code="campaign_bad_state",
            )
        await self._session.execute(
            text(
                """
                UPDATE ops.notification_campaigns
                SET status = 'scheduled', scheduled_at = :when
                WHERE id = :id
                """
            ),
            {"id": str(campaign_id), "when": when},
        )
        await self._session.commit()
        logger.info("campaign_scheduled id=%s at=%s", campaign_id, when)

    # ------------------------------------------------------------------ #
    # Audience
    # ------------------------------------------------------------------ #
    async def count_segment(
        self,
        raw_filter: dict[str, Any] | None,
        *,
        consent_column: str = "whatsapp_marketing",
    ) -> int:
        """Size an ad-hoc segment that is not attached to a campaign yet.

        Used by the admin UI's segment builder so an audience can be sized while
        it is still being edited.
        """
        try:
            segment = Segment.parse(raw_filter)
        except ValueError as exc:
            raise CampaignError(str(exc), code="campaign_bad_segment") from exc
        return await self._segments.count(segment, consent_column=consent_column)

    async def preview_audience(self, campaign_id: uuid.UUID) -> int:
        """How many people this campaign would reach right now."""
        campaign = await self._require(campaign_id)
        segment = Segment.parse(campaign["audience_filter"])
        return await self._segments.count(
            segment, consent_column=self._consent_column(campaign)
        )

    async def resolve_audience(self, campaign_id: uuid.UUID) -> int:
        """Materialise the segment into campaign_recipients. Idempotent.

        Returns the number of NEW rows. Re-running after a pause adds only people
        who have since qualified — the unique index absorbs the rest, so nobody
        already in the campaign is duplicated.
        """
        campaign = await self._require(campaign_id)
        segment = Segment.parse(campaign["audience_filter"])
        members = await self._segments.resolve(
            segment, consent_column=self._consent_column(campaign)
        )

        if not members:
            logger.warning("campaign_empty_audience id=%s", campaign_id)
            await self._session.commit()
            return 0

        inserted = 0
        for member in members:
            row = (
                await self._session.execute(
                    text(
                        """
                        INSERT INTO ops.campaign_recipients
                            (campaign_id, user_id, recipient, variables, status)
                        VALUES (:cid, :uid, :recipient, CAST(:vars AS JSONB), 'pending')
                        ON CONFLICT DO NOTHING
                        RETURNING id
                        """
                    ),
                    {
                        "cid": str(campaign_id),
                        "uid": str(member["user_id"]),
                        "recipient": member["recipient"],
                        # Per-recipient values merged over the campaign defaults
                        # at send time. Snapshotted here so a name change
                        # mid-campaign does not make two batches disagree.
                        "vars": _json({"customer_name": member["customer_name"]}),
                    },
                )
            ).scalar_one_or_none()
            if row is not None:
                inserted += 1

        await self._session.execute(
            text(
                """
                UPDATE ops.notification_campaigns
                SET total_recipients = (
                        SELECT COUNT(*) FROM ops.campaign_recipients
                         WHERE campaign_id = :cid
                    )
                WHERE id = :cid
                """
            ),
            {"cid": str(campaign_id)},
        )
        await self._session.commit()

        logger.info(
            "campaign_audience_resolved id=%s new=%s total=%s",
            campaign_id,
            inserted,
            len(members),
        )
        return inserted

    # ------------------------------------------------------------------ #
    # Run control
    # ------------------------------------------------------------------ #
    async def start(self, campaign_id: uuid.UUID) -> CampaignProgress:
        """Resolve the audience and mark the campaign running.

        Sending itself is the worker's job — this returns as soon as the campaign
        is queued, so an admin clicking "start" on a 20,000-person blast gets an
        immediate response instead of an HTTP timeout.
        """
        campaign = await self._require(campaign_id)
        if campaign["status"] not in _STARTABLE:
            raise CampaignError(
                f"A {campaign['status']} campaign cannot be started",
                code="campaign_bad_state",
            )

        await self.resolve_audience(campaign_id)

        await self._session.execute(
            text(
                """
                UPDATE ops.notification_campaigns
                SET status = 'running',
                    started_at = COALESCE(started_at, now()),
                    paused_at = NULL
                WHERE id = :id
                """
            ),
            {"id": str(campaign_id)},
        )
        await self._session.commit()
        logger.info("campaign_started id=%s", campaign_id)
        return await self.progress(campaign_id)

    async def pause(self, campaign_id: uuid.UUID) -> None:
        """Stop sending. In-flight batches finish; nothing new is claimed.

        Recipient rows are left exactly as they are, so resuming continues from
        where it stopped rather than restarting the campaign.
        """
        campaign = await self._require(campaign_id)
        if campaign["status"] != "running":
            raise CampaignError(
                f"A {campaign['status']} campaign is not running",
                code="campaign_bad_state",
            )
        await self._session.execute(
            text(
                """
                UPDATE ops.notification_campaigns
                SET status = 'paused', paused_at = now()
                WHERE id = :id
                """
            ),
            {"id": str(campaign_id)},
        )
        await self._session.commit()
        logger.info("campaign_paused id=%s", campaign_id)

    async def cancel(self, campaign_id: uuid.UUID) -> None:
        """Abandon a campaign. Unsent recipients are marked skipped."""
        await self._require(campaign_id)
        await self._session.execute(
            text(
                """
                UPDATE ops.campaign_recipients
                SET status = 'skipped', failure_reason = 'campaign cancelled'
                WHERE campaign_id = :id AND status = 'pending'
                """
            ),
            {"id": str(campaign_id)},
        )
        await self._session.execute(
            text(
                """
                UPDATE ops.notification_campaigns
                SET status = 'cancelled', completed_at = now()
                WHERE id = :id
                """
            ),
            {"id": str(campaign_id)},
        )
        await self._session.commit()
        logger.info("campaign_cancelled id=%s", campaign_id)

    # ------------------------------------------------------------------ #
    # Sending
    # ------------------------------------------------------------------ #
    async def send_batch(self, campaign_id: uuid.UUID) -> int:
        """Send one batch. Returns how many were dispatched.

        The claim-then-send shape matters: recipients are flipped to 'queued'
        inside a committed transaction *before* any provider call. A worker that
        dies mid-batch leaves them queued rather than pending, so the next worker
        does not re-send them — the notification's idempotency key would catch it
        anyway, but not before burning a Meta API call per recipient.
        """
        campaign = await self._require(campaign_id)
        if campaign["status"] != "running":
            return 0

        batch_size = self._settings.campaign_batch_size
        claimed = (
            await self._session.execute(
                text(
                    """
                    UPDATE ops.campaign_recipients
                    SET status = 'queued', queued_at = now()
                    WHERE id IN (
                        SELECT id FROM ops.campaign_recipients
                         WHERE campaign_id = :cid AND status = 'pending'
                         ORDER BY created_at
                         LIMIT :lim
                         FOR UPDATE SKIP LOCKED
                    )
                    RETURNING id, user_id, recipient, variables
                    """
                ),
                {"cid": str(campaign_id), "lim": batch_size},
            )
        ).mappings().all()
        await self._session.commit()

        if not claimed:
            await self._complete_if_done(campaign_id)
            return 0

        notification_type = campaign["notification_type"]
        campaign_variables = _as_dict(campaign["variables"])
        sent = 0

        for recipient in claimed:
            # Campaign defaults first, per-recipient values second — so a
            # personalised customer_name always beats the campaign-wide one.
            variables = campaign_variables | _as_dict(recipient["variables"])
            try:
                outcome = await self._whatsapp.send_marketing(
                    notification_type,
                    recipient=recipient["recipient"],
                    user_id=recipient["user_id"],
                    variables=variables,
                    # Keyed on the recipient row, so this exact send happens once
                    # no matter how many times the worker restarts.
                    idempotency_key=f"campaign:{campaign_id}:{recipient['id']}",
                    campaign_id=campaign_id,
                )
            except Exception:  # noqa: BLE001
                # One bad recipient must not abort the batch — the other 49
                # people in it have done nothing wrong.
                logger.exception(
                    "campaign_recipient_failed campaign=%s recipient=%s",
                    campaign_id,
                    recipient["id"],
                )
                await self._mark_recipient(
                    recipient["id"], status="failed", reason="send raised"
                )
                continue

            if outcome.notification_id is None:
                # Consent was withdrawn between resolution and send, or this send
                # was already claimed. Either way it is not a failure.
                await self._mark_recipient(
                    recipient["id"], status="skipped", reason="suppressed or duplicate"
                )
                continue

            await self._mark_recipient(
                recipient["id"],
                status="sent" if not outcome.failed else "failed",
                notification_id=outcome.notification_id,
                reason=None if not outcome.failed else "delivery failed",
            )
            if not outcome.failed:
                sent += 1

        await self._refresh_counters(campaign_id)
        await self._session.commit()

        # Pacing. Protects the phone number's quality rating, which OTP shares.
        await asyncio.sleep(self._settings.campaign_batch_pause_seconds)

        logger.info(
            "campaign_batch_sent id=%s claimed=%s sent=%s",
            campaign_id,
            len(claimed),
            sent,
        )
        return sent

    async def _mark_recipient(
        self,
        recipient_id: uuid.UUID,
        *,
        status: str,
        notification_id: uuid.UUID | None = None,
        reason: str | None = None,
    ) -> None:
        await self._session.execute(
            text(
                """
                UPDATE ops.campaign_recipients
                SET status = :status,
                    notification_id = COALESCE(:nid, notification_id),
                    failure_reason = :reason,
                    sent_at   = CASE WHEN :is_sent   THEN COALESCE(sent_at, now())   ELSE sent_at END,
                    failed_at = CASE WHEN :is_failed THEN COALESCE(failed_at, now()) ELSE failed_at END
                WHERE id = :id
                """
            ),
            {
                "id": str(recipient_id),
                "status": status,
                "nid": str(notification_id) if notification_id else None,
                "reason": (reason or "")[:400] or None,
                "is_sent": status == "sent",
                "is_failed": status == "failed",
            },
        )

    async def _refresh_counters(self, campaign_id: uuid.UUID) -> None:
        """Recompute the cached counters from the recipient rows.

        Recomputed rather than incremented: an increment that runs twice after a
        retry silently inflates the numbers an admin uses to judge the campaign.
        """
        await self._session.execute(
            text(
                """
                UPDATE ops.notification_campaigns c
                SET sent_count      = agg.sent,
                    delivered_count = agg.delivered,
                    read_count      = agg.read,
                    failed_count    = agg.failed,
                    skipped_count   = agg.skipped
                FROM (
                    SELECT
                      COUNT(*) FILTER (WHERE status IN ('sent','delivered','read')) AS sent,
                      COUNT(*) FILTER (WHERE status IN ('delivered','read'))        AS delivered,
                      COUNT(*) FILTER (WHERE status = 'read')                       AS read,
                      COUNT(*) FILTER (WHERE status = 'failed')                     AS failed,
                      COUNT(*) FILTER (WHERE status = 'skipped')                    AS skipped
                    FROM ops.campaign_recipients WHERE campaign_id = :cid
                ) agg
                WHERE c.id = :cid
                """
            ),
            {"cid": str(campaign_id)},
        )

    async def _complete_if_done(self, campaign_id: uuid.UUID) -> None:
        """Mark a running campaign complete once no pending recipients remain."""
        remaining = (
            await self._session.execute(
                text(
                    """
                    SELECT COUNT(*) FROM ops.campaign_recipients
                     WHERE campaign_id = :cid AND status = 'pending'
                    """
                ),
                {"cid": str(campaign_id)},
            )
        ).scalar_one()
        if int(remaining) > 0:
            return

        await self._refresh_counters(campaign_id)
        await self._session.execute(
            text(
                """
                UPDATE ops.notification_campaigns
                SET status = 'completed', completed_at = now()
                WHERE id = :cid AND status = 'running'
                """
            ),
            {"cid": str(campaign_id)},
        )
        await self._session.commit()
        logger.info("campaign_completed id=%s", campaign_id)

    # ------------------------------------------------------------------ #
    # Reporting
    # ------------------------------------------------------------------ #
    async def progress(self, campaign_id: uuid.UUID) -> CampaignProgress:
        campaign = await self._require(campaign_id)
        row = (
            await self._session.execute(
                text(
                    """
                    SELECT
                      COUNT(*)                                        AS resolved,
                      COUNT(*) FILTER (WHERE status IN ('sent','delivered','read')) AS sent,
                      COUNT(*) FILTER (WHERE status = 'failed')       AS failed,
                      COUNT(*) FILTER (WHERE status = 'skipped')      AS skipped,
                      COUNT(*) FILTER (WHERE status = 'pending')      AS remaining
                    FROM ops.campaign_recipients WHERE campaign_id = :cid
                    """
                ),
                {"cid": str(campaign_id)},
            )
        ).mappings().one()

        return CampaignProgress(
            campaign_id=campaign_id,
            resolved=int(row["resolved"]),
            sent=int(row["sent"]),
            failed=int(row["failed"]),
            skipped=int(row["skipped"]),
            remaining=int(row["remaining"]),
            status=campaign["status"],
        )

    async def analytics(self, campaign_id: uuid.UUID) -> dict[str, Any]:
        """The campaign analytics payload.

        Computed from campaign_recipients rather than the cached counters,
        because this is the number someone makes a decision on and the recipient
        rows are the source of truth.

        Rates are percentages of *recipients*, not of *sent* — a campaign where
        980 of 1000 sent and all 980 delivered has a 98% delivery rate, not 100%.
        Dividing by sent would hide the 20 that never went out at all.
        """
        campaign = await self._require(campaign_id)
        row = (
            await self._session.execute(
                text(
                    """
                    SELECT
                      COUNT(*)                                        AS recipients,
                      COUNT(*) FILTER (WHERE status IN ('sent','delivered','read')) AS sent,
                      COUNT(*) FILTER (WHERE status IN ('delivered','read'))        AS delivered,
                      COUNT(*) FILTER (WHERE status = 'read')         AS read,
                      COUNT(*) FILTER (WHERE status = 'failed')       AS failed,
                      COUNT(*) FILTER (WHERE status = 'skipped')      AS skipped
                    FROM ops.campaign_recipients WHERE campaign_id = :cid
                    """
                ),
                {"cid": str(campaign_id)},
            )
        ).mappings().one()

        recipients = int(row["recipients"])

        def rate(count: int) -> float:
            return round(count * 100 / recipients, 2) if recipients else 0.0

        return {
            "campaign_id": str(campaign_id),
            "name": campaign["name"],
            "status": campaign["status"],
            "recipients": recipients,
            "sent": int(row["sent"]),
            "delivered": int(row["delivered"]),
            "read": int(row["read"]),
            "failed": int(row["failed"]),
            "skipped": int(row["skipped"]),
            "delivery_rate": rate(int(row["delivered"])),
            "read_rate": rate(int(row["read"])),
            "failure_rate": rate(int(row["failed"])),
            "started_at": campaign["started_at"],
            "completed_at": campaign["completed_at"],
        }

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    async def _require(self, campaign_id: uuid.UUID) -> dict:
        row = (
            await self._session.execute(
                text("SELECT * FROM ops.notification_campaigns WHERE id = :id"),
                {"id": str(campaign_id)},
            )
        ).mappings().first()
        if row is None:
            raise CampaignError(
                "Campaign not found", code="campaign_not_found", status_code=404
            )
        return dict(row)

    @staticmethod
    def _consent_column(campaign: dict) -> str:
        """Cart campaigns check the cart opt-in; everything else checks marketing."""
        if str(campaign.get("campaign_type") or "") == "ABANDONED_CART":
            return "whatsapp_abandoned_cart"
        return "whatsapp_marketing"


def _json(value: Any) -> str:
    import json

    return json.dumps(value or {}, default=str)


def _as_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        import json

        try:
            parsed = json.loads(raw or "{}")
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            return {}
    return {}
