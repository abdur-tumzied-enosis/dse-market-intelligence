-- Per-company historical announcements with structured extracted fields.
-- Source: dsebd.org/old_news.php?inst={ticker}&criteria=3&archive=news (static HTML).

CREATE TABLE IF NOT EXISTS company_announcements (
    id                  SERIAL PRIMARY KEY,
    ticker              TEXT NOT NULL,
    published_at        TIMESTAMPTZ NOT NULL,
    headline            TEXT NOT NULL,
    details             TEXT,
    source              TEXT NOT NULL DEFAULT 'dse_direct_company_news',

    -- Classified announcement type
    announcement_type   TEXT,          -- 'eps_disclosure' | 'dividend' | 'agm' |
                                       -- 'board_meeting' | 'suspension' | 'resumption' |
                                       -- 'rights_issue' | 'auditor_qualification' |
                                       -- 'ipo' | 'credit_rating' | 'other'

    -- EPS fields (populated when announcement_type = 'eps_disclosure')
    eps_value           NUMERIC(10,4),
    eps_period          TEXT,          -- 'Q1_2026' | 'H1_2025' | 'FY2025'
    eps_type            TEXT,          -- 'audited' | 'unaudited'

    -- Dividend fields (populated when announcement_type = 'dividend')
    dividend_cash_pct   NUMERIC(6,2),
    dividend_stock_pct  NUMERIC(6,2),
    dividend_year       INTEGER,

    content_hash        TEXT,
    ingestion_job       TEXT,

    UNIQUE (content_hash)
);

CREATE INDEX IF NOT EXISTS idx_company_announcements_ticker_published
    ON company_announcements (ticker, published_at DESC);

CREATE INDEX IF NOT EXISTS idx_company_announcements_type_published
    ON company_announcements (announcement_type, published_at DESC);

CREATE INDEX IF NOT EXISTS idx_company_announcements_eps
    ON company_announcements (ticker, eps_period)
    WHERE announcement_type = 'eps_disclosure' AND eps_value IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_company_announcements_dividend
    ON company_announcements (ticker, dividend_year)
    WHERE announcement_type = 'dividend' AND dividend_cash_pct IS NOT NULL;
