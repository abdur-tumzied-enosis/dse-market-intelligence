# tests/unit/test_chat_tools.py
import pytest
from unittest.mock import AsyncMock, MagicMock


class _Row(dict):
    """Dict subclass that also supports attribute access — mimics asyncpg Record."""
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k)


def _make_row(**kwargs) -> _Row:
    return _Row(kwargs)


def _make_pool_fetch(rows: list[dict]) -> AsyncMock:
    pool = AsyncMock()
    pool.fetch.return_value = [_make_row(**r) for r in rows]
    pool.fetchrow.return_value = _make_row(**rows[0]) if rows else None
    return pool


@pytest.mark.asyncio
async def test_get_stock_price_returns_ticker():
    from chat.tools import build_tools
    pool = AsyncMock()
    pool.fetch.return_value = [_make_row(date="2026-01-01", open=10.0, high=11.0, low=9.5, close=10.5, volume=100)]
    tools = build_tools(pool)
    t = next(x for x in tools if x.name == "get_stock_price")
    result = await t.ainvoke({"ticker": "BRACBANK", "days": 5})
    assert result["ticker"] == "BRACBANK"


@pytest.mark.asyncio
async def test_get_fundamentals_returns_dict():
    from chat.tools import build_tools
    pool = AsyncMock()
    pool.fetchrow.return_value = _make_row(ticker="GP", eps=5.0, nav=30.0, pe_ratio=12.0, fiscal_year=2024,
                                           cash_dividend_pct=10.0, stock_dividend_pct=0.0, fetched_at="2026-01-01",
                                           sector="Telecom", company_name="Grameenphone")
    pool.fetch.return_value = []
    tools = build_tools(pool)
    t = next(x for x in tools if x.name == "get_fundamentals")
    result = await t.ainvoke({"ticker": "GP"})
    assert isinstance(result, dict)


@pytest.mark.asyncio
async def test_get_fundamentals_no_data():
    from chat.tools import build_tools
    pool = AsyncMock()
    pool.fetchrow.return_value = None
    pool.fetch.return_value = []
    tools = build_tools(pool)
    t = next(x for x in tools if x.name == "get_fundamentals")
    result = await t.ainvoke({"ticker": "UNKNOWN"})
    assert "error" in result


@pytest.mark.asyncio
async def test_search_news_by_ticker():
    from chat.tools import build_tools
    pool = AsyncMock()
    pool.fetch.return_value = [
        _make_row(headline="GP earnings up", published_at="2026-01-01",
                  source="FE", tickers=["GP"], sentiment_score=0.5,
                  sentiment_label="positive", url="http://example.com")
    ]
    tools = build_tools(pool)
    t = next(x for x in tools if x.name == "search_news")
    result = await t.ainvoke({"query": "earnings", "ticker": "GP", "limit": 3})
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_get_ml_prediction_structure():
    from chat.tools import build_tools
    pool = AsyncMock()
    pool.fetch.return_value = [
        _make_row(horizon_days=5, predicted_direction="up", confidence=0.72,
                  target_price=50.0, predicted_at="2026-01-01", model_version="lstm_v0")
    ]
    pool.fetchrow.return_value = None
    tools = build_tools(pool)
    t = next(x for x in tools if x.name == "get_ml_prediction")
    result = await t.ainvoke({"ticker": "BRACBANK"})
    assert "ticker" in result
    assert "predictions" in result


@pytest.mark.asyncio
async def test_screen_stocks_returns_list():
    from chat.tools import build_tools
    pool = AsyncMock()
    pool.fetch.return_value = [
        _make_row(ticker="BRACBANK", sector="Banking", eps=5.0, pe_ratio=8.0,
                  nav=30.0, health_score=72.0, scored_at="2026-01-01")
    ]
    tools = build_tools(pool)
    t = next(x for x in tools if x.name == "screen_stocks")
    result = await t.ainvoke({"max_pe": 15.0, "limit": 5})
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_get_macro_data_returns_list():
    from chat.tools import build_tools
    pool = AsyncMock()
    pool.fetch.return_value = [
        _make_row(indicator_name="usd_bdt", value=110.5, unit="BDT",
                  recorded_at="2026-01-01", source="bb")
    ]
    tools = build_tools(pool)
    t = next(x for x in tools if x.name == "get_macro_data")
    result = await t.ainvoke({"days": 30})
    assert isinstance(result, list)


def test_all_8_tools_present():
    from chat.tools import build_tools
    pool = AsyncMock()
    tools = build_tools(pool)
    names = {t.name for t in tools}
    assert names == {
        "get_stock_price", "get_fundamentals", "get_sector_comparison",
        "search_news", "get_ml_prediction", "screen_stocks",
        "get_portfolio_analysis", "get_macro_data",
    }
