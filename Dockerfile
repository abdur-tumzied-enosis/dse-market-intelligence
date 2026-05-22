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

# 1. Define the permanent location for browsers in the image
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright

# 2. Mount apt caches for system dependencies
# 3. Mount a temporary custom cache directory for the browser binaries
# 4. Download into the cache, install deps, then copy to the permanent location
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    --mount=type=cache,target=/tmp/pw-cache \
    PLAYWRIGHT_BROWSERS_PATH=/tmp/pw-cache playwright install chromium --with-deps && \
    mkdir -p /opt/playwright && \
    cp -a /tmp/pw-cache/. /opt/playwright/

COPY . .

# ---------------------------------------------------------------------------
FROM base AS scheduler
COPY . .

# ---------------------------------------------------------------------------
FROM base AS mgmt
COPY . .
