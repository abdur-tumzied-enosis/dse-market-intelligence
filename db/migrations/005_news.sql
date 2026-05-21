-- News articles + pgvector document chunks

CREATE TABLE IF NOT EXISTS news (
    id              BIGSERIAL       PRIMARY KEY,
    source          TEXT            NOT NULL,
    url             TEXT            UNIQUE,
    headline        TEXT            NOT NULL,
    body            TEXT,
    language        TEXT            NOT NULL DEFAULT 'en',  -- 'en' | 'bn'
    published_at    TIMESTAMPTZ     NOT NULL,
    fetched_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    tickers         TEXT[],         -- extracted DSE tickers mentioned
    sentiment_score NUMERIC(5, 4),  -- -1.0 to 1.0 (haiku scored)
    sentiment_label TEXT,           -- 'positive' | 'neutral' | 'negative'
    ingestion_job   TEXT
);

CREATE INDEX IF NOT EXISTS idx_news_published ON news (published_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_source    ON news (source);
CREATE INDEX IF NOT EXISTS idx_news_tickers   ON news USING gin (tickers);

-- Document chunks with pgvector embeddings (for RAG)
-- Covers both news chunks and annual report chunks
CREATE TABLE IF NOT EXISTS document_chunks (
    id              BIGSERIAL       PRIMARY KEY,
    doc_type        TEXT            NOT NULL,  -- 'news' | 'annual_report'
    doc_id          BIGINT,                    -- FK to news.id or annual_reports.id
    ticker          TEXT,
    chunk_index     SMALLINT        NOT NULL,
    chunk_text      TEXT            NOT NULL,
    embedding       vector(1024),             -- voyage-finance-2 dimensions
    model           TEXT            NOT NULL DEFAULT 'voyage-finance-2',
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- IVFFlat index (tune lists parameter after bulk load — sqrt of row count)
-- CREATE INDEX idx_chunks_embedding ON document_chunks
--     USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
-- Commented: create after bulk load when row count is known.

CREATE INDEX IF NOT EXISTS idx_chunks_ticker   ON document_chunks (ticker);
CREATE INDEX IF NOT EXISTS idx_chunks_doc_type ON document_chunks (doc_type, doc_id);

-- Annual reports (PDF metadata)
CREATE TABLE IF NOT EXISTS annual_reports (
    id              BIGSERIAL       PRIMARY KEY,
    ticker          TEXT            NOT NULL REFERENCES companies (ticker),
    fiscal_year     SMALLINT        NOT NULL,
    pdf_url         TEXT,
    pdf_path        TEXT,           -- MinIO object key
    extracted_at    TIMESTAMPTZ,
    page_count      SMALLINT,
    source          TEXT            NOT NULL DEFAULT 'dse_direct',
    ingested_at     TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    UNIQUE (ticker, fiscal_year)
);
