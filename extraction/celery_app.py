"""Celery application for DSE pipeline."""
from __future__ import annotations

import os

from celery import Celery

TASK_ROUTES = {
    "extraction.tasks.scrape_all_fundamentals": {"queue": "scraper"},
    "extraction.tasks.check_index_composition": {"queue": "scraper"},
    "extraction.tasks.process_new_articles": {"queue": "nlp"},
    "extraction.tasks.run_ml_inference": {"queue": "ml"},
    "extraction.tasks.retrain_ml_models": {"queue": "ml"},
    "extraction.tasks.recalculate_beta_all_stocks": {"queue": "ml"},
}

celery_app = Celery(
    "dse_pipeline",
    broker=os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/1"),
    backend=os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/2"),
    include=["extraction.tasks"],
)

celery_app.conf.update(
    task_routes=TASK_ROUTES,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_default_retry_delay=300,
    task_max_retries=3,
    worker_prefetch_multiplier=1,
    task_track_started=True,
)
