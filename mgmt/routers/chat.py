# mgmt/routers/chat.py
from __future__ import annotations

import json
import uuid
from datetime import date

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from mgmt.deps import get_chat_agent, get_db

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])

_TIER_QUOTA: dict[str, int] = {
    "free": 3,
    "pro": 30,
    "pro_plus": 100,
    "institution": 0,  # 0 = unlimited
}


class ChatRequest(BaseModel):
    messages: list[dict] = Field(min_length=1)
    tier: str = Field(default="free")
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))

    @field_validator("messages")
    @classmethod
    def messages_not_empty(cls, v: list[dict]) -> list[dict]:
        if not v:
            raise ValueError("messages must not be empty")
        return v

    @field_validator("tier")
    @classmethod
    def tier_valid(cls, v: str) -> str:
        if v not in _TIER_QUOTA:
            return "free"
        return v


async def _check_quota(redis, session_id: str, tier: str) -> bool:
    """Return True if request is within quota. institution tier = unlimited."""
    limit = _TIER_QUOTA.get(tier, 3)
    if limit == 0:
        return True
    day_key = f"quota:{session_id}:{date.today().isoformat()}"
    try:
        count = await redis.incr(day_key)
        if count == 1:
            await redis.expire(day_key, 86400)  # 24h TTL
        return count <= limit
    except Exception as exc:
        logger.warning("quota_check_failed", error=str(exc))
        return True  # fail open


@router.post("/chat")
async def stock_chat(
    body: ChatRequest,
    request: Request,
    pool=Depends(get_db),
):
    """SSE streaming stock analysis chat endpoint."""
    agent = await get_chat_agent(request)

    # Quota check
    try:
        from mgmt.cache import get_redis
        redis = await get_redis()
        allowed = await _check_quota(redis, body.session_id, body.tier)
        if not allowed:
            limit = _TIER_QUOTA.get(body.tier, 3)

            async def _quota_exceeded():
                yield f"data: {json.dumps({'type': 'error', 'error': f'Daily quota exceeded ({limit}/day for {body.tier} tier)'})}\n\n"

            return StreamingResponse(_quota_exceeded(), media_type="text/event-stream")
    except Exception as exc:
        logger.warning("quota_check_error", error=str(exc))

    async def event_stream():
        try:
            async for chunk in agent.chat_stream(
                pool,
                body.messages,
                session_id=body.session_id,
                tier=body.tier,
            ):
                yield f"data: {json.dumps(chunk, default=str)}\n\n"
        except Exception as exc:
            logger.error("chat.stream_error", error=str(exc))
            yield f"data: {json.dumps({'type': 'error', 'error': str(exc)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
