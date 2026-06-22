"""FastAPI dependencies."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import UnauthorizedError
from app.core.security.jwt import JWTManager
from app.db.session import get_session
from app.schemas.common import ClientContext
from app.services.account_service import AccountService
from app.services.auth_service import AuthService
from app.services.avatar_service import AvatarService

bearer_scheme = HTTPBearer(auto_error=False)


def get_request_context(
    request: Request,
    x_request_id: Annotated[str | None, Header()] = None,
    x_device_name: Annotated[str | None, Header()] = None,
    x_device_type: Annotated[str | None, Header()] = "unknown",
) -> ClientContext:
    forwarded = request.headers.get("x-forwarded-for")
    ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else None)
    return ClientContext(
        ip_address=ip,
        user_agent=request.headers.get("user-agent"),
        request_id=x_request_id or request.headers.get("x-request-id"),
        device_name=x_device_name,
        device_type=x_device_type or "unknown",
    )


async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    session_factory = request.app.state.session_factory
    async for session in get_session(session_factory):
        yield session


def get_jwt_manager(request: Request) -> JWTManager:
    return request.app.state.jwt_manager


def get_auth_service(request: Request) -> AuthService:
    return request.app.state.auth_service


def get_account_service(request: Request) -> AccountService:
    return request.app.state.account_service


def get_avatar_service(request: Request) -> AvatarService:
    return request.app.state.avatar_service


async def get_current_user_id(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    jwt_manager: Annotated[JWTManager, Depends(get_jwt_manager)],
) -> uuid.UUID:
    if not credentials:
        raise UnauthorizedError("Missing authorization header", code="missing_token")

    payload = jwt_manager.decode_token(credentials.credentials, expected_type="access")
    redis = request.app.state.redis_client
    if await redis.is_access_token_blacklisted(payload.jti):
        raise UnauthorizedError("Token has been revoked", code="token_revoked")

    return uuid.UUID(payload.sub)


async def get_optional_user_id(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    jwt_manager: Annotated[JWTManager, Depends(get_jwt_manager)],
) -> uuid.UUID | None:
    if not credentials:
        return None
    try:
        return await get_current_user_id(request, credentials, jwt_manager)
    except UnauthorizedError:
        return None


CurrentUserId = Annotated[uuid.UUID, Depends(get_current_user_id)]
OptionalUserId = Annotated[uuid.UUID | None, Depends(get_optional_user_id)]
DbSession = Annotated[AsyncSession, Depends(get_db)]
AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
AccountServiceDep = Annotated[AccountService, Depends(get_account_service)]
AvatarServiceDep = Annotated[AvatarService, Depends(get_avatar_service)]
ClientCtx = Annotated[ClientContext, Depends(get_request_context)]
