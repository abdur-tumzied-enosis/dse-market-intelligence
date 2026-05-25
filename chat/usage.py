# chat/usage.py
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_COST_TABLE: dict[str, dict[str, float]] = {
    "google:gemini-2.5-flash-thinking": {"in": 3.50, "out": 15.00},
    "google:gemini-2.5-flash":          {"in": 0.15, "out": 0.60},
    "google:gemini-2.5-pro":            {"in": 1.25, "out": 10.00},
    "openrouter:gpt-4o-mini":           {"in": 0.15, "out": 0.60},
    "openrouter:claude-3-5-haiku":      {"in": 0.80, "out": 4.00},
    "openrouter:gemini":                {"in": 0.15, "out": 0.60},
}


def estimate_cost(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    thinking_tokens: int = 0,
) -> float:
    """Estimate USD cost for a single LLM call."""
    key = f"{provider}:{model}".lower()
    rates = None
    for k, v in _COST_TABLE.items():
        if key.startswith(k) or k in key:
            rates = v
            break
    if rates is None:
        return 0.0
    total_in = input_tokens + thinking_tokens
    return (total_in * rates["in"] + output_tokens * rates["out"]) / 1_000_000


async def log_llm_usage(
    pool,
    session_id: str,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    thinking_tokens: int = 0,
    tool_calls_n: int = 0,
    latency_ms: int | None = None,
    error: str | None = None,
    tier: str = "free",
) -> None:
    """Write one row to llm_usage_log. Non-fatal on DB error."""
    cost = estimate_cost(provider, model, input_tokens, output_tokens, thinking_tokens)
    try:
        await pool.execute(
            """
            INSERT INTO llm_usage_log
                (session_id, provider, model, input_tokens, output_tokens,
                 thinking_tokens, cost_usd, tool_calls_n, latency_ms, error, tier)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            """,
            session_id, provider, model,
            input_tokens, output_tokens, thinking_tokens,
            cost, tool_calls_n, latency_ms, error, tier,
        )
    except Exception as exc:
        log.warning("log_llm_usage failed: %s", exc)
