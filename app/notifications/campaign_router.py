"""Campaign APIs — `/api/v1/campaigns/*`.

Every route here is admin-only. Marketing sends cost money, consume Meta
conversation quota and reach real customers, so authorisation is not optional:
the router is mounted behind the admin guard and each mutation additionally
requires `Permission.MARKETING_SEND`.

Long operations return immediately. `start` resolves the audience and flips the
campaign to running; the worker does the sending. An admin launching a
20,000-person campaign gets a response in milliseconds rather than an HTTP
timeout halfway through the blast.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.admin_api.core.security.permissions import Permission, require
from app.admin_api.dependencies import CurrentAdmin
from app.config import settings
from app.notifications.analytics_service import AnalyticsService
from app.notifications.campaign_service import CampaignService, CampaignError
from app.notifications.segmentation import available_rules
from app.storefront.dependencies import DbSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/campaigns", tags=["campaigns"])


# ---------------------------------------------------------------------- #
# Schemas
# ---------------------------------------------------------------------- #
class AudienceFilter(BaseModel):
    rules: list[str] = Field(default_factory=list)
    match: str = Field(default="all", pattern="^(all|any)$")
    params: dict[str, Any] = Field(default_factory=dict)


class CreateCampaignRequest(BaseModel):
    campaign_name: str = Field(..., min_length=1, max_length=200)
    campaign_type: str = Field(default="MARKETING")
    notification_type: str | None = None
    audience_filter: AudienceFilter = Field(default_factory=AudienceFilter)
    variables: dict[str, Any] = Field(default_factory=dict)
    scheduled_at: datetime | None = None


class ScheduleCampaignRequest(BaseModel):
    campaign_id: uuid.UUID
    scheduled_at: datetime


class CampaignResponse(BaseModel):
    campaign_id: uuid.UUID
    status: str
    message: str


class AudiencePreview(BaseModel):
    campaign_id: uuid.UUID
    audience_size: int


# ---------------------------------------------------------------------- #
# Dependencies
# ---------------------------------------------------------------------- #
def get_campaign_service(request: Request, db: DbSession) -> CampaignService:
    build = getattr(request.app.state, "build_notifications", None)
    if build is None:  # pragma: no cover - misconfiguration
        raise HTTPException(status_code=503, detail="Messaging is not configured")
    return CampaignService(db, settings, notifications=build(db))


CampaignDep = Annotated[CampaignService, Depends(get_campaign_service)]

#: Mutations require MARKETING_SEND; reads only require a signed-in admin,
#: matching the codebase convention that reads stay open to every admin.
RequireMarketing = Depends(require(Permission.MARKETING_SEND))


def _handle(exc: CampaignError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code, detail={"code": exc.code, "message": exc.message}
    )


# ---------------------------------------------------------------------- #
# Segment catalogue
# ---------------------------------------------------------------------- #
@router.get("/segments")
async def list_segment_rules(admin: CurrentAdmin) -> dict:
    """The audience filters an admin can build a segment from."""
    return {"rules": available_rules()}


@router.post("/segments/preview")
async def preview_segment(
    payload: AudienceFilter,
    admin: CurrentAdmin,
    campaigns: CampaignDep,
) -> dict:
    """Count an ad-hoc segment before saving it to a campaign.

    Lets the admin UI show "this reaches 4,812 customers" while the segment is
    still being edited.
    """
    try:
        size = await campaigns.count_segment(payload.model_dump())
    except CampaignError as exc:
        raise _handle(exc) from exc

    return {
        "audience_size": size,
        "rules": payload.rules,
        "match": payload.match,
    }


# ---------------------------------------------------------------------- #
# CRUD
# ---------------------------------------------------------------------- #
@router.post("", response_model=CampaignResponse, dependencies=[RequireMarketing])
async def create_campaign(
    payload: CreateCampaignRequest,
    admin: CurrentAdmin,
    campaigns: CampaignDep,
) -> CampaignResponse:
    """Create a campaign. Draft unless `scheduled_at` is supplied."""
    try:
        campaign_id = await campaigns.create(
            name=payload.campaign_name,
            campaign_type=payload.campaign_type,
            notification_type=payload.notification_type,
            audience_filter=payload.audience_filter.model_dump(),
            variables=payload.variables,
            scheduled_at=payload.scheduled_at,
            created_by=admin.id,
        )
    except CampaignError as exc:
        raise _handle(exc) from exc

    return CampaignResponse(
        campaign_id=campaign_id,
        status="scheduled" if payload.scheduled_at else "draft",
        message="Campaign created.",
    )


@router.get("")
async def list_campaigns(
    admin: CurrentAdmin,
    db: DbSession,
    status: str | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """Campaign list for the admin dashboard."""
    from sqlalchemy import text

    rows = (
        await db.execute(
            text(
                """
                SELECT id, name, campaign_type, status, channel,
                       total_recipients, sent_count, delivered_count,
                       read_count, failed_count,
                       scheduled_at, started_at, completed_at, created_at
                FROM ops.notification_campaigns
                WHERE channel = 'whatsapp'
                  -- CAST guards asyncpg's AmbiguousParameterError: a bind param
                  -- compared only against NULL has no inferable type.
                  AND (CAST(:status AS TEXT) IS NULL OR status = CAST(:status AS TEXT))
                ORDER BY created_at DESC
                LIMIT :lim OFFSET :off
                """
            ),
            {"status": status, "lim": limit, "off": offset},
        )
    ).mappings().all()

    return {"campaigns": [dict(r) for r in rows], "limit": limit, "offset": offset}


@router.post("/schedule", response_model=CampaignResponse, dependencies=[RequireMarketing])
async def schedule_campaign(
    payload: ScheduleCampaignRequest,
    admin: CurrentAdmin,
    campaigns: CampaignDep,
) -> CampaignResponse:
    """Set or change a campaign's send time."""
    try:
        await campaigns.schedule(payload.campaign_id, when=payload.scheduled_at)
    except CampaignError as exc:
        raise _handle(exc) from exc

    return CampaignResponse(
        campaign_id=payload.campaign_id,
        status="scheduled",
        message=f"Campaign scheduled for {payload.scheduled_at.isoformat()}.",
    )


@router.get("/{campaign_id}/audience", response_model=AudiencePreview)
async def preview_campaign_audience(
    campaign_id: uuid.UUID,
    admin: CurrentAdmin,
    campaigns: CampaignDep,
) -> AudiencePreview:
    """How many people this campaign reaches right now."""
    try:
        size = await campaigns.preview_audience(campaign_id)
    except CampaignError as exc:
        raise _handle(exc) from exc
    return AudiencePreview(campaign_id=campaign_id, audience_size=size)


# ---------------------------------------------------------------------- #
# Run control
# ---------------------------------------------------------------------- #
@router.post("/{campaign_id}/start", dependencies=[RequireMarketing])
async def start_campaign(
    campaign_id: uuid.UUID,
    admin: CurrentAdmin,
    campaigns: CampaignDep,
) -> dict:
    """Resolve the audience and begin sending.

    Returns as soon as the campaign is queued. Progress is available from
    `/{id}/analytics` while it runs.
    """
    try:
        progress = await campaigns.start(campaign_id)
    except CampaignError as exc:
        raise _handle(exc) from exc

    logger.info("campaign_start_requested id=%s admin=%s", campaign_id, admin.id)
    return {
        "campaign_id": str(campaign_id),
        "status": progress.status,
        "recipients": progress.resolved,
        "remaining": progress.remaining,
        "message": f"Campaign started for {progress.resolved} recipients.",
    }


@router.post("/{campaign_id}/pause", dependencies=[RequireMarketing])
async def pause_campaign(
    campaign_id: uuid.UUID,
    admin: CurrentAdmin,
    campaigns: CampaignDep,
) -> dict:
    """Stop sending. Resume with `/start` — it continues where it stopped."""
    try:
        await campaigns.pause(campaign_id)
    except CampaignError as exc:
        raise _handle(exc) from exc

    logger.info("campaign_pause_requested id=%s admin=%s", campaign_id, admin.id)
    return {"campaign_id": str(campaign_id), "status": "paused"}


@router.post("/{campaign_id}/resume", dependencies=[RequireMarketing])
async def resume_campaign(
    campaign_id: uuid.UUID,
    admin: CurrentAdmin,
    campaigns: CampaignDep,
) -> dict:
    """Resume a paused campaign.

    Deliberately the same code path as `start`: audience resolution is
    idempotent, so resuming picks up anyone who has newly qualified without
    re-messaging anybody already in the campaign.
    """
    try:
        progress = await campaigns.start(campaign_id)
    except CampaignError as exc:
        raise _handle(exc) from exc

    return {
        "campaign_id": str(campaign_id),
        "status": progress.status,
        "remaining": progress.remaining,
    }


@router.post("/{campaign_id}/cancel", dependencies=[RequireMarketing])
async def cancel_campaign(
    campaign_id: uuid.UUID,
    admin: CurrentAdmin,
    campaigns: CampaignDep,
) -> dict:
    """Abandon a campaign. Unsent recipients are marked skipped."""
    try:
        await campaigns.cancel(campaign_id)
    except CampaignError as exc:
        raise _handle(exc) from exc

    logger.info("campaign_cancel_requested id=%s admin=%s", campaign_id, admin.id)
    return {"campaign_id": str(campaign_id), "status": "cancelled"}


# ---------------------------------------------------------------------- #
# Analytics
# ---------------------------------------------------------------------- #
@router.get("/{campaign_id}/analytics")
async def campaign_analytics(
    campaign_id: uuid.UUID,
    admin: CurrentAdmin,
    campaigns: CampaignDep,
) -> dict:
    """Delivery and engagement for one campaign."""
    try:
        return await campaigns.analytics(campaign_id)
    except CampaignError as exc:
        raise _handle(exc) from exc


@router.get("/{campaign_id}/recipients")
async def campaign_recipients(
    campaign_id: uuid.UUID,
    admin: CurrentAdmin,
    db: DbSession,
    status: str | None = Query(default=None),
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """Per-recipient delivery detail — who got it, who did not, and why."""
    from sqlalchemy import text

    rows = (
        await db.execute(
            text(
                """
                SELECT r.id, r.user_id, r.recipient, r.status, r.failure_reason,
                       r.sent_at, r.delivered_at, r.read_at, r.failed_at
                FROM ops.campaign_recipients r
                WHERE r.campaign_id = :cid
                  AND (CAST(:status AS TEXT) IS NULL OR r.status = CAST(:status AS TEXT))
                ORDER BY r.created_at
                LIMIT :lim OFFSET :off
                """
            ),
            {"cid": str(campaign_id), "status": status, "lim": limit, "off": offset},
        )
    ).mappings().all()

    return {"recipients": [dict(r) for r in rows], "limit": limit, "offset": offset}


@router.get("/analytics/top")
async def top_campaigns(
    admin: CurrentAdmin,
    db: DbSession,
    limit: int = Query(default=10, le=50),
) -> dict:
    """Best-performing campaigns, ranked by read rate."""
    return {"campaigns": await AnalyticsService(db).top_campaigns(limit=limit)}


@router.get("/analytics/revenue")
async def campaign_revenue(
    admin: CurrentAdmin,
    db: DbSession,
    days: int = Query(default=30, ge=1, le=365),
) -> dict:
    """Revenue attributed to campaigns.

    Last-touch attribution within the window — see the docstring on
    `AnalyticsService.campaign_revenue`. Directional, not proof of causation.
    """
    return {"campaigns": await AnalyticsService(db).campaign_revenue(days=days)}
