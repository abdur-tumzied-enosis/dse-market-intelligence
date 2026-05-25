-- Tracks actual vs predicted direction once horizon passes

CREATE TABLE IF NOT EXISTS prediction_outcomes (
    id                  BIGSERIAL       PRIMARY KEY,
    prediction_id       BIGINT          NOT NULL REFERENCES ml_predictions (id),
    ticker              TEXT            NOT NULL,
    horizon_days        SMALLINT        NOT NULL,
    predicted_at        TIMESTAMPTZ     NOT NULL,
    predicted_direction TEXT            NOT NULL,
    confidence          NUMERIC(6, 4)   NOT NULL,
    price_at_prediction NUMERIC(12, 4),
    price_at_horizon    NUMERIC(12, 4),
    return_pct          NUMERIC(8, 4),
    actual_direction    TEXT,
    correct             BOOLEAN,
    evaluated_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_outcomes_pending
    ON prediction_outcomes (predicted_at, correct) WHERE correct IS NULL;
CREATE INDEX IF NOT EXISTS idx_outcomes_evaluated
    ON prediction_outcomes (correct, horizon_days, evaluated_at DESC);
