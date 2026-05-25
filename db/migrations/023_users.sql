-- db/migrations/023_users.sql
-- User accounts for product API authentication

CREATE TABLE IF NOT EXISTS users (
    id              BIGSERIAL       PRIMARY KEY,
    email           TEXT            NOT NULL UNIQUE,
    hashed_password TEXT            NOT NULL,
    full_name       TEXT,
    tier            TEXT            NOT NULL DEFAULT 'free',
    is_active       BOOLEAN         NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_tier CHECK (tier IN ('free', 'pro', 'pro_plus', 'institution'))
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);
