-- Stock prices hypertable + daily OHLCV materialized view

CREATE TABLE IF NOT EXISTS stock_prices (
    time            TIMESTAMPTZ     NOT NULL,
    ticker          TEXT            NOT NULL REFERENCES companies (ticker),
    open            NUMERIC(12, 4),
    high            NUMERIC(12, 4),
    low             NUMERIC(12, 4),
    close           NUMERIC(12, 4)  NOT NULL,
    volume          BIGINT,
    trades          INTEGER,
    value_bdt       NUMERIC(18, 2),
    prev_close      NUMERIC(12, 4),
    change_pct      NUMERIC(8, 4),
    -- lineage
    source          TEXT            NOT NULL,
    ingested_at     TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    ingestion_job   TEXT,
    quality_flag    TEXT            NOT NULL DEFAULT 'ok'
);

SELECT create_hypertable(
    'stock_prices', 'time',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => true
);

CREATE INDEX IF NOT EXISTS idx_stock_prices_ticker_time
    ON stock_prices (ticker, time DESC);

-- Daily OHLCV continuous aggregate (refreshes automatically via TimescaleDB policy)
CREATE MATERIALIZED VIEW IF NOT EXISTS daily_ohlcv
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 day', time) AS day,
    ticker,
    FIRST(open, time)          AS open,
    MAX(high)                  AS high,
    MIN(low)                   AS low,
    LAST(close, time)          AS close,
    SUM(volume)                AS volume,
    SUM(trades)                AS trades,
    SUM(value_bdt)             AS value_bdt,
    LAST(source, time)         AS source
FROM stock_prices
GROUP BY day, ticker
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
    'daily_ohlcv',
    start_offset  => INTERVAL '3 days',
    end_offset    => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => true
);
