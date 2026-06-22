from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from app.dependencies import AccountServiceDep, ClientCtx, CurrentUserId, DbSession
from app.schemas.common import MessageResponse
from app.schemas.security import DeviceListResponse, LoginHistoryListResponse

router = APIRouter(prefix="/api/user/me/security", tags=["security"])


@router.get("/devices", response_model=DeviceListResponse)
async def list_devices(
    session: DbSession,
    account: AccountServiceDep,
    user_id: CurrentUserId,
    ctx: ClientCtx,
) -> DeviceListResponse:
    return await account.list_devices(session, user_id, current_user_agent=ctx.user_agent)


@router.get("/logins", response_model=LoginHistoryListResponse)
async def list_logins(
    session: DbSession,
    account: AccountServiceDep,
    user_id: CurrentUserId,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> LoginHistoryListResponse:
    return await account.list_login_history(session, user_id, limit=limit, offset=offset)


@router.post("/devices/{device_id}/revoke", response_model=MessageResponse)
async def revoke_device(
    session: DbSession,
    account: AccountServiceDep,
    user_id: CurrentUserId,
    device_id: uuid.UUID,
    ctx: ClientCtx,
) -> MessageResponse:
    return await account.revoke_device(session, user_id, device_id, ctx)


@router.post("/logout-all", response_model=MessageResponse)
async def logout_all(
    session: DbSession,
    account: AccountServiceDep,
    user_id: CurrentUserId,
    ctx: ClientCtx,
) -> MessageResponse:
    return await account.logout_all(session, user_id, ctx)
