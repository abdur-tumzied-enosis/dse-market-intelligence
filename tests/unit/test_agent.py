"""
Unit tests for mgmt/agent/agent.py.

Three layers:
  1. Pure functions  — no I/O, no mocks
  2. Tool layer      — individual tools with a mock asyncpg pool
  3. Loop layer      — sweep/chat_stream with a patched fake LLM
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from mgmt.agent.agent import (
    Agent,
    _RunState,
    _compress_result,
    _is_market_open,
    _to_lc,
    _trim,
)

# ── helpers ───────────────────────────────────────────────────────────────────

def _sys() -> SystemMessage:
    return SystemMessage(content="sys")


def _human(text: str = "hi") -> HumanMessage:
    return HumanMessage(content=text)


def _ai(text: str = "ok") -> AIMessage:
    return AIMessage(content=text)


def _mock_pool(**fetch_return) -> AsyncMock:
    """asyncpg pool stub whose fetch/fetchrow return empty lists by default."""
    pool = AsyncMock()
    pool.fetch.return_value = fetch_return.get("fetch", [])
    pool.fetchrow.return_value = fetch_return.get("fetchrow", {"id": 1})
    return pool


# ── 1. Pure-function tests ────────────────────────────────────────────────────

class TestTrim:
    def test_no_trim_when_under_limit(self):
        msgs = [_sys()] + [_human()] * 5
        assert _trim(msgs) == msgs

    def test_keeps_system_and_recent(self):
        from mgmt.agent.agent import _MAX_HISTORY, _KEEP_RECENT
        msgs = [_sys()] + [_human(f"msg{i}") for i in range(_MAX_HISTORY + 5)]
        result = _trim(msgs)
        assert isinstance(result[0], SystemMessage)
        non_sys = [m for m in result if not isinstance(m, SystemMessage)]
        assert len(non_sys) == _KEEP_RECENT

    def test_system_messages_always_preserved(self):
        from mgmt.agent.agent import _MAX_HISTORY
        msgs = [_sys()] + [_human(f"m{i}") for i in range(_MAX_HISTORY + 10)]
        result = _trim(msgs)
        assert sum(1 for m in result if isinstance(m, SystemMessage)) == 1

    def test_oldest_non_system_dropped(self):
        from mgmt.agent.agent import _MAX_HISTORY, _KEEP_RECENT
        msgs = [_sys()] + [_human(f"msg{i}") for i in range(_MAX_HISTORY + 2)]
        result = _trim(msgs)
        kept_texts = [m.content for m in result if isinstance(m, HumanMessage)]
        # oldest messages (msg0, msg1, …) must not appear
        expected_first = f"msg{_MAX_HISTORY + 2 - _KEEP_RECENT}"
        assert kept_texts[0] == expected_first


class TestCompressResult:
    def test_short_list_unchanged(self):
        data = [{"a": i} for i in range(5)]
        assert _compress_result(data) == data

    def test_long_list_truncated(self):
        data = [{"i": i} for i in range(20)]
        result = _compress_result(data, max_rows=10)
        assert result["total"] == 20
        assert result["showing"] == 10
        assert len(result["rows"]) == 10

    def test_non_list_passthrough(self):
        obj = {"status": "ok"}
        assert _compress_result(obj) is obj

    def test_default_max_rows_is_10(self):
        data = list(range(15))
        result = _compress_result(data)
        assert result["showing"] == 10


class TestToLc:
    def test_user_becomes_human(self):
        msgs = _to_lc([{"role": "user", "content": "hello"}])
        assert len(msgs) == 1
        assert isinstance(msgs[0], HumanMessage)
        assert msgs[0].content == "hello"

    def test_assistant_becomes_ai(self):
        msgs = _to_lc([{"role": "assistant", "content": "world"}])
        assert isinstance(msgs[0], AIMessage)

    def test_unknown_role_skipped(self):
        msgs = _to_lc([{"role": "system", "content": "ignored"}])
        assert msgs == []

    def test_empty_content_defaults_to_empty_string(self):
        msgs = _to_lc([{"role": "user", "content": None}])
        assert msgs[0].content == ""

    def test_mixed_roles(self):
        raw = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "a"},
            {"role": "user", "content": "q2"},
        ]
        result = _to_lc(raw)
        assert len(result) == 3
        assert isinstance(result[1], AIMessage)


class TestIsMarketOpen:
    def test_open_reads_store(self):
        with patch("mgmt.agent.agent.get_market_status_sync",
                   return_value={"status": "Open", "source": "dse_direct"}):
            assert _is_market_open() is True

    def test_closed_reads_store(self):
        with patch("mgmt.agent.agent.get_market_status_sync",
                   return_value={"status": "Closed", "source": "clock"}):
            assert _is_market_open() is False


class TestShouldAuto:
    def _agent(self, auto_risk: str = "low") -> Agent:
        return Agent(provider="ollama", model="x", auto_execute_risk=auto_risk)

    def test_risk_none_always_auto(self):
        a = self._agent("none")
        assert a._should_auto("none", "scheduled") is True

    def test_chat_mode_always_auto(self):
        a = self._agent("none")
        assert a._should_auto("high", "chat") is True

    def test_risk_within_threshold(self):
        a = self._agent("medium")
        assert a._should_auto("low", "scheduled") is True
        assert a._should_auto("medium", "scheduled") is True

    def test_risk_above_threshold(self):
        a = self._agent("low")
        assert a._should_auto("medium", "scheduled") is False
        assert a._should_auto("high", "scheduled") is False


# ── 2. Tool-layer tests ───────────────────────────────────────────────────────

@pytest.fixture
def agent() -> Agent:
    return Agent(provider="ollama", model="test-model", auto_execute_risk="medium")


@pytest.fixture
def state() -> _RunState:
    return _RunState(messages=[_sys()])


@pytest.mark.asyncio
class TestScratchpadTool:
    async def test_saves_note(self, agent, state):
        pool = _mock_pool()
        tools = agent._make_tools(pool, state)
        sp = next(t for t in tools if t.name == "scratchpad")
        result = await sp.ainvoke({"note": "test note"})
        assert result["saved"] is True
        assert result["total_notes"] == 1
        assert "test note" in state.scratchpad

    async def test_blank_note_not_saved(self, agent, state):
        pool = _mock_pool()
        tools = agent._make_tools(pool, state)
        sp = next(t for t in tools if t.name == "scratchpad")
        await sp.ainvoke({"note": "   "})
        assert len(state.scratchpad) == 0

    async def test_multiple_notes_accumulate(self, agent, state):
        pool = _mock_pool()
        tools = agent._make_tools(pool, state)
        sp = next(t for t in tools if t.name == "scratchpad")
        await sp.ainvoke({"note": "note1"})
        await sp.ainvoke({"note": "note2"})
        assert state.scratchpad == ["note1", "note2"]


@pytest.mark.asyncio
class TestQueryDbReadonlyTool:
    async def test_rejects_insert(self, agent, state):
        pool = _mock_pool()
        tools = agent._make_tools(pool, state)
        qdb = next(t for t in tools if t.name == "query_db_readonly")
        result = await qdb.ainvoke({"sql": "INSERT INTO t VALUES (1)"})
        assert "error" in result

    async def test_rejects_update(self, agent, state):
        pool = _mock_pool()
        tools = agent._make_tools(pool, state)
        qdb = next(t for t in tools if t.name == "query_db_readonly")
        result = await qdb.ainvoke({"sql": "UPDATE t SET x=1"})
        assert "error" in result

    async def test_rejects_drop(self, agent, state):
        pool = _mock_pool()
        tools = agent._make_tools(pool, state)
        qdb = next(t for t in tools if t.name == "query_db_readonly")
        result = await qdb.ainvoke({"sql": "SELECT 1; DROP TABLE companies"})
        assert "error" in result

    async def test_select_calls_pool(self, agent, state):
        pool = _mock_pool(fetch=[{"id": 1, "name": "GP"}])
        tools = agent._make_tools(pool, state)
        qdb = next(t for t in tools if t.name == "query_db_readonly")
        result = await qdb.ainvoke({"sql": "SELECT * FROM companies LIMIT 5"})
        assert isinstance(result, list)
        pool.fetch.assert_called_once()

    async def test_caps_rows_at_max(self, agent, state):
        from mgmt.agent.agent import _MAX_ROWS
        pool = _mock_pool(fetch=[{"id": i} for i in range(_MAX_ROWS + 20)])
        tools = agent._make_tools(pool, state)
        qdb = next(t for t in tools if t.name == "query_db_readonly")
        result = await qdb.ainvoke({"sql": "SELECT * FROM companies"})
        assert len(result) <= _MAX_ROWS

    async def test_db_error_returned_as_dict(self, agent, state):
        pool = AsyncMock()
        pool.fetch.side_effect = Exception("connection refused")
        tools = agent._make_tools(pool, state)
        qdb = next(t for t in tools if t.name == "query_db_readonly")
        result = await qdb.ainvoke({"sql": "SELECT 1"})
        assert "error" in result
        assert "connection refused" in result["error"]


@pytest.mark.asyncio
class TestTriggerJobTool:
    async def test_no_scheduler_returns_error(self, agent, state):
        pool = _mock_pool()
        tools = agent._make_tools(pool, state)
        tj = next(t for t in tools if t.name == "trigger_job")
        result = await tj.ainvoke({"job_id": "live_price_pull"})
        assert "error" in result

    async def test_with_scheduler_triggers(self, agent, state):
        pool = _mock_pool()
        scheduler = MagicMock()
        agent.scheduler = scheduler
        tools = agent._make_tools(pool, state)
        tj = next(t for t in tools if t.name == "trigger_job")
        result = await tj.ainvoke({"job_id": "live_price_pull", "reason": "test"})
        assert result["status"] == "triggered"
        scheduler.modify_job.assert_called_once()


# ── 3. Loop-layer tests ───────────────────────────────────────────────────────

class _FakeLLM:
    """
    Minimal fake LangChain chat model — supports both ainvoke (for _run_loop)
    and astream (for chat_stream). bind_tools returns self.
    """
    def __init__(self, responses: list[AIMessage]):
        self._responses = list(responses)
        self._call = 0

    def bind_tools(self, tools):
        return self

    def _next(self) -> AIMessage:
        idx = min(self._call, len(self._responses) - 1)
        self._call += 1
        return self._responses[idx]

    async def ainvoke(self, msgs) -> AIMessage:
        return self._next()

    async def astream(self, msgs):
        msg = self._next()
        yield msg


def _fake_llm_no_tools(text: str = "All good.") -> _FakeLLM:
    return _FakeLLM([AIMessage(content=text)])


def _fake_llm_one_tool_then_done(tool_name: str, tool_args: dict, final_text: str = "Done.") -> _FakeLLM:
    tc = {"id": "call_001", "name": tool_name, "args": tool_args}
    return _FakeLLM([
        AIMessage(content="", tool_calls=[tc]),
        AIMessage(content=final_text),
    ])


@pytest.mark.asyncio
class TestSweep:
    async def test_skipped_when_not_configured(self):
        a = Agent(provider="openrouter", model="x", auto_execute_risk="low")
        pool = _mock_pool()
        with patch.object(a, "_is_configured", return_value=False):
            result = await a.sweep(pool)
        assert result.get("skipped")

    async def test_sweep_runs_and_returns_summary(self):
        pool = _mock_pool()
        a = Agent(provider="ollama", model="test", auto_execute_risk="low")
        a._llm_base = _fake_llm_no_tools("Pipeline healthy.")
        result = await a.sweep(pool)
        assert "summary" in result
        assert "decisions" in result

    async def test_sweep_tool_call_executes_scratchpad(self):
        pool = _mock_pool()
        a = Agent(provider="ollama", model="test", auto_execute_risk="low")
        a._llm_base = _fake_llm_one_tool_then_done(
            "scratchpad", {"note": "pipeline looks fine"}, "Done."
        )
        result = await a.sweep(pool)
        assert result["summary"] == "Done."


@pytest.mark.asyncio
class TestChatStream:
    async def test_not_configured_yields_error_text(self):
        a = Agent(provider="openrouter", model="x", auto_execute_risk="low")
        pool = _mock_pool()
        with patch.object(a, "_is_configured", return_value=False):
            events = [e async for e in a.chat_stream(pool, [])]
        texts = [e["text"] for e in events if e["type"] == "text"]
        assert any("not configured" in t.lower() for t in texts)
        assert events[-1]["type"] == "done"

    async def test_plain_text_response(self):
        pool = _mock_pool()
        a = Agent(provider="ollama", model="test", auto_execute_risk="low")
        a._llm_base = _fake_llm_no_tools("Hello operator.")
        messages = [{"role": "user", "content": "status?"}]
        events = [e async for e in a.chat_stream(pool, messages)]
        text_events = [e for e in events if e["type"] == "text"]
        assert any("Hello operator" in e["text"] for e in text_events)
        assert events[-1]["type"] == "done"

    async def test_tool_call_emits_tool_events(self):
        pool = _mock_pool()
        a = Agent(provider="ollama", model="test", auto_execute_risk="low")
        tc = {"id": "c1", "name": "scratchpad", "args": {"note": "checking"}}
        a._llm_base = _FakeLLM([
            AIMessage(content="", tool_calls=[tc]),
            AIMessage(content="Noted."),
        ])
        messages = [{"role": "user", "content": "note something"}]
        events = [e async for e in a.chat_stream(pool, messages)]

        tool_call_events = [e for e in events if e["type"] == "tool_call"]
        tool_result_events = [e for e in events if e["type"] == "tool_result"]
        assert len(tool_call_events) == 1
        assert tool_call_events[0]["name"] == "scratchpad"
        assert len(tool_result_events) == 1

    async def test_done_event_always_last(self):
        pool = _mock_pool()
        a = Agent(provider="ollama", model="test", auto_execute_risk="low")
        a._llm_base = _fake_llm_no_tools("ok")
        events = [e async for e in a.chat_stream(pool, [])]
        assert events[-1]["type"] == "done"

    async def test_scratchpad_notes_appear_in_system_on_next_turn(self):
        """SELECT strategy: scratchpad notes injected into system message on next turn."""
        pool = _mock_pool()
        a = Agent(provider="ollama", model="test", auto_execute_risk="low")

        received_system_contents: list[str] = []

        class _CaptureLLM:
            _call = 0
            def bind_tools(self, tools): return self
            async def astream(self, msgs):
                sys_content = msgs[0].content if msgs else ""
                received_system_contents.append(sys_content)
                self._call += 1
                if self._call == 1:
                    tc = {"id": "c1", "name": "scratchpad", "args": {"note": "CANARY_NOTE"}}
                    yield AIMessage(content="", tool_calls=[tc])
                else:
                    yield AIMessage(content="done")

        a._llm_base = _CaptureLLM()
        _ = [e async for e in a.chat_stream(pool, [{"role": "user", "content": "go"}])]

        assert len(received_system_contents) >= 2
        assert "CANARY_NOTE" in received_system_contents[1]
