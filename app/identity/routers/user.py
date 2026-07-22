from __future__ import annotations

from fastapi import APIRouter

from app.identity.dependencies import AccountServiceDep, CurrentUserId, DbSession
from app.identity.schemas.onboarding import OnboardingResponse
from app.identity.schemas.user import CurrentUserResponse, ProfileUpdateRequest

router = APIRouter(prefix="/api/user", tags=["user"])


@router.get("/me", response_model=CurrentUserResponse)
async def get_me(
    session: DbSession,
    account: AccountServiceDep,
    user_id: CurrentUserId,
) -> CurrentUserResponse:
    return await account.get_me(session, user_id)


@router.patch("/me", response_model=CurrentUserResponse)
async def patch_me(
    session: DbSession,
    account: AccountServiceDep,
    user_id: CurrentUserId,
    body: ProfileUpdateRequest,
) -> CurrentUserResponse:
    return await account.update_me(session, user_id, body)


@router.get("/me/onboarding", response_model=OnboardingResponse)
async def get_onboarding(
    session: DbSession,
    account: AccountServiceDep,
    user_id: CurrentUserId,
) -> OnboardingResponse:
    return await account.get_onboarding(session, user_id)
