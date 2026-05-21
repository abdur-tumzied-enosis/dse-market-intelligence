"""
DSE Pipeline Ops Agent — LangChain, provider-switchable.

Set OPS_AGENT_PROVIDER=openrouter (default) or OPS_AGENT_PROVIDER=ollama.
OpenRouter model example: "anthropic/claude-haiku-4-5"
Ollama model example:     "llama3.1"
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

import asyncpg
import structlog
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from mgmt.agent.llm import make_llm
from mgmt.config import get_settings

logger = structlog.get_logger(__name__)

SYSTEM_PROMPT = """You are the DSE Pipeline Ops Agent — autonomous operator for the Dhaka Stock Exchange data extraction pipeline.

Responsibilities:
1. Monitor pipeline health: job success rates, source availability, data freshness
2. Diagnose problems: failing adapters, stale data, quality issues
3. Take corrective actions: retry jobs, pause failing adapters, escalate
4. Explain your reasoning clearly

Pipeline context:
- Market hours: Sunday–Thursday 10:00–14:30 Bangladesh time (UTC+6)
- 16 data streams with priority-ordered adapter chains and automatic failover
- Sources: bdshare (Python lib), AmarStock API, DSE direct scrape, World Bank API

Risk levels for actions:
- LOW: trigger_job (retry), promote_adapter, fire_alert (INFO/WARNING)
- MEDIUM: pause_adapter
- HIGH: fire_alert (CRITICAL)

In sweep/alert_hook mode: explain what you find and what actions you're taking.
In chat mode: answer operator questions and execute requested actions."""

TOOLS = [
    {
        "name": "get_pipeline_status",
        "description": "Get current pipeline health: recent jobs, source health, unacked alerts, stream freshness",
        "input_schema": {
            "type": "object",
            "properties": {
                "hours_back": {
                    "type": "integer",
                    "default": 24,
                    "description": "How many hours of history to include",
                }
            },
        },
    },
    {
        "name": "query_db_readonly",
        "description": "Execute a read-only SELECT query against the pipeline database",
        "input_schema": {
            "type": "object",
            "required": ["sql"],
            "properties": {
                "sql": {"type": "string"},
                "params": {"type": "array", "items": {}, "default": []},
            },
        },
    },
    {
        "name": "trigger_job",
        "description": "Trigger a scheduler job to run immediately by job ID",
        "input_schema": {
            "type": "object",
            "required": ["job_id"],
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "Scheduler job ID (e.g. live_price_pull, eod_snapshot, health_checks)",
                },
                "reason": {"type": "string"},
            },
        },
    },
    {
        "name": "pause_adapter",
        "description": "Pause a failing adapter for a stream",
        "input_schema": {
            "type": "object",
            "required": ["stream_name", "adapter_name"],
            "properties": {
                "stream_name": {"type": "string"},
                "adapter_name": {"type": "string"},
                "reason": {"type": "string"},
            },
        },
    },
    {
        "name": "promote_adapter",
        "description": "Increase priority of an adapter (lower priority number = tried first)",
        "input_schema": {
            "type": "object",
            "required": ["stream_name", "adapter_name"],
            "properties": {
                "stream_name": {"type": "string"},
                "adapter_name": {"type": "string"},
                "reason": {"type": "string"},
            },
        },
    },
    {
        "name": "fire_alert",
        "description": "Create a pipeline alert for human attention",
        "input_schema": {
            "type": "object",
            "required": ["severity", "message"],
            "properties": {
                "severity": {"type": "string", "enum": ["INFO", "WARNING", "CRITICAL"]},
                "message": {"type": "string"},
                "stream_name": {"type": "string"},
                "details": {"type": "object"},
            },
        },
    },
]

_TOOL_RISK: dict[str, str] = {
    "get_pipeline_status": "none",
    "query_db_readonly": "none",
    "trigger_job": "low",
    "promote_adapter": "low",
    "pause_adapter": "medium",
    "fire_alert": "low",
}

_SAFE_SQL_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|GRANT|REVOKE|EXECUTE|CALL)\b",
    re.IGNORECASE,
)

# LangChain / OpenAI function-calling format for bind_tools()
_LC_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": t["name"],
            "description": t["description"],
            "parameters": t["input_schema"],
        },
    }
    for t in TOOLS
]


def _to_lc(messages: list[dict]) -> list[BaseMessage]:
    result: list[BaseMessage] = []
    for m in messages:
        content = m.get("content") or ""
        if m["role"] == "user":
            result.append(HumanMessage(content=content))
        elif m["role"] == "assistant":
            result.append(AIMessage(content=content))
    return result


class OpsAgent:
    def __init__(self, provider: str, model: str, auto_execute_risk: str) -> None:
        self._provider = provider
        self._model = model
        self._auto_execute_risk = auto_execute_risk
        self.scheduler = None

    def _llm(self):
        return make_llm(self._provider, self._model, get_settings()).bind_tools(_LC_TOOLS)

    def _is_configured(self) -> bool:
        if self._provider == "ollama":
            return True
        settings = get_settings()
        if self._provider == "google":
            return bool(settings.google_api_key)
        return bool(settings.openrouter_api_key)

    # ── Public entry points ─────────────────────────────────────────────

    async def sweep(self, pool: asyncpg.Pool) -> dict:
        if not self._is_configured():
            return {"skipped": f"OPENROUTER_API_KEY not configured"}
        status = await self._get_pipeline_status(pool, hours_back=24)
        messages = [
            {
                "role": "user",
                "content": (
                    "Perform a pipeline health sweep. Analyze the current state "
                    "and take any needed corrective actions.\n\n"
                    f"Current state:\n{json.dumps(status, indent=2, default=str)}"
                ),
            }
        ]
        return await self._run_loop(pool, messages, mode="scheduled")

    async def on_alert(self, pool: asyncpg.Pool, alert: dict) -> dict:
        if not self._is_configured():
            return {"skipped": "OPENROUTER_API_KEY not configured"}
        messages = [
            {
                "role": "user",
                "content": (
                    f"CRITICAL alert fired:\n\n{json.dumps(alert, indent=2, default=str)}\n\n"
                    "Analyze the situation and take appropriate actions."
                ),
            }
        ]
        return await self._run_loop(pool, messages, mode="alert_hook")

    async def chat_stream(
        self, pool: asyncpg.Pool, messages: list[dict]
    ) -> AsyncGenerator[dict, None]:
        if not self._is_configured():
            yield {"type": "text", "text": "Ops agent not configured (OPENROUTER_API_KEY missing)."}
            yield {"type": "done"}
            return

        llm = self._llm()
        lc_messages: list[BaseMessage] = [SystemMessage(content=SYSTEM_PROMPT)] + _to_lc(messages)

        for _ in range(10):
            full = None
            async for chunk in llm.astream(lc_messages):
                if isinstance(chunk.content, str) and chunk.content:
                    yield {"type": "text", "text": chunk.content}
                full = chunk if full is None else full + chunk

            if full is None:
                break

            lc_messages.append(full)

            if not full.tool_calls:
                break

            tool_msgs: list[BaseMessage] = []
            for tc in full.tool_calls:
                name, inp = tc["name"], tc["args"]
                yield {"type": "tool_call", "name": name, "input": inp}
                result, _ = await self._execute_tool(pool, name, inp, mode="chat")
                yield {"type": "tool_result", "name": name, "result": result}
                tool_msgs.append(ToolMessage(
                    tool_call_id=tc["id"],
                    content=json.dumps(result, default=str),
                    name=name,
                ))
            lc_messages.extend(tool_msgs)

        yield {"type": "done"}

    # ── Internal agent loop ─────────────────────────────────────────────

    async def _run_loop(
        self, pool: asyncpg.Pool, messages: list[dict], mode: str
    ) -> dict:
        llm = self._llm()
        lc_messages: list[BaseMessage] = [SystemMessage(content=SYSTEM_PROMPT)] + _to_lc(messages)
        decision_ids: list[int] = []
        final_text = ""

        for _ in range(8):
            response: AIMessage = await llm.ainvoke(lc_messages)
            lc_messages.append(response)

            if isinstance(response.content, str) and response.content:
                final_text = response.content

            if not response.tool_calls:
                break

            tool_msgs: list[BaseMessage] = []
            for tc in response.tool_calls:
                name, inp = tc["name"], tc["args"]
                risk = _TOOL_RISK.get(name, "low")
                if name == "fire_alert" and inp.get("severity") == "CRITICAL":
                    risk = "high"

                should_auto = self._should_auto_execute(risk, mode)
                result, _ = await self._execute_tool(
                    pool, name, inp, mode, dry_run=not should_auto
                )

                status_val = "auto_executed" if should_auto else "pending_approval"
                if risk != "none":
                    did = await self._record_decision(
                        pool,
                        run_mode=mode,
                        action_type=name,
                        target=json.dumps(inp),
                        reasoning=final_text or "no reasoning text",
                        risk_level=risk,
                        status=status_val,
                        tool_calls=[{"name": name, "input": inp, "result": result}],
                    )
                    decision_ids.append(did)

                tool_msgs.append(ToolMessage(
                    tool_call_id=tc["id"],
                    content=json.dumps(result, default=str),
                    name=name,
                ))
            lc_messages.extend(tool_msgs)

        return {"decisions": decision_ids, "summary": final_text}

    # ── Tool execution ──────────────────────────────────────────────────

    async def _execute_tool(
        self,
        pool: asyncpg.Pool,
        name: str,
        inp: dict,
        mode: str,
        dry_run: bool = False,
    ) -> tuple[Any, str]:
        risk = _TOOL_RISK.get(name, "low")

        if name == "get_pipeline_status":
            result = await self._get_pipeline_status(pool, inp.get("hours_back", 24))
            return result, "none"

        if name == "query_db_readonly":
            sql = inp.get("sql", "").strip()
            if not sql.upper().startswith("SELECT") or _SAFE_SQL_RE.search(sql):
                return {"error": "Only plain SELECT statements allowed"}, "none"
            try:
                rows = await pool.fetch(sql, *(inp.get("params") or []))
                return [dict(r) for r in rows[:100]], "none"
            except Exception as exc:
                return {"error": str(exc)}, "none"

        if name == "trigger_job":
            if dry_run:
                return {"status": "queued_for_approval", "job_id": inp.get("job_id")}, "low"
            if self.scheduler:
                import pytz
                try:
                    job_id = inp["job_id"]
                    self.scheduler.modify_job(job_id, next_run_time=datetime.now(pytz.utc))
                    return {"status": "triggered", "job_id": job_id}, "low"
                except Exception as exc:
                    return {"error": str(exc)}, "low"
            return {"error": "scheduler not available"}, "low"

        if name == "pause_adapter":
            if dry_run:
                return {"status": "queued_for_approval"}, "medium"
            from mgmt.adapter_state import set_override
            await set_override(
                pool,
                inp["stream_name"],
                inp["adapter_name"],
                paused=True,
                reason=inp.get("reason", f"agent pause ({mode})"),
            )
            return {"status": "paused", "stream": inp["stream_name"], "adapter": inp["adapter_name"]}, "medium"

        if name == "promote_adapter":
            if dry_run:
                return {"status": "queued_for_approval"}, "low"
            from mgmt.adapter_state import get_override, set_override
            ov = get_override(inp["stream_name"], inp["adapter_name"])
            new_delta = ov.priority_delta - 1
            await set_override(
                pool,
                inp["stream_name"],
                inp["adapter_name"],
                priority_delta=new_delta,
                reason=inp.get("reason", f"agent promote ({mode})"),
            )
            return {"status": "promoted", "priority_delta": new_delta}, "low"

        if name == "fire_alert":
            sev = inp.get("severity", "INFO")
            risk = "high" if sev == "CRITICAL" else "low"
            if dry_run:
                return {"status": "queued_for_approval"}, risk
            from extraction.observability import fire_alert
            await fire_alert(
                severity=sev,
                message=inp["message"],
                stream_name=inp.get("stream_name"),
                details=inp.get("details", {}),
            )
            return {"status": "alert_fired", "severity": sev}, risk

        return {"error": f"Unknown tool: {name}"}, "none"

    # ── Helpers ─────────────────────────────────────────────────────────

    def _should_auto_execute(self, risk: str, mode: str) -> bool:
        if risk == "none":
            return True
        if mode == "chat":
            return True
        if risk == "low" and self._auto_execute_risk == "low":
            return True
        return False

    async def _get_pipeline_status(self, pool: asyncpg.Pool, hours_back: int = 24) -> dict:
        jobs = await pool.fetch(
            """
            SELECT job_name, stream_name, status, started_at, duration_ms,
                   records_inserted, quality_failures, error_message
            FROM pipeline_jobs
            WHERE started_at > NOW() - ($1 * INTERVAL '1 hour')
            ORDER BY started_at DESC
            LIMIT 100
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
            SELECT DISTINCT ON (stream_name)
                stream_name, started_at AS last_success
            FROM pipeline_jobs
            WHERE status = 'success'
            ORDER BY stream_name, started_at DESC
            """
        )
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "jobs_last_Nh": [dict(r) for r in jobs],
            "source_health": [dict(r) for r in health],
            "unacked_alerts": [dict(r) for r in alerts],
            "stream_freshness": [dict(r) for r in freshness],
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
        return row["id"]  # type: ignore[index]
