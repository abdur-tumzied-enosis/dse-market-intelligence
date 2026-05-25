-- LLM API usage tracking: cost monitoring + Grafana dashboards

CREATE TABLE IF NOT EXISTS llm_usage_log (
    id              BIGSERIAL       PRIMARY KEY,
    session_id      TEXT            NOT NULL,
    provider        TEXT            NOT NULL,              -- 'google' | 'openrouter' | 'ollama'
    model           TEXT            NOT NULL,
    input_tokens    INTEGER         NOT NULL DEFAULT 0,
    output_tokens   INTEGER         NOT NULL DEFAULT 0,
    thinking_tokens INTEGER         NOT NULL DEFAULT 0,    -- Gemini thinking tokens
    cost_usd        NUMERIC(10, 6)  NOT NULL DEFAULT 0,
    tool_calls_n    SMALLINT        NOT NULL DEFAULT 0,
    latency_ms      INTEGER,
    error           TEXT,
    tier            TEXT            NOT NULL DEFAULT 'free',
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_llm_usage_session ON llm_usage_log (session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_usage_created  ON llm_usage_log (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_usage_provider ON llm_usage_log (provider, model, created_at DESC);
