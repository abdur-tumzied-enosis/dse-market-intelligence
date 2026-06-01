"""
Historical fundamentals loader — scrapes displayCompany.php for all active tickers.

Extracts multi-year EPS, NAV, P/E, dividends (typically 5–8 years per company)
and writes into the fundamentals table with fiscal_year set.

Usage:
    python -m extraction.bulk_load.fundamentals_historical_loader              # all tickers
    python -m extraction.bulk_load.fundamentals_historical_loader BRACBANK GP  # specific

Rate: 3 concurrent, 1.5s delay → ~406 tickers in ~12 min.
"""
from __future__ import annotations

import asyncio
import logging
import math
import sys
from datetime import datetime, timezone
from typing import Any

from db.pool import get_pool
from extraction.adapters.dse_direct.company_info import DSEDirectCompanyInfoAdapter
from extraction.base import AdapterError

logger = logging.getLogger(__name__)

_CONCURRENCY = 3
_DELAY_S = 1.5


def _num(value: Any) -> float | None:
    """Coerce pandas/numpy NaN to None so NUMERIC columns never store 'NaN'.

    Missing dividends/EPS arrive as None but pandas turns them into float NaN in
    the DataFrame; asyncpg writes that straight into NUMERIC as 'NaN', which then
    fails Pydantic finite_number validation on read.
    """
    if value is None:
        return None
    try:
        if math.isnan(value):
            return None
    except (TypeError, ValueError):
        return value
    return value


async def _load_one(
    pool,
    ticker: str,
    adapter: DSEDirectCompanyInfoAdapter,
    job_id: str,
) -> dict:
    try:
        result = await adapter.fetch_historical(ticker=ticker)
    except AdapterError as exc:
        logger.warning("fundamentals_hist_failed ticker=%s error=%s", ticker, exc)
        return {"ticker": ticker, "status": "failed", "upserted": 0}

    upserted = 0
    for _, row in result.data.iterrows():
        await pool.execute(
            """
            INSERT INTO fundamentals
                (ticker, fiscal_year, eps, eps_diluted, nav, pe,
                 cash_div_pct, stock_div_pct, fetched_at, source, ingestion_job)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
            ON CONFLICT (ticker, fiscal_year)
            WHERE fiscal_year IS NOT NULL
            DO UPDATE SET
                eps            = EXCLUDED.eps,
                eps_diluted    = EXCLUDED.eps_diluted,
                nav            = EXCLUDED.nav,
                pe             = EXCLUDED.pe,
                cash_div_pct   = EXCLUDED.cash_div_pct,
                stock_div_pct  = EXCLUDED.stock_div_pct,
                fetched_at     = EXCLUDED.fetched_at,
                ingestion_job  = EXCLUDED.ingestion_job
            """,
            row["ticker"],
            int(row["fiscal_year"]),
            _num(row.get("eps")),
            _num(row.get("eps_diluted")),
            _num(row.get("nav")),
            _num(row.get("pe")),
            _num(row.get("cash_div_pct")),
            _num(row.get("stock_div_pct")),
            row["fetched_at"],
            row["source"],
            job_id,
        )
        upserted += 1

    return {"ticker": ticker, "status": "ok", "upserted": upserted, "years": result.records}


async def bulk_load_fundamentals_historical(tickers: list[str] | None = None) -> dict:
    pool = await get_pool()

    if tickers is None:
        rows = await pool.fetch(
            "SELECT ticker FROM companies WHERE is_active = true ORDER BY ticker"
        )
        tickers = [r["ticker"] for r in rows]

    total = len(tickers)
    logger.info("fundamentals_hist_start total=%d", total)

    adapter = DSEDirectCompanyInfoAdapter()
    sem = asyncio.Semaphore(_CONCURRENCY)
    job_id = f"fundamentals_hist_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    done = 0
    summary: dict = {"ok": 0, "failed": 0, "total_upserted": 0}

    async def fetch_one(ticker: str) -> None:
        nonlocal done
        async with sem:
            r = await _load_one(pool, ticker, adapter, job_id)
            done += 1
            if r["status"] == "ok":
                summary["ok"] += 1
                summary["total_upserted"] += r["upserted"]
                logger.info(
                    "[%d/%d] %s years=%d upserted=%d",
                    done, total, ticker, r["years"], r["upserted"],
                )
            else:
                summary["failed"] += 1
                logger.warning("[%d/%d] %s FAILED", done, total, ticker)
            await asyncio.sleep(_DELAY_S)

    await asyncio.gather(*[fetch_one(t) for t in tickers])
    logger.info(
        "fundamentals_hist_done ok=%d failed=%d upserted=%d",
        summary["ok"], summary["failed"], summary["total_upserted"],
    )
    return summary


if __name__ == "__main__":
    logging.basicConfig(level="INFO", format="%(levelname)s %(message)s")
    tickers_arg = sys.argv[1:] or None
    asyncio.run(bulk_load_fundamentals_historical(tickers_arg))
