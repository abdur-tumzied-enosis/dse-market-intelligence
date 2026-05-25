# chat/agent.py
"""
StockAnalystAgent -- user-facing DSE stock analysis chatbot.

Context engineering (mirrors OpsAgent pattern in mgmt/agent/agent.py):
  SELECT   -- system message rebuilt each turn (BD time, market status)
  COMPRESS -- history trimmed when > _MAX_HISTORY messages
  ISOLATE  -- all DB tools hard-capped at 50 rows
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from chat.prompt import build_system_message, detect_language
from chat.rag import build_rag_context
from chat.routing import is_complex_query, make_routed_llm
from chat.tools import build_tools
from chat.usage import log_llm_usage

log = logging.getLogger(__name__)

_MAX_HISTORY = 20
_KEEP_RECENT = 8
_MAX_LOOP = 10


@dataclass
class _ChatState:
    messages: list[BaseMessage]
    lang: str = "en"
    tool_calls_total: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


def _trim(messages: list[BaseMessage]) -> list[BaseMessage]:
    if len(messages) <= _MAX_HISTORY:
        return messages
    system = [m for m in messages if isinstance(m, SystemMessage)]
    rest = [m for m in messages if not isinstance(m, SystemMessage)]
    return system + rest[-_KEEP_RECENT:]


def _to_lc(messages: list[dict]) -> list[BaseMessage]:
    out = []
    for m in messages:
        role, content = m.get("role", ""), m.get("content") or ""
        if role == "user":
            out.append(HumanMessage(content=content))
        elif role == "assistant":
            out.append(AIMessage(content=content))
    return out


def _extract_tokens(response: AIMessage) -> tuple[int, int]:
    meta = getattr(response, "usage_metadata", None) or {}
    return meta.get("input_tokens", 0), meta.get("output_tokens", 0)


class StockAnalystAgent:
    def __init__(self, provider: str, model: str) -> None:
        self._provider = provider
        self._model = model
        self._llm_base = None
        log.info("chat_agent.init provider=%s model=%s", provider, model)

    @property
    def _base_llm(self):
        if self._llm_base is None:
            from mgmt.config import get_settings
            from mgmt.agent.llm import make_llm
            self._llm_base = make_llm(self._provider, self._model, get_settings())
        return self._llm_base

    async def chat_stream(
        self,
        pool,
        messages: list[dict],
        session_id: str = "anonymous",
        tier: str = "free",
    ) -> AsyncGenerator[dict, None]:
        t_start = time.monotonic()

        # Detect language from latest user message
        user_msgs = [m for m in messages if m.get("role") == "user"]
        last_user = user_msgs[-1]["content"] if user_msgs else ""
        lang = detect_language(last_user)

        # RAG: embed last user message, retrieve relevant chunks
        rag_context = ""
        if last_user:
            try:
                rag_context = await build_rag_context(pool, last_user)
            except Exception as exc:
                log.debug("rag skipped: %s", exc)

        system_msg = build_system_message(lang=lang, ticker_context=rag_context)
        state = _ChatState(
            messages=[system_msg] + _to_lc(messages),
            lang=lang,
        )

        # Model routing
        is_complex = is_complex_query(last_user)
        yield {"type": "routing", "thinking_mode": is_complex}

        from mgmt.config import get_settings
        settings = get_settings()
        routed_llm = make_routed_llm(self._provider, self._model, settings, is_complex)
        tools = build_tools(pool)
        tool_map = {t.name: t for t in tools}
        llm = routed_llm.bind_tools(tools)

        for loop_idx in range(_MAX_LOOP):
            # SELECT: refresh system message each turn
            state.messages[0] = build_system_message(lang=state.lang, ticker_context=rag_context)
            # COMPRESS: trim if history large
            state.messages = _trim(state.messages)

            full: AIMessage | None = None
            async for chunk in llm.astream(state.messages):
                if isinstance(chunk.content, str) and chunk.content:
                    yield {"type": "text", "text": chunk.content}
                full = chunk if full is None else full + chunk  # type: ignore[operator]

            if full is None:
                break
            state.messages.append(full)

            in_t, out_t = _extract_tokens(full)
            state.input_tokens += in_t
            state.output_tokens += out_t

            if not full.tool_calls:
                break

            tool_msgs: list[BaseMessage] = []
            for tc in full.tool_calls:
                name, inp = tc["name"], tc["args"]
                state.tool_calls_total += 1
                yield {"type": "tool_call", "name": name, "input": inp}
                if name in tool_map:
                    result = await tool_map[name].ainvoke(inp)
                else:
                    result = {"error": f"Unknown tool: {name}"}
                yield {"type": "tool_result", "name": name, "result": result}
                tool_msgs.append(ToolMessage(
                    tool_call_id=tc["id"],
                    content=json.dumps(result, default=str),
                    name=name,
                ))
            state.messages.extend(tool_msgs)

        # Usage logging (non-fatal)
        latency_ms = int((time.monotonic() - t_start) * 1000)
        try:
            await log_llm_usage(
                pool=pool,
                session_id=session_id,
                provider=self._provider,
                model=self._model,
                input_tokens=state.input_tokens,
                output_tokens=state.output_tokens,
                tool_calls_n=state.tool_calls_total,
                latency_ms=latency_ms,
                tier=tier,
            )
        except Exception as exc:
            log.warning("usage_log failed: %s", exc)

        yield {"type": "done"}
