# api/routers/sectors.py
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from api.deps import get_current_user, get_db
from api.schemas.sectors import SectorDetail, SectorRow

router = APIRouter(prefix="/sectors", tags=["sectors"])


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


@router.get("", response_model=list[SectorRow])
async def list_sectors(pool=Depends(get_db), _user=Depends(get_current_user)):
    cache_key = "cache:api:sectors:list"
    cached = await _cache_get(cache_key)
    if cached:
        return cached

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (sector) sector, pe, change_pct, market_cap_bdt, fetched_at
            FROM sector_pe
            ORDER BY sector, fetched_at DESC
            """
        )

    result = [dict(r) for r in rows]
    await _cache_set(cache_key, result, ttl=3600)
    return result


@router.get("/{sector}", response_model=SectorDetail)
async def get_sector(sector: str, pool=Depends(get_db), _user=Depends(get_current_user)):
    cache_key = f"cache:api:sectors:detail:{sector}"
    cached = await _cache_get(cache_key)
    if cached:
        return cached

    async with pool.acquire() as conn:
        pe_history = await conn.fetch(
            """
            SELECT sector, pe, change_pct, market_cap_bdt, fetched_at
            FROM sector_pe WHERE sector = $1
            ORDER BY fetched_at DESC LIMIT 30
            """,
            sector,
        )
        if not pe_history:
            raise HTTPException(status_code=404, detail=f"Sector '{sector}' not found")

        companies = await conn.fetch(
            "SELECT ticker FROM companies WHERE sector = $1 AND is_active = true ORDER BY market_cap_bdt DESC NULLS LAST",
            sector,
        )

    history_list = [dict(r) for r in pe_history]
    result = {
        "sector": sector,
        "latest_pe": history_list[0] if history_list else None,
        "pe_history": history_list,
        "companies": [r["ticker"] for r in companies],
    }
    await _cache_set(cache_key, result, ttl=3600)
    return result
