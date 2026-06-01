-- Daily snapshot of the three DSE indices (DSEX, DS30, DSES), one row per
-- trading day. Backs the Bull/Bear regime indicator: regime = DSEX vs its
-- 50-day moving average. No index history existed before this table; it is
-- seeded by a one-time bdshare backfill (extraction/bulk_load/index_history_loader.py)
-- and kept current by job_eod_snapshot (extraction/scheduler.py).

CREATE TABLE IF NOT EXISTS index_daily (
    date        date PRIMARY KEY,
    dsex        numeric,
    ds30        numeric,
    dses        numeric,
    source      text NOT NULL,
    ingested_at timestamptz NOT NULL DEFAULT now()
);
