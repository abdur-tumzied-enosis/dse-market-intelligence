"""
BDShare full historical backfill — 2012-01-01 to today, all active tickers.

Pre-ML prerequisite: upgrades AmarStock no_ohlc rows to real OHLCV and fills
any tickers that have no data at all.

Uses ON CONFLICT ... DO UPDATE so existing no_ohlc rows get upgraded to
quality_flag='ok' with real open/close values.

Checkpoint file records progress so the run can be safely interrupted and resumed.

Usage:
    python -m extraction.bulk_load.run bdshare-backfill
    python -m extraction.bulk_load.run bdshare-backfill --concurrency 3 --delay 2.0
    python -m extraction.bulk_load.run bdshare-backfill --tickers GP,BRACBANK
    python -m extraction.bulk_load.run bdshare-backfill --force   # ignore checkpoint, re-fetch all
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import asyncpg
import structlog

from extraction.adapters.bdshare.historical import BDShareHistoricalAdapter
from extraction.normalizers import normalize_ticker

logger = structlog.get_logger(__name__)

FETCH_START = "2012-01-01"
BATCH_SIZE = 10_000
DEFAULT_CONCURRENCY = 3
DEFAULT_DELAY = 2.0  # seconds — bdshare hits DSE; be polite

CHECKPOINT_PATH = Path("tests/fixtures/bdshare_backfill_checkpoint.json")

_UPSERT_SQL = """
    INSERT INTO stock_prices
        (time, ticker, open, high, low, close,
         volume, trades, value_bdt, prev_close, change_pct,
         source, ingested_at, quality_flag)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
    ON CONFLICT (time, ticker) DO UPDATE SET
        open         = EXCLUDED.open,
        high         = EXCLUDED.high,
        low          = EXCLUDED.low,
        close        = EXCLUDED.close,
        volume       = COALESCE(EXCLUDED.volume,    stock_prices.volume),
        trades       = COALESCE(EXCLUDED.trades,    stock_prices.trades),
        value_bdt    = COALESCE(EXCLUDED.value_bdt, stock_prices.value_bdt),
        source       = EXCLUDED.source,
        quality_flag = EXCLUDED.quality_flag,
        ingested_at  = EXCLUDED.ingested_at
    WHERE stock_prices.quality_flag <> 'ok'
"""

_adapter = BDShareHistoricalAdapter()


# ---------------------------------------------------------------------------
# Checkpoint helpers
# ---------------------------------------------------------------------------

def _load_checkpoint() -> dict:
    if CHECKPOINT_PATH.exists():
        try:
            return json.loads(CHECKPOINT_PATH.read_text())
        except Exception:
            pass
    return {"done": [], "failed": {}}


def _save_checkpoint(cp: dict) -> None:
    CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_PATH.write_text(json.dumps(cp, indent=2))


# ---------------------------------------------------------------------------
# Per-ticker fetch + insert
# ---------------------------------------------------------------------------

@dataclass
class TickerResult:
    ticker: str
    rows_upserted: int = 0
    skipped: bool = False
    error: Optional[str] = None


async def _fetch_bdshare(ticker: str, fetch_end: str) -> object:
    """Run sync bdshare call in thread so we don't block the event loop."""
    import bdshare as bd
    return await asyncio.to_thread(bd.get_historical_data, FETCH_START, fetch_end, ticker)


async def _load_ticker(
    ticker: str,
    pool: asyncpg.Pool,
    semaphore: asyncio.Semaphore,
    delay: float,
    fetch_end: str,
) -> TickerResult:
    result = TickerResult(ticker=ticker)
    async with semaphore:
        try:
            raw = await _fetch_bdshare(ticker, fetch_end)
        except Exception as exc:
            result.error = f"fetch: {exc}"
            logger.warning("bdshare_fetch_failed", ticker=ticker, error=str(exc))
            return result
        finally:
            await asyncio.sleep(delay)

    if raw is None or len(raw) == 0:
        result.error = "empty response"
        logger.warning("bdshare_empty", ticker=ticker)
        return result

    try:
        df = _adapter.normalize(raw)
    except Exception as exc:
        result.error = f"normalize: {exc}"
        logger.error("normalize_failed", ticker=ticker, error=str(exc))
        return result

    if df.empty:
        result.error = "empty after normalize"
        return result

    ingested_at = datetime.now(timezone.utc)
    rows: list[tuple] = []
    for _, row in df.iterrows():
        if row.get("date") is None or row.get("close") is None:
            continue
        rows.append((
            row["date"],
            normalize_ticker(ticker),
            row.get("open"),
            row.get("high"),
            row.get("low"),
            row["close"],
            int(row["volume"])   if row.get("volume")   is not None else None,
            int(row["trades"])   if row.get("trades")   is not None else None,
            row.get("value_bdt"),
            None,  # prev_close
            None,  # change_pct
            "bdshare_historical",
            ingested_at,
            "ok",
        ))

    if not rows:
        result.error = "no valid rows after filtering"
        return result

    try:
        async with pool.acquire() as conn:
            for i in range(0, len(rows), BATCH_SIZE):
                await conn.executemany(_UPSERT_SQL, rows[i : i + BATCH_SIZE])
        result.rows_upserted = len(rows)
    except Exception as exc:
        result.error = f"insert: {exc}"
        logger.error("insert_failed", ticker=ticker, error=str(exc))
        return result

    logger.info("bdshare_ticker_done", ticker=ticker, rows=result.rows_upserted)
    return result


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

@dataclass
class BackfillReport:
    total: int = 0
    loaded: int = 0
    skipped_checkpoint: int = 0
    failed: int = 0
    total_rows: int = 0
    results: list[TickerResult] = field(default_factory=list)


async def run_backfill(
    dsn: str,
    tickers: Optional[list[str]] = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    delay: float = DEFAULT_DELAY,
    force: bool = False,
) -> BackfillReport:
    report = BackfillReport()
    fetch_end = date.today().isoformat()

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=concurrency + 2)
    try:
        if tickers is None:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT ticker FROM companies WHERE is_active = true ORDER BY ticker"
                )
            tickers = [r["ticker"] for r in rows]

        cp = _load_checkpoint() if not force else {"done": [], "failed": {}}
        done_set: set[str] = set(cp.get("done", []))

        pending = [t for t in tickers if t not in done_set]
        report.skipped_checkpoint = len(tickers) - len(pending)
        report.total = len(tickers)

        print(f"Tickers total : {report.total}")
        print(f"Already done  : {report.skipped_checkpoint}  (checkpoint)")
        print(f"To fetch      : {len(pending)}")
        print(f"Concurrency   : {concurrency}  |  delay: {delay}s")
        est_min = round(len(pending) * delay / concurrency / 60, 1)
        print(f"ETA           : ~{est_min} min minimum\n")

        semaphore = asyncio.Semaphore(concurrency)
        tasks = [
            _load_ticker(t, pool, semaphore, delay, fetch_end)
            for t in pending
        ]

        completed = 0
        for coro in asyncio.as_completed(tasks):
            r = await coro
            report.results.append(r)
            completed += 1

            if r.error:
                report.failed += 1
                cp["failed"][r.ticker] = r.error
            else:
                report.loaded += 1
                report.total_rows += r.rows_upserted
                done_set.add(r.ticker)
                cp["done"] = list(done_set)

            # Save checkpoint every 10 tickers
            if completed % 10 == 0:
                _save_checkpoint(cp)
                pct = (completed / len(pending) * 100) if pending else 100
                print(
                    f"  [{completed}/{len(pending)}  {pct:.0f}%]"
                    f"  rows so far: {report.total_rows:,}"
                    f"  failed: {report.failed}"
                )

        _save_checkpoint(cp)

    finally:
        await pool.close()

    return report
