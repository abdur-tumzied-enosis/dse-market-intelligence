"""
Roll back a migration by removing it from _migrations so it re-runs on next migrate.

If a matching <migration>_down.sql file exists in db/migrations/, it is executed first.

Usage:
    python -m db.rollback 024
    python -m db.rollback 024_ml_unique_constraints
    python -m db.rollback 024_ml_unique_constraints.sql
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def resolve_filename(arg: str) -> str:
    """Accept prefix (024), stem (024_ml_unique_constraints), or full name."""
    arg = arg.removesuffix(".sql")
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.stem == arg or path.stem.startswith(arg + "_") or path.stem.startswith(arg):
            return path.name
    raise SystemExit(f"No migration found matching '{arg}'")


async def rollback(dsn: str, filename: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM _migrations WHERE filename = $1", filename
        )
        if not exists:
            log.warning("'%s' not in _migrations — may already be rolled back", filename)

        # Run down script if present
        down_path = MIGRATIONS_DIR / filename.replace(".sql", "_down.sql")
        if down_path.exists():
            log.info("Running down script: %s", down_path.name)
            await conn.execute(down_path.read_text(encoding="utf-8"))
        else:
            log.info("No down script found — only removing from _migrations")

        await conn.execute(
            "DELETE FROM _migrations WHERE filename = $1", filename
        )
        log.info("Rolled back: %s", filename)
    finally:
        await conn.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python -m db.rollback <migration-id>")

    url = os.environ.get("DATABASE_SYNC_URL") or os.environ.get("DATABASE_URL", "")
    if not url:
        raise SystemExit("DATABASE_URL or DATABASE_SYNC_URL not set")

    dsn = url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgresql+psycopg://", "postgresql://", 1
    )
    filename = resolve_filename(sys.argv[1])
    log.info("Rolling back: %s", filename)
    asyncio.run(rollback(dsn, filename))
