"""Redis key naming conventions.

Auth state is bundled one key per identity (see ``bundle.py``); the ``bundle_*``
helpers name those keys and the ``*_field`` helpers name the fields inside them.
"""

from __future__ import annotations


# --- Bundle keys (one per identity) ---
def bundle_email_key(email_normalized: str) -> str:
    return f"u:e:{email_normalized}"


def bundle_ip_key(ip_address: str) -> str:
    return f"u:i:{ip_address}"


def bundle_user_key(user_id: str) -> str:
    return f"u:u:{user_id}"


# --- Fields within a bundle ---
def rate_limit_field(scope: str) -> str:
    return f"rl:{scope}"


def otp_field(purpose: str) -> str:
    return f"otp:{purpose}"


PHONE_VERIFY_FIELD = "phone:verify"


# --- Standalone keys ---
def access_blacklist_key(jti: str) -> str:
    return f"blacklist:access:{jti}"
