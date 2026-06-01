"""
Bulk-load historical AmarStock CSV files into stock_prices.

Each file is named YYYY-MM-DD.csv and contains columns:
    Date (YYYYMMDD int), Scrip, Open, High, Low, Close, Volume

Strategy:
  1. Read all CSVs into memory (~1.5 M rows).
  2. Auto-register any ticker not yet in companies (placeholder name/sector).
  3. COPY rows to a temp table, then INSERT … ON CONFLICT (time, ticker) DO NOTHING.

Run:
    python scripts/load_amarstock_historical.py
    # or via make:
    make load-historical
"""

from __future__ import annotations

import asyncio
import csv
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path(__file__).parent.parent / "data" / "amarstock_csv"
SOURCE = "amarstock_csv"


def _build_dsn() -> str:
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db   = os.getenv("POSTGRES_DB",   "dse_intelligence")
    user = os.getenv("POSTGRES_USER", "dse")
    pw   = os.getenv("POSTGRES_PASSWORD", "")
    return f"postgresql://{user}:{pw}@{host}:{port}/{db}"


def _parse_date(raw: str) -> datetime:
    s = raw.strip()
    return datetime(int(s[:4]), int(s[4:6]), int(s[6:8]), tzinfo=timezone.utc)


def _float_or_none(val: str) -> float | None:
    v = val.strip()
    return float(v) if v else None


def _price_or_none(val: str) -> float | None:
    """OHLC price, treating 0/blank as missing — a traded price is never literally 0."""
    f = _float_or_none(val)
    return f if f else None


def _int_or_none(val: str) -> int | None:
    v = val.strip()
    return int(float(v)) if v else None


async def main() -> None:
    dsn = _build_dsn()
    print(f"Connecting to {dsn.split('@')[-1]} …")
    conn = await asyncpg.connect(dsn)

    # ── 1. Read all CSV files ──────────────────────────────────────────────────
    print(f"Reading CSV files from {DATA_DIR} …")
    rows: list[tuple] = []
    tickers: set[str] = set()
    files = sorted(DATA_DIR.glob("*.csv"))
    skipped = 0

    for i, f in enumerate(files, 1):
        with open(f, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                try:
                    ticker = row["Scrip"].strip()
                    close_raw = row["Close"].strip()
                    if not close_raw:
                        skipped += 1
                        continue
                    rows.append((
                        _parse_date(row["Date"]),
                        ticker,
                        _price_or_none(row["Open"]),
                        _price_or_none(row["High"]),
                        _price_or_none(row["Low"]),
                        float(close_raw),
                        _int_or_none(row["Volume"]),
                    ))
                    tickers.add(ticker)
                except (ValueError, KeyError) as exc:
                    skipped += 1
        if i % 500 == 0 or i == len(files):
            print(f"  {i:>5}/{len(files)} files  |  {len(rows):>9,} rows")

    print(f"\nTotal rows: {len(rows):,}  |  unique tickers: {len(tickers)}  |  skipped: {skipped}")

    # ── 2. Ensure all tickers exist in companies ───────────────────────────────
    print("\nSyncing companies table …")
    existing = {r["ticker"] for r in await conn.fetch("SELECT ticker FROM companies")}
    missing  = sorted(tickers - existing)
    if missing:
        await conn.executemany(
            """
            INSERT INTO companies (ticker, name, sector)
            VALUES ($1, $2, $3)
            ON CONFLICT DO NOTHING
            """,
            [
                (t, t, "Index" if t.startswith("00") else "Unknown")
                for t in missing
            ],
        )
        print(f"  Inserted {len(missing)} placeholder companies")
    else:
        print("  All tickers already in companies — nothing to add")

    # ── 3. COPY to temp → INSERT with ON CONFLICT ──────────────────────────────
    print("\nInserting into stock_prices …")

    await conn.execute("""
        CREATE TEMP TABLE _sp_load (
            time      TIMESTAMPTZ,
            ticker    TEXT,
            open      NUMERIC,
            high      NUMERIC,
            low       NUMERIC,
            close     NUMERIC,
            volume    BIGINT
        )
    """)

    await conn.copy_records_to_table(
        "_sp_load",
        records=rows,
        columns=["time", "ticker", "open", "high", "low", "close", "volume"],
    )
    print(f"  COPY complete — {len(rows):,} rows in temp table")

    result = await conn.execute(
        """
        INSERT INTO stock_prices
            (time, ticker, open, high, low, close, volume, source, quality_flag)
        SELECT time, ticker, open, high, low, close, volume, $1, 'ok'
        FROM _sp_load
        ON CONFLICT (time, ticker) DO NOTHING
        """,
        SOURCE,
    )
    # result is e.g. "INSERT 0 1234567"
    inserted = int(result.split()[-1])
    skipped_conflicts = len(rows) - inserted
    print(f"  Inserted:          {inserted:>9,}")
    print(f"  Skipped (conflict):{skipped_conflicts:>9,}")

    await conn.close()
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
