"""Admin payment reconciliation.

Note what is absent: there is no endpoint that sets a payment to paid. Payment
state is established by the provider, and a manual override would defeat the
entire state machine. The strongest action an admin has is "go ask Razorpay
again", which applies the real answer through the same path as a webhook.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query

from app.admin_api.core.security.permissions import MaintenanceRunner
from app.admin_api.dependencies import CurrentAdmin, DbSession
from app.admin_api.services.payment_admin_service import (
    PaymentAdminService,
    PaymentDetailOut,
    PaymentQueueOut,
)
from app.config import settings
from app.storefront.lib.razorpay_client import RazorpayClient
from app.storefront.workers.reconciliation_worker import _build

router = APIRouter(prefix="/admin/payments", tags=["admin-payments"])


@router.get("", response_model=PaymentQueueOut)
async def payment_queue(
    _admin: CurrentAdmin,
    db: DbSession,
    scope: str = Query(default="attention", pattern="^(attention|unresolved|all)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> PaymentQueueOut:
    """Payments needing a human, unresolved payments, or everything."""
    return await PaymentAdminService(db).queue(scope=scope, page=page, page_size=page_size)


@router.get("/{order_id}", response_model=PaymentDetailOut)
async def payment_detail(
    order_id: uuid.UUID,
    _admin: CurrentAdmin,
    db: DbSession,
) -> PaymentDetailOut:
    """Every attempt, refund, webhook delivery and timeline entry for one order."""
    detail = await PaymentAdminService(db).detail(order_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return detail


@router.post("/{order_id}/reconcile")
async def reconcile_order_payment(
    order_id: uuid.UUID,
    _admin: MaintenanceRunner,
    db: DbSession,
) -> dict:
    """Force an immediate provider check for this order's unresolved attempts.

    Clears the backoff gate and the review flag so the sweep will pick the
    payment up, then runs one sweep synchronously.
    """
    from sqlalchemy import text

    await db.execute(
        text(
            """
            UPDATE commerce.payments
            SET next_reconcile_at = now(),
                reconcile_attempts = 0,
                needs_admin_review = FALSE
            WHERE order_id = :oid
              AND status IN ('created','pending','verification_required','authorized')
            """
        ),
        {"oid": str(order_id)},
    )
    await db.commit()

    return await _build(db, RazorpayClient(settings), None).run(limit=25)
