-- Macro economic indicators

CREATE TABLE IF NOT EXISTS macro_indicators (
    id          BIGSERIAL       PRIMARY KEY,
    indicator   TEXT            NOT NULL,   -- 'policy_rate' | 'cpi' | 'usd_bdt' | 'gdp' | 'remittance'
    value       NUMERIC(20, 6)  NOT NULL,
    unit        TEXT,                       -- 'percent' | 'bdt_per_usd' | 'usd_billion' etc.
    period      TEXT            NOT NULL,   -- 'YYYY-MM' or 'YYYY' or 'YYYY-Q1'
    period_type TEXT            NOT NULL,   -- 'monthly' | 'quarterly' | 'annual'
    source      TEXT            NOT NULL,
    fetched_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    UNIQUE (indicator, period, source)
);

CREATE INDEX IF NOT EXISTS idx_macro_indicator_period ON macro_indicators (indicator, period DESC);
