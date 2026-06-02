-- Enrichment flag for the companies roster.
--
-- The seed (extraction/bulk_load/seed_companies.py) now sources its ticker
-- roster from DSE's company_listing.php, which carries only the trading code —
-- no name, sector, category, or market cap. New tickers are inserted as a bare
-- roster row and left for the enrichment pass
-- (extraction/bulk_load/enrich_companies.py) to fill from displayCompany.php.
--
-- enriched_at IS NULL  → row needs enrichment (placeholder name/sector).
-- enriched_at IS NOT NULL → details fetched from DSE at that time.
--
-- Existing rows already carry real name/sector/cap (AmarStock seed), so backfill
-- them as enriched to keep them out of the enrichment queue.

ALTER TABLE companies ADD COLUMN IF NOT EXISTS enriched_at TIMESTAMPTZ;

UPDATE companies SET enriched_at = COALESCE(enriched_at, updated_at)
WHERE enriched_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_companies_unenriched
    ON companies (enriched_at) WHERE enriched_at IS NULL;
