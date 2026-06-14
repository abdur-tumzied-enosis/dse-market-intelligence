"""asyncpg connection pool — shared singleton for all extraction code."""
from __future__ import annotations

import os

import asyncpg

_pool: asyncpg.Pool | None = None
_batch_pool: asyncpg.Pool | None = None


def _dsn() -> str:
    url = os.environ.get("DATABASE_URL", "")
    return (
        url.replace("postgresql+asyncpg://", "postgresql://")
           .replace("postgresql+psycopg://", "postgresql://")
    )


def _batch_dsn() -> str:
    """Direct-to-Postgres DSN (bypasses pgBouncer) for heavy offline jobs.

    DATABASE_SYNC_URL points straight at the db host (db:5432), not the
    pgBouncer transaction pool. Multi-subquery batch queries (ML training /
    scoring) hang on the transaction pool but run in milliseconds direct.
    Falls back to the normal DSN if DATABASE_SYNC_URL is unset.
    """
    url = os.environ.get("DATABASE_SYNC_URL", "")
    if not url:
        return _dsn()
    return (
        url.replace("postgresql+psycopg://", "postgresql://")
           .replace("postgresql+asyncpg://", "postgresql://")
    )


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            _dsn(),
            min_size=2,
            max_size=10,
            command_timeout=30,
            statement_cache_size=0,  # pgBouncer transaction mode breaks prepared statements
        )
    return _pool


async def get_batch_pool() -> asyncpg.Pool:
    """Pool for offline ML batch jobs: direct DB connection, long timeout.

    Bypasses pgBouncer (transaction pooling stalls the heavy multi-subquery
    training/scoring queries) and allows prepared-statement caching since this
    is a direct session connection.
    """
    global _batch_pool
    if _batch_pool is None:
        _batch_pool = await asyncpg.create_pool(
            _batch_dsn(),
            min_size=1,
            max_size=4,
            command_timeout=600,
        )
    return _batch_pool


async def close_pool() -> None:
    global _pool, _batch_pool
    if _pool:
        await _pool.close()
        _pool = None
    if _batch_pool:
        await _batch_pool.close()
        _batch_pool = None
