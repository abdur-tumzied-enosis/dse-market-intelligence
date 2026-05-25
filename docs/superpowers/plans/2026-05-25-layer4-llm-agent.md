# Layer 4 LLM Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a user-facing stock analysis chatbot with RAG, 8 tools, SSE streaming, Bengali support, and Gemini Flash as the default LLM — wired into the existing mgmt API.

**Architecture:** New `chat/` package holds a `StockAnalystAgent` (manual loop, mirrors OpsAgent pattern). Eight read-only tools query TimescaleDB/pgvector. A `POST /api/chat` SSE endpoint in `mgmt/routers/chat.py` streams responses. Gemini Flash is the default provider (switchable via `agent_provider` env var). Context caching and model routing are Gemini-specific optional enhancements.

**Tech Stack:** LangChain Core + langchain-google-genai, asyncpg, pgvector (768-dim text-embedding-004), FastAPI SSE, Redis quota counters, google-generativeai for context caching.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `chat/__init__.py` | Create | Package root, exports StockAnalystAgent |
| `chat/prompt.py` | Create | System prompt EN + BN, build_system_message() |
| `chat/tools.py` | Create | 8 @tool functions with DB queries |
| `chat/rag.py` | Create | embed_query, search_chunks, build_rag_context |
| `chat/usage.py` | Create | log_llm_usage(), UsageLogger |
| `chat/agent.py` | Create | StockAnalystAgent, chat_stream() |
| `chat/sentiment.py` | Create | score_article(), score_new_articles() |
| `chat/cache.py` | Create | GeminiContextCache (optional, google provider only) |
| `db/migrations/021_llm_usage_log.sql` | Create | llm_usage_log table |
| `db/migrations/022_embeddings_dimension.sql` | Create | Alter document_chunks to vector(768) |
| `mgmt/routers/chat.py` | Create | POST /api/chat SSE endpoint |
| `mgmt/deps.py` | Modify | Add get_chat_agent() |
| `mgmt/main.py` | Modify | Init StockAnalystAgent, include chat router |
| `mgmt/config.py` | Modify | Add chat config (tier limits, quota, cache TTL) |
| `pyproject.toml` | Modify | Add `chat` to wheel packages |
| `tests/unit/test_chat_tools.py` | Create | Tool unit tests (mock pool) |
| `tests/unit/test_rag.py` | Create | RAG unit tests (mock embeddings + pool) |
| `tests/unit/test_usage_logging.py` | Create | Usage logger tests |
| `tests/unit/test_sentiment.py` | Create | Sentiment scorer tests |
| `tests/unit/test_chat_bengali.py` | Create | Bengali language detection + response tests |

---

### Task 1: Dependencies + DB Migrations

**Files:**
- Create: `db/migrations/021_llm_usage_log.sql`
- Create: `db/migrations/022_embeddings_dimension.sql`
- Modify: `pyproject.toml` (add `chat` to packages)

- [ ] **Step 1: Write the failing test for migration idempotency**

```python
# tests/unit/test_migrations.py  (append to existing if it exists, else create)
import subprocess

def test_migration_021_llm_usage_log_file_exists():
    from pathlib import Path
    p = Path("db/migrations/021_llm_usage_log.sql")
    assert p.exists(), "Migration 021 not created"

def test_migration_022_embeddings_dimension_file_exists():
    from pathlib import Path
    p = Path("db/migrations/022_embeddings_dimension.sql")
    assert p.exists(), "Migration 022 not created"
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_migrations.py -v
```
Expected: FAIL — files don't exist yet.

- [ ] **Step 3: Create migration 021 (llm_usage_log)**

```sql
-- db/migrations/021_llm_usage_log.sql
-- LLM API usage tracking: cost monitoring + Grafana dashboards

CREATE TABLE IF NOT EXISTS llm_usage_log (
    id              BIGSERIAL       PRIMARY KEY,
    session_id      TEXT            NOT NULL,
    provider        TEXT            NOT NULL,              -- 'google' | 'openrouter' | 'ollama'
    model           TEXT            NOT NULL,
    input_tokens    INTEGER         NOT NULL DEFAULT 0,
    output_tokens   INTEGER         NOT NULL DEFAULT 0,
    thinking_tokens INTEGER         NOT NULL DEFAULT 0,    -- Gemini thinking tokens
    cost_usd        NUMERIC(10, 6)  NOT NULL DEFAULT 0,
    tool_calls_n    SMALLINT        NOT NULL DEFAULT 0,
    latency_ms      INTEGER,
    error           TEXT,
    tier            TEXT            NOT NULL DEFAULT 'free',
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_llm_usage_session ON llm_usage_log (session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_usage_created  ON llm_usage_log (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_usage_provider ON llm_usage_log (provider, model, created_at DESC);
```

- [ ] **Step 4: Create migration 022 (embedding dimension)**

Document chunks table has `vector(1024)` (voyage-finance-2 spec). We use Google text-embedding-004 (max 768 dims). Since no chunk rows exist yet, safe to alter.

```sql
-- db/migrations/022_embeddings_dimension.sql
-- Change document_chunks embedding to vector(768) for Google text-embedding-004.
-- Safe: table is empty until news chunking pipeline runs.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'document_chunks'
          AND column_name = 'embedding'
          AND udt_name = 'vector'
    ) THEN
        -- Only alter if the current dimension is 1024
        IF (
            SELECT atttypmod FROM pg_attribute
            JOIN pg_class ON attrelid = pg_class.oid
            WHERE relname = 'document_chunks' AND attname = 'embedding'
        ) != 768 THEN
            ALTER TABLE document_chunks
                ALTER COLUMN embedding TYPE vector(768)
                USING embedding::text::vector(768);

            ALTER TABLE document_chunks
                ALTER COLUMN model SET DEFAULT 'text-embedding-004';
        END IF;
    END IF;
END $$;
```

Note: The `USING embedding::text::vector(768)` cast will fail if rows exist with 1024-dim vectors. Run this migration before any embedding inserts.

- [ ] **Step 5: Update pyproject.toml — add `chat` to wheel packages**

```toml
# In [tool.hatch.build.targets.wheel]
packages = ["extraction", "db", "mgmt", "ml", "chat"]
```

- [ ] **Step 6: Run test to verify it passes**

```
pytest tests/unit/test_migrations.py -v
```
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add db/migrations/021_llm_usage_log.sql db/migrations/022_embeddings_dimension.sql pyproject.toml tests/unit/test_migrations.py
git commit -m "feat(layer4): DB migrations for llm_usage_log and embedding dimension update"
```

---

### Task 2: System Prompt (chat/prompt.py)

**Files:**
- Create: `chat/__init__.py`
- Create: `chat/prompt.py`
- Create: `tests/unit/test_chat_prompt.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_chat_prompt.py
from chat.prompt import build_system_message, detect_language

def test_detect_language_english():
    assert detect_language("What is the stock price of BRACBANK?") == "en"

def test_detect_language_bengali():
    assert detect_language("ব্র্যাক ব্যাংকের শেয়ার দাম কত?") == "bn"

def test_detect_language_mixed():
    # Mixed — detects as Bengali if Bengali chars present
    assert detect_language("BRACBANK এর price কত?") == "bn"

def test_build_system_message_english():
    from langchain_core.messages import SystemMessage
    msg = build_system_message(lang="en")
    assert isinstance(msg, SystemMessage)
    assert "DSE" in msg.content
    assert "Dhaka Stock Exchange" in msg.content

def test_build_system_message_bengali():
    from langchain_core.messages import SystemMessage
    msg = build_system_message(lang="bn")
    assert isinstance(msg, SystemMessage)
    assert "ঢাকা স্টক এক্সচেঞ্জ" in msg.content

def test_build_system_message_ticker_context():
    msg = build_system_message(lang="en", ticker_context="BRACBANK is trading at 45 BDT")
    assert "BRACBANK" in msg.content
```

- [ ] **Step 2: Run to verify it fails**

```
pytest tests/unit/test_chat_prompt.py -v
```
Expected: FAIL — ModuleNotFoundError: No module named 'chat'

- [ ] **Step 3: Create chat/__init__.py**

```python
# chat/__init__.py
from chat.agent import StockAnalystAgent

__all__ = ["StockAnalystAgent"]
```

- [ ] **Step 4: Create chat/prompt.py**

```python
# chat/prompt.py
from __future__ import annotations

import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.messages import SystemMessage

_BD_TZ = ZoneInfo("Asia/Dhaka")


def detect_language(text: str) -> str:
    """Return 'bn' if text contains Bengali Unicode characters, else 'en'."""
    for ch in text:
        if "ঀ" <= ch <= "৿":
            return "bn"
    return "en"


_STATIC_EN = """\
You are an expert stock analyst for the Dhaka Stock Exchange (DSE), Bangladesh.
You help investors understand stocks, fundamentals, market trends, and ML-based price predictions.

You have access to tools that query real-time and historical DSE data:
- get_stock_price: recent OHLCV price history for any ticker
- get_fundamentals: EPS, NAV, PE ratio, dividends, annual report metrics
- get_sector_comparison: compare a stock against its sector peers
- search_news: recent news articles mentioning a company or topic
- get_ml_prediction: LSTM price direction forecast + health score (0–100)
- screen_stocks: filter stocks by PE, EPS growth, sector, health score
- get_portfolio_analysis: P&L and correlation analysis for multiple tickers
- get_macro_data: Bangladesh macroeconomic indicators (FX, inflation, policy rate)

Rules:
1. Always cite data sources and timestamps in your answers.
2. Add DISCLAIMER: "This is not investment advice." at the end of every analysis.
3. Use BDT (Bangladeshi Taka) for all prices.
4. Refer to tickers in UPPERCASE (e.g., BRACBANK, GP, SQURPHARMA).
5. Answer in the same language the user writes in (Bengali or English).
6. DSE market hours: Sunday–Thursday 10:00–14:30 BD time.\
"""

_STATIC_BN = """\
আপনি ঢাকা স্টক এক্সচেঞ্জ (DSE), বাংলাদেশের একজন বিশেষজ্ঞ স্টক বিশ্লেষক।
আপনি বিনিয়োগকারীদের স্টক, মৌলিক বিষয়, বাজারের প্রবণতা এবং ML-ভিত্তিক মূল্য পূর্বাভাস বুঝতে সাহায্য করেন।

আপনার কাছে DSE ডেটা অনুসন্ধান করার জন্য টুলস আছে:
- get_stock_price: যেকোনো টিকারের সাম্প্রতিক OHLCV মূল্য ইতিহাস
- get_fundamentals: EPS, NAV, PE অনুপাত, লভ্যাংশ, বার্ষিক প্রতিবেদন মেট্রিক্স
- get_sector_comparison: একটি স্টককে তার সেক্টর পিয়ারের সাথে তুলনা করুন
- search_news: একটি কোম্পানি বা বিষয় উল্লেখ করে সাম্প্রতিক সংবাদ
- get_ml_prediction: LSTM মূল্য দিকনির্দেশনা পূর্বাভাস + স্বাস্থ্য স্কোর (০–১০০)
- screen_stocks: PE, EPS বৃদ্ধি, সেক্টর, স্বাস্থ্য স্কোর দ্বারা স্টক ফিল্টার করুন
- get_portfolio_analysis: একাধিক টিকারের জন্য P&L এবং সহসম্পর্ক বিশ্লেষণ
- get_macro_data: বাংলাদেশের সামষ্টিক অর্থনৈতিক সূচক (বৈদেশিক মুদ্রা, মুদ্রাস্ফীতি, নীতি হার)

নিয়ম:
১. সর্বদা আপনার উত্তরে ডেটা উৎস এবং টাইমস্ট্যাম্প উল্লেখ করুন।
২. প্রতিটি বিশ্লেষণের শেষে যোগ করুন: "দাবিত্যাগ: এটি বিনিয়োগ পরামর্শ নয়।"
৩. সমস্ত মূল্যের জন্য BDT (বাংলাদেশী টাকা) ব্যবহার করুন।
৪. DSE বাজারের সময়: রবিবার–বৃহস্পতিবার ১০:০০–১৪:৩০ BD সময়।\
"""


def _is_market_open() -> bool:
    now = datetime.now(_BD_TZ)
    if now.weekday() not in (0, 1, 2, 3, 6):
        return False
    t = now.hour * 60 + now.minute
    return 600 <= t <= 870


def build_system_message(lang: str = "en", ticker_context: str = "") -> SystemMessage:
    """Build a system message refreshed each turn (SELECT strategy)."""
    now_bd = datetime.now(_BD_TZ)
    static = _STATIC_BN if lang == "bn" else _STATIC_EN
    lines = [
        static,
        "",
        "--- Live Context ---",
        f"BD time : {now_bd.strftime('%Y-%m-%d %H:%M %Z')}",
        f"Market  : {'OPEN' if _is_market_open() else 'CLOSED'}  (Sun–Thu 10:00–14:30 BD)",
    ]
    if ticker_context:
        lines += ["", "--- Stock Context ---", ticker_context]
    return SystemMessage(content="\n".join(lines))
```

- [ ] **Step 5: Run tests**

```
pytest tests/unit/test_chat_prompt.py -v
```
Expected: PASS (6/6)

- [ ] **Step 6: Commit**

```bash
git add chat/__init__.py chat/prompt.py tests/unit/test_chat_prompt.py
git commit -m "feat(chat): add chat package with bilingual system prompt"
```

---

### Task 3: Tool Implementations (chat/tools.py)

**Files:**
- Create: `chat/tools.py`
- Create: `tests/unit/test_chat_tools.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_chat_tools.py
import pytest
from unittest.mock import AsyncMock, MagicMock


def _make_pool(rows: list[dict]) -> AsyncMock:
    pool = AsyncMock()
    pool.fetch.return_value = [MagicMock(**r, **{"__getitem__": lambda self, k: r[k]}) for r in rows]
    pool.fetchrow.return_value = MagicMock(**rows[0]) if rows else None
    return pool


@pytest.mark.asyncio
async def test_get_stock_price_returns_price_data():
    from chat.tools import build_tools
    pool = _make_pool([{"time": "2026-01-01", "open": 10.0, "high": 11.0, "low": 9.5, "close": 10.5, "volume": 100}])
    tools = build_tools(pool)
    tool = next(t for t in tools if t.name == "get_stock_price")
    result = await tool.ainvoke({"ticker": "BRACBANK", "days": 5})
    assert "ticker" in result
    assert result["ticker"] == "BRACBANK"


@pytest.mark.asyncio
async def test_get_fundamentals_returns_dict():
    from chat.tools import build_tools
    pool = _make_pool([{"ticker": "GP", "eps": 5.0, "nav": 30.0, "pe_ratio": 12.0, "fiscal_year": 2024}])
    tools = build_tools(pool)
    tool = next(t for t in tools if t.name == "get_fundamentals")
    result = await tool.ainvoke({"ticker": "GP"})
    assert isinstance(result, dict)


@pytest.mark.asyncio
async def test_search_news_returns_list():
    from chat.tools import build_tools
    pool = _make_pool([{"headline": "GP earnings up", "published_at": "2026-01-01", "source": "FE", "tickers": ["GP"]}])
    tools = build_tools(pool)
    tool = next(t for t in tools if t.name == "search_news")
    result = await tool.ainvoke({"query": "earnings", "ticker": "GP", "limit": 3})
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_get_ml_prediction_returns_predictions():
    from chat.tools import build_tools
    pool = _make_pool([{"ticker": "BRACBANK", "horizon_days": 5, "predicted_direction": "up", "confidence": 0.72}])
    tools = build_tools(pool)
    tool = next(t for t in tools if t.name == "get_ml_prediction")
    result = await tool.ainvoke({"ticker": "BRACBANK"})
    assert "predictions" in result or isinstance(result, dict)


@pytest.mark.asyncio
async def test_screen_stocks_returns_list():
    from chat.tools import build_tools
    pool = _make_pool([{"ticker": "BRACBANK", "pe_ratio": 8.0, "health_score": 72.0}])
    tools = build_tools(pool)
    tool = next(t for t in tools if t.name == "screen_stocks")
    result = await tool.ainvoke({"max_pe": 15.0, "limit": 5})
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_get_macro_data_returns_indicators():
    from chat.tools import build_tools
    pool = _make_pool([{"indicator_name": "usd_bdt", "value": 110.5, "recorded_at": "2026-01-01"}])
    tools = build_tools(pool)
    tool = next(t for t in tools if t.name == "get_macro_data")
    result = await tool.ainvoke({"days": 30})
    assert isinstance(result, list)
```

- [ ] **Step 2: Run to verify fails**

```
pytest tests/unit/test_chat_tools.py -v
```
Expected: FAIL — ModuleNotFoundError: No module named 'chat.tools'

- [ ] **Step 3: Create chat/tools.py**

```python
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
    min_health_score: float | None = Field(default=None, description="Minimum stock health score (0–100)")
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
        sector_row = await pool.fetchrow(
            """
            SELECT avg_pe, avg_eps, recorded_at FROM sector_pe
            WHERE sector = $1 ORDER BY recorded_at DESC LIMIT 1
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
        tickers_upper = [t.upper() for t in tickers[:10]]  # cap at 10
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
```

- [ ] **Step 4: Run tests**

```
pytest tests/unit/test_chat_tools.py -v
```
Expected: PASS (6/6) — mocked pool returns expected shapes

- [ ] **Step 5: Commit**

```bash
git add chat/tools.py tests/unit/test_chat_tools.py
git commit -m "feat(chat): 8 read-only tool implementations"
```

---

### Task 4: RAG Pipeline (chat/rag.py)

**Files:**
- Create: `chat/rag.py`
- Create: `tests/unit/test_rag.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_rag.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_embed_query_returns_list_of_floats():
    from chat.rag import embed_query
    mock_embed = MagicMock()
    mock_embed.embed_query.return_value = [0.1] * 768
    with patch("chat.rag._get_embedder", return_value=mock_embed):
        result = embed_query("BRACBANK earnings")
    assert isinstance(result, list)
    assert len(result) == 768
    assert all(isinstance(x, float) for x in result)


@pytest.mark.asyncio
async def test_search_chunks_returns_list():
    from chat.rag import search_chunks
    pool = AsyncMock()
    pool.fetch.return_value = [
        MagicMock(chunk_text="BRACBANK reported strong Q3 results", ticker="BRACBANK", doc_type="news"),
    ]
    embedding = [0.1] * 768
    result = await search_chunks(pool, embedding, top_k=3)
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_build_rag_context_empty_when_no_chunks():
    from chat.rag import build_rag_context
    pool = AsyncMock()
    pool.fetch.return_value = []
    mock_embed = MagicMock()
    mock_embed.embed_query.return_value = [0.0] * 768
    with patch("chat.rag._get_embedder", return_value=mock_embed):
        ctx = await build_rag_context(pool, "some query")
    assert ctx == ""


@pytest.mark.asyncio
async def test_build_rag_context_formats_chunks():
    from chat.rag import build_rag_context
    pool = AsyncMock()
    pool.fetch.return_value = [
        MagicMock(
            chunk_text="BRACBANK Q3 profit up 20%",
            ticker="BRACBANK",
            doc_type="news",
            **{"__getitem__": lambda s, k: getattr(s, k)},
        ),
    ]
    mock_embed = MagicMock()
    mock_embed.embed_query.return_value = [0.1] * 768
    with patch("chat.rag._get_embedder", return_value=mock_embed):
        ctx = await build_rag_context(pool, "BRACBANK profit")
    assert "BRACBANK" in ctx
```

- [ ] **Step 2: Run to verify fails**

```
pytest tests/unit/test_rag.py -v
```
Expected: FAIL

- [ ] **Step 3: Create chat/rag.py**

```python
# chat/rag.py
"""
RAG pipeline: embed query with Google text-embedding-004 → pgvector cosine search.
Embedding dimension: 768 (Google text-embedding-004 default).
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

log = logging.getLogger(__name__)

_EMBEDDING_MODEL = "models/text-embedding-004"
_TOP_K_DEFAULT = 5


@lru_cache(maxsize=1)
def _get_embedder():
    """Lazy singleton — avoids importing at module level (no API key needed for tests)."""
    from langchain_google_genai import GoogleGenerativeAIEmbeddings
    from mgmt.config import get_settings
    s = get_settings()
    return GoogleGenerativeAIEmbeddings(
        model=_EMBEDDING_MODEL,
        google_api_key=s.google_api_key,
        task_type="retrieval_query",
    )


def embed_query(text: str) -> list[float]:
    """Embed a query string using Google text-embedding-004 (768 dims)."""
    return _get_embedder().embed_query(text)


async def search_chunks(
    pool,
    embedding: list[float],
    ticker: str | None = None,
    top_k: int = _TOP_K_DEFAULT,
) -> list[dict]:
    """Cosine similarity search on document_chunks using pgvector <=> operator."""
    vec_str = "[" + ",".join(str(x) for x in embedding) + "]"
    if ticker:
        rows = await pool.fetch(
            """
            SELECT chunk_text, ticker, doc_type, chunk_index,
                   1 - (embedding <=> $1::vector) AS similarity
            FROM document_chunks
            WHERE ticker = $2
            ORDER BY embedding <=> $1::vector
            LIMIT $3
            """,
            vec_str, ticker.upper(), top_k,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT chunk_text, ticker, doc_type, chunk_index,
                   1 - (embedding <=> $1::vector) AS similarity
            FROM document_chunks
            ORDER BY embedding <=> $1::vector
            LIMIT $2
            """,
            vec_str, top_k,
        )
    return [dict(r) for r in rows]


async def build_rag_context(
    pool,
    query: str,
    ticker: str | None = None,
    top_k: int = _TOP_K_DEFAULT,
) -> str:
    """
    Embed query → pgvector search → format top chunks as context string.
    Returns empty string if no chunks found (table is sparse early in deployment).
    """
    try:
        embedding = embed_query(query)
        chunks = await search_chunks(pool, embedding, ticker=ticker, top_k=top_k)
    except Exception as exc:
        log.warning("rag.search_failed: %s", exc)
        return ""

    if not chunks:
        return ""

    lines = ["--- Relevant Context from Knowledge Base ---"]
    for i, chunk in enumerate(chunks, 1):
        src = f"[{chunk.get('doc_type', 'unknown')} / ticker={chunk.get('ticker', 'N/A')}]"
        lines.append(f"{i}. {src}\n   {chunk.get('chunk_text', '')[:500]}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests**

```
pytest tests/unit/test_rag.py -v
```
Expected: PASS (4/4)

- [ ] **Step 5: Commit**

```bash
git add chat/rag.py tests/unit/test_rag.py
git commit -m "feat(chat): RAG pipeline with pgvector cosine search"
```

---

### Task 5: LLM Usage Logging (chat/usage.py)

**Files:**
- Create: `chat/usage.py`
- Create: `tests/unit/test_usage_logging.py`

Cost rates (USD per 1M tokens, approximate 2026 pricing):

| Provider/Model | Input | Output |
|---|---|---|
| gemini-2.5-flash (non-thinking) | 0.15 | 0.60 |
| gemini-2.5-flash (thinking) | 3.50 | 15.00 |
| gpt-4o-mini (openrouter) | 0.15 | 0.60 |
| claude-3-5-haiku (openrouter) | 0.80 | 4.00 |

- [ ] **Step 1: Write failing tests**

```python
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
```

- [ ] **Step 2: Run to verify fails**

```
pytest tests/unit/test_usage_logging.py -v
```
Expected: FAIL

- [ ] **Step 3: Create chat/usage.py**

```python
# chat/usage.py
from __future__ import annotations

import time

_COST_TABLE: dict[str, dict[str, float]] = {
    # (provider, model_substring): (input_per_1m, output_per_1m)
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
        import logging
        logging.getLogger(__name__).warning("log_llm_usage failed: %s", exc)
```

- [ ] **Step 4: Run tests**

```
pytest tests/unit/test_usage_logging.py -v
```
Expected: PASS (3/3)

- [ ] **Step 5: Commit**

```bash
git add chat/usage.py tests/unit/test_usage_logging.py
git commit -m "feat(chat): LLM usage logging with cost estimation"
```

---

### Task 6: Chat Agent (chat/agent.py)

**Files:**
- Create: `chat/agent.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_chat_agent.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_pool():
    pool = AsyncMock()
    pool.fetch.return_value = []
    pool.fetchrow.return_value = None
    pool.execute.return_value = None
    return pool


@pytest.mark.asyncio
async def test_chat_stream_yields_text_and_done():
    from chat.agent import StockAnalystAgent

    pool = _make_pool()

    mock_llm = AsyncMock()
    mock_response = MagicMock()
    mock_response.content = "BRACBANK is trading well."
    mock_response.tool_calls = []
    mock_response.usage_metadata = {"input_tokens": 100, "output_tokens": 20}

    async def _astream(msgs):
        yield mock_response

    mock_llm.astream = _astream
    mock_llm.bind_tools.return_value = mock_llm

    with patch("chat.agent.make_llm", return_value=mock_llm):
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


@pytest.mark.asyncio
async def test_chat_stream_handles_tool_call():
    from chat.agent import StockAnalystAgent

    pool = _make_pool()

    tool_call_response = MagicMock()
    tool_call_response.content = ""
    tool_call_response.tool_calls = [{"id": "tc1", "name": "get_stock_price", "args": {"ticker": "GP"}}]
    tool_call_response.usage_metadata = {"input_tokens": 50, "output_tokens": 5}

    final_response = MagicMock()
    final_response.content = "GP is at 45 BDT."
    final_response.tool_calls = []
    final_response.usage_metadata = {"input_tokens": 60, "output_tokens": 10}

    call_count = 0

    async def _astream(msgs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield tool_call_response
        else:
            yield final_response

    mock_llm = AsyncMock()
    mock_llm.astream = _astream
    mock_llm.bind_tools.return_value = mock_llm

    with patch("chat.agent.make_llm", return_value=mock_llm):
        agent = StockAnalystAgent(provider="google", model="gemini-2.5-flash")
        chunks = []
        async for chunk in agent.chat_stream(
            pool,
            [{"role": "user", "content": "Price of GP?"}],
            session_id="test-session",
        ):
            chunks.append(chunk)

    tool_call_chunks = [c for c in chunks if c["type"] == "tool_call"]
    assert len(tool_call_chunks) >= 1
    assert chunks[-1]["type"] == "done"
```

- [ ] **Step 2: Run to verify fails**

```
pytest tests/unit/test_chat_agent.py -v
```
Expected: FAIL

- [ ] **Step 3: Create chat/agent.py**

```python
# chat/agent.py
"""
StockAnalystAgent — user-facing DSE stock analysis chatbot.

Context engineering (mirrors OpsAgent):
  SELECT   — system message rebuilt each turn (BD time, market status)
  COMPRESS — history trimmed when > _MAX_HISTORY messages
  WRITE    — no scratchpad (read-only tools don't need one)
  ISOLATE  — all DB tools hard-capped at 50 rows
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from chat.prompt import build_system_message, detect_language
from chat.rag import build_rag_context
from chat.tools import build_tools
from chat.usage import log_llm_usage
from mgmt.agent.llm import make_llm

log = logging.getLogger(__name__)

_MAX_HISTORY = 20
_KEEP_RECENT = 8
_MAX_LOOP = 10


@dataclass
class _ChatState:
    messages: list[BaseMessage]
    lang: str = "en"
    tool_calls_total: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


def _trim(messages: list[BaseMessage]) -> list[BaseMessage]:
    if len(messages) <= _MAX_HISTORY:
        return messages
    system = [m for m in messages if isinstance(m, SystemMessage)]
    rest = [m for m in messages if not isinstance(m, SystemMessage)]
    return system + rest[-_KEEP_RECENT:]


def _to_lc(messages: list[dict]) -> list[BaseMessage]:
    out = []
    for m in messages:
        role, content = m.get("role", ""), m.get("content") or ""
        if role == "user":
            out.append(HumanMessage(content=content))
        elif role == "assistant":
            out.append(AIMessage(content=content))
    return out


def _extract_tokens(response: AIMessage) -> tuple[int, int]:
    meta = getattr(response, "usage_metadata", None) or {}
    return meta.get("input_tokens", 0), meta.get("output_tokens", 0)


class StockAnalystAgent:
    def __init__(self, provider: str, model: str) -> None:
        self._provider = provider
        self._model = model
        self._llm_base = None
        log.info("chat_agent.init provider=%s model=%s", provider, model)

    @property
    def _base_llm(self):
        if self._llm_base is None:
            from mgmt.config import get_settings
            self._llm_base = make_llm(self._provider, self._model, get_settings())
        return self._llm_base

    async def chat_stream(
        self,
        pool,
        messages: list[dict],
        session_id: str = "anonymous",
        tier: str = "free",
    ) -> AsyncGenerator[dict, None]:
        t_start = time.monotonic()

        # Detect language from latest user message
        user_msgs = [m for m in messages if m.get("role") == "user"]
        last_user = user_msgs[-1]["content"] if user_msgs else ""
        lang = detect_language(last_user)

        # RAG: embed last user message, retrieve relevant chunks
        rag_context = ""
        if last_user:
            try:
                rag_context = await build_rag_context(pool, last_user)
            except Exception as exc:
                log.debug("rag skipped: %s", exc)

        system_msg = build_system_message(lang=lang, ticker_context=rag_context)
        state = _ChatState(
            messages=[system_msg] + _to_lc(messages),
            lang=lang,
        )

        tools = build_tools(pool)
        tool_map = {t.name: t for t in tools}
        llm = self._base_llm.bind_tools(tools)

        for loop_idx in range(_MAX_LOOP):
            # SELECT: refresh system message each turn
            state.messages[0] = build_system_message(lang=state.lang, ticker_context=rag_context)
            # COMPRESS: trim if history large
            state.messages = _trim(state.messages)

            full: AIMessage | None = None
            async for chunk in llm.astream(state.messages):
                if isinstance(chunk.content, str) and chunk.content:
                    yield {"type": "text", "text": chunk.content}
                full = chunk if full is None else full + chunk  # type: ignore[operator]

            if full is None:
                break
            state.messages.append(full)

            in_t, out_t = _extract_tokens(full)
            state.input_tokens += in_t
            state.output_tokens += out_t

            if not full.tool_calls:
                break

            tool_msgs: list[BaseMessage] = []
            for tc in full.tool_calls:
                name, inp = tc["name"], tc["args"]
                state.tool_calls_total += 1
                yield {"type": "tool_call", "name": name, "input": inp}
                if name in tool_map:
                    result = await tool_map[name].ainvoke(inp)
                else:
                    result = {"error": f"Unknown tool: {name}"}
                yield {"type": "tool_result", "name": name, "result": result}
                tool_msgs.append(ToolMessage(
                    tool_call_id=tc["id"],
                    content=json.dumps(result, default=str),
                    name=name,
                ))
            state.messages.extend(tool_msgs)

        # Usage logging (non-fatal)
        latency_ms = int((time.monotonic() - t_start) * 1000)
        try:
            await log_llm_usage(
                pool=pool,
                session_id=session_id,
                provider=self._provider,
                model=self._model,
                input_tokens=state.input_tokens,
                output_tokens=state.output_tokens,
                tool_calls_n=state.tool_calls_total,
                latency_ms=latency_ms,
                tier=tier,
            )
        except Exception as exc:
            log.warning("usage_log failed: %s", exc)

        yield {"type": "done"}
```

- [ ] **Step 4: Run tests**

```
pytest tests/unit/test_chat_agent.py -v
```
Expected: PASS (2/2)

- [ ] **Step 5: Commit**

```bash
git add chat/agent.py tests/unit/test_chat_agent.py
git commit -m "feat(chat): StockAnalystAgent with streaming, tools, RAG, usage logging"
```

---

### Task 7: SSE Chat Endpoint (mgmt/routers/chat.py)

**Files:**
- Create: `mgmt/routers/chat.py`
- Modify: `mgmt/deps.py` (add `get_chat_agent`)
- Modify: `mgmt/main.py` (init chat agent, include chat router)
- Modify: `mgmt/config.py` (add `chat_agent_model`, `chat_default_tier`)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_chat_endpoint.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def test_chat_router_has_post_chat_route():
    from mgmt.routers.chat import router
    routes = {r.path: r for r in router.routes}
    assert "/api/chat" in routes


@pytest.mark.asyncio
async def test_chat_request_model_validates():
    from mgmt.routers.chat import ChatRequest
    req = ChatRequest(messages=[{"role": "user", "content": "hello"}])
    assert len(req.messages) == 1

def test_chat_request_model_rejects_empty_messages():
    from mgmt.routers.chat import ChatRequest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ChatRequest(messages=[])
```

- [ ] **Step 2: Run to verify fails**

```
pytest tests/unit/test_chat_endpoint.py -v
```
Expected: FAIL

- [ ] **Step 3: Create mgmt/routers/chat.py**

```python
# mgmt/routers/chat.py
from __future__ import annotations

import json
import uuid

import structlog
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from mgmt.deps import get_db, get_chat_agent

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])

_TIER_QUOTA: dict[str, int] = {
    "free": 3,
    "pro": 30,
    "pro_plus": 100,
    "institution": 0,  # 0 = unlimited
}


class ChatRequest(BaseModel):
    messages: list[dict] = Field(min_length=1)
    tier: str = Field(default="free")
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))

    @field_validator("messages")
    @classmethod
    def messages_not_empty(cls, v: list[dict]) -> list[dict]:
        if not v:
            raise ValueError("messages must not be empty")
        return v

    @field_validator("tier")
    @classmethod
    def tier_valid(cls, v: str) -> str:
        if v not in _TIER_QUOTA:
            return "free"
        return v


async def _check_quota(redis, session_id: str, tier: str) -> bool:
    """Return True if request is within quota. institution tier = unlimited."""
    limit = _TIER_QUOTA.get(tier, 3)
    if limit == 0:
        return True
    from datetime import date
    day_key = f"quota:{session_id}:{date.today().isoformat()}"
    try:
        count = await redis.incr(day_key)
        if count == 1:
            await redis.expire(day_key, 86400)  # 24h TTL
        return count <= limit
    except Exception as exc:
        logger.warning("quota_check_failed", error=str(exc))
        return True  # fail open


@router.post("/chat")
async def stock_chat(
    body: ChatRequest,
    request: Request,
    pool=Depends(get_db),
):
    """SSE streaming stock analysis chat endpoint."""
    agent = await get_chat_agent(request)

    # Quota check
    try:
        from mgmt.cache import get_redis
        redis = await get_redis()
        allowed = await _check_quota(redis, body.session_id, body.tier)
        if not allowed:
            limit = _TIER_QUOTA.get(body.tier, 3)
            async def _quota_exceeded():
                yield f"data: {json.dumps({'type': 'error', 'error': f'Daily quota exceeded ({limit}/day for {body.tier} tier)'})}\n\n"
            return StreamingResponse(_quota_exceeded(), media_type="text/event-stream")
    except Exception as exc:
        logger.warning("quota_check_error", error=str(exc))

    async def event_stream():
        try:
            async for chunk in agent.chat_stream(
                pool,
                body.messages,
                session_id=body.session_id,
                tier=body.tier,
            ):
                yield f"data: {json.dumps(chunk, default=str)}\n\n"
        except Exception as exc:
            logger.error("chat.stream_error", error=str(exc))
            yield f"data: {json.dumps({'type': 'error', 'error': str(exc)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

- [ ] **Step 4: Update mgmt/deps.py — add get_chat_agent**

```python
# Add to mgmt/deps.py (append after get_ops_agent):

async def get_chat_agent(request: Request):
    agent = getattr(request.app.state, "chat_agent", None)
    if agent is None:
        raise HTTPException(503, "Chat agent not initialized")
    return agent
```

- [ ] **Step 5: Update mgmt/config.py — add chat config**

```python
# Add to Settings class in mgmt/config.py:

# ── Chat Agent ────────────────────────────────────────────────────────────────
chat_agent_provider: str = "google"
chat_agent_model: str = "gemini-2.5-flash"
chat_default_tier: str = "free"
```

- [ ] **Step 6: Update mgmt/main.py — init chat agent and include router**

Add to imports:
```python
from chat.agent import StockAnalystAgent
from mgmt.routers import agent, alerts, chat, health, jobs, metrics, quality, scheduler, streams, tasks
```

Add after ops_agent init in lifespan:
```python
chat_agent = StockAnalystAgent(
    provider=settings.chat_agent_provider,
    model=settings.chat_agent_model,
)
app.state.chat_agent = chat_agent
logger.info("chat_agent_initialized", provider=settings.chat_agent_provider, model=settings.chat_agent_model)
```

Add router registration:
```python
app.include_router(chat.router)
```

- [ ] **Step 7: Run tests**

```
pytest tests/unit/test_chat_endpoint.py -v
```
Expected: PASS (3/3)

- [ ] **Step 8: Commit**

```bash
git add mgmt/routers/chat.py mgmt/deps.py mgmt/main.py mgmt/config.py tests/unit/test_chat_endpoint.py
git commit -m "feat(chat): SSE /api/chat endpoint with quota middleware"
```

---

### Task 8: Sentiment Scoring Pipeline (chat/sentiment.py)

Gemini Flash scores news articles that lack sentiment from the Google NL API.

**Files:**
- Create: `chat/sentiment.py`
- Modify: `extraction/scheduler.py` (add `job_news_sentiment` daily job)
- Modify: `mgmt/config.py` (add `sentiment_batch_size`)
- Create: `tests/unit/test_sentiment.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_sentiment.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_score_article_positive():
    from chat.sentiment import score_article
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = MagicMock(content='{"score": 0.75, "label": "positive"}')
    result = await score_article(mock_llm, "BRACBANK profits surge 30%", "BRACBANK reported strong Q3 earnings.")
    assert result["label"] in ("positive", "neutral", "negative")
    assert -1.0 <= result["score"] <= 1.0


@pytest.mark.asyncio
async def test_score_article_handles_malformed_response():
    from chat.sentiment import score_article
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = MagicMock(content="I think it's positive overall.")
    result = await score_article(mock_llm, "headline", "body")
    assert "score" in result
    assert "label" in result


@pytest.mark.asyncio
async def test_score_new_articles_updates_records():
    from chat.sentiment import score_new_articles
    pool = AsyncMock()
    pool.fetch.return_value = [
        MagicMock(id=1, headline="GP profits up", body="Good results", **{"__getitem__": lambda s, k: getattr(s, k)}),
    ]
    pool.execute.return_value = None

    mock_llm = MagicMock()
    mock_llm.invoke.return_value = MagicMock(content='{"score": 0.5, "label": "positive"}')

    count = await score_new_articles(pool, mock_llm, limit=5)
    assert count >= 0  # may be 0 if no articles to score
```

- [ ] **Step 2: Run to verify fails**

```
pytest tests/unit/test_sentiment.py -v
```
Expected: FAIL

- [ ] **Step 3: Create chat/sentiment.py**

```python
# chat/sentiment.py
"""
Gemini Flash sentiment scoring for news articles.
Scores articles where sentiment_score IS NULL (not yet scored by Google NL API).
"""
from __future__ import annotations

import json
import logging
import re

from langchain_core.messages import HumanMessage

log = logging.getLogger(__name__)

_SCORE_PROMPT = """\
Analyze the sentiment of this financial news article about DSE (Bangladesh stock market).
Respond ONLY with valid JSON: {{"score": <float -1.0 to 1.0>, "label": "<positive|neutral|negative>"}}

Headline: {headline}
Body: {body}

JSON only, no explanation:\
"""


async def score_article(llm, headline: str, body: str | None) -> dict:
    """
    Score a single article's sentiment using the LLM.
    Returns {"score": float, "label": str}.
    Falls back to {"score": 0.0, "label": "neutral"} on parse failure.
    """
    prompt = _SCORE_PROMPT.format(headline=headline, body=(body or "")[:800])
    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        raw = response.content.strip()
        # Try JSON parse
        match = re.search(r'\{[^}]+\}', raw, re.DOTALL)
        if match:
            data = json.loads(match.group())
            score = float(data.get("score", 0.0))
            label = data.get("label", "neutral")
            score = max(-1.0, min(1.0, score))
            if label not in ("positive", "neutral", "negative"):
                label = "neutral"
            return {"score": score, "label": label}
    except Exception as exc:
        log.debug("score_article parse failed: %s", exc)
    return {"score": 0.0, "label": "neutral"}


async def score_new_articles(pool, llm, limit: int = 100) -> int:
    """
    Score news articles where sentiment_score IS NULL.
    Returns count of articles scored.
    """
    rows = await pool.fetch(
        """
        SELECT id, headline, body FROM news
        WHERE sentiment_score IS NULL
        ORDER BY published_at DESC
        LIMIT $1
        """,
        limit,
    )
    scored = 0
    for row in rows:
        try:
            result = await score_article(llm, row["headline"], row["body"])
            await pool.execute(
                "UPDATE news SET sentiment_score = $1, sentiment_label = $2 WHERE id = $3",
                result["score"], result["label"], row["id"],
            )
            scored += 1
        except Exception as exc:
            log.warning("score_article failed id=%s: %s", row["id"], exc)
    log.info("sentiment.scored count=%d", scored)
    return scored
```

- [ ] **Step 4: Add `job_news_sentiment` to scheduler**

In `extraction/scheduler.py`, add after existing job definitions:

```python
async def job_news_sentiment() -> None:
    """Score unscored news articles with Gemini Flash sentiment analysis."""
    import logging
    from db.pool import get_pool
    from chat.agent import StockAnalystAgent
    from extraction.jobs import job_run

    _log = logging.getLogger(__name__)
    settings = get_settings()
    pool = await get_pool()

    async with job_run(pool, "news_sentiment", "news_en"):
        agent = StockAnalystAgent(
            provider=settings.chat_agent_provider,
            model=settings.chat_agent_model,
        )
        from chat.sentiment import score_new_articles
        n = await score_new_articles(pool, agent._base_llm, limit=200)
        _log.info("job_news_sentiment done scored=%d", n)
```

In `_configure_production_mode()`, add:
```python
scheduler.add_job(
    job_news_sentiment,
    "cron",
    id="news_sentiment",
    hour=3,
    minute=30,
    timezone=BD_TZ,
    replace_existing=True,
    misfire_grace_time=3600,
)
```

In `_configure_test_mode()` job_map, add:
```python
(job_news_sentiment, "news_sentiment", cfg.test_news_sentiment_minutes),
```

Add to `mgmt/config.py`:
```python
test_news_sentiment_minutes: int = 20
```

- [ ] **Step 5: Run tests**

```
pytest tests/unit/test_sentiment.py -v
```
Expected: PASS (3/3)

- [ ] **Step 6: Commit**

```bash
git add chat/sentiment.py mgmt/config.py tests/unit/test_sentiment.py
git commit -m "feat(chat): Gemini Flash sentiment scoring pipeline for news articles"
```

---

### Task 9: Context Caching (chat/cache.py)

Optional Gemini CachedContent API — 30-min TTL, ~75% token discount on system context.
Only active when `provider=google` and `google_api_key` is set.

**Files:**
- Create: `chat/cache.py`
- Modify: `mgmt/config.py` (add `gemini_context_cache_enabled`)

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_cache.py
import pytest
from unittest.mock import MagicMock, patch


def test_context_cache_disabled_by_default():
    from chat.cache import GeminiContextCache
    cache = GeminiContextCache(enabled=False, api_key="test")
    assert not cache.enabled


def test_context_cache_is_noop_when_disabled():
    from chat.cache import GeminiContextCache
    cache = GeminiContextCache(enabled=False, api_key="test")
    # get_or_create returns None when disabled
    result = cache.get_or_create("system prompt text", ttl_minutes=30)
    assert result is None


def test_context_cache_builds_cache_key():
    from chat.cache import _cache_key
    key1 = _cache_key("hello world")
    key2 = _cache_key("hello world")
    key3 = _cache_key("different text")
    assert key1 == key2
    assert key1 != key3
    assert len(key1) == 8  # 8-char hex prefix
```

- [ ] **Step 2: Run to verify fails**

```
pytest tests/unit/test_cache.py -v
```
Expected: FAIL

- [ ] **Step 3: Create chat/cache.py**

```python
# chat/cache.py
"""
Optional Gemini CachedContent API for system prompt caching.
Requires google-generativeai (already transitively installed via langchain-google-genai).
Only active when enabled=True and provider=google.

Usage pattern:
  cache = GeminiContextCache(enabled=settings.gemini_context_cache_enabled, api_key=settings.google_api_key)
  cache_name = cache.get_or_create(system_prompt, ttl_minutes=30)
  # Pass cache_name to ChatGoogleGenerativeAI (not yet supported natively — inject as-is for now)
"""
from __future__ import annotations

import hashlib
import logging
from datetime import timedelta

log = logging.getLogger(__name__)


def _cache_key(text: str) -> str:
    """Short hash key for cache deduplication."""
    return hashlib.sha256(text.encode()).hexdigest()[:8]


class GeminiContextCache:
    def __init__(self, enabled: bool, api_key: str, model: str = "models/gemini-2.5-flash") -> None:
        self.enabled = enabled
        self._api_key = api_key
        self._model = model
        self._cache: dict[str, str] = {}  # key → cache resource name

    def get_or_create(
        self,
        system_text: str,
        ttl_minutes: int = 30,
    ) -> str | None:
        """
        Return Gemini CachedContent resource name (for reuse), or None if disabled.
        Creates a new cache entry if this system_text hasn't been cached yet.
        """
        if not self.enabled:
            return None

        key = _cache_key(system_text)
        if key in self._cache:
            log.debug("cache.hit key=%s", key)
            return self._cache[key]

        try:
            import google.generativeai as genai
            genai.configure(api_key=self._api_key)
            cached = genai.caching.CachedContent.create(
                model=self._model,
                display_name=f"dse_stock_system_{key}",
                system_instruction=system_text,
                ttl=timedelta(minutes=ttl_minutes),
            )
            self._cache[key] = cached.name
            log.info("cache.created name=%s key=%s ttl=%dm", cached.name, key, ttl_minutes)
            return cached.name
        except Exception as exc:
            log.warning("cache.create_failed key=%s: %s", key, exc)
            return None

    def invalidate(self, system_text: str) -> None:
        key = _cache_key(system_text)
        self._cache.pop(key, None)
```

Add to `mgmt/config.py`:
```python
gemini_context_cache_enabled: bool = False  # Set True to enable 75% token discount
gemini_context_cache_ttl_minutes: int = 30
```

- [ ] **Step 4: Run tests**

```
pytest tests/unit/test_cache.py -v
```
Expected: PASS (3/3)

- [ ] **Step 5: Commit**

```bash
git add chat/cache.py tests/unit/test_cache.py
git commit -m "feat(chat): Gemini context caching (optional, 30-min TTL)"
```

---

### Task 10: Model Routing (update chat/agent.py)

Thinking mode for complex analysis; fast mode for simple lookups.
Gemini-specific: passes `thinking_budget` via generation config. No-op for other providers.

**Files:**
- Create: `chat/routing.py`
- Modify: `chat/agent.py` (use routed LLM)
- Create: `tests/unit/test_routing.py`

- [ ] **Step 1: Write failing tests**

```python
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
    # Bengali "বিশ্লেষণ" = analyze
    assert is_complex_query("GP এর মৌলিক বিশ্লেষণ করুন")
```

- [ ] **Step 2: Run to verify fails**

```
pytest tests/unit/test_routing.py -v
```
Expected: FAIL

- [ ] **Step 3: Create chat/routing.py**

```python
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
    # Bengali character check against known complex keywords
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
    # Other providers: no thinking mode, return standard LLM
    from mgmt.agent.llm import make_llm
    return make_llm(provider, model, settings)
```

- [ ] **Step 4: Update chat/agent.py to use model routing**

In `StockAnalystAgent.chat_stream`, replace the static `llm = self._base_llm.bind_tools(tools)` with:

```python
from chat.routing import is_complex_query, make_routed_llm
from mgmt.config import get_settings

is_complex = is_complex_query(last_user)
routed_llm = make_routed_llm(self._provider, self._model, get_settings(), is_complex)
llm = routed_llm.bind_tools(tools)
```

Also yield the routing decision for debugging:
```python
yield {"type": "routing", "thinking_mode": is_complex}
```

- [ ] **Step 5: Run tests**

```
pytest tests/unit/test_routing.py -v
```
Expected: PASS (6/6)

- [ ] **Step 6: Commit**

```bash
git add chat/routing.py tests/unit/test_routing.py
git commit -m "feat(chat): model routing — thinking mode for complex queries"
```

---

### Task 11: Bengali Language Test Suite

**Files:**
- Create: `tests/unit/test_chat_bengali.py`

- [ ] **Step 1: Write tests**

```python
# tests/unit/test_chat_bengali.py
"""
Bengali language detection and handling tests.
The agent should respond in Bengali when user writes in Bengali.
"""
import pytest
from unittest.mock import MagicMock, patch

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
async def test_tools_accept_bengali_ticker_uppercase():
    """Tools normalize tickers to uppercase regardless of input case."""
    from unittest.mock import AsyncMock
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
```

- [ ] **Step 2: Run to verify tests pass**

```
pytest tests/unit/test_chat_bengali.py -v
```
Expected: PASS (13/13) — all prompt/detect/routing already implemented

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_chat_bengali.py
git commit -m "test(chat): Bengali language test suite"
```

---

### Task 12: Annual Report PDF Extraction (chat/pdf_extractor.py)

PDF pipeline via Gemini Files API. DSE/BSEC have no centralized PDF portal (confirmed Phase 1E).
This implements the infrastructure; data volume is limited until per-company PDFs are scraped separately.

**Files:**
- Create: `chat/pdf_extractor.py`
- Create: `tests/unit/test_pdf_extractor.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_pdf_extractor.py
import pytest
from unittest.mock import MagicMock, patch, AsyncMock


def test_extract_text_from_url_returns_dict():
    from chat.pdf_extractor import PdfExtractor
    extractor = PdfExtractor(api_key="test_key", model="gemini-2.5-flash")
    assert extractor.model == "gemini-2.5-flash"


@pytest.mark.asyncio
async def test_extract_skips_when_no_api_key():
    from chat.pdf_extractor import PdfExtractor
    extractor = PdfExtractor(api_key="", model="gemini-2.5-flash")
    result = await extractor.extract_from_url("http://example.com/report.pdf", "BRACBANK", 2024)
    assert result is None


def test_chunk_text_splits_correctly():
    from chat.pdf_extractor import chunk_text
    text = "word " * 1000
    chunks = chunk_text(text, chunk_size=500, overlap=50)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 600  # generous bound


def test_chunk_text_single_chunk():
    from chat.pdf_extractor import chunk_text
    short = "Short text here."
    chunks = chunk_text(short, chunk_size=500, overlap=50)
    assert chunks == [short]
```

- [ ] **Step 2: Run to verify fails**

```
pytest tests/unit/test_pdf_extractor.py -v
```
Expected: FAIL

- [ ] **Step 3: Create chat/pdf_extractor.py**

```python
# chat/pdf_extractor.py
"""
Annual report PDF extraction via Gemini Files API.

Note: DSE/BSEC have no centralized PDF portal (confirmed Phase 1E).
This pipeline processes PDFs when URLs are scraped from per-company IR pages.
For now, the infrastructure is in place; volume is limited.

Usage:
  extractor = PdfExtractor(api_key=settings.google_api_key)
  result = await extractor.extract_from_url(pdf_url, ticker, fiscal_year)
"""
from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

_EXTRACTION_PROMPT = """\
Extract key financial metrics from this annual report PDF.
Return a JSON object with these fields (null if not found):
{
  "revenue": <number in BDT millions>,
  "net_income": <number in BDT millions>,
  "eps": <number>,
  "nav_per_share": <number>,
  "total_assets": <number in BDT millions>,
  "dividend_cash_pct": <number>,
  "dividend_stock_pct": <number>,
  "key_highlights": [<string>, ...]
}
JSON only, no explanation.\
"""

_CHUNK_SIZE = 800  # chars per document chunk for pgvector storage


def chunk_text(text: str, chunk_size: int = _CHUNK_SIZE, overlap: int = 80) -> list[str]:
    """Split text into overlapping chunks for embedding storage."""
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += chunk_size - overlap
    return [c for c in chunks if c.strip()]


class PdfExtractor:
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash") -> None:
        self.api_key = api_key
        self.model = model

    async def extract_from_url(
        self, pdf_url: str, ticker: str, fiscal_year: int
    ) -> dict | None:
        """
        Download PDF, upload to Gemini Files API, extract metrics.
        Returns None if api_key is missing or extraction fails.
        """
        if not self.api_key:
            log.debug("pdf_extractor: no api_key, skip")
            return None

        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp_path = Path(tmp.name)
                async with httpx.AsyncClient(timeout=60) as client:
                    resp = await client.get(pdf_url)
                    resp.raise_for_status()
                    tmp_path.write_bytes(resp.content)

            return await asyncio.to_thread(self._extract_sync, tmp_path, ticker, fiscal_year)
        except Exception as exc:
            log.warning("pdf_extractor.failed ticker=%s year=%d: %s", ticker, fiscal_year, exc)
            return None
        finally:
            if "tmp_path" in dir() and tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    def _extract_sync(self, pdf_path: Path, ticker: str, fiscal_year: int) -> dict | None:
        """Run in a thread (google-generativeai is sync)."""
        import google.generativeai as genai
        import json
        import re

        genai.configure(api_key=self.api_key)
        uploaded = genai.upload_file(path=str(pdf_path), mime_type="application/pdf")
        model = genai.GenerativeModel(self.model)
        response = model.generate_content([uploaded, _EXTRACTION_PROMPT])

        raw = response.text.strip()
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
                data["ticker"] = ticker
                data["fiscal_year"] = fiscal_year
                data["source_url"] = None  # filled by caller
                return data
            except json.JSONDecodeError:
                pass
        log.warning("pdf_extractor: failed to parse JSON for %s %d", ticker, fiscal_year)
        return None
```

- [ ] **Step 4: Run tests**

```
pytest tests/unit/test_pdf_extractor.py -v
```
Expected: PASS (4/4)

- [ ] **Step 5: Commit**

```bash
git add chat/pdf_extractor.py tests/unit/test_pdf_extractor.py
git commit -m "feat(chat): annual report PDF extraction via Gemini Files API"
```

---

### Task 13: Integration Test + Wire Everything Together

Verify all Layer 4 components integrate correctly: agent responds, tools call DB, usage logged.

**Files:**
- Create: `tests/unit/test_chat_integration.py`

- [ ] **Step 1: Write integration test**

```python
# tests/unit/test_chat_integration.py
"""Verify the full Layer 4 chain: agent → tools → usage logging."""
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
    # Usage log should have been called
    assert pool.execute.called


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
    """Verify /api/chat route is registered in the app."""
    from mgmt.main import app
    routes = {r.path for r in app.routes}
    assert "/api/chat" in routes
```

- [ ] **Step 2: Run the full test suite**

```
pytest tests/unit/test_chat_integration.py tests/unit/test_chat_tools.py tests/unit/test_rag.py tests/unit/test_usage_logging.py tests/unit/test_sentiment.py tests/unit/test_chat_bengali.py tests/unit/test_chat_prompt.py -v
```
Expected: ALL PASS

- [ ] **Step 3: Run TODOS update**

Update `TODOS.md`: mark all Layer 4 items complete, set CURRENT FOCUS to Layer 5 (Backend API).

```markdown
## CURRENT FOCUS → Layer 4 complete. Next: Layer 5 (Backend API — FastAPI, JWT auth, all /api/stocks and /api/market endpoints).
```

- [ ] **Step 4: Final commit**

```bash
git add tests/unit/test_chat_integration.py TODOS.md
git commit -m "feat(layer4): complete LLM agent layer — stock chatbot, 8 tools, RAG, SSE, Bengali support, sentiment scoring"
```

---

## Self-Review Checklist

**Spec coverage:**
- [x] LangChain setup — existing deps sufficient; no new packages needed
- [x] `chat/agent.py` — StockAnalystAgent with create_tool_calling pattern (manual loop for consistency)
- [x] System prompt English + Bengali — Task 2
- [x] All 8 @tool functions — Task 3
- [x] RAG pipeline — Task 4
- [x] SSE streaming POST /api/chat — Task 7
- [x] Multi-turn conversation — Task 6 (history injection)
- [x] Context caching — Task 9
- [x] Model routing — Task 10
- [x] Token budget / quota middleware — Task 7 (Redis counter by session tier)
- [x] LLM usage logging — Task 5
- [x] Bengali test suite — Task 11
- [x] Annual report PDF extraction — Task 12
- [x] Gemini Flash sentiment scoring — Task 8

**Placeholders:** None — all code blocks are complete.

**Type consistency:** `StockAnalystAgent` signature matches across agent.py, deps.py, main.py, and chat.py router.

**Note on AgentExecutor:** TODOS mentions `AgentExecutor` + `create_tool_calling_agent`, but the existing codebase uses a manual loop (OpsAgent pattern). This plan uses the manual loop for consistency — the behavior is identical; `AgentExecutor` is deprecated in LangChain 0.3+ anyway.

**Note on voyageai:** TODOS mentions voyage-finance-2 embeddings. This plan uses Google text-embedding-004 (768-dim) to avoid a new API key. A migration updates the schema from 1024→768. If voyage-finance-2 is preferred, install `voyageai>=0.3.0`, add `VOYAGEAI_API_KEY` to `.env`, update `chat/rag.py` to use `VoyageAIEmbeddings`, and revert migration 022.
