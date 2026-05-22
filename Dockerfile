# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

WORKDIR /app

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dep installs
RUN pip install --no-cache-dir uv

COPY pyproject.toml .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system .

# ---------------------------------------------------------------------------
FROM base AS worker
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system playwright
RUN playwright install chromium --with-deps
COPY . .

# ---------------------------------------------------------------------------
FROM base AS scheduler
COPY . .

# ---------------------------------------------------------------------------
FROM base AS mgmt
COPY . .
