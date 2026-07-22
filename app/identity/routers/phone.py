from __future__ import annotations

from fastapi import APIRouter, Request

from app.identity.dependencies import CurrentUserId, DbSession
from app.identity.schemas.common import MessageResponse
from app.identity.schemas.phone import SendPhoneOtpRequest, VerifyPhoneOtpRequest
from app.identity.services.phone_service import PhoneService

router = APIRouter(prefix="/api/user/phone", tags=["phone"])


def _phone_service(request: Request) -> PhoneService:
    return request.app.state.phone_service


@router.post("/send-otp", response_model=MessageResponse)
async def send_phone_otp(
    request: Request,
    session: DbSession,
    user_id: CurrentUserId,
    body: SendPhoneOtpRequest | None = None,
) -> MessageResponse:
    phone = body.phone if body else None
    return await _phone_service(request).send_otp(session, user_id, phone=phone)


@router.post("/verify", response_model=MessageResponse)
async def verify_phone_otp(
    request: Request,
    session: DbSession,
    user_id: CurrentUserId,
    body: VerifyPhoneOtpRequest,
) -> MessageResponse:
    return await _phone_service(request).verify_otp(session, user_id, otp=body.otp)
