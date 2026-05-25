-- LSTM price direction predictions per ticker per horizon

CREATE TABLE IF NOT EXISTS ml_predictions (
    id              BIGSERIAL       PRIMARY KEY,
    ticker          TEXT            NOT NULL REFERENCES companies (ticker),
    predicted_at    TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    horizon_days    SMALLINT        NOT NULL,
    predicted_direction TEXT            NOT NULL,
    confidence          NUMERIC(6, 4)   NOT NULL,
    target_price        NUMERIC(12, 4),
    model_version       TEXT            NOT NULL DEFAULT 'lstm_v0',
    CONSTRAINT chk_confidence       CHECK (confidence BETWEEN 0 AND 1),
    CONSTRAINT chk_horizon          CHECK (horizon_days IN (5, 10, 20)),
    CONSTRAINT chk_predicted_dir    CHECK (predicted_direction IN ('up', 'down'))
);

CREATE INDEX IF NOT EXISTS idx_ml_predictions_ticker_horizon
    ON ml_predictions (ticker, horizon_days, predicted_at DESC);
CREATE INDEX IF NOT EXISTS idx_ml_predictions_predicted_at
    ON ml_predictions (predicted_at DESC);
