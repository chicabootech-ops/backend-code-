from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials

from app.identity.dependencies import (
    AuthServiceDep,
    ClientCtx,
    DbSession,
    OptionalUserId,
    bearer_scheme,
)
from app.identity.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    ResendVerificationRequest,
    ResetPasswordRequest,
    TokenResponse,
    VerifyEmailRequest,
)
from app.identity.schemas.common import MessageResponse

router = APIRouter(prefix="/api/user/auth", tags=["auth"])

REFRESH_COOKIE = "refresh_token"


def _set_refresh_cookie(response: Response, refresh_token: str, max_age: int) -> None:
    from app.config import settings

    response.set_cookie(
        key=REFRESH_COOKIE,
        value=refresh_token,
        httponly=True,
        secure=settings.app_env != "development",
        samesite="lax",
        max_age=max_age,
        path="/api/user/auth",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(key=REFRESH_COOKIE, path="/api/user/auth")


@router.post("/register", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    session: DbSession,
    auth: AuthServiceDep,
    ctx: ClientCtx,
) -> MessageResponse:
    return await auth.register(session, body, ctx)


@router.post("/verify-email", response_model=MessageResponse)
async def verify_email(
    body: VerifyEmailRequest,
    session: DbSession,
    auth: AuthServiceDep,
    ctx: ClientCtx,
) -> MessageResponse:
    return await auth.verify_email(session, body, ctx)


@router.post("/resend-verification", response_model=MessageResponse)
async def resend_verification(
    body: ResendVerificationRequest,
    session: DbSession,
    auth: AuthServiceDep,
    ctx: ClientCtx,
) -> MessageResponse:
    return await auth.resend_verification(session, body, ctx)


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    response: Response,
    session: DbSession,
    auth: AuthServiceDep,
    ctx: ClientCtx,
) -> TokenResponse:
    tokens = await auth.login(session, body, ctx)
    _set_refresh_cookie(response, tokens.refresh_token, max_age=604800)
    return tokens


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshRequest,
    request: Request,
    response: Response,
    session: DbSession,
    auth: AuthServiceDep,
    ctx: ClientCtx,
) -> TokenResponse:
    refresh_token = body.refresh_token or request.cookies.get(REFRESH_COOKIE)
    if not refresh_token:
        from app.identity.core.exceptions import UnauthorizedError

        raise UnauthorizedError("Refresh token required", code="missing_refresh_token")
    tokens = await auth.refresh(session, refresh_token, ctx)
    _set_refresh_cookie(response, tokens.refresh_token, max_age=604800)
    return tokens


@router.post("/logout", response_model=MessageResponse)
async def logout(
    body: LogoutRequest,
    request: Request,
    response: Response,
    session: DbSession,
    auth: AuthServiceDep,
    ctx: ClientCtx,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    user_id: OptionalUserId = None,
) -> MessageResponse:
    access_token = credentials.credentials if credentials else None
    refresh_token = body.refresh_token or request.cookies.get(REFRESH_COOKIE)
    result = await auth.logout(
        session,
        access_token=access_token,
        refresh_token=refresh_token,
        ctx=ctx,
        user_id=user_id,
    )
    _clear_refresh_cookie(response)
    return result


@router.post("/forgot-password", response_model=MessageResponse)
async def forgot_password(
    body: ForgotPasswordRequest,
    session: DbSession,
    auth: AuthServiceDep,
    ctx: ClientCtx,
) -> MessageResponse:
    return await auth.forgot_password(session, body, ctx)


@router.post("/reset-password", response_model=MessageResponse)
async def reset_password(
    body: ResetPasswordRequest,
    session: DbSession,
    auth: AuthServiceDep,
    ctx: ClientCtx,
) -> MessageResponse:
    return await auth.reset_password(session, body, ctx)
