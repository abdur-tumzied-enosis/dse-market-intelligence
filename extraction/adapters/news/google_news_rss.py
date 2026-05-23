"""
Google News RSS adapter — single adapter covering all BD financial news sources.

Fetches https://news.google.com/rss/search for Bangladesh finance/stock queries.
Google News RSS aggregates TBS, Financial Express, Daily Star, Dhaka Tribune, Reuters BD, etc.
No individual site scrapers needed; Google handles source diversity and deduplication.

Output schema matches the `news` table (005_news.sql + 011_news_content_hash.sql):
    source, url, headline, body, language, published_at, fetched_at, tickers, content_hash

content_hash = MD5(lower(headline) + '|' + source) — used for dedup instead of url,
because Google News proxy URLs (news.google.com/rss/articles/...) regenerate periodically
for the same underlying article, causing false duplicate inserts if url is the dedup key.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import feedparser
import pandas as pd
import structlog

from extraction.base import AdapterError, AdapterResult, BaseAdapter

logger = structlog.get_logger(__name__)

_GOOGLE_RSS_BASE = "https://news.google.com/rss/search"

_DEFAULT_QUERIES = [
    "Bangladesh stock market DSE finance",
    "Dhaka Stock Exchange company earnings",
]

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return _HTML_TAG_RE.sub("", text).strip()


class GoogleNewsRSSAdapter(BaseAdapter):
    """Fetch BD financial news via Google News RSS. Covers all major English sources."""

    name = "google_news_rss"
    priority = 1
    timeout_seconds = 30

    def __init__(self, queries: list[str] | None = None) -> None:
        self._queries = queries or _DEFAULT_QUERIES

    async def fetch(self, **kwargs: Any) -> AdapterResult:
        fetched_at = datetime.now(timezone.utc)
        raw: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        loop = asyncio.get_running_loop()

        for query in self._queries:
            params = urlencode({"q": query, "hl": "en", "gl": "BD", "ceid": "BD:en"})
            feed_url = f"{_GOOGLE_RSS_BASE}?{params}"

            feed = await loop.run_in_executor(None, feedparser.parse, feed_url)

            if feed.bozo and not feed.entries:
                raise AdapterError(
                    self.name,
                    f"RSS parse error for '{query}': {feed.bozo_exception}",
                    retryable=True,
                )

            for entry in feed.entries:
                url: str = entry.get("link", "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)

                published_struct = entry.get("published_parsed")
                if published_struct:
                    published_at = datetime(*published_struct[:6], tzinfo=timezone.utc)
                else:
                    published_at = fetched_at

                source_info = entry.get("source", {})
                source = source_info.get("title", "google_news") if isinstance(source_info, dict) else "google_news"

                headline = _strip_html(entry.get("title", ""))
                content_hash = hashlib.md5(
                    f"{headline.lower().strip()}|{source}".encode()
                ).hexdigest()

                raw.append({
                    "source": source,
                    "url": url,
                    "headline": headline,
                    "body": _strip_html(entry.get("summary", "")),
                    "language": "en",
                    "published_at": published_at,
                    "fetched_at": fetched_at,
                    "tickers": [],
                    "content_hash": content_hash,
                })

        df = self.normalize(raw)
        return AdapterResult(
            data=df,
            source_name=self.name,
            fetched_at=fetched_at,
            quality="ok" if len(df) > 0 else "partial",
            records=len(df),
        )

    def normalize(self, raw: Any) -> pd.DataFrame:
        if not raw:
            return pd.DataFrame(
                columns=["source", "url", "headline", "body", "language",
                         "published_at", "fetched_at", "tickers", "content_hash"]
            )
        df = pd.DataFrame(raw)
        df["headline"] = df["headline"].str.strip()
        df = df[df["headline"].str.len() > 0].copy()
        return df.reset_index(drop=True)

    async def health_check(self) -> bool:
        loop = asyncio.get_running_loop()
        params = urlencode({"q": self._queries[0], "hl": "en", "gl": "BD", "ceid": "BD:en"})
        feed_url = f"{_GOOGLE_RSS_BASE}?{params}"
        feed = await loop.run_in_executor(None, feedparser.parse, feed_url)
        return bool(feed.entries)
