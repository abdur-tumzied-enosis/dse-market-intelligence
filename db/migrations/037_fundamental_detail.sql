-- Persist the fundamental scoring explanation payload (pillars + drivers)
-- produced by ml/inference/score_fundamentals.py. Score itself stays in
-- fundamental_score; this holds the beginner-facing breakdown.
ALTER TABLE stock_scores
    ADD COLUMN IF NOT EXISTS fundamental_detail JSONB;
