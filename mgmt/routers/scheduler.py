from __future__ import annotations

from datetime import datetime, timezone

import pytz
from fastapi import APIRouter, Depends, HTTPException, Request

from mgmt.deps import get_scheduler

router = APIRouter(prefix="/mgmt/scheduler", tags=["scheduler"])


def _job_info(job) -> dict:
    return {
        "id": job.id,
        "name": job.name or job.id,
        "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
        "trigger": str(job.trigger),
        "paused": job.next_run_time is None,
    }


@router.get("")
async def list_scheduler_jobs(scheduler=Depends(get_scheduler)):
    return [_job_info(j) for j in scheduler.get_jobs()]


@router.get("/{job_id}")
async def get_scheduler_job(job_id: str, scheduler=Depends(get_scheduler)):
    job = scheduler.get_job(job_id)
    if not job:
        raise HTTPException(404, f"Scheduler job '{job_id}' not found")
    return _job_info(job)


@router.post("/{job_id}/trigger")
async def trigger_job(job_id: str, scheduler=Depends(get_scheduler)):
    job = scheduler.get_job(job_id)
    if not job:
        raise HTTPException(404, f"Scheduler job '{job_id}' not found")
    scheduler.modify_job(job_id, next_run_time=datetime.now(pytz.utc))
    return {"status": "triggered", "job_id": job_id}


@router.post("/{job_id}/pause")
async def pause_job(job_id: str, scheduler=Depends(get_scheduler)):
    job = scheduler.get_job(job_id)
    if not job:
        raise HTTPException(404, f"Scheduler job '{job_id}' not found")
    scheduler.pause_job(job_id)
    return {"status": "paused", "job_id": job_id}


@router.post("/{job_id}/resume")
async def resume_job(job_id: str, scheduler=Depends(get_scheduler)):
    job = scheduler.get_job(job_id)
    if not job:
        raise HTTPException(404, f"Scheduler job '{job_id}' not found")
    scheduler.resume_job(job_id)
    return {"status": "resumed", "job_id": job_id}
