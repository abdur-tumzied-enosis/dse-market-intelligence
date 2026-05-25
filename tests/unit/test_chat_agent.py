# tests/unit/test_chat_agent.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_pool():
    pool = AsyncMock()
    pool.fetch.return_value = []
    pool.fetchrow.return_value = None
    pool.execute.return_value = None
    return pool


def _make_response(content: str, tool_calls: list | None = None):
    r = MagicMock()
    r.content = content
    r.tool_calls = tool_calls or []
    r.usage_metadata = {"input_tokens": 100, "output_tokens": 20}
    return r


@pytest.mark.asyncio
async def test_chat_stream_yields_text_and_done():
    from chat.agent import StockAnalystAgent

    pool = _make_pool()
    mock_response = _make_response("BRACBANK is trading well.")

    async def _astream(msgs):
        yield mock_response

    mock_llm = MagicMock()
    mock_llm.astream = _astream
    mock_llm.bind_tools.return_value = mock_llm

    with patch("chat.agent.make_routed_llm", return_value=mock_llm), \
         patch("chat.agent.build_rag_context", return_value=""):
        agent = StockAnalystAgent(provider="google", model="gemini-2.5-flash")
        chunks = []
        async for chunk in agent.chat_stream(
            pool,
            [{"role": "user", "content": "What is BRACBANK price?"}],
            session_id="test-session",
        ):
            chunks.append(chunk)

    types = [c["type"] for c in chunks]
    assert "done" in types
    text_chunks = [c for c in chunks if c["type"] == "text"]
    assert len(text_chunks) > 0
    assert "BRACBANK" in text_chunks[0]["text"]


@pytest.mark.asyncio
async def test_chat_stream_routing_event_emitted():
    from chat.agent import StockAnalystAgent

    pool = _make_pool()
    mock_response = _make_response("GP is at 45 BDT.")

    async def _astream(msgs):
        yield mock_response

    mock_llm = MagicMock()
    mock_llm.astream = _astream
    mock_llm.bind_tools.return_value = mock_llm

    with patch("chat.agent.make_routed_llm", return_value=mock_llm), \
         patch("chat.agent.build_rag_context", return_value=""):
        agent = StockAnalystAgent(provider="google", model="gemini-2.5-flash")
        chunks = []
        async for chunk in agent.chat_stream(
            pool, [{"role": "user", "content": "Price of GP?"}]
        ):
            chunks.append(chunk)

    routing_chunks = [c for c in chunks if c["type"] == "routing"]
    assert len(routing_chunks) == 1
    assert "thinking_mode" in routing_chunks[0]


@pytest.mark.asyncio
async def test_chat_stream_handles_tool_call():
    from chat.agent import StockAnalystAgent

    pool = _make_pool()
    tool_response = _make_response("", tool_calls=[
        {"id": "tc1", "name": "get_stock_price", "args": {"ticker": "GP", "days": 5}}
    ])
    final_response = _make_response("GP is at 45 BDT.")

    call_count = 0

    async def _astream(msgs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield tool_response
        else:
            yield final_response

    mock_llm = MagicMock()
    mock_llm.astream = _astream
    mock_llm.bind_tools.return_value = mock_llm

    with patch("chat.agent.make_routed_llm", return_value=mock_llm), \
         patch("chat.agent.build_rag_context", return_value=""):
        agent = StockAnalystAgent(provider="google", model="gemini-2.5-flash")
        chunks = []
        async for chunk in agent.chat_stream(
            pool, [{"role": "user", "content": "Price of GP?"}],
            session_id="test-session",
        ):
            chunks.append(chunk)

    tool_call_chunks = [c for c in chunks if c["type"] == "tool_call"]
    assert len(tool_call_chunks) >= 1
    assert tool_call_chunks[0]["name"] == "get_stock_price"
    assert chunks[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_chat_stream_logs_usage():
    from chat.agent import StockAnalystAgent

    pool = _make_pool()
    mock_response = _make_response("Done.")

    async def _astream(msgs):
        yield mock_response

    mock_llm = MagicMock()
    mock_llm.astream = _astream
    mock_llm.bind_tools.return_value = mock_llm

    with patch("chat.agent.make_routed_llm", return_value=mock_llm), \
         patch("chat.agent.build_rag_context", return_value=""):
        agent = StockAnalystAgent(provider="google", model="gemini-2.5-flash")
        async for _ in agent.chat_stream(pool, [{"role": "user", "content": "hello"}]):
            pass

    # pool.execute was called for usage logging
    assert pool.execute.called


@pytest.mark.asyncio
async def test_chat_stream_bengali_detected():
    from chat.agent import StockAnalystAgent

    pool = _make_pool()
    mock_response = _make_response("GP এর দাম ৪৫ টাকা।")

    async def _astream(msgs):
        yield mock_response

    mock_llm = MagicMock()
    mock_llm.astream = _astream
    mock_llm.bind_tools.return_value = mock_llm

    with patch("chat.agent.make_routed_llm", return_value=mock_llm), \
         patch("chat.agent.build_rag_context", return_value=""):
        agent = StockAnalystAgent(provider="google", model="gemini-2.5-flash")
        chunks = []
        async for chunk in agent.chat_stream(
            pool, [{"role": "user", "content": "GP এর দাম কত?"}]
        ):
            chunks.append(chunk)

    assert chunks[-1]["type"] == "done"
