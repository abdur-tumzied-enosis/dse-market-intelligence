from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from mgmt.deps import get_db

router = APIRouter(prefix="/mgmt/health", tags=["health"])


@router.get("")
async def source_health_summary(pool=Depends(get_db)):
    """Latest health snapshot per source."""
    rows = await pool.fetch(
        """
        SELECT DISTINCT ON (source_name)
            source_name, url, reachable, status_code, response_ms,
            checked_at, structure_hash, hash_changed
        FROM source_health
        ORDER BY source_name, checked_at DESC
        """
    )
    return [dict(r) for r in rows]


@router.get("/history/{source}")
async def source_health_history(
    source: str,
    limit: int = Query(50, ge=1, le=200),
    pool=Depends(get_db),
):
    rows = await pool.fetch(
        """
        SELECT source_name, url, reachable, status_code, response_ms,
               checked_at, structure_hash, hash_changed
        FROM source_health
        WHERE source_name = $1
        ORDER BY checked_at DESC
        LIMIT $2
        """,
        source,
        limit,
    )
    if not rows:
        raise HTTPException(404, f"No health records for source '{source}'")
    return [dict(r) for r in rows]


@router.get("/structure-hashes")
async def structure_hashes(pool=Depends(get_db)):
    """Latest structure hash per source with change detection."""
    rows = await pool.fetch(
        """
        SELECT DISTINCT ON (source_name)
            source_name, checked_at, structure_hash, prev_hash, hash_changed
        FROM source_health
        WHERE structure_hash IS NOT NULL
        ORDER BY source_name, checked_at DESC
        """
    )
    return [dict(r) for r in rows]
