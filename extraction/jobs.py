"""
Job execution context manager for tracking pipeline job metadata.

Every job (scheduler + Celery) wraps execution in job_run() to log:
- start/finish times
- success/failure status
- error messages
- duration + record counts

Job metadata flows to the observability dashboard.
"""
from __future__ import annotations

import logging
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
    Context manager for tracking job execution.

    Logs to pipeline_jobs table. Yields context dict for job to populate
    metadata (records_fetched, records_inserted, etc.).

    Usage:
        async with job_run("live_price_pull", stream_name="live_prices") as ctx:
            data = await fetch_live_prices()
            ctx["records_fetched"] = len(data)
            await insert_data(data)
            ctx["records_inserted"] = len(data)

    Args:
        job_name: Job identifier (e.g. "live_price_pull", "scrape_all_fundamentals")
        stream_name: Stream name if job is stream-specific (e.g. "live_prices")
        adapter_name: Adapter name if job uses specific adapter

    Yields:
        Context dict: job can populate records_fetched, records_inserted, etc.
    """
    # TODO: Import db module when available
    # from extraction.db import get_db
    # db = get_db()

    job_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    t0 = time.monotonic()

    ctx = {
        "job_id": job_id,
        "job_name": job_name,
        "stream_name": stream_name,
        "adapter_name": adapter_name,
        "records_fetched": 0,
        "records_inserted": 0,
        "quality_failures": 0,
    }

    logger.info(
        "job_start",
        job_id=job_id,
        job_name=job_name,
        stream_name=stream_name,
        adapter_name=adapter_name,
    )

    # TODO: Insert job start record
    # await db.execute(
    #     """
    #     INSERT INTO pipeline_jobs
    #     (job_id, job_name, stream_name, adapter_used, started_at, status)
    #     VALUES ($1, $2, $3, $4, $5, 'running')
    #     """,
    #     job_id, job_name, stream_name, adapter_name, started_at,
    # )

    try:
        yield ctx

        # Success path
        duration_ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            "job_success",
            job_id=job_id,
            job_name=job_name,
            duration_ms=duration_ms,
            records_fetched=ctx["records_fetched"],
            records_inserted=ctx["records_inserted"],
        )

        # TODO: Update job record to success
        # await db.execute(
        #     """
        #     UPDATE pipeline_jobs
        #     SET status='success',
        #         finished_at=NOW(),
        #         duration_ms=$1,
        #         records_fetched=$2,
        #         records_inserted=$3,
        #         quality_failures=$4
        #     WHERE job_id=$5
        #     """,
        #     duration_ms,
        #     ctx["records_fetched"],
        #     ctx["records_inserted"],
        #     ctx["quality_failures"],
        #     job_id,
        # )

    except Exception as exc:
        # Failure path
        duration_ms = int((time.monotonic() - t0) * 1000)
        error_msg = str(exc)

        logger.error(
            "job_failed",
            job_id=job_id,
            job_name=job_name,
            duration_ms=duration_ms,
            error=error_msg,
        )

        # TODO: Update job record to failed
        # await db.execute(
        #     """
        #     UPDATE pipeline_jobs
        #     SET status='failed',
        #         finished_at=NOW(),
        #         duration_ms=$1,
        #         error_message=$2
        #     WHERE job_id=$3
        #     """,
        #     duration_ms,
        #     error_msg,
        #     job_id,
        # )

        raise


# Decorators for quick wrapping


def sync_job_run(job_name: str, stream_name: Optional[str] = None):
    """Decorator for synchronous job functions.

    Usage:
        @sync_job_run("my_job", stream_name="live_prices")
        def my_job():
            pass
    """

    def decorator(func):
        def wrapper(*args, **kwargs):
            # TODO: Implement sync wrapper
            # For now, just call the function directly
            return func(*args, **kwargs)

        return wrapper

    return decorator


def async_job_run(job_name: str, stream_name: Optional[str] = None):
    """Decorator for async job functions.

    Usage:
        @async_job_run("my_job", stream_name="live_prices")
        async def my_job():
            pass
    """

    def decorator(func):
        async def wrapper(*args, **kwargs):
            # TODO: Implement async wrapper using job_run context
            return await func(*args, **kwargs)

        return wrapper

    return decorator
