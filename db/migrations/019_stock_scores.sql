-- Composite stock health scores (0–100)

CREATE TABLE IF NOT EXISTS stock_scores (
    id                  BIGSERIAL       PRIMARY KEY,
    ticker              TEXT            NOT NULL REFERENCES companies (ticker),
    scored_at           TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    health_score        NUMERIC(6, 2),
    fundamental_score   NUMERIC(6, 4),
    momentum_score      NUMERIC(6, 4),
    valuation_score     NUMERIC(6, 4),
    sentiment_score     NUMERIC(6, 4),
    model_version       TEXT            NOT NULL DEFAULT 'v1',
    CONSTRAINT chk_health CHECK (health_score BETWEEN 0 AND 100)
);

CREATE INDEX IF NOT EXISTS idx_stock_scores_ticker_scored
    ON stock_scores (ticker, scored_at DESC);
CREATE INDEX IF NOT EXISTS idx_stock_scores_health
    ON stock_scores (health_score DESC, scored_at DESC);
