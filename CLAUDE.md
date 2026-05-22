# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (includes playwright chromium)
make dev-install

# Services
make up            # start Docker stack
make down          # stop
make logs          # tail all; make logs s=worker for one service
make db-shell      # psql into timescaledb
make redis-cli

# Database
make migrate       # apply SQL migrations (docker)
make migrate-local # apply against local DB
make seed-companies

# Tests
make test                     # unit tests only
make test-integration         # requires running db + redis
make test-all
make test-cov                 # HTML coverage report
make smoke-test               # live bdshare (Sun–Thu 10am–2:30pm BD time only)

# Single test
pytest tests/unit/test_amarstock_adapters.py::test_name -v
pytest -k "test_name" tests/unit/

# Code quality
make lint          # ruff check
make fmt           # ruff format
make typecheck     # mypy strict
make check         # lint + typecheck

# Frontend (mgmt-ui/)
npm run dev        # port 3001
npm run build
npm run lint
```

## Architecture

### Data Flow

```
Sources (10+) → Adapter Chains → DataStream → Quality Checks → TimescaleDB/Redis
                                                    ↓
                                          APScheduler (8 jobs)
                                                    ↓
                                        FastAPI mgmt API (:8001)
                                                    ↓
                                          Next.js UI (:3001)
                                                    ↓
                                        LangChain Agent (gamani/openrouter/olama)
```

### Adapter Pattern (`extraction/`)

Every data source implements `BaseAdapter` (`extraction/base.py`) with `fetch()`, `normalize()`, and `health_check()`. Adapters are grouped into **16 `DataStream`s** defined in `extraction/registry.py`, each with a priority-ordered chain — if adapter priority=1 fails, priority=2 runs automatically. `AllAdaptersFailedError` is raised only if all fail.

The six adapter families under `extraction/adapters/`:
- **amarstock/**: REST JSON API (no auth, static hashes — see memory for URLs)
- **bdshare/**: wrapper around the `bdshare` Python library (fragile; see `docs/bdshare-issues.md`)
- **dse_direct/**: Playwright-based scrape of dse.org (headless chromium)
- **bsec/**: BSEC IPO filing scraper
- **macro/**: Bangladesh Bank + World Bank REST APIs
- **news/**: RSS + scrape for 5 BD news outlets

### Scheduling & Tasks

- `extraction/scheduler.py`: APScheduler entry point; 8 cron/interval jobs stored in PostgreSQL. Jobs call stream fetch + write to DB.
- `extraction/tasks.py`: Celery task stubs (not yet fully implemented — decorators and queue routing defined, implementations are TODOs).
- Three Redis DBs: 0=app cache, 1=celery broker, 2=celery results.

### Management API (`mgmt/`)

FastAPI app at port 8001. `mgmt/main.py` lifespan initializes DB pool, APScheduler, and OpsAgent. Seven routers: streams, jobs, scheduler, quality, health, alerts, agent.

`mgmt/config.py` is the single source of truth for all env vars (Pydantic `BaseSettings`).

### OpsAgent (`mgmt/agent/`)

LangChain-based agent in `ops_agent.py`. Provider-switchable at runtime via `agent_provider` env var — supports OpenRouter (default), Anthropic, Ollama, Google. LLM init is in `llm.py`. Tools include pipeline status, read-only DB query, trigger job, pause/promote adapter, fire alert. Most tool implementations are stubs with TODOs.

### Database (`db/`)

PostgreSQL + TimescaleDB + pgvector. Migrations in `db/migrations/` are plain SQL files run in order by `db/migrate.py` (idempotent, tracks applied in `_migrations` table). Key tables: `companies`, `stock_prices` (TimescaleDB hypertable), `fundamentals`, `news` (pgvector embeddings), `macro_indicators`, plus pipeline tables (`pipeline_jobs`, `alerts`, `source_health`, `adapter_overrides`).

### Quality & Observability

- `extraction/quality.py`: rules run after every fetch — checks required columns, empty results, negative prices, price spikes beyond threshold. Writes `QualityFailure` records to DB.
- `extraction/health.py`: pings source URLs, hashes HTML structure to detect silent breakage.
- `extraction/observability.py`: runs health checks, fires alerts (SMTP/Twilio stubs).

### Testing Strategy

Smoke tests in `tests/smoke/` make live API calls and save results as pickles to `tests/fixtures/`. Unit tests in `tests/unit/` load those pickles — so unit tests are fully offline. Run smoke tests first when adding a new adapter to generate fixtures.

### Frontend (`mgmt-ui/`)

Next.js 16 App Router. Four pages: dashboard, streams, alerts, agent. Calls backend at `http://localhost:8001`. No state management library — uses React 19 hooks + fetch.

## Key Configuration

| Env Var | Purpose |
|---|---|
| `DATABASE_URL` | asyncpg URL (app) |
| `DATABASE_SYNC_URL` | psycopg URL (migrations) |
| `CELERY_BROKER_URL` | Redis DB 1 |
| `agent_provider` | `openrouter` \| `anthropic` \| `ollama` \| `google` |
| `agent_model` | LLM model ID for ops agent |
| `SCHEDULER_TIMEZONE` | `Asia/Dhaka` |
| `DSE_PLAYWRIGHT_HEADLESS` | `true` in Docker, `false` for local debugging |

Copy `.env.example` → `.env` before first run.

## Current State

Celery task bodies, most OpsAgent tools, news adapters (Phase 1F), and ML models (Phase 1E) are stubbed. The extraction layer, scheduler, management API, and Docker stack are functional. See `TODOS.md` for task tracking and `ARCHITECTURE.md` for full system design.
