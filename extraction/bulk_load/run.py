"""
Phase 1I — Bulk historical load orchestrator.

Steps (run individually or all at once):
  1. seed   — Upsert companies table from AmarStock live prices
  2. load   — Bulk-load historical prices for all tickers
  3. report — Print gap analysis report

Usage:
    python -m extraction.bulk_load.run seed
    python -m extraction.bulk_load.run load
    python -m extraction.bulk_load.run load --tickers GP,BRACBANK,SQURPHARMA
    python -m extraction.bulk_load.run load --concurrency 5 --delay 2.0
    python -m extraction.bulk_load.run load --force          # re-load existing tickers
    python -m extraction.bulk_load.run report
    python -m extraction.bulk_load.run report --from-date 2018-01-01
    python -m extraction.bulk_load.run all                   # seed + load + report
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date

import structlog
from dotenv import load_dotenv

load_dotenv()
logger = structlog.get_logger(__name__)


def _get_dsn() -> str:
    url = os.environ.get("DATABASE_SYNC_URL", "") or os.environ.get("DATABASE_URL", "")
    return (
        url
        .replace("postgresql+asyncpg://", "postgresql://")
        .replace("postgresql+psycopg://", "postgresql://")
    )


async def _run(args: argparse.Namespace) -> None:
    dsn = _get_dsn()
    if not dsn:
        sys.exit("ERROR: DATABASE_URL or DATABASE_SYNC_URL not set in .env")

    if args.cmd in ("seed", "all"):
        from extraction.bulk_load.seed_companies import seed
        print("-- Step 1: Seeding companies table ----------------------------------")
        count = await seed(dsn)
        print(f"   -> Seeded {count} companies.\n")

    if args.cmd in ("load", "all"):
        from extraction.bulk_load.historical_loader import HistoricalLoader
        print("-- Step 2: Bulk loading historical prices ----------------------------")
        tickers = (
            [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
            if args.tickers
            else None
        )
        loader = HistoricalLoader(
            dsn=dsn,
            concurrency=args.concurrency,
            delay=args.delay,
            batch_size=args.batch_size,
            force=args.force,
        )
        report = await loader.run(tickers=tickers)

        print(f"   -> Loaded  : {report.loaded} tickers, {report.total_rows:,} rows")
        print(f"   -> Skipped : {report.skipped} (already have data -- use --force to reload)")
        print(f"   -> Failed  : {report.failed}")
        if report.failed > 0:
            failed_names = [r.ticker for r in report.results if r.error][:30]
            print(f"   -> Failed tickers: {', '.join(failed_names)}")
        print()

    if args.cmd in ("report", "all"):
        from extraction.bulk_load.gap_report import run_report, print_report, AMARSTOCK_DATA_START
        print("-- Step 3: Gap analysis ----------------------------------------------")
        from_date = (
            date.fromisoformat(args.from_date)
            if hasattr(args, "from_date") and args.from_date
            else AMARSTOCK_DATA_START
        )
        gaps = await run_report(dsn, from_date=from_date)
        print_report(gaps)


def _add_load_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--tickers",     default="", help="Comma-separated tickers (default: all)")
    p.add_argument("--concurrency", type=int,   default=10,      help="Parallel requests (default: 10)")
    p.add_argument("--delay",       type=float, default=1.0,     help="Seconds between requests per worker (default: 1.0)")
    p.add_argument("--batch-size",  type=int,   default=10_000,  dest="batch_size", help="DB insert batch size (default: 10000)")
    p.add_argument("--force",       action="store_true",          help="Re-load tickers that already have data")


def _add_report_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--from-date", default="",
        dest="from_date",
        help="Expected data start date YYYY-MM-DD (default: 2018-01-01)",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m extraction.bulk_load.run",
        description="Phase 1I: Bulk historical data loader for DSE stock prices",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("seed", help="Seed companies table from AmarStock live prices")

    load_p = sub.add_parser("load", help="Bulk-load historical prices for all/selected tickers")
    _add_load_args(load_p)

    report_p = sub.add_parser("report", help="Print gap analysis report")
    _add_report_args(report_p)

    all_p = sub.add_parser("all", help="Run seed → load → report in sequence")
    _add_load_args(all_p)
    _add_report_args(all_p)

    args = parser.parse_args()

    # Fill in defaults for subparsers that lack certain flags
    for attr, default in [
        ("tickers", ""), ("concurrency", 10), ("delay", 1.0),
        ("batch_size", 10_000), ("force", False), ("from_date", ""),
    ]:
        if not hasattr(args, attr):
            setattr(args, attr, default)

    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
