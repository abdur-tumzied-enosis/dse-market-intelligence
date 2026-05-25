# tests/unit/test_chat_integration.py
"""Verify the full Layer 4 chain: agent -> tools -> usage logging."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_pool():
    pool = AsyncMock()
    pool.fetch.return_value = []
    pool.fetchrow.return_value = None
    pool.execute.return_value = None
    return pool


@pytest.mark.asyncio
async def test_full_chat_pipeline_yields_done():
    from chat.agent import StockAnalystAgent

    pool = _make_pool()

    mock_response = MagicMock()
    mock_response.content = "GP closed at 45 BDT yesterday."
    mock_response.tool_calls = []
    mock_response.usage_metadata = {"input_tokens": 200, "output_tokens": 50}

    mock_llm = MagicMock()

    async def _stream(msgs):
        yield mock_response

    mock_llm.astream = _stream
    mock_llm.bind_tools.return_value = mock_llm

    with patch("chat.agent.make_routed_llm", return_value=mock_llm), \
         patch("chat.agent.build_rag_context", return_value=""):
        agent = StockAnalystAgent(provider="google", model="gemini-2.5-flash")
        result = []
        async for chunk in agent.chat_stream(
            pool,
            [{"role": "user", "content": "Price of GP?"}],
            session_id="integ-test",
            tier="pro",
        ):
            result.append(chunk)

    assert result[-1]["type"] == "done"
    text_parts = [c["text"] for c in result if c["type"] == "text"]
    assert any("GP" in t or "45" in t for t in text_parts)
    assert pool.execute.called  # usage logged


def test_all_8_tools_present():
    from chat.tools import build_tools
    pool = AsyncMock()
    tools = build_tools(pool)
    names = {t.name for t in tools}
    expected = {
        "get_stock_price", "get_fundamentals", "get_sector_comparison",
        "search_news", "get_ml_prediction", "screen_stocks",
        "get_portfolio_analysis", "get_macro_data",
    }
    assert names == expected


def test_chat_router_mounted_in_main():
    """Verify /api/chat route is registered in the FastAPI app."""
    from mgmt.main import app
    routes = {r.path for r in app.routes}
    assert "/api/chat" in routes


def test_chat_agent_initialized_in_config():
    """Verify config has chat agent settings."""
    from mgmt.config import get_settings
    settings = get_settings()
    assert hasattr(settings, "chat_agent_provider")
    assert hasattr(settings, "chat_agent_model")


def test_stock_analyst_agent_importable():
    from chat import StockAnalystAgent
    agent = StockAnalystAgent(provider="google", model="gemini-2.5-flash")
    assert agent._provider == "google"
    assert agent._model == "gemini-2.5-flash"


def test_chat_request_model():
    from mgmt.routers.chat import ChatRequest
    req = ChatRequest(messages=[{"role": "user", "content": "hello"}])
    assert req.tier == "free"
    assert len(req.session_id) > 0


def test_usage_estimate_cost_coverage():
    from chat.usage import estimate_cost
    # Verify all major provider/model combinations return non-negative costs
    cases = [
        ("google", "gemini-2.5-flash", 1000, 500),
        ("google", "gemini-2.5-pro", 1000, 500),
        ("openrouter", "gpt-4o-mini", 1000, 500),
        ("ollama", "llama3", 1000, 500),
    ]
    for provider, model, inp, out in cases:
        cost = estimate_cost(provider, model, inp, out)
        assert cost >= 0.0, f"Negative cost for {provider}/{model}"
