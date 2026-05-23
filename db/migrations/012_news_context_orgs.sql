-- Add context_orgs to news table for knowledge graph signal capture.
--
-- context_orgs stores regulatory, macro, and institutional entities extracted
-- from articles that are NOT DSE-listed companies. These are high-value graph
-- nodes for stock prediction: NBR actions, Bangladesh Bank rate changes,
-- BGMEA export data, ACC investigations co-mentioned with listed companies.
--
-- Distinct from tickers (which are matched DSE symbols).
-- Enables future KG edges: [BRACBANK] ← mentioned_with → [Bangladesh Bank]

ALTER TABLE news ADD COLUMN IF NOT EXISTS context_orgs TEXT[] DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_news_context_orgs ON news USING gin (context_orgs);
