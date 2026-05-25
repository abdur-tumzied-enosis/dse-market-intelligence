-- Prevent duplicate scores/predictions when nightly jobs run more than once.
-- Use explicit date columns (not expression indexes) to avoid IMMUTABLE requirement.

ALTER TABLE stock_scores
    ADD COLUMN IF NOT EXISTS scored_date DATE;
UPDATE stock_scores SET scored_date = scored_at::date WHERE scored_date IS NULL;

-- Remove duplicates created before this constraint existed; keep latest row per group.
DELETE FROM stock_scores
WHERE id NOT IN (
    SELECT MAX(id)
    FROM stock_scores
    GROUP BY ticker, model_version, scored_date
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_stock_scores_ticker_model_date
    ON stock_scores (ticker, model_version, scored_date);

ALTER TABLE ml_predictions
    ADD COLUMN IF NOT EXISTS prediction_date DATE;
UPDATE ml_predictions SET prediction_date = predicted_at::date WHERE prediction_date IS NULL;

DELETE FROM ml_predictions
WHERE id NOT IN (
    SELECT MAX(id)
    FROM ml_predictions
    GROUP BY ticker, horizon_days, model_version, prediction_date
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_ml_predictions_ticker_horizon_model_date
    ON ml_predictions (ticker, horizon_days, model_version, prediction_date);
