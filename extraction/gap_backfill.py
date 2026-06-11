"""Daily-bar gap backfill — self-healing for whole missed trading days.

job_price_gap_backfill (daily 18:00 BD cron; also caught up by boot recovery)
scans the last gap_backfill_window_days for Sun–Thu dates with zero
stock_prices rows, refills them per ticker from the historical_ohlcv chain,
and refreshes the continuous aggregates so backfilled bars appear in the
daily/weekly/monthly/sector views (daily_ohlcv's refresh policy only looks
back 3 days, so old inserts never materialize on their own).

Dates that stay empty after a fetch pass (DSE holidays) are recorded in
market_gaps with reason='backfill_no_data' and excluded from future scans.

Pure decision logic lives in find_missing_dates(); DB access goes through
_get_pool() so tests can patch it.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

import pandas as pd
import pytz

logger = logging.getLogger(__name__)
BD_TZ = pytz.timezone("Asia/Dhaka")

# Python weekday(): Mon=0..Sun=6. DSE trades Sun–Thu → {6,0,1,2,3}.
_MARKET_WEEKDAYS = {6, 0, 1, 2, 3}

NO_DATA_REASON = "backfill_no_data"
_FETCH_CONCURRENCY = 3
_FETCH_DELAY_SECONDS = 1.5


async def _get_pool() -> "asyncpg.Pool":
    """Indirection so tests can patch the DB pool without a live database."""
    from db.pool import get_pool
    return await get_pool()


def find_missing_dates(
    present: set[date],
    skip: set[date],
    today: date,
    window_days: int,
) -> list[date]:
    """Trading dates (Sun–Thu) in [today - window_days, today - 1] that are in
    neither `present` (have stock_prices rows) nor `skip` (recorded as
    holiday/no-data). Today is excluded — live/EOD jobs own today's bar.
    """
    out: list[date] = []
    d = today - timedelta(days=window_days)
    while d < today:
        if d.weekday() in _MARKET_WEEKDAYS and d not in present and d not in skip:
            out.append(d)
        d += timedelta(days=1)
    return out


# Same column set as bulk_load loaders. DO NOTHING — backfill must never
# clobber bars written by the live/EOD jobs or earlier loads.
_INSERT_SQL = """
    INSERT INTO stock_prices
        (time, ticker, open, high, low, close,
         volume, trades, value_bdt, prev_close, change_pct,
         source, ingested_at, quality_flag)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
    ON CONFLICT (time, ticker) DO NOTHING
"""


def _opt_int(val: object) -> int | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        if pd.isna(val):  # type: ignore[arg-type]
            return None
    except (TypeError, ValueError):
        pass
    return int(val)  # type: ignore[arg-type]


def rows_from_frame(
    df: pd.DataFrame,
    missing: set[date],
    ingested_at: datetime,
) -> list[tuple]:
    """_INSERT_SQL tuples for bars whose trading date is in `missing`.

    Bars without a close (amarstock_historical carries only high/low) get
    close=(high+low)/2 and quality_flag='no_ohlc' — the bulk-loader
    convention; a later bdshare pass can upgrade them to real OHLCV.
    """
    from extraction.bulk_load.historical_loader import _mid  # noqa: PLC0415

    rows: list[tuple] = []
    if df.empty:
        return rows
    for _, row in df.iterrows():
        ts = row.get("date")
        if ts is None or pd.isna(ts):
            continue
        ts = pd.Timestamp(ts).to_pydatetime()
        if ts.date() not in missing:
            continue
        close = row.get("close")
        quality = "ok"
        # NaN from mixed-dict DataFrame construction counts as absent
        if close is None or (isinstance(close, float) and pd.isna(close)):
            try:
                if pd.isna(close):  # type: ignore[arg-type]
                    close = None
            except (TypeError, ValueError):
                pass
            close = _mid(row.get("high"), row.get("low"))
            quality = "no_ohlc"
        if close is None:
            continue
        rows.append((
            ts,
            row["ticker"],
            row.get("open"),
            row.get("high"),
            row.get("low"),
            close,
            _opt_int(row.get("volume")),
            _opt_int(row.get("trades")),
            row.get("value_bdt"),
            None,   # prev_close
            None,   # change_pct
            row.get("source"),
            ingested_at,
            quality,
        ))
    return rows
