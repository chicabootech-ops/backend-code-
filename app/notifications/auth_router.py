"""Public OTP endpoints — `POST /api/v1/auth/send-otp` and `/verify-otp`.

Codes go out over WhatsApp only. These are unauthenticated by necessity (you
cannot require a session to log in), which makes them the most abusable surface
in the application, so they carry four independent limits:

    1. Per-destination cooldown       60s between codes to one number
    2. Per-destination hourly cap     5 codes/hour to one number
    3. Per-IP hourly cap              20 codes/hour from one address
    4. Per-challenge verify attempts  5 wrong guesses, then the code dies

Limits 1, 2 and 4 live in `OtpService` against Postgres; limit 3 is enforced here
via Redis, because an IP is a property of the request rather than of the
challenge.

**Response shape is deliberately uniform.** `send-otp` returns the same body
whether or not the number belongs to a registered account, and never reveals
whether a code was actually delivered to a real handset. Varying the response —
or its timing — turns this endpoint into a way to enumerate which phone numbers
have Chic A Boo accounts.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.config import settings
from app.identity.core.validation import to_e164
from app.identity.services.rate_limit_service import RateLimitService, by_ip
from app.notifications.otp_service import OtpError
from app.notifications.whatsapp_service import OTP_PURPOSES, WhatsAppService
from app.storefront.dependencies import DbSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth-otp"])


# ---------------------------------------------------------------------- #
# Schemas
# ---------------------------------------------------------------------- #
class SendOtpRequest(BaseModel):
    phone_number: str = Field(..., min_length=6, max_length=20)
    #: LOGIN | SIGNUP | PHONE_VERIFICATION | PASSWORD_RESET
    purpose: str = Field(default="LOGIN")

    @field_validator("phone_number")
    @classmethod
    def _normalise_phone(cls, value: str) -> str:
        return to_e164(value, settings.phone_country_code)

    @field_validator("purpose")
    @classmethod
    def _known_purpose(cls, value: str) -> str:
        upper = value.upper()
        if upper not in OTP_PURPOSES:
            raise ValueError(f"purpose must be one of: {', '.join(OTP_PURPOSES)}")
        return upper


class VerifyOtpRequest(BaseModel):
    phone_number: str = Field(..., min_length=6, max_length=20)
    otp: str = Field(..., min_length=4, max_length=10)
    purpose: str = Field(default="LOGIN")

    @field_validator("phone_number")
    @classmethod
    def _normalise_phone(cls, value: str) -> str:
        return to_e164(value, settings.phone_country_code)

    @field_validator("otp")
    @classmethod
    def _digits_only(cls, value: str) -> str:
        code = value.strip()
        if not code.isdigit():
            raise ValueError("otp must be numeric")
        return code

    @field_validator("purpose")
    @classmethod
    def _known_purpose(cls, value: str) -> str:
        upper = value.upper()
        if upper not in OTP_PURPOSES:
            raise ValueError(f"purpose must be one of: {', '.join(OTP_PURPOSES)}")
        return upper


class OtpResponse(BaseModel):
    success: bool = True
    message: str
    #: Seconds until another code may be requested. Lets the UI render a real
    #: countdown instead of guessing.
    retry_after_seconds: int = settings.otp_resend_cooldown_seconds


class VerifyResponse(BaseModel):
    success: bool = True
    message: str
    verified: bool = True


# ---------------------------------------------------------------------- #
# Dependencies
# ---------------------------------------------------------------------- #
def get_whatsapp_service(request: Request, db: DbSession) -> WhatsAppService:
    """Build the service from the providers wired at startup."""
    build = getattr(request.app.state, "build_notifications", None)
    if build is None:  # pragma: no cover - misconfiguration
        raise HTTPException(status_code=503, detail="Messaging is not configured")
    return WhatsAppService(db, settings, notifications=build(db))


WhatsAppDep = Annotated[WhatsAppService, Depends(get_whatsapp_service)]


def _client_ip(request: Request) -> str:
    """The caller's address, trusting the proxy chain's first hop.

    Requests reach this app through Cloudflare → Vercel → Render, so
    `request.client.host` is a proxy and would rate-limit the entire internet as
    one client. The leftmost X-Forwarded-For entry is the original caller.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _enforce_ip_limit(request: Request) -> None:
    """Per-IP hourly cap.

    `check_rules` already fails open when Redis is unavailable — rate limiting is
    a protective layer over a path that is safe on its own, and refusing every
    OTP because the limiter is down would take login out with it. The
    per-destination caps in Postgres still apply either way.
    """
    limiter: RateLimitService | None = getattr(
        request.app.state, "rate_limit_service", None
    )
    if limiter is None:
        return

    await limiter.check_rules(
        [
            by_ip(
                "otp_send",
                _client_ip(request),
                limit=settings.rate_limit_otp_per_ip_hourly,
                window_seconds=3600,
            )
        ]
    )


# ---------------------------------------------------------------------- #
# Endpoints
# ---------------------------------------------------------------------- #
@router.post("/send-otp", response_model=OtpResponse)
async def send_otp(
    payload: SendOtpRequest,
    request: Request,
    whatsapp: WhatsAppDep,
) -> OtpResponse:
    """Issue a code and deliver it over WhatsApp."""
    await _enforce_ip_limit(request)

    client_ip = _client_ip(request)
    try:
        await whatsapp.send_otp(
            phone_number=payload.phone_number,
            purpose=payload.purpose,
            ip_address=client_ip,
        )
    except OtpError as exc:
        # Cooldown and rate-limit refusals are surfaced honestly — the customer
        # needs to know to wait. A *delivery* failure is also surfaced, because
        # silently claiming success is how a dead channel looks like a customer
        # problem for a week before anyone reads the logs.
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc

    # Note what is absent: no indication of whether this number has an account.
    return OtpResponse(message="If that number can receive WhatsApp, a code is on its way.")


@router.post("/verify-otp", response_model=VerifyResponse)
async def verify_otp(
    payload: VerifyOtpRequest,
    whatsapp: WhatsAppDep,
) -> VerifyResponse:
    """Check a submitted code.

    Verification is local — the code is matched against its Argon2 hash — so it
    keeps working during a Meta outage. Only issuing a new code needs WhatsApp.
    """
    try:
        await whatsapp.verify_otp(
            phone_number=payload.phone_number,
            otp=payload.otp,
            purpose=payload.purpose,
        )
    except OtpError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc

    return VerifyResponse(message="Phone number verified.")


@router.post("/resend-otp", response_model=OtpResponse)
async def resend_otp(
    payload: SendOtpRequest,
    request: Request,
    whatsapp: WhatsAppDep,
) -> OtpResponse:
    """Resend is `send-otp` by another name, deliberately.

    `OtpService.issue` supersedes the previous challenge, so the old code stops
    working the instant a new one goes out, and the cooldown plus hourly cap are
    enforced in one place. Giving resend its own path is how two simultaneously
    valid codes for one number happen.
    """
    return await send_otp(payload, request, whatsapp)
