from __future__ import annotations

from fastapi import APIRouter

from app.identity.dependencies import AccountServiceDep, ClientCtx, CurrentUserId, DbSession
from app.identity.schemas.preferences import PreferencesResponse, PreferencesUpdateRequest

router = APIRouter(prefix="/api/user/me/preferences", tags=["preferences"])


@router.get("", response_model=PreferencesResponse)
async def get_preferences(
    session: DbSession,
    account: AccountServiceDep,
    user_id: CurrentUserId,
) -> PreferencesResponse:
    return await account.get_preferences(session, user_id)


@router.patch("", response_model=PreferencesResponse)
async def patch_preferences(
    session: DbSession,
    account: AccountServiceDep,
    user_id: CurrentUserId,
    body: PreferencesUpdateRequest,
    ctx: ClientCtx,
) -> PreferencesResponse:
    return await account.update_preferences(session, user_id, body, ctx)
