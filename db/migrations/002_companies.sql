-- Companies reference table — seeded from DSE company list

CREATE TABLE IF NOT EXISTS companies (
    ticker          TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    sector          TEXT NOT NULL,
    category        TEXT,           -- A, B, N, Z, etc.
    isin            TEXT,
    listing_date    DATE,
    market_cap_bdt  NUMERIC,
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_companies_sector ON companies (sector);
CREATE INDEX IF NOT EXISTS idx_companies_active  ON companies (is_active);
