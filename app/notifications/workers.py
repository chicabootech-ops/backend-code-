"""Background workers for the WhatsApp platform.

These replace what the spec called Celery tasks. They are plain asyncio loops on
the existing event-worker pattern rather than Celery jobs, because every provider
call in this codebase is async — a Celery task would need its own synchronous
engine and an `asyncio.run()` bridge per job, plus two more Render processes.

    NotificationWorker   drains pending notifications
    RetryWorker          re-sends transient failures on the ladder
    ReconcileWorker      resolves UNKNOWN sends that never got a webhook
    CampaignWorker       starts due campaigns and paces their batches
    CartReminderWorker   runs the 1h / 24h / 48h ladder
    MaintenanceWorker    OTP cleanup and the daily analytics rollup

Every loop follows the same three rules:

*   **Each tick opens its own session and commits per unit of work.** A single
    long-lived transaction across a whole batch would hold row locks for minutes
    and roll back everyone's work when one recipient fails.
*   **Exceptions are caught per item and per tick.** A worker that dies on one
    bad row stops delivering for everybody.
*   **Nothing here decides delivery semantics.** Retry counts, consent and
    idempotency live in the services; workers only choose *when* to call them.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings
from app.notifications.analytics_service import AnalyticsService
from app.notifications.campaign_service import CampaignService
from app.notifications.cart_reminder_service import CartReminderService
from app.notifications.repository import NotificationRepository
from app.notifications.service import NotificationService

logger = logging.getLogger(__name__)


class _Loop:
    """Shared start/stop plumbing for a periodic async worker."""

    #: Seconds between ticks. Subclasses override.
    interval = 30.0
    #: Back-off after an unhandled error, so a broken dependency does not spin.
    error_backoff = 30.0
    name = "worker"

    def __init__(
        self,
        session_factory: async_sessionmaker,
        settings: Settings,
        *,
        build_notifications,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._build_notifications = build_notifications
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name=f"chicaboo-{self.name}")
        logger.info("%s started interval=%ss", self.name, self.interval)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        logger.info("%s stopped", self.name)

    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
                await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("%s tick failed; backing off", self.name)
                await asyncio.sleep(self.error_backoff)

    async def tick(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def _notifications(self, session) -> NotificationService:
        return self._build_notifications(session)


class NotificationWorker(_Loop):
    """Delivers notifications created with `deliver_now=False`.

    Before this existed, `deliver_now=False` created a row that nothing ever
    picked up — the notification simply sat 'pending' forever. Campaign sends and
    any deferred transactional message depend on this loop.
    """

    name = "notification-worker"
    interval = 5.0

    async def tick(self) -> None:
        async with self._session_factory() as session:
            repo = NotificationRepository(session)
            pending = await repo.pending_batch(limit=50)
            # The batch query took FOR UPDATE SKIP LOCKED row locks; commit to
            # release them before the (slow) provider calls, so a second worker
            # is not blocked behind this one's HTTP round-trips.
            await session.commit()

            if not pending:
                return

            notifications = self._notifications(session)
            for notification_id in pending:
                try:
                    await notifications.deliver(notification_id)
                except Exception:  # noqa: BLE001
                    logger.exception("delivery_failed id=%s", notification_id)
                    await session.rollback()

            logger.info("notifications_delivered count=%s", len(pending))


class RetryWorker(_Loop):
    """Re-sends transient failures whose backoff has elapsed.

    Runs on a slower interval than the delivery worker: the shortest rung of the
    ladder is five minutes, so polling every few seconds would be pure noise.
    """

    name = "retry-worker"
    interval = 60.0

    async def tick(self) -> None:
        async with self._session_factory() as session:
            repo = NotificationRepository(session)
            due = await repo.retry_batch(limit=50)
            await session.commit()

            if not due:
                return

            notifications = self._notifications(session)
            for notification_id in due:
                try:
                    # `deliver` increments the attempt number, so the retry
                    # claims a fresh attempt row rather than colliding with the
                    # failed one.
                    await notifications.deliver(notification_id)
                except Exception:  # noqa: BLE001
                    logger.exception("retry_failed id=%s", notification_id)
                    await session.rollback()

            logger.info("notifications_retried count=%s", len(due))


class ReconcileWorker(_Loop):
    """Resolves UNKNOWN notifications that never received a delivery signal.

    An UNKNOWN is a send where the provider call timed out — Meta may or may not
    have delivered it. The service layer deliberately refuses to guess. If no
    webhook has arrived after the grace period, this closes the row out.

    It resolves to FAILED, not to delivered: a message with no delivery signal
    after several minutes most likely did not arrive, and marking it failed makes
    it visible in the failure metrics rather than silently counted as fine. It
    does NOT re-send — the whole point of UNKNOWN is that re-sending risks a
    duplicate OTP.
    """

    name = "reconcile-worker"
    interval = 120.0

    async def tick(self) -> None:
        async with self._session_factory() as session:
            repo = NotificationRepository(session)
            stale = await repo.stale_unknown_batch(
                older_than_seconds=self._settings.notification_unknown_reconcile_seconds,
                limit=100,
            )
            if not stale:
                await session.commit()
                return

            for row in stale:
                await repo.set_status(
                    row["id"],
                    status="failed",
                    failed=True,
                    completed=True,
                    last_error="no delivery signal received; resolved by reconciler",
                )
                logger.warning(
                    "notification_reconciled_unknown id=%s type=%s",
                    row["id"],
                    row["notification_type"],
                )
            await session.commit()
            logger.info("notifications_reconciled count=%s", len(stale))


class CampaignWorker(_Loop):
    """Starts scheduled campaigns and paces batches for running ones."""

    name = "campaign-worker"
    interval = 15.0

    async def tick(self) -> None:
        async with self._session_factory() as session:
            notifications = self._notifications(session)
            campaigns = CampaignService(session, self._settings, notifications=notifications)

            # 1. Launch anything whose scheduled time has arrived.
            due = (
                await session.execute(
                    text(
                        """
                        SELECT id FROM ops.notification_campaigns
                        WHERE status = 'scheduled'
                          AND scheduled_at IS NOT NULL
                          AND scheduled_at <= now()
                          AND channel = 'whatsapp'
                        ORDER BY scheduled_at
                        LIMIT 5
                        FOR UPDATE SKIP LOCKED
                        """
                    )
                )
            ).scalars().all()
            await session.commit()

            for campaign_id in due:
                try:
                    await campaigns.start(campaign_id)
                    logger.info("scheduled_campaign_started id=%s", campaign_id)
                except Exception:  # noqa: BLE001
                    logger.exception("scheduled_campaign_failed id=%s", campaign_id)
                    await session.rollback()

            # 2. Push one batch for each running campaign. One batch per tick
            #    rather than draining the whole campaign, so a 20k blast cannot
            #    monopolise the worker and starve the others.
            running = (
                await session.execute(
                    text(
                        """
                        SELECT id FROM ops.notification_campaigns
                        WHERE status = 'running' AND channel = 'whatsapp'
                        ORDER BY started_at
                        LIMIT 5
                        """
                    )
                )
            ).scalars().all()
            await session.commit()

            for campaign_id in running:
                try:
                    await campaigns.send_batch(campaign_id)
                except Exception:  # noqa: BLE001
                    logger.exception("campaign_batch_failed id=%s", campaign_id)
                    await session.rollback()


class CartReminderWorker(_Loop):
    """Runs the abandoned-cart ladder.

    Every ten minutes rather than continuously: the rungs are an hour apart at
    the closest, so a tighter loop would only re-run the same empty queries.
    """

    name = "cart-reminder-worker"
    interval = 600.0

    async def tick(self) -> None:
        async with self._session_factory() as session:
            notifications = self._notifications(session)
            carts = CartReminderService(session, self._settings, notifications=notifications)

            total = 0
            # Ascending order matters: stage 2 requires stage 1 to exist, so
            # running them in this order lets a cart advance one rung per tick
            # rather than waiting a full cycle between rungs.
            for stage in (1, 2, 3):
                try:
                    total += await carts.run_stage(stage, limit=100)
                except Exception:  # noqa: BLE001
                    logger.exception("cart_reminder_stage_failed stage=%s", stage)
                    await session.rollback()

            try:
                await carts.mark_abandoned()
            except Exception:  # noqa: BLE001
                logger.exception("cart_mark_abandoned_failed")
                await session.rollback()

            if total:
                logger.info("cart_reminders_sent total=%s", total)


class MaintenanceWorker(_Loop):
    """OTP cleanup and the daily analytics rollup.

    Hourly. Both jobs are cheap and idempotent, so the exact cadence does not
    matter; what matters is that expired OTP hashes do not accumulate forever.
    """

    name = "maintenance-worker"
    interval = 3600.0

    async def tick(self) -> None:
        async with self._session_factory() as session:
            # 1. Purge spent OTP challenges. Consumed/superseded/expired rows
            #    have no further use, and each holds an Argon2 hash of a code.
            #    Kept for 7 days so the OTP success-rate metric has a window to
            #    read — deleting immediately would zero out the analytics.
            deleted = await session.execute(
                text(
                    """
                    DELETE FROM identity.otp_challenges
                    WHERE created_at < now() - interval '7 days'
                      AND (consumed_at IS NOT NULL
                           OR superseded_at IS NOT NULL
                           OR expires_at < now())
                    """
                )
            )
            await session.commit()
            if deleted.rowcount:
                logger.info("otp_challenges_purged count=%s", deleted.rowcount)

            # 2. Roll up yesterday. Idempotent, so re-running after a restart
            #    corrects rather than duplicates.
            analytics = AnalyticsService(session)
            try:
                await analytics.build_daily_rollup()
            except Exception:  # noqa: BLE001
                logger.exception("analytics_rollup_failed")
                await session.rollback()

            # 3. Trim webhook events. Meta redeliveries stop long before this;
            #    the table is a debugging aid, not a ledger.
            trimmed = await session.execute(
                text(
                    """
                    DELETE FROM ops.whatsapp_webhook_events
                    WHERE created_at < now() - interval '30 days'
                    """
                )
            )
            await session.commit()
            if trimmed.rowcount:
                logger.info("webhook_events_trimmed count=%s", trimmed.rowcount)


class WorkerSupervisor:
    """Starts and stops every notification worker as one unit.

    Held on `app.state` and driven by the FastAPI lifespan, so the workers share
    the API process. That is right for this traffic level — the loops are idle
    almost all the time. If message volume grows enough that they compete with
    request handling, the same classes run unchanged in a separate Render worker
    service pointed at `run_workers()`.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker,
        settings: Settings,
        *,
        build_notifications,
    ) -> None:
        common = {
            "settings": settings,
            "build_notifications": build_notifications,
        }
        self._workers = [
            NotificationWorker(session_factory, **common),
            RetryWorker(session_factory, **common),
            ReconcileWorker(session_factory, **common),
            CampaignWorker(session_factory, **common),
            CartReminderWorker(session_factory, **common),
            MaintenanceWorker(session_factory, **common),
        ]
        self._settings = settings

    async def start(self) -> None:
        if not self._settings.whatsapp_configured:
            # Without credentials every tick would claim work, fail to send and
            # burn the retry budget. Better to stay idle and say so once.
            logger.warning(
                "Notification workers not started — WhatsApp is not configured. "
                "Set WHATSAPP_ENABLED=true, WHATSAPP_ACCESS_TOKEN and "
                "WHATSAPP_PHONE_NUMBER_ID."
            )
            return
        for worker in self._workers:
            await worker.start()
        logger.info("notification workers started count=%s", len(self._workers))

    async def stop(self) -> None:
        for worker in self._workers:
            await worker.stop()


async def run_workers() -> None:  # pragma: no cover - process entrypoint
    """Entrypoint for running the workers as a standalone Render service.

    Usage: `python -m app.notifications.workers`
    """
    from app.config import settings
    from app.db.session import create_engine, create_session_factory
    from app.notifications.providers.whatsapp import WhatsAppProvider
    from app.notifications.types import Channel

    # database_dsn, not database_url — it normalises the driver prefix and
    # strips the query-string artefacts Neon's connection strings carry.
    engine = create_engine(settings.database_dsn)
    session_factory = create_session_factory(engine)
    providers = {Channel.WHATSAPP: WhatsAppProvider(settings)}

    def build_notifications(session):
        return NotificationService(session, settings, providers=providers)

    supervisor = WorkerSupervisor(
        session_factory, settings, build_notifications=build_notifications
    )
    await supervisor.start()
    logger.info("standalone worker process running since %s", datetime.now(UTC))
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await supervisor.stop()
        await WhatsAppProvider.aclose()
        await engine.dispose()


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_workers())
