-- Partial unique index so historical annual fundamentals are idempotent on re-load.
-- NULL fiscal_year rows (weekly scrapes) are unaffected.

CREATE UNIQUE INDEX IF NOT EXISTS idx_fundamentals_ticker_fiscal_year
    ON fundamentals (ticker, fiscal_year)
    WHERE fiscal_year IS NOT NULL;
