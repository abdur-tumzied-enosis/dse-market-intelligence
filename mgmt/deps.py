from __future__ import annotations

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader

from db.pool import get_pool as _get_pool
from mgmt.config import get_settings

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def get_db():
    return await _get_pool()


async def get_scheduler(request: Request):
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None:
        raise HTTPException(503, "Scheduler not running")
    return scheduler


async def get_ops_agent(request: Request):
    agent = getattr(request.app.state, "ops_agent", None)
    if agent is None:
        raise HTTPException(503, "Ops agent not initialized")
    return agent


async def require_api_key(key: str | None = Security(_api_key_header)) -> None:
    settings = get_settings()
    if settings.mgmt_api_key and key != settings.mgmt_api_key:
        raise HTTPException(401, "Invalid or missing API key")
