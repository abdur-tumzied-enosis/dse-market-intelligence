# tests/unit/test_usage_logging.py
import pytest
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_log_llm_usage_inserts_row():
    from chat.usage import log_llm_usage
    pool = AsyncMock()
    pool.execute.return_value = None

    await log_llm_usage(
        pool=pool,
        session_id="test-session-123",
        provider="google",
        model="gemini-2.5-flash",
        input_tokens=500,
        output_tokens=200,
        thinking_tokens=0,
        tool_calls_n=2,
        latency_ms=1200,
        tier="free",
    )
    pool.execute.assert_called_once()
    sql = pool.execute.call_args[0][0]
    assert "llm_usage_log" in sql


def test_estimate_cost_google_non_thinking():
    from chat.usage import estimate_cost
    cost = estimate_cost("google", "gemini-2.5-flash", input_tokens=1_000_000, output_tokens=0)
    assert abs(cost - 0.15) < 0.01


def test_estimate_cost_zero_for_unknown():
    from chat.usage import estimate_cost
    cost = estimate_cost("ollama", "llama3", input_tokens=1000, output_tokens=1000)
    assert cost == 0.0


def test_estimate_cost_with_thinking_tokens():
    from chat.usage import estimate_cost
    cost = estimate_cost("google", "gemini-2.5-flash-thinking", input_tokens=1_000_000, output_tokens=0)
    assert abs(cost - 3.50) < 0.01


@pytest.mark.asyncio
async def test_log_llm_usage_non_fatal_on_db_error():
    from chat.usage import log_llm_usage
    pool = AsyncMock()
    pool.execute.side_effect = Exception("DB connection error")
    # Should not raise
    await log_llm_usage(
        pool=pool,
        session_id="err-session",
        provider="google",
        model="gemini-2.5-flash",
        input_tokens=100,
        output_tokens=50,
    )
