"""Deterministic fingerprints for lookup keys (not password storage)."""

from __future__ import annotations

import hashlib


def fingerprint_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
