from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt

from app.admin_api.core.exceptions import UnauthorizedError


@dataclass(frozen=True)
class AdminTokenPayload:
    sub: uuid.UUID
    email: str
    role: str
    jti: str


class AdminJWTManager:
    def __init__(self, secret: str, ttl_seconds: int) -> None:
        self._secret = secret
        self._ttl_seconds = ttl_seconds

    def create_access_token(self, admin_id: uuid.UUID, email: str, role: str) -> str:
        now = datetime.now(timezone.utc)
        jti = str(uuid.uuid4())
        payload = {
            "sub": str(admin_id),
            "email": email,
            "role": role,
            "type": "admin_access",
            "jti": jti,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=self._ttl_seconds)).timestamp()),
        }
        return jwt.encode(payload, self._secret, algorithm="HS256")

    def decode_token(self, token: str) -> AdminTokenPayload:
        try:
            payload = jwt.decode(token, self._secret, algorithms=["HS256"])
        except jwt.PyJWTError as exc:
            raise UnauthorizedError("Invalid admin token", code="invalid_token") from exc

        if payload.get("type") != "admin_access":
            raise UnauthorizedError("Invalid token type", code="invalid_token")

        return AdminTokenPayload(
            sub=uuid.UUID(payload["sub"]),
            email=payload["email"],
            role=payload.get("role", "admin"),
            jti=payload.get("jti", ""),
        )
