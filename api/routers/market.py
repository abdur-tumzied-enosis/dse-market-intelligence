# api/routers/market.py
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends, Query
from api.deps import get_current_user, get_db
from api.schemas.market import HeatmapItem, MarketSummary, TopMover, MarketIndices
import httpx

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


_AMARSTOCK_MARKET_URL = "https://www.amarstock.com/Info/DSE"
_AMARSTOCK_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; DSEIntelBot/1.0)"}


async def _fetch_indices_from_amarstock() -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(_AMARSTOCK_MARKET_URL, headers=_AMARSTOCK_HEADERS)
        resp.raise_for_status()
        data = resp.json()
    return {
        "dsex_value": float(data["IndexValue"]),
        "dsex_change_pct": float(data["ChangePct"]),
        "ds30_value": float(data["D30Index"]),
        "ds30_change_pct": float(data["D30ChangePct"]),
        "dses_value": float(data["DsIndex"]),
        "dses_change_pct": float(data["DsChangePct"]),
        "market_status": data["MarketStatus"],
        "advance": int(data["Advance"]),
        "decline": int(data["Decline"]),
        "unchanged": int(data["Unchange"]),
    }


@router.get("/indices", response_model=MarketIndices)
async def market_indices(_user=Depends(get_current_user)):
    cache_key = "cache:api:market:indices"
    cached = await _cache_get(cache_key)
    if cached:
        return cached
    result = await _fetch_indices_from_amarstock()
    await _cache_set(cache_key, result, ttl=60)
    return result


@router.get("/summary", response_model=MarketSummary)
async def market_summary(pool=Depends(get_db), _user=Depends(get_current_user)):
    cache_key = "cache:api:market:summary"
    cached = await _cache_get(cache_key)
    if cached:
        return cached

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            WITH latest AS (
                SELECT DISTINCT ON (ticker) ticker, change_pct, volume, value_bdt
                FROM stock_prices
                ORDER BY ticker, time DESC
            )
            SELECT
                COUNT(*)                                        AS total_stocks,
                COUNT(*) FILTER (WHERE change_pct > 0)         AS advance,
                COUNT(*) FILTER (WHERE change_pct < 0)         AS decline,
                COUNT(*) FILTER (WHERE change_pct = 0 OR change_pct IS NULL) AS unchanged,
                SUM(volume)                                     AS total_volume,
                SUM(value_bdt)                                  AS total_value_bdt,
                ROUND(AVG(change_pct), 4)                       AS avg_change_pct
            FROM latest
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

    async with pool.acquire() as conn:
        gainers = await conn.fetch(
            """
            WITH latest AS (
                SELECT DISTINCT ON (sp.ticker) sp.ticker, c.name, sp.close, sp.change_pct
                FROM stock_prices sp
                JOIN companies c ON c.ticker = sp.ticker
                WHERE c.is_active = true
                ORDER BY sp.ticker, sp.time DESC
            )
            SELECT ticker, name, close, change_pct
            FROM latest
            WHERE change_pct IS NOT NULL
            ORDER BY change_pct DESC
            LIMIT $1
            """,
            n,
        )
        losers = await conn.fetch(
            """
            WITH latest AS (
                SELECT DISTINCT ON (sp.ticker) sp.ticker, c.name, sp.close, sp.change_pct
                FROM stock_prices sp
                JOIN companies c ON c.ticker = sp.ticker
                WHERE c.is_active = true
                ORDER BY sp.ticker, sp.time DESC
            )
            SELECT ticker, name, close, change_pct
            FROM latest
            WHERE change_pct IS NOT NULL
            ORDER BY change_pct ASC
            LIMIT $1
            """,
            n,
        )

    result = {
        "gainers": [dict(r) for r in gainers],
        "losers": [dict(r) for r in losers],
    }
    await _cache_set(cache_key, result, ttl=300)
    return result


@router.get("/heatmap", response_model=list[HeatmapItem])
async def market_heatmap(pool=Depends(get_db), _user=Depends(get_current_user)):
    cache_key = "cache:api:market:heatmap"
    cached = await _cache_get(cache_key)
    if cached:
        return cached

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH latest AS (
                SELECT DISTINCT ON (sp.ticker) sp.ticker, sp.change_pct, sp.value_bdt
                FROM stock_prices sp
                JOIN companies c ON c.ticker = sp.ticker
                WHERE c.is_active = true
                ORDER BY sp.ticker, sp.time DESC
            )
            SELECT l.ticker, c.sector, l.change_pct, l.value_bdt
            FROM latest l
            JOIN companies c ON c.ticker = l.ticker
            ORDER BY c.sector, l.value_bdt DESC NULLS LAST
            """
        )

    result = [dict(r) for r in rows]
    await _cache_set(cache_key, result, ttl=300)
    return result
