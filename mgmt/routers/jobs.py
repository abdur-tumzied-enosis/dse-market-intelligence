from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from mgmt.deps import get_db

router = APIRouter(prefix="/mgmt/jobs", tags=["jobs"])


@router.get("")
async def list_jobs(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    stream: str | None = Query(None),
    status: str | None = Query(None),
    pool=Depends(get_db),
):
    conditions = []
    params: list = []
    i = 1

    if stream:
        conditions.append(f"stream_name = ${i}")
        params.append(stream)
        i += 1
    if status:
        conditions.append(f"status = ${i}")
        params.append(status)
        i += 1

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params += [limit, offset]

    rows = await pool.fetch(
        f"""
        SELECT job_id, job_name, stream_name, adapter_used, started_at, finished_at,
               status, records_fetched, records_inserted, quality_failures,
               error_message, duration_ms
        FROM pipeline_jobs
        {where}
        ORDER BY started_at DESC
        LIMIT ${i} OFFSET ${i + 1}
        """,
        *params,
    )
    return [dict(r) for r in rows]


@router.get("/stats")
async def job_stats(pool=Depends(get_db)):
    row = await pool.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE status = 'success')  AS success_count,
            COUNT(*) FILTER (WHERE status = 'failed')   AS failed_count,
            COUNT(*) FILTER (WHERE status = 'running')  AS running_count,
            COUNT(*) FILTER (WHERE started_at > NOW() - INTERVAL '24 hours') AS last_24h,
            AVG(duration_ms) FILTER (WHERE status = 'success') AS avg_duration_ms
        FROM pipeline_jobs
        """
    )
    return dict(row) if row else {}


@router.get("/{job_id}")
async def get_job(job_id: str, pool=Depends(get_db)):
    row = await pool.fetchrow("SELECT * FROM pipeline_jobs WHERE job_id = $1", job_id)
    if not row:
        raise HTTPException(404, f"Job '{job_id}' not found")
    return dict(row)
