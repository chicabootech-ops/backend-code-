"""Background loop that keeps unresolved payments moving.

Runs in-process alongside the event worker. It exists so that a customer whose
webhook never arrived does not depend on someone noticing — Case 16 is only
actually handled if something sweeps without being asked.

The loop itself is deliberately dumb: wake up, ask ReconciliationService for one
sweep, sleep. All the judgement lives in the service, which is also what the
admin endpoint calls, so scheduled and manual reconciliation cannot diverge.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.storefront.lib.razorpay_client import RazorpayClient
from app.storefront.services.payment_service import PaymentService
from app.storefront.services.reconciliation_service import ReconciliationService

logger = logging.getLogger(__name__)

#: How often to sweep. Backoff per payment lives in ReconciliationService, so
#: this only needs to be fine-grained enough not to add much latency of its own.
SWEEP_INTERVAL_SECONDS = 60.0
#: Give the app a moment to finish starting before the first sweep.
STARTUP_DELAY_SECONDS = 20.0
ERROR_BACKOFF_SECONDS = 120.0
#: Payments per sweep. Keeps any single pass bounded.
BATCH_LIMIT = 50


class ReconciliationWorker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        razorpay: RazorpayClient,
        *,
        email_service: Any | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._razorpay = razorpay
        self._email = email_service
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if not self._razorpay.configured:
            # Without keys every sweep would fail on the first API call.
            logger.info("Reconciliation worker disabled — Razorpay is not configured")
            return
        self._task = asyncio.create_task(self._run(), name="chicaboo-payment-reconciler")
        logger.info("Payment reconciliation worker started")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        logger.info("Payment reconciliation worker stopped")

    async def _run(self) -> None:
        await asyncio.sleep(STARTUP_DELAY_SECONDS)
        while True:
            try:
                summary = await self.sweep_once()
                if summary.get("payments_checked"):
                    logger.info("payment_reconcile_sweep %s", summary)
                await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — a bad sweep must not kill the loop
                logger.exception("Reconciliation sweep failed; backing off")
                await asyncio.sleep(ERROR_BACKOFF_SECONDS)

    async def sweep_once(self) -> dict:
        """One sweep in its own session. Exposed so tests can drive it directly."""
        async with self._session_factory() as session:
            service = _build(session, self._razorpay, self._email)
            try:
                return await service.run(limit=BATCH_LIMIT)
            except Exception:
                await session.rollback()
                raise


def _build(
    session: AsyncSession, razorpay: RazorpayClient, email_service: Any | None
) -> ReconciliationService:
    """Wire a reconciler that shares PaymentService's state machine and settlement.

    Reusing PaymentService here is the point: reconciliation must apply exactly
    the same transition rules and the same once-only side effects as the webhook
    path, or the two would drift and a reconciled payment would confirm an order
    differently from a webhooked one.
    """
    payment_service = PaymentService(session, razorpay=razorpay, email_service=email_service)
    return ReconciliationService(session, razorpay, payment_service=payment_service)
