# api/routers/analyze.py
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Query
from api.deps import get_current_user, get_db

router = APIRouter(tags=["analyze"])


async def _fetch_ticker_analysis(conn, ticker: str) -> dict:
    """Assemble full analysis for one ticker from DB tables."""
    company = await conn.fetchrow(
        "SELECT ticker, name, sector, category, market_cap_bdt FROM companies WHERE ticker = $1",
        ticker,
    )
    if not company:
        return {}

    fundamentals = await conn.fetch(
        """
        SELECT fiscal_year, eps, nav, pe, cash_div_pct, stock_div_pct, fetched_at
        FROM fundamentals WHERE ticker = $1
        ORDER BY fiscal_year DESC NULLS LAST
        LIMIT 8
        """,
        ticker,
    )

    predictions = await conn.fetch(
        """
        SELECT DISTINCT ON (horizon_days) horizon_days, predicted_direction, confidence, target_price, predicted_at
        FROM ml_predictions WHERE ticker = $1
        ORDER BY horizon_days, predicted_at DESC
        """,
        ticker,
    )

    health_score = await conn.fetchrow(
        """
        SELECT health_score, fundamental_score, momentum_score, valuation_score, sentiment_score, scored_at
        FROM stock_scores WHERE ticker = $1
        ORDER BY scored_at DESC LIMIT 1
        """,
        ticker,
    )

    latest_price = await conn.fetchrow(
        "SELECT close, change_pct, high, low, volume, time FROM stock_prices WHERE ticker = $1 ORDER BY time DESC LIMIT 1",
        ticker,
    )

    recent_news = await conn.fetch(
        """
        SELECT title, published_at, sentiment_score, url
        FROM news WHERE $1 = ANY(tickers)
        ORDER BY published_at DESC LIMIT 5
        """,
        ticker,
    )

    return {
        "ticker": ticker,
        "company": dict(company),
        "latest_price": dict(latest_price) if latest_price else None,
        "fundamentals": [dict(r) for r in fundamentals],
        "predictions": [dict(r) for r in predictions],
        "health_score": dict(health_score) if health_score else None,
        "recent_news": [dict(r) for r in recent_news],
    }


@router.get("/analyze/{ticker}")
async def analyze_ticker(
    ticker: str,
    pool=Depends(get_db),
    _user=Depends(get_current_user),
):
    ticker = ticker.upper()
    async with pool.acquire() as conn:
        result = await _fetch_ticker_analysis(conn, ticker)
    if not result:
        raise HTTPException(status_code=404, detail=f"Ticker {ticker} not found")
    return result


@router.get("/compare")
async def compare_tickers(
    tickers: list[str] = Query(min_length=2, max_length=5),
    pool=Depends(get_db),
    _user=Depends(get_current_user),
):
    tickers = [t.upper() for t in tickers]
    results = []
    async with pool.acquire() as conn:
        for ticker in tickers:
            analysis = await _fetch_ticker_analysis(conn, ticker)
            if analysis:
                results.append(analysis)
    return {"tickers": tickers, "analyses": results}
