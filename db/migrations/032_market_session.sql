-- db/migrations/032_market_session.sql
-- Real DSE trading-session status, one row per check (transition history).
-- Latest row by checked_at is the current status.
CREATE TABLE IF NOT EXISTS market_session (
    id           BIGSERIAL PRIMARY KEY,
    session_date DATE        NOT NULL,
    status       TEXT        NOT NULL,   -- 'Open' | 'Closed'
    raw_label    TEXT,
    source       TEXT        NOT NULL,   -- 'dse_direct' | 'clock'
    checked_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_market_session_checked_at
    ON market_session (checked_at DESC);
