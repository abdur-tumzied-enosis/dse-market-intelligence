.PHONY: help up down restart logs migrate test lint fmt typecheck clean install dev-install

COMPOSE = docker compose
PYTHON = python

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------
up: ## Start all extraction services (db, redis, worker, scheduler, mgmt_api)
	$(COMPOSE) up -d

down: ## Stop all services
	$(COMPOSE) down

restart: ## Restart all services
	$(COMPOSE) restart

logs: ## Tail logs (all services). Use: make logs s=worker
	$(COMPOSE) logs -f $(s)

ps: ## Show running containers
	$(COMPOSE) ps

db-shell: ## Open psql shell in db container
	$(COMPOSE) exec db psql -U $${POSTGRES_USER:-dse} -d $${POSTGRES_DB:-dse_intelligence}

redis-cli: ## Open redis-cli in redis container
	$(COMPOSE) exec redis redis-cli

worker-shell: ## Open shell in worker container
	$(COMPOSE) exec worker bash

# ---------------------------------------------------------------------------
# Database migrations
# ---------------------------------------------------------------------------
migrate: ## Run all migrations (idempotent)
	$(COMPOSE) exec db psql -U $${POSTGRES_USER:-dse} -d $${POSTGRES_DB:-dse_intelligence} \
		-c "SELECT 1" > /dev/null 2>&1 || (echo "DB not ready" && exit 1)
	$(PYTHON) db/migrate.py

migrate-local: ## Run migrations against local DB (no Docker)
	$(PYTHON) db/migrate.py

# ---------------------------------------------------------------------------
# Development
# ---------------------------------------------------------------------------
install: ## Install production deps via uv
	uv pip install .

dev-install: ## Install all deps including dev extras
	uv pip install -e ".[dev]"
	playwright install chromium

smoke-test: ## Run bdshare smoke tests (run during DSE market hours: Sun-Thu 10am-2:30pm BD)
	$(PYTHON) -m pytest tests/smoke/ -v -s --no-cov

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
test: ## Run unit tests
	$(PYTHON) -m pytest tests/unit/ -v

test-integration: ## Run integration tests (requires running DB + Redis)
	$(PYTHON) -m pytest tests/integration/ -v

test-all: ## Run all tests
	$(PYTHON) -m pytest tests/ -v

test-cov: ## Run tests with coverage report
	$(PYTHON) -m pytest tests/unit/ --cov=extraction --cov-report=html
	@echo "Coverage report: htmlcov/index.html"

# ---------------------------------------------------------------------------
# Code quality
# ---------------------------------------------------------------------------
lint: ## Lint with ruff
	ruff check extraction/ db/ mgmt/ tests/

fmt: ## Format with ruff
	ruff format extraction/ db/ mgmt/ tests/

typecheck: ## Type check with mypy
	mypy extraction/ db/ mgmt/

check: lint typecheck ## Run lint + typecheck

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
clean: ## Remove __pycache__, .pytest_cache, build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name htmlcov -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true

clean-volumes: ## WARNING: destroy Docker volumes (wipes DB data)
	$(COMPOSE) down -v

# ---------------------------------------------------------------------------
# Bulk data load (one-time)
# ---------------------------------------------------------------------------
seed-companies: ## Seed companies table from DSE company list
	$(PYTHON) -m db.seeds.companies

load-historical: ## Load data/amarstock_csv/ into stock_prices (~1.5M rows, idempotent)
	$(PYTHON) scripts/load_amarstock_historical.py

bulk-historical: ## Bulk download AmarStock CSVs (2012-present). Slow — run once.
	$(PYTHON) -m extraction.bulk_load.amarstock_historical

# ---------------------------------------------------------------------------
# Pipeline verification (test mode)
# ---------------------------------------------------------------------------
verify-pipeline: ## Run pipeline in test mode for 1h then report. Set PIPELINE_TEST_MODE=true in .env first.
	@echo "Starting pipeline in test mode. Run 'make pipeline-report' after ~1h."
	$(COMPOSE) up -d
	@echo "Stack is up. Scheduler running with compressed intervals."
	@echo "Monitor with: make logs s=scheduler"

pipeline-report: ## Print pass/fail report for the last hour of pipeline runs.
	$(PYTHON) pipeline_report.py --hours 1

pipeline-report-week: ## Print pass/fail report for the last 7 days.
	$(PYTHON) pipeline_report.py --hours 168
