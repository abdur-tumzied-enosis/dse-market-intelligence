-- Pipeline observability tables

CREATE TABLE IF NOT EXISTS pipeline_jobs (
    id              BIGSERIAL       PRIMARY KEY,
    job_id          TEXT            NOT NULL UNIQUE,  -- APScheduler/Celery task id
    job_name        TEXT            NOT NULL,
    stream_name     TEXT,
    adapter_used    TEXT,
    started_at      TIMESTAMPTZ     NOT NULL,
    finished_at     TIMESTAMPTZ,
    status          TEXT            NOT NULL,  -- 'running' | 'success' | 'failed' | 'partial'
    records_fetched INTEGER,
    records_inserted INTEGER,
    quality_failures INTEGER        DEFAULT 0,
    error_message   TEXT,
    duration_ms     INTEGER,
    metadata        JSONB           DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_jobs_started    ON pipeline_jobs (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_stream     ON pipeline_jobs (stream_name, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_status     ON pipeline_jobs (status);

-- Source health snapshots (every 6h)
CREATE TABLE IF NOT EXISTS source_health (
    id              BIGSERIAL       PRIMARY KEY,
    checked_at      TIMESTAMPTZ     NOT NULL,
    source_name     TEXT            NOT NULL,
    url             TEXT,
    reachable       BOOLEAN         NOT NULL,
    status_code     SMALLINT,
    response_ms     INTEGER,
    structure_hash  TEXT,
    prev_hash       TEXT,           -- NULL on first check or no change
    hash_changed    BOOLEAN         GENERATED ALWAYS AS (
        structure_hash IS DISTINCT FROM prev_hash AND prev_hash IS NOT NULL
    ) STORED
);

CREATE INDEX IF NOT EXISTS idx_health_source_checked
    ON source_health (source_name, checked_at DESC);

-- AI Ops Agent decisions
CREATE TABLE IF NOT EXISTS agent_decisions (
    id              BIGSERIAL       PRIMARY KEY,
    decided_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    run_mode        TEXT            NOT NULL,   -- 'scheduled' | 'alert_hook' | 'chat'
    action_type     TEXT            NOT NULL,
    target          TEXT,
    reasoning       TEXT            NOT NULL,
    risk_level      TEXT            NOT NULL,   -- 'low' | 'medium' | 'high'
    status          TEXT            NOT NULL DEFAULT 'pending',
    -- 'auto_executed' | 'pending_approval' | 'approved' | 'rejected' | 'rolled_back'
    approved_by     TEXT,
    approved_at     TIMESTAMPTZ,
    outcome         TEXT,
    tool_calls      JSONB           DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_agent_decisions_status
    ON agent_decisions (status, decided_at DESC);

-- Pipeline alerts
CREATE TABLE IF NOT EXISTS pipeline_alerts (
    id              BIGSERIAL       PRIMARY KEY,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    severity        TEXT            NOT NULL,   -- 'CRITICAL' | 'WARNING' | 'INFO'
    stream_name     TEXT,
    message         TEXT            NOT NULL,
    details         JSONB           DEFAULT '{}',
    acknowledged_at TIMESTAMPTZ,
    acknowledged_by TEXT,
    notified_via    TEXT[]          DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_alerts_severity
    ON pipeline_alerts (severity, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_unacked
    ON pipeline_alerts (acknowledged_at) WHERE acknowledged_at IS NULL;
