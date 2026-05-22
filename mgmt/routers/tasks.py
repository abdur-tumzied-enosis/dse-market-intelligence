"""Celery task management endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/mgmt/tasks", tags=["tasks"])

TASK_REGISTRY: dict[str, dict] = {
    "scrape_all_fundamentals": {
        "description": "Scrape fundamentals for all tickers (~18 min, 350 pages)",
        "queue": "scraper",
        "task_name": "extraction.tasks.scrape_all_fundamentals",
        "estimated_duration": "~18 minutes",
    },
    "check_index_composition": {
        "description": "Check if DSE index composition changed (new/delisted companies)",
        "queue": "scraper",
        "task_name": "extraction.tasks.check_index_composition",
        "estimated_duration": "~1 minute",
    },
    "process_new_articles": {
        "description": "Score sentiment + generate embeddings for new articles (Haiku)",
        "queue": "nlp",
        "task_name": "extraction.tasks.process_new_articles",
        "estimated_duration": "variable",
    },
    "run_ml_inference": {
        "description": "End-of-day ML inference: predict price moves + health scores",
        "queue": "ml",
        "task_name": "extraction.tasks.run_ml_inference",
        "estimated_duration": "~5 minutes",
    },
    "retrain_ml_models": {
        "description": "Full ML model retrain (LSTM + XGBoost). Only promotes if better.",
        "queue": "ml",
        "task_name": "extraction.tasks.retrain_ml_models",
        "estimated_duration": "~2 hours",
    },
    "recalculate_beta_all_stocks": {
        "description": "Monthly beta calculation using 1-year rolling returns",
        "queue": "ml",
        "task_name": "extraction.tasks.recalculate_beta_all_stocks",
        "estimated_duration": "~10 minutes",
    },
}


@router.get("")
async def list_tasks():
    """List all available Celery tasks with metadata."""
    return [{"name": name, **meta} for name, meta in TASK_REGISTRY.items()]


@router.post("/{task_name}/run")
async def run_task(task_name: str):
    """Dispatch a Celery task by name. Returns task_id for status polling."""
    if task_name not in TASK_REGISTRY:
        raise HTTPException(404, f"Task '{task_name}' not found")
    try:
        from extraction.celery_app import celery_app
        result = celery_app.send_task(TASK_REGISTRY[task_name]["task_name"])
        return {
            "task_id": result.id,
            "status": "queued",
            "task_name": task_name,
            "queue": TASK_REGISTRY[task_name]["queue"],
        }
    except Exception as exc:
        raise HTTPException(503, f"Celery unavailable: {exc}")


@router.get("/results/{task_id}")
async def task_result(task_id: str):
    """Get Celery task status and result by task_id."""
    try:
        from celery.result import AsyncResult
        from extraction.celery_app import celery_app
        result = AsyncResult(task_id, app=celery_app)
        return {
            "task_id": task_id,
            "status": result.status,
            "result": result.result if result.ready() else None,
            "traceback": result.traceback if result.failed() else None,
        }
    except Exception as exc:
        raise HTTPException(503, f"Could not get task status: {exc}")
