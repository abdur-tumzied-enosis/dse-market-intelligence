# api/routers/market.py
from __future__ import annotations

import asyncio
import json as _json
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from api.deps import get_current_user, get_db
from api.schemas.market import HeatmapItem, MarketIndices, MarketSummary

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
    try:
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
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"AmarStock response malformed: {exc}") from exc


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

    computed_cte = """
        WITH ranked AS (
            SELECT sp.ticker, c.name, sp.close,
                   DENSE_RANK() OVER (PARTITION BY sp.ticker ORDER BY sp.time::date DESC) AS day_rank
            FROM stock_prices sp
            JOIN companies c ON c.ticker = sp.ticker
            WHERE c.is_active = true
        ),
        today AS (
            SELECT ticker, name, MAX(close) AS close
            FROM ranked WHERE day_rank = 1
            GROUP BY ticker, name
        ),
        yesterday AS (
            SELECT ticker, MAX(close) AS close
            FROM ranked WHERE day_rank = 2
            GROUP BY ticker
        ),
        computed AS (
            SELECT t.ticker, t.name, t.close,
                CASE WHEN y.close IS NOT NULL AND y.close > 0
                    THEN ROUND(((t.close - y.close) / y.close * 100)::numeric, 2)
                    ELSE NULL
                END AS change_pct
            FROM today t
            LEFT JOIN yesterday y ON y.ticker = t.ticker
        )
    """
    async with pool.acquire() as conn:
        gainers = await conn.fetch(
            computed_cte + """
            SELECT ticker, name, close, change_pct
            FROM computed
            WHERE change_pct IS NOT NULL AND change_pct > 0
            ORDER BY change_pct DESC
            LIMIT $1
            """,
            n,
        )
        losers = await conn.fetch(
            computed_cte + """
            SELECT ticker, name, close, change_pct
            FROM computed
            WHERE change_pct IS NOT NULL AND change_pct < 0
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
            WITH ranked AS (
                SELECT sp.ticker, sp.time, sp.close, sp.value_bdt,
                       DENSE_RANK() OVER (
                           PARTITION BY sp.ticker ORDER BY sp.time::date DESC
                       ) AS day_rank
                FROM stock_prices sp
                JOIN companies c ON c.ticker = sp.ticker
                WHERE c.is_active = true
            ),
            today AS (
                SELECT ticker, MAX(close) AS close, MAX(value_bdt) AS value_bdt
                FROM ranked WHERE day_rank = 1
                GROUP BY ticker
            ),
            yesterday AS (
                SELECT ticker, MAX(close) AS close
                FROM ranked WHERE day_rank = 2
                GROUP BY ticker
            )
            SELECT t.ticker, c.sector,
                CASE WHEN y.close IS NOT NULL AND y.close > 0
                    THEN ROUND(((t.close - y.close) / y.close * 100)::numeric, 2)
                    ELSE NULL
                END AS change_pct,
                t.value_bdt
            FROM today t
            JOIN companies c ON c.ticker = t.ticker
            LEFT JOIN yesterday y ON y.ticker = t.ticker
            ORDER BY c.sector, t.value_bdt DESC NULLS LAST
            """
        )

    result = [dict(r) for r in rows]
    await _cache_set(cache_key, result, ttl=300)
    return result


async def _default_get_indices() -> dict:
    """Default indices fetch: cache check, fallback to amarstock, cache result."""
    cache_key = "cache:api:market:indices"
    cached = await _cache_get(cache_key)
    if cached:
        return cached
    result = await _fetch_indices_from_amarstock()
    await _cache_set(cache_key, result, ttl=60)
    return result


async def _generate_market_events(get_indices_fn=_default_get_indices, interval: int = 30):
    """Async generator that yields SSE-formatted market index events.

    Stops after the first event when market_status != 'Open'; the client is
    responsible for reconnecting later to check if the market has re-opened.
    """
    while True:
        try:
            data = await get_indices_fn()
            yield f"data: {_json.dumps(data)}\n\n"
            if data.get("market_status") != "Open":
                return  # market closed — client will reconnect later
        except (asyncio.CancelledError, GeneratorExit):
            return
        except Exception as exc:
            yield f"data: {_json.dumps({'error': 'fetch_failed', 'detail': type(exc).__name__})}\n\n"
        if interval > 0:
            await asyncio.sleep(interval)
        else:
            return  # test mode: yield once then stop


@router.get("/stream")
async def market_stream():
    """SSE stream of market indices — no auth required (public data)."""
    return StreamingResponse(
        _generate_market_events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
