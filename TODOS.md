# DSE Stock Intelligence Platform — Build Tracker

> Status legend: `[ ]` todo · `[~]` in progress · `[x]` done · `[-]` blocked · `[s]` skipped

---

## CURRENT FOCUS → Layer 1: Data Extraction

---

## Layer 0 — Project Bootstrap

- [x] `git init` + `.gitignore` (Python, .env, __pycache__, models/)
- [x] `pyproject.toml` with Python 3.12 + uv or pip-tools
- [x] `.env.example` with all required env vars
- [x] `docker-compose.yml` — extraction-only services (db + redis + scheduler + worker)
- [ ] Verify Docker Desktop running on Windows
- [x] `Makefile` or `scripts/` for common dev commands (up, down, migrate, test)

---

## Layer 1 — Data Extraction (ACTIVE)

### Phase 1A — Infrastructure Skeleton

- [x] Create `extraction/` package structure (matches PRD §16 file tree)
- [x] `extraction/base.py` — `BaseAdapter`, `DataStream`, `AdapterResult`, `AdapterError`, `AllAdaptersFailedError`
- [x] `extraction/registry.py` — `STREAMS` dict skeleton (all 16 streams defined, adapters stubbed)
- [x] `extraction/normalizers.py` — shared utils: BDT string → Decimal, BD time → UTC, ticker upper()
- [x] `extraction/quality.py` — `QUALITY_RULES` dict + `run_quality_checks()`
- [x] `extraction/health.py` — `compute_structure_hash()`, `check_source_health()`

### Phase 1B — Database (extraction tables only)

- [x] `db/migrations/001_extensions.sql` — enable TimescaleDB + pgvector
- [x] `db/migrations/002_companies.sql` — companies table
- [x] `db/migrations/003_stock_prices.sql` — hypertable + daily_ohlcv materialized view
- [x] `db/migrations/004_fundamentals.sql` — fundamentals + annual_reports + dividends + sector_pe
- [x] `db/migrations/005_news.sql` — news + document_chunks (pgvector)
- [x] `db/migrations/006_macro.sql` — macro_indicators
- [x] `db/migrations/007_pipeline.sql` — pipeline_jobs + source_health + agent_decisions + pipeline_alerts
- [x] `db/migrate.py` — run migrations in order, idempotent
- [ ] Verify TimescaleDB hypertable creation works locally
- [ ] Verify pgvector extension loads

### Phase 1C — bdshare Adapters (PRIMARY source for most streams)

- [ ] `pip install bdshare==1.2.1` — pin version
- [ ] **Smoke test all bdshare methods against real DSE** — run during market hours, record actual column names returned
  - [ ] `get_current_trade_data()` — log actual columns, row count, sample row
  - [ ] `get_hist_data("SQURPHARMA", "2024-01-01", "2024-12-31")` — verify OHLCV structure
  - [ ] `get_basic_hist_data()` — confirm TA-compatible column order
  - [ ] `get_market_info()` — confirm DSEX/DS30/DSES columns
  - [ ] `get_latest_pe()` — confirm PE + EPS + sector columns
  - [ ] `get_company_info("SQURPHARMA")` — map each DataFrame in returned list to fields
  - [ ] `get_company_info("BRACBANK")` — test banking sector (structure may differ)
  - [ ] `get_sector_performance()` — confirm sector + change_pct + pe columns
  - [ ] `get_top_gainers_losers()` — confirm columns
  - [ ] `get_corporate_announcements()` — confirm date + ticker + category + details columns
  - [ ] `get_price_sensitive_news()` — confirm structure
  - [ ] `get_agm_news()` — confirm cash_div_pct + stock_div_pct + agm_date
  - [ ] `get_market_depth_data("GP")` — confirm 5-level buy/sell structure
  - [ ] Save fixture files: `tests/fixtures/bdshare_{method}_sample.pkl` for each
- [x] `extraction/adapters/bdshare/live_prices.py` — `BDShareLivePricesAdapter` + `normalize()`
- [x] `extraction/adapters/bdshare/historical.py` — `BDShareHistoricalAdapter` + `normalize()`
- [x] `extraction/adapters/bdshare/market_info.py` — `BDShareMarketInfoAdapter`
- [x] `extraction/adapters/bdshare/fundamentals.py` — `BDShareCompanyInfoAdapter` (parse list of DFs)
- [x] `extraction/adapters/bdshare/sector.py` — `BDShareSectorAdapter`
- [x] `extraction/adapters/bdshare/announcements.py` — `BDShareAnnouncementsAdapter` + PSN + AGM
- [x] `extraction/adapters/bdshare/depth.py` — `BDShareDepthAdapter`
- [ ] Unit tests: `normalize()` for each bdshare adapter using fixtures (blocked on smoke test)

### Phase 1D — AmarStock Adapters (BACKUP / fundamentals PRIMARY)

- [ ] Test `https://api.amarstock.com/latest-share-price` — verify JSON structure, field names
- [ ] Test AmarStock CSV download — verify format, date range available
- [ ] `extraction/adapters/amarstock/live_prices.py` — `AmarStockLivePricesAdapter`
- [ ] `extraction/adapters/amarstock/csv_historical.py` — `AmarStockCSVAdapter` (bulk one-time + incremental)
- [ ] Scrape `amarstock.com/stock-chart/SQURPHARMA` with BeautifulSoup — map all fields to canonical schema
- [ ] Scrape 5 more tickers across sectors — verify field consistency
- [ ] `extraction/adapters/amarstock/fundamentals_scraper.py` — `AmarStockFundamentalsAdapter`
- [ ] Unit tests with saved HTML fixtures

### Phase 1E — DSE Direct + Playwright Adapters (TERTIARY / PDF)

- [ ] Test dsebd.org pages with requests — identify which need Playwright vs static HTML
- [ ] `extraction/adapters/dse_direct/playwright_base.py` — `PlaywrightAdapter` base class
- [ ] `extraction/adapters/dse_direct/live_prices.py` — `DSEDirectLivePricesAdapter`
- [ ] `extraction/adapters/dse_direct/announcements.py` — `DSEDirectAnnouncementsAdapter`
- [ ] `extraction/adapters/dse_direct/pdf_discovery.py` — `DSEDirectPDFAdapter` (find + download PDFs)
- [ ] Test PDF discovery for 3 tickers — verify PDF links found correctly
- [ ] Structure hash baseline run — record hashes for AmarStock + dsebd.org key pages

### Phase 1F — News Adapters

- [ ] `extraction/adapters/news/tbs_rss.py` — `TBSNewsRSSAdapter` (RSS feed parse)
- [ ] `extraction/adapters/news/financial_express.py` — RSS + BS4 fallback
- [ ] `extraction/adapters/news/daily_star.py` — RSS
- [ ] `extraction/adapters/news/dhaka_tribune.py` — RSS
- [ ] `extraction/adapters/news/prothomalo_playwright.py` — Playwright (no RSS)
- [ ] `extraction/adapters/news/ticker_extractor.py` — haiku NER: article text → list of DSE tickers
- [ ] Test ticker extraction on 10 real articles — measure accuracy

### Phase 1G — Macro Adapters

- [ ] Test Bangladesh Bank HTML pages — confirm table structure for each indicator
- [ ] `extraction/adapters/macro/bangladesh_bank.py` — policy rate + CPI + FX + remittance scrapers
- [ ] `extraction/adapters/macro/world_bank.py` — `WorldBankAdapter(indicator=...)` — all 5 macro indicators
- [ ] BSEC: `extraction/adapters/bsec/ipo_playwright.py` — IPO filings

### Phase 1H — DataStream Wiring + Failover

- [ ] Wire all 16 streams in `extraction/registry.py` with real adapter instances
- [ ] Failover integration tests — patch primary adapter to fail, verify secondary takes over
- [ ] Schema consistency test — all adapters per stream return identical columns
- [ ] `extraction/scheduler.py` — all APScheduler jobs (matches ARCHITECTURE.md §17.2)
- [ ] `extraction/tasks.py` — all Celery tasks (scrape_all_fundamentals, process_new_articles, etc.)
- [ ] `job_run()` context manager wired into all jobs

### Phase 1I — Bulk Historical Load (one-time)

- [ ] Bulk download AmarStock CSVs: all tickers, 2012–present
- [ ] Batch insert into TimescaleDB: 10,000 rows/batch
- [ ] Verify row counts per ticker, flag gaps
- [ ] Seed `companies` table: 350+ tickers + sector + category from DSE company list

### Phase 1J — Observability

- [ ] `source_health` table populated by 6-hourly health checks
- [ ] `pipeline_jobs` table — every run logged
- [ ] `pipeline_alerts` table — all alerts recorded
- [ ] Grafana + Prometheus Docker services added to compose
- [ ] Pipeline health dashboard (job status grid + freshness bars + error rate)
- [ ] Alert routing: WhatsApp (CRITICAL) + email (WARNING)

### Phase 1K — Management API + UI (extraction layer only)

- [ ] FastAPI app skeleton: `mgmt/main.py`
- [ ] `/mgmt/streams` endpoints (GET all, GET one, adapter list)
- [ ] `/mgmt/adapters/{name}/promote|demote|pause|resume`
- [ ] `/mgmt/jobs` endpoints (status, history, trigger, pause, resume)
- [ ] `/mgmt/quality/failures` + `/mgmt/freshness`
- [ ] `/mgmt/health` endpoints + `/mgmt/structure-hashes`
- [ ] `/mgmt/alerts` endpoints
- [ ] AI Ops Agent: `mgmt/agent/ops_agent.py` — scheduled sweep + alert hook + chat
- [ ] `/mgmt/agent` endpoints (status, run, decisions, queue, approve/reject, chat SSE)
- [ ] Next.js management UI: `/mgmt/dashboard` — stream health grid + freshness bars
- [ ] `/mgmt/streams/{id}` — adapter priority drag-reorder + data sample viewer
- [ ] `/mgmt/agent` — decision log + approval queue cards + chat console

### Phase 1L — Testing + Hardening

- [ ] Per-adapter unit tests (normalize() on fixture data) — all adapters
- [ ] Failover integration tests — all 16 streams
- [ ] Quality check unit tests — all rule sets
- [ ] Health check tests — mock source down, verify alert fires
- [ ] Load test: 350 tickers × bdshare fundamentals — measure rate limiting behavior
- [ ] Run full pipeline for 1 week — verify no silent failures

---

## Layer 2 — Storage (FUTURE)

> Not started. Unlock after Layer 1 is stable.

- [ ] TimescaleDB continuous aggregates tuned for query patterns
- [ ] pgvector index tuning (ivfflat lists parameter)
- [ ] Redis cache layer: TTLs per data type
- [ ] MinIO / S3 setup for PDF storage
- [ ] DB backup strategy (pg_dump schedule)
- [ ] Connection pooling (pgBouncer or asyncpg pool tuning)

---

## Layer 3 — ML Prediction Engine (FUTURE)

> Not started. Requires ≥1 year of clean extracted data first.

- [ ] Feature engineering pipeline (technical indicators: RSI, MACD, BB, OBV, ATR, ADX)
- [ ] LSTM model: architecture + training (2012–2022) + validation (2023) + test (2024)
- [ ] Sector-specific LSTM variants (banking, pharma, telecom, general)
- [ ] XGBoost fundamental model + feature selection
- [ ] DCF calculator
- [ ] Monte Carlo simulation (10,000 scenarios, 5–10yr horizon)
- [ ] Stock Health Score composite formula calibration
- [ ] Model versioning + artifact storage (`/models/v1/`)
- [ ] Nightly inference pipeline (350+ stocks)
- [ ] Prediction accuracy monitoring (MAE + directional accuracy)
- [ ] Quarterly retrain trigger

---

## Layer 4 — LLM Agent Layer (FUTURE)

> Not started. Requires storage + ML layers.

- [ ] Claude client setup with prompt caching
- [ ] System prompt finalized (English + Bengali)
- [ ] All 8 tool functions implemented + `execute_tool()` router
- [ ] RAG pipeline: embed query → pgvector search → inject context
- [ ] SSE streaming chat endpoint (`POST /api/chat`)
- [ ] Multi-turn conversation support
- [ ] Bengali language test suite
- [ ] Annual report PDF extraction via Claude Files API
- [ ] Haiku sentiment scoring pipeline for news articles

---

## Layer 5 — Backend API (FUTURE)

> Not started. Requires LLM layer.

- [ ] FastAPI app full setup: auth, CORS, rate limiting, error handling
- [ ] JWT auth: register + login + refresh
- [ ] All `/api/stocks` endpoints
- [ ] All `/api/market` endpoints + WebSocket price stream
- [ ] `/api/sectors` endpoints
- [ ] `/api/chat` SSE + `/api/analyze` + `/api/compare`
- [ ] Portfolio CRUD + analysis
- [ ] PDF report generation (WeasyPrint)
- [ ] Rate limiting per subscription tier (free / pro / institution)
- [ ] Redis caching per endpoint (TTLs per data type)
- [ ] API integration tests

---

## Layer 6 — Frontend (FUTURE)

> Not started. Requires backend API.

- [ ] Next.js 14 app setup (App Router, TypeScript, Tailwind)
- [ ] `/dashboard` — market overview, DSEX live, movers, heatmap
- [ ] `/stocks` — screener with filters
- [ ] `/stocks/[ticker]` — deep dive: price chart + 10yr fundamentals + shareholding
- [ ] `/predict/[ticker]` — ML fan chart + Monte Carlo scenarios
- [ ] `/chat` — LLM analyst chat with streaming + tool indicators
- [ ] `/portfolio` — holdings + P&L + risk metrics + rebalancing
- [ ] `/sectors` — sector PE + performance comparison
- [ ] `/reports` — generate + download PDF reports
- [ ] TradingView Lightweight Charts integration
- [ ] Recharts for fundamentals (EPS, revenue, PE history)
- [ ] Stock Health Score gauge component (0–100)
- [ ] Bengali language toggle
- [ ] Mobile responsive
- [ ] Disclaimer banner on all pages

---

## Layer 7 — Infrastructure + Deployment (FUTURE)

> Not started.

- [ ] Production docker-compose.yml (all services)
- [ ] Nginx config (SSL termination, reverse proxy)
- [ ] Let's Encrypt cert setup
- [ ] VPS provisioning (Hetzner CPX41 recommended)
- [ ] Environment secrets management
- [ ] PostgreSQL backup cron (daily pg_dump to MinIO)
- [ ] Prometheus scrape config + Grafana dashboards
- [ ] CI pipeline (lint + test on push)

---

## Milestone Checkpoints

| Milestone | Condition | Status |
|---|---|---|
| M1: Data flows | live_prices stream works end-to-end with failover | `[ ]` |
| M2: All streams | All 16 streams ingesting + quality checks passing | `[ ]` |
| M3: Bulk history | 2012–present OHLCV loaded, verified | `[ ]` |
| M4: Mgmt UI live | Pipeline dashboard + AI ops agent running | `[ ]` |
| M5: ML baseline | LSTM + XGBoost trained, predictions in DB | `[ ]` |
| M6: LLM analyst | Chat endpoint live, 3 tool calls working | `[ ]` |
| M7: Full API | All endpoints live, auth working | `[ ]` |
| M8: Frontend MVP | Dashboard + stock page + chat page live | `[ ]` |
| M9: Production | Deployed on VPS, SSL, monitoring live | `[ ]` |

---

## Known Blockers / Decisions Needed

- [ ] Confirm: run bdshare smoke tests during DSE market hours (Sun–Thu 10am–2:30pm BD = 4am–8:30am UTC)
- [ ] Confirm: AmarStock rate limit — need to test before committing to 2s delay
- [ ] Decision: uv vs pip-tools vs poetry for dep management
- [ ] Decision: asyncpg vs psycopg3 for async PostgreSQL
- [ ] Decision: Playwright runs in Docker or on host for dev?
- [ ] Decision: MinIO or S3 for local dev PDF storage?
