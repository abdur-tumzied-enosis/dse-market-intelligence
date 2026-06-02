"""
Job execution context manager — logs every pipeline run to pipeline_jobs table.
"""
from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Optional

import structlog

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def job_run(
    job_name: str,
    stream_name: Optional[str] = None,
    adapter_name: Optional[str] = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """
    Context manager for tracking job execution in pipeline_jobs table.

    Usage:
        async with job_run("live_price_pull", stream_name="live_prices") as ctx:
            data = await fetch_live_prices()
            ctx["records_fetched"] = len(data)
            await insert_data(data)
            ctx["records_inserted"] = len(data)
    """
    from db.pool import get_pool

    job_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    t0 = time.monotonic()

    ctx: dict[str, Any] = {
        "job_id": job_id,
        "job_name": job_name,
        "stream_name": stream_name,
        "adapter_name": adapter_name,
        "records_fetched": 0,
        "records_inserted": 0,
        "quality_failures": 0,
    }

    logger.info("job_start", job_id=job_id, job_name=job_name, stream_name=stream_name)

    try:
        pool = await get_pool()
        await pool.execute(
            """
            INSERT INTO pipeline_jobs
                (job_id, job_name, stream_name, adapter_used, started_at, status)
            VALUES ($1, $2, $3, $4, $5, 'running')
            """,
            job_id, job_name, stream_name, adapter_name, started_at,
        )
    except Exception as db_exc:
        logger.warning("job_db_insert_failed", job_id=job_id, error=str(db_exc))

    try:
        yield ctx

        duration_ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            "job_success",
            job_id=job_id,
            job_name=job_name,
            duration_ms=duration_ms,
            records_fetched=ctx["records_fetched"],
            records_inserted=ctx["records_inserted"],
        )

        try:
            pool = await get_pool()
            await pool.execute(
                """
                UPDATE pipeline_jobs
                SET status           = 'success',
                    finished_at      = NOW(),
                    duration_ms      = $1,
                    records_fetched  = $2,
                    records_inserted = $3,
                    quality_failures = $4
                WHERE job_id = $5
                """,
                duration_ms,
                ctx["records_fetched"],
                ctx["records_inserted"],
                ctx["quality_failures"],
                job_id,
            )
        except Exception as db_exc:
            logger.warning("job_db_update_failed", job_id=job_id, error=str(db_exc))

    except Exception as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        error_msg = str(exc)

        logger.error("job_failed", job_id=job_id, job_name=job_name, error=error_msg, duration_ms=duration_ms)

        try:
            pool = await get_pool()
            await pool.execute(
                """
                UPDATE pipeline_jobs
                SET status        = 'failed',
                    finished_at   = NOW(),
                    duration_ms   = $1,
                    error_message = $2
                WHERE job_id = $3
                """,
                duration_ms, error_msg, job_id,
            )
        except Exception as db_exc:
            logger.warning("job_db_update_failed", job_id=job_id, error=str(db_exc))

        try:
            from extraction.observability import fire_alert

            await fire_alert(
                severity="WARNING",
                message=f"Job failed: {job_name}",
                stream_name=stream_name,
                details={
                    "job_id": job_id,
                    "job_name": job_name,
                    "adapter_name": adapter_name,
                    "error": error_msg,
                    "duration_ms": duration_ms,
                },
            )
        except Exception as alert_exc:
            logger.warning("job_fail_alert_failed", job_id=job_id, error=str(alert_exc))

        raise
