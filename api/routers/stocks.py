# api/routers/stocks.py
from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from api.analysis.wyckoff import (
    LOOKBACK_BARS,
    clip_ranges_to_window,
    detect_wyckoff,
)
from api.deps import get_current_user, get_db
from api.schemas.common import PagedResponse
from api.schemas.stocks import (
    AnnouncementsResponse,
    CompanyRow,
    FundamentalsResponse,
    LivePrice,
    OHLCVResponse,
    PredictionsResponse,
    StockDetail,
    TrackRecordResponse,
    WyckoffResponse,
)
from extraction.base import AllAdaptersFailedError
from extraction.market_status import get_market_status
from extraction.registry import STREAMS

router = APIRouter(prefix="/stocks", tags=["stocks"])


def _decode_detail(value):
    """Decode a fundamental_detail JSONB column (asyncpg returns it as a str)."""
    if value is None:
        return None
    return json.loads(value) if isinstance(value, str) else value

_INTERVAL_TABLE = {"daily": "daily_ohlcv", "weekly": "weekly_ohlcv", "monthly": "monthly_ohlcv"}

FREE_FUNDAMENTALS_YEARS = 3
PRO_FUNDAMENTALS_YEARS = 10


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


def _f(value: Any) -> float | None:
    """JSON-safe float; None for unparseable or non-finite (Starlette uses
    allow_nan=False, so one NaN/inf 500s the whole response)."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _rating(score: Any) -> str:
    if score is None:
        return "N/A"
    s = float(score)
    if s >= 80:
        return "STRONG_BUY"
    if s >= 60:
        return "BUY"
    if s >= 40:
        return "HOLD"
    if s >= 20:
        return "SELL"
    return "STRONG_SELL"


async def _live_snapshot() -> list[dict]:
    """All-stock live snapshot from the live_prices stream, cached 75s and shared
    across per-ticker requests. 502 if every adapter in the chain fails."""
    cached = await _cache_get("cache:live_snapshot")
    if cached is not None:
        return cached
    try:
        result = await STREAMS["live_prices"].fetch()
    except AllAdaptersFailedError as exc:
        raise HTTPException(status_code=502, detail=f"live_prices unavailable: {exc}") from exc
    records = result.data.to_dict("records")
    await _cache_set("cache:live_snapshot", records, ttl=75)
    return records


@router.get("", response_model=PagedResponse[CompanyRow])
async def list_stocks(
    q: str | None = Query(None, max_length=64),
    sector: str | None = None,
    category: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    pool=Depends(get_db),
    _user=Depends(get_current_user),
):
    q = q.strip().lower() if q else None
    cache_key = f"cache:api:stocks:list:{q}:{sector}:{category}:{limit}:{offset}"
    cached = await _cache_get(cache_key)
    if cached:
        return cached

    where = "WHERE c.is_active = true"
    params: list = []
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params.append(f"%{escaped}%")
        n = len(params)
        where += f" AND (ticker ILIKE ${n} ESCAPE '\\' OR name ILIKE ${n} ESCAPE '\\')"
    if sector:
        params.append(sector)
        where += f" AND c.sector = ${len(params)}"
    if category:
        params.append(category)
        where += f" AND c.category = ${len(params)}"

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT c.ticker, c.name, c.sector, c.category, c.market_cap_bdt, c.is_active,
                   f.pe,
                   s.health_score,
                   p.close AS last_close,
                   p.change_pct
            FROM companies c
            LEFT JOIN LATERAL (
                SELECT pe FROM fundamentals WHERE ticker = c.ticker
                ORDER BY fetched_at DESC LIMIT 1
            ) f ON true
            LEFT JOIN LATERAL (
                SELECT health_score FROM stock_scores WHERE ticker = c.ticker
                ORDER BY scored_at DESC LIMIT 1
            ) s ON true
            LEFT JOIN LATERAL (
                SELECT close, change_pct FROM stock_prices WHERE ticker = c.ticker
                ORDER BY time DESC LIMIT 1
            ) p ON true
            {where}
            ORDER BY c.market_cap_bdt DESC NULLS LAST
            LIMIT {limit} OFFSET {offset}
            """,
            *params,
        )
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM companies c {where}", *params
        )

    items = []
    for r in rows:
        d = dict(r)
        d["rating"] = _rating(d.get("health_score"))
        items.append(d)
    result = {"items": items, "total": total, "limit": limit, "offset": offset}
    await _cache_set(cache_key, result, ttl=300)
    return result


@router.get("/{ticker}", response_model=StockDetail)
async def get_stock(ticker: str, pool=Depends(get_db), _user=Depends(get_current_user)):
    ticker = ticker.upper()
    cache_key = f"cache:api:stocks:detail:{ticker}"
    cached = await _cache_get(cache_key)
    if cached:
        return cached

    async with pool.acquire() as conn:
        company = await conn.fetchrow(
            """
            SELECT ticker, name, sector, category, market_cap_bdt, is_active,
                   listing_date, isin,
                   face_value, market_lot, electronic_share, debut_trading_date,
                   operational_status, short_loan_mn, long_loan_mn, loan_as_on,
                   credit_rating_st, credit_rating_lt, delisting_remark
            FROM companies WHERE ticker = $1
            """,
            ticker,
        )
        if not company:
            raise HTTPException(status_code=404, detail=f"Ticker {ticker} not found")

        latest_price = await conn.fetchrow(
            """
            SELECT close, change_pct, volume, value_bdt, high, low, time
            FROM stock_prices WHERE ticker = $1
            ORDER BY time DESC LIMIT 1
            """,
            ticker,
        )
        latest_fundamentals = await conn.fetchrow(
            """
            SELECT eps, nav, pe, cash_div_pct, stock_div_pct,
                   sponsor_pct, public_pct, fiscal_year
            FROM fundamentals WHERE ticker = $1
            ORDER BY fetched_at DESC LIMIT 1
            """,
            ticker,
        )
        health_score = await conn.fetchrow(
            """
            SELECT health_score, fundamental_score, momentum_score, scored_at,
                   fundamental_detail
            FROM stock_scores WHERE ticker = $1
            ORDER BY scored_at DESC LIMIT 1
            """,
            ticker,
        )

    health_score_dict: dict | None = None
    if health_score:
        health_score_dict = dict(health_score)
        health_score_dict["fundamental_detail"] = _decode_detail(
            health_score_dict.get("fundamental_detail"))

    result = {
        "company": dict(company),
        "latest_price": dict(latest_price) if latest_price else None,
        "fundamentals": dict(latest_fundamentals) if latest_fundamentals else None,
        "health_score": health_score_dict,
    }
    await _cache_set(cache_key, result, ttl=300)
    return result


@router.get("/{ticker}/prices", response_model=OHLCVResponse)
async def get_prices(
    ticker: str,
    from_date: date | None = Query(None, alias="from"),
    to_date: date | None = Query(None, alias="to"),
    interval: str = Query("daily", pattern="^(daily|weekly|monthly)$"),
    pool=Depends(get_db),
    _user=Depends(get_current_user),
):
    ticker = ticker.upper()
    cache_key = f"cache:api:stocks:prices:{ticker}:{from_date}:{to_date}:{interval}"
    cached = await _cache_get(cache_key)
    if cached:
        return cached

    table = _INTERVAL_TABLE[interval]
    where_parts = ["ticker = $1"]
    params: list = [ticker]
    if from_date:
        params.append(from_date)
        where_parts.append(f"day >= ${len(params)}")
    if to_date:
        params.append(to_date)
        where_parts.append(f"day <= ${len(params)}")
    where = " AND ".join(where_parts)

    async with pool.acquire() as conn:
        exists = await conn.fetchrow("SELECT 1 FROM companies WHERE ticker = $1", ticker)
        if not exists:
            raise HTTPException(status_code=404, detail=f"Ticker {ticker} not found")
        rows = await conn.fetch(
            f"SELECT day, open, high, low, close, volume, value_bdt FROM {table} WHERE {where} ORDER BY day DESC LIMIT 800",
            *params,
        )

    result = {"ticker": ticker, "interval": interval, "items": [dict(r) for r in rows]}
    await _cache_set(cache_key, result, ttl=3600)
    return result


@router.get("/{ticker}/wyckoff", response_model=WyckoffResponse)
async def get_wyckoff(
    ticker: str,
    from_date: date | None = Query(None, alias="from"),
    to_date: date | None = Query(None, alias="to"),
    interval: str = Query("daily", pattern="^(daily|weekly|monthly)$"),
    pool=Depends(get_db),
    _user=Depends(get_current_user),
):
    ticker = ticker.upper()
    cache_key = f"cache:api:stocks:wyckoff:{ticker}:{from_date}:{to_date}:{interval}"
    cached = await _cache_get(cache_key)
    if cached:
        return cached

    table = _INTERVAL_TABLE[interval]
    params: list = [ticker]
    upper = ""
    if to_date:
        params.append(to_date)
        upper = f" AND day <= ${len(params)}"

    if from_date:
        # Fetch LOOKBACK_BARS of history *before* the visible window so the
        # detector has lead-in for the rolling-band warm-up + prior-trend
        # classification; without it short windows (1M/3M) detect nothing.
        # The extra bars are clamped back off the response below.
        params.append(from_date)
        from_idx = len(params)
        sql = (
            f"SELECT day, open, high, low, close, volume FROM ("
            f" SELECT day, open, high, low, close, volume FROM {table}"
            f" WHERE ticker = $1 AND day < ${from_idx}{upper}"
            f" ORDER BY day DESC LIMIT {LOOKBACK_BARS}"
            f") buf"
            f" UNION ALL"
            f" SELECT day, open, high, low, close, volume FROM {table}"
            f" WHERE ticker = $1 AND day >= ${from_idx}{upper}"
            f" ORDER BY day"
        )
    else:
        sql = (
            f"SELECT day, open, high, low, close, volume FROM {table}"
            f" WHERE ticker = $1{upper} ORDER BY day"
        )

    async with pool.acquire() as conn:
        exists = await conn.fetchrow("SELECT 1 FROM companies WHERE ticker = $1", ticker)
        if not exists:
            raise HTTPException(status_code=404, detail=f"Ticker {ticker} not found")
        rows = await conn.fetch(sql, *params)

    bars = [
        {
            "day": r["day"],
            "open": _f(r["open"]) or 0.0,
            "high": _f(r["high"]) or 0.0,
            "low": _f(r["low"]) or 0.0,
            "close": _f(r["close"]) or 0.0,
            "volume": _f(r["volume"]) or 0.0,
        }
        for r in rows
    ]
    detected = clip_ranges_to_window(detect_wyckoff(bars), from_date)

    result = {
        "ticker": ticker,
        "interval": interval,
        "ranges": [
            {
                "start_day": rng.start_day,
                "end_day": rng.end_day,
                "phase": rng.phase,
                "phase_help": rng.phase_help,
                "confidence": rng.confidence,
                "support": rng.support,
                "resistance": rng.resistance,
                "events": [
                    {
                        "day": ev.day,
                        "type": ev.type,
                        "price": ev.price,
                        "label": ev.label,
                        "help": ev.help,
                    }
                    for ev in rng.events
                ],
            }
            for rng in detected
        ],
    }
    await _cache_set(cache_key, result, ttl=3600)
    return result


@router.get("/{ticker}/fundamentals", response_model=FundamentalsResponse)
async def get_fundamentals(ticker: str, pool=Depends(get_db), user=Depends(get_current_user)):
    ticker = ticker.upper()
    max_years = (
        FREE_FUNDAMENTALS_YEARS if user["tier"] == "free" else PRO_FUNDAMENTALS_YEARS
    )
    cache_key = f"cache:api:stocks:fundamentals:{ticker}:{max_years}"
    cached = await _cache_get(cache_key)
    if cached:
        return cached

    async with pool.acquire() as conn:
        exists = await conn.fetchrow("SELECT 1 FROM companies WHERE ticker = $1", ticker)
        if not exists:
            raise HTTPException(status_code=404, detail=f"Ticker {ticker} not found")
        rows = await conn.fetch(
            """
            SELECT fiscal_year, eps, nav, pe, cash_div_pct, stock_div_pct,
                   sponsor_pct, public_pct,
                   net_profit_bdt, total_comprehensive_income_bdt,
                   dividend_yield_pct, eps_basis,
                   fetched_at
            FROM fundamentals WHERE ticker = $1
            ORDER BY fiscal_year DESC NULLS LAST, fetched_at DESC
            LIMIT $2
            """,
            ticker,
            max_years,
        )

    items = [dict(r) for r in rows]
    result = {
        "ticker": ticker,
        "items": items,
        "max_years": max_years,
        "is_truncated": user["tier"] == "free" and len(items) >= max_years,
    }
    await _cache_set(cache_key, result, ttl=86400)
    return result


@router.get("/{ticker}/track-record", response_model=TrackRecordResponse)
async def get_track_record(ticker: str, pool=Depends(get_db), user=Depends(get_current_user)):
    """Analyst track-record bundle: shareholding trend, corporate-action history,
    quarterly EPS — one round trip for the stock page's track-record panels.

    Free tier sees corporate actions for the FREE_FUNDAMENTALS_YEARS most recent
    fiscal years (matches the fundamentals paywall); shareholding (max 3 snapshots
    on the source page) and current-year quarterly rows are not gated.
    """
    ticker = ticker.upper()
    is_free = user["tier"] == "free"
    cache_key = f"cache:api:stocks:track_record:{ticker}:{user['tier']}"
    cached = await _cache_get(cache_key)
    if cached:
        return cached

    async with pool.acquire() as conn:
        exists = await conn.fetchrow("SELECT 1 FROM companies WHERE ticker = $1", ticker)
        if not exists:
            raise HTTPException(status_code=404, detail=f"Ticker {ticker} not found")
        shareholding = await conn.fetch(
            """
            SELECT as_on_date, sponsor_pct, govt_pct, institution_pct,
                   foreign_pct, public_pct
            FROM shareholding_history WHERE ticker = $1
            ORDER BY as_on_date
            """,
            ticker,
        )
        actions = await conn.fetch(
            """
            SELECT fiscal_year, action_type, value_pct, ratio_text, ratio
            FROM corporate_actions WHERE ticker = $1
            ORDER BY fiscal_year DESC, action_type
            """,
            ticker,
        )
        quarterly = await conn.fetch(
            """
            SELECT fiscal_year, quarter, eps_basic, eps_diluted, period_end_price
            FROM fundamentals_quarterly WHERE ticker = $1
            ORDER BY fiscal_year DESC, quarter
            """,
            ticker,
        )

    action_items = [dict(r) for r in actions]
    action_years = sorted({r["fiscal_year"] for r in action_items}, reverse=True)
    is_truncated = False
    if is_free and len(action_years) > FREE_FUNDAMENTALS_YEARS:
        visible = set(action_years[:FREE_FUNDAMENTALS_YEARS])
        action_items = [r for r in action_items if r["fiscal_year"] in visible]
        is_truncated = True

    result = {
        "ticker": ticker,
        "shareholding": [dict(r) for r in shareholding],
        "actions": action_items,
        "quarterly": [dict(r) for r in quarterly],
        "max_action_years": FREE_FUNDAMENTALS_YEARS if is_free else len(action_years) or 1,
        "is_truncated": is_truncated,
    }
    await _cache_set(cache_key, result, ttl=86400)
    return result


@router.get("/{ticker}/predictions", response_model=PredictionsResponse)
async def get_predictions(ticker: str, pool=Depends(get_db), _user=Depends(get_current_user)):
    ticker = ticker.upper()
    cache_key = f"cache:api:stocks:predictions:{ticker}"
    cached = await _cache_get(cache_key)
    if cached:
        return cached

    async with pool.acquire() as conn:
        exists = await conn.fetchrow("SELECT 1 FROM companies WHERE ticker = $1", ticker)
        if not exists:
            raise HTTPException(status_code=404, detail=f"Ticker {ticker} not found")
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (horizon_days)
                horizon_days, predicted_direction, confidence, target_price,
                predicted_at, model_version
            FROM ml_predictions WHERE ticker = $1
            ORDER BY horizon_days, predicted_at DESC
            """,
            ticker,
        )

    result = {"ticker": ticker, "predictions": [dict(r) for r in rows]}
    await _cache_set(cache_key, result, ttl=14400)
    return result


@router.get("/{ticker}/announcements", response_model=AnnouncementsResponse)
async def get_announcements(
    ticker: str,
    announcement_type: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    pool=Depends(get_db),
    _user=Depends(get_current_user),
):
    ticker = ticker.upper()
    cache_key = f"cache:api:stocks:announcements:{ticker}:{announcement_type}:{limit}:{offset}"
    cached = await _cache_get(cache_key)
    if cached:
        return cached

    where = "WHERE ticker = $1"
    params: list = [ticker]
    if announcement_type:
        params.append(announcement_type)
        where += f" AND announcement_type = ${len(params)}"

    async with pool.acquire() as conn:
        exists = await conn.fetchrow("SELECT 1 FROM companies WHERE ticker = $1", ticker)
        if not exists:
            raise HTTPException(status_code=404, detail=f"Ticker {ticker} not found")
        rows = await conn.fetch(
            f"""
            SELECT id, published_at, headline, announcement_type,
                   eps_value, eps_period, dividend_cash_pct, dividend_stock_pct
            FROM company_announcements {where}
            ORDER BY published_at DESC
            LIMIT {limit} OFFSET {offset}
            """,
            *params,
        )
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM company_announcements {where}", *params
        )

    result = {"ticker": ticker, "total": total, "items": [dict(r) for r in rows]}
    await _cache_set(cache_key, result, ttl=3600)
    return result


@router.get("/{ticker}/score")
async def get_health_score(ticker: str, pool=Depends(get_db), _user=Depends(get_current_user)):
    ticker = ticker.upper()
    cache_key = f"cache:api:stocks:score:{ticker}"
    cached = await _cache_get(cache_key)
    if cached:
        return cached

    async with pool.acquire() as conn:
        exists = await conn.fetchrow("SELECT 1 FROM companies WHERE ticker = $1", ticker)
        if not exists:
            raise HTTPException(status_code=404, detail=f"Ticker {ticker} not found")
        row = await conn.fetchrow(
            """
            SELECT health_score, fundamental_score, momentum_score,
                   valuation_score, sentiment_score, scored_at, model_version,
                   fundamental_detail
            FROM stock_scores WHERE ticker = $1
            ORDER BY scored_at DESC LIMIT 1
            """,
            ticker,
        )

    if row:
        result = dict(row)
        result["fundamental_detail"] = _decode_detail(result.get("fundamental_detail"))
    else:
        result = {"ticker": ticker, "health_score": None}
    await _cache_set(cache_key, result, ttl=14400)
    return result


@router.get("/{ticker}/live", response_model=LivePrice)
async def get_live_price(ticker: str, pool=Depends(get_db), _user=Depends(get_current_user)):
    ticker = ticker.upper()
    async with pool.acquire() as conn:
        exists = await conn.fetchrow("SELECT 1 FROM companies WHERE ticker = $1", ticker)
    if not exists:
        raise HTTPException(status_code=404, detail=f"Ticker {ticker} not found")

    session = await get_market_status()
    status = session["status"]
    as_of = datetime.now(UTC)

    for rec in await _live_snapshot():
        if str(rec.get("ticker") or "").strip().upper() != ticker:
            continue
        ltp = _f(rec.get("ltp") if rec.get("ltp") is not None else rec.get("close"))
        prev_close = _f(rec.get("prev_close"))
        # dse_direct feed carries no %CHANGE column → compute from prev_close
        # (same fallback the ingest path uses in _live_records_to_rows).
        change_pct = _f(rec.get("change_pct"))
        if change_pct is None and ltp is not None and prev_close not in (None, 0):
            change_pct = (ltp - prev_close) / prev_close * 100.0
        value = rec.get("value_bdt")
        if value is None and rec.get("value_mn") is not None:
            value = _f(rec.get("value_mn"))
            value = value * 1_000_000 if value is not None else None
        return {
            "ticker": ticker,
            "available": True,
            "ltp": ltp,
            "high": _f(rec.get("high")),
            "low": _f(rec.get("low")),
            "prev_close": prev_close,
            "change_pct": _f(change_pct),
            "volume": _f(rec.get("volume")),
            "value_bdt": _f(value),
            "market_status": status,
            "status_source": session["source"],
            "as_of": as_of,
        }

    return {
        "ticker": ticker,
        "available": False,
        "ltp": None, "high": None, "low": None, "prev_close": None,
        "change_pct": None, "volume": None, "value_bdt": None,
        "market_status": status,
        "status_source": session["source"],
        "as_of": as_of,
    }
