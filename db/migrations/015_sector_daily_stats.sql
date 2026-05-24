-- Sector daily stats + daily_ohlcv policy tune + index verification.

-- ── 1. sector_daily_stats ─────────────────────────────────────────────────────
-- Regular materialized view (not CA — CA can't join with non-hypertable tables).
-- Joins daily_ohlcv with companies to aggregate per sector per day.

CREATE MATERIALIZED VIEW IF NOT EXISTS sector_daily_stats AS
SELECT
    d.day,
    c.sector,
    COUNT(DISTINCT d.ticker)    AS ticker_count,
    AVG(d.close)                AS avg_close,
    SUM(d.volume)               AS total_volume,
    SUM(d.value_bdt)            AS total_value_bdt,
    SUM(d.trades)               AS total_trades,
    MAX(d.high)                 AS day_high,
    MIN(d.low)                  AS day_low
FROM daily_ohlcv d
JOIN companies c USING (ticker)
WHERE c.sector IS NOT NULL
GROUP BY d.day, c.sector
WITH NO DATA;

-- Unique index required for REFRESH CONCURRENTLY
CREATE UNIQUE INDEX IF NOT EXISTS idx_sector_daily_stats_day_sector
    ON sector_daily_stats (day DESC, sector);

-- Scheduled refresh: TimescaleDB background job, runs daily at ~15:10 BD time.
-- Procedure signature required by add_job: (job_id INT, config JSONB).
CREATE OR REPLACE PROCEDURE refresh_sector_daily_stats(job_id INT, config JSONB)
LANGUAGE plpgsql AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY sector_daily_stats;
    ANALYZE sector_daily_stats;
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM timescaledb_information.jobs
        WHERE proc_schema = 'public'
          AND proc_name   = 'refresh_sector_daily_stats'
    ) THEN
        PERFORM add_job('refresh_sector_daily_stats', INTERVAL '1 day');
    END IF;
END;
$$;

-- ── 2. Tune daily_ohlcv refresh policy: 1 hour → 1 day ───────────────────────
-- daily OHLCV data doesn't need sub-daily refresh; 1 day saves background load.
DO $$
DECLARE
    _job_id bigint;
    _current_interval interval;
BEGIN
    -- jobs.hypertable_name stores the view name directly (not the materialization hypertable)
    SELECT job_id, schedule_interval
    INTO _job_id, _current_interval
    FROM timescaledb_information.jobs
    WHERE proc_name    = 'policy_refresh_continuous_aggregate'
      AND hypertable_name = 'daily_ohlcv'
    LIMIT 1;

    IF _job_id IS NOT NULL AND _current_interval <> INTERVAL '1 day' THEN
        PERFORM alter_job(_job_id, schedule_interval => INTERVAL '1 day');
    END IF;
END;
$$;

-- ── 3. Index audit ────────────────────────────────────────────────────────────
-- fundamentals(ticker, fetched_at DESC) — created in 004_fundamentals.sql.
-- Confirming idempotently in case it was dropped.
CREATE INDEX IF NOT EXISTS idx_fundamentals_ticker_fetched
    ON fundamentals (ticker, fetched_at DESC);

-- news(published_at DESC) + GIN(tickers) — created in 005_news.sql.
CREATE INDEX IF NOT EXISTS idx_news_published ON news (published_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_tickers   ON news USING gin (tickers);
