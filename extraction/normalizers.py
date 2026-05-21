"""Shared normalization utilities used across all adapters."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

DHAKA_TZ = ZoneInfo("Asia/Dhaka")

_CLEAN_NUMBER_RE = re.compile(r"[^\d.\-]")


def to_decimal(value: object) -> Decimal | None:
    """Convert BDT-formatted string or numeric to Decimal. Returns None on failure."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    cleaned = _CLEAN_NUMBER_RE.sub("", str(value).strip())
    if not cleaned or cleaned in ("-", "."):
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def to_utc(dt: datetime | None, *, assume_dhaka: bool = True) -> datetime | None:
    """Ensure datetime is UTC. If naive and assume_dhaka=True, treat as Dhaka time."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        if assume_dhaka:
            dt = dt.replace(tzinfo=DHAKA_TZ)
        else:
            dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def normalize_ticker(ticker: str) -> str:
    """Upper-case, strip whitespace."""
    return ticker.strip().upper()


def bd_date_str_to_utc(date_str: str, fmt: str = "%d-%b-%Y") -> datetime | None:
    """Parse BD date string to UTC midnight. Tries fmt then ISO 8601 (YYYY-MM-DD)."""
    s = date_str.strip() if date_str else ""
    for f in (fmt, "%Y-%m-%d"):
        try:
            return to_utc(datetime.strptime(s, f), assume_dhaka=True)
        except (ValueError, AttributeError):
            continue
    return None
