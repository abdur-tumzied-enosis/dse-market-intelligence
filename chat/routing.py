# chat/routing.py
"""
Model routing: thinking mode for complex analysis, fast mode for simple lookups.
Thinking mode only applies to Gemini 2.5 Flash (provider=google).
"""
from __future__ import annotations

_COMPLEX_KEYWORDS_EN = {
    "analyze", "analysis", "compare", "comparison", "explain", "why", "strategy",
    "recommend", "predict", "forecast", "evaluate", "assess", "review", "portfolio",
    "valuation", "risk", "outlook",
}
_COMPLEX_KEYWORDS_BN = {
    "বিশ্লেষণ", "তুলনা", "পূর্বাভাস", "কৌশল", "পোর্টফোলিও", "মূল্যায়ন",
}

_LONG_QUERY_CHARS = 200
_THINKING_BUDGET_COMPLEX = 1024
_THINKING_BUDGET_SIMPLE = 0


def is_complex_query(message: str) -> bool:
    """Return True if the query requires deep reasoning (thinking mode)."""
    if len(message) > _LONG_QUERY_CHARS:
        return True
    words = set(message.lower().split())
    if words & _COMPLEX_KEYWORDS_EN:
        return True
    for kw in _COMPLEX_KEYWORDS_BN:
        if kw in message:
            return True
    return False


def get_thinking_budget(is_complex: bool) -> int:
    return _THINKING_BUDGET_COMPLEX if is_complex else _THINKING_BUDGET_SIMPLE


def make_routed_llm(provider: str, model: str, settings, is_complex: bool):
    """Return a LangChain LLM configured for the detected complexity tier."""
    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        thinking_budget = get_thinking_budget(is_complex)
        return ChatGoogleGenerativeAI(
            model=model or "gemini-2.5-flash",
            api_key=settings.google_api_key,
            request_timeout=120 if is_complex else 60,
            max_retries=3,
            generation_config={"thinking_config": {"thinking_budget": thinking_budget}},
        )
    if provider == "vertex":
        from langchain_google_vertexai import ChatVertexAI
        thinking_budget = get_thinking_budget(is_complex)
        return ChatVertexAI(
            model=model or "gemini-2.5-flash",
            project=settings.gcp_project,
            location=settings.gcp_location,
            max_retries=3,
            thinking_budget=thinking_budget,
        )
    from mgmt.agent.llm import make_llm
    return make_llm(provider, model, settings)
