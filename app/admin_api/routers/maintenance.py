from __future__ import annotations

from fastapi import APIRouter, Query

from app.admin_api.dependencies import CurrentAdmin, DbSession
from app.storefront.services.reconciliation_service import ReconciliationService

router = APIRouter(prefix="/admin/maintenance", tags=["admin-maintenance"])


@router.post("/reconcile")
async def reconcile(
    _admin: CurrentAdmin,
    db: DbSession,
    stale_minutes: int = Query(default=30, ge=1, le=1440),
) -> dict:
    """Release expired stock reservations and auto-cancel abandoned pending orders."""
    return await ReconciliationService(db).run(stale_minutes=stale_minutes)
