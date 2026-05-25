# api/routers/stocks.py
from __future__ import annotations
from datetime import date
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query
from api.deps import get_current_user, get_db
from api.schemas.common import PagedResponse
from api.schemas.stocks import (
    AnnouncementsResponse, AnnouncementRow, CompanyRow, FundamentalsResponse,
    FundamentalsRow, HealthScoreRow, LatestFundamentals, LatestPrice,
    OHLCVResponse, PredictionRow, PredictionsResponse, StockDetail,
)

router = APIRouter(prefix="/stocks", tags=["stocks"])

_INTERVAL_TABLE = {"daily": "daily_ohlcv", "weekly": "weekly_ohlcv", "monthly": "monthly_ohlcv"}


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


@router.get("", response_model=PagedResponse[CompanyRow])
async def list_stocks(
    sector: str | None = None,
    category: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    pool=Depends(get_db),
    _user=Depends(get_current_user),
):
    cache_key = f"cache:api:stocks:list:{sector}:{category}:{limit}:{offset}"
    cached = await _cache_get(cache_key)
    if cached:
        return cached

    where = "WHERE is_active = true"
    params: list = []
    if sector:
        params.append(sector)
        where += f" AND sector = ${len(params)}"
    if category:
        params.append(category)
        where += f" AND category = ${len(params)}"

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT ticker, name, sector, category, market_cap_bdt, is_active
            FROM companies {where}
            ORDER BY market_cap_bdt DESC NULLS LAST
            LIMIT {limit} OFFSET {offset}
            """,
            *params,
        )
        total = await conn.fetchval(f"SELECT COUNT(*) FROM companies {where}", *params)

    result = {
        "items": [dict(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }
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
                   listing_date, isin
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
            SELECT health_score, fundamental_score, momentum_score, scored_at
            FROM stock_scores WHERE ticker = $1
            ORDER BY scored_at DESC LIMIT 1
            """,
            ticker,
        )

    result = {
        "company": dict(company),
        "latest_price": dict(latest_price) if latest_price else None,
        "fundamentals": dict(latest_fundamentals) if latest_fundamentals else None,
        "health_score": dict(health_score) if health_score else None,
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
            f"SELECT day, open, high, low, close, volume, value_bdt FROM {table} WHERE {where} ORDER BY day DESC LIMIT 500",
            *params,
        )

    result = {"ticker": ticker, "interval": interval, "items": [dict(r) for r in rows]}
    await _cache_set(cache_key, result, ttl=3600)
    return result


@router.get("/{ticker}/fundamentals", response_model=FundamentalsResponse)
async def get_fundamentals(ticker: str, pool=Depends(get_db), _user=Depends(get_current_user)):
    ticker = ticker.upper()
    cache_key = f"cache:api:stocks:fundamentals:{ticker}"
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
                   sponsor_pct, public_pct, fetched_at
            FROM fundamentals WHERE ticker = $1
            ORDER BY fiscal_year DESC NULLS LAST, fetched_at DESC
            LIMIT 15
            """,
            ticker,
        )

    result = {"ticker": ticker, "items": [dict(r) for r in rows]}
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
                   valuation_score, sentiment_score, scored_at, model_version
            FROM stock_scores WHERE ticker = $1
            ORDER BY scored_at DESC LIMIT 1
            """,
            ticker,
        )

    result = dict(row) if row else {"ticker": ticker, "health_score": None}
    await _cache_set(cache_key, result, ttl=14400)
    return result
