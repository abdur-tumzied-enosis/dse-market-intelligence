# extraction/market_status.py
"""DSE market-session status: scrape → store (Postgres + Redis) → read.

Single source of truth for "is the market Open or Closed". Replaces the
clock-based guesses scattered across the API, scheduler, and LLM prompts. When
the scrape is unavailable, falls back to the Sun-Thu 10:00-14:30 clock and tags
the record source="clock" so callers can flag the value as estimated.
"""
from __future__ import annotations

from datetime import datetime

from extraction.normalizers import DHAKA_TZ


def normalize_status(raw_label: str | None) -> str:
    """Map a DSE status label to 'Open' or 'Closed'. Anything that is not an
    explicit 'open' reads as 'Closed' (fail-safe: we never invent an open
    market)."""
    if not raw_label:
        return "Closed"
    text = raw_label.lower()
    if "status" in text:
        text = text.split("status", 1)[1]
    text = text.strip(" :\t\r\n")
    return "Open" if text.startswith("open") else "Closed"


def clock_status(now: datetime) -> str:
    """Fallback status from the clock — DSE trades Sun-Thu, 10:00-14:30
    Asia/Dhaka. `now` must be tz-aware. Mon=0..Sun=6; Fri=4, Sat=5 closed."""
    if now.weekday() in (4, 5):
        return "Closed"
    minutes = now.hour * 60 + now.minute
    return "Open" if 600 <= minutes <= 870 else "Closed"
