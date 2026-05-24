-- Weekly OHLCV continuous aggregate — hierarchical CA on top of daily_ohlcv.
-- Requires TimescaleDB 2.9+ (timescale/timescaledb-ha:pg16 ships 2.14+).

CREATE MATERIALIZED VIEW IF NOT EXISTS weekly_ohlcv
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('7 days', day) AS week,
    ticker,
    FIRST(open, day)           AS open,
    MAX(high)                  AS high,
    MIN(low)                   AS low,
    LAST(close, day)           AS close,
    SUM(volume)                AS volume,
    SUM(trades)                AS trades,
    SUM(value_bdt)             AS value_bdt
FROM daily_ohlcv
GROUP BY week, ticker
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
    'weekly_ohlcv',
    start_offset      => INTERVAL '8 weeks',
    end_offset        => INTERVAL '1 week',
    schedule_interval => INTERVAL '1 week',
    if_not_exists     => true
);
