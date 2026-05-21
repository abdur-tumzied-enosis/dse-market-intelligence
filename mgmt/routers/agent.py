from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from mgmt.deps import get_db, get_ops_agent

router = APIRouter(prefix="/mgmt/agent", tags=["agent"])


@router.get("/status")
async def agent_status(pool=Depends(get_db)):
    """Last agent run summary."""
    row = await pool.fetchrow(
        """
        SELECT run_mode, decided_at, status, action_type, reasoning
        FROM agent_decisions
        ORDER BY decided_at DESC
        LIMIT 1
        """
    )
    return dict(row) if row else {"status": "no_runs"}


@router.post("/run")
async def trigger_sweep(request: Request, pool=Depends(get_db)):
    """Trigger an immediate ops agent sweep."""
    agent = await get_ops_agent(request)
    result = await agent.sweep(pool)
    return result


@router.get("/decisions")
async def list_decisions(
    limit: int = Query(50, ge=1, le=200),
    run_mode: str | None = Query(None),
    status: str | None = Query(None),
    pool=Depends(get_db),
):
    conditions = []
    params: list[Any] = []
    i = 1

    if run_mode:
        conditions.append(f"run_mode = ${i}")
        params.append(run_mode)
        i += 1
    if status:
        conditions.append(f"status = ${i}")
        params.append(status)
        i += 1

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.append(limit)

    rows = await pool.fetch(
        f"""
        SELECT id, decided_at, run_mode, action_type, target, reasoning,
               risk_level, status, approved_by, approved_at, outcome
        FROM agent_decisions
        {where}
        ORDER BY decided_at DESC
        LIMIT ${i}
        """,
        *params,
    )
    return [dict(r) for r in rows]


@router.get("/queue")
async def approval_queue(pool=Depends(get_db)):
    """Decisions awaiting human approval."""
    rows = await pool.fetch(
        """
        SELECT id, decided_at, run_mode, action_type, target, reasoning,
               risk_level, tool_calls
        FROM agent_decisions
        WHERE status = 'pending_approval'
        ORDER BY decided_at DESC
        """
    )
    return [dict(r) for r in rows]


@router.post("/decisions/{decision_id}/approve")
async def approve_decision(
    decision_id: int,
    request: Request,
    pool=Depends(get_db),
):
    row = await pool.fetchrow(
        "SELECT * FROM agent_decisions WHERE id = $1 AND status = 'pending_approval'",
        decision_id,
    )
    if not row:
        raise HTTPException(404, f"Decision {decision_id} not found or not pending")

    tool_calls = json.loads(row["tool_calls"] or "[]")
    agent = await get_ops_agent(request)

    executed = []
    for tc in tool_calls:
        result, _ = await agent._execute_tool(pool, tc["name"], tc["input"], mode="chat")
        executed.append({"name": tc["name"], "result": result})

    await pool.execute(
        """
        UPDATE agent_decisions
        SET status = 'approved', approved_by = 'api', approved_at = NOW(),
            outcome = $1
        WHERE id = $2
        """,
        json.dumps(executed),
        decision_id,
    )
    return {"status": "approved", "decision_id": decision_id, "executed": executed}


@router.post("/decisions/{decision_id}/reject")
async def reject_decision(decision_id: int, pool=Depends(get_db)):
    result = await pool.execute(
        """
        UPDATE agent_decisions
        SET status = 'rejected', approved_by = 'api', approved_at = NOW()
        WHERE id = $1 AND status = 'pending_approval'
        """,
        decision_id,
    )
    if result == "UPDATE 0":
        raise HTTPException(404, f"Decision {decision_id} not found or not pending")
    return {"status": "rejected", "decision_id": decision_id}


class ChatRequest(BaseModel):
    messages: list[dict]


@router.post("/chat")
async def agent_chat(body: ChatRequest, request: Request, pool=Depends(get_db)):
    """SSE streaming chat with the ops agent."""
    agent = await get_ops_agent(request)

    async def event_stream():
        async for chunk in agent.chat_stream(pool, body.messages):
            yield f"data: {json.dumps(chunk)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
