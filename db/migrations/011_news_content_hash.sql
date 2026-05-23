-- Add content_hash to news table for deduplication.
--
-- Problem: Google News RSS uses proxy URLs (news.google.com/rss/articles/...)
-- that get regenerated periodically for the same underlying article.
-- Deduplicating on url alone causes duplicate inserts when the proxy URL rotates.
--
-- Solution: content_hash = MD5(lower(trim(headline)) || '|' || source)
-- This identifies the same article regardless of URL changes.
-- The existing url UNIQUE constraint is kept for direct-source URLs.

ALTER TABLE news ADD COLUMN IF NOT EXISTS content_hash TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_news_content_hash ON news (content_hash)
    WHERE content_hash IS NOT NULL;

-- Backfill existing rows
UPDATE news
SET content_hash = MD5(lower(trim(headline)) || '|' || source)
WHERE content_hash IS NULL;
