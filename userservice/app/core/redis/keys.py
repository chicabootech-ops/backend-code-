"""Redis key naming conventions."""

from __future__ import annotations


def otp_key(purpose: str, email_normalized: str) -> str:
    return f"otp:{purpose}:{email_normalized}"


def refresh_session_key(jti: str) -> str:
    return f"refresh:session:{jti}"


def access_blacklist_key(jti: str) -> str:
    return f"blacklist:access:{jti}"


def rate_limit_key(scope: str, identifier: str) -> str:
    return f"ratelimit:{scope}:{identifier}"
