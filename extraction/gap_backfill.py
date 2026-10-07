"""Daily-bar gap backfill — self-healing for whole missed trading days.

job_price_gap_backfill (daily 18:00 BD cron; also caught up by boot recovery)
scans the last gap_backfill_window_days for Sun–Thu dates with zero
stock_prices rows, refills them from the AmarStock daily CSV (one request per
day, full OHLCV) — falling back per ticker to the historical_ohlcv chain for
days the CSV can't serve — and refreshes the continuous aggregates so backfilled bars appear in the
daily/weekly/monthly/sector views (daily_ohlcv's refresh policy only looks
back 3 days, so old inserts never materialize on their own).

Dates that stay empty after a fetch pass (DSE holidays) are recorded in
market_gaps with reason='backfill_no_data' and excluded from future scans.

Pure decision logic lives in find_missing_dates(); DB access goes through
_get_pool() so tests can patch it.
"""
from __future__ import annotations

import asyncio
import io
import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncpg

import httpx
import pandas as pd
import pytz

from extraction.normalizers import to_decimal
from extraction.observability import fire_alert
from mgmt.config import get_settings

logger = logging.getLogger(__name__)
BD_TZ = pytz.timezone("Asia/Dhaka")

# Python weekday(): Mon=0..Sun=6. DSE trades Sun–Thu → {6,0,1,2,3}.
_MARKET_WEEKDAYS = {6, 0, 1, 2, 3}

NO_DATA_REASON = "backfill_no_data"
_FETCH_CONCURRENCY = 3
_FETCH_DELAY_SECONDS = 1.5

# Same endpoint + source tag as pull_amarstock_csv.py / load_amarstock_historical.py.
_CSV_URL = "https://www.amarstock.com/data/download/CSV"
_CSV_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; DSEIntelBot/1.0)"}
_CSV_SOURCE = "amarstock_csv"


async def _get_pool() -> asyncpg.Pool:
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


def _nan_to_none(val: Any) -> Any:
    """Return None when val is None or a pandas/float NaN; otherwise return val unchanged."""
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    return val


def _opt_int(val: Any) -> int | None:
    """Return int(val) or None when val is None/NaN."""
    v = _nan_to_none(val)
    return None if v is None else int(v)


def rows_from_frame(
    df: pd.DataFrame,
    missing: set[date],
    ingested_at: datetime,
) -> list[tuple[Any, ...]]:
    """_INSERT_SQL tuples for bars whose trading date is in `missing`.

    Bars without a close (amarstock_historical carries only high/low) get
    close=(high+low)/2 and quality_flag='no_ohlc' — the bulk-loader
    convention; a later bdshare pass can upgrade them to real OHLCV.
    """
    # _mid is reused from historical_loader — canonical (high+low)/2 no_ohlc convention;
    # copying would risk divergence if the formula ever changes.
    from extraction.bulk_load.historical_loader import _mid  # noqa: PLC0415

    rows: list[tuple[Any, ...]] = []
    if df.empty:
        return rows
    for _, row in df.iterrows():
        ts = row.get("date")
        if ts is None or pd.isna(ts):
            continue
        ts = pd.Timestamp(ts).to_pydatetime()
        if ts.date() not in missing:
            continue
        # NaN from mixed-dict DataFrame construction counts as absent
        raw_close: Any = _nan_to_none(row.get("close"))
        quality = "ok"
        close: Decimal | None
        if raw_close is None:
            close = _mid(row.get("high"), row.get("low"))
            if close is not None:
                close = Decimal(str(close))
            quality = "no_ohlc"
        else:
            close = Decimal(str(raw_close))
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


# Refresh order matters: weekly/monthly are hierarchical CAs on daily_ohlcv.
# Daily must materialize first. sector_daily_stats is a plain materialized
# view (not a CA) fully rebuilt by its own daily TimescaleDB job (migration 015).
_CA_VIEWS = ("daily_ohlcv", "weekly_ohlcv", "monthly_ohlcv")


def _now_bd_date() -> date:
    """Current BD calendar date. Wrapped so tests can patch it."""
    return datetime.now(BD_TZ).date()


def _day_start_utc(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


async def _present_dates(pool: asyncpg.Pool, window_start: date) -> set[date]:
    """Trading dates that already have at least one stock_prices row."""
    rows = await pool.fetch(
        "SELECT DISTINCT (time AT TIME ZONE 'UTC')::date AS d "
        "FROM stock_prices WHERE time >= $1",
        _day_start_utc(window_start),
    )
    return {r["d"] for r in rows}


async def _skip_dates(pool: asyncpg.Pool) -> set[date]:
    """Dates already recorded as holiday/no-data by a previous backfill pass."""
    rows = await pool.fetch(
        "SELECT session_date FROM market_gaps WHERE reason = $1",
        NO_DATA_REASON,
    )
    return {r["session_date"] for r in rows}


def frame_from_csv(text: str) -> pd.DataFrame:
    """Parse an AmarStock daily CSV (Date,Scrip,Open,High,Low,Close,Volume;
    Date=YYYYMMDD) into the rows_from_frame shape. 0/blank prices → None."""
    raw = pd.read_csv(io.StringIO(text), dtype=str)
    if raw.empty:
        return pd.DataFrame()

    def _price(v: Any) -> Decimal | None:
        d = to_decimal(v)
        return d if d else None

    return pd.DataFrame({
        "ticker": raw["Scrip"].str.strip(),
        "date": pd.to_datetime(raw["Date"], format="%Y%m%d", utc=True),
        "open": raw["Open"].apply(_price),
        "high": raw["High"].apply(_price),
        "low": raw["Low"].apply(_price),
        "close": raw["Close"].apply(_price),
        "volume": pd.to_numeric(raw["Volume"], errors="coerce"),
        "source": _CSV_SOURCE,
    })


async def _csv_rows(days: set[date], known: set[str]) -> list[tuple[Any, ...]]:
    """_INSERT_SQL tuples for `days` from the AmarStock daily CSV. Per-day
    failures are logged and skipped — pass 2 retries those days per ticker."""
    rows: list[tuple[Any, ...]] = []
    async with httpx.AsyncClient(timeout=30, headers=_CSV_HEADERS) as client:
        for d in sorted(days):
            try:
                resp = await client.post(_CSV_URL, data={"QuotesType": 1, "date": d.isoformat()})
                resp.raise_for_status()
                df = frame_from_csv(resp.text)
            except Exception as exc:
                logger.warning("gap_backfill: csv day failed date=%s error=%s", d, exc)
                continue
            finally:
                await asyncio.sleep(_FETCH_DELAY_SECONDS)
            if not df.empty:
                rows += rows_from_frame(df[df["ticker"].isin(known)], days, datetime.now(UTC))
    return rows


async def _fetch_ticker_rows(
    ticker: str,
    start: str,
    end: str,
    missing: set[date],
    semaphore: asyncio.Semaphore,
) -> list[tuple[Any, ...]]:
    """One ticker through the historical_ohlcv chain, filtered to missing dates.
    Raises on AllAdaptersFailedError — caller counts and continues."""
    from extraction.registry import STREAMS  # noqa: PLC0415 — heavy import deferred

    async with semaphore:
        try:
            result = await STREAMS["historical_ohlcv"].fetch(
                ticker=ticker, start=start, end=end
            )
        finally:
            await asyncio.sleep(_FETCH_DELAY_SECONDS)
    return rows_from_frame(result.data, missing, datetime.now(UTC))


async def _record_no_data_date(pool: asyncpg.Pool, d: date) -> bool:
    """Insert a market_gaps row for a holiday/no-data date unless one already
    exists for (session_date, NO_DATA_REASON).  Returns True if inserted.

    recovered=TRUE is deliberate: it means "nothing left to recover", so these
    holiday rows never surface in the idx_market_gaps_unrecovered partial index
    that drives the intraday-gap recovery target list.
    """
    existing = await pool.fetchrow(
        "SELECT 1 FROM market_gaps WHERE session_date = $1 AND reason = $2",
        d, NO_DATA_REASON,
    )
    if existing:
        return False
    await pool.execute(
        "INSERT INTO market_gaps (session_date, gap_start, gap_end, reason, recovered) "
        "VALUES ($1, $2, $3, $4, TRUE)",
        d, _day_start_utc(d), _day_start_utc(d) + timedelta(days=1), NO_DATA_REASON,
    )
    return True


async def _refresh_aggregates(pool: asyncpg.Pool, lo: date, hi: date) -> int:
    """Materialize backfilled rows into the CA chain. daily_ohlcv's policy has
    start_offset='3 days', so older inserts never refresh on their own.
    Per-view failures are logged, not raised — data is safe in stock_prices.
    Returns the number of views that failed to refresh."""
    lo_ts = _day_start_utc(lo)
    hi_ts = _day_start_utc(hi) + timedelta(days=1)
    failed = 0
    for view in _CA_VIEWS:
        try:
            await pool.execute(
                # Casts required: Postgres can't infer param types inside CALL.
                f"CALL refresh_continuous_aggregate('{view}', $1::timestamptz, $2::timestamptz)",
                lo_ts, hi_ts,
            )
        except Exception as exc:
            logger.warning("gap_backfill: CA refresh failed view=%s error=%s", view, exc)
            failed += 1
    return failed


async def backfill_missing_dates() -> dict[str, int]:
    """Scan, refill, refresh. Returns a summary dict; never raises — this runs
    inside scheduler jobs and boot recovery, neither may crash.

    rows_inserted counts rows sent to the DO NOTHING upsert, not rows that
    actually landed (a partial day would dedupe silently — acceptable)."""
    summary: dict[str, int] = {
        "missing": 0, "recovered_dates": 0, "no_data_dates": 0,
        "rows_inserted": 0, "tickers_failed": 0, "ca_refresh_failed": 0,
    }
    cfg = get_settings()
    if not cfg.gap_backfill_enabled:
        logger.info("gap_backfill: disabled (gap_backfill_enabled=False)")
        return summary

    try:
        pool = await _get_pool()
        today = _now_bd_date()
        window_start = today - timedelta(days=cfg.gap_backfill_window_days)
        present = await _present_dates(pool, window_start)
        skip = await _skip_dates(pool)
        missing = find_missing_dates(present, skip, today, cfg.gap_backfill_window_days)
        summary["missing"] = len(missing)
        if not missing:
            logger.info("gap_backfill: no missing dates in last %d days",
                        cfg.gap_backfill_window_days)
            return summary

        logger.warning("gap_backfill: missing dates: %s",
                       ", ".join(d.isoformat() for d in missing))
        missing_set = set(missing)

        # Pass 1: AmarStock daily CSV — whole market in one request per day,
        # full OHLCV. Only known tickers (stock_prices.ticker FK → companies).
        known = {r["ticker"] for r in await pool.fetch("SELECT ticker FROM companies")}
        csv_rows = await _csv_rows(missing_set, known)
        if csv_rows:
            await pool.executemany(_INSERT_SQL, csv_rows)
            summary["rows_inserted"] += len(csv_rows)
            missing_set -= {r[0].date() for r in csv_rows}

        # Pass 2: per-ticker historical_ohlcv chain, only for days the CSV
        # didn't fill (endpoint down / day not yet published).
        if missing_set:
            start, end = min(missing_set).isoformat(), max(missing_set).isoformat()
            tickers = [r["ticker"] for r in await pool.fetch(
                "SELECT ticker FROM companies WHERE is_active = true ORDER BY ticker"
            )]
            semaphore = asyncio.Semaphore(_FETCH_CONCURRENCY)

            async def _one(ticker: str) -> int:
                try:
                    rows = await _fetch_ticker_rows(ticker, start, end, missing_set, semaphore)
                except Exception as exc:
                    summary["tickers_failed"] += 1
                    logger.warning("gap_backfill: ticker failed ticker=%s error=%s",
                                   ticker, exc)
                    return 0
                if rows:
                    await pool.executemany(_INSERT_SQL, rows)
                return len(rows)

            counts = await asyncio.gather(*(_one(t) for t in tickers))
            summary["rows_inserted"] += sum(counts)

        # Which dates actually filled? Still-empty = holiday/no-data → record
        # so the next scan skips them. Recovered → refresh the CA chain.
        after = await _present_dates(pool, window_start)
        recovered = [d for d in missing if d in after]
        still_empty = [d for d in missing if d not in after]
        summary["recovered_dates"] = len(recovered)
        summary["no_data_dates"] = len(still_empty)

        for d in still_empty:
            await _record_no_data_date(pool, d)

        if recovered:
            summary["ca_refresh_failed"] = await _refresh_aggregates(
                pool, recovered[0], recovered[-1]
            )

        await fire_alert(
            severity="WARNING",
            message=(f"Price gap backfill: {len(missing)} missing date(s), "
                     f"recovered {len(recovered)}, no-data {len(still_empty)}"),
            stream_name="historical_ohlcv",
            details={
                "missing": [d.isoformat() for d in missing],
                "recovered": [d.isoformat() for d in recovered],
                "no_data": [d.isoformat() for d in still_empty],
                "rows_inserted": summary["rows_inserted"],
                "tickers_failed": summary["tickers_failed"],
                "ca_refresh_failed": summary["ca_refresh_failed"],
            },
        )
    except Exception as exc:
        logger.error("gap_backfill: aborted error=%s", exc, exc_info=True)

    logger.info(
        "gap_backfill_summary missing=%d recovered=%d no_data=%d rows=%d failed_tickers=%d",
        summary["missing"], summary["recovered_dates"], summary["no_data_dates"],
        summary["rows_inserted"], summary["tickers_failed"],
    )
    return summary
