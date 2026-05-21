-- Adapter state overrides — paused flag and priority adjustments.
-- Loaded on mgmt app startup to restore state across restarts.

CREATE TABLE IF NOT EXISTS adapter_overrides (
    id              BIGSERIAL       PRIMARY KEY,
    stream_name     TEXT            NOT NULL,
    adapter_name    TEXT            NOT NULL,
    paused          BOOLEAN         NOT NULL DEFAULT FALSE,
    priority_delta  INTEGER         NOT NULL DEFAULT 0,
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    reason          TEXT,
    CONSTRAINT uq_adapter_override UNIQUE (stream_name, adapter_name)
);

CREATE INDEX IF NOT EXISTS idx_adapter_overrides_stream
    ON adapter_overrides (stream_name);
