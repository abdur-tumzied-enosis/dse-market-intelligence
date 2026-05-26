-- db/migrations/025_access_control.sql
-- Runtime-configurable tier limits, feature flags, and per-user access overrides.

CREATE TABLE IF NOT EXISTS tier_limits (
    tier        TEXT    NOT NULL,
    limit_key   TEXT    NOT NULL,
    limit_value INTEGER NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tier, limit_key)
);

CREATE TABLE IF NOT EXISTS feature_flags (
    flag_key    TEXT    NOT NULL,
    tier        TEXT    NOT NULL,
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by  BIGINT  REFERENCES users(id) ON DELETE SET NULL,
    PRIMARY KEY (flag_key, tier)
);

CREATE TABLE IF NOT EXISTS user_access_overrides (
    id          BIGSERIAL   PRIMARY KEY,
    user_id     BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    flag_key    TEXT        NOT NULL,
    override    TEXT        NOT NULL,
    expires_at  TIMESTAMPTZ,
    note        TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by  BIGINT      REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT chk_override CHECK (override IN ('grant', 'revoke')),
    UNIQUE (user_id, flag_key)
);

CREATE INDEX IF NOT EXISTS idx_user_overrides_user_id ON user_access_overrides (user_id);

-- Seed: default tier limits (0 = unlimited)
INSERT INTO tier_limits (tier, limit_key, limit_value) VALUES
    ('free',        'api_calls_per_day',        50),
    ('free',        'chat_queries_per_day',       3),
    ('free',        'portfolio_holdings_max',     5),
    ('pro',         'api_calls_per_day',       1000),
    ('pro',         'chat_queries_per_day',      30),
    ('pro',         'portfolio_holdings_max',     0),
    ('pro_plus',    'api_calls_per_day',       5000),
    ('pro_plus',    'chat_queries_per_day',     100),
    ('pro_plus',    'portfolio_holdings_max',     0),
    ('institution', 'api_calls_per_day',          0),
    ('institution', 'chat_queries_per_day',        0),
    ('institution', 'portfolio_holdings_max',      0)
ON CONFLICT (tier, limit_key) DO NOTHING;

-- Seed: default feature flags
INSERT INTO feature_flags (flag_key, tier, enabled) VALUES
    ('predictions',           'free',        false),
    ('predictions',           'pro',         true),
    ('predictions',           'pro_plus',    true),
    ('predictions',           'institution', true),
    ('reports',               'free',        false),
    ('reports',               'pro',         true),
    ('reports',               'pro_plus',    true),
    ('reports',               'institution', true),
    ('portfolio_analysis',    'free',        false),
    ('portfolio_analysis',    'pro',         true),
    ('portfolio_analysis',    'pro_plus',    true),
    ('portfolio_analysis',    'institution', true),
    ('chat',                  'free',        true),
    ('chat',                  'pro',         true),
    ('chat',                  'pro_plus',    true),
    ('chat',                  'institution', true),
    ('screener_health_score', 'free',        false),
    ('screener_health_score', 'pro',         true),
    ('screener_health_score', 'pro_plus',    true),
    ('screener_health_score', 'institution', true)
ON CONFLICT (flag_key, tier) DO NOTHING;
