# DSE Stock Intelligence Platform — Build Tracker

> Status legend: `[ ]` todo · `[~]` in progress · `[x]` done · `[-]` blocked · `[s]` skipped

---

## CURRENT FOCUS → Layer 4 complete. Next: Layer 5 (Backend API — FastAPI, JWT auth, all /api/stocks and /api/market endpoints).
--- pe_vs_sector work on it with proper data

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

### Phase 1D — Company Announcements + AmarStock Adapters

#### Company Announcements Pipeline (2026-05-24)

- [x] `extraction/adapters/dse_direct/announcements.py` — `DSEDirectCompanyNewsAdapter` (httpx static HTML; `old_news.php?inst={ticker}&criteria=3`; no Playwright needed; browser-like headers required)
- [x] `db/migrations/016_company_announcements.sql` — `company_announcements` table: structured fields (announcement_type, eps_value, eps_period, eps_type, dividend_cash_pct, dividend_stock_pct, dividend_year, content_hash); 4 indexes
- [x] `extraction/parsers/__init__.py` + `extraction/parsers/announcement_parser.py` — classifies 10 announcement types; extracts EPS (value + period + type) and dividend (cash % + stock % + year) from text; EPS extracted across all types (not just eps_disclosure — DSE embeds EPS in dividend continuation news)
- [x] `extraction/bulk_load/announcement_loader.py` — bulk scraper: semaphore(3) + 1.5s delay; ~12 min for 406 tickers; ON CONFLICT content_hash DO NOTHING
- [x] `extraction/bulk_load/run.py` — added `announcements` subcommand
- [x] Migration applied; tested on BRACBANK/CITYBANK/GP (162 rows, EPS + stock dividend extracted correctly)
- [x] Run full bulk load: 406 tickers → 399 ok / 7 failed (no DSE history: AFCAGRO, ACTIVEFINE, BXSYNTH, KAY&QUE, REGENTTEX, SAVAREFR, SHURWID) / **20,868 rows inserted** (2026-05-24)
- [ ] Unit tests: parser on fixture announcements (EPS, dividend, AGM, board_meeting, other)

#### Historical Fundamentals Load (2026-05-24)

- [x] `db/migrations/017_fundamentals_fiscal_year_index.sql` — partial unique index on `(ticker, fiscal_year) WHERE fiscal_year IS NOT NULL`; NULL rows (weekly scrapes) unaffected
- [x] `extraction/bulk_load/fundamentals_historical_loader.py` — scrapes `displayCompany.php` for all active tickers; 5–8 years EPS/NAV/PE/cash_div/stock_div; ON CONFLICT (ticker, fiscal_year) DO UPDATE; 3 concurrent, 1.5s delay
- [x] Run full bulk load: 406 tickers → 395 ok / 11 failed (bonds/sukuk have no EPS tables — expected) / **1,945 rows upserted** (2026-05-24)

#### Fundamentals Enrichment (2026-06-12) — spec: docs/superpowers/specs/2026-06-12-fundamentals-enrichment-{prd,design}.md

- [x] `db/migrations/036_fundamentals_enrichment.sql` — shareholding_history, corporate_actions, fundamentals_quarterly + fundamentals/companies column adds
- [x] Header-grid table parsing (`_expand_header_grid`) replaces hardcoded column indices; D1 (net profit/TCI captured), D2 (dividend-only years kept — CITYBANK 5→22 years), D3 (diluted-EPS fallthrough) fixed
- [x] `fetch_company_bundle()` — one request → yearly/quarterly/shareholding/actions/company-meta; loader writes all five with idempotent upserts
- [x] Weekly job per-table metrics; bundle-aware health check; quality rules (shareholding sum, eps/nav bounds)
- [x] Track-record features: profit CAGR 3y/5y, dividend streak, cash-div ratio, rights count, institutional/foreign flow
- [ ] Run full enrichment bulk load across all 406 tickers (only CITYBANK/GP/SQURPHARMA/FAMILYTEX loaded so far)
- [ ] P4 (separate effort): annual-report PDF extraction — `ir_url`/`psi_url` now stored per company

#### AmarStock Adapters (BACKUP / fundamentals PRIMARY)

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
- [x] `extraction/adapters/dse_direct/company_info.py` — `DSEDirectCompanyInfoAdapter` + `fetch_historical()` (multi-year EPS/NAV/PE/cash_div/stock_div; hardcoded PE table column indices; th/td dividend history strings parsed per-year)
- [x] `extraction/adapters/dse_direct/gainers_losers.py` — `DSEDirectGainersAdapter` + `DSEDirectLosersAdapter` (10 gainers, 10 losers; confirmed working 2026-05-24)
- [x] `extraction/adapters/dse_direct/sector_pe.py` — `DSEDirectSectorPEAdapter` (18 sectors; confirmed working 2026-05-24)
- [s] Test PDF discovery for 3 tickers — N/A; confirmed no centralized PDF source exists
- [s] Structure hash baseline — deferred to Phase 1L
- [x] `tests/smoke/test_dse_direct_smoke.py` — 9/9 pass

### Phase 1E+ — BsecPDFAdapter

- [-] `BsecPDFAdapter` — **CANCELLED**: BSEC (sec.gov.bd) has no queryable company PDF portal. Annual reports are on per-company IR pages only. `annual_reports_pdf` stream remains stubbed.

### Phase 1F — News Adapters (COMPLETE 2026-05-23)

Replaced 5 individual site scrapers with single Google News RSS adapter (covers all sources).
Replaced Haiku NER with Google Natural Language API + rapidfuzz fuzzy match.

- [x] `extraction/adapters/news/google_news_rss.py` — `GoogleNewsRSSAdapter` (Google News RSS, 2 BD finance queries; covers TBS/FE/Daily Star/Dhaka Tribune/Reuters BD automatically)
- [x] `extraction/adapters/news/ticker_extractor.py` — `TickerExtractor` (Google NL API analyzeEntities → ORG filter → rapidfuzz WRatio match against companies table; free tier: 5k req/month)
- [x] `extraction/registry.py` — `news_en` stream wired with `GoogleNewsRSSAdapter`; `news_bn` wired with Bengali query
- [x] `mgmt/config.py` — `google_cloud_api_key` added
- [x] `pyproject.toml` — `rapidfuzz>=3.9.0` added
- [x] Wire `TickerExtractor` into news scheduler job — `job_news_scrape()` in `extraction/scheduler.py`; insert-first strategy: NER only on newly inserted rows; ON CONFLICT url DO NOTHING RETURNING id
- [x] `mgmt/config.py` — `news_scrape_interval_hours=12` (12h → ~3k NL API calls/month, within free tier)
- [x] Smoke test — 164 articles from 9 sources, content-hash dedup verified, blocklist preflight working (2026-05-24). NL API enabled (2026-05-24): GP + BRACBANK extracted correctly, BSEC/DSE blocklisted to context_orgs, sentiment +0.33 (positive) on earnings article, preflight skips macro-only articles. 8/8 smoke tests pass.

### Phase 1G — Macro Adapters (COMPLETE 2026-05-21)

- [x] Test Bangladesh Bank HTML pages — confirm table structure for each indicator
- [x] `extraction/adapters/macro/bangladesh_bank.py` — policy rate + CPI + FX + remittance scrapers
- [x] `extraction/adapters/macro/worldbank.py` — `WorldBankAdapter(indicator=...)` — all 5 macro indicators
- [x] BSEC: `extraction/adapters/bsec/ipo_scraper.py` — IPO filings (154 records: 137 fixed 2008-present, 17 bookbuilding 2022-present; httpx+BS4, no Playwright needed; `db/migrations/009_ipo_filings.sql` applied 2026-05-21)

### Phase 1H — DataStream Wiring + Failover

- [x] Wire all 16 streams in `extraction/registry.py` with real adapter instances
- [x] Failover integration tests — patch primary adapter to fail, verify secondary takes over (4/4 tests passing)
- [x] Schema consistency test — all adapters per stream return identical columns (5/5 streams compliant; baseline validation passed)
- [x] `extraction/scheduler.py` — all 9 APScheduler jobs scaffolded and wired: live_prices, eod_snapshot, announcements (→ bulk_load_announcements), news_scrape, daily_macro (→ macro_usd_bdt + policy_rate), weekly_fundamentals (→ bulk_load_fundamentals_historical), monthly (→ all 5 macro streams), quarterly, health_checks
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

### Phase 1J — Observability (COMPLETE 2026-05-21)

- [x] `source_health` table populated by 6-hourly health checks — `extraction/observability.py:run_health_checks()` + scheduler job
- [x] `pipeline_jobs` table — every run logged — `extraction/jobs.py` real asyncpg INSERT/UPDATE (was TODO stubs)
- [x] `pipeline_alerts` table — all alerts recorded — `extraction/observability.py:fire_alert()`
- [x] Grafana + Prometheus Docker services added to compose — `docker-compose.yml`; configs in `monitoring/`
- [x] Pipeline health dashboard (job status grid + freshness bars + error rate) — `monitoring/grafana/dashboards/pipeline_health.json`; PostgreSQL datasource provisioned
- [x] Alert routing: WhatsApp (CRITICAL) + email (WARNING) — `extraction/observability.py`; email via smtplib; WhatsApp via Twilio (optional, env-gated)

### Phase 1K — Management API + UI (COMPLETE 2026-05-21)

- [x] FastAPI app skeleton: `mgmt/main.py` — lifespan starts scheduler + ops agent
- [x] `/mgmt/streams` endpoints (GET all, GET one, adapter list)
- [x] `/mgmt/streams/{stream}/adapters/{adapter}/promote|demote|pause|resume` — persisted to `adapter_overrides` table (migration 010)
- [x] `/mgmt/jobs` endpoints (list, single, stats) — `/mgmt/scheduler` for APScheduler trigger/pause/resume
- [x] `/mgmt/quality/failures` + `/mgmt/quality/freshness`
- [x] `/mgmt/health` endpoints + `/mgmt/health/structure-hashes`
- [x] `/mgmt/alerts` endpoints (list, summary, acknowledge)
- [x] AI Ops Agent: `mgmt/agent/ops_agent.py` — sweep + alert_hook + chat_stream; tools: get_pipeline_status, query_db_readonly, trigger_job, pause_adapter, promote_adapter, fire_alert; auto-execute low-risk, queue medium/high for approval
- [x] `/mgmt/agent` endpoints (status, run, decisions, queue, approve/reject, chat SSE)
- [x] Next.js management UI in `mgmt-ui/`: dashboard (stream health grid + freshness bars + source health)
- [x] `/mgmt/streams/{id}` — adapter priority up/down arrows + pause/resume buttons
- [x] `/mgmt/agent` — decision log + approval queue cards + SSE chat console

### Phase 1L — Testing + Hardening

- [x] Per-adapter unit tests (normalize() on fixture data) — all done: amarstock/bdshare/dse_direct + macro + bsec + announcement parser (412 tests total)
- [x] Failover integration tests — 12 streams covered (live_prices, historical_ohlcv, fundamentals, announcements, psn, top_gainers_losers, market_depth, 5 macro streams)
- [x] Quality check unit tests — all rule sets covered (empty, missing_cols, price_spike, negative_price)
- [x] Health check tests — mock source down, verify alert fires
- [x] Load test: 350 tickers × bdshare fundamentals — measure rate limiting behavior
- [x] Run full pipeline for 1 week — verify no silent failures

---

## Layer 2 — Storage (ACTIVE)

> Approach A: pgBouncer → Redis TTL cache → TimescaleDB tuning. Deferred: MinIO, backup, pgvector tuning, llm_usage_log, Neo4j.

### Phase 2A — pgBouncer (connection multiplexing)

- [x] Add `pgbouncer` service to `docker-compose.yml` — `edoburu/pgbouncer:1.23.1-p0`, transaction mode, port 6432
- [s] `docker/pgbouncer/pgbouncer.ini` — not needed; edoburu image is fully env-var driven
- [s] `docker/pgbouncer/userlist.txt` — not needed; edoburu generates it from DB_USER/DB_PASSWORD env vars
- [x] Update all app services (`mgmt_api`, `worker`, `scheduler`) — `DATABASE_URL` → `pgbouncer:6432`; `DATABASE_SYNC_URL` stays `db:5432` direct (APScheduler SQLAlchemy needs it)
- [x] `.env.example` — `DATABASE_URL` → localhost:6432; `DATABASE_SYNC_URL` stays localhost:5432; added `PGBOUNCER_PORT=6432`
- [x] `db/pool.py` — `min_size=2`, `max_size=10`, `statement_cache_size=0` (required: pgBouncer transaction mode breaks asyncpg prepared statements)
- [x] Smoke: pgBouncer healthy, mgmt_api connects through pool. Fixed: `AUTH_TYPE=scram-sha-256` (timescaledb-ha pg16 ignores `POSTGRES_HOST_AUTH_METHOD`; defaults to scram)

### Phase 2B — Redis TTL Cache Layer (COMPLETE 2026-05-24)

- [x] `mgmt/cache.py` — `get_redis()` singleton (redis.asyncio), `cache_get(key)`, `cache_set(key, value, ttl)`, `cache_delete_pattern(pattern)` (SCAN-based, safe in prod)
- [x] `mgmt/config.py` — TTL constants: `cache_ttl_live_prices=300`, `cache_ttl_market_summary=900`, `cache_ttl_fundamentals=86400`, `cache_ttl_sector_pe=3600`, `cache_ttl_pipeline_status=30`
- [x] Key schema: `cache:pipeline_status:{stream}:{status}:{limit}:{offset}`, `cache:live_prices*`
- [x] `extraction/scheduler.py` — cache invalidation in `job_live_prices()` (deletes `cache:pipeline_status:*` + `cache:live_prices*` after each run; non-fatal on failure)
- [s] `mgmt/routers/streams.py` — streams router reads in-memory `STREAMS` registry, not DB; caching adds no value
- [x] `mgmt/routers/jobs.py` — cache-aside on `/mgmt/jobs` list; key encodes all query params; 30s TTL
- [x] `mgmt/main.py` — `get_redis()` warm on startup, `close_redis()` on shutdown
- [X] Smoke: hit `/mgmt/jobs` twice in < 30s, verify cache hit via `make redis-cli` → `MONITOR`

### Phase 2C — TimescaleDB Continuous Aggregate Tuning

- [x] `db/migrations/013_weekly_ohlcv.sql` — `weekly_ohlcv` hierarchical CA on `daily_ohlcv`; 7-day bucket; refresh weekly, 8-week lookback
- [x] `db/migrations/014_monthly_ohlcv.sql` — `monthly_ohlcv` hierarchical CA on `daily_ohlcv` (not weekly — avoids bucket-alignment drift at month boundaries); refresh monthly
- [x] `db/migrations/015_sector_daily_stats.sql` — `sector_daily_stats` regular matview (CA can't join non-hypertable tables); `refresh_sector_daily_stats` procedure scheduled via `add_job` daily; `REFRESH CONCURRENTLY` backed by unique index
- [x] Tune `daily_ohlcv` refresh policy: `schedule_interval` 1h → 1d (in 015; uses `alter_job` via dynamic job_id lookup)
- [x] Index audit in 015: `fundamentals(ticker, fetched_at DESC)`, `news(published_at DESC)`, `news USING gin(tickers)` — all confirmed/idempotent
- [x] `make migrate` — applied via `docker compose exec mgmt_api python db/migrate.py` (local .env uses Docker hostnames; run inside container)
- [x] EXPLAIN ANALYZE — all 3 patterns index-only, no seq scans. weekly_ohlcv ChunkAppend 0.47ms, sector_daily_stats Bitmap+GIN 0.48ms, news GIN 0.07ms. Fixed: `refresh_sector_daily_stats` now runs `ANALYZE` post-refresh (planner was overestimating 6938→278 before stats update)

### Deferred (post-Layer 2)

- [ ] pgvector index tuning (ivfflat lists parameter) — defer until embeddings volume known
- [ ] MinIO / S3 setup for PDF storage — defer until annual report PDF pipeline built
- [ ] DB backup strategy (pg_dump schedule) — pre-prod task - skip for now
- [ ] `db/migrations/011_llm_usage_log.sql` — `llm_usage_log` table — defer to Layer 4 LLM layer
- [ ] Neo4j service in docker-compose — defer until Layer 4 Graphiti integration

---

## Layer 3 — ML Prediction Engine (COMPLETE 2026-05-25)

> Data situation: 244k rows across 406 tickers. ~475 rows/ticker full OHLCV (2024–2026). LSTM v0 baseline — retrain quarterly or when AmarStock CSV recovers to get pre-2024 history.

### Goal: 4 outputs
> 1. **Price direction** — LSTM: P(price higher in 5/10/20 days), per ticker, nightly ✓
> 2. **Fundamental rank** — XGBoost: composite score from EPS growth/NAV/PE vs sector/dividend consistency ✓
> 3. **Intrinsic value** — DCF: fair value estimate using historical EPS + growth rate ✓
> 4. **Stock Health Score (0–100)** — composite of above 3; shown as gauge on every stock page ✓

### Phase 3A — Dependencies + DB (COMPLETE)

- [x] Add to `pyproject.toml`: `scikit-learn>=1.5`, `xgboost>=2.0`, `ta>=0.11`, `torch>=2.3`, `joblib>=1.4`
- [x] `db/migrations/018_ml_predictions.sql` — `ml_predictions` table
- [x] `db/migrations/019_stock_scores.sql` — `stock_scores` table
- [x] `db/migrations/020_prediction_outcomes.sql` — `prediction_outcomes` table
- [x] `models/` directory gitignored; `ml/` package added to wheel

### Phase 3B — Feature Engineering (COMPLETE)

- [x] `ml/features/price_features.py` — RSI, MACD, Bollinger, ATR, ADX, OBV, returns, volume_zscore
- [x] `ml/features/fundamental_features.py` — EPS growth 1yr/3yr CAGR, NAV growth, div yield, EPS consistency
- [x] `ml/features/macro_features.py` — USD/BDT change, policy rate delta, CPI trend
- [x] `ml/features/feature_store.py` — async DB fetch + feature assembly; ffill + zero-fill

### Phase 3C — XGBoost Fundamental Model (COMPLETE)

- [x] `ml/models/fundamental_scorer.py` — `FundamentalScorer` XGBoost wrapper with save/load
- [x] `ml/train/train_fundamental.py` — walk-forward CV, saves `models/v1/fundamental_scorer.pkl`
- [x] `ml/inference/score_fundamentals.py` — scores all tickers, writes to `stock_scores`

### Phase 3D — LSTM Price Direction Model (COMPLETE)

- [x] `ml/models/lstm_predictor.py` — 2-layer LSTM, hidden=64, 3 horizon output heads
- [x] `ml/train/train_lstm.py` — pooled sequences, early stopping, saves `models/v1/lstm_v0.pt`
- [x] `ml/inference/predict_prices.py` — writes 3 rows/ticker to `ml_predictions`
- [ ] Sector-specific variants — deferred until more price history available

### Phase 3E — DCF + Monte Carlo (COMPLETE)

- [x] `ml/valuation/dcf.py` — EPS-based Gordon Growth terminal value
- [x] `ml/valuation/monte_carlo.py` — N=10,000 scenarios, P10/P50/P90 range

### Phase 3F — Stock Health Score + Inference Pipeline (COMPLETE)

- [x] `ml/scoring/health_score.py` — 0.35×fundamental + 0.35×momentum + 0.20×valuation + 0.10×sentiment
- [x] `extraction/scheduler.py` — `job_nightly_ml()` at 22:00 BD + `job_quarterly_retrain()` quarterly
- [x] `mgmt/config.py` — `test_nightly_ml_minutes: int = 15`

### Phase 3G — Accuracy Monitoring (COMPLETE)

- [x] `db/migrations/020_prediction_outcomes.sql` — tracks actual vs predicted per ticker/horizon
- [x] `ml/monitoring/accuracy_report.py` — `populate_outcomes()`, `generate_report()`, `check_accuracy_thresholds()`
- [x] Quarterly retrain gated on WARNING (<48%) / CRITICAL (<45%) thresholds; 56 unit tests, all passing

---

## Layer 4 — LLM Agent Layer (COMPLETE 2026-05-25)

> **Framework:** LangChain + manual loop (mirrors OpsAgent). Default: Gemini 2.5 Flash (google provider). Provider switchable via `chat_agent_provider` env var. Embeddings: Google text-embedding-004 (768 dims).

### Phase 4A — Core Agent + Tools (COMPLETE)

- [x] `chat/__init__.py`, `chat/prompt.py` — bilingual system prompt (EN + BN) with live market context
- [x] `chat/tools.py` — `build_tools(pool)` factory with 8 read-only @tool functions
- [x] `chat/rag.py` — embed query → pgvector cosine search → build_rag_context()
- [x] `chat/usage.py` — `log_llm_usage()`, `estimate_cost()`, writes to `llm_usage_log`
- [x] `chat/agent.py` — `StockAnalystAgent` with `chat_stream()` SSE generator; SELECT/COMPRESS context strategies
- [x] `chat/routing.py` — `is_complex_query()` + `make_routed_llm()` (Gemini thinking mode for complex queries)
- [x] `chat/cache.py` — `GeminiContextCache` optional 30-min system prompt caching (Gemini CachedContent API)
- [x] `chat/sentiment.py` — `score_article()` + `score_new_articles()` via Gemini Flash
- [x] `chat/pdf_extractor.py` — `PdfExtractor` + `chunk_text()` via Gemini Files API

### Phase 4B — API Endpoint + Scheduler (COMPLETE)

- [x] `db/migrations/021_llm_usage_log.sql` — llm_usage_log table with cost tracking
- [x] `db/migrations/022_embeddings_dimension.sql` — alter document_chunks to vector(768) for text-embedding-004
- [x] `mgmt/routers/chat.py` — `POST /api/chat` SSE endpoint with Redis quota middleware
- [x] `mgmt/deps.py` — `get_chat_agent()` dependency
- [x] `mgmt/main.py` — StockAnalystAgent init in lifespan, chat router registered
- [x] `mgmt/config.py` — chat agent config (provider, model, tier, cache TTL)
- [x] `extraction/scheduler.py` — `job_news_sentiment` daily cron at 03:30 BD time

### Phase 4C — Testing (COMPLETE)

- [x] 77 unit tests across 11 test files — all passing
- [x] Bengali language test suite (15 tests)
- [x] Integration tests verifying full chain (7 tests)

### Deferred

- [ ] Graphiti + Neo4j knowledge graph — defer until pgvector RAG proven in prod
- [ ] Annual report bulk PDF ingestion — limited data (no centralized DSE/BSEC PDF portal)

---

## Layer 5 — Backend API (ACTIVE)

> Spec: `docs/superpowers/specs/2026-05-26-consumer-frontend-design.md`
> Stack: FastAPI (:8000), JWT auth, Redis rate-limiting, tier-gated endpoints

### Phase G — Access Control (prerequisite for all phases)

- [ ] `db/migrations/025_access_control.sql` — tier_limits + feature_flags + user_access_overrides + seed data
- [ ] `mgmt/routers/access.py` — 7 admin endpoints (tier limits CRUD, feature flags toggle, user overrides CRUD)
- [ ] `api/access.py` — `check_feature(user, flag)` + `get_limit(tier, key)` with Redis cache (TTL 60s)
- [ ] Cache invalidation on every mgmt write (delete affected Redis keys)
- [ ] mgmt-ui `/access` page — Tab 1: Tier Limits (inline editable table)
- [ ] mgmt-ui `/access` page — Tab 2: Feature Flags (toggle switches per tier)
- [ ] mgmt-ui `/access` page — Tab 3: User Overrides (email search + grant/revoke + expiry + notes)

### Phase A — Foundation

- [ ] `db/migrations/023_users.sql` — users table (id, email, password_hash, tier, is_verified)
- [ ] `api/` FastAPI skeleton: main.py, CORS, JWT middleware, deps.py
- [ ] Auth endpoints: register, verify-email, login, refresh, logout
- [ ] `frontend/` Next.js 16.2.6 init: Tailwind v4, shadcn dark theme, TypeScript
- [ ] Sidebar + TopBar layout shell (authenticated route group)
- [ ] `/login` and `/register` pages — connected to real API
- [ ] `middleware.ts` — unauthenticated → redirect to `/login`

### Phase B — Market Dashboard

- [ ] `GET /api/market/summary`, `/movers`, `/heatmap` endpoints (Redis cached)
- [ ] `/dashboard` page: IndexCards + MoverStrip + HeatmapGrid (SSR)
- [ ] TopBar live DSEX strip via SSE
- [ ] Landing page `/` — hero, features, pricing table, disclaimer footer

### Phase C — Stock Pages

- [ ] `GET /api/stocks`, `/api/stocks/{ticker}` (tier-gated via `check_feature`)
- [ ] `GET /api/stocks/{ticker}/fundamentals?years=N` (free: max 3, pro: max 10)
- [ ] `/stocks` screener: DataTable + sector/PE/rating/health_score filters
- [ ] `/stocks/[ticker]`: PriceChart (TradingView) + HealthGauge + RatingBadge
- [ ] FundamentalsChart (Recharts) — 3yr free / 10yr pro
- [ ] PaywallOverlay on pro-only sections → links to `/settings#upgrade`
- [ ] `/sectors` page: sector PE table + heatmap

### Phase D — AI Chat

- [ ] `POST /api/chat` SSE endpoint (wires to existing `chat/` Layer 4 agent)
- [ ] `GET /api/chat/quota` endpoint
- [ ] Chat quota enforced via `get_limit()` + Redis counter per user per day
- [ ] `/chat` page: ChatWindow + ToolCallIndicator + QuickPrompts
- [ ] QuotaChip in Sidebar — hard 429 block + reset time shown

### Phase E — Predictions + Portfolio

- [ ] `db/migrations/024_portfolio.sql` — portfolio_holdings (user_id, ticker, quantity, avg_cost)
- [ ] `GET /api/stocks/{ticker}/predictions/{horizon}` (pro only via `check_feature`)
- [ ] `/predict/[ticker]`: PredictionFanChart + Monte Carlo bands
- [ ] Portfolio CRUD endpoints + `/api/portfolio/analysis` (pro only)
- [ ] `/portfolio` page: holdings table, P&L, sector pie chart

### Phase F — Reports + Polish

- [ ] `POST /api/reports/generate` + download (pro only, WeasyPrint)
- [ ] `/reports` page
- [ ] `/settings` page: profile + tier info + upgrade CTA
- [ ] Mobile responsive pass (all pages)
- [ ] Error boundaries, loading skeletons, empty states
- [ ] API integration tests (auth flow + tier gating + feature flags)

---

## Layer 6 — Frontend (ACTIVE)

> Part of same spec as Layer 5. See phases A–F above.
> Stack: Next.js 16.2.6, Tailwind v4, shadcn/ui dark, TanStack Query v5
> Design tokens + component tree in spec doc.

- [ ] All pages built (dashboard, stocks, predict, sectors, chat, portfolio, reports, settings)
- [ ] TradingView Lightweight Charts v5 — PriceChart + PredictionFanChart
- [ ] Recharts v2 — FundamentalsChart (EPS/revenue/PE/dividends)
- [ ] HealthGauge SVG component (0–100, color-coded)
- [ ] Dark terminal theme applied globally (#0a0a0f base)
- [ ] Mobile responsive (all pages)
- [ ] Disclaimer banner on every authenticated page

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
