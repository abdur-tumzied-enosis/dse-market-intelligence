from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from mgmt.deps import get_db

router = APIRouter(prefix="/mgmt/alerts", tags=["alerts"])


@router.get("")
async def list_alerts(
    limit: int = Query(50, ge=1, le=200),
    severity: str | None = Query(None),
    unacked_only: bool = Query(False),
    stream: str | None = Query(None),
    pool=Depends(get_db),
):
    conditions = []
    params: list = []
    i = 1

    if severity:
        conditions.append(f"severity = ${i}")
        params.append(severity.upper())
        i += 1
    if unacked_only:
        conditions.append("acknowledged_at IS NULL")
    if stream:
        conditions.append(f"stream_name = ${i}")
        params.append(stream)
        i += 1

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.append(limit)

    rows = await pool.fetch(
        f"""
        SELECT id, severity, stream_name, message, details, created_at,
               acknowledged_at, acknowledged_by, notified_via
        FROM pipeline_alerts
        {where}
        ORDER BY created_at DESC
        LIMIT ${i}
        """,
        *params,
    )
    return [dict(r) for r in rows]


class AcknowledgeRequest(BaseModel):
    acknowledged_by: str = "api"


@router.post("/{alert_id}/acknowledge")
async def acknowledge_alert(
    alert_id: int,
    body: AcknowledgeRequest,
    pool=Depends(get_db),
):
    result = await pool.execute(
        """
        UPDATE pipeline_alerts
        SET acknowledged_at = NOW(),
            acknowledged_by = $1
        WHERE id = $2 AND acknowledged_at IS NULL
        """,
        body.acknowledged_by,
        alert_id,
    )
    if result == "UPDATE 0":
        raise HTTPException(404, f"Alert {alert_id} not found or already acknowledged")
    return {"status": "acknowledged", "alert_id": alert_id}


@router.get("/summary")
async def alerts_summary(pool=Depends(get_db)):
    row = await pool.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE severity = 'CRITICAL' AND acknowledged_at IS NULL) AS critical_unacked,
            COUNT(*) FILTER (WHERE severity = 'WARNING'  AND acknowledged_at IS NULL) AS warning_unacked,
            COUNT(*) FILTER (WHERE acknowledged_at IS NULL)                           AS total_unacked,
            COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '24 hours')          AS last_24h
        FROM pipeline_alerts
        """
    )
    return dict(row) if row else {}
