"""
Download full-market daily OHLCV CSVs from AmarStock.

Endpoint: POST https://www.amarstock.com/data/download/CSV
Payload:  QuotesType=1, date=YYYY-MM-DD

Files saved to: data/amarstock_csv/YYYY-MM-DD.csv
Progress tracked in: data/amarstock_csv/progress.json

Resume-safe: re-run any time; skips already-downloaded dates.
Empty responses (< 2 KB) = holiday/weekend — saved as .skip marker.

Usage:
    python pull_amarstock_csv.py               # resume from last position
    python pull_amarstock_csv.py --from 2020-01-01  # override start
    python pull_amarstock_csv.py --dry-run     # show plan, no downloads
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import date, timedelta
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
START_DATE = date(2013, 1, 1)
URL = "https://www.amarstock.com/data/download/CSV"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DSEIntelBot/1.0)",
    "Content-Type": "application/x-www-form-urlencoded",
}
OUT_DIR = Path("data/amarstock_csv")
PROGRESS_FILE = OUT_DIR / "progress.json"
EMPTY_THRESHOLD_BYTES = 2_000   # < 2 KB = holiday / no data
REQUEST_DELAY = 1.5             # seconds between requests
TIMEOUT = 30


# ---------------------------------------------------------------------------
# Progress helpers
# ---------------------------------------------------------------------------

def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        return json.loads(PROGRESS_FILE.read_text())
    return {"last_completed": None, "downloaded": 0, "skipped": 0, "errors": 0}


def save_progress(p: dict) -> None:
    PROGRESS_FILE.write_text(json.dumps(p, indent=2))


# ---------------------------------------------------------------------------
# Skip-day logic
# ---------------------------------------------------------------------------

def is_always_off(d: date) -> bool:
    """Friday is always off. Saturday might have data — don't pre-skip it."""
    return d.weekday() == 4  # 0=Mon … 4=Fri


# ---------------------------------------------------------------------------
# Core download
# ---------------------------------------------------------------------------

def download_day(client: httpx.Client, d: date) -> tuple[str, int]:
    """
    Returns (status, bytes_written).
    status: 'ok' | 'empty' | 'error'
    """
    iso = d.isoformat()
    csv_path = OUT_DIR / f"{iso}.csv"
    skip_path = OUT_DIR / f"{iso}.skip"

    if csv_path.exists() or skip_path.exists():
        return "already", 0

    try:
        resp = client.post(URL, data={"QuotesType": 1, "date": iso}, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
    except Exception as exc:
        print(f"  ERROR {iso}: {exc}")
        return "error", 0

    size = len(resp.content)

    if size < EMPTY_THRESHOLD_BYTES:
        skip_path.write_bytes(b"")
        print(f"  SKIP  {iso}  ({size} B — holiday/weekend)")
        return "empty", size

    csv_path.write_bytes(resp.content)
    lines = resp.text.count("\n")
    print(f"  OK    {iso}  {size:>7,} B  ~{lines} rows")
    return "ok", size


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def date_range(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def main() -> None:
    parser = argparse.ArgumentParser(description="AmarStock bulk CSV downloader")
    parser.add_argument("--from", dest="from_date", help="Override start date (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="Print plan without downloading")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    progress = load_progress()

    # Determine start date
    if args.from_date:
        start = date.fromisoformat(args.from_date)
    elif progress["last_completed"]:
        start = date.fromisoformat(progress["last_completed"]) + timedelta(days=1)
    else:
        start = START_DATE

    end = date.today()

    if start > end:
        print("Already up to date.")
        return

    total_days = (end - start).days + 1
    print(f"Range: {start} to {end}  ({total_days} calendar days)")
    print(f"Output: {OUT_DIR.resolve()}")
    if args.dry_run:
        print("Dry run — no downloads.")
        return

    with httpx.Client(follow_redirects=True) as client:
        for d in date_range(start, end):
            if is_always_off(d):
                progress["skipped"] += 1
                progress["last_completed"] = d.isoformat()
                continue

            status, _ = download_day(client, d)

            if status == "ok":
                progress["downloaded"] += 1
            elif status == "empty":
                progress["skipped"] += 1
            elif status == "error":
                progress["errors"] += 1
                # Don't advance last_completed on error so we retry next run
                save_progress(progress)
                continue

            progress["last_completed"] = d.isoformat()
            save_progress(progress)
            time.sleep(REQUEST_DELAY)

    print(
        f"\nDone. downloaded={progress['downloaded']}  "
        f"skipped={progress['skipped']}  errors={progress['errors']}"
    )


if __name__ == "__main__":
    main()
