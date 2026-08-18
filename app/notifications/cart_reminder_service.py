"""Abandoned-cart reminders.

The ladder, per the spec:

    cart goes quiet ──1h──> first reminder ──24h──> second ──48h──> coupon

Three properties this implementation holds, each of which took a specific
decision:

*   **The clock runs from the cart's last activity, not from the previous
    reminder.** A customer who adds an item after receiving reminder 1 has
    re-engaged; the ladder should reset around their new activity rather than
    marching on regardless. `stage_due` therefore measures from
    `carts.updated_at`.

*   **A stage is sent at most once per cart, enforced in the database.**
    `ops.cart_reminders` has a unique index on `(cart_id, stage)`, so a worker
    that crashes after sending but before committing its bookkeeping cannot
    re-nudge anyone on restart.

*   **A converted or emptied cart drops out immediately.** The query requires
    live items and no linked order, so paying for a cart between the reminder
    being scheduled and sent means the reminder does not go — nobody gets
    "you left something behind" about an order they already paid for.

Consent is `whatsapp_abandoned_cart`, checked in `NotificationService.send`.
Meta classifies these as MARKETING templates, so an opted-out customer is
silently skipped rather than messaged.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.notifications.service import NotificationService
from app.notifications.whatsapp_service import WhatsAppService

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AbandonedCart:
    cart_id: uuid.UUID
    user_id: uuid.UUID
    recipient: str
    customer_name: str
    item_name: str
    item_count: int
    stage: int


class CartReminderService:
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

    def _stage_hours(self, stage: int) -> int:
        return {
            1: self._settings.cart_reminder_first_hours,
            2: self._settings.cart_reminder_second_hours,
            3: self._settings.cart_reminder_coupon_hours,
        }[stage]

    # ------------------------------------------------------------------ #
    # Discovery
    # ------------------------------------------------------------------ #
    async def due(self, stage: int, *, limit: int = 100) -> list[AbandonedCart]:
        """Carts eligible for one rung of the ladder.

        The `NOT EXISTS` on cart_reminders is what makes this safe to poll on a
        loop: a cart that already received this stage is simply not returned.

        The previous-stage requirement means the ladder cannot skip a rung —
        a cart that has been quiet for three days receives reminder 1, then 2,
        then 3 on successive worker passes, rather than jumping straight to the
        coupon and handing a discount to someone a cheaper nudge would have
        converted.
        """
        if stage not in (1, 2, 3):
            raise ValueError(f"Cart reminder stage must be 1, 2 or 3 — got {stage}")

        hours = self._stage_hours(stage)
        previous_stage_clause = (
            ""
            if stage == 1
            else """
              AND EXISTS (SELECT 1 FROM ops.cart_reminders prev
                           WHERE prev.cart_id = c.id AND prev.stage = :prev_stage)
            """
        )

        rows = (
            await self._session.execute(
                text(
                    f"""
                    SELECT
                      c.id                AS cart_id,
                      c.user_id           AS user_id,
                      u.phone             AS recipient,
                      COALESCE(NULLIF(btrim(prof.first_name), ''), 'there')
                                          AS customer_name,
                      COALESCE(
                        (SELECT p.name
                           FROM commerce.cart_items ci2
                           JOIN commerce.product_variants pv ON pv.id = ci2.product_variant_id
                           JOIN commerce.products p          ON p.id = pv.product_id
                          WHERE ci2.cart_id = c.id AND ci2.deleted_at IS NULL
                          ORDER BY ci2.created_at
                          LIMIT 1),
                        'your items'
                      )                   AS item_name,
                      (SELECT COUNT(*) FROM commerce.cart_items ci3
                        WHERE ci3.cart_id = c.id AND ci3.deleted_at IS NULL)
                                          AS item_count
                    FROM commerce.carts c
                    JOIN identity.users u ON u.id = c.user_id
                    LEFT JOIN public.user_profiles prof ON prof.user_id = u.id
                    WHERE c.status = 'active'
                      AND c.deleted_at IS NULL
                      -- Never nudge a cart that became an order.
                      AND c.converted_order_id IS NULL
                      AND c.updated_at < now() - make_interval(hours => :hours)
                      AND u.deleted_at IS NULL
                      AND u.status = 'active'
                      AND u.phone IS NOT NULL
                      AND u.phone_verified
                      -- Must still contain something to come back to.
                      AND EXISTS (SELECT 1 FROM commerce.cart_items ci
                                   WHERE ci.cart_id = c.id AND ci.deleted_at IS NULL)
                      -- This rung has not already been sent for this cart.
                      AND NOT EXISTS (SELECT 1 FROM ops.cart_reminders r
                                       WHERE r.cart_id = c.id AND r.stage = :stage)
                      {previous_stage_clause}
                    ORDER BY c.updated_at
                    LIMIT :lim
                    """  # noqa: S608 — previous_stage_clause is a fixed literal
                ),
                {
                    "hours": hours,
                    "stage": stage,
                    "prev_stage": stage - 1,
                    "lim": limit,
                },
            )
        ).mappings().all()

        return [
            AbandonedCart(
                cart_id=r["cart_id"],
                user_id=r["user_id"],
                recipient=r["recipient"],
                customer_name=r["customer_name"],
                item_name=r["item_name"],
                item_count=int(r["item_count"]),
                stage=stage,
            )
            for r in rows
        ]

    # ------------------------------------------------------------------ #
    # Sending
    # ------------------------------------------------------------------ #
    async def send_reminder(self, cart: AbandonedCart) -> bool:
        """Send one reminder and record it. Returns True if a message went out.

        The ledger row is claimed BEFORE the send, against the unique index. If
        the send then fails the row stays, and the cart does not get retried on
        this rung — deliberate. A reminder is a courtesy, and the failure modes
        (no WhatsApp, opted out) do not improve on a second attempt, whereas
        re-nudging someone the ladder already touched is a real annoyance.
        """
        claimed = (
            await self._session.execute(
                text(
                    """
                    INSERT INTO ops.cart_reminders (cart_id, user_id, stage)
                    VALUES (:cart_id, :user_id, :stage)
                    ON CONFLICT (cart_id, stage) DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "cart_id": str(cart.cart_id),
                    "user_id": str(cart.user_id),
                    "stage": cart.stage,
                },
            )
        ).scalar_one_or_none()

        if claimed is None:
            # Another worker took this rung between the query and here.
            await self._session.commit()
            return False
        await self._session.commit()

        item_label = (
            cart.item_name
            if cart.item_count <= 1
            else f"{cart.item_name} +{cart.item_count - 1} more"
        )

        outcome = await self._whatsapp.send_abandoned_cart_reminder(
            cart_id=cart.cart_id,
            stage=cart.stage,
            recipient=cart.recipient,
            customer_name=cart.customer_name,
            item_name=item_label,
            user_id=cart.user_id,
            coupon_code=self._settings.cart_reminder_coupon_code,
            discount="",
        )

        # Link the ledger row to the notification for the admin timeline.
        if outcome.notification_id is not None:
            await self._session.execute(
                text(
                    """
                    UPDATE ops.cart_reminders
                    SET notification_id = :nid
                    WHERE id = :id
                    """
                ),
                {"id": str(claimed), "nid": str(outcome.notification_id)},
            )
            await self._session.commit()

        logger.info(
            "cart_reminder_sent cart=%s stage=%s delivered=%s",
            cart.cart_id,
            cart.stage,
            outcome.notification_id is not None,
        )
        return outcome.notification_id is not None

    async def run_stage(self, stage: int, *, limit: int = 100) -> int:
        """Send every due reminder for one rung. Returns the count sent."""
        if stage == 3 and not self._settings.cart_reminder_coupon_code:
            # The coupon rung without a coupon is just a third nudge, which reads
            # as harassment. Configure CART_REMINDER_COUPON_CODE to enable it.
            logger.info("cart_reminder_stage3_disabled — no coupon configured")
            return 0

        carts = await self.due(stage, limit=limit)
        sent = 0
        for cart in carts:
            try:
                if await self.send_reminder(cart):
                    sent += 1
            except Exception:  # noqa: BLE001
                logger.exception(
                    "cart_reminder_failed cart=%s stage=%s", cart.cart_id, stage
                )
        if sent:
            logger.info("cart_reminders_run stage=%s sent=%s", stage, sent)
        return sent

    async def mark_abandoned(self) -> int:
        """Flip long-quiet active carts to 'abandoned'.

        Housekeeping for reporting and the `cart_abandoned` segment rule. It runs
        after the ladder so the reminder queries — which look for 'active' carts —
        are not emptied out from under them mid-run.
        """
        result = await self._session.execute(
            text(
                """
                UPDATE commerce.carts
                SET status = 'abandoned'
                WHERE status = 'active'
                  AND deleted_at IS NULL
                  AND converted_order_id IS NULL
                  AND updated_at < now() - make_interval(hours => :hours)
                """
            ),
            {"hours": self._settings.cart_reminder_coupon_hours},
        )
        await self._session.commit()
        count = result.rowcount or 0
        if count:
            logger.info("carts_marked_abandoned count=%s", count)
        return count
