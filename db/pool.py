"""asyncpg connection pool — shared singleton for all extraction code."""
from __future__ import annotations

import os

import asyncpg

_pool: asyncpg.Pool | None = None


def _dsn() -> str:
    url = os.environ.get("DATABASE_URL", "")
    return (
        url.replace("postgresql+asyncpg://", "postgresql://")
           .replace("postgresql+psycopg://", "postgresql://")
    )


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            _dsn(),
            min_size=1,
            max_size=5,
            command_timeout=30,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
