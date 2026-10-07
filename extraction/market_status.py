# extraction/market_status.py
"""DSE market-session status: scrape → store (Postgres + Redis) → read.

Single source of truth for "is the market Open or Closed". Replaces the
clock-based guesses scattered across the API, scheduler, and LLM prompts. When
the scrape is unavailable, falls back to the Sun-Thu 10:00-14:30 clock and tags
the record source="clock" so callers can flag the value as estimated.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from extraction.normalizers import DHAKA_TZ
from mgmt.cache import cache_get, cache_set

logger = logging.getLogger(__name__)

_REDIS_KEY = "cache:market:session"
_SESSION_TTL = 86_400  # 1 day — refreshed on every scrape
_sync_client = None  # lazy sync redis client for prompt builders


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


async def _get_pool():
    """Indirection so tests can patch the DB pool without a live database."""
    from db.pool import get_pool
    return await get_pool()


def _sync_redis():
    """Lazily build a synchronous redis client for the (sync) LLM prompt builders."""
    global _sync_client
    if _sync_client is None:
        import redis  # redis-py sync client

        from mgmt.config import get_settings
        _sync_client = redis.Redis.from_url(get_settings().redis_url, decode_responses=True)
    return _sync_client


async def _write(record: dict) -> None:
    """Persist a session record: append a DB row and mirror to Redis. Both are
    best-effort — a refresh must never crash the scheduler job."""
    from datetime import date as _date
    try:
        pool = await _get_pool()
        await pool.execute(
            """
            INSERT INTO market_session (session_date, status, raw_label, source, checked_at)
            VALUES ($1, $2, $3, $4, $5)
            """,
            _date.fromisoformat(record["session_date"]),
            record["status"],
            record["raw_label"],
            record["source"],
            datetime.fromisoformat(record["checked_at"]),
        )
    except Exception as exc:
        logger.warning("market_status: DB write failed error=%s", exc)
    try:
        await cache_set(_REDIS_KEY, record, ttl=_SESSION_TTL)
    except Exception as exc:
        logger.warning("market_status: redis write failed error=%s", exc)


async def refresh_market_status() -> dict:
    """Scrape DSE status → normalize → persist (DB + Redis) → return the record.
    On any scrape/parse failure, derive the status from the clock and tag
    source='clock'."""
    # Lazy import: extraction.registry imports the adapter, which imports this
    # module — a module-level import here would be circular.
    from extraction.registry import STREAMS

    now = datetime.now(DHAKA_TZ)
    try:
        result = await STREAMS["market_status"].fetch()
        rec = result.data.to_dict("records")[0]
        status = rec["status"]
        raw_label = rec.get("raw_label")
        source = "dse_direct"
    except Exception as exc:
        logger.warning("market_status: refresh failed, clock fallback error=%s", exc)
        status = clock_status(now)
        raw_label = None
        source = "clock"
    record = {
        "status": status,
        "source": source,
        "raw_label": raw_label,
        "session_date": now.date().isoformat(),
        "checked_at": now.isoformat(),
    }
    await _write(record)
    return record


async def get_market_status() -> dict:
    """Read path: Redis → latest DB row → clock fallback. Never raises."""
    try:
        cached = await cache_get(_REDIS_KEY)
        if cached:
            return cached
    except Exception as exc:
        logger.debug("market_status: redis read failed error=%s", exc)
    try:
        pool = await _get_pool()
        row = await pool.fetchrow(
            "SELECT status, source, checked_at FROM market_session ORDER BY checked_at DESC LIMIT 1"
        )
        checked = row["checked_at"] if row else None
        # A row from a previous BD day is stale (e.g. "Open" left behind by an
        # outage) — fall through to the clock instead of trusting it.
        if row and hasattr(checked, "astimezone") and \
                checked.astimezone(DHAKA_TZ).date() == datetime.now(DHAKA_TZ).date():
            return {
                "status": row["status"],
                "source": row["source"],
                "checked_at": checked.isoformat() if hasattr(checked, "isoformat") else checked,
            }
    except Exception as exc:
        logger.debug("market_status: db read failed error=%s", exc)
    now = datetime.now(DHAKA_TZ)
    return {"status": clock_status(now), "source": "clock", "checked_at": now.isoformat()}


def get_market_status_sync() -> dict:
    """Synchronous read for the LLM prompt builders: Redis → clock fallback."""
    try:
        raw = _sync_redis().get(_REDIS_KEY)
        if raw:
            return json.loads(raw)
    except Exception as exc:
        logger.debug("market_status: sync redis read failed error=%s", exc)
    now = datetime.now(DHAKA_TZ)
    return {"status": clock_status(now), "source": "clock", "checked_at": now.isoformat()}
