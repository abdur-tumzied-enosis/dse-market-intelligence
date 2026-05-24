"""
DSE Pipeline Management API

Run:
    uvicorn mgmt.main:app --reload --port 8001
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from db.pool import close_pool, get_pool
from extraction.scheduler import configure_scheduler, get_scheduler
from mgmt.adapter_state import load_overrides
from mgmt.agent.agent import Agent
from mgmt.cache import close_redis, get_redis
from mgmt.config import get_settings
from mgmt.routers import agent, alerts, health, jobs, metrics, quality, scheduler, streams, tasks

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    # DB pool
    pool = await get_pool()

    # Redis cache
    await get_redis()
    logger.info("redis_cache_connected")

    # Restore adapter override state
    try:
        await load_overrides(pool)
        logger.info("adapter_overrides_loaded")
    except Exception as exc:
        logger.warning("adapter_overrides_load_failed", error=str(exc))

    # APScheduler — SQLAlchemy job store must bypass pgBouncer (transaction mode
    # breaks LISTEN/NOTIFY and prepared statements used by SQLAlchemy)
    sched = get_scheduler(settings.database_sync_url)
    configure_scheduler(sched)
    sched.start()
    app.state.scheduler = sched
    logger.info("scheduler_started")

    # Ops agent
    ops_agent = Agent(
        provider=settings.agent_provider,
        model=settings.agent_model,
        auto_execute_risk=settings.ops_agent_auto_execute_risk,
    )
    ops_agent.scheduler = sched
    app.state.ops_agent = ops_agent
    logger.info("ops_agent_initialized", provider=settings.agent_provider, model=settings.agent_model)

    yield

    sched.shutdown(wait=False)
    await close_redis()
    await close_pool()
    logger.info("mgmt_shutdown_complete")


app = FastAPI(
    title="DSE Pipeline Management API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(streams.router)
app.include_router(jobs.router)
app.include_router(scheduler.router)
app.include_router(quality.router)
app.include_router(health.router)
app.include_router(alerts.router)
app.include_router(agent.router)
app.include_router(tasks.router)
app.include_router(metrics.router)


@app.get("/mgmt/ping")
async def ping():
    return {"status": "ok"}
