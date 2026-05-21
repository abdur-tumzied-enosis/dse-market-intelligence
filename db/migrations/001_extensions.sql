-- Enable required PostgreSQL extensions
-- Idempotent: safe to re-run

CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;  -- trigram indexes for text search
CREATE EXTENSION IF NOT EXISTS btree_gin;
