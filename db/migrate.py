"""Run all SQL migrations in order. Idempotent — safe to re-run."""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import asyncpg
import structlog
from dotenv import load_dotenv

load_dotenv()
logger = structlog.get_logger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


async def run_migrations(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS _migrations (
                filename   TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

        migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        for path in migration_files:
            already_applied = await conn.fetchval(
                "SELECT 1 FROM _migrations WHERE filename = $1", path.name
            )
            if already_applied:
                logger.info("migration_skipped", file=path.name)
                continue

            sql = path.read_text(encoding="utf-8")
            logger.info("migration_applying", file=path.name)
            await conn.execute(sql)
            await conn.execute(
                "INSERT INTO _migrations (filename) VALUES ($1)", path.name
            )
            logger.info("migration_done", file=path.name)
    finally:
        await conn.close()


def _dsn_from_env() -> str:
    url = os.environ.get("DATABASE_SYNC_URL") or os.environ.get("DATABASE_URL", "")
    # asyncpg wants postgresql:// not postgresql+asyncpg://
    return url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgresql+psycopg://", "postgresql://"
    )


if __name__ == "__main__":
    dsn = _dsn_from_env()
    if not dsn:
        raise SystemExit("DATABASE_URL or DATABASE_SYNC_URL not set")
    asyncio.run(run_migrations(dsn))
    print("All migrations applied.")
