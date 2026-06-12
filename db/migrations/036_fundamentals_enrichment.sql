-- 1. fundamentals: per-year additions
ALTER TABLE fundamentals
    ADD COLUMN IF NOT EXISTS total_comprehensive_income_bdt NUMERIC(20, 2),
    ADD COLUMN IF NOT EXISTS dividend_yield_pct NUMERIC(8, 4),
    ADD COLUMN IF NOT EXISTS eps_basis TEXT;
-- net_profit_bdt already exists (004); loader writes profit_mn * 1e6.

-- 2. shareholding history (FR2)
-- NUMERIC(7,4): 100.0000 must fit (fully govt/sponsor-held tickers exist);
-- the NUMERIC(6,4) used in 004 caps at 99.9999 — do not copy that.
CREATE TABLE IF NOT EXISTS shareholding_history (
    id              BIGSERIAL    PRIMARY KEY,
    ticker          TEXT         NOT NULL REFERENCES companies (ticker),
    as_on_date      DATE         NOT NULL,
    sponsor_pct     NUMERIC(7,4),
    govt_pct        NUMERIC(7,4),
    institution_pct NUMERIC(7,4),
    foreign_pct     NUMERIC(7,4),
    public_pct      NUMERIC(7,4),
    fetched_at      TIMESTAMPTZ  NOT NULL,
    source          TEXT         NOT NULL,
    ingested_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (ticker, as_on_date)
);
CREATE INDEX IF NOT EXISTS idx_shareholding_ticker_date
    ON shareholding_history (ticker, as_on_date DESC);

-- 3. corporate actions (FR3)
CREATE TABLE IF NOT EXISTS corporate_actions (
    id          BIGSERIAL   PRIMARY KEY,
    ticker      TEXT        NOT NULL REFERENCES companies (ticker),
    fiscal_year SMALLINT    NOT NULL,
    action_type TEXT        NOT NULL CHECK (action_type IN ('cash_div','stock_div','right_issue')),
    value_pct   NUMERIC(8,4),          -- cash/stock dividend, % of face value (DSE convention:
                                       -- 15% on 10 BDT face = 1.50 BDT/share), gross of tax
    ratio_text  TEXT,                  -- right issue raw, e.g. '1R:2'
    ratio       NUMERIC(8,4),          -- right issue: new shares per existing
    source      TEXT        NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (ticker, fiscal_year, action_type)
);
-- One action per type per year matches the SOURCE: the DSE th/td strings print one
-- combined figure per year ("15% 2025"). Interim/final dividend granularity lives in
-- company_announcements (migration 016), not here.
CREATE INDEX IF NOT EXISTS idx_corporate_actions_ticker
    ON corporate_actions (ticker, fiscal_year DESC);

-- 4. quarterly EPS (FR5)
CREATE TABLE IF NOT EXISTS fundamentals_quarterly (
    id               BIGSERIAL   PRIMARY KEY,
    ticker           TEXT        NOT NULL REFERENCES companies (ticker),
    fiscal_year      SMALLINT    NOT NULL,
    quarter          SMALLINT    NOT NULL CHECK (quarter BETWEEN 1 AND 4),
    eps_basic        NUMERIC(10,4),
    eps_diluted      NUMERIC(10,4),
    period_end_price NUMERIC(12,4),
    fetched_at       TIMESTAMPTZ NOT NULL,
    source           TEXT        NOT NULL,
    ingested_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (ticker, fiscal_year, quarter)
);

-- 5. companies: risk/meta fields (FR4, latest-snapshot semantics)
ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS face_value         NUMERIC(10,2),
    ADD COLUMN IF NOT EXISTS market_lot         INTEGER,
    ADD COLUMN IF NOT EXISTS scrip_code         TEXT,
    ADD COLUMN IF NOT EXISTS electronic_share   BOOLEAN,
    ADD COLUMN IF NOT EXISTS debut_trading_date DATE,
    ADD COLUMN IF NOT EXISTS operational_status TEXT,
    ADD COLUMN IF NOT EXISTS short_loan_mn      NUMERIC(20,2),
    ADD COLUMN IF NOT EXISTS long_loan_mn       NUMERIC(20,2),
    ADD COLUMN IF NOT EXISTS loan_as_on         DATE,
    ADD COLUMN IF NOT EXISTS credit_rating_st   TEXT,
    ADD COLUMN IF NOT EXISTS credit_rating_lt   TEXT,
    ADD COLUMN IF NOT EXISTS delisting_remark   TEXT,
    ADD COLUMN IF NOT EXISTS ir_url             TEXT,
    ADD COLUMN IF NOT EXISTS psi_url            TEXT;
