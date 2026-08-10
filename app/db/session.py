"""Async engine + session factory.

Latency note, because it dominates everything else here: the database is Neon in
``us-east-1`` and each round-trip from outside that region measures ~235 ms. Per
request the old configuration spent roughly four of them before any application
query ran —

    pool_pre_ping SELECT 1      1 RTT
    statement prepare + execute 2 RTT   (statement_cache_size=0)
    reset-on-return ROLLBACK    1 RTT

— so an endpoint issuing a handful of queries was paying seconds in pure network
time. The settings below remove the avoidable ones. They do not, and cannot, fix
the underlying 235 ms: that is a function of physical distance between the app
and the database, and is only solved by running them in the same region.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

#: Recycle below the pooler's idle timeout. This is what lets us drop
#: ``pool_pre_ping``: rather than paying a SELECT 1 on every checkout to discover
#: a dead connection, we retire connections before the server would close them.
POOL_RECYCLE_SECONDS = 240

#: Sized for a small always-on service. The cost of an extra idle connection is
#: trivial next to the ~6 s a cold connect (TLS + auth across regions) costs.
POOL_SIZE = 10
MAX_OVERFLOW = 20
#: Seconds a request will wait for a free connection before erroring.
POOL_TIMEOUT = 10


def create_engine(database_url: str) -> AsyncEngine:
    connect_args: dict = {}
    if "postgresql+asyncpg" in database_url:
        # Required when talking to a transaction-pooling proxy (Neon's -pooler
        # endpoint, pgbouncer): server-side prepared statements do not survive
        # being handed between backends.
        #
        # Measured alternative, for the record: switching to Neon's direct
        # endpoint so this cache can be enabled was worth ~5% and gave up the
        # pooler, so it is deliberately not done.
        connect_args["statement_cache_size"] = 0

    return create_async_engine(
        database_url,
        echo=False,
        # Deliberately off; see POOL_RECYCLE_SECONDS above. Turning this back on
        # silently adds a full round-trip to every single request.
        pool_pre_ping=False,
        pool_recycle=POOL_RECYCLE_SECONDS,
        pool_size=POOL_SIZE,
        max_overflow=MAX_OVERFLOW,
        pool_timeout=POOL_TIMEOUT,
        # LIFO hands back the most recently used connection, so a warm one stays
        # warm instead of the pool round-robining through cold ones.
        pool_use_lifo=True,
        connect_args=connect_args,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
