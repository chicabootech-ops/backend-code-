from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.admin_api.core.security.permissions import UserWriter
from app.admin_api.dependencies import CampaignAdminServiceDep, CurrentAdmin
from app.admin_api.schemas.campaign import (
    AudienceOut,
    CampaignOut,
    CampaignSendRequest,
    CampaignSendResult,
    CampaignTestRequest,
)

router = APIRouter(prefix="/admin/campaigns", tags=["admin-campaigns"])


def _ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


@router.get("/audience", response_model=AudienceOut)
async def audience(service: CampaignAdminServiceDep, admin: CurrentAdmin) -> AudienceOut:
    return AudienceOut(confirmed=await service.audience_size())


@router.get("", response_model=list[CampaignOut])
async def list_campaigns(
    service: CampaignAdminServiceDep,
    admin: CurrentAdmin,
    limit: int = Query(50, ge=1, le=200),
) -> list[CampaignOut]:
    return await service.list_campaigns(limit=limit)


@router.post("/test", status_code=204)
async def send_test(
    payload: CampaignTestRequest,
    service: CampaignAdminServiceDep,
    admin: UserWriter,
) -> None:
    """Send one copy to a chosen address before committing to the list."""
    await service.send_test(
        subject=payload.subject, body_html=payload.body_html, to_email=payload.to_email
    )


@router.post("/send", response_model=CampaignSendResult)
async def send_campaign(
    payload: CampaignSendRequest,
    service: CampaignAdminServiceDep,
    admin: UserWriter,
    request: Request,
) -> CampaignSendResult:
    """Send to every confirmed subscriber. Not reversible once started."""
    return await service.create_and_send(
        name=payload.name,
        subject=payload.subject,
        body_html=payload.body_html,
        admin_id=admin.sub,
        ip_address=_ip(request),
    )
