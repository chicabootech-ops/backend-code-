from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request, Response

from app.admin_api.core.security.permissions import UserWriter
from app.admin_api.dependencies import CurrentAdmin, SubscriberAdminServiceDep
from app.admin_api.schemas.subscriber import (
    SubscriberListResponse,
    SubscriberOut,
    SubscriberStats,
)

router = APIRouter(prefix="/admin/subscribers", tags=["admin-subscribers"])


def _ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


@router.get("/stats", response_model=SubscriberStats)
async def stats(service: SubscriberAdminServiceDep, admin: CurrentAdmin) -> SubscriberStats:
    return await service.stats()


@router.get("", response_model=SubscriberListResponse)
async def list_subscribers(
    service: SubscriberAdminServiceDep,
    admin: CurrentAdmin,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    status: str | None = Query(None, pattern="^(pending|confirmed|unsubscribed)$"),
    search: str | None = Query(None, max_length=200),
    sort: str = Query("created_at"),
    direction: str = Query("desc", pattern="^(asc|desc)$"),
) -> SubscriberListResponse:
    return await service.list_subscribers(
        page=page,
        page_size=page_size,
        status=status,
        search=search,
        sort=sort,
        direction=direction,
    )


@router.get("/export")
async def export_csv(
    service: SubscriberAdminServiceDep,
    admin: CurrentAdmin,
    status: str | None = Query("confirmed", pattern="^(pending|confirmed|unsubscribed|all)$"),
) -> Response:
    csv_text = await service.export_csv(status=None if status == "all" else status)
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="subscribers.csv"'},
    )


@router.post("/{subscriber_id}/unsubscribe", response_model=SubscriberOut)
async def unsubscribe(
    subscriber_id: uuid.UUID,
    service: SubscriberAdminServiceDep,
    admin: UserWriter,
    request: Request,
) -> SubscriberOut:
    """Opt a subscriber out on their behalf.

    Gated on USER_WRITE rather than left open: this changes what a person
    receives, so a read-only support role must not be able to do it.
    """
    return await service.unsubscribe(subscriber_id, admin_id=admin.sub, ip_address=_ip(request))
