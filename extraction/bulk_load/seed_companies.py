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


def _market_cap_bdt(item: dict) -> float | None:
    """Market cap in BDT = last price × shares outstanding.

    Computed from Close (fallback LTP) × TotalSecurities rather than AmarStock's
    own `MarketCap` field, which is inconsistent (e.g. BRACBANK/SQURPHARMA report
    4–5× the price×shares value). Returns None when price or shares are missing.
    """
    shares = item.get("TotalSecurities")
    price = item.get("Close") or item.get("LTP")
    try:
        shares_f = float(shares)
        price_f = float(price)
    except (TypeError, ValueError):
        return None
    if shares_f > 0 and price_f > 0:
        return round(shares_f * price_f, 2)
    return None


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
            "ticker":         ticker,
            "name":           str(item.get("FullName") or ticker),
            "sector":         str(item.get("BusinessSegment") or "Unknown"),
            "category":       str(item.get("MarketCategory") or ""),
            "market_cap_bdt": _market_cap_bdt(item),
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
                INSERT INTO companies (ticker, name, sector, category, market_cap_bdt, is_active, updated_at)
                VALUES ($1, $2, $3, $4, $5, true, NOW())
                ON CONFLICT (ticker) DO UPDATE
                    SET name           = EXCLUDED.name,
                        sector         = EXCLUDED.sector,
                        category       = EXCLUDED.category,
                        market_cap_bdt = COALESCE(EXCLUDED.market_cap_bdt, companies.market_cap_bdt),
                        is_active      = true,
                        updated_at     = NOW()
                """,
                c["ticker"], c["name"], c["sector"], c["category"], c["market_cap_bdt"],
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
