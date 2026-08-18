"""Messaging analytics.

Two read paths, deliberately separate:

*   **Live queries** over `ops.notifications` for "what happened today" and for a
    single campaign. Bounded and indexed.
*   **A daily rollup** into `ops.notification_analytics_daily` for anything
    spanning weeks. `ops.notifications` is on the OTP hot path — a dashboard
    scanning three months of it competes with someone trying to log in.

The rate definitions are stated once here because they are easy to define
plausibly and wrongly:

    delivery_rate = delivered / requested   (NOT delivered / sent)
    read_rate     = read      / delivered   (NOT read / requested)
    otp_success   = verified  / issued

Delivery is measured against everything we *tried* to send, so messages that
never left count against it — dividing by `sent` would hide a broken template
behind a perfect score. Read is measured against delivered, because a message
that never arrived cannot be read and counting it as unread understates
engagement. OTP success counts challenges actually consumed, which is the number
that answers "can customers log in".
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator * 100 / denominator, 2) if denominator else 0.0


class AnalyticsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ #
    # Overview
    # ------------------------------------------------------------------ #
    async def overview(self, *, days: int = 30) -> dict[str, Any]:
        """Headline messaging numbers for the dashboard."""
        row = (
            await self._session.execute(
                text(
                    """
                    SELECT
                      COUNT(*)                                              AS requested,
                      COUNT(*) FILTER (WHERE status IN ('sent','delivered','read')) AS sent,
                      COUNT(*) FILTER (WHERE status IN ('delivered','read'))        AS delivered,
                      COUNT(*) FILTER (WHERE status = 'read')               AS read,
                      COUNT(*) FILTER (WHERE status = 'failed')             AS failed,
                      COUNT(*) FILTER (WHERE status = 'unknown')            AS unknown,
                      COUNT(*) FILTER (WHERE category = 'otp')              AS otp_total,
                      COUNT(*) FILTER (WHERE category = 'marketing')        AS marketing_total
                    FROM ops.notifications
                    WHERE created_at > now() - make_interval(days => :days)
                    """
                ),
                {"days": days},
            )
        ).mappings().one()

        requested = int(row["requested"])
        delivered = int(row["delivered"])

        return {
            "window_days": days,
            "requested": requested,
            "sent": int(row["sent"]),
            "delivered": delivered,
            "read": int(row["read"]),
            "failed": int(row["failed"]),
            "unknown": int(row["unknown"]),
            "otp_total": int(row["otp_total"]),
            "marketing_total": int(row["marketing_total"]),
            "delivery_rate": _rate(delivered, requested),
            "read_rate": _rate(int(row["read"]), delivered),
            "failure_rate": _rate(int(row["failed"]), requested),
        }

    async def otp_success_rate(self, *, days: int = 30) -> dict[str, Any]:
        """How reliably customers can actually authenticate.

        `consumed_at IS NOT NULL` is the success signal — the code was entered
        correctly. Expired-unused and superseded challenges are counted
        separately, because they mean different things: expired suggests the
        message never arrived, superseded means the customer asked again.
        """
        row = (
            await self._session.execute(
                text(
                    """
                    SELECT
                      COUNT(*)                                             AS issued,
                      COUNT(*) FILTER (WHERE consumed_at IS NOT NULL)      AS verified,
                      COUNT(*) FILTER (WHERE superseded_at IS NOT NULL)    AS superseded,
                      COUNT(*) FILTER (WHERE consumed_at IS NULL
                                       AND superseded_at IS NULL
                                       AND expires_at < now())             AS expired,
                      COALESCE(AVG(attempts) FILTER (WHERE consumed_at IS NOT NULL), 0)
                                                                           AS avg_attempts
                    FROM identity.otp_challenges
                    WHERE created_at > now() - make_interval(days => :days)
                    """
                ),
                {"days": days},
            )
        ).mappings().one()

        issued = int(row["issued"])
        return {
            "window_days": days,
            "issued": issued,
            "verified": int(row["verified"]),
            "expired_unused": int(row["expired"]),
            "superseded": int(row["superseded"]),
            "success_rate": _rate(int(row["verified"]), issued),
            "avg_attempts_to_verify": round(float(row["avg_attempts"]), 2),
        }

    async def by_type(self, *, days: int = 30) -> list[dict[str, Any]]:
        """Per-notification-type breakdown — which messages fail."""
        rows = (
            await self._session.execute(
                text(
                    """
                    SELECT
                      notification_type,
                      category,
                      COUNT(*)                                              AS requested,
                      COUNT(*) FILTER (WHERE status IN ('delivered','read')) AS delivered,
                      COUNT(*) FILTER (WHERE status = 'read')               AS read,
                      COUNT(*) FILTER (WHERE status = 'failed')             AS failed
                    FROM ops.notifications
                    WHERE created_at > now() - make_interval(days => :days)
                      AND notification_type IS NOT NULL
                    GROUP BY notification_type, category
                    ORDER BY requested DESC
                    """
                ),
                {"days": days},
            )
        ).mappings().all()

        return [
            {
                "notification_type": r["notification_type"],
                "category": r["category"],
                "requested": int(r["requested"]),
                "delivered": int(r["delivered"]),
                "read": int(r["read"]),
                "failed": int(r["failed"]),
                "delivery_rate": _rate(int(r["delivered"]), int(r["requested"])),
                "read_rate": _rate(int(r["read"]), int(r["delivered"])),
            }
            for r in rows
        ]

    async def failure_reasons(self, *, days: int = 30, limit: int = 20) -> list[dict]:
        """The actual Meta error codes behind failures.

        Grouped by code rather than message because Meta varies the human text;
        the code is what maps to a fix (132001 = template not approved, 131026 =
        recipient not on WhatsApp).
        """
        rows = (
            await self._session.execute(
                text(
                    """
                    SELECT failure_code,
                           MAX(failure_reason) AS example,
                           error_class,
                           COUNT(*) AS count
                    FROM ops.notification_attempts
                    WHERE status = 'failed'
                      AND requested_at > now() - make_interval(days => :days)
                      AND failure_code IS NOT NULL
                    GROUP BY failure_code, error_class
                    ORDER BY count DESC
                    LIMIT :lim
                    """
                ),
                {"days": days, "lim": limit},
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Campaign performance
    # ------------------------------------------------------------------ #
    async def top_campaigns(self, *, limit: int = 10) -> list[dict[str, Any]]:
        """Best-performing campaigns by read rate.

        Ordered by read rate rather than raw reads so a small, well-targeted
        campaign is not buried under a large indiscriminate one.
        """
        rows = (
            await self._session.execute(
                text(
                    """
                    SELECT
                      c.id, c.name, c.campaign_type, c.status,
                      c.started_at, c.completed_at,
                      COUNT(r.*)                                              AS recipients,
                      COUNT(r.*) FILTER (WHERE r.status IN ('delivered','read')) AS delivered,
                      COUNT(r.*) FILTER (WHERE r.status = 'read')             AS read
                    FROM ops.notification_campaigns c
                    LEFT JOIN ops.campaign_recipients r ON r.campaign_id = c.id
                    WHERE c.channel = 'whatsapp'
                      AND c.status IN ('running', 'completed')
                    GROUP BY c.id
                    HAVING COUNT(r.*) > 0
                    ORDER BY (COUNT(r.*) FILTER (WHERE r.status = 'read'))::float
                             / NULLIF(COUNT(r.*), 0) DESC
                    LIMIT :lim
                    """
                ),
                {"lim": limit},
            )
        ).mappings().all()

        return [
            {
                "campaign_id": str(r["id"]),
                "name": r["name"],
                "campaign_type": r["campaign_type"],
                "status": r["status"],
                "recipients": int(r["recipients"]),
                "delivered": int(r["delivered"]),
                "read": int(r["read"]),
                "delivery_rate": _rate(int(r["delivered"]), int(r["recipients"])),
                "read_rate": _rate(int(r["read"]), int(r["recipients"])),
                "started_at": r["started_at"],
                "completed_at": r["completed_at"],
            }
            for r in rows
        ]

    async def campaign_revenue(self, *, days: int = 30) -> list[dict[str, Any]]:
        """Revenue attributed to campaigns.

        **Attribution model, stated explicitly because it is a judgement call:**
        an order counts toward a campaign if the customer received that campaign
        and then ordered within the attribution window. This is last-touch and
        generous — it cannot distinguish a purchase the campaign caused from one
        that was going to happen anyway. Treat it as directional, not as proof.
        """
        rows = (
            await self._session.execute(
                text(
                    """
                    SELECT
                      c.id, c.name, c.campaign_type,
                      COUNT(DISTINCT o.id)                     AS orders,
                      COALESCE(SUM(o.grand_total_paise), 0)    AS revenue_paise
                    FROM ops.notification_campaigns c
                    JOIN ops.campaign_recipients r
                      ON r.campaign_id = c.id
                     AND r.status IN ('sent', 'delivered', 'read')
                    JOIN commerce.orders o
                      ON o.user_id = r.user_id
                     AND o.status IN ('confirmed','processing','shipped','delivered','completed')
                     -- Ordered AFTER receiving it, within the window.
                     AND o.created_at > r.sent_at
                     AND o.created_at < r.sent_at + make_interval(days => :window)
                    WHERE c.channel = 'whatsapp'
                    GROUP BY c.id
                    ORDER BY revenue_paise DESC
                    """
                ),
                {"window": days},
            )
        ).mappings().all()

        return [
            {
                "campaign_id": str(r["id"]),
                "name": r["name"],
                "campaign_type": r["campaign_type"],
                "orders": int(r["orders"]),
                "revenue_paise": int(r["revenue_paise"]),
                "revenue": f"₹{int(r['revenue_paise']) // 100:,}",
                "attribution_window_days": days,
            }
            for r in rows
        ]

    # ------------------------------------------------------------------ #
    # Daily rollup
    # ------------------------------------------------------------------ #
    async def build_daily_rollup(self, *, day: date | None = None) -> int:
        """Compute one day's rollup. Idempotent — re-running overwrites.

        Defaults to yesterday rather than today: a rollup of a day still in
        progress is immediately stale, and worse, it looks authoritative.
        """
        rows = await self._session.execute(
            text(
                """
                INSERT INTO ops.notification_analytics_daily
                    (day, notification_type, category, channel,
                     requested, sent, delivered, read, failed, unknown)
                SELECT
                  DATE(n.created_at)                                    AS day,
                  n.notification_type,
                  n.category,
                  'whatsapp'                                            AS channel,
                  COUNT(*)                                              AS requested,
                  COUNT(*) FILTER (WHERE n.status IN ('sent','delivered','read')) AS sent,
                  COUNT(*) FILTER (WHERE n.status IN ('delivered','read'))        AS delivered,
                  COUNT(*) FILTER (WHERE n.status = 'read')             AS read,
                  COUNT(*) FILTER (WHERE n.status = 'failed')           AS failed,
                  COUNT(*) FILTER (WHERE n.status = 'unknown')          AS unknown
                FROM ops.notifications n
                WHERE DATE(n.created_at) = COALESCE(
                          CAST(:day AS DATE), CURRENT_DATE - 1)
                  AND n.notification_type IS NOT NULL
                GROUP BY DATE(n.created_at), n.notification_type, n.category
                ON CONFLICT (day, notification_type, channel) DO UPDATE
                SET requested = EXCLUDED.requested,
                    sent      = EXCLUDED.sent,
                    delivered = EXCLUDED.delivered,
                    read      = EXCLUDED.read,
                    failed    = EXCLUDED.failed,
                    unknown   = EXCLUDED.unknown,
                    computed_at = now()
                """
            ),
            {"day": day},
        )
        await self._session.commit()
        count = rows.rowcount or 0
        logger.info("analytics_rollup_built day=%s rows=%s", day or "yesterday", count)
        return count

    async def daily_series(
        self, *, days: int = 30, notification_type: str | None = None
    ) -> list[dict[str, Any]]:
        """Time series from the rollup, for dashboard charts."""
        rows = (
            await self._session.execute(
                text(
                    """
                    SELECT day,
                           SUM(requested) AS requested,
                           SUM(sent)      AS sent,
                           SUM(delivered) AS delivered,
                           SUM(read)      AS read,
                           SUM(failed)    AS failed
                    FROM ops.notification_analytics_daily
                    WHERE day > CURRENT_DATE - CAST(:days AS INTEGER)
                      -- CAST guards asyncpg's AmbiguousParameterError: a bind
                      -- param compared only against NULL has no inferable type.
                      AND (CAST(:ntype AS TEXT) IS NULL
                           OR notification_type = CAST(:ntype AS TEXT))
                    GROUP BY day
                    ORDER BY day
                    """
                ),
                {"days": days, "ntype": notification_type},
            )
        ).mappings().all()

        return [
            {
                "day": r["day"].isoformat(),
                "requested": int(r["requested"]),
                "sent": int(r["sent"]),
                "delivered": int(r["delivered"]),
                "read": int(r["read"]),
                "failed": int(r["failed"]),
                "delivery_rate": _rate(int(r["delivered"]), int(r["requested"])),
            }
            for r in rows
        ]
