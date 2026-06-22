"""RS256 JWT utilities."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.core.exceptions import UnauthorizedError


@dataclass(frozen=True)
class TokenPayload:
    sub: str
    jti: str
    token_type: str
    exp: int
    iat: int


class JWTManager:
    def __init__(
        self,
        private_key_pem: str,
        public_key_pem: str,
        *,
        access_ttl_seconds: int = 900,
        issuer: str = "chicaboo-userservice",
        audience: str = "chicaboo",
    ) -> None:
        if not private_key_pem or not public_key_pem:
            raise ValueError("JWT private and public keys are required")
        self._private_key = private_key_pem
        self._public_key = public_key_pem
        self._access_ttl = access_ttl_seconds
        self._issuer = issuer
        self._audience = audience

    def create_access_token(self, user_id: str) -> tuple[str, str, int]:
        jti = str(uuid.uuid4())
        now = datetime.now(UTC)
        exp = now + timedelta(seconds=self._access_ttl)
        payload = {
            "sub": user_id,
            "jti": jti,
            "type": "access",
            "iss": self._issuer,
            "aud": self._audience,
            "iat": int(now.timestamp()),
            "exp": int(exp.timestamp()),
        }
        token = jwt.encode(payload, self._private_key, algorithm="RS256")
        return token, jti, self._access_ttl

    def decode_token(self, token: str, *, expected_type: str | None = None) -> TokenPayload:
        try:
            data: dict[str, Any] = jwt.decode(
                token,
                self._public_key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
            )
        except jwt.PyJWTError as exc:
            raise UnauthorizedError("Invalid or expired token", code="invalid_token") from exc

        token_type = data.get("type", "access")
        if expected_type and token_type != expected_type:
            raise UnauthorizedError("Invalid token type", code="invalid_token_type")

        return TokenPayload(
            sub=str(data["sub"]),
            jti=str(data["jti"]),
            token_type=token_type,
            exp=int(data["exp"]),
            iat=int(data["iat"]),
        )
