"""
DSE Pipeline Ops Agent — context-engineered LangChain implementation.

Context engineering strategies (langchain.com/blog/context-engineering-for-agents):
  WRITE    — scratchpad tool for in-session note-taking; DB decision log
  SELECT   — system message rebuilt each turn: live BD time, market status, scratchpad
  COMPRESS — pipeline status returns signal-dense summary, not raw rows;
             message history trimmed when window grows large
  ISOLATE  — DB result sets hard-capped; scratchpad stored in state, not message history

Tool definitions follow langchain.com/oss/python/langchain/tools.md:
  @tool decorator + Pydantic BaseModel args_schema.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Literal
from zoneinfo import ZoneInfo

import asyncpg
import structlog
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from mgmt.agent.llm import make_llm
from mgmt.config import get_settings

logger = structlog.get_logger(__name__)

_BD_TZ = ZoneInfo("Asia/Dhaka")
_MAX_HISTORY = 20   # messages before trimming (COMPRESS)
_KEEP_RECENT = 8    # non-system messages to keep after trim
_MAX_ROWS = 50      # hard cap on query_db_readonly rows (ISOLATE)
_MAX_LOOP = 8       # max tool-call iterations per run

_TOOL_RISK: dict[str, str] = {
    "scratchpad": "none",
    "get_pipeline_status": "none",
    "query_db_readonly": "none",
    "trigger_job": "low",
    "promote_adapter": "low",
    "pause_adapter": "medium",
    "fire_alert": "low",  # CRITICAL overridden to "high" at call time
}
_RISK_LEVEL = {"none": 0, "low": 1, "medium": 2, "high": 3}

_SAFE_SQL_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|GRANT|REVOKE|EXECUTE|CALL)\b",
    re.IGNORECASE,
)


# ── Tool input schemas ────────────────────────────────────────────────────────

class ScratchpadInput(BaseModel):
    note: str = Field(description="Your private observation, hypothesis, or step-by-step plan")


class PipelineStatusInput(BaseModel):
    hours_back: int = Field(
        default=6, ge=1, le=48,
        description="Hours of job history to include (default 6)",
    )


class QueryDbInput(BaseModel):
    sql: str = Field(description="Read-only SELECT statement")
    params: list[str] = Field(default_factory=list, description="Positional query parameters as strings")


class TriggerJobInput(BaseModel):
    job_id: str = Field(description="Scheduler job ID. Valid IDs: live_price_pull, eod_snapshot, dse_announcements, daily_macro, weekly_fundamentals, monthly, quarterly_retrain, health_checks")
    reason: str = Field(default="")


class PauseAdapterInput(BaseModel):
    stream_name: str
    adapter_name: str
    reason: str = Field(default="")


class PromoteAdapterInput(BaseModel):
    stream_name: str
    adapter_name: str
    reason: str = Field(default="")


class FireAlertInput(BaseModel):
    severity: Literal["INFO", "WARNING", "CRITICAL"]
    message: str
    stream_name: str = Field(default="")
    details: dict[str, str] = Field(default_factory=dict)


# ── System prompt ─────────────────────────────────────────────────────────────

_STATIC_INSTRUCTIONS = """\
You are the DSE Pipeline Ops Agent — autonomous operator for the Dhaka Stock Exchange data pipeline.

Responsibilities:
1. Monitor pipeline health: job success rates, source availability, data freshness
2. Diagnose problems: failing adapters, stale data, quality failures
3. Take corrective actions: retry jobs, pause failing adapters, escalate alerts
4. Use the scratchpad to note observations before acting — think, then execute

Sources: bdshare (Python lib, fragile), AmarStock API, DSE direct scrape, World Bank API
Architecture: 16 DataStreams with priority-ordered adapter chains and automatic failover

Tool risk levels:
  none   → scratchpad, get_pipeline_status, query_db_readonly
  low    → trigger_job, promote_adapter, fire_alert (INFO/WARNING)
  medium → pause_adapter
  high   → fire_alert (CRITICAL)

AUTO_EXECUTE_UP_TO={auto_execute_risk} — actions above this level are queued for human approval.
In sweep/alert_hook mode: diagnose → record findings in scratchpad → act.
In chat mode: answer questions and execute requested actions.\
"""


def _is_market_open() -> bool:
    now = datetime.now(_BD_TZ)
    if now.weekday() not in (0, 1, 2, 3, 6):  # Mon–Thu + Sun
        return False
    t = now.hour * 60 + now.minute
    return 600 <= t <= 870  # 10:00–14:30


def _build_system(auto_execute_risk: str, scratchpad: list[str] | None = None) -> SystemMessage:
    """
    SELECT: rebuilt each turn — injects current BD time, market status, and
    any scratchpad notes accumulated in this session.
    """
    now_bd = datetime.now(_BD_TZ)
    lines = [
        _STATIC_INSTRUCTIONS.format(auto_execute_risk=auto_execute_risk),
        "",
        "--- Live Context ---",
        f"BD time : {now_bd.strftime('%Y-%m-%d %H:%M %Z')}",
        f"Market  : {'OPEN' if _is_market_open() else 'CLOSED'}  (Sun–Thu 10:00–14:30 BD)",
    ]
    if scratchpad:
        lines += ["", "--- Your Notes (scratchpad) ---"]
        lines += [f"  {i + 1}. {note}" for i, note in enumerate(scratchpad)]
    return SystemMessage(content="\n".join(lines))


# ── Context management ────────────────────────────────────────────────────────

@dataclass
class _RunState:
    """Mutable context threaded through an agent loop."""
    messages: list[BaseMessage]
    scratchpad: list[str] = field(default_factory=list)
    decisions: list[int] = field(default_factory=list)
    final_text: str = ""


def _trim(messages: list[BaseMessage]) -> list[BaseMessage]:
    """
    COMPRESS: when history exceeds _MAX_HISTORY, drop oldest non-system messages
    while preserving SystemMessages and the most recent _KEEP_RECENT turns.
    """
    if len(messages) <= _MAX_HISTORY:
        return messages
    system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
    rest = [m for m in messages if not isinstance(m, SystemMessage)]
    logger.debug("context.trimmed", dropped=len(rest) - _KEEP_RECENT, kept=_KEEP_RECENT)
    return system_msgs + rest[-_KEEP_RECENT:]


def _compress_result(result: Any, max_rows: int = 10) -> Any:
    """COMPRESS: reduce oversized list results before injecting into ToolMessage."""
    if isinstance(result, list) and len(result) > max_rows:
        return {"total": len(result), "showing": max_rows, "rows": result[:max_rows]}
    return result


def _to_lc(messages: list[dict]) -> list[BaseMessage]:
    """Convert API role dicts to LangChain messages (user/assistant only)."""
    out: list[BaseMessage] = []
    for m in messages:
        role, content = m.get("role", ""), m.get("content") or ""
        if role == "user":
            out.append(HumanMessage(content=content))
        elif role == "assistant":
            out.append(AIMessage(content=content))
    return out


# ── Agent ─────────────────────────────────────────────────────────────────────

class Agent:
    def __init__(self, provider: str, model: str, auto_execute_risk: str) -> None:
        self._provider = provider
        self._model = model
        self._auto_execute_risk = auto_execute_risk
        self.scheduler = None
        self._llm_base = None  # cached base LLM (without tools binding)
        logger.info("agent.init", provider=provider, model=model, auto_execute_risk=auto_execute_risk)

    @property
    def _base_llm(self):
        if self._llm_base is None:
            logger.debug("agent.llm_init", provider=self._provider, model=self._model)
            self._llm_base = make_llm(self._provider, self._model, get_settings())
        return self._llm_base

    def _is_configured(self) -> bool:
        if self._provider == "ollama":
            return True
        s = get_settings()
        ok = bool(s.google_api_key if self._provider == "google" else s.openrouter_api_key)
        if not ok:
            logger.warning("agent.not_configured", provider=self._provider)
        return ok

    def _system(self, scratchpad: list[str] | None = None) -> SystemMessage:
        return _build_system(self._auto_execute_risk, scratchpad)

    def _make_tools(self, pool: asyncpg.Pool, state: _RunState) -> list:
        """
        WRITE + ISOLATE: create @tool-decorated closures that share pool and state.
        Each run gets fresh tool instances so scratchpad mutations stay isolated.
        """
        agent = self

        @tool("scratchpad", args_schema=ScratchpadInput)
        async def scratchpad(note: str) -> dict:
            """Write a private note to yourself. Record observations, hypotheses, or a plan before acting. Notes persist for this session and appear in your context each turn. Not visible to the operator."""
            n = note.strip()
            if n:
                state.scratchpad.append(n)
            logger.debug("tool.scratchpad", total_notes=len(state.scratchpad))
            return {"saved": True, "total_notes": len(state.scratchpad)}

        @tool("get_pipeline_status", args_schema=PipelineStatusInput)
        async def get_pipeline_status(hours_back: int = 6) -> dict:
            """Get a compressed pipeline health snapshot: job success/failure counts, unreachable sources, unacked alerts, and stale streams."""
            return await agent._get_pipeline_status(pool, hours_back)

        @tool("query_db_readonly", args_schema=QueryDbInput)
        async def query_db_readonly(sql: str, params: list[Any] | None = None) -> Any:
            """Run a read-only SELECT against the pipeline database (max 50 rows returned)."""
            sql = sql.strip()
            if not sql.upper().startswith("SELECT") or _SAFE_SQL_RE.search(sql):
                logger.warning("tool.query_db_readonly.blocked", sql=sql[:120])
                return {"error": "Only plain SELECT statements allowed"}
            logger.debug("tool.query_db_readonly", sql=sql[:120], params=params)
            try:
                rows = await pool.fetch(sql, *(params or []))
                result = [dict(r) for r in rows[:_MAX_ROWS]]
                logger.info("tool.query_db_readonly.ok", rows_returned=len(result))
                return result
            except Exception as exc:
                logger.error("tool.query_db_readonly.error", error=str(exc), sql=sql[:120])
                return {"error": str(exc)}

        @tool("trigger_job", args_schema=TriggerJobInput)
        async def trigger_job(job_id: str, reason: str = "") -> dict:
            """Trigger a scheduler job to run immediately by job ID."""
            logger.info("tool.trigger_job", job_id=job_id, reason=reason)
            if agent.scheduler:
                import pytz
                try:
                    agent.scheduler.modify_job(job_id, next_run_time=datetime.now(pytz.utc))
                    logger.info("tool.trigger_job.ok", job_id=job_id)
                    return {"status": "triggered", "job_id": job_id}
                except Exception as exc:
                    logger.error("tool.trigger_job.error", job_id=job_id, error=str(exc))
                    return {"error": str(exc)}
            logger.error("tool.trigger_job.no_scheduler")
            return {"error": "scheduler not attached"}

        @tool("pause_adapter", args_schema=PauseAdapterInput)
        async def pause_adapter(stream_name: str, adapter_name: str, reason: str = "") -> dict:
            """Pause a failing adapter within a named stream."""
            logger.info("tool.pause_adapter", stream=stream_name, adapter=adapter_name, reason=reason)
            from mgmt.adapter_state import set_override
            await set_override(
                pool, stream_name, adapter_name,
                paused=True, reason=reason or "agent pause",
            )
            logger.info("tool.pause_adapter.ok", stream=stream_name, adapter=adapter_name)
            return {"status": "paused", "stream": stream_name, "adapter": adapter_name}

        @tool("promote_adapter", args_schema=PromoteAdapterInput)
        async def promote_adapter(stream_name: str, adapter_name: str, reason: str = "") -> dict:
            """Increase an adapter's priority so it is tried before lower-priority adapters."""
            logger.info("tool.promote_adapter", stream=stream_name, adapter=adapter_name, reason=reason)
            from mgmt.adapter_state import get_override, set_override
            ov = get_override(stream_name, adapter_name)
            new_delta = ov.priority_delta - 1
            await set_override(
                pool, stream_name, adapter_name,
                priority_delta=new_delta, reason=reason or "agent promote",
            )
            logger.info("tool.promote_adapter.ok", stream=stream_name, adapter=adapter_name, priority_delta=new_delta)
            return {"status": "promoted", "priority_delta": new_delta}

        @tool("fire_alert", args_schema=FireAlertInput)
        async def fire_alert(
            severity: Literal["INFO", "WARNING", "CRITICAL"],
            message: str,
            stream_name: str = "",
            details: dict[str, Any] | None = None,
        ) -> dict:
            """Create a pipeline alert for human attention."""
            logger.info("tool.fire_alert", severity=severity, stream=stream_name, message=message[:120])
            from extraction.observability import fire_alert as _fire
            await _fire(
                severity=severity,
                message=message,
                stream_name=stream_name or None,
                details=details or {},
            )
            return {"status": "alert_fired", "severity": severity}

        return [
            scratchpad, get_pipeline_status, query_db_readonly,
            trigger_job, pause_adapter, promote_adapter, fire_alert,
        ]

    # ── Public API ─────────────────────────────────────────────────────────────

    async def sweep(self, pool: asyncpg.Pool) -> dict:
        logger.info("sweep.start", provider=self._provider, model=self._model)
        if not self._is_configured():
            logger.warning("sweep.skipped", reason="not_configured")
            return {"skipped": "OPS_AGENT provider not configured"}
        state = _RunState(messages=[
            self._system(),
            HumanMessage(content=(
                "Perform a pipeline health sweep. Call get_pipeline_status to assess "
                "current state, record your findings in the scratchpad, then take any "
                "needed corrective actions."
            )),
        ])
        result = await self._run_loop(pool, state, mode="scheduled")
        logger.info("sweep.done", decisions=len(result.get("decisions", [])))
        return result

    async def on_alert(self, pool: asyncpg.Pool, alert: dict) -> dict:
        logger.info("on_alert.start", severity=alert.get("severity"), stream=alert.get("stream_name"))
        if not self._is_configured():
            logger.warning("on_alert.skipped", reason="not_configured")
            return {"skipped": "OPS_AGENT provider not configured"}
        state = _RunState(messages=[
            self._system(),
            HumanMessage(content=(
                f"CRITICAL alert fired:\n\n{json.dumps(alert, indent=2, default=str)}\n\n"
                "Investigate via get_pipeline_status and query_db_readonly, note your "
                "analysis in the scratchpad, then take appropriate actions."
            )),
        ])
        result = await self._run_loop(pool, state, mode="alert_hook")
        logger.info("on_alert.done", decisions=len(result.get("decisions", [])))
        return result

    async def chat_stream(
        self, pool: asyncpg.Pool, messages: list[dict]
    ) -> AsyncGenerator[dict, None]:
        n_user_msgs = sum(1 for m in messages if m.get("role") == "user")
        logger.info("chat_stream.start", history_len=n_user_msgs)

        if not self._is_configured():
            yield {"type": "text", "text": "Ops agent not configured (provider API key missing)."}
            yield {"type": "done"}
            return

        state = _RunState(messages=[self._system()] + _to_lc(messages))
        tools = self._make_tools(pool, state)
        tool_map = {t.name: t for t in tools}
        llm = self._base_llm.bind_tools(tools)

        for loop_idx in range(_MAX_LOOP):
            logger.debug("chat_stream.loop", iteration=loop_idx + 1, max=_MAX_LOOP)
            # SELECT: refresh system message (time + market + notes) each turn
            state.messages[0] = self._system(state.scratchpad)
            # COMPRESS: trim if history has grown large
            state.messages = _trim(state.messages)

            full: AIMessage | None = None
            text_chars = 0
            async for chunk in llm.astream(state.messages):
                if isinstance(chunk.content, str) and chunk.content:
                    text_chars += len(chunk.content)
                    yield {"type": "text", "text": chunk.content}
                full = chunk if full is None else full + chunk  # type: ignore[operator]

            logger.debug("chat_stream.streamed", iteration=loop_idx + 1, chars=text_chars)

            if full is None:
                logger.warning("chat_stream.empty_response", iteration=loop_idx + 1)
                break
            state.messages.append(full)

            if not full.tool_calls:
                logger.info("chat_stream.done", iterations=loop_idx + 1, chars=text_chars)
                break

            tool_names = [tc["name"] for tc in full.tool_calls]
            logger.info("chat_stream.tool_calls", iteration=loop_idx + 1, tools=tool_names)
            tool_msgs: list[BaseMessage] = []
            for tc in full.tool_calls:
                name, inp = tc["name"], tc["args"]
                if name not in tool_map:
                    logger.error("chat_stream.unknown_tool", tool=name)
                    result: Any = {"error": f"Unknown tool: {name}"}
                else:
                    logger.debug("chat_stream.invoke_tool", tool=name)
                    result = await tool_map[name].ainvoke(inp)
                compressed = _compress_result(result)
                yield {"type": "tool_call", "name": name, "input": inp}
                yield {"type": "tool_result", "name": name, "result": compressed}
                tool_msgs.append(ToolMessage(
                    tool_call_id=tc["id"],
                    content=json.dumps(compressed, default=str),
                    name=name,
                ))
            state.messages.extend(tool_msgs)
        else:
            logger.warning("chat_stream.max_loop_reached", max=_MAX_LOOP)

        yield {"type": "done"}

    # ── Internal loop ──────────────────────────────────────────────────────────

    async def _run_loop(self, pool: asyncpg.Pool, state: _RunState, mode: str) -> dict:
        logger.info("run_loop.start", mode=mode, max_loop=_MAX_LOOP)
        tools = self._make_tools(pool, state)
        tool_map = {t.name: t for t in tools}
        llm = self._base_llm.bind_tools(tools)

        for loop_idx in range(_MAX_LOOP):
            logger.debug("run_loop.iteration", mode=mode, iteration=loop_idx + 1)
            # SELECT: inject live context + current scratchpad each turn
            state.messages[0] = self._system(state.scratchpad)
            # COMPRESS: trim before sending to the LLM
            state.messages = _trim(state.messages)

            response: AIMessage = await llm.ainvoke(state.messages)
            state.messages.append(response)

            if isinstance(response.content, str) and response.content:
                state.final_text = response.content

            if not response.tool_calls:
                logger.info("run_loop.done", mode=mode, iterations=loop_idx + 1, decisions=len(state.decisions))
                break

            tool_names = [tc["name"] for tc in response.tool_calls]
            logger.info("run_loop.tool_calls", mode=mode, iteration=loop_idx + 1, tools=tool_names)

            tool_msgs: list[BaseMessage] = []
            for tc in response.tool_calls:
                name, inp = tc["name"], tc["args"]
                risk = _TOOL_RISK.get(name, "low")
                if name == "fire_alert" and inp.get("severity") == "CRITICAL":
                    risk = "high"

                auto = self._should_auto(risk, mode)
                logger.debug("run_loop.tool_dispatch", tool=name, risk=risk, auto=auto, mode=mode)

                if auto and name in tool_map:
                    result = await tool_map[name].ainvoke(inp)
                else:
                    logger.info("run_loop.tool_queued", tool=name, risk=risk, mode=mode)
                    result = {"status": "queued_for_approval", "action": name, "input": inp}

                compressed = _compress_result(result)

                if risk != "none":
                    did = await self._record_decision(
                        pool,
                        run_mode=mode,
                        action_type=name,
                        target=json.dumps(inp),
                        reasoning=state.final_text or "no reasoning yet",
                        risk_level=risk,
                        status="auto_executed" if auto else "pending_approval",
                        tool_calls=[{"name": name, "input": inp, "result": result}],
                    )
                    state.decisions.append(did)

                tool_msgs.append(ToolMessage(
                    tool_call_id=tc["id"],
                    content=json.dumps(compressed, default=str),
                    name=name,
                ))
            state.messages.extend(tool_msgs)
        else:
            logger.warning("run_loop.max_loop_reached", mode=mode, max=_MAX_LOOP)

        return {"decisions": state.decisions, "summary": state.final_text}

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _should_auto(self, risk: str, mode: str) -> bool:
        if risk == "none":
            return True
        if mode == "chat":
            return True
        return _RISK_LEVEL.get(risk, 99) <= _RISK_LEVEL.get(self._auto_execute_risk, 0)

    async def _get_pipeline_status(self, pool: asyncpg.Pool, hours_back: int = 6) -> dict:
        """
        COMPRESS: return a signal-dense summary — failure counts, unreachable sources,
        stale streams — rather than dumping raw job rows into the context window.
        """
        jobs = await pool.fetch(
            """
            SELECT job_name, stream_name, status, started_at, duration_ms,
                   records_inserted, quality_failures, error_message
            FROM pipeline_jobs
            WHERE started_at > NOW() - ($1 * INTERVAL '1 hour')
            ORDER BY started_at DESC
            LIMIT 200
            """,
            hours_back,
        )
        health = await pool.fetch(
            """
            SELECT DISTINCT ON (source_name)
                source_name, reachable, status_code, response_ms, checked_at
            FROM source_health
            ORDER BY source_name, checked_at DESC
            """
        )
        alerts = await pool.fetch(
            """
            SELECT severity, stream_name, message, created_at
            FROM pipeline_alerts
            WHERE acknowledged_at IS NULL
            ORDER BY created_at DESC
            LIMIT 20
            """
        )
        freshness = await pool.fetch(
            """
            SELECT DISTINCT ON (stream_name) stream_name, started_at AS last_success
            FROM pipeline_jobs
            WHERE status = 'success'
            ORDER BY stream_name, started_at DESC
            """
        )

        job_rows = [dict(r) for r in jobs]
        failed = [r for r in job_rows if r["status"] == "failed"]
        success_count = sum(1 for r in job_rows if r["status"] == "success")
        unreachable = [dict(r) for r in health if not r["reachable"]]

        now = datetime.now(timezone.utc)
        stale_streams: list[dict] = []
        if _is_market_open():
            for row in freshness:
                age = (now - row["last_success"].replace(tzinfo=timezone.utc)).total_seconds() / 60
                if age > 30:
                    stale_streams.append({"stream": row["stream_name"], "age_minutes": round(age)})

        logger.info(
            "get_pipeline_status.summary",
            hours_back=hours_back,
            jobs_total=len(job_rows),
            jobs_failed=len(failed),
            sources_unreachable=len(unreachable),
            unacked_alerts=len(alerts),
            stale_streams=len(stale_streams),
            market_open=_is_market_open(),
        )
        return {
            "as_of": now.isoformat(),
            "market_open": _is_market_open(),
            "jobs": {
                "total": len(job_rows),
                "success": success_count,
                "failed": len(failed),
                "recent_failures": failed[:10],
            },
            "sources": {
                "total": len(health),
                "reachable": sum(1 for r in health if r["reachable"]),
                "unreachable": unreachable,
            },
            "unacked_alerts": [dict(r) for r in alerts],
            "stale_streams": stale_streams,
        }

    async def _record_decision(
        self,
        pool: asyncpg.Pool,
        run_mode: str,
        action_type: str,
        target: str,
        reasoning: str,
        risk_level: str,
        status: str,
        tool_calls: list,
    ) -> int:
        row = await pool.fetchrow(
            """
            INSERT INTO agent_decisions
                (run_mode, action_type, target, reasoning, risk_level, status, tool_calls)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id
            """,
            run_mode,
            action_type,
            target,
            reasoning,
            risk_level,
            status,
            json.dumps(tool_calls),
        )
        decision_id: int = row["id"]  # type: ignore[index]
        logger.info(
            "decision.recorded",
            decision_id=decision_id,
            action=action_type,
            risk=risk_level,
            status=status,
            mode=run_mode,
        )
        return decision_id
