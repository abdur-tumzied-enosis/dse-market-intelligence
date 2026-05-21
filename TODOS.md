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
- [x] Verify Docker Desktop running on Windows
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
- [x] Verify TimescaleDB hypertable creation works locally
- [x] Verify pgvector extension loads

### Phase 1C — bdshare Adapters (PRIMARY source for most streams)

- [x] `pip install bdshare==1.2.1` — pin version (already in pyproject.toml, installed 2026-05-21)
- [~] **Smoke test all bdshare methods against real DSE** — 7/13 passing, 6 blocked/failed
  - [x] `get_current_trade_data()` — columns: symbol, ltp, high, low, close, ycp, change, trade, value, volume (396 rows). NOTE: no `open` in live feed
  - [x] `get_historical_data(start, end, code)` — columns: date(idx), symbol, ltp, high, low, open, close, ycp, trade, value, volume. NOTE: was `get_hist_data` (deprecated)
  - [x] `get_basic_historical_data(start, end, code)` — columns: date, open, high, low, close, volume. NOTE: was `get_basic_hist_data` (deprecated)
  - [x] `get_market_info()` — columns: Date, Total Trade, Total Volume, Total Value (mn), Total Market Cap. (mn), DSEX Index, DSES Index, DS30 Index, DGEN Index (30-day history, take row 0)
  - [x] `get_latest_pe()` — numeric columns 0-8 (no headers): [ticker, ltp, close, pe, ?, ?, ?, eps, ?] — 420 rows
  - [-] `get_company_info("SQURPHARMA")` — **BLOCKED: bdshare bug** `pd.read_html(r.content)` fails with OSError in pandas+lxml; need to patch bdshare or implement direct scraper
  - [-] `get_company_info("BRACBANK")` — same bdshare bug
  - [-] `get_sector_performance()` — **BLOCKED: returns empty** — may work during market hours, needs re-test
  - [-] `get_top_gainers_losers()` — **BLOCKED: returns empty** — may work during market hours, needs re-test
  - [-] `get_corporate_announcements()` — empty post-market (columns expected: code, news, date); re-test during market hours
  - [-] `get_price_sensitive_news()` — empty post-market (columns expected: code, news, date); re-test during market hours
  - [x] `get_agm_news()` — columns: company, yearEnd, dividend, agmDate, recordDate, venue, time (209 rows). NOTE: `company` is full name not ticker; `dividend` is combined string "10% C"
  - [x] `get_market_depth_data("GP")` — returns empty DataFrame post-market (expected); structure TBD from market-hours run
  - [x] Save fixture files: `tests/fixtures/bdshare_{method}_sample.pkl` — saved for all 7 passing methods
- [x] `extraction/adapters/bdshare/live_prices.py` — `BDShareLivePricesAdapter` + `normalize()`
- [x] `extraction/adapters/bdshare/historical.py` — `BDShareHistoricalAdapter` + `normalize()`
- [x] `extraction/adapters/bdshare/market_info.py` — `BDShareMarketInfoAdapter`
- [x] `extraction/adapters/bdshare/fundamentals.py` — `BDShareCompanyInfoAdapter` (parse list of DFs)
- [x] `extraction/adapters/bdshare/sector.py` — `BDShareSectorAdapter`
- [x] `extraction/adapters/bdshare/announcements.py` — `BDShareAnnouncementsAdapter` + PSN + AGM
- [x] `extraction/adapters/bdshare/depth.py` — `BDShareDepthAdapter`
- [ ] Unit tests: `normalize()` for each bdshare adapter using fixtures (blocked on smoke test)

### Phase 1D — AmarStock Adapters (BACKUP / fundamentals PRIMARY)

- [x] Smoke-test all AmarStock endpoints — 14/14 passing (2026-05-21)
  - API is SPA-internal JSON (not the assumed REST API). Actual endpoints discovered via Playwright network intercept.
  - `GET /LatestPrice/dbfd2587c77f` — 426 stocks, 62 fields: full OHLCV + PE/EPS/NAV/shareholding/quarterly EPS (static hash, no auth)
  - `GET /data/1981d726120d/{ticker}` — 93-field per-stock detail: fundamentals + 3 shareholding periods + 5 recent news + MA/EMA signals
  - `GET /Info/DSE` — DSEX/DS30/DSES indices + market status + advance/decline
  - `GET /info/Stocks` — 494 instruments (Code, Name, Group/sector)
  - `GET /qoutes/3ace8d562de8/{ticker}` — ~700 records back to 2018, MaxPrice/MinPrice/Volume (no Open/Close)
  - `GET /MarketPrice/328338530b39/{ticker}` — 5-level bid/ask depth
  - `POST /data/download/CSV {QuotesType, date}` — full-market daily OHLCV snapshot (no auth needed)
- [x] `extraction/adapters/amarstock/live_prices.py` — `AmarStockLivePricesAdapter` (rewritten to use `/LatestPrice/` endpoint)
- [x] `extraction/adapters/amarstock/csv_historical.py` — `AmarStockCSVAdapter` (rewritten to use `/qoutes/` endpoint; MaxPrice=high, MinPrice=low; no Open/Close)
- [x] `extraction/adapters/amarstock/fundamentals_scraper.py` — `AmarStockFundamentalsAdapter` (rewritten: JSON API replaces HTML scrape)
- [x] Fixture files saved: `tests/fixtures/amarstock_*.pkl` — all endpoints
- [x] Unit tests using saved fixtures (all 3 adapters) — 37 tests, all passing

### Phase 1E — DSE Direct + Playwright Adapters (complete 2026-05-21)

- [x] Test dsebd.org pages with requests — all old PHP URLs 404; found correct URLs
- [s] `extraction/adapters/dse_direct/playwright_base.py` — no base class needed; Playwright inline per adapter
- [x] `extraction/adapters/dse_direct/live_prices.py` — `DSEDirectLivePricesAdapter` (httpx + BS4; 396 stocks; quality=partial)
- [x] `extraction/adapters/dse_direct/announcements.py` — `DSEDirectAnnouncementsAdapter` + `DSEDirectPSNAdapter` (Playwright; key-value row parser; 106/548 rows; published_at correct)
- [x] `extraction/adapters/dse_direct/depth.py` — `DSEDirectDepthPlaywrightAdapter` (price stats only; no auth → no bid/ask; quality=partial)
- [x] `extraction/adapters/dse_direct/pdf_reports.py` — `DSEDirectPDFAdapter` STUB — DSE + BSEC don't host company annual report PDFs; per-company IR pages only; no viable generic scraper
- [s] Test PDF discovery for 3 tickers — N/A; confirmed no centralized PDF source exists
- [s] Structure hash baseline — deferred to Phase 1L
- [x] `tests/smoke/test_dse_direct_smoke.py` — 9/9 pass

### Phase 1E+ — BsecPDFAdapter

- [-] `BsecPDFAdapter` — **CANCELLED**: BSEC (sec.gov.bd) has no queryable company PDF portal. Annual reports are on per-company IR pages only. `annual_reports_pdf` stream remains stubbed.

### Phase 1F — News Adapters

- [ ] `extraction/adapters/news/tbs_rss.py` — `TBSNewsRSSAdapter` (RSS feed parse)
- [ ] `extraction/adapters/news/financial_express.py` — RSS + BS4 fallback
- [ ] `extraction/adapters/news/daily_star.py` — RSS
- [ ] `extraction/adapters/news/dhaka_tribune.py` — RSS
- [ ] `extraction/adapters/news/prothomalo_playwright.py` — Playwright (no RSS)
- [ ] `extraction/adapters/news/ticker_extractor.py` — haiku NER: article text → list of DSE tickers
- [ ] Test ticker extraction on 10 real articles — measure accuracy

### Phase 1G — Macro Adapters (COMPLETE 2026-05-21)

- [x] Test Bangladesh Bank HTML pages — confirm table structure for each indicator
- [x] `extraction/adapters/macro/bangladesh_bank.py` — policy rate + CPI + FX + remittance scrapers
- [x] `extraction/adapters/macro/worldbank.py` — `WorldBankAdapter(indicator=...)` — all 5 macro indicators
- [x] BSEC: `extraction/adapters/bsec/ipo_scraper.py` — IPO filings (154 records: 137 fixed 2008-present, 17 bookbuilding 2022-present; httpx+BS4, no Playwright needed; `db/migrations/009_ipo_filings.sql` applied 2026-05-21)

### Phase 1H — DataStream Wiring + Failover

- [x] Wire all 16 streams in `extraction/registry.py` with real adapter instances
- [x] Failover integration tests — patch primary adapter to fail, verify secondary takes over (4/4 tests passing)
- [x] Schema consistency test — all adapters per stream return identical columns (5/5 streams compliant; baseline validation passed)
- [x] `extraction/scheduler.py` — all 7 APScheduler jobs scaffolded (market hours, EOD, announcements, macro, weekly, monthly, quarterly)
- [x] `extraction/tasks.py` — all 6 Celery task stubs (scraper/nlp/ml queues)
- [x] `extraction/jobs.py` — `job_run()` context manager (logs to pipeline_jobs table)

### Phase 1I — Bulk Historical Load (one-time)

- [x] `db/migrations/008_stock_prices_unique.sql` — unique index (time, ticker) for idempotent inserts
- [x] `extraction/bulk_load/seed_companies.py` — seed companies table from AmarStock live prices (426+ tickers, single API call)
- [x] `extraction/bulk_load/historical_loader.py` — async bulk loader: semaphore concurrency + 10k-row batch insert; close=mid(high,low) with quality_flag='no_ohlc' (AmarStock has no open/close); ON CONFLICT DO NOTHING
- [x] `extraction/bulk_load/gap_report.py` — row counts per ticker vs expected DSE trading days (Sun–Thu); flags empty/major/minor gaps
- [x] `extraction/bulk_load/run.py` — CLI: `python -m extraction.bulk_load.run [seed|load|report|all]`
- [x] **RUN seed**: 426 companies upserted into companies table (2026-05-21)
- [x] **RUN load**: 400/426 tickers loaded, 52,634 rows inserted (2026-05-21). 26 failed = bonds/delisted (empty AmarStock response: ABBLPBOND, ACHIASF, AMPL, AOPLC, APEXWEAV, BDPAINTS, BENGALBISC, CRAFTSMAN, DBLPBOND, HIMADRI, KBSEED, KFL, MAMUNAGRO, MASTERAGRO, MBPLCPBOND, MKFOOTWEAR, MOSTFAMETL, NIALCO, ORYZAAGRO, SADHESIVE, SEB1PBOND, UCB2PBOND, USMANIAGL, WEBCOATS, WONDERTOYS, YUSUFLOUR)
- [x] **RUN report**: all 400 loaded tickers in major_gap (<50%) — expected: AmarStock /qoutes/ only stores trade-days (not every DSE day); illiquid stocks have 1–20 rows, liquid stocks ~700 rows. Quality known.
- [x] `extraction/bulk_load/bdshare_fallback.py` — BDShare fallback for 6 bond tickers (ABBLPBOND, DBLPBOND, MBPLCPBOND, SEB1PBOND, UCB2PBOND, USMANIAGL): 475 rows each = 2,850 rows inserted, quality_flag='ok' (full OHLCV). 20 confirmed-dead tickers marked is_active=false.
- [x] **Phase 1I COMPLETE**: 406 active companies, 20 inactive. Total stock_prices rows: ~55,484 (52,634 AmarStock no_ohlc + 2,850 BDShare ok). Remaining gap: AmarStock data is trade-day-sparse; BDShare full backfill (2012–present all tickers) deferred to pre-ML phase.

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

- [~] Re-run partial bdshare smoke during market hours: sector_performance, top_gainers_losers, corporate_announcements, price_sensitive_news, market_depth
- [-] **bdshare bug**: `get_company_info()` fails — `pd.read_html(r.content)` raises `OSError` with lxml. Need to patch bdshare or implement own scraper for DSE company info page
- [ ] Confirm: AmarStock rate limit — need to test before committing to 2s delay
- [ ] Decision: uv vs pip-tools vs poetry for dep management
- [ ] Decision: asyncpg vs psycopg3 for async PostgreSQL
- [ ] Decision: Playwright runs in Docker or on host for dev?
- [ ] Decision: MinIO or S3 for local dev PDF storage?
