"""Argon2id password hashing."""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(plain: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, plain)
    except VerifyMismatchError:
        return False


def hash_otp(otp: str) -> str:
    """Hash a short OTP/token with Argon2id."""
    return _hasher.hash(otp)


def verify_otp(plain: str, otp_hash: str) -> bool:
    return verify_password(plain, otp_hash)
