-- Fundamentals, annual reports, dividends, sector PE

CREATE TABLE IF NOT EXISTS fundamentals (
    id              BIGSERIAL       PRIMARY KEY,
    ticker          TEXT            NOT NULL REFERENCES companies (ticker),
    fetched_at      TIMESTAMPTZ     NOT NULL,
    fiscal_year     SMALLINT,
    -- Income statement
    revenue_bdt     NUMERIC(20, 2),
    net_profit_bdt  NUMERIC(20, 2),
    eps             NUMERIC(10, 4),
    eps_diluted     NUMERIC(10, 4),
    -- Balance sheet
    nav             NUMERIC(10, 4),  -- Net Asset Value per share
    nav_adjusted    NUMERIC(10, 4),
    -- Valuation
    pe              NUMERIC(10, 4),
    pb              NUMERIC(10, 4),
    -- Dividends (latest declared)
    cash_div_pct    NUMERIC(8, 4),
    stock_div_pct   NUMERIC(8, 4),
    -- Shareholding
    sponsor_pct     NUMERIC(6, 4),
    institution_pct NUMERIC(6, 4),
    public_pct      NUMERIC(6, 4),
    foreign_pct     NUMERIC(6, 4),
    -- Lineage
    source          TEXT            NOT NULL,
    ingested_at     TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    ingestion_job   TEXT,
    quality_flag    TEXT            NOT NULL DEFAULT 'ok'
);

CREATE INDEX IF NOT EXISTS idx_fundamentals_ticker_fetched
    ON fundamentals (ticker, fetched_at DESC);

-- Dividend history
CREATE TABLE IF NOT EXISTS dividends (
    id              BIGSERIAL       PRIMARY KEY,
    ticker          TEXT            NOT NULL REFERENCES companies (ticker),
    agm_date        DATE,
    record_date     DATE,
    cash_div_pct    NUMERIC(8, 4),
    stock_div_pct   NUMERIC(8, 4),
    fiscal_year     SMALLINT,
    source          TEXT            NOT NULL,
    ingested_at     TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dividends_ticker ON dividends (ticker, agm_date DESC);

-- Sector PE snapshot
CREATE TABLE IF NOT EXISTS sector_pe (
    id              BIGSERIAL       PRIMARY KEY,
    fetched_at      TIMESTAMPTZ     NOT NULL,
    sector          TEXT            NOT NULL,
    pe              NUMERIC(10, 4),
    change_pct      NUMERIC(8, 4),
    market_cap_bdt  NUMERIC(20, 2),
    source          TEXT            NOT NULL,
    ingested_at     TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);
