"""
BDShare fallback loader for tickers with no AmarStock data.

Fetches full OHLCV from BDShare, inserts into stock_prices.
Also marks confirmed-dead tickers as is_active=false in companies.

Usage:
    python -m extraction.bulk_load.bdshare_fallback
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import asyncpg
import bdshare as bd
import structlog
from dotenv import load_dotenv

from extraction.adapters.bdshare.historical import BDShareHistoricalAdapter
from extraction.normalizers import normalize_ticker

load_dotenv()
logger = structlog.get_logger(__name__)

# Tickers that have BDShare data but empty AmarStock response (bonds)
BDSHARE_ONLY = [
    "ABBLPBOND",
    "DBLPBOND",
    "MBPLCPBOND",
    "SEB1PBOND",
    "UCB2PBOND",
    "USMANIAGL",
]

# Tickers with no data anywhere — mark inactive
DEAD_TICKERS = [
    "ACHIASF", "AMPL", "AOPLC", "APEXWEAV", "BDPAINTS",
    "BENGALBISC", "CRAFTSMAN", "HIMADRI", "KBSEED", "KFL",
    "MAMUNAGRO", "MASTERAGRO", "MKFOOTWEAR", "MOSTFAMETL",
    "NIALCO", "ORYZAAGRO", "SADHESIVE", "WEBCOATS",
    "WONDERTOYS", "YUSUFLOUR",
]

FETCH_START = "2012-01-01"
FETCH_END   = "2026-05-21"
BATCH_SIZE  = 10_000

_INSERT_SQL = """
    INSERT INTO stock_prices
        (time, ticker, open, high, low, close,
         volume, trades, value_bdt, prev_close, change_pct,
         source, ingested_at, quality_flag)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
    ON CONFLICT (time, ticker) DO NOTHING
"""

adapter = BDShareHistoricalAdapter()


def _dsn() -> str:
    url = os.environ.get("DATABASE_SYNC_URL", "") or os.environ.get("DATABASE_URL", "")
    return (
        url
        .replace("postgresql+asyncpg://", "postgresql://")
        .replace("postgresql+psycopg://", "postgresql://")
    )


async def load_bdshare_ticker(ticker: str, pool: asyncpg.Pool) -> int:
    raw = bd.get_historical_data(FETCH_START, FETCH_END, ticker)
    if raw is None or len(raw) == 0:
        logger.warning("bdshare_empty", ticker=ticker)
        return 0

    df = adapter.normalize(raw)
    if df.empty:
        logger.warning("normalize_empty", ticker=ticker)
        return 0

    ingested_at = datetime.now(timezone.utc)
    rows = []
    for _, row in df.iterrows():
        if row.get("date") is None or row.get("close") is None:
            continue
        rows.append((
            row["date"],       # time
            ticker,            # ticker
            row.get("open"),   # open
            row.get("high"),   # high
            row.get("low"),    # low
            row["close"],      # close (real value)
            int(row["volume"]) if row.get("volume") is not None else None,
            int(row["trades"]) if row.get("trades") is not None else None,
            row.get("value_bdt"),
            None,              # prev_close
            None,              # change_pct
            "bdshare_historical",
            ingested_at,
            "ok",              # full OHLCV — quality ok
        ))

    if not rows:
        return 0

    inserted = 0
    async with pool.acquire() as conn:
        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i : i + BATCH_SIZE]
            await conn.executemany(_INSERT_SQL, batch)
            inserted += len(batch)

    logger.info("bdshare_ticker_loaded", ticker=ticker, rows=inserted)
    return inserted


async def mark_dead(tickers: list[str], pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE companies SET is_active = false, updated_at = NOW() WHERE ticker = ANY($1::text[])",
            tickers,
        )
    count = int(result.split()[-1])
    logger.info("dead_tickers_deactivated", count=count)
    return count


async def run() -> None:
    dsn = _dsn()
    if not dsn:
        raise SystemExit("DATABASE_URL or DATABASE_SYNC_URL not set")

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
    try:
        total_rows = 0
        print(f"Loading {len(BDSHARE_ONLY)} tickers from BDShare ({FETCH_START} to {FETCH_END})...")
        for ticker in BDSHARE_ONLY:
            try:
                n = await load_bdshare_ticker(ticker, pool)
                total_rows += n
                print(f"  {ticker}: {n} rows inserted")
            except Exception as exc:
                print(f"  {ticker}: FAILED - {exc}")

        print(f"\nTotal rows inserted: {total_rows}")

        print(f"\nMarking {len(DEAD_TICKERS)} dead tickers as is_active=false...")
        count = await mark_dead(DEAD_TICKERS, pool)
        print(f"  Deactivated: {count} tickers")

    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(run())
