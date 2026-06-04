-- market_gaps — durable record of live-price polling holes during market hours.
-- Volume/value self-recover (session-cumulative feed); only intraday PRICE
-- granularity is lost across a gap. This ledger makes holes visible so consumers
-- can flag affected bars, and gives a backfill target list (recovered flag).

CREATE TABLE IF NOT EXISTS market_gaps (
    id           BIGSERIAL PRIMARY KEY,
    session_date DATE        NOT NULL,
    gap_start    TIMESTAMPTZ NOT NULL,   -- last snapshot instant before the gap (UTC)
    gap_end      TIMESTAMPTZ NOT NULL,   -- first snapshot instant after the gap (UTC)
    reason       TEXT        NOT NULL DEFAULT 'scheduler_downtime',
    recovered    BOOLEAN     NOT NULL DEFAULT FALSE,
    detected_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_market_gaps_session_date
    ON market_gaps (session_date);
CREATE INDEX IF NOT EXISTS idx_market_gaps_unrecovered
    ON market_gaps (recovered) WHERE recovered = FALSE;
