from __future__ import annotations

from fastapi import APIRouter

from app.dependencies import AuthServiceDep
from app.schemas.auth import ValidateTokenRequest, ValidateTokenResponse

router = APIRouter(prefix="/internal", tags=["internal"])


@router.post("/validate-token", response_model=ValidateTokenResponse)
async def validate_token(
    body: ValidateTokenRequest,
    auth: AuthServiceDep,
) -> ValidateTokenResponse:
    try:
        data = await auth.validate_access_token(body.token)
        return ValidateTokenResponse(
            valid=True,
            user_id=data["user_id"],
            expires_at=data["expires_at"],
        )
    except Exception:
        return ValidateTokenResponse(valid=False)
