from __future__ import annotations

from fastapi import APIRouter, Query

from app.admin_api.core.security.permissions import MaintenanceRunner
from app.admin_api.dependencies import CurrentAdmin, DbSession
from app.config import settings
from app.storefront.lib.razorpay_client import RazorpayClient
from app.storefront.workers.reconciliation_worker import _build

router = APIRouter(prefix="/admin/maintenance", tags=["admin-maintenance"])


@router.post("/reconcile")
async def reconcile(
    _admin: MaintenanceRunner,
    db: DbSession,
    limit: int = Query(default=50, ge=1, le=500),
) -> dict:
    """Reconcile unresolved payments against Razorpay and release expired stock.

    Runs the same sweep as the background worker — manual and scheduled
    reconciliation share one code path so they cannot reach different verdicts.
    An order is only ever cancelled when Razorpay confirms no payment exists.
    """
    return await _build(db, RazorpayClient(settings), None).run(limit=limit)
