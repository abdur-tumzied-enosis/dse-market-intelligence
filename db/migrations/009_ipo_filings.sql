-- IPO filings from BSEC (fixed price + bookbuilding)
CREATE TABLE IF NOT EXISTS ipo_filings (
    id              SERIAL PRIMARY KEY,
    company_name    TEXT        NOT NULL,
    ipo_type        TEXT        NOT NULL CHECK (ipo_type IN ('fixed', 'bookbuilding')),
    consent_date    DATE,
    sub_open_date   DATE,
    sub_close_date  DATE,
    nrb_close_date  DATE,
    amount_crore    NUMERIC(14, 4),
    prospectus_url  TEXT,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (company_name, ipo_type)
);

CREATE INDEX IF NOT EXISTS idx_ipo_filings_consent_date
    ON ipo_filings (consent_date DESC NULLS LAST);

CREATE INDEX IF NOT EXISTS idx_ipo_filings_sub_close_date
    ON ipo_filings (sub_close_date DESC NULLS LAST);
