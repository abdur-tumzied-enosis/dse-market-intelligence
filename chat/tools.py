# chat/tools.py
"""
8 read-only tools for the StockAnalystAgent.
All tools receive an asyncpg pool via the build_tools() factory closure.
"""
from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field


# ── Input schemas ─────────────────────────────────────────────────────────────

class GetStockPriceInput(BaseModel):
    ticker: str = Field(description="DSE ticker symbol (uppercase), e.g. 'BRACBANK'")
    days: int = Field(default=30, ge=1, le=365, description="Days of price history to return")


class GetFundamentalsInput(BaseModel):
    ticker: str = Field(description="DSE ticker symbol")


class GetSectorComparisonInput(BaseModel):
    ticker: str = Field(description="DSE ticker symbol")


class SearchNewsInput(BaseModel):
    query: str = Field(description="Keyword search term")
    ticker: str | None = Field(default=None, description="Filter results to a specific ticker")
    limit: int = Field(default=5, ge=1, le=20)


class GetMLPredictionInput(BaseModel):
    ticker: str = Field(description="DSE ticker symbol")


class ScreenStocksInput(BaseModel):
    sector: str | None = Field(default=None, description="Sector name filter")
    min_pe: float | None = Field(default=None, description="Minimum PE ratio")
    max_pe: float | None = Field(default=None, description="Maximum PE ratio")
    min_eps_growth: float | None = Field(default=None, description="Minimum 1-year EPS growth (e.g. 0.1 = 10%)")
    min_health_score: float | None = Field(default=None, description="Minimum stock health score (0-100)")
    limit: int = Field(default=20, ge=1, le=50)


class GetPortfolioAnalysisInput(BaseModel):
    tickers: list[str] = Field(description="List of DSE ticker symbols to analyze")
    days: int = Field(default=30, ge=7, le=365)


class GetMacroDataInput(BaseModel):
    indicator: str | None = Field(default=None, description="Specific indicator: 'usd_bdt', 'policy_rate', 'cpi', 'remittance', 'gdp_growth'")
    days: int = Field(default=90, ge=1, le=730)


_MAX_ROWS = 50


def build_tools(pool) -> list:
    """Return @tool instances closed over the given asyncpg pool."""

    @tool("get_stock_price", args_schema=GetStockPriceInput)
    async def get_stock_price(ticker: str, days: int = 30) -> dict:
        """Fetch recent OHLCV price data for a DSE-listed stock. Returns close price, volume, and summary stats."""
        rows = await pool.fetch(
            """
            SELECT time::date AS date, open, high, low, close, volume
            FROM stock_prices
            WHERE ticker = $1 AND time >= NOW() - ($2 * INTERVAL '1 day')
            ORDER BY time DESC
            LIMIT $3
            """,
            ticker.upper(), days, min(days, _MAX_ROWS),
        )
        if not rows:
            return {"ticker": ticker.upper(), "error": "No price data found", "rows": []}
        data = [dict(r) for r in rows]
        closes = [r["close"] for r in data if r["close"]]
        return {
            "ticker": ticker.upper(),
            "rows": data,
            "latest_close": closes[0] if closes else None,
            "period_high": max(r["high"] for r in data if r["high"]),
            "period_low": min(r["low"] for r in data if r["low"]),
        }

    @tool("get_fundamentals", args_schema=GetFundamentalsInput)
    async def get_fundamentals(ticker: str) -> dict:
        """Fetch fundamental financial data: EPS, NAV, PE ratio, dividends, and multi-year fiscal history."""
        row = await pool.fetchrow(
            """
            SELECT f.ticker, f.eps, f.nav, f.pe_ratio, f.cash_dividend_pct,
                   f.stock_dividend_pct, f.fiscal_year, f.fetched_at,
                   c.sector, c.name AS company_name
            FROM fundamentals f
            JOIN companies c USING (ticker)
            WHERE f.ticker = $1
            ORDER BY f.fetched_at DESC
            LIMIT 1
            """,
            ticker.upper(),
        )
        history = await pool.fetch(
            """
            SELECT fiscal_year, eps, nav, pe_ratio, cash_dividend_pct, stock_dividend_pct
            FROM fundamentals
            WHERE ticker = $1 AND fiscal_year IS NOT NULL
            ORDER BY fiscal_year DESC
            LIMIT 5
            """,
            ticker.upper(),
        )
        if not row:
            return {"ticker": ticker.upper(), "error": "No fundamental data found"}
        return {
            **dict(row),
            "history": [dict(r) for r in history],
        }

    @tool("get_sector_comparison", args_schema=GetSectorComparisonInput)
    async def get_sector_comparison(ticker: str) -> dict:
        """Compare a stock's PE ratio, EPS, and NAV against its sector average and peers."""
        company = await pool.fetchrow(
            "SELECT sector FROM companies WHERE ticker = $1",
            ticker.upper(),
        )
        if not company:
            return {"error": f"Ticker {ticker.upper()} not found"}

        sector = company["sector"]
        target = await pool.fetchrow(
            """
            SELECT eps, nav, pe_ratio FROM fundamentals
            WHERE ticker = $1 ORDER BY fetched_at DESC LIMIT 1
            """,
            ticker.upper(),
        )
        # sector_pe carries the DSE sectoral median P/E (column `pe`); it has no
        # EPS/NAV. DSE labels use '&' ('Food & Allied') while companies.sector
        # spells 'and', so match on the normalized name.
        sector_row = await pool.fetchrow(
            """
            SELECT pe, change_pct, fetched_at FROM sector_pe
            WHERE lower(replace(sector, '&', 'and')) = lower(replace($1, '&', 'and'))
            ORDER BY fetched_at DESC LIMIT 1
            """,
            sector,
        )
        peers = await pool.fetch(
            """
            SELECT f.ticker, f.eps, f.pe_ratio
            FROM fundamentals f
            JOIN companies c USING (ticker)
            WHERE c.sector = $1 AND f.ticker != $2
            ORDER BY f.fetched_at DESC
            LIMIT 10
            """,
            sector, ticker.upper(),
        )
        return {
            "ticker": ticker.upper(),
            "sector": sector,
            "target": dict(target) if target else {},
            "sector_avg": dict(sector_row) if sector_row else {},
            "peers": [dict(r) for r in peers],
        }

    @tool("search_news", args_schema=SearchNewsInput)
    async def search_news(query: str, ticker: str | None = None, limit: int = 5) -> list[dict]:
        """Search recent news articles. Filter by ticker or keyword query. Returns headline, source, date, sentiment."""
        if ticker:
            rows = await pool.fetch(
                """
                SELECT headline, source, published_at, sentiment_score, sentiment_label, url, tickers
                FROM news
                WHERE $1 = ANY(tickers)
                ORDER BY published_at DESC
                LIMIT $2
                """,
                ticker.upper(), limit,
            )
        else:
            rows = await pool.fetch(
                """
                SELECT headline, source, published_at, sentiment_score, sentiment_label, url, tickers
                FROM news
                WHERE headline ILIKE $1 OR body ILIKE $1
                ORDER BY published_at DESC
                LIMIT $2
                """,
                f"%{query}%", limit,
            )
        return [dict(r) for r in rows]

    @tool("get_ml_prediction", args_schema=GetMLPredictionInput)
    async def get_ml_prediction(ticker: str) -> dict:
        """Get ML-based price direction predictions (5/10/20 day horizons) and composite stock health score."""
        predictions = await pool.fetch(
            """
            SELECT horizon_days, predicted_direction, confidence, target_price, predicted_at, model_version
            FROM ml_predictions
            WHERE ticker = $1
            ORDER BY predicted_at DESC, horizon_days ASC
            LIMIT 6
            """,
            ticker.upper(),
        )
        score = await pool.fetchrow(
            """
            SELECT health_score, fundamental_score, momentum_score, valuation_score,
                   sentiment_score, scored_at
            FROM stock_scores
            WHERE ticker = $1
            ORDER BY scored_at DESC
            LIMIT 1
            """,
            ticker.upper(),
        )
        return {
            "ticker": ticker.upper(),
            "predictions": [dict(r) for r in predictions],
            "health_score": dict(score) if score else None,
        }

    @tool("screen_stocks", args_schema=ScreenStocksInput)
    async def screen_stocks(
        sector: str | None = None,
        min_pe: float | None = None,
        max_pe: float | None = None,
        min_eps_growth: float | None = None,
        min_health_score: float | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Screen DSE stocks by fundamental and ML criteria. Returns ranked list with PE, EPS, health score."""
        conditions = ["c.is_active = true"]
        params: list[Any] = []
        i = 1

        if sector:
            conditions.append(f"c.sector ILIKE ${i}")
            params.append(f"%{sector}%")
            i += 1
        if min_pe is not None:
            conditions.append(f"f.pe_ratio >= ${i}")
            params.append(min_pe)
            i += 1
        if max_pe is not None:
            conditions.append(f"f.pe_ratio <= ${i}")
            params.append(max_pe)
            i += 1
        if min_health_score is not None:
            conditions.append(f"ss.health_score >= ${i}")
            params.append(min_health_score)
            i += 1

        params.append(limit)
        where = " AND ".join(conditions)
        rows = await pool.fetch(
            f"""
            SELECT c.ticker, c.sector, f.eps, f.pe_ratio, f.nav,
                   ss.health_score, ss.scored_at
            FROM companies c
            LEFT JOIN LATERAL (
                SELECT eps, pe_ratio, nav FROM fundamentals
                WHERE ticker = c.ticker ORDER BY fetched_at DESC LIMIT 1
            ) f ON true
            LEFT JOIN LATERAL (
                SELECT health_score, scored_at FROM stock_scores
                WHERE ticker = c.ticker ORDER BY scored_at DESC LIMIT 1
            ) ss ON true
            WHERE {where}
            ORDER BY ss.health_score DESC NULLS LAST
            LIMIT ${i}
            """,
            *params,
        )
        return [dict(r) for r in rows]

    @tool("get_portfolio_analysis", args_schema=GetPortfolioAnalysisInput)
    async def get_portfolio_analysis(tickers: list[str], days: int = 30) -> dict:
        """Analyze a portfolio of DSE stocks: recent returns, volatility, and pairwise correlation."""
        tickers_upper = [t.upper() for t in tickers[:10]]
        rows = await pool.fetch(
            """
            SELECT ticker, time::date AS date, close
            FROM stock_prices
            WHERE ticker = ANY($1) AND time >= NOW() - ($2 * INTERVAL '1 day')
            ORDER BY ticker, time ASC
            """,
            tickers_upper, days,
        )
        by_ticker: dict[str, list] = {}
        for r in rows:
            by_ticker.setdefault(r["ticker"], []).append(float(r["close"] or 0))

        stats = {}
        for t, prices in by_ticker.items():
            if len(prices) >= 2:
                ret = (prices[-1] - prices[0]) / prices[0] if prices[0] else 0
                import statistics
                rets = [(prices[i] - prices[i - 1]) / prices[i - 1] for i in range(1, len(prices)) if prices[i - 1]]
                vol = statistics.stdev(rets) if len(rets) > 1 else 0
                stats[t] = {"return_pct": round(ret * 100, 2), "volatility": round(vol * 100, 4), "data_points": len(prices)}

        return {"tickers": tickers_upper, "period_days": days, "stats": stats}

    @tool("get_macro_data", args_schema=GetMacroDataInput)
    async def get_macro_data(indicator: str | None = None, days: int = 90) -> list[dict]:
        """Fetch Bangladesh macroeconomic indicators: USD/BDT rate, policy rate, CPI, remittance, GDP growth."""
        if indicator:
            rows = await pool.fetch(
                """
                SELECT indicator_name, value, unit, recorded_at, source
                FROM macro_indicators
                WHERE indicator_name ILIKE $1 AND recorded_at >= NOW() - ($2 * INTERVAL '1 day')
                ORDER BY recorded_at DESC
                LIMIT 20
                """,
                f"%{indicator}%", days,
            )
        else:
            rows = await pool.fetch(
                """
                SELECT DISTINCT ON (indicator_name)
                    indicator_name, value, unit, recorded_at, source
                FROM macro_indicators
                WHERE recorded_at >= NOW() - ($1 * INTERVAL '1 day')
                ORDER BY indicator_name, recorded_at DESC
                """,
                days,
            )
        return [dict(r) for r in rows]

    return [
        get_stock_price,
        get_fundamentals,
        get_sector_comparison,
        search_news,
        get_ml_prediction,
        screen_stocks,
        get_portfolio_analysis,
        get_macro_data,
    ]
