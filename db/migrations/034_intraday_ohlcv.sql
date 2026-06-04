-- Intraday OHLCV — 5/10/30/60-minute continuous aggregates over intraday_prices,
-- plus *_final views that convert cumulative volume/value/trades into per-bar
-- deltas.
--
-- TWO-LAYER DESIGN (continuous aggregates cannot contain window functions):
--   1. CA layer  → FIRST/MAX/MIN/LAST for OHLC; LAST(cum_*) keeps the cumulative
--                  reading at the END of each bucket.
--   2. *_final view → per-bar volume = cum_at_bucket_end − cum_at_prev_bucket_end,
--                  via LAG() partitioned per ticker per trading day.
--
-- HIERARCHY (matches daily→weekly→monthly, migrations 013/014): 5m is built from
-- raw; 10m←5m (×2), 30m←10m (×3), 60m←30m (×2). All intervals divide evenly so
-- bucket edges align with no drift. LAST(cum_*) of LAST(cum_*) is still the last
-- cumulative in the wider bucket — correct at every level.

-- ── 5-minute (base, from raw snapshots) ────────────────────────────────
CREATE MATERIALIZED VIEW IF NOT EXISTS ohlcv_5m
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('5 minutes', time) AS bucket,
    ticker,
    FIRST(ltp, time)        AS open,
    MAX(ltp)                AS high,
    MIN(ltp)                AS low,
    LAST(ltp, time)         AS close,
    LAST(cum_volume, time)  AS cum_volume,
    LAST(cum_value, time)   AS cum_value,
    LAST(cum_trades, time)  AS cum_trades
FROM intraday_prices
GROUP BY bucket, ticker
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
    'ohlcv_5m',
    start_offset      => INTERVAL '1 day',
    end_offset        => INTERVAL '5 minutes',
    schedule_interval => INTERVAL '5 minutes',
    if_not_exists     => true
);

-- ── 10-minute (←5m) ────────────────────────────────────────────────────
CREATE MATERIALIZED VIEW IF NOT EXISTS ohlcv_10m
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('10 minutes', bucket) AS bucket,
    ticker,
    FIRST(open, bucket)       AS open,
    MAX(high)                 AS high,
    MIN(low)                  AS low,
    LAST(close, bucket)       AS close,
    LAST(cum_volume, bucket)  AS cum_volume,
    LAST(cum_value, bucket)   AS cum_value,
    LAST(cum_trades, bucket)  AS cum_trades
FROM ohlcv_5m
GROUP BY 1, ticker
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
    'ohlcv_10m',
    start_offset      => INTERVAL '1 day',
    end_offset        => INTERVAL '10 minutes',
    schedule_interval => INTERVAL '10 minutes',
    if_not_exists     => true
);

-- ── 30-minute (←10m) ───────────────────────────────────────────────────
CREATE MATERIALIZED VIEW IF NOT EXISTS ohlcv_30m
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('30 minutes', bucket) AS bucket,
    ticker,
    FIRST(open, bucket)       AS open,
    MAX(high)                 AS high,
    MIN(low)                  AS low,
    LAST(close, bucket)       AS close,
    LAST(cum_volume, bucket)  AS cum_volume,
    LAST(cum_value, bucket)   AS cum_value,
    LAST(cum_trades, bucket)  AS cum_trades
FROM ohlcv_10m
GROUP BY 1, ticker
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
    'ohlcv_30m',
    start_offset      => INTERVAL '2 days',
    end_offset        => INTERVAL '30 minutes',
    schedule_interval => INTERVAL '30 minutes',
    if_not_exists     => true
);

-- ── 60-minute (←30m) ───────────────────────────────────────────────────
CREATE MATERIALIZED VIEW IF NOT EXISTS ohlcv_60m
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('60 minutes', bucket) AS bucket,
    ticker,
    FIRST(open, bucket)       AS open,
    MAX(high)                 AS high,
    MIN(low)                  AS low,
    LAST(close, bucket)       AS close,
    LAST(cum_volume, bucket)  AS cum_volume,
    LAST(cum_value, bucket)   AS cum_value,
    LAST(cum_trades, bucket)  AS cum_trades
FROM ohlcv_30m
GROUP BY 1, ticker
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
    'ohlcv_60m',
    start_offset      => INTERVAL '2 days',
    end_offset        => INTERVAL '60 minutes',
    schedule_interval => INTERVAL '60 minutes',
    if_not_exists     => true
);

-- ── Delta views — cumulative → per-bar volume/value/trades ──────────────
-- volume[n] = cum[n] − cum[n-1] within the same ticker+trading-day. The first
-- bar of each day has no predecessor (LAG NULL) → COALESCE to its own cumulative
-- (= day-open through first-bucket close). Partition resets per UTC date; DSE
-- trades 04:00–08:30 UTC so a session never straddles a UTC midnight.
CREATE OR REPLACE VIEW ohlcv_5m_final AS
SELECT bucket, ticker, open, high, low, close,
    COALESCE(cum_volume - LAG(cum_volume) OVER w, cum_volume) AS volume,
    COALESCE(cum_value  - LAG(cum_value)  OVER w, cum_value)  AS value_bdt,
    COALESCE(cum_trades - LAG(cum_trades) OVER w, cum_trades) AS trades
FROM ohlcv_5m
WINDOW w AS (PARTITION BY ticker, (bucket AT TIME ZONE 'UTC')::date ORDER BY bucket);

CREATE OR REPLACE VIEW ohlcv_10m_final AS
SELECT bucket, ticker, open, high, low, close,
    COALESCE(cum_volume - LAG(cum_volume) OVER w, cum_volume) AS volume,
    COALESCE(cum_value  - LAG(cum_value)  OVER w, cum_value)  AS value_bdt,
    COALESCE(cum_trades - LAG(cum_trades) OVER w, cum_trades) AS trades
FROM ohlcv_10m
WINDOW w AS (PARTITION BY ticker, (bucket AT TIME ZONE 'UTC')::date ORDER BY bucket);

CREATE OR REPLACE VIEW ohlcv_30m_final AS
SELECT bucket, ticker, open, high, low, close,
    COALESCE(cum_volume - LAG(cum_volume) OVER w, cum_volume) AS volume,
    COALESCE(cum_value  - LAG(cum_value)  OVER w, cum_value)  AS value_bdt,
    COALESCE(cum_trades - LAG(cum_trades) OVER w, cum_trades) AS trades
FROM ohlcv_30m
WINDOW w AS (PARTITION BY ticker, (bucket AT TIME ZONE 'UTC')::date ORDER BY bucket);

CREATE OR REPLACE VIEW ohlcv_60m_final AS
SELECT bucket, ticker, open, high, low, close,
    COALESCE(cum_volume - LAG(cum_volume) OVER w, cum_volume) AS volume,
    COALESCE(cum_value  - LAG(cum_value)  OVER w, cum_value)  AS value_bdt,
    COALESCE(cum_trades - LAG(cum_trades) OVER w, cum_trades) AS trades
FROM ohlcv_60m
WINDOW w AS (PARTITION BY ticker, (bucket AT TIME ZONE 'UTC')::date ORDER BY bucket);
