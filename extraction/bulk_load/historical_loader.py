"""
Bulk historical price loader — AmarStock /qoutes/ endpoint.

Fetches ~700 rows per ticker (MaxPrice=high, MinPrice=low, Volume, Trades, Value).
AmarStock does not provide open/close; close is estimated as (high+low)/2
with quality_flag='no_ohlc'. BDShare incremental jobs can backfill full OHLCV later.

Skips tickers with existing data unless force=True. Idempotent via
ON CONFLICT (time, ticker) DO NOTHING (requires migration 008).

Usage:
    loader = HistoricalLoader(dsn="postgresql://...", concurrency=10)
    report = await loader.run()
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

import asyncpg
import structlog

from extraction.adapters.amarstock.csv_historical import AmarStockCSVAdapter
from extraction.base import AdapterError

logger = structlog.get_logger(__name__)

BATCH_SIZE = 10_000
DEFAULT_CONCURRENCY = 10
DEFAULT_DELAY = 1.0  # seconds between requests per worker

_INSERT_SQL = """
    INSERT INTO stock_prices
        (time, ticker, open, high, low, close,
         volume, trades, value_bdt, prev_close, change_pct,
         source, ingested_at, quality_flag)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
    ON CONFLICT (time, ticker) DO NOTHING
"""


def _mid(high: Decimal | None, low: Decimal | None) -> Decimal | None:
    """Return (high+low)/2 as close estimate, or whichever is available."""
    if high is not None and low is not None:
        try:
            return (Decimal(str(high)) + Decimal(str(low))) / 2
        except InvalidOperation:
            pass
    return high if high is not None else low


@dataclass
class LoadResult:
    ticker: str
    rows_fetched: int = 0
    rows_inserted: int = 0
    skipped: bool = False
    error: Optional[str] = None


@dataclass
class LoadReport:
    total_tickers: int = 0
    loaded: int = 0
    skipped: int = 0
    failed: int = 0
    total_rows: int = 0
    results: list[LoadResult] = field(default_factory=list)


class HistoricalLoader:
    def __init__(
        self,
        dsn: str,
        concurrency: int = DEFAULT_CONCURRENCY,
        delay: float = DEFAULT_DELAY,
        batch_size: int = BATCH_SIZE,
        force: bool = False,
    ) -> None:
        self._dsn = dsn
        self._concurrency = concurrency
        self._delay = delay
        self._batch_size = batch_size
        self._force = force
        self._adapter = AmarStockCSVAdapter(request_delay=delay)

    async def _get_tickers(self, conn: asyncpg.Connection) -> list[str]:
        rows = await conn.fetch(
            "SELECT ticker FROM companies WHERE is_active = true ORDER BY ticker"
        )
        return [r["ticker"] for r in rows]

    async def _get_existing_counts(self, conn: asyncpg.Connection) -> dict[str, int]:
        rows = await conn.fetch(
            "SELECT ticker, COUNT(*) AS cnt FROM stock_prices GROUP BY ticker"
        )
        return {r["ticker"]: r["cnt"] for r in rows}

    async def _load_ticker(
        self,
        ticker: str,
        pool: asyncpg.Pool,
        semaphore: asyncio.Semaphore,
        existing_count: int,
    ) -> LoadResult:
        result = LoadResult(ticker=ticker)

        if not self._force and existing_count > 0:
            logger.debug("ticker_skip_exists", ticker=ticker, rows=existing_count)
            result.skipped = True
            return result

        async with semaphore:
            try:
                adapter_result = await self._adapter.fetch(ticker)
            except AdapterError as exc:
                logger.warning("fetch_failed", ticker=ticker, error=str(exc))
                result.error = str(exc)
                return result
            except Exception as exc:
                logger.error("fetch_error", ticker=ticker, error=str(exc))
                result.error = f"unexpected: {exc}"
                return result

        df = adapter_result.data
        result.rows_fetched = len(df)

        if df.empty:
            result.error = "empty response"
            return result

        ingested_at = datetime.now(timezone.utc)
        rows_to_insert: list[tuple] = []

        for _, row in df.iterrows():
            high = row.get("high")
            low = row.get("low")
            close = _mid(high, low)

            if close is None or row["date"] is None:
                continue

            rows_to_insert.append((
                row["date"],              # $1  time
                ticker,                   # $2  ticker
                None,                     # $3  open (not in AmarStock data)
                high,                     # $4  high
                low,                      # $5  low
                close,                    # $6  close (estimated midpoint)
                int(row["volume"]) if row.get("volume") is not None else None,  # $7
                int(row["trades"]) if row.get("trades") is not None else None,  # $8
                row.get("value_bdt"),     # $9  value_bdt
                None,                     # $10 prev_close
                None,                     # $11 change_pct
                "amarstock_historical",   # $12 source
                ingested_at,              # $13 ingested_at
                "no_ohlc",               # $14 quality_flag — close is estimated
            ))

        if not rows_to_insert:
            result.error = "no valid rows after filtering"
            return result

        try:
            inserted = await self._batch_insert(pool, rows_to_insert)
            result.rows_inserted = inserted
        except Exception as exc:
            logger.error("insert_failed", ticker=ticker, error=str(exc))
            result.error = f"insert: {exc}"
            return result

        logger.info(
            "ticker_loaded",
            ticker=ticker,
            fetched=result.rows_fetched,
            inserted=result.rows_inserted,
        )
        return result

    async def _batch_insert(self, pool: asyncpg.Pool, rows: list[tuple]) -> int:
        inserted = 0
        async with pool.acquire() as conn:
            for i in range(0, len(rows), self._batch_size):
                batch = rows[i : i + self._batch_size]
                await conn.executemany(_INSERT_SQL, batch)
                inserted += len(batch)
        return inserted

    async def run(self, tickers: Optional[list[str]] = None) -> LoadReport:
        report = LoadReport()

        pool = await asyncpg.create_pool(
            self._dsn,
            min_size=2,
            max_size=self._concurrency + 2,
        )
        try:
            async with pool.acquire() as conn:
                all_tickers = tickers or await self._get_tickers(conn)
                existing = await self._get_existing_counts(conn)

            report.total_tickers = len(all_tickers)
            logger.info(
                "bulk_load_start",
                total_tickers=len(all_tickers),
                concurrency=self._concurrency,
                force=self._force,
            )

            semaphore = asyncio.Semaphore(self._concurrency)
            tasks = [
                self._load_ticker(t, pool, semaphore, existing.get(t, 0))
                for t in all_tickers
            ]
            results: list[LoadResult] = await asyncio.gather(*tasks)

            for r in results:
                report.results.append(r)
                if r.skipped:
                    report.skipped += 1
                elif r.error:
                    report.failed += 1
                else:
                    report.loaded += 1
                    report.total_rows += r.rows_inserted

        finally:
            await pool.close()

        logger.info(
            "bulk_load_complete",
            loaded=report.loaded,
            skipped=report.skipped,
            failed=report.failed,
            total_rows=report.total_rows,
        )
        return report
