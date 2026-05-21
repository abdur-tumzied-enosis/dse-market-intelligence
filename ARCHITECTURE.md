# DSE Stock Intelligence Platform — Architecture

> **Target:** Institutional & serious retail investors on Dhaka Stock Exchange (DSE), Bangladesh  
> **Core Value:** Data-driven stock analysis + LLM-powered insights + ML predictions (1–10 year horizon)  
> **Data Source:** AmarStock API, DSE official CSVs, company annual report PDFs, BD financial news

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Full Architecture Diagram](#2-full-architecture-diagram)
3. [Layer 1 — Data Ingestion](#3-layer-1--data-ingestion)
4. [Layer 2 — Storage](#4-layer-2--storage)
5. [Layer 3 — ML Prediction Engine](#5-layer-3--ml-prediction-engine)
6. [Layer 4 — LLM Agent Layer](#6-layer-4--llm-agent-layer)
7. [Layer 5 — Backend API](#7-layer-5--backend-api)
8. [Layer 6 — Frontend](#8-layer-6--frontend)
9. [Database Schema](#9-database-schema)
10. [LLM Tool Definitions](#10-llm-tool-definitions)
11. [ML Model Design](#11-ml-model-design)
12. [Data Flow Walkthroughs](#12-data-flow-walkthroughs)
13. [Tech Stack Summary](#13-tech-stack-summary)
14. [Infrastructure & Deployment](#14-infrastructure--deployment)
15. [Build Phases & Timeline](#15-build-phases--timeline)
16. [Risk & Limitations](#16-risk--limitations)
17. [Data Pipeline Maintenance](#17-data-pipeline-maintenance)
18. [Monetization & Cost Management](#18-monetization--cost-management)

---

## 1. System Overview

```
What the platform does:

  ┌─────────────────────────────────────────────────────────────┐
  │  INPUT                                                       │
  │  • 10+ years DSE historical price data (850+ stocks)        │
  │  • Company fundamentals: EPS, PE, NAV, revenue, dividends   │
  │  • Annual report PDFs (10 years per company)                 │
  │  • Daily news, DSE announcements, regulatory filings         │
  │  • Macro: GDP, inflation, interest rates (Bangladesh Bank)   │
  └──────────────────────────┬──────────────────────────────────┘
                             ↓
  ┌─────────────────────────────────────────────────────────────┐
  │  PROCESSING                                                  │
  │  • ML models: 1yr technical + 5yr fundamental + 10yr macro  │
  │  • LLM agent: Claude with tool calling over all data        │
  │  • RAG pipeline: semantic search over news + reports        │
  │  • Sentiment scoring: per-company, per-sector               │
  └──────────────────────────┬──────────────────────────────────┘
                             ↓
  ┌─────────────────────────────────────────────────────────────┐
  │  OUTPUT                                                      │
  │  • Stock Health Score (0–100) for every DSE stock           │
  │  • Buy / Hold / Sell verdict with price targets             │
  │  • 1yr / 3yr / 5yr / 10yr growth projections               │
  │  • Plain-language explanations (English + Bengali)          │
  │  • Portfolio risk analysis and rebalancing suggestions       │
  │  • Automated PDF reports for institutional investors         │
  └─────────────────────────────────────────────────────────────┘
```

---

## 2. Full Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         EXTERNAL DATA SOURCES                            │
│                                                                          │
│  AmarStock API          DSE Official        BD News Sites                │
│  api.amarstock.com      dsebd.org           prothomalo.com               │
│  (live prices,          (announcements,     thefinancialexpress.com      │
│   historical CSV)        filings)            bdnews24.com                │
│                                                                          │
│  Bangladesh Bank        Annual Report PDFs  Macro Data                  │
│  (interest rates,       (uploaded manually  (GDP, CPI, FX)              │
│   monetary policy)       or auto-scraped)                               │
└────────────┬─────────────────┬──────────────────┬───────────────────────┘
             ↓                 ↓                  ↓
┌──────────────────────────────────────────────────────────────────────────┐
│                        DATA INGESTION LAYER                              │
│                                                                          │
│  ┌─────────────────┐  ┌────────────────┐  ┌──────────────────────────┐  │
│  │ Price Ingester  │  │ PDF Extractor  │  │ News Scraper + Embedder  │  │
│  │ (APScheduler)   │  │ (Claude API    │  │ (BeautifulSoup + voyage  │  │
│  │ every 15 min    │  │  Files API)    │  │  embeddings → pgvector)  │  │
│  │ during market   │  │                │  │                          │  │
│  │ hours           │  │ Extracts:      │  │ Runs: daily              │  │
│  │                 │  │ • Revenue      │  │ Stores: vector + text    │  │
│  │ Historical:     │  │ • Net Profit   │  │                          │  │
│  │ 2012 → now     │  │ • EPS          │  └──────────────────────────┘  │
│  │ via CSV bulk    │  │ • Dividends    │                               │
│  │ download        │  │ • Debt/Equity  │  ┌──────────────────────────┐  │
│  └─────────────────┘  │ • Management   │  │ Fundamental Scraper      │  │
│                        │   guidance     │  │ (weekly)                 │  │
│                        │ • Risk factors │  │ scrapes stock-chart      │  │
│                        └────────────────┘  │ pages: PE, NAV, beta,   │  │
│                                            │ free float, holdings     │  │
│                                            └──────────────────────────┘  │
└────────────┬─────────────────┬──────────────────┬───────────────────────┘
             ↓                 ↓                  ↓
┌──────────────────────────────────────────────────────────────────────────┐
│                           STORAGE LAYER                                  │
│                                                                          │
│  ┌──────────────────────────┐    ┌──────────────────────────────────┐   │
│  │   TimescaleDB            │    │   PostgreSQL (fundamentals)       │   │
│  │   (time-series prices)   │    │                                   │   │
│  │                          │    │   companies table                 │   │
│  │   stock_prices           │    │   fundamentals (10yr per stock)   │   │
│  │   ├─ ticker              │    │   annual_reports                  │   │
│  │   ├─ timestamp           │    │   dividends                       │   │
│  │   ├─ open/high/low/close │    │   sector_pe                       │   │
│  │   ├─ volume              │    │   macro_indicators                │   │
│  │   └─ value               │    │   ml_predictions                  │   │
│  └──────────────────────────┘    └──────────────────────────────────┘   │
│                                                                          │
│  ┌──────────────────────────┐    ┌──────────────────────────────────┐   │
│  │   pgvector               │    │   Redis                           │   │
│  │   (semantic search)      │    │   (live cache)                    │   │
│  │                          │    │                                   │   │
│  │   news_embeddings        │    │   • Current prices (TTL 15min)    │   │
│  │   report_chunks          │    │   • DSEX/DS30 index values        │   │
│  │   announcement_vectors   │    │   • Top gainers/losers            │   │
│  └──────────────────────────┘    │   • Session data                  │   │
│                                  └──────────────────────────────────┘   │
└────────────┬─────────────────────────────────────────────────────────────┘
             ↓
┌──────────────────────────────────────────────────────────────────────────┐
│                       ML PREDICTION ENGINE                               │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  Model 1: Technical (1yr horizon)                               │    │
│  │  Algorithm: LSTM (PyTorch)                                      │    │
│  │  Inputs: 60-day OHLCV window + RSI + MACD + BB + volume trend   │    │
│  │  Output: price range (low/mid/high) + direction probability     │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  Model 2: Fundamental (1–5yr horizon)                           │    │
│  │  Algorithm: XGBoost + DCF (Discounted Cash Flow)                │    │
│  │  Inputs: EPS 10yr CAGR, PE vs sector, NAV/price, revenue growth │    │
│  │           debt ratio, free float, dividend consistency          │    │
│  │  Output: fair value BDT + upside/downside %, growth rating A–F  │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  Model 3: Macro + Sector (5–10yr horizon)                       │    │
│  │  Algorithm: Linear regression + Monte Carlo simulation          │    │
│  │  Inputs: GDP growth, CPI, policy rate, sector rotation,         │    │
│  │           global industry trends, regulatory environment        │    │
│  │  Output: bull / base / bear 10yr scenario bands                 │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  Stock Health Score (composite 0–100)                           │    │
│  │  = 30% technical score                                          │    │
│  │  + 40% fundamental score                                        │    │
│  │  + 20% sentiment score (from news/LLM)                         │    │
│  │  + 10% macro/sector score                                       │    │
│  └─────────────────────────────────────────────────────────────────┘    │
└────────────┬─────────────────────────────────────────────────────────────┘
             ↓
┌──────────────────────────────────────────────────────────────────────────┐
│                        LLM AGENT LAYER (Claude)                          │
│                                                                          │
│  Model: claude-sonnet-4-6 (reasoning) / claude-haiku-4-5 (fast queries) │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────┐       │
│  │  System Prompt: Expert DSE analyst. 10yr data access.        │       │
│  │  Speaks English + Bengali. Gives verdicts with data backup.  │       │
│  │  Always flags risks. Never guarantees returns.               │       │
│  └──────────────────────────────────────────────────────────────┘       │
│                                                                          │
│  Tools available to LLM:                                                 │
│  ┌────────────────────────────────────────────────────────────────┐     │
│  │  get_stock_price(ticker, from, to)                             │     │
│  │  get_fundamentals(ticker, years=10)                            │     │
│  │  get_sector_comparison(ticker)                                 │     │
│  │  get_peer_comparison(ticker, metric)                           │     │
│  │  search_news(ticker, query, days_back)                         │     │
│  │  get_annual_report_data(ticker, year)                          │     │
│  │  get_ml_prediction(ticker, horizon_years)                      │     │
│  │  get_portfolio_analysis(holdings_json)                         │     │
│  │  screen_stocks(filters_json)                                   │     │
│  │  get_macro_data(indicator, from, to)                           │     │
│  │  get_sentiment_score(ticker, days_back)                        │     │
│  └────────────────────────────────────────────────────────────────┘     │
│                                                                          │
│  RAG Pipeline:                                                           │
│  user query → embed → pgvector similarity search → top-k chunks         │
│  → injected into LLM context → grounded answer with citations           │
└────────────┬─────────────────────────────────────────────────────────────┘
             ↓
┌──────────────────────────────────────────────────────────────────────────┐
│                         BACKEND API (FastAPI)                            │
│                                                                          │
│  POST /api/chat              → LLM conversational analyst                │
│  GET  /api/stock/{ticker}    → full stock profile                        │
│  GET  /api/predict/{ticker}  → ML predictions (1/3/5/10yr)              │
│  POST /api/screen            → filtered stock screener                   │
│  POST /api/portfolio         → portfolio analysis                        │
│  GET  /api/market/summary    → live DSEX/DS30 overview                  │
│  GET  /api/market/movers     → top gainers/losers/volume                 │
│  GET  /api/news/{ticker}     → latest news + sentiment                   │
│  POST /api/report/generate   → PDF institutional report                  │
│  WS   /ws/prices             → WebSocket live price stream               │
└────────────┬─────────────────────────────────────────────────────────────┘
             ↓
┌──────────────────────────────────────────────────────────────────────────┐
│                          FRONTEND (Next.js 14)                           │
│                                                                          │
│  Pages:                                                                  │
│  ├── /dashboard          Market overview, DSEX live, movers             │
│  ├── /stocks             Full stock screener with filters                │
│  ├── /stocks/[ticker]    Company deep dive page                          │
│  ├── /predict/[ticker]   ML prediction charts                            │
│  ├── /chat               LLM analyst chat interface                      │
│  ├── /portfolio          Portfolio manager + analyzer                    │
│  ├── /sectors            Sector PE, performance comparison               │
│  └── /reports            Download PDF reports                            │
│                                                                          │
│  Components:                                                             │
│  ├── TradingView Lightweight Charts (price charts)                       │
│  ├── Recharts (fundamentals, EPS trends, PE history)                     │
│  ├── Chat UI with streaming (SSE)                                        │
│  └── Stock Health Score gauge (0–100)                                    │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Layer 1 — Data Ingestion

### 3.1 Price Ingester

```python
# runs via APScheduler — every 15 minutes during DSE market hours (10am–2:30pm BD time)
# and once at end-of-day for final close prices

async def ingest_live_prices():
    response = await httpx.get("https://api.amarstock.com/latest-share-price")
    stocks = response.json()
    
    rows = []
    for stock in stocks:
        rows.append({
            "ticker":    stock["TRADING_CODE"],
            "timestamp": datetime.utcnow(),
            "open":      stock["OPEN"],
            "high":      stock["HIGH"],
            "low":       stock["LOW"],
            "close":     stock["LTP"],
            "ycp":       stock["YCP"],
            "volume":    stock["VOLUME"],
            "value":     stock["VALUE"],
            "trades":    stock["TRADE"]
        })
    
    await db.execute_many(INSERT_STOCK_PRICES_SQL, rows)
    await redis.set("live_prices", json.dumps(rows), ex=900)  # 15min TTL

# Historical bulk load — run once on setup
async def bulk_load_historical():
    # Download all CSVs from 2012–present via AmarStock CSV download
    # Process each CSV → insert into TimescaleDB in batches of 10,000 rows
    pass
```

### 3.2 Fundamental Scraper

```
Schedule: Weekly (Sunday night, before market open)
Source:   amarstock.com/stock-chart/{ticker} for each of 350+ stocks

Extracts per stock:
  - EPS (audited, unaudited, Q1/Q2/Q3)
  - PE ratio (audited + unaudited)
  - NAV and NAV/price ratio
  - Market cap
  - Free float %
  - Authorized capital, paid-up capital, reserve surplus
  - Beta
  - Shareholding: Directors, Govt, Institution, Foreign, Public
  - Dividend history (last 10 years)
  - 52-week high/low
```

### 3.3 PDF Annual Report Extractor

```
Trigger: Manual upload OR auto-detect new filing on DSE site

Process:
  1. Upload PDF to Claude Files API
  2. Send to claude-sonnet-4-6 with extraction prompt
  3. Claude returns structured JSON:
     {
       "ticker": "SQURPHARMA",
       "year": 2023,
       "revenue": 45230000000,
       "gross_profit": 12400000000,
       "net_profit": 6800000000,
       "eps": 28.4,
       "total_assets": 98000000000,
       "total_debt": 12000000000,
       "cash": 8500000000,
       "dividend_per_share": 15,
       "shares_outstanding": 239000000,
       "management_outlook": "...",
       "key_risks": ["..."],
       "capex": 2300000000
     }
  4. Store in annual_reports table
  5. Chunk report text → embed → store in pgvector
```

### 3.4 News Scraper + Embedder

```
Sources:
  - The Financial Express BD (thefinancialexpress.com.bd)
  - The Daily Star business section (thedailystar.net/business)
  - DSE announcements (dsebd.org/latest_news.php)
  - Prothom Alo business (Bengali)

Process:
  1. Scrape daily — new articles only
  2. Extract: headline, body, date, tickers mentioned
  3. LLM (haiku — cheap) assigns sentiment: +1 / 0 / -1 per ticker
  4. Embed article body → store in pgvector with metadata
  5. Store sentiment scores → aggregate per stock per day
```

---

## 4. Layer 2 — Storage

### 4.1 TimescaleDB — Stock Prices

```sql
-- Hypertable partitioned by time (auto-managed by TimescaleDB)
CREATE TABLE stock_prices (
    ticker      TEXT        NOT NULL,
    ts          TIMESTAMPTZ NOT NULL,
    open        NUMERIC(12,2),
    high        NUMERIC(12,2),
    low         NUMERIC(12,2),
    close       NUMERIC(12,2),
    ycp         NUMERIC(12,2),  -- yesterday close price
    volume      BIGINT,
    value       NUMERIC(18,2),  -- in BDT millions
    trades      INTEGER,
    PRIMARY KEY (ticker, ts)
);

SELECT create_hypertable('stock_prices', 'ts');
CREATE INDEX ON stock_prices (ticker, ts DESC);

-- Continuous aggregate for daily OHLCV
CREATE MATERIALIZED VIEW daily_ohlcv
WITH (timescaledb.continuous) AS
SELECT
    ticker,
    time_bucket('1 day', ts) AS day,
    FIRST(open, ts)  AS open,
    MAX(high)        AS high,
    MIN(low)         AS low,
    LAST(close, ts)  AS close,
    SUM(volume)      AS volume,
    SUM(value)       AS value
FROM stock_prices
GROUP BY ticker, day;
```

### 4.2 PostgreSQL — Companies & Fundamentals

```sql
CREATE TABLE companies (
    ticker          TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    name_bn         TEXT,  -- Bengali name
    sector          TEXT,
    category        TEXT,  -- A, B, N, Z on DSE
    listing_year    INTEGER,
    authorized_cap  BIGINT,
    paid_up_cap     BIGINT,
    shares_out      BIGINT,
    is_sharia       BOOLEAN DEFAULT FALSE,
    last_updated    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE fundamentals (
    id              SERIAL PRIMARY KEY,
    ticker          TEXT REFERENCES companies(ticker),
    recorded_at     DATE NOT NULL,
    eps_audited     NUMERIC(10,2),
    eps_unaudited   NUMERIC(10,2),
    eps_q1          NUMERIC(10,2),
    eps_q2          NUMERIC(10,2),
    eps_q3          NUMERIC(10,2),
    pe_audited      NUMERIC(10,2),
    pe_unaudited    NUMERIC(10,2),
    nav             NUMERIC(12,2),
    nav_price_ratio NUMERIC(8,4),
    market_cap      BIGINT,
    free_float_pct  NUMERIC(6,2),
    beta            NUMERIC(8,4),
    div_directors   NUMERIC(6,2),  -- shareholding %
    div_govt        NUMERIC(6,2),
    div_institution NUMERIC(6,2),
    div_foreign     NUMERIC(6,2),
    div_public      NUMERIC(6,2),
    week52_high     NUMERIC(12,2),
    week52_low      NUMERIC(12,2),
    UNIQUE(ticker, recorded_at)
);

CREATE TABLE annual_reports (
    id              SERIAL PRIMARY KEY,
    ticker          TEXT REFERENCES companies(ticker),
    fiscal_year     INTEGER NOT NULL,
    revenue         BIGINT,
    gross_profit    BIGINT,
    net_profit      BIGINT,
    total_assets    BIGINT,
    total_debt      BIGINT,
    cash            BIGINT,
    eps             NUMERIC(10,2),
    nav_per_share   NUMERIC(10,2),
    dividend_pct    NUMERIC(6,2),
    capex           BIGINT,
    roe             NUMERIC(8,4),  -- return on equity
    roa             NUMERIC(8,4),  -- return on assets
    debt_equity     NUMERIC(8,4),
    mgmt_outlook    TEXT,
    key_risks       TEXT[],
    pdf_url         TEXT,
    UNIQUE(ticker, fiscal_year)
);

CREATE TABLE dividends (
    id              SERIAL PRIMARY KEY,
    ticker          TEXT REFERENCES companies(ticker),
    year            INTEGER,
    cash_pct        NUMERIC(6,2),
    stock_pct       NUMERIC(6,2),  -- bonus shares
    ex_date         DATE,
    record_date     DATE,
    UNIQUE(ticker, year)
);

CREATE TABLE sector_pe (
    sector          TEXT NOT NULL,
    recorded_at     DATE NOT NULL,
    pe_simple       NUMERIC(10,2),
    pe_weighted     NUMERIC(10,2),
    market_cap      BIGINT,
    PRIMARY KEY(sector, recorded_at)
);

CREATE TABLE macro_indicators (
    indicator       TEXT NOT NULL,  -- 'GDP_GROWTH','CPI','POLICY_RATE','USD_BDT'
    recorded_at     DATE NOT NULL,
    value           NUMERIC(12,4),
    source          TEXT,
    PRIMARY KEY(indicator, recorded_at)
);

CREATE TABLE ml_predictions (
    id              SERIAL PRIMARY KEY,
    ticker          TEXT REFERENCES companies(ticker),
    generated_at    TIMESTAMPTZ DEFAULT NOW(),
    horizon_years   INTEGER,  -- 1, 3, 5, 10
    model_version   TEXT,
    price_low       NUMERIC(12,2),
    price_mid       NUMERIC(12,2),
    price_high      NUMERIC(12,2),
    direction_prob  NUMERIC(5,4),  -- probability of upward movement
    health_score    INTEGER,       -- 0–100
    rating          TEXT,          -- 'STRONG_BUY','BUY','HOLD','SELL','STRONG_SELL'
    fair_value      NUMERIC(12,2),
    upside_pct      NUMERIC(8,2)
);

CREATE TABLE news (
    id              SERIAL PRIMARY KEY,
    ticker          TEXT,
    published_at    TIMESTAMPTZ,
    headline        TEXT,
    url             TEXT UNIQUE,
    source          TEXT,
    sentiment       SMALLINT,   -- -1, 0, 1
    sentiment_score NUMERIC(4,3)  -- -1.0 to 1.0
);
```

### 4.3 pgvector — Semantic Search

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE document_chunks (
    id          SERIAL PRIMARY KEY,
    ticker      TEXT,
    doc_type    TEXT,  -- 'news', 'annual_report', 'announcement'
    doc_date    DATE,
    chunk_text  TEXT,
    embedding   vector(1536),  -- OpenAI text-embedding-3-small dims
    metadata    JSONB
);

CREATE INDEX ON document_chunks 
USING ivfflat (embedding vector_cosine_ops) 
WITH (lists = 100);
```

---

## 5. Layer 3 — ML Prediction Engine

### 5.1 Model 1: LSTM Technical Model (1-year)

```
Architecture:
  Input:  60 trading days × 12 features
          [open, high, low, close, volume, value,
           RSI-14, MACD, MACD-signal, BB-upper, BB-lower, SMA-20]
  
  Layers:
    LSTM(128 units) → Dropout(0.2)
    LSTM(64 units)  → Dropout(0.2)
    Dense(32)       → ReLU
    Dense(3)        → [price_low, price_mid, price_high]

  Output: 252-day (1yr) price prediction with confidence interval

Training:
  - 2012–2022 data for training
  - 2023 data for validation
  - 2024 data for testing
  - Retrain quarterly with new data
  - Separate model per sector (banking, pharma, telecom, etc.)
```

### 5.2 Model 2: XGBoost Fundamental Model (1–5yr)

```
Features (per stock, trailing 10yr):
  Technical:
    - EPS CAGR (3yr, 5yr, 10yr)
    - Revenue CAGR (3yr, 5yr, 10yr)
    - Net profit margin trend
    - ROE trend
    - Debt/equity trend

  Valuation:
    - Current PE vs 5yr avg PE
    - Current PE vs sector PE (premium/discount %)
    - NAV/price ratio
    - Price/Book ratio

  Quality:
    - Dividend consistency score (0–10)
    - Free float % (liquidity)
    - Director shareholding change (insider signal)
    - Beta (risk)

  Macro:
    - Sector GDP correlation
    - Industry tailwind/headwind score

Target: 
    - 1yr forward return (actual from historical)
    - Classification: outperform / market / underperform

DCF Component:
    - Project FCF using 5yr avg growth rate
    - Terminal value at 3% perpetuity growth
    - Discount at WACC (policy rate + equity risk premium)
    - Output: intrinsic value per share
```

### 5.3 Model 3: Macro Scenario Model (5–10yr)

```
Monte Carlo Simulation:
  - Run 10,000 scenarios varying:
    - GDP growth: 5%–7.5% range (Bangladesh historical)
    - Inflation: 6%–10% range
    - Policy rate: 7%–10% range
    - Sector-specific growth multiplier
  
  Output per stock:
    - P10 (bear case): price at 10th percentile scenario
    - P50 (base case): median scenario price
    - P90 (bull case): 90th percentile scenario price
    - 10yr annualized return bands
```

### 5.4 Stock Health Score

```
score = (
    technical_score  * 0.30 +   # from LSTM direction probability
    fundamental_score * 0.40 +   # from XGBoost + DCF upside
    sentiment_score  * 0.20 +   # from news LLM scoring (30-day avg)
    macro_score      * 0.10     # sector tailwind + macro environment
)

Thresholds:
    80–100 → STRONG BUY  (green)
    65–79  → BUY         (light green)
    45–64  → HOLD        (yellow)
    30–44  → SELL        (orange)
    0–29   → STRONG SELL (red)
```

---

## 6. Layer 4 — LLM Agent Layer

### 6.1 Agent Architecture

```python
import anthropic

client = anthropic.Anthropic()

SYSTEM_PROMPT = """
You are a senior equity analyst specializing in the Dhaka Stock Exchange (DSE), Bangladesh.
You have access to:
  - 10+ years of historical price data for all 350+ DSE-listed companies
  - Annual financial reports (revenue, profit, EPS, NAV, dividends)
  - Real-time news and market announcements
  - ML model predictions for 1yr, 3yr, 5yr, and 10yr horizons
  - Macro data: Bangladesh GDP, CPI, policy rates

Communication:
  - Answer in English by default, Bengali if user writes in Bengali
  - Always back claims with specific data points
  - Give clear verdicts: Strong Buy / Buy / Hold / Sell / Strong Sell
  - Always disclose that predictions carry uncertainty
  - Flag regulatory, liquidity, and concentration risks

Never: guarantee returns, give financial advice without data, invent numbers.
"""

tools = [
    {
        "name": "get_stock_price",
        "description": "Retrieve historical or current stock price data for a DSE ticker",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker":     {"type": "string", "description": "DSE ticker e.g. SQURPHARMA"},
                "from_date":  {"type": "string", "description": "ISO date YYYY-MM-DD"},
                "to_date":    {"type": "string", "description": "ISO date YYYY-MM-DD"},
                "interval":   {"type": "string", "enum": ["daily","weekly","monthly"]}
            },
            "required": ["ticker"]
        }
    },
    {
        "name": "get_fundamentals",
        "description": "Get company fundamentals: EPS, PE, NAV, market cap, dividends, shareholding",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker":       {"type": "string"},
                "years":        {"type": "integer", "default": 10},
                "include_annual_reports": {"type": "boolean", "default": True}
            },
            "required": ["ticker"]
        }
    },
    {
        "name": "get_sector_comparison",
        "description": "Compare a stock against its sector peers on key metrics",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker":  {"type": "string"},
                "metrics": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "e.g. ['pe','eps_growth','nav_price','dividend_yield']"
                }
            },
            "required": ["ticker"]
        }
    },
    {
        "name": "search_news",
        "description": "Semantic search over news, announcements, and report text for a company",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker":    {"type": "string"},
                "query":     {"type": "string"},
                "days_back": {"type": "integer", "default": 365},
                "top_k":     {"type": "integer", "default": 10}
            },
            "required": ["ticker", "query"]
        }
    },
    {
        "name": "get_ml_prediction",
        "description": "Get ML model price predictions and health score for a stock",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker":        {"type": "string"},
                "horizon_years": {
                    "type": "integer",
                    "enum": [1, 3, 5, 10],
                    "description": "Prediction horizon in years"
                }
            },
            "required": ["ticker", "horizon_years"]
        }
    },
    {
        "name": "screen_stocks",
        "description": "Screen all DSE stocks using fundamental and technical filters",
        "input_schema": {
            "type": "object",
            "properties": {
                "sector":          {"type": "string"},
                "min_health_score":{"type": "integer"},
                "max_pe":          {"type": "number"},
                "min_eps_growth":  {"type": "number", "description": "3yr CAGR %"},
                "min_market_cap":  {"type": "number", "description": "BDT millions"},
                "rating":          {"type": "string", "enum": ["STRONG_BUY","BUY","HOLD","SELL","STRONG_SELL"]},
                "limit":           {"type": "integer", "default": 20}
            }
        }
    },
    {
        "name": "get_portfolio_analysis",
        "description": "Analyze a portfolio of DSE stocks for risk, diversification, and returns",
        "input_schema": {
            "type": "object",
            "properties": {
                "holdings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "ticker":   {"type": "string"},
                            "quantity": {"type": "integer"},
                            "avg_cost": {"type": "number"}
                        }
                    }
                }
            },
            "required": ["holdings"]
        }
    },
    {
        "name": "get_macro_data",
        "description": "Get Bangladesh macroeconomic indicators",
        "input_schema": {
            "type": "object",
            "properties": {
                "indicator": {
                    "type": "string",
                    "enum": ["GDP_GROWTH","CPI","POLICY_RATE","USD_BDT","REMITTANCE","FDI"]
                },
                "from_date": {"type": "string"},
                "to_date":   {"type": "string"}
            },
            "required": ["indicator"]
        }
    }
]
```

### 6.2 RAG Pipeline

```python
async def rag_search(query: str, ticker: str = None, top_k: int = 10):
    # 1. Embed the query
    embedding = await embed(query)  # voyage-finance-2 or text-embedding-3-small
    
    # 2. Search pgvector
    sql = """
        SELECT chunk_text, doc_type, doc_date, metadata,
               1 - (embedding <=> $1) AS similarity
        FROM document_chunks
        WHERE ($2 IS NULL OR ticker = $2)
        ORDER BY embedding <=> $1
        LIMIT $3
    """
    chunks = await db.fetch(sql, embedding, ticker, top_k)
    
    # 3. Return formatted context for LLM
    return [
        {
            "text":       c["chunk_text"],
            "source":     c["doc_type"],
            "date":       str(c["doc_date"]),
            "similarity": float(c["similarity"])
        }
        for c in chunks
    ]
```

### 6.3 Streaming Chat Endpoint

```python
# FastAPI SSE endpoint — streams LLM response token by token to frontend

@app.post("/api/chat")
async def chat(request: ChatRequest):
    async def generate():
        messages = request.messages
        
        # Inject RAG context if ticker mentioned
        if request.active_ticker:
            context = await rag_search(
                request.messages[-1]["content"], 
                ticker=request.active_ticker
            )
            # Prepend context to user message
            messages[-1]["content"] = format_with_context(
                messages[-1]["content"], context
            )
        
        with client.messages.stream(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=tools,
            messages=messages
        ) as stream:
            for event in stream:
                if hasattr(event, "delta") and hasattr(event.delta, "text"):
                    yield f"data: {json.dumps({'text': event.delta.text})}\n\n"
                elif event.type == "tool_use":
                    result = await execute_tool(event.name, event.input)
                    yield f"data: {json.dumps({'tool': event.name, 'result': result})}\n\n"
    
    return EventSourceResponse(generate())
```

---

## 7. Layer 5 — Backend API

### 7.1 Endpoint Reference

```
Authentication:
  POST /auth/register           → create account
  POST /auth/login              → JWT token
  POST /auth/refresh            → refresh token

Market Data:
  GET  /api/market/summary      → DSEX, DS30, DSES live values
  GET  /api/market/movers       → top 10 gainers, losers, volume
  GET  /api/market/heatmap      → sector performance grid
  WS   /ws/prices               → real-time price stream (WebSocket)

Stocks:
  GET  /api/stocks              → list all with health scores + ratings
  GET  /api/stocks/{ticker}     → full profile (price + fundamentals + score)
  GET  /api/stocks/{ticker}/price?from=&to=&interval=
  GET  /api/stocks/{ticker}/fundamentals?years=10
  GET  /api/stocks/{ticker}/news?days=90
  GET  /api/stocks/{ticker}/dividends
  GET  /api/stocks/{ticker}/annual-reports
  GET  /api/stocks/{ticker}/predictions/{horizon}

Screening:
  POST /api/screen              → filter stocks by any criteria
  GET  /api/sectors             → sector summary with PE and performance
  GET  /api/sectors/{sector}/stocks

AI:
  POST /api/chat                → SSE streaming LLM analyst
  POST /api/analyze/{ticker}    → full LLM deep-dive report
  POST /api/compare             → compare 2–5 stocks head-to-head

Portfolio:
  GET  /api/portfolio           → user portfolio
  POST /api/portfolio/holdings  → add holding
  PUT  /api/portfolio/holdings/{id}
  DELETE /api/portfolio/holdings/{id}
  GET  /api/portfolio/analysis  → risk + return analysis

Reports:
  POST /api/reports/generate    → trigger PDF report generation
  GET  /api/reports             → list user's generated reports
  GET  /api/reports/{id}/download → download PDF

Admin:
  POST /api/admin/ingest/trigger → manual data refresh trigger
  GET  /api/admin/ingest/status  → ingestion job status
```

### 7.2 Rate Limiting & Caching

```
Rate limits (by subscription tier):
  Free:       50 API calls/day, chat limited to 10 msgs/day
  Pro:        1000 API calls/day, unlimited chat
  Institution: unlimited + priority support + PDF reports

Caching strategy:
  Redis TTL:
    live prices        → 15 minutes
    fundamentals       → 24 hours
    ML predictions     → 7 days (expensive to regenerate)
    sector summaries   → 1 hour
    news sentiment     → 6 hours
```

---

## 8. Layer 6 — Frontend

### 8.1 Page Structure

```
/dashboard
  ├── Market status bar (DSEX | DS30 | DSES live)
  ├── Top movers strip (gainers/losers scrolling)
  ├── Market heatmap (sector squares, color = performance)
  ├── Macro snapshot (USD/BDT, policy rate, CPI)
  └── Quick access: watchlist + portfolio summary

/stocks
  ├── Filters: sector, rating, PE range, health score, market cap
  ├── Table: ticker, price, change%, health score (gauge), rating, PE, EPS CAGR
  └── Sort by: health score, market cap, volume, EPS growth

/stocks/[ticker]
  ├── Header: name, ticker, sector, rating badge, health score gauge
  ├── Price chart (TradingView Lightweight Charts)
  │     └── Toggles: price, volume, MA overlays, technicals
  ├── 10-Year Fundamentals section
  │     ├── EPS trend (bar chart, 10 years)
  │     ├── Revenue + Net Profit trend (bar chart)
  │     ├── PE history vs sector PE (line chart)
  │     ├── NAV/Price ratio over time
  │     └── Dividend history (bar chart)
  ├── Valuation section
  │     ├── Fair value vs current price
  │     ├── PE comparison: stock vs sector vs market
  │     └── DCF intrinsic value range
  ├── Shareholding structure (donut chart)
  ├── Prediction section → links to /predict/[ticker]
  └── Recent news + sentiment (with LLM summaries)

/predict/[ticker]
  ├── 1yr technical forecast (fan chart with confidence bands)
  ├── 1–5yr fundamental projection (bull/base/bear)
  ├── 10yr scenario analysis (Monte Carlo output — P10/P50/P90)
  ├── Key assumptions panel
  └── Risk factors panel (auto-generated by LLM)

/chat
  ├── Persistent conversation (multi-turn)
  ├── Side panel: active stock context
  ├── Quick prompts: "Analyze SQURPHARMA", "Best pharma stocks", etc.
  ├── Embedded mini-charts when LLM references a stock
  └── Streaming response with tool-call indicators

/portfolio
  ├── Holdings table: ticker, qty, avg cost, current price, P&L%
  ├── Portfolio chart (pie: by stock, by sector)
  ├── Risk metrics: concentration, beta, volatility
  ├── 1yr forward projection (aggregate)
  └── LLM rebalancing suggestions panel

/reports
  ├── Generate report: select stock(s), horizon, format
  └── Download history
```

### 8.2 Key UI Components

```typescript
// Stock Health Score — circular gauge
<HealthGauge score={78} size="lg" />
// 0-29 red → 30-44 orange → 45-64 yellow → 65-79 light green → 80-100 green

// Rating Badge
<RatingBadge rating="BUY" />

// Streaming chat
<ChatWindow 
  endpoint="/api/chat"
  activeTicker={ticker}
  streaming={true}        // SSE
/>

// Prediction fan chart
<PredictionChart
  ticker={ticker}
  data={{
    actual: [...],        // historical prices
    forecast_low: [...],  // bear case
    forecast_mid: [...],  // base case  
    forecast_high: [...]  // bull case
  }}
/>
```

---

## 9. Database Schema

### Entity Relationship Overview

```
companies
    └──< fundamentals (one per week)
    └──< annual_reports (one per fiscal year)
    └──< dividends (one per year)
    └──< ml_predictions (one per horizon per run)
    └──< news (many — tagged with ticker)

stock_prices (timescaledb hypertable)
    └── partitioned by time, indexed by ticker

document_chunks (pgvector)
    └── linked to ticker + doc_type

users
    └──< portfolios
         └──< holdings → references companies

sector_pe → sector + date
macro_indicators → indicator + date
```

---

## 10. LLM Tool Definitions

See full tool definitions in Section 6.1 above.

### Tool Execution Logic

```python
async def execute_tool(name: str, inputs: dict) -> dict:
    match name:
        case "get_stock_price":
            rows = await db.fetch(
                "SELECT * FROM daily_ohlcv WHERE ticker=$1 AND day BETWEEN $2 AND $3",
                inputs["ticker"], inputs.get("from_date"), inputs.get("to_date")
            )
            return {"data": [dict(r) for r in rows]}
        
        case "get_fundamentals":
            fund = await db.fetch(
                "SELECT * FROM fundamentals WHERE ticker=$1 ORDER BY recorded_at DESC LIMIT $2",
                inputs["ticker"], inputs.get("years", 10) * 52
            )
            reports = await db.fetch(
                "SELECT * FROM annual_reports WHERE ticker=$1 ORDER BY fiscal_year DESC",
                inputs["ticker"]
            )
            return {"fundamentals": [...], "annual_reports": [...]}
        
        case "search_news":
            query_embedding = await embed(inputs["query"])
            chunks = await rag_search(inputs["query"], inputs["ticker"])
            news = await db.fetch(
                "SELECT * FROM news WHERE ticker=$1 AND published_at > NOW()-$2::interval ORDER BY published_at DESC",
                inputs["ticker"], f"{inputs.get('days_back', 365)} days"
            )
            return {"news": [...], "related_documents": chunks}
        
        case "get_ml_prediction":
            pred = await db.fetchrow(
                """SELECT * FROM ml_predictions 
                   WHERE ticker=$1 AND horizon_years=$2 
                   ORDER BY generated_at DESC LIMIT 1""",
                inputs["ticker"], inputs["horizon_years"]
            )
            return dict(pred)
        
        case "screen_stocks":
            # Dynamic WHERE clause from filters
            results = await screener.run(inputs)
            return {"stocks": results, "count": len(results)}
        
        case "get_portfolio_analysis":
            return await portfolio_analyzer.analyze(inputs["holdings"])
        
        case "get_macro_data":
            rows = await db.fetch(
                "SELECT * FROM macro_indicators WHERE indicator=$1 ORDER BY recorded_at",
                inputs["indicator"]
            )
            return {"data": [dict(r) for r in rows]}
```

---

## 11. ML Model Design

### Training Pipeline

```
1. Feature Engineering (Python / pandas)
   ├── Price features: returns, log-returns, volatility (20d, 60d)
   ├── Technical: RSI, MACD, BB%B, OBV, ATR, ADX
   ├── Fundamental: PE, EPS CAGR, NAV/price, ROE, D/E
   └── Macro: GDP growth, CPI, policy rate (aligned by date)

2. Training
   ├── LSTM: 2012–2022 train, 2023 val, 2024 test
   ├── XGBoost: same split, forward-chained CV to avoid lookahead
   └── Save model artifacts to /models/{version}/

3. Inference (scheduled: nightly after market close)
   ├── Generate predictions for all 350+ stocks
   ├── Store in ml_predictions table
   └── Update Redis cache

4. Monitoring
   ├── Track prediction vs actual (MAE, directional accuracy)
   ├── Alert if accuracy degrades >10% vs baseline
   └── Retrain trigger: quarterly or on accuracy drop
```

### Model Versioning

```
/models/
  v1/
    lstm_banking.pt
    lstm_pharma.pt
    lstm_general.pt
    xgboost_fundamental.pkl
    scaler.pkl
    feature_config.json
  v2/   ← next retrain
```

---

## 12. Data Flow Walkthroughs

### Flow A: User asks "Is BRACBANK a good investment for 5 years?"

```
1. Frontend sends message to POST /api/chat
2. FastAPI passes to LLM agent with system prompt
3. LLM decides: needs fundamentals + news + ML prediction
4. Tool calls (parallel where possible):
   ├── get_fundamentals("BRACBANK", years=10)
   │     → returns 10yr EPS, PE, NAV, dividends, shareholding
   ├── search_news("BRACBANK", "performance outlook risks", days_back=365)
   │     → returns top 10 relevant news chunks via pgvector
   └── get_ml_prediction("BRACBANK", horizon_years=5)
         → returns fair_value, upside_pct, rating, price_low/mid/high
5. LLM synthesizes all data → generates response:
   "BRACBANK shows [X]% EPS CAGR over 10 years...
    Current PE of [X] is [X]% below sector average...
    5-year ML projection: BDT [low]–[high], base case [mid]...
    Recent news: [summary of key developments]...
    Verdict: BUY. Fair value BDT [X] vs current [Y]. Upside [Z]%
    Risks: rising NPL ratio, interest rate sensitivity..."
6. Response streams to frontend via SSE
7. Frontend shows text alongside an auto-fetched mini price chart
```

### Flow B: Nightly data refresh

```
11:00 PM BD time (after all reporting done):
  1. APScheduler triggers ingest_live_prices() → final EOD snapshot
  2. Triggers fundamental_scraper() for any updated disclosures
  3. News scraper runs → new articles embedded → pgvector updated
  4. Sentiment scores recalculated for all stocks
  5. ML models run inference → new predictions stored
  6. Health scores recalculated
  7. Redis cache cleared → fresh data on next request
  8. Alerts checked → notify users of price targets hit
```

### Flow C: Annual report PDF processing

```
1. Admin uploads PDF via /api/admin/upload-report
2. File stored in S3/MinIO
3. Background job: 
   ├── Upload to Claude Files API
   ├── Send extraction prompt to claude-sonnet-4-6
   ├── Receive structured JSON (revenue, profit, EPS, risks, outlook)
   ├── Store in annual_reports table
   ├── Chunk full text (500 token chunks, 50 overlap)
   ├── Embed each chunk → store in document_chunks (pgvector)
   └── Update company fundamentals with latest year data
4. ML models retrained if new year data crosses threshold
```

---

## 13. Tech Stack Summary

| Layer | Technology | Purpose |
|---|---|---|
| Language | Python 3.12 | Backend + ML + data pipeline |
| Web framework | FastAPI | REST API + WebSocket + SSE |
| Frontend | Next.js 14 (App Router) | UI |
| Styling | Tailwind CSS | Design |
| Charts | TradingView Lightweight Charts | Price charts |
| Charts | Recharts | Fundamentals + ML charts |
| Time-series DB | TimescaleDB (PostgreSQL ext) | OHLCV storage |
| Relational DB | PostgreSQL 16 | Companies, fundamentals, reports |
| Vector DB | pgvector (same PostgreSQL) | Semantic search / RAG |
| Cache | Redis 7 | Live prices, sessions |
| Object storage | MinIO (self-hosted) or AWS S3 | PDF storage |
| LLM | Claude claude-sonnet-4-6 | Analyst + PDF extractor |
| LLM (fast) | Claude claude-haiku-4-5 | Sentiment scoring, simple queries |
| Embeddings | voyage-finance-2 | Finance-domain embeddings |
| ML | PyTorch (LSTM) + XGBoost | Price + fundamental models |
| ML utilities | pandas, scikit-learn, numpy | Feature engineering |
| Task scheduling | APScheduler | Data ingestion jobs |
| Task queue | Celery + Redis | Heavy background jobs |
| HTTP client | httpx | Async HTTP |
| Auth | JWT + bcrypt | User authentication |
| PDF generation | WeasyPrint | Institutional PDF reports |
| Containerization | Docker + Docker Compose | All services |
| Reverse proxy | Nginx | SSL, load balancing |
| Monitoring | Prometheus + Grafana | System health |

---

## 14. Infrastructure & Deployment

### Docker Compose Services

```yaml
services:
  api:           # FastAPI backend
  worker:        # Celery worker for background jobs
  scheduler:     # APScheduler service
  db:            # PostgreSQL 16 + TimescaleDB + pgvector
  redis:         # Redis 7
  storage:       # MinIO
  frontend:      # Next.js (or serve as static via Nginx)
  nginx:         # Reverse proxy + SSL termination
  prometheus:    # Metrics
  grafana:       # Dashboards
```

### Minimum Server Specs (Production)

```
Option A — Single VPS:
  CPU:  8 vCPU
  RAM:  32 GB (TimescaleDB + ML models need memory)
  Disk: 500 GB SSD (10yr price history + PDFs + vector index)
  OS:   Ubuntu 22.04 LTS
  Cost: ~$80–150/month (Hetzner, DigitalOcean, or Contabo)

Option B — Managed (AWS):
  RDS for PostgreSQL (db.t3.large) → TimescaleDB + pgvector
  EC2 (t3.xlarge) → API + ML inference
  ElastiCache (Redis) → cache layer
  S3 → PDF storage
  Cost: ~$200–400/month

Recommendation: Start with Option A (single VPS), scale when needed.
```

### Environment Variables

```env
# Database
DATABASE_URL=postgresql://user:pass@db:5432/dse_platform
REDIS_URL=redis://redis:6379

# Anthropic
ANTHROPIC_API_KEY=sk-ant-...

# Embeddings
VOYAGE_API_KEY=...

# AmarStock
AMARSTOCK_BASE_URL=https://api.amarstock.com

# Storage
MINIO_URL=http://storage:9000
MINIO_ACCESS_KEY=...
MINIO_SECRET_KEY=...

# Security
JWT_SECRET=...
JWT_EXPIRE_MINUTES=1440

# Notification
SMTP_HOST=...
WHATSAPP_API_KEY=...
```

---

## 15. Build Phases & Timeline

### Phase 1 — Data Foundation (Weeks 1–4)

```
Week 1: Infrastructure setup
  ✓ Docker Compose with PostgreSQL + TimescaleDB + pgvector + Redis
  ✓ Database schema creation
  ✓ Basic FastAPI skeleton

Week 2: Historical data ingestion
  ✓ Bulk load all AmarStock CSVs (2012–present) into TimescaleDB
  ✓ Company master data (350+ tickers, sectors, categories)
  ✓ Verify data integrity

Week 3: Live data pipeline
  ✓ APScheduler: 15-min price sync during market hours
  ✓ Fundamental scraper (weekly)
  ✓ News scraper + sentiment scoring (daily)

Week 4: PDF pipeline
  ✓ Manual PDF upload endpoint
  ✓ Claude extraction → structured JSON → DB
  ✓ Chunk + embed → pgvector
```

### Phase 2 — Core Application (Weeks 5–8)

```
Week 5: REST API
  ✓ All /api/stocks endpoints
  ✓ /api/market endpoints
  ✓ /api/sectors endpoints
  ✓ Authentication (JWT)

Week 6: Frontend — Market + Stock pages
  ✓ Dashboard with live market data
  ✓ Stock screener with filters
  ✓ Company deep dive page
  ✓ TradingView charts integration

Week 7: Frontend — Fundamentals
  ✓ 10-year EPS / revenue / PE charts
  ✓ Shareholding donut charts
  ✓ Dividend history
  ✓ Peer comparison tables

Week 8: Polish + Testing
  ✓ Mobile responsive UI
  ✓ Error handling
  ✓ Rate limiting
  ✓ Basic end-to-end tests
```

### Phase 3 — LLM Agent (Weeks 9–11)

```
Week 9: Tool functions
  ✓ All 8 tool functions implemented + tested
  ✓ RAG pipeline (pgvector search)
  ✓ Tool execution layer

Week 10: Chat endpoint + streaming
  ✓ SSE streaming endpoint
  ✓ Multi-turn conversation support
  ✓ Bengali language support
  ✓ Context injection (active ticker)

Week 11: Chat frontend
  ✓ Chat UI with streaming display
  ✓ Tool-call progress indicators
  ✓ Quick prompt chips
  ✓ Embedded mini-charts in responses
```

### Phase 4 — ML Models (Weeks 12–15)

```
Week 12: Feature engineering
  ✓ Technical indicators computed + stored
  ✓ Fundamental features normalized
  ✓ Train/val/test split preparation

Week 13: LSTM model
  ✓ Architecture design + training
  ✓ Sector-specific variants
  ✓ Backtesting on 2024 data

Week 14: XGBoost + DCF model
  ✓ XGBoost feature selection + training
  ✓ DCF calculator
  ✓ Health score formula calibration

Week 15: Integration + Monte Carlo
  ✓ Predictions stored in DB
  ✓ Prediction charts in frontend
  ✓ Monte Carlo 10yr scenarios
  ✓ Nightly inference scheduling
```

### Phase 5 — Investor Features (Weeks 16–18)

```
Week 16: Portfolio manager
  ✓ Holdings CRUD
  ✓ P&L calculation
  ✓ Portfolio risk metrics
  ✓ LLM rebalancing suggestions

Week 17: Alerts + Notifications
  ✓ Price target alerts
  ✓ Health score change alerts
  ✓ Earnings release notifications
  ✓ Email + WhatsApp delivery

Week 18: PDF Reports
  ✓ Report template design
  ✓ LLM-generated analysis text
  ✓ WeasyPrint PDF generation
  ✓ Download + history
```

**Total: ~18 weeks (4–5 months) with 1 backend + 1 frontend developer**

---

## 16. Risk & Limitations

### Data Risks

```
• AmarStock API: no SLA — scraping may break if site structure changes
  Mitigation: maintain CSV backup download as fallback

• Annual reports: not all DSE companies publish machine-readable PDFs
  Mitigation: manual data entry fallback for key companies

• Historical gaps: some small-cap stocks may have missing data years
  Mitigation: flag missing data clearly in UI, exclude from ML training
```

### ML Model Risks

```
• DSE is a thin, illiquid market — models trained on liquid markets fail
  Mitigation: train only on DSE data, include liquidity as a feature

• 10-year predictions carry compounding uncertainty — wide bands
  Mitigation: always show confidence intervals, never point predictions

• Regime changes (political, regulatory) invalidate historical patterns
  Mitigation: include macro regime indicator as feature; retrain quarterly

• Past performance does not predict future returns
  Mitigation: prominent disclaimers on all prediction pages
```

### LLM Risks

```
• Hallucination: LLM may generate plausible-sounding but wrong numbers
  Mitigation: all data via tool calls from DB — LLM never invents numbers;
              system prompt explicitly forbids citing unverified data

• Cost: heavy LLM usage (claude-sonnet-4-6) can be expensive at scale
  Mitigation: cache frequent queries; use claude-haiku-4-5 for simple tasks;
              rate limit free tier users

• Latency: multi-tool-call chains can take 10–20 seconds
  Mitigation: streaming SSE so user sees response immediately
```

### Legal / Compliance

```
• Platform provides analysis ONLY — not licensed investment advice
• Prominent disclaimer required on all pages:
  "This platform provides data and analysis for informational purposes only.
   It does not constitute investment advice. Invest at your own risk.
   Past performance does not guarantee future results."
• Do not use user portfolio data for model training without consent
• Store all data in Bangladesh or compliant jurisdiction (GDPR if EU users)
```

---

## 17. Data Pipeline Maintenance

This section answers: **how does the system keep all scheduled data pulls running reliably, handle failures, detect bad data, and alert the team?**

---

### 17.1 Scheduler Architecture

Two components handle all scheduled work:

```
┌─────────────────────────────────────────────────────────────────┐
│  APScheduler (in-process, lightweight)                          │
│  → handles high-frequency jobs: 15-min price pull, 1-hr checks │
│  → runs inside the `scheduler` Docker service                   │
│  → state stored in PostgreSQL (survives restarts)               │
└────────────────────────┬────────────────────────────────────────┘
                         │ for heavy jobs, dispatches to ↓
┌─────────────────────────────────────────────────────────────────┐
│  Celery + Redis (task queue)                                     │
│  → handles slow/heavy jobs: ML retrain, PDF extraction,         │
│    bulk fundamental scrape (350 pages), embedding pipeline      │
│  → workers can run in parallel                                   │
│  → jobs are retried automatically on failure                     │
└─────────────────────────────────────────────────────────────────┘
```

**Rule:** If a job takes < 5 seconds → APScheduler runs it directly.  
If a job takes > 5 seconds or involves external I/O bursts → APScheduler *enqueues* it to Celery.

---

### 17.2 Every Job Defined

```python
# ingestion/scheduler.py

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
import pytz

BD_TZ = pytz.timezone("Asia/Dhaka")

scheduler = AsyncIOScheduler(
    jobstores={"default": SQLAlchemyJobStore(url=DATABASE_URL)},
    timezone=BD_TZ
)

# ── EVERY 15 MIN (market hours: Sun–Thu 10:00–14:30) ──────────────
@scheduler.scheduled_job(
    "cron", day_of_week="sun-thu",
    hour="10-14", minute="0,15,30,45",
    id="live_price_pull"
)
async def job_live_prices():
    await ingest_live_prices()
    await update_redis_cache()

# ── END OF DAY (14:35 Sun–Thu) ────────────────────────────────────
@scheduler.scheduled_job(
    "cron", day_of_week="sun-thu",
    hour=14, minute=35,
    id="eod_snapshot"
)
async def job_eod():
    await ingest_eod_snapshot()        # final close prices
    await update_52week_ranges()
    await update_circuit_breakers()
    await update_market_pe()
    await celery_app.send_task("tasks.run_ml_inference")   # heavy → Celery

# ── EVERY 2 HOURS ─────────────────────────────────────────────────
@scheduler.scheduled_job("interval", hours=2, id="dse_announcements")
async def job_announcements():
    await scrape_dse_announcements()
    await scrape_news_all_sources()
    # sentiment scoring + embedding → Celery (slow)
    await celery_app.send_task("tasks.process_new_articles")

# ── DAILY 02:00 ───────────────────────────────────────────────────
@scheduler.scheduled_job("cron", hour=2, minute=0, id="daily_macro")
async def job_daily_macro():
    await fetch_usd_bdt_rate()
    await check_bsec_circulars()
    await check_new_ipo_filings()

# ── WEEKLY Sunday 23:00 ───────────────────────────────────────────
@scheduler.scheduled_job(
    "cron", day_of_week="sun",
    hour=23, minute=0,
    id="weekly_fundamentals"
)
async def job_weekly_fundamentals():
    # 350 pages × 3s delay = ~18 min → offload to Celery
    await celery_app.send_task("tasks.scrape_all_fundamentals")
    await celery_app.send_task("tasks.check_index_composition")

# ── MONTHLY 1st day 01:00 ─────────────────────────────────────────
@scheduler.scheduled_job("cron", day=1, hour=1, minute=0, id="monthly")
async def job_monthly():
    await fetch_bangladesh_bank_rates()   # policy rate, CPI
    await fetch_forex_reserves()
    await celery_app.send_task("tasks.recalculate_beta_all_stocks")

# ── QUARTERLY (Jan/Apr/Jul/Oct 1st) ──────────────────────────────
@scheduler.scheduled_job(
    "cron", month="1,4,7,10",
    day=1, hour=3, minute=0,
    id="quarterly_retrain"
)
async def job_quarterly():
    await fetch_gdp_data()
    await celery_app.send_task("tasks.retrain_ml_models")
```

---

### 17.3 Celery Task Definitions

```python
# ingestion/tasks.py

from celery import Celery
app = Celery("dse_platform", broker=REDIS_URL, backend=REDIS_URL)

app.conf.task_routes = {
    "tasks.run_ml_inference":      {"queue": "ml"},        # GPU/heavy queue
    "tasks.retrain_ml_models":     {"queue": "ml"},
    "tasks.scrape_all_fundamentals": {"queue": "scraper"}, # I/O queue
    "tasks.process_new_articles":  {"queue": "nlp"},       # LLM queue
}

@app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=300,    # retry after 5 min
    autoretry_for=(Exception,)
)
def scrape_all_fundamentals(self):
    tickers = db.get_all_tickers()
    for ticker in tickers:
        try:
            data = scrape_fundamental_page(ticker)
            db.upsert_fundamentals(ticker, data)
            time.sleep(2.5)  # polite rate limiting
        except Exception as exc:
            log_job_failure("fundamentals", ticker, str(exc))
            # continue — don't stop entire batch for one failure

@app.task(bind=True, max_retries=3, default_retry_delay=60)
def process_new_articles(self):
    articles = db.get_unprocessed_articles()
    for article in articles:
        # LLM sentiment scoring (haiku)
        sentiment = score_sentiment_haiku(article)
        # Embedding
        embedding = embed(article["body"])
        db.update_article(article["id"], sentiment, embedding)

@app.task(bind=True, max_retries=1)
def retrain_ml_models(self):
    # Long job — up to 2 hours
    train_lstm_models()
    train_xgboost_model()
    evaluate_and_promote()   # only swap model if new version beats old
    notify_admin("ML retrain complete")
```

---

### 17.4 Job State Tracking (DB table)

Every job writes a record on start and finish. This powers the monitoring dashboard.

```sql
CREATE TABLE pipeline_jobs (
    id              SERIAL PRIMARY KEY,
    job_id          TEXT NOT NULL,         -- e.g. "live_price_pull"
    job_type        TEXT NOT NULL,         -- "scheduler" | "celery"
    started_at      TIMESTAMPTZ DEFAULT NOW(),
    finished_at     TIMESTAMPTZ,
    status          TEXT,                  -- "running" | "success" | "failed" | "partial"
    records_written INTEGER DEFAULT 0,
    error_message   TEXT,
    duration_ms     INTEGER
);

CREATE INDEX ON pipeline_jobs (job_id, started_at DESC);
```

```python
# Every job wraps execution in this context manager

@contextmanager
async def job_run(job_id: str):
    run_id = await db.execute(
        "INSERT INTO pipeline_jobs (job_id, status) VALUES ($1, 'running') RETURNING id",
        job_id
    )
    t0 = time.monotonic()
    try:
        yield
        ms = int((time.monotonic() - t0) * 1000)
        await db.execute(
            "UPDATE pipeline_jobs SET status='success', finished_at=NOW(), duration_ms=$1 WHERE id=$2",
            ms, run_id
        )
    except Exception as e:
        await db.execute(
            "UPDATE pipeline_jobs SET status='failed', finished_at=NOW(), error_message=$1 WHERE id=$2",
            str(e), run_id
        )
        raise
```

---

### 17.5 Data Quality Checks

After every ingestion run, automated checks validate the data before it reaches the API or ML models.

```python
# ingestion/quality.py

QUALITY_RULES = {
    "live_price_pull": [
        # Rule: must have received prices for at least 300 of 350 stocks
        lambda result: result["stocks_received"] >= 300,
        # Rule: no stock price can change >20% in one 15-min tick (circuit breaker sanity)
        lambda result: result["max_pct_change"] <= 20.0,
        # Rule: DSEX index value must be non-zero
        lambda result: result["dsex_value"] > 0,
    ],
    "weekly_fundamentals": [
        # Rule: PE ratio must be between 0 and 500 for any stock
        lambda result: result["pe_out_of_range_count"] == 0,
        # Rule: EPS can't flip sign more than once in 3 years (data error signal)
        lambda result: result["eps_sign_flip_count"] < 10,
    ],
    "eod_snapshot": [
        # Rule: total market turnover > 0 (market actually traded)
        lambda result: result["total_value"] > 0,
        # Rule: at least 100 stocks traded
        lambda result: result["active_stocks"] >= 100,
    ]
}

async def run_quality_checks(job_id: str, result: dict) -> bool:
    rules = QUALITY_RULES.get(job_id, [])
    failures = [r for r in rules if not r(result)]
    
    if failures:
        await alert_ops(
            level="warning",
            message=f"Quality check failed for {job_id}: {len(failures)} rules violated",
            details=result
        )
        # Mark job as "partial" not "success"
        return False
    return True
```

**If quality check fails:**
- Data is flagged in DB (`quality_flag = 'suspect'`)
- API still serves last known good data (stale but valid)
- Ops team alerted
- Job retried in 30 minutes

---

### 17.6 Failure Handling & Retry Strategy

```
Job fails → what happens?

CRITICAL jobs (live prices, EOD snapshot):
  Retry: 3× with 5-min backoff
  If all retries fail:
    → serve stale Redis cache to users
    → show "Data delayed" banner on frontend
    → page on-call via WhatsApp alert

NON-CRITICAL jobs (news scrape, fundamentals):
  Retry: 3× with exponential backoff
  If all retries fail:
    → log to pipeline_jobs as "failed"
    → Grafana alert (email, non-urgent)
    → next scheduled run will pick up

ML inference failure:
  → keep serving previous predictions
  → show "Predictions as of [date]" label
  → alert team but do NOT crash the app

Source goes down (AmarStock API 404):
  → switch to CSV download fallback
  → alert team with specific source name
  → mark data as "delayed" in Redis
```

---

### 17.7 Monitoring Dashboard

All job health visible in Grafana via Prometheus metrics + direct DB queries.

```
Pipeline Health Dashboard panels:

┌────────────────────────────────────────────────────────────┐
│  Job Status Grid                                            │
│  live_price_pull    [✓] Last: 2 min ago  Duration: 0.8s   │
│  eod_snapshot       [✓] Last: 3h ago     Duration: 4.2s   │
│  dse_announcements  [✓] Last: 1h ago     Duration: 12s    │
│  weekly_fundamentals[✓] Last: 2d ago     Duration: 18min  │
│  ml_inference       [✓] Last: 8h ago     Duration: 4min   │
│  ml_retrain         [✓] Last: 89d ago    Duration: 2.1hr  │
├────────────────────────────────────────────────────────────┤
│  Data Freshness                                             │
│  Prices:       2 min stale   [GREEN]                       │
│  Fundamentals: 3 days stale  [GREEN — within weekly SLA]  │
│  News:         45 min stale  [GREEN]                       │
│  Macro:        18 days stale [GREEN — within monthly SLA] │
├────────────────────────────────────────────────────────────┤
│  Error Rate (last 7 days)                                  │
│  Total runs: 2,847  Failed: 3  Partial: 8   99.6% success │
├────────────────────────────────────────────────────────────┤
│  Records Written (last 24h)                                │
│  stock_prices:    113,400 rows                             │
│  news:            87 articles                              │
│  fundamentals:    0 (not weekly day)                       │
└────────────────────────────────────────────────────────────┘
```

**Admin API endpoints** (internal only):

```
GET  /api/admin/pipeline/status       → all job statuses
GET  /api/admin/pipeline/jobs?job_id= → history for one job
POST /api/admin/pipeline/trigger/{job_id}  → manual re-run
GET  /api/admin/data/freshness        → staleness per data type
GET  /api/admin/data/quality          → quality check history
```

---

### 17.8 Source Change Detection

AmarStock scraping will break if site structure changes. Detection layer:

```python
# ingestion/health_check.py

async def check_source_health():
    checks = {
        "amarstock_api": lambda: fetch_prices_count() >= 300,
        "dse_announcements": lambda: scrape_test_page("dsebd.org") is not None,
        "news_tbs": lambda: fetch_rss("tbsnews.net/rss") is not None,
        "bb_rates": lambda: fetch_policy_rate() is not None,
    }
    
    for source, check in checks.items():
        try:
            ok = check()
            await db.upsert_source_health(source, ok)
            if not ok:
                await alert_ops(f"Source DOWN: {source}")
        except Exception as e:
            await alert_ops(f"Source ERROR: {source} — {e}")

# Run this check every 6 hours
```

---

### 17.9 Data Lineage

Every row in the DB knows where it came from and when:

```sql
-- All ingested tables include:
source          TEXT,     -- 'amarstock_api' | 'dse_csv' | 'scraped_fundamental' | 'bb_api'
ingested_at     TIMESTAMPTZ DEFAULT NOW(),
ingestion_job   TEXT,     -- job_id that wrote this row
quality_flag    TEXT DEFAULT 'ok'  -- 'ok' | 'suspect' | 'manual_override'
```

Enables:
- "Show only rows from verified sources"
- Audit trail for any data point
- Retroactively flag bad batches if a bug is found

---

### 17.10 Alerting Channels

```
Severity    → Channel
─────────────────────────────────────────────────────
CRITICAL    → WhatsApp (ops team) + email
             (live price pull fails, DB down, API down)

WARNING     → Email only
             (quality check failed, source degraded,
              ML inference skipped)

INFO        → Grafana only (no notification)
             (job completed successfully, records written)
```

```python
async def alert_ops(level: str, message: str, details: dict = None):
    log.error(f"[{level}] {message}", extra=details)
    
    if level == "CRITICAL":
        await send_whatsapp(OPS_NUMBERS, f"🚨 {message}")
        await send_email(OPS_EMAIL, f"CRITICAL: {message}", details)
    
    elif level == "WARNING":
        await send_email(OPS_EMAIL, f"WARNING: {message}", details)
    
    # Always write to pipeline_alerts table for Grafana
    await db.insert_alert(level, message, details)
```

---

### Summary — Who Manages What

```
APScheduler service   → triggers all jobs on schedule, stores state in DB
Celery workers        → run heavy/slow jobs in background, auto-retry
Quality checks        → validate data after every ingestion
pipeline_jobs table   → permanent log of every run
Grafana dashboard     → real-time visibility into pipeline health
Alert system          → WhatsApp/email on critical failures
Source health checks  → detect when upstream sites change or go down
Data lineage columns  → every DB row is traceable to source + job
```

---

---

## 18. Monetization & Cost Management

This section covers: subscription tiers, real running costs, LLM cost control mechanisms, unit economics, and growth path.

---

### 18.1 Infrastructure Costs (Fixed)

These costs apply regardless of user count. Self-host everything on VPS to keep fixed costs low.

| Service | Spec | Cost/month |
|---|---|---|
| Primary VPS (Hetzner CPX41) | 8 vCPU, 16 GB RAM — API + DB + Redis + scheduler | ~$40 |
| ML Worker VPS | 4 vCPU, 8 GB RAM — Celery ML queue | ~$20 |
| PostgreSQL + TimescaleDB + pgvector | Self-hosted on primary VPS | $0 |
| Redis | Self-hosted on primary VPS | $0 |
| MinIO (PDF storage) | Self-hosted on primary VPS | $0 |
| Domain + SSL | Let's Encrypt (free cert) | ~$15/yr |
| **Total fixed** | | **~$60/month** |

> Scale trigger: move to larger VPS ($80–120/month) when hitting 1,000+ active users.

---

### 18.2 LLM Costs (Variable)

LLM is the dominant variable cost. Every design decision must account for it.

**Claude API pricing:**

```
claude-sonnet-4-6:   $3.00 / 1M input tokens    $15.00 / 1M output tokens
claude-haiku-4-5:    $0.25 / 1M input tokens     $1.25 / 1M output tokens
Prompt cache read:   80% discount vs full input price
```

**Cost per operation:**

| Operation | Model | Approx tokens | Cost per call |
|---|---|---|---|
| Chat query (1 turn + 2 tool calls) | Sonnet | 6,000 in + 600 out | $0.027 |
| Chat query (cached system prompt) | Sonnet | 1,500 new + 4,500 cached + 600 out | $0.010 |
| Sentiment score — 1 article | Haiku | 600 in + 50 out | $0.00016 |
| PDF annual report extraction | Sonnet | 40,000 in + 2,000 out | $0.150 |
| Stock deep-dive report | Sonnet | 12,000 in + 3,000 out | $0.081 |
| Portfolio analysis | Sonnet | 8,000 in + 1,500 out | $0.047 |
| Simple factual lookup (price, PE) | Haiku | 800 in + 100 out | $0.00033 |

**Platform-level background LLM costs (not user-driven):**

```
Daily news sentiment:  100 articles × $0.00016      =  $0.016/day
Nightly pre-generation top 50 stocks × $0.081        =  $4.05/day → $122/month
Monthly PDF extraction: ~30 new reports × $0.150     =  $4.50/month
Total background LLM cost                            ≈  $130/month
```

**Per-user chat LLM cost (with caching):**

```
Light user  (3  queries/day): 3  × $0.010 × 30 days = $0.90/month
Active user (10 queries/day): 10 × $0.010 × 30 days = $3.00/month
Heavy user  (30 queries/day): 30 × $0.010 × 30 days = $9.00/month
```

**Total platform cost at different scales:**

| Active users | Infra | Background LLM | User chat LLM (avg 8 q/day) | **Total/month** |
|---|---|---|---|---|
| 50 | $60 | $130 | $36 | **$226** |
| 200 | $60 | $130 | $144 | **$334** |
| 500 | $80 | $130 | $360 | **$570** |
| 1,000 | $120 | $130 | $720 | **$970** |
| 5,000 | $200 | $130 | $3,600 | **$3,930** |

---

### 18.3 Prompt Caching — Primary Cost Lever

Cache the system prompt and frequently accessed stock context. Every repeated query on the same stock hits the cache.

```python
import anthropic

client = anthropic.Anthropic()

async def run_llm_query(user_message: str, stock_context: dict) -> str:
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,                    # ~1,500 tokens
                "cache_control": {"type": "ephemeral"}    # cached — 80% cheaper on repeat
            },
            {
                "type": "text",
                "text": format_stock_context(stock_context),  # ~3,000 tokens of DB data
                "cache_control": {"type": "ephemeral"}        # cached per stock per session
            }
        ],
        messages=[{"role": "user", "content": user_message}]
    )
    return response.content[0].text

# Cache TTL = 5 minutes (Anthropic's prompt cache window)
# Same user asking 3 questions about BRACBANK in one session:
#   Query 1: full price  → $0.027
#   Query 2: cache hit   → $0.010  (63% saving)
#   Query 3: cache hit   → $0.010
#   Total 3 queries: $0.047 vs $0.081 without caching
```

---

### 18.4 Subscription Tiers

#### Free Tier — BDT 0/month

```
Access:
  ✓ Live market dashboard (DSEX / DS30 / DSES)
  ✓ Latest prices for all 350+ stocks
  ✓ Basic stock profile (price chart, current fundamentals)
  ✓ Stock screener — 3 filters maximum
  ✓ LLM chat analyst — 3 queries/day
  ✓ Sector overview
  ✗ ML price predictions (direction label only, no price target)
  ✗ Full 10-year fundamentals
  ✗ Portfolio manager
  ✗ Alerts
  ✗ PDF reports

LLM cost to platform per free user: ~$0.90/month
Purpose: acquisition funnel, not revenue
```

#### Pro Tier — BDT 499/month (~$4.55)

```
Access:
  ✓ Everything in Free
  ✓ LLM chat analyst — 30 queries/day
  ✓ Full ML predictions: 1yr / 3yr / 5yr with price ranges
  ✓ Full 10-year fundamentals + annual report extracted data
  ✓ Portfolio manager — up to 30 holdings
  ✓ Stock screener — unlimited filters
  ✓ Sector PE + macro dashboard
  ✓ Price alerts + health score alerts (email)
  ✓ Peer comparison (up to 5 stocks)
  ✗ 10yr Monte Carlo scenario analysis
  ✗ PDF institutional reports
  ✗ API access

LLM cost to platform per Pro user:  ~$3.00/month (10 q/day avg)
Revenue per Pro user:                $4.55/month
Gross margin per Pro user:          ~$1.55/month (34%)
```

#### Pro+ Tier — BDT 999/month (~$9.10)

```
Access:
  ✓ Everything in Pro
  ✓ LLM chat analyst — 100 queries/day
  ✓ 10-year Monte Carlo scenario analysis (bull/base/bear bands)
  ✓ Portfolio manager — unlimited holdings
  ✓ PDF report generation — 5 reports/month
  ✓ WhatsApp price alerts
  ✓ Early access to new features

LLM cost to platform per Pro+ user: ~$9.00/month (heavy use)
Revenue per Pro+ user:               $9.10/month
Gross margin:                        ~$0.10/month (near breakeven)
Value: Pro+ exists to capture power users; margin comes from volume
```

#### Institution Tier — BDT 9,999/month (~$91)

```
Access:
  ✓ Everything in Pro+
  ✓ LLM chat — unlimited queries
  ✓ PDF reports — unlimited
  ✓ REST API access — 1,000 calls/day
  ✓ White-label option (broker can embed under own brand)
  ✓ Custom stock universe + watchlists
  ✓ Bulk portfolio analysis (multiple client accounts)
  ✓ Dedicated WhatsApp support
  ✓ Up to 5 seats per subscription

LLM cost to platform per Institution: ~$30–50/month (heavy but cached)
Revenue per Institution:               $91/month
Gross margin:                          ~$41–61/month (45–67%)
```

---

### 18.5 Unit Economics

**Scenario A — Early Stage (20 Pro + 2 Institution)**

```
Revenue:
  20 Pro         × BDT 499  = BDT  9,980  (~$91)
   2 Institution × BDT 9,999 = BDT 19,998  (~$182)
  Total:                       BDT 29,978  (~$273)

Costs:
  Infra:                     $60
  Background LLM:            $130
  User chat LLM (20 Pro):    $60
  User chat LLM (2 Inst.):   $60
  Total:                     $310

Result: -$37/month (slightly underwater — needs 3 institutions to breakeven)
Breakeven: 3 Institution clients OR 40 Pro users
```

**Scenario B — Growth Stage (200 Pro + 10 Institution)**

```
Revenue:
  200 Pro        × BDT 499  = BDT  99,800  (~$909)
   10 Institution × BDT 9,999 = BDT  99,990  (~$909)
  Total:                        BDT 199,790  (~$1,818)

Costs:
  Infra:                     $60
  Background LLM:            $130
  User chat LLM:             $740
  Total:                     $930

Net profit: $888/month   (49% margin)
```

**Scenario C — Scale (1,000 Pro + 30 Institution)**

```
Revenue:
  1,000 Pro        × $4.55  = $4,550
     30 Institution × $91   = $2,730
  Total:                      $7,280/month

Costs:
  Infra:                     $200
  Background LLM:            $130
  User chat LLM:             $3,200
  Total:                     $3,530

Net profit: $3,750/month   (52% margin)
```

---

### 18.6 LLM Cost Control — Code Implementation

#### Hard Query Limits (Redis counter)

```python
# middleware/quota.py

DAILY_LIMITS = {
    "free":        3,
    "pro":        30,
    "pro_plus":  100,
    "institution": 9999   # effectively unlimited
}

async def enforce_quota(user_id: str, tier: str) -> None:
    key = f"chat_quota:{user_id}:{date.today().isoformat()}"
    used = await redis.incr(key)
    if used == 1:
        await redis.expire(key, 86400)  # reset at midnight

    limit = DAILY_LIMITS[tier]
    if used > limit:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "daily_limit_reached",
                "limit": limit,
                "used": used,
                "resets_at": "midnight BD time",
                "upgrade_url": "/pricing"
            }
        )
```

#### Route Queries to Cheapest Sufficient Model

```python
# llm/router.py

SIMPLE_PATTERNS = [
    "what is the price",
    "current pe",
    "last dividend",
    "52 week",
    "market cap",
    "what sector",
    "when was listed",
    "latest eps"
]

def select_model(query: str) -> str:
    q = query.lower()
    if any(p in q for p in SIMPLE_PATTERNS):
        return "claude-haiku-4-5-20251001"   # 12× cheaper
    return "claude-sonnet-4-6"
```

#### Cache LLM Responses (24-hour TTL for non-live queries)

```python
# llm/cache.py

CACHEABLE_QUERY_TYPES = [
    "fundamental_analysis",   # changes weekly
    "10yr_report",            # changes quarterly
    "sector_comparison",      # changes daily
    "peer_comparison",        # changes daily
]

async def cached_llm_response(
    ticker: str,
    query_type: str,
    run_fn: Callable
) -> str:
    if query_type not in CACHEABLE_QUERY_TYPES:
        return await run_fn()  # live price questions — never cache

    cache_key = f"llm:{ticker}:{query_type}:{date.today().isoformat()}"
    hit = await redis.get(cache_key)
    if hit:
        return hit.decode()  # free — no LLM call

    result = await run_fn()
    await redis.setex(cache_key, 86400, result)
    return result
```

#### Nightly Pre-generation for Top 50 Stocks

```python
# ingestion/tasks.py

TOP_50_STOCKS = [
    "SQURPHARMA", "BRACBANK", "GRAMEENPHONE", "ROBI",
    "DUTCHBANGL", "ISLAMIBANK", "BEXIMCO", "RENATA",
    # ... top 50 by market cap + daily volume
]

@app.task
async def pregenerate_popular_analyses():
    """
    Run every night at 22:00 BD time.
    Pre-generates LLM analysis for top 50 stocks.
    Free-tier users on these stocks get instant response at zero marginal cost.
    Cost: 50 × $0.081 = $4.05/night
    """
    for ticker in TOP_50_STOCKS:
        for query_type in ["fundamental_analysis", "sector_comparison"]:
            cache_key = f"llm:{ticker}:{query_type}:{date.today().isoformat()}"
            result = await run_deep_analysis(ticker, query_type)
            await redis.setex(cache_key, 90000, result)  # 25hr TTL
```

#### Token Budget by Tier

```python
# llm/context.py

MAX_CONTEXT_TOKENS = {
    "free":        4_000,    # summary data only — last 1yr
    "pro":        12_000,    # 5yr data + recent news
    "pro_plus":   24_000,    # 10yr data + full news archive
    "institution": 60_000    # everything — raw rows
}

def build_stock_context(ticker: str, tier: str) -> str:
    budget = MAX_CONTEXT_TOKENS[tier]

    if budget <= 4_000:
        # Free: current price + 1yr fundamentals only
        return build_summary_context(ticker, years=1)
    elif budget <= 12_000:
        # Pro: 5yr fundamentals + last 90 days news
        return build_standard_context(ticker, years=5, news_days=90)
    elif budget <= 24_000:
        # Pro+: 10yr fundamentals + last 365 days news + annual reports
        return build_full_context(ticker, years=10, news_days=365)
    else:
        # Institution: everything
        return build_institution_context(ticker)
```

---

### 18.7 Cost Monitoring Dashboard

Track LLM spend in real-time so there are no surprise bills.

```sql
CREATE TABLE llm_usage_log (
    id              SERIAL PRIMARY KEY,
    user_id         TEXT,
    tier            TEXT,
    model           TEXT,
    query_type      TEXT,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    cache_hit       BOOLEAN DEFAULT FALSE,
    cost_usd        NUMERIC(10,6),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX ON llm_usage_log (created_at DESC);
CREATE INDEX ON llm_usage_log (user_id, created_at DESC);
```

```python
# After every LLM call, log usage

async def log_llm_usage(
    user_id: str, tier: str, model: str,
    query_type: str, usage: dict, cache_hit: bool
):
    input_cost  = usage.input_tokens  * MODEL_PRICES[model]["input"]
    output_cost = usage.output_tokens * MODEL_PRICES[model]["output"]
    if cache_hit:
        input_cost *= 0.20  # 80% discount

    await db.execute("""
        INSERT INTO llm_usage_log
        (user_id, tier, model, query_type, input_tokens, output_tokens, cache_hit, cost_usd)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
    """, user_id, tier, model, query_type,
         usage.input_tokens, usage.output_tokens, cache_hit,
         input_cost + output_cost)
```

**Grafana panels from `llm_usage_log`:**
```
• Total LLM spend today / this month
• Cost per tier breakdown (free vs pro vs institution)
• Top 10 most expensive users this month
• Cache hit rate (target > 60%)
• Model distribution (haiku vs sonnet %)
• Daily cost trend vs revenue trend
```

**Budget alerts:**
```python
# If daily LLM spend exceeds threshold → alert immediately

DAILY_BUDGET_ALERT_USD = 50   # alert at $50/day
DAILY_BUDGET_HARD_CAP_USD = 100  # throttle all non-institution users at $100/day

async def check_daily_budget():
    today_spend = await db.fetchval(
        "SELECT SUM(cost_usd) FROM llm_usage_log WHERE created_at > NOW() - interval '24h'"
    )
    if today_spend > DAILY_BUDGET_HARD_CAP_USD:
        await enable_throttle_mode()   # switch all free users to haiku only
        await alert_ops("CRITICAL", f"LLM daily cap hit: ${today_spend:.2f}")
    elif today_spend > DAILY_BUDGET_ALERT_USD:
        await alert_ops("WARNING", f"LLM spend high: ${today_spend:.2f}/day")
```

---

### 18.8 Revenue Growth Path

```
Stage 1 — Validation (months 1–6)
  Target: 100 free + 20 Pro users
  Strategy:
    • Post in BD stock Facebook groups (DSE investors, stock analysis groups)
    • stockbd.com forum presence
    • Twitter/X BD finance community
    • Free tier drives word-of-mouth
  Revenue:  ~$91/month
  Costs:    ~$250/month
  Status:   loss-making — expected, validating product-market fit

Stage 2 — Institution Focus (months 6–12)
  Target: 300 free + 80 Pro + 3 Institution
  Strategy:
    • Direct outreach to BD asset managers, mutual funds, brokerage research desks
    • 1 institution deal = revenue of 22 Pro users at higher margin
    • Offer free 1-month trial to institutions
    • Broker white-label: broker pays BDT 4,999/month, serves their clients
  Revenue:  ~$637/month
  Costs:    ~$400/month
  Status:   profitable

Stage 3 — API Monetization (months 12–24)
  Target: data API sold to BD fintech apps, trading bots, academic researchers
  Pricing: BDT 1,999/month for 500 API calls/day
  Revenue addon: +$200–500/month

Stage 4 — Scale (year 2+)
  Target: enterprise sales to insurance investment desks, pension funds, NBFIs
  1 enterprise = 10 seats × BDT 3,000 = BDT 30,000/month (~$273)
  5 enterprise clients = $1,365/month additional
```

---

### 18.9 Monetization Summary

```
Cheapest path to profitability:
  3 Institution clients → covers all costs

Best unit economics:
  Institution tier — $91 revenue, ~$40 cost, $51 profit per client

LLM cost is controllable via:
  1. Prompt caching          → 63% cost reduction on repeat queries
  2. Model routing           → haiku for simple lookups (12× cheaper)
  3. Response caching        → 24hr Redis cache on non-live analyses
  4. Nightly pre-generation  → top 50 stocks served at zero marginal cost
  5. Hard query limits       → prevent runaway cost from heavy free users
  6. Token budgets per tier  → free users get summarized context only
  7. Daily budget hard cap   → automatic throttle at $100/day

Target cost structure at maturity:
  Infrastructure:  10% of revenue
  LLM:             40% of revenue
  Gross margin:    50%
```

---

*Architecture Version 1.2 — DSE Stock Intelligence Platform*  
*Last updated: 2026-05-20*
