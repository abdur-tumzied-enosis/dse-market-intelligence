"""
Seed companies table from AmarStock live prices endpoint.

Single API call returns 426+ stocks with ticker, name, sector, category.
Upserts on conflict — safe to re-run.

Usage:
    python -m extraction.bulk_load.seed_companies
"""
from __future__ import annotations

import asyncio
import os

import asyncpg
import httpx
import structlog
from dotenv import load_dotenv

from extraction.normalizers import normalize_ticker

load_dotenv()
logger = structlog.get_logger(__name__)

LATEST_PRICE_URL = "https://www.amarstock.com/LatestPrice/dbfd2587c77f"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; DSEIntelBot/1.0)"}


def _dsn() -> str:
    url = os.environ.get("DATABASE_SYNC_URL", "") or os.environ.get("DATABASE_URL", "")
    return (
        url
        .replace("postgresql+asyncpg://", "postgresql://")
        .replace("postgresql+psycopg://", "postgresql://")
    )


async def fetch_company_list() -> list[dict]:
    async with httpx.AsyncClient(timeout=30, headers=HEADERS, follow_redirects=True) as client:
        resp = await client.get(LATEST_PRICE_URL)
        resp.raise_for_status()
        raw: list[dict] = resp.json()

    companies = []
    for item in raw:
        ticker = normalize_ticker(str(item.get("Scrip", "")))
        if not ticker:
            continue
        companies.append({
            "ticker":   ticker,
            "name":     str(item.get("FullName") or ticker),
            "sector":   str(item.get("BusinessSegment") or "Unknown"),
            "category": str(item.get("MarketCategory") or ""),
        })
    return companies


async def seed(dsn: str) -> int:
    """Upsert companies into DB. Returns number of rows processed."""
    logger.info("seed_companies_start", url=LATEST_PRICE_URL)
    companies = await fetch_company_list()
    logger.info("seed_companies_fetched", count=len(companies))

    conn = await asyncpg.connect(dsn)
    try:
        upserted = 0
        for c in companies:
            await conn.execute(
                """
                INSERT INTO companies (ticker, name, sector, category, is_active, updated_at)
                VALUES ($1, $2, $3, $4, true, NOW())
                ON CONFLICT (ticker) DO UPDATE
                    SET name       = EXCLUDED.name,
                        sector     = EXCLUDED.sector,
                        category   = EXCLUDED.category,
                        is_active  = true,
                        updated_at = NOW()
                """,
                c["ticker"], c["name"], c["sector"], c["category"],
            )
            upserted += 1
        logger.info("seed_companies_done", upserted=upserted)
        return upserted
    finally:
        await conn.close()


if __name__ == "__main__":
    dsn = _dsn()
    if not dsn:
        raise SystemExit("DATABASE_URL or DATABASE_SYNC_URL not set")
    count = asyncio.run(seed(dsn))
    print(f"Seeded {count} companies.")
