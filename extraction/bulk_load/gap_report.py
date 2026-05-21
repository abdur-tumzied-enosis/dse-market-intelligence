"""
Gap analysis report for historical price data.

Queries stock_prices for row counts per ticker, computes expected
DSE trading days (Sun–Thu), and flags tickers with coverage gaps.

Coverage thresholds:
    >= 80% → ok
    50–79% → minor_gap
    < 50%  → major_gap
    0 rows → empty

Usage:
    python -m extraction.bulk_load.gap_report
    python -m extraction.bulk_load.gap_report --from-date 2018-01-01
"""
from __future__ import annotations

import argparse
import asyncio
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import asyncpg
import structlog
from dotenv import load_dotenv

load_dotenv()
logger = structlog.get_logger(__name__)

# DSE trades Sun–Thu. Python weekday(): Mon=0, Tue=1, Wed=2, Thu=3, Sun=6
DSE_TRADING_WEEKDAYS = {0, 1, 2, 3, 6}

# AmarStock /qoutes/ data starts here; use as default from_date
AMARSTOCK_DATA_START = date(2018, 1, 1)


def _dsn() -> str:
    url = os.environ.get("DATABASE_SYNC_URL", "") or os.environ.get("DATABASE_URL", "")
    return (
        url
        .replace("postgresql+asyncpg://", "postgresql://")
        .replace("postgresql+psycopg://", "postgresql://")
    )


def expected_trading_days(from_date: date, to_date: date) -> int:
    """Count DSE trading weekdays (Sun–Thu) in [from_date, to_date]."""
    count = 0
    d = from_date
    while d <= to_date:
        if d.weekday() in DSE_TRADING_WEEKDAYS:
            count += 1
        d += timedelta(days=1)
    return count


@dataclass
class TickerGap:
    ticker: str
    actual_rows: int
    date_min: Optional[date]
    date_max: Optional[date]
    expected_rows: int
    coverage_pct: float
    status: str  # 'ok' | 'minor_gap' | 'major_gap' | 'empty'


async def run_report(
    dsn: str,
    from_date: Optional[date] = None,
) -> list[TickerGap]:
    """
    Generate gap report for all active tickers.

    Args:
        from_date: Start of expected range (default: AMARSTOCK_DATA_START)
    """
    if from_date is None:
        from_date = AMARSTOCK_DATA_START
    to_date = date.today()
    expected = expected_trading_days(from_date, to_date)
    logger.info("gap_report_start", from_date=from_date, to_date=to_date, expected_days=expected)

    conn = await asyncpg.connect(dsn)
    try:
        tickers = [
            r["ticker"]
            for r in await conn.fetch(
                "SELECT ticker FROM companies WHERE is_active = true ORDER BY ticker"
            )
        ]

        stats_rows = await conn.fetch(
            """
            SELECT
                ticker,
                COUNT(*)        AS actual_rows,
                MIN(time)::date AS date_min,
                MAX(time)::date AS date_max
            FROM stock_prices
            GROUP BY ticker
            """
        )
        stats = {r["ticker"]: r for r in stats_rows}
    finally:
        await conn.close()

    gaps: list[TickerGap] = []
    for ticker in tickers:
        row = stats.get(ticker)
        if row is None:
            gaps.append(TickerGap(
                ticker=ticker,
                actual_rows=0,
                date_min=None,
                date_max=None,
                expected_rows=expected,
                coverage_pct=0.0,
                status="empty",
            ))
            continue

        actual = row["actual_rows"]
        coverage = (actual / expected * 100) if expected > 0 else 0.0
        if coverage >= 80:
            status = "ok"
        elif coverage >= 50:
            status = "minor_gap"
        else:
            status = "major_gap"

        gaps.append(TickerGap(
            ticker=ticker,
            actual_rows=actual,
            date_min=row["date_min"],
            date_max=row["date_max"],
            expected_rows=expected,
            coverage_pct=round(coverage, 1),
            status=status,
        ))

    return sorted(gaps, key=lambda g: g.coverage_pct)


def print_report(gaps: list[TickerGap]) -> None:
    empty = [g for g in gaps if g.status == "empty"]
    major = [g for g in gaps if g.status == "major_gap"]
    minor = [g for g in gaps if g.status == "minor_gap"]
    ok    = [g for g in gaps if g.status == "ok"]

    print(f"\n{'='*72}")
    print(f"HISTORICAL DATA GAP REPORT  --  {datetime.now(timezone.utc).date()}")
    print(f"{'='*72}")
    print(f"Total tickers : {len(gaps)}")
    print(f"  OK (>=80%)  : {len(ok)}")
    print(f"  Minor gap   : {len(minor)}")
    print(f"  Major gap   : {len(major)}")
    print(f"  Empty       : {len(empty)}")

    if empty:
        print(f"\nEMPTY -- no data loaded ({len(empty)} tickers):")
        for g in empty[:30]:
            print(f"  {g.ticker}")
        if len(empty) > 30:
            print(f"  ... and {len(empty) - 30} more")

    if major:
        print(f"\nMAJOR GAPS (<50% coverage) -- {len(major)} tickers:")
        hdr = f"  {'Ticker':<20} {'Actual':>8} {'Expected':>8} {'Coverage':>10}  Range"
        print(hdr)
        print("  " + "-" * 68)
        for g in major[:40]:
            rng = f"{g.date_min} -> {g.date_max}" if g.date_min else "-"
            print(f"  {g.ticker:<20} {g.actual_rows:>8,} {g.expected_rows:>8,} {g.coverage_pct:>9.1f}%  {rng}")
        if len(major) > 40:
            print(f"  ... and {len(major) - 40} more")

    if minor:
        print(f"\nMINOR GAPS (50-79%) -- {len(minor)} tickers:")
        for g in minor[:20]:
            rng = f"{g.date_min} -> {g.date_max}" if g.date_min else "-"
            print(f"  {g.ticker:<20} {g.actual_rows:>8,} / {g.expected_rows:>8,}  {g.coverage_pct:>6.1f}%  {rng}")
        if len(minor) > 20:
            print(f"  ... and {len(minor) - 20} more")

    total_rows = sum(g.actual_rows for g in gaps)
    print(f"\nTotal rows in stock_prices : {total_rows:,}")
    print(f"{'='*72}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Gap analysis for historical price data")
    parser.add_argument(
        "--from-date",
        default=str(AMARSTOCK_DATA_START),
        help=f"Start of expected range (default: {AMARSTOCK_DATA_START})",
    )
    args = parser.parse_args()
    from_date = date.fromisoformat(args.from_date)

    dsn = _dsn()
    if not dsn:
        raise SystemExit("DATABASE_URL or DATABASE_SYNC_URL not set")

    gaps = asyncio.run(run_report(dsn, from_date=from_date))
    print_report(gaps)


if __name__ == "__main__":
    main()
