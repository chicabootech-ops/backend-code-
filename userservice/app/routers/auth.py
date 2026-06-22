from fastapi import APIRouter

router = APIRouter(prefix="/api/user/auth", tags=["auth"])


@router.post("/register")
async def register():
    """Email + password signup."""
    return {"message": "not implemented"}


@router.post("/login")
async def login():
    """Validate credentials, return JWT + refresh token."""
    return {"message": "not implemented"}


@router.post("/refresh")
async def refresh():
    """Validate refresh token, issue new JWT."""
    return {"message": "not implemented"}


@router.post("/logout")
async def logout():
    """Invalidate refresh token."""
    return {"message": "not implemented"}


@router.post("/forgot-password")
async def forgot_password():
    """Send reset email with time-limited token."""
    return {"message": "not implemented"}


@router.post("/reset-password")
async def reset_password():
    """Validate token, update password hash."""
    return {"message": "not implemented"}


@router.post("/verify-email")
async def verify_email():
    """Confirm email OTP/link."""
    return {"message": "not implemented"}
