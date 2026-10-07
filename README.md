# DSE Stock Intelligence Platform

Data extraction, ML scoring, and AI analysis for the Dhaka Stock Exchange (DSE).

Pulls prices, fundamentals, corporate actions, IPO filings, macro indicators, and news from 10+ sources into TimescaleDB, scores stocks with ML models, and serves it through a REST API, an ops dashboard, and an LLM chat agent.

## Stack

- **Python 3.12** — FastAPI, APScheduler, Celery, Playwright, pandas
- **PostgreSQL + TimescaleDB + pgvector**, pgBouncer, Redis
- **Next.js** — `frontend/` (user app), `mgmt-ui/` (ops dashboard)
- **LangChain** agents — OpenRouter / Anthropic / Vertex / Google / Ollama

## Layout

| Path | What |
|---|---|
| `extraction/` | Source adapters, fallback chains, scheduler, quality checks |
| `db/` | SQL migrations, seeds, connection pools |
| `ml/` | Feature engineering, training, scoring, valuation |
| `api/` | Public REST API (:8000) |
| `chat/` | Chat agent, RAG, sentiment |
| `mgmt/` | Management API + OpsAgent (:8001) |
| `frontend/` | User-facing Next.js app |
| `mgmt-ui/` | Ops dashboard (:3001) |
| `monitoring/` | Prometheus / Grafana config |
| `tests/` | Unit, integration, smoke tests |

## Quick start

```bash
cp .env.example .env          # fill in DB password + LLM keys
make dev-install              # Python deps + Playwright chromium
make up                       # db, redis, pgbouncer, worker, scheduler, APIs
make migrate
make seed-companies
```

Frontends:

```bash
cd mgmt-ui && npm install && npm run dev    # http://localhost:3001
cd frontend && npm install && npm run dev
```

## Common commands

```bash
make logs s=worker     # tail one service
make db-shell          # psql
make test              # unit tests (offline, use pickled fixtures)
make test-integration  # needs db + redis running
make smoke-test        # live sources; DSE market hours only (Sun–Thu 10:00–14:30 BDT)
make check             # ruff + mypy
make help              # everything else
```

## Docs

- [ARCHITECTURE.md](ARCHITECTURE.md) — full system design
- [DATA_EXTRACTION_PRD.md](DATA_EXTRACTION_PRD.md) — extraction requirements
- [TODOS.md](TODOS.md) — task tracking
- [docs/](docs/) — source-specific notes (e.g. `bdshare-issues.md`)

## Notes

- Never commit `.env` or service-account JSON files.
- DSE moved off `dsebd.org`; scrapers use `old.dsebd.org` and the `www.dse.com.bd` JSON API.
