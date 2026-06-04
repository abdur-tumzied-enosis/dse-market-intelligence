-- Raw intraday price snapshots — append-only hypertable feeding the intraday
-- OHLCV continuous aggregates (migration 034).
--
-- WHY a separate table (not stock_prices): job_live_prices UPSERTs exactly ONE
-- row per ticker per trading day into stock_prices, because the live feeds
-- report SESSION-CUMULATIVE volume/value/trades and daily_ohlcv does SUM(volume)
-- — appending fresh rows there would inflate daily volume ~Nx. Intraday OHLCV
-- needs the opposite: one row PER POLL so 5/10/30/60-minute buckets have detail
-- to aggregate. So live snapshots are ALSO appended here, raw and untouched.
--
-- Cumulative columns are stored AS-IS from the feed. Per-bar volume = the delta
-- between consecutive snapshots, computed downstream in the *_final views
-- (migration 034) via LAG() — never SUM() over these rows.

CREATE TABLE IF NOT EXISTS intraday_prices (
    time        TIMESTAMPTZ     NOT NULL,   -- real snapshot instant (UTC), NOT a day bucket
    ticker      TEXT            NOT NULL REFERENCES companies (ticker),
    ltp         NUMERIC(12, 4)  NOT NULL,   -- last traded price at snapshot
    cum_volume  BIGINT,                     -- session-cumulative volume (raw)
    cum_value   NUMERIC(18, 2),             -- session-cumulative value_bdt (raw)
    cum_trades  INTEGER,                    -- session-cumulative trade count (raw)
    source      TEXT            NOT NULL,
    ingested_at TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

SELECT create_hypertable(
    'intraday_prices', 'time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => true
);

CREATE INDEX IF NOT EXISTS idx_intraday_prices_ticker_time
    ON intraday_prices (ticker, time DESC);

-- Compress chunks older than 7 days (raw snapshots are write-once, read-rarely
-- after the CAs materialize). segmentby ticker → strong intra-segment ordering.
ALTER TABLE intraday_prices SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'ticker',
    timescaledb.compress_orderby   = 'time DESC'
);

SELECT add_compression_policy(
    'intraday_prices', INTERVAL '7 days', if_not_exists => true
);

-- Drop raw snapshots after 30 days; the 5/10/30/60m continuous aggregates persist.
SELECT add_retention_policy(
    'intraday_prices', INTERVAL '30 days', if_not_exists => true
);
