"""Async database engine and session factory.

Thin re-export of :mod:`app.db.session`. The engine tuning lives in one place —
these used to be four copies of the same settings, which is how the whole app
ended up running with ``pool_pre_ping`` (a full round-trip per request) on.
"""

from __future__ import annotations

from app.db.session import create_engine, create_session_factory, get_session

__all__ = ["create_engine", "create_session_factory", "get_session"]
