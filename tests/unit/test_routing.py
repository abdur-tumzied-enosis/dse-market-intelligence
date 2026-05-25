# tests/unit/test_routing.py
from chat.routing import is_complex_query, get_thinking_budget


def test_simple_query_is_not_complex():
    assert not is_complex_query("What is the price of BRACBANK?")


def test_complex_query_contains_analyze():
    assert is_complex_query("Analyze the fundamentals of GP and compare with sector peers")


def test_complex_query_long_text():
    long = "Tell me " * 30  # >200 chars
    assert is_complex_query(long)


def test_thinking_budget_complex():
    assert get_thinking_budget(is_complex=True) == 1024


def test_thinking_budget_simple():
    assert get_thinking_budget(is_complex=False) == 0


def test_bengali_complex_keywords():
    assert is_complex_query("GP এর মৌলিক বিশ্লেষণ করুন")


def test_simple_bengali_price_query():
    assert not is_complex_query("BRACBANK দাম কত?")


def test_complex_keyword_recommend():
    assert is_complex_query("Do you recommend buying this stock?")


def test_complex_keyword_risk():
    assert is_complex_query("What is the risk profile of this portfolio?")
