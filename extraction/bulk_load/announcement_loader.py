"""
Bulk announcement loader — fetches per-company historical announcements for all active tickers.

Usage:
    python -m extraction.bulk_load.announcement_loader              # all active tickers
    python -m extraction.bulk_load.announcement_loader BRACBANK GP  # specific tickers

Rate: 3 concurrent requests, 1.5s delay between each → ~406 tickers in ~12 min.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone

from db.pool import get_pool
from extraction.adapters.dse_direct import DSEDirectCompanyNewsAdapter
from extraction.base import AdapterError
from extraction.parsers.announcement_parser import content_hash, parse_announcement

logger = logging.getLogger(__name__)

_CONCURRENCY = 3
_DELAY_S = 1.5


async def _load_one(
    pool,
    ticker: str,
    adapter: DSEDirectCompanyNewsAdapter,
    job_id: str,
) -> dict:
    try:
        result = await adapter.fetch(ticker=ticker)
    except AdapterError as exc:
        logger.warning("announcement_fetch_failed ticker=%s error=%s", ticker, exc)
        return {"ticker": ticker, "status": "failed", "inserted": 0, "total": 0}

    inserted = 0
    for _, row in result.data.iterrows():
        parsed = parse_announcement(
            ticker=ticker,
            headline=row["headline"],
            details=row.get("details") or "",
            published_at=str(row["published_at"]),
        )
        chash = content_hash(ticker, row["headline"], str(row["published_at"]))

        rec = await pool.fetchrow(
            """
            INSERT INTO company_announcements
                (ticker, published_at, headline, details, source,
                 announcement_type, eps_value, eps_period, eps_type,
                 dividend_cash_pct, dividend_stock_pct, dividend_year,
                 content_hash, ingestion_job)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
            ON CONFLICT (content_hash) DO NOTHING
            RETURNING id
            """,
            ticker,
            row["published_at"],
            row["headline"],
            row.get("details") or None,
            row["source"],
            parsed.announcement_type,
            parsed.eps_value,
            parsed.eps_period,
            parsed.eps_type,
            parsed.dividend_cash_pct,
            parsed.dividend_stock_pct,
            parsed.dividend_year,
            chash,
            job_id,
        )
        if rec:
            inserted += 1

    return {"ticker": ticker, "status": "ok", "inserted": inserted, "total": result.records}


async def bulk_load_announcements(tickers: list[str] | None = None) -> dict:
    pool = await get_pool()

    if tickers is None:
        rows = await pool.fetch(
            "SELECT ticker FROM companies WHERE is_active = true ORDER BY ticker"
        )
        tickers = [r["ticker"] for r in rows]

    total = len(tickers)
    logger.info("announcement_bulk_start total=%d", total)

    adapter = DSEDirectCompanyNewsAdapter()
    sem = asyncio.Semaphore(_CONCURRENCY)
    job_id = f"announcement_bulk_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    done = 0
    summary: dict = {"ok": 0, "failed": 0, "total_inserted": 0}

    async def fetch_one(ticker: str) -> None:
        nonlocal done
        async with sem:
            r = await _load_one(pool, ticker, adapter, job_id)
            done += 1
            if r["status"] == "ok":
                summary["ok"] += 1
                summary["total_inserted"] += r["inserted"]
                logger.info(
                    "[%d/%d] %s inserted=%d/%d",
                    done, total, ticker, r["inserted"], r["total"],
                )
            else:
                summary["failed"] += 1
                logger.warning("[%d/%d] %s FAILED", done, total, ticker)
            await asyncio.sleep(_DELAY_S)

    await asyncio.gather(*[fetch_one(t) for t in tickers])
    logger.info(
        "announcement_bulk_done ok=%d failed=%d inserted=%d",
        summary["ok"], summary["failed"], summary["total_inserted"],
    )
    return summary


if __name__ == "__main__":
    logging.basicConfig(level="INFO", format="%(levelname)s %(message)s")
    tickers_arg = sys.argv[1:] or None
    asyncio.run(bulk_load_announcements(tickers_arg))
