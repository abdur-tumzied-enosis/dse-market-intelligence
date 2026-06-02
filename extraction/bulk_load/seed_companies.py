"""
Seed the companies roster from the DSE official company listing.

Source: https://www.dsebd.org/company_listing.php — the authoritative DSE
listing. The page carries only the trading code per row (no name, sector,
category, or market cap), so this seed registers the *roster* only: each new
ticker is inserted as a bare placeholder row (name=ticker, sector='Unknown')
with enriched_at left NULL. The enrichment pass
(extraction/bulk_load/enrich_companies.py) then fills name/sector/category/
market_cap from displayCompany.php for every unenriched row.

Existing rows are never clobbered — a re-run only (re)activates listed tickers
and bumps updated_at. enriched_at is preserved, so already-enriched companies
stay out of the enrichment queue.

Usage:
    python -m extraction.bulk_load.seed_companies
"""
from __future__ import annotations

import asyncio
import os
import re

import asyncpg
import httpx
import structlog
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from extraction.normalizers import normalize_ticker

load_dotenv()
logger = structlog.get_logger(__name__)

LISTING_URL = "https://www.dsebd.org/company_listing.php"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.dsebd.org/",
}

# displayCompany.php?name=<TICKER> — the only reliable carrier of the trading
# code on the listing page (the visible link text mashes code + price together).
_NAME_RE = re.compile(r"displayCompany\.php\?name=([^&\"'#]+)", re.IGNORECASE)

# Treasury bonds use the regular code TB<tenor>Y<maturity>, e.g. TB10Y0127.
# The listing carries 223 of these plus a handful of corporate bonds/debentures
# (codes ending in BOND) and one sukuk — debt instruments, not equities. The
# roster tracks tradeable companies + mutual funds only, so these are dropped.
_TREASURY_RE = re.compile(r"^TB\d+Y\d+$")


def _is_debt_instrument(ticker: str) -> bool:
    """True for treasury bonds, corporate bonds/debentures, and sukuk."""
    return (
        bool(_TREASURY_RE.match(ticker))
        or ticker.endswith("BOND")
        or ticker.endswith("SUKUK")
    )


def _dsn() -> str:
    url = os.environ.get("DATABASE_SYNC_URL", "") or os.environ.get("DATABASE_URL", "")
    return (
        url
        .replace("postgresql+asyncpg://", "postgresql://")
        .replace("postgresql+psycopg://", "postgresql://")
    )


def parse_tickers(html: str) -> list[str]:
    """Extract the deduplicated, sorted ticker roster from listing-page HTML.

    The page links each company via displayCompany.php?name=<TICKER> and repeats
    links across its scrolling + A–Z sections, so the same ticker appears many
    times — dedup via a set. Debt instruments (treasury/corporate bonds, sukuk)
    are excluded so the roster holds only equities + mutual funds.
    """
    soup = BeautifulSoup(html, "lxml")
    tickers: set[str] = set()
    for a in soup.find_all("a", href=True):
        m = _NAME_RE.search(a["href"])
        if not m:
            continue
        ticker = normalize_ticker(m.group(1))
        if ticker and not _is_debt_instrument(ticker):
            tickers.add(ticker)
    return sorted(tickers)


async def fetch_tickers() -> list[str]:
    async with httpx.AsyncClient(timeout=30, headers=HEADERS, follow_redirects=True) as client:
        resp = await client.get(LISTING_URL)
        resp.raise_for_status()
    return parse_tickers(resp.text)


async def seed(dsn: str) -> dict[str, int]:
    """Upsert the DSE roster. Returns {total, new, existing}.

    New tickers land as placeholders (enriched_at NULL) for the enrichment pass.
    Existing rows keep their name/sector/category/market_cap/enriched_at and are
    only reactivated + timestamp-bumped.
    """
    logger.info("seed_companies_start", url=LISTING_URL)
    tickers = await fetch_tickers()
    logger.info("seed_companies_fetched", count=len(tickers))

    conn = await asyncpg.connect(dsn)
    try:
        new = 0
        for ticker in tickers:
            # xmax = 0 on the returned row ⇒ this was an INSERT (new ticker);
            # non-zero ⇒ the row already existed and we only updated it.
            inserted = await conn.fetchval(
                """
                INSERT INTO companies (ticker, name, sector, is_active, enriched_at, updated_at)
                VALUES ($1, $1, 'Unknown', true, NULL, NOW())
                ON CONFLICT (ticker) DO UPDATE
                    SET is_active  = true,
                        updated_at = NOW()
                RETURNING (xmax = 0)
                """,
                ticker,
            )
            new += bool(inserted)
        existing = len(tickers) - new
        logger.info("seed_companies_done", total=len(tickers), new=new, existing=existing)
        return {"total": len(tickers), "new": new, "existing": existing}
    finally:
        await conn.close()


if __name__ == "__main__":
    dsn = _dsn()
    if not dsn:
        raise SystemExit("DATABASE_URL or DATABASE_SYNC_URL not set")
    result = asyncio.run(seed(dsn))
    print(
        f"Seeded {result['total']} tickers "
        f"({result['new']} new → pending enrichment, {result['existing']} existing)."
    )
