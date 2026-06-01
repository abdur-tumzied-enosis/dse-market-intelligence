# api/routers/market.py
from __future__ import annotations

import asyncio
import json as _json
import math
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from api.deps import get_current_user, get_db
from api.schemas.market import HeatmapItem, MarketIndices, MarketSummary
from extraction.base import AllAdaptersFailedError
from extraction.normalizers import DHAKA_TZ
from extraction.registry import STREAMS

router = APIRouter(prefix="/market", tags=["market"])


async def _cache_get(key: str) -> Any | None:
    try:
        from mgmt.cache import cache_get
        return await cache_get(key)
    except Exception:
        return None


async def _cache_set(key: str, value: Any, ttl: int) -> None:
    try:
        from mgmt.cache import cache_set
        await cache_set(key, value, ttl)
    except Exception:
        pass


# ── Source: registry DataStreams (failover chains), not raw AmarStock ──────
# live_prices:    bdshare → amarstock → dse_direct
# market_indices: bdshare_market_info
# AmarStock is a priority-2 fallback inside these chains, never a hard dep.


async def _fetch_live_records() -> list[dict]:
    """All-stock live snapshot via the live_prices stream. 502 if every adapter fails."""
    try:
        result = await STREAMS["live_prices"].fetch()
    except AllAdaptersFailedError as exc:
        raise HTTPException(status_code=502, detail=f"live_prices unavailable: {exc}") from exc
    return result.data.to_dict("records")


async def _companies_map(pool) -> dict[str, dict]:
    """ticker → {name, sector} for display enrichment of source rows."""
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT ticker, name, sector FROM companies WHERE is_active = true")
    return {r["ticker"]: {"name": r["name"], "sector": r["sector"]} for r in rows}


def _f(value: Any) -> float | None:
    """Coerce to a JSON-safe float. Returns None for unparseable or non-finite
    values (NaN/Infinity) — Starlette renders responses with allow_nan=False, so
    a single non-finite float anywhere in the payload 500s the whole request."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _rec_ltp(rec: dict) -> float | None:
    """Last traded price — amarstock exposes 'ltp', other sources use 'close'."""
    return _f(rec.get("ltp") if rec.get("ltp") is not None else rec.get("close"))


def _rec_value(rec: dict) -> float | None:
    """Traded value. Within one response all rows come from one adapter, so the
    unit is internally consistent (used only for relative treemap sizing)."""
    v = rec.get("value_bdt")
    if v is None:
        v = rec.get("value_mn")
    return _f(v)


def _rec_change_pct(rec: dict) -> float | None:
    """Percent change. dse_direct's feed has no %CHANGE column — derive it from
    prev_close when the source omits it (same fallback as the ingest path)."""
    cp = _f(rec.get("change_pct"))
    if cp is not None:
        return cp
    ltp = _rec_ltp(rec)
    prev = _f(rec.get("prev_close"))
    if ltp is not None and prev not in (None, 0):
        return (ltp - prev) / prev * 100.0
    return None


def _market_status(now: datetime | None = None) -> str:
    """DSE trades Sun–Thu, 10:00–14:30 Asia/Dhaka. Derived from the clock since
    bdshare market_info carries no status flag."""
    now = now or datetime.now(DHAKA_TZ)
    # Mon=0 .. Sun=6; trading days are Sun(6), Mon..Thu(0..3)
    if now.weekday() in (4, 5):  # Fri, Sat
        return "Closed"
    minutes = now.hour * 60 + now.minute
    return "Open" if 600 <= minutes <= 870 else "Closed"


_MA_WINDOW = 50   # target moving-average window (trading days)
_MIN_DAYS = 5     # below this, refuse to call a regime


def compute_regime(dsex_series: list[float], as_of: str | None) -> dict:
    """Bull/Bear regime from a DSEX series ordered most-recent-first.

    Bull when the latest DSEX value is >= the mean of the last `window` values
    (window = min(50, len)). Marked provisional while the window is short, and
    Unknown/insufficient below _MIN_DAYS so a thin series never forces a call.
    """
    n = len(dsex_series)
    if n < _MIN_DAYS:
        return {
            "regime": "Unknown",
            "dsex": dsex_series[0] if n else None,
            "ma": None,
            "window": n,
            "provisional": True,
            "distance_pct": None,
            "as_of": as_of,
            "data_status": "insufficient",
        }
    window = min(_MA_WINDOW, n)
    dsex = dsex_series[0]
    ma = sum(dsex_series[:window]) / window
    provisional = window < _MA_WINDOW
    distance_pct = (dsex - ma) / ma * 100.0 if ma else None
    return {
        "regime": "Bull" if dsex >= ma else "Bear",
        "dsex": dsex,
        "ma": ma,
        "window": window,
        "provisional": provisional,
        "distance_pct": distance_pct,
        "as_of": as_of,
        "data_status": "provisional" if provisional else "ok",
    }


@router.get("/indices", response_model=MarketIndices)
async def market_indices(_user=Depends(get_current_user)):
    cache_key = "cache:api:market:indices"
    cached = await _cache_get(cache_key)
    if cached:
        return cached
    result = await _build_indices()
    await _cache_set(cache_key, result, ttl=60)
    return result


async def _build_indices() -> dict:
    """Index values from market_indices stream; breadth from the live snapshot;
    status from the trading-hours clock. Accepts partial breadth if live fails."""
    try:
        idx_result = await STREAMS["market_indices"].fetch()
    except AllAdaptersFailedError as exc:
        raise HTTPException(status_code=502, detail=f"market_indices unavailable: {exc}") from exc
    idx_map = {row["index_name"]: row for row in idx_result.data.to_dict("records")}

    def g(name: str, field: str) -> float:
        row = idx_map.get(name)
        return _f(row[field]) or 0.0 if row else 0.0

    advance = decline = unchanged = 0
    try:
        for rec in await _fetch_live_records():
            cp = _rec_change_pct(rec)
            if cp is None:
                continue
            if cp > 0:
                advance += 1
            elif cp < 0:
                decline += 1
            else:
                unchanged += 1
    except HTTPException:
        pass  # breadth is best-effort; indices values still returned

    return {
        "dsex_value": g("DSEX", "value"),
        "dsex_change_pct": g("DSEX", "change_pct"),
        "ds30_value": g("DS30", "value"),
        "ds30_change_pct": g("DS30", "change_pct"),
        "dses_value": g("DSES", "value"),
        "dses_change_pct": g("DSES", "change_pct"),
        "market_status": _market_status(),
        "advance": advance,
        "decline": decline,
        "unchanged": unchanged,
    }


@router.get("/summary", response_model=MarketSummary)
async def market_summary(pool=Depends(get_db), _user=Depends(get_current_user)):
    cache_key = "cache:api:market:summary"
    cached = await _cache_get(cache_key)
    if cached:
        return cached

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            WITH ranked AS (
                SELECT sp.ticker, sp.close, sp.volume, sp.value_bdt,
                       DENSE_RANK() OVER (PARTITION BY sp.ticker ORDER BY sp.time::date DESC) AS day_rank
                FROM stock_prices sp
                JOIN companies c ON c.ticker = sp.ticker
                WHERE c.is_active = true
            ),
            today AS (
                SELECT ticker, MAX(close) AS close, MAX(volume) AS volume, MAX(value_bdt) AS value_bdt
                FROM ranked WHERE day_rank = 1
                GROUP BY ticker
            ),
            yesterday AS (
                SELECT ticker, MAX(close) AS close
                FROM ranked WHERE day_rank = 2
                GROUP BY ticker
            ),
            computed AS (
                SELECT t.ticker, t.volume, t.value_bdt,
                    CASE WHEN y.close IS NOT NULL AND y.close > 0
                        THEN ROUND(((t.close - y.close) / y.close * 100)::numeric, 4)
                        ELSE NULL
                    END AS change_pct
                FROM today t
                LEFT JOIN yesterday y ON y.ticker = t.ticker
            )
            SELECT
                COUNT(*)                                        AS total_stocks,
                COUNT(*) FILTER (WHERE change_pct > 0)         AS advance,
                COUNT(*) FILTER (WHERE change_pct < 0)         AS decline,
                COUNT(*) FILTER (WHERE change_pct = 0 OR change_pct IS NULL) AS unchanged,
                SUM(volume)                                     AS total_volume,
                SUM(value_bdt)                                  AS total_value_bdt,
                ROUND(AVG(change_pct), 4)                       AS avg_change_pct
            FROM computed
            """
        )

    result = dict(row)
    await _cache_set(cache_key, result, ttl=300)
    return result


@router.get("/movers")
async def market_movers(
    n: int = Query(10, ge=1, le=50),
    pool=Depends(get_db),
    _user=Depends(get_current_user),
):
    cache_key = f"cache:api:market:movers:{n}"
    cached = await _cache_get(cache_key)
    if cached:
        return cached

    records = await _fetch_live_records()
    cmap = await _companies_map(pool)

    movers: list[dict] = []
    for rec in records:
        ticker = str(rec.get("ticker") or "").strip()
        change_pct = _rec_change_pct(rec)
        close = _f(rec.get("close") if rec.get("close") is not None else rec.get("ltp"))
        if not ticker or change_pct is None or close is None:
            continue
        movers.append({
            "ticker": ticker,
            "name": cmap.get(ticker, {}).get("name") or rec.get("full_name") or ticker,
            "close": close,
            "change_pct": change_pct,
        })

    movers.sort(key=lambda x: x["change_pct"], reverse=True)
    result = {
        "gainers": movers[:n],
        "losers": movers[:-n - 1:-1],
    }
    await _cache_set(cache_key, result, ttl=120)
    return result


@router.get("/heatmap", response_model=list[HeatmapItem])
async def market_heatmap(pool=Depends(get_db), _user=Depends(get_current_user)):
    cache_key = "cache:api:market:heatmap"
    cached = await _cache_get(cache_key)
    if cached:
        return cached

    records = await _fetch_live_records()
    cmap = await _companies_map(pool)

    result: list[dict] = []
    for rec in records:
        ticker = str(rec.get("ticker") or "").strip()
        if not ticker:
            continue
        info = cmap.get(ticker, {})
        # market_cap kept in millions (frontend renders market_cap/1000 → "B");
        # amarstock fallback supplies it, bdshare/dse_direct do not → None.
        mc = rec.get("market_cap_mn")
        result.append({
            "ticker": ticker,
            "name": info.get("name") or rec.get("full_name") or ticker,
            "sector": info.get("sector") or rec.get("sector") or "Other",
            "change_pct": _rec_change_pct(rec),
            "ltp": _rec_ltp(rec),
            "value_bdt": _rec_value(rec),
            "market_cap": _f(mc),
        })

    result.sort(key=lambda x: (x["sector"], -(x["value_bdt"] or 0)))
    await _cache_set(cache_key, result, ttl=120)
    return result


async def _default_get_indices() -> dict:
    """Default indices fetch for the SSE stream: cache check, then registry build."""
    cache_key = "cache:api:market:indices"
    cached = await _cache_get(cache_key)
    if cached:
        return cached
    result = await _build_indices()
    await _cache_set(cache_key, result, ttl=60)
    return result


# Cap a single SSE connection's lifetime. The EventSource client reconnects
# automatically, so bounding this frees the connection periodically and stops a
# never-ending response from blocking `uvicorn --reload` graceful shutdown.
_STREAM_MAX_SECONDS = 300


async def _generate_market_events(
    request: Request | None = None,
    get_indices_fn=_default_get_indices,
    interval: int = 30,
):
    """Async generator that yields SSE-formatted market index events.

    Ends when: the market closes (one final event), the client disconnects, the
    server cancels the task (shutdown/reload), or the connection exceeds
    _STREAM_MAX_SECONDS. Honoring disconnects + bounding the lifetime keeps a
    long-lived stream from blocking server reload/shutdown.
    """
    elapsed = 0
    while True:
        if request is not None and await request.is_disconnected():
            return
        try:
            data = await get_indices_fn()
            yield f"data: {_json.dumps(data)}\n\n"
            if data.get("market_status") != "Open":
                return  # market closed — client will reconnect later
        except (asyncio.CancelledError, GeneratorExit):
            return
        except Exception as exc:
            yield f"data: {_json.dumps({'error': 'fetch_failed', 'detail': type(exc).__name__})}\n\n"
        if interval <= 0:
            return  # test mode: yield once then stop
        # Sleep in 1s slices so a client disconnect (or task cancellation on
        # server shutdown) is noticed within ~1s instead of stuck in one long await.
        for _ in range(interval):
            if request is not None and await request.is_disconnected():
                return
            await asyncio.sleep(1)
            elapsed += 1
        if elapsed >= _STREAM_MAX_SECONDS:
            return  # cap lifetime; EventSource client reconnects


@router.get("/stream")
async def market_stream(request: Request):
    """SSE stream of market indices — no auth required (public data)."""
    return StreamingResponse(
        _generate_market_events(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
