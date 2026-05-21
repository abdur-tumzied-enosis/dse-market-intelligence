FROM python:3.12-slim AS base

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dep installs
RUN pip install --no-cache-dir uv

COPY pyproject.toml .
RUN uv pip install --system --no-cache .

# ---------------------------------------------------------------------------
FROM base AS worker
RUN uv pip install --system --no-cache playwright
RUN playwright install chromium --with-deps
COPY . .

# ---------------------------------------------------------------------------
FROM base AS scheduler
COPY . .

# ---------------------------------------------------------------------------
FROM base AS mgmt
COPY . .
