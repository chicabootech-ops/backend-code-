from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError

_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16)

#: Argon2 hash of a value nobody can log in with. Verified against when the
#: email doesn't exist so a miss costs the same wall-clock time as a wrong
#: password — otherwise response timing leaks which admin emails are real.
_DUMMY_HASH = _hasher.hash("chicaboo-nonexistent-account-placeholder")


def verify_password(plain: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, plain)
    except (VerifyMismatchError, VerificationError):
        return False


def waste_verification_time() -> None:
    """Burn one Argon2 verification so unknown-email logins aren't faster."""
    verify_password("chicaboo-nonexistent-account-placeholder-x", _DUMMY_HASH)
