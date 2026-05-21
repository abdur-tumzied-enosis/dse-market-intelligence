from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from mgmt.deps import get_db

router = APIRouter(prefix="/mgmt/quality", tags=["quality"])


@router.get("/failures")
async def quality_failures(
    limit: int = Query(50, ge=1, le=200),
    stream: str | None = Query(None),
    pool=Depends(get_db),
):
    params: list = []
    conditions = ["quality_failures > 0"]
    i = 1

    if stream:
        conditions.append(f"stream_name = ${i}")
        params.append(stream)
        i += 1

    where = "WHERE " + " AND ".join(conditions)
    params.append(limit)

    rows = await pool.fetch(
        f"""
        SELECT job_id, job_name, stream_name, adapter_used, started_at,
               status, quality_failures, records_fetched, records_inserted,
               error_message, duration_ms
        FROM pipeline_jobs
        {where}
        ORDER BY started_at DESC
        LIMIT ${i}
        """,
        *params,
    )
    return [dict(r) for r in rows]


@router.get("/freshness")
async def freshness(pool=Depends(get_db)):
    """Last successful job per stream with staleness flag."""
    from extraction.registry import STREAMS

    rows = await pool.fetch(
        """
        SELECT DISTINCT ON (stream_name)
            stream_name,
            started_at     AS last_success_at,
            adapter_used,
            records_inserted
        FROM pipeline_jobs
        WHERE status = 'success'
        ORDER BY stream_name, started_at DESC
        """
    )

    latest: dict[str, dict] = {r["stream_name"]: dict(r) for r in rows}

    result = []
    for name in STREAMS:
        entry = latest.get(name)
        result.append(
            {
                "stream": name,
                "last_success_at": entry["last_success_at"].isoformat() if entry else None,
                "adapter_used": entry["adapter_used"] if entry else None,
                "records_inserted": entry["records_inserted"] if entry else None,
                "stale": entry is None,
            }
        )
    return result
