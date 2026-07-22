from __future__ import annotations

from fastapi import APIRouter

from app.identity.dependencies import AvatarServiceDep, CurrentUserId, DbSession
from app.identity.schemas.avatar import (
    AvatarConfirmRequest,
    AvatarConfirmResponse,
    AvatarUploadUrlRequest,
    AvatarUploadUrlResponse,
    AvatarUrlResponse,
)
from app.identity.schemas.common import MessageResponse

router = APIRouter(prefix="/api/user/me/avatar", tags=["avatar"])


@router.post(
    "/upload-url",
    response_model=AvatarUploadUrlResponse,
    summary="Get presigned R2 upload URL",
    description="Returns a 15-minute presigned PUT URL for `avatars/{user_id}.webp`. Max 5MB.",
)
async def create_upload_url(
    session: DbSession,
    avatar: AvatarServiceDep,
    user_id: CurrentUserId,
    body: AvatarUploadUrlRequest,
) -> AvatarUploadUrlResponse:
    return await avatar.create_upload_url(session, user_id, body)


@router.post(
    "/confirm",
    response_model=AvatarConfirmResponse,
    summary="Confirm avatar upload",
    description="Verifies the object exists in R2 and stores the key in the user profile.",
)
async def confirm_upload(
    session: DbSession,
    avatar: AvatarServiceDep,
    user_id: CurrentUserId,
    body: AvatarConfirmRequest,
) -> AvatarConfirmResponse:
    return await avatar.confirm_upload(session, user_id, body)


@router.get(
    "/url",
    response_model=AvatarUrlResponse,
    summary="Get presigned avatar URL",
    description="Returns a 1-hour presigned GET URL for the stored avatar key.",
)
async def get_avatar_url(
    session: DbSession,
    avatar: AvatarServiceDep,
    user_id: CurrentUserId,
) -> AvatarUrlResponse:
    return await avatar.get_avatar_url(session, user_id)


@router.delete(
    "",
    response_model=MessageResponse,
    summary="Delete avatar",
    description="Removes the avatar from R2 and clears the stored key.",
)
async def delete_avatar(
    session: DbSession,
    avatar: AvatarServiceDep,
    user_id: CurrentUserId,
) -> MessageResponse:
    return await avatar.delete_avatar(session, user_id)
