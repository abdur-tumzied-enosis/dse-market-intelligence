"""
Enrich the companies roster from DSE displayCompany.php.

seed_companies.py registers new tickers as bare placeholders (name=ticker,
sector='Unknown', enriched_at NULL). This pass fills the gaps: for every row
with enriched_at IS NULL it scrapes displayCompany.php (via the existing
DSEDirectCompanyInfoAdapter) and writes name, sector, category, market cap,
then stamps enriched_at so the row drops out of the queue.

COALESCE on each field means a partial parse never wipes existing data; the row
is still marked enriched so a single unparseable company can't wedge the queue.

Usage:
    python -m extraction.bulk_load.enrich_companies            # all unenriched
    python -m extraction.bulk_load.enrich_companies BRACBANK GP  # specific tickers

Rate: 3 concurrent, 1.5s delay (mirrors fundamentals_historical_loader).
"""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from db.pool import get_pool
from extraction.adapters.dse_direct.company_info import DSEDirectCompanyInfoAdapter
from extraction.base import AdapterError

logger = logging.getLogger(__name__)

_CONCURRENCY = 3
_DELAY_S = 1.5


def _to_decimal(value: Any) -> Decimal | None:
    """Coerce a parsed numeric (Decimal / float / NaN / None) to Decimal or None."""
    if value is None:
        return None
    try:
        d = Decimal(str(value))
    except (ValueError, ArithmeticError):
        return None
    return d if d.is_finite() else None


def _market_cap_bdt(row: Any) -> Decimal | None:
    """displayCompany reports Market Capitalization in millions of BDT."""
    cap_mn = _to_decimal(row.get("market_cap_mn"))
    return cap_mn * 1_000_000 if cap_mn is not None else None


def _listing_date(row: Any) -> date | None:
    """displayCompany gives only a listing *year*; store it as Jan 1 of that year.
    pandas may surface the year as int / numpy int / float, so coerce defensively."""
    year = row.get("listing_year")
    if year is None:
        return None
    try:
        y = int(year)
    except (TypeError, ValueError):
        return None
    return date(y, 1, 1) if 1900 <= y <= 2100 else None


async def _enrich_one(pool, ticker: str, adapter: DSEDirectCompanyInfoAdapter) -> dict:
    try:
        result = await adapter.fetch(ticker=ticker)
    except AdapterError as exc:
        logger.warning("enrich_companies_failed ticker=%s error=%s", ticker, exc)
        return {"ticker": ticker, "status": "failed"}

    row = result.data.iloc[0]
    name = row.get("full_name") or None
    sector = row.get("sector") or None
    category = row.get("market_category") or None

    await pool.execute(
        """
        UPDATE companies SET
            name           = COALESCE($2, name),
            sector         = COALESCE($3, sector),
            category       = COALESCE($4, category),
            market_cap_bdt = COALESCE($5, market_cap_bdt),
            listing_date   = COALESCE($6, listing_date),
            enriched_at    = $7,
            updated_at     = $7
        WHERE ticker = $1
        """,
        ticker,
        name,
        sector,
        category,
        _market_cap_bdt(row),
        _listing_date(row),
        datetime.now(timezone.utc),
    )
    return {"ticker": ticker, "status": "ok", "sector": sector}


async def enrich_companies(tickers: list[str] | None = None) -> dict:
    """Enrich the given tickers, or every unenriched company when None."""
    pool = await get_pool()

    if tickers is None:
        rows = await pool.fetch(
            "SELECT ticker FROM companies WHERE enriched_at IS NULL ORDER BY ticker"
        )
        tickers = [r["ticker"] for r in rows]

    total = len(tickers)
    logger.info("enrich_companies_start total=%d", total)
    if total == 0:
        return {"ok": 0, "failed": 0, "total": 0}

    adapter = DSEDirectCompanyInfoAdapter()
    sem = asyncio.Semaphore(_CONCURRENCY)
    done = 0
    summary: dict = {"ok": 0, "failed": 0, "total": total}

    async def run_one(ticker: str) -> None:
        nonlocal done
        async with sem:
            r = await _enrich_one(pool, ticker, adapter)
            done += 1
            if r["status"] == "ok":
                summary["ok"] += 1
                logger.info("[%d/%d] %s sector=%s", done, total, ticker, r.get("sector"))
            else:
                summary["failed"] += 1
                logger.warning("[%d/%d] %s FAILED", done, total, ticker)
            await asyncio.sleep(_DELAY_S)

    await asyncio.gather(*[run_one(t) for t in tickers])
    logger.info(
        "enrich_companies_done ok=%d failed=%d total=%d",
        summary["ok"], summary["failed"], summary["total"],
    )
    return summary


if __name__ == "__main__":
    logging.basicConfig(level="INFO", format="%(levelname)s %(message)s")
    tickers_arg = sys.argv[1:] or None
    asyncio.run(enrich_companies(tickers_arg))
