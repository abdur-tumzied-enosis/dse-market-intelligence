# tests/unit/test_chat_bengali.py
"""
Bengali language detection and handling tests.
The agent should respond in Bengali when user writes in Bengali.
"""
import pytest
from unittest.mock import AsyncMock

from chat.prompt import detect_language, build_system_message


# ── Language detection ────────────────────────────────────────────────────────

def test_detects_english():
    assert detect_language("What is the current price of BRACBANK?") == "en"


def test_detects_bengali_headline():
    assert detect_language("ব্র্যাক ব্যাংকের শেয়ার দাম কত?") == "bn"


def test_detects_bengali_in_mixed_sentence():
    assert detect_language("BRACBANK-এর EPS কত?") == "bn"


def test_detects_bengali_company_name():
    assert detect_language("গ্রামীণফোনের লভ্যাংশ কখন দেবে?") == "bn"


def test_detects_english_numbers_only():
    assert detect_language("BRACBANK 45.50 EPS") == "en"


def test_detects_bengali_numbers():
    assert detect_language("৪৫.৫০ টাকা") == "bn"


# ── System prompt Bengali content ─────────────────────────────────────────────

def test_bengali_system_has_dse_reference():
    msg = build_system_message(lang="bn")
    assert "ঢাকা স্টক এক্সচেঞ্জ" in msg.content


def test_bengali_system_has_disclaimer():
    msg = build_system_message(lang="bn")
    assert "দাবিত্যাগ" in msg.content


def test_bengali_system_has_market_hours():
    msg = build_system_message(lang="bn")
    assert "রবিবার" in msg.content


def test_english_system_has_disclaimer():
    msg = build_system_message(lang="en")
    assert "DISCLAIMER" in msg.content


# ── Bengali tool queries ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tools_accept_Bengali_ticker_normalized():
    """Tools normalize tickers to uppercase regardless of input case."""
    from chat.tools import build_tools
    pool = AsyncMock()
    pool.fetch.return_value = []
    pool.fetchrow.return_value = None
    tools = build_tools(pool)
    price_tool = next(t for t in tools if t.name == "get_stock_price")
    result = await price_tool.ainvoke({"ticker": "bracbank", "days": 5})
    assert result["ticker"] == "BRACBANK"


# ── Routing detects Bengali complex queries ────────────────────────────────────

def test_routing_detects_bengali_analysis():
    from chat.routing import is_complex_query
    assert is_complex_query("GP এর বিশ্লেষণ করুন এবং সেক্টরের সাথে তুলনা করুন")


def test_routing_simple_bengali_price_query():
    from chat.routing import is_complex_query
    assert not is_complex_query("BRACBANK দাম কত?")


def test_routing_detects_bengali_portfolio():
    from chat.routing import is_complex_query
    assert is_complex_query("আমার পোর্টফোলিও বিশ্লেষণ করুন")


def test_detect_language_returns_string():
    result = detect_language("test")
    assert isinstance(result, str)
    assert result in ("en", "bn")
