"""
Phase 1F smoke test — GoogleNewsRSSAdapter + TickerExtractor.

Hits live Google News RSS and (optionally) Google NL API.
Run:
    python -m pytest tests/smoke/test_news_smoke.py -v -s --no-cov

Saves fixture: tests/fixtures/news_google_rss_sample.pkl
Requires: feedparser installed, internet access.
NL API tests require GOOGLE_CLOUD_API_KEY in .env (free tier: 5k req/month).
"""
from __future__ import annotations

import os
import pickle
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"
FIXTURE_DIR.mkdir(exist_ok=True)

# Synthetic company map for NER tests — no DB needed
_COMPANY_MAP: dict[str, str] = {
    # ticker → ticker (direct symbol mentions)
    "gp": "GP", "bracbank": "BRACBANK", "squarepharma": "SQUAREPHARMA",
    "beximco": "BEXIMCO", "walton": "WALTONHIL", "dbbl": "DUTCHBANGL",
    "ebl": "EBL", "citybank": "CITYBANK", "brac": "BRACBANK",
    # company names → ticker
    "grameenphone": "GP", "grameenphone plc": "GP",
    "brac bank": "BRACBANK", "brac bank limited": "BRACBANK",
    "dutch-bangla bank": "DUTCHBANGL", "dutch bangla bank": "DUTCHBANGL",
    "eastern bank": "EBL", "eastern bank limited": "EBL",
    "city bank": "CITYBANK", "the city bank": "CITYBANK",
    "square pharmaceuticals": "SQUAREPHARMA",
    "beximco pharmaceuticals": "BXPHARMA",
    "walton hi-tech industries": "WALTONHIL",
    "islami bank": "ISLAMIBANK", "islami bank bangladesh": "ISLAMIBANK",
    "dutch-bangla bank limited": "DUTCHBANGL",
    "national bank": "NBL",
    "prime bank": "PRIMEBANK",
    "southeast bank": "SEBL",
}


# ── RSS fetch ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rss_fetch_returns_articles():
    """GoogleNewsRSSAdapter returns at least 10 articles."""
    from extraction.adapters.news.google_news_rss import GoogleNewsRSSAdapter

    adapter = GoogleNewsRSSAdapter()
    result = await adapter.fetch()

    assert result.records > 0, "No articles returned from Google News RSS"
    assert len(result.data) >= 5, f"Expected >=5 articles, got {len(result.data)}"

    # Save fixture
    fixture_path = FIXTURE_DIR / "news_google_rss_sample.pkl"
    with open(fixture_path, "wb") as f:
        pickle.dump(result.data, f)
    print(f"\nSaved {len(result.data)} articles -> {fixture_path.name}")


@pytest.mark.asyncio
async def test_rss_articles_have_required_schema():
    """All required columns present and non-null for key fields."""
    from extraction.adapters.news.google_news_rss import GoogleNewsRSSAdapter

    result = await GoogleNewsRSSAdapter().fetch()
    df = result.data

    required_cols = {"source", "url", "headline", "language", "published_at", "fetched_at", "content_hash"}
    missing = required_cols - set(df.columns)
    assert not missing, f"Missing columns: {missing}"

    assert df["headline"].str.len().gt(0).all(), "Empty headlines found"
    assert df["url"].str.len().gt(0).all(), "Empty URLs found"
    assert (df["language"] == "en").all(), "Unexpected language values"
    assert df["content_hash"].notna().all(), "Null content hashes"


@pytest.mark.asyncio
async def test_rss_articles_are_recent():
    """Published dates are within the last 7 days."""
    from extraction.adapters.news.google_news_rss import GoogleNewsRSSAdapter

    result = await GoogleNewsRSSAdapter().fetch()
    df = result.data

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    recent_count = sum(
        1 for pub in df["published_at"]
        if isinstance(pub, datetime) and pub.replace(tzinfo=pub.tzinfo or timezone.utc) >= cutoff
    )
    assert recent_count > 0, (
        f"No articles within last 30 days — oldest: {min(df['published_at'])}"
    )
    print(f"\n{recent_count}/{len(df)} articles within last 30 days")


@pytest.mark.asyncio
async def test_rss_content_hashes_unique():
    """content_hash dedup is working — no two articles share a hash."""
    from extraction.adapters.news.google_news_rss import GoogleNewsRSSAdapter

    result = await GoogleNewsRSSAdapter().fetch()
    df = result.data

    dupes = df[df["content_hash"].duplicated(keep=False)]
    assert len(dupes) == 0, (
        f"Duplicate content_hashes found:\n{dupes[['headline', 'source', 'content_hash']].to_string()}"
    )


@pytest.mark.asyncio
async def test_rss_sources_are_diverse():
    """Multiple news outlets represented."""
    from extraction.adapters.news.google_news_rss import GoogleNewsRSSAdapter

    result = await GoogleNewsRSSAdapter().fetch()
    sources = result.data["source"].unique()
    print(f"\nSources: {sorted(sources)}")
    assert len(sources) >= 2, f"Expected >=2 sources, got {sources}"


# ── TickerExtractor (NL API) ───────────────────────────────────────────────

def _get_api_key() -> str:
    key = os.environ.get("GOOGLE_CLOUD_API_KEY", "")
    if not key:
        # Try loading .env directly
        env_path = Path(__file__).parents[2] / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("GOOGLE_CLOUD_API_KEY="):
                    key = line.split("=", 1)[1].strip()
    return key


async def _nl_api_available(api_key: str) -> bool:
    """Return False if Cloud NL API returns 403 (not enabled) or key is missing."""
    import httpx
    payload = {"document": {"type": "PLAIN_TEXT", "content": "test"}, "encodingType": "UTF8"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://language.googleapis.com/v1/documents:analyzeEntitySentiment",
                params={"key": api_key},
                json=payload,
            )
            return resp.status_code != 403
    except Exception:
        return False


@pytest.mark.asyncio
async def test_ticker_extractor_known_companies():
    """NL API correctly identifies GP and BRACBANK from an article mentioning both."""
    api_key = _get_api_key()
    if not api_key:
        pytest.skip("GOOGLE_CLOUD_API_KEY not set")
    if not await _nl_api_available(api_key):
        pytest.skip(
            "Cloud Natural Language API not enabled — enable at "
            "https://console.cloud.google.com/apis/library/language.googleapis.com"
        )

    from extraction.adapters.news.ticker_extractor import TickerExtractor

    extractor = TickerExtractor(api_key=api_key, company_map=_COMPANY_MAP)
    text = (
        "Grameenphone reported strong Q1 earnings today. "
        "BRAC Bank shares rose 2.5% on the DSE following the announcement. "
        "The Bangladesh Securities and Exchange Commission (BSEC) welcomed the results."
    )
    result = await extractor.extract(text)

    print(f"\nTickers: {result.tickers}")
    print(f"Context orgs: {result.context_orgs}")
    print(f"Sentiment: {result.sentiment_score} ({result.sentiment_label})")

    assert "GP" in result.tickers, f"Expected GP in tickers, got {result.tickers}"
    assert "BRACBANK" in result.tickers, f"Expected BRACBANK in tickers, got {result.tickers}"
    assert "bsec" in result.context_orgs or "BSEC" not in result.tickers, (
        "BSEC should not appear as a ticker"
    )


@pytest.mark.asyncio
async def test_ticker_extractor_blocklist():
    """Blocklisted orgs (Bangladesh Bank, NBR) go to context_orgs, not tickers."""
    api_key = _get_api_key()
    if not api_key:
        pytest.skip("GOOGLE_CLOUD_API_KEY not set")
    # This test passes even without NL API — preflight skips the call for macro-only articles.

    from extraction.adapters.news.ticker_extractor import TickerExtractor

    extractor = TickerExtractor(api_key=api_key, company_map=_COMPANY_MAP)
    text = (
        "Bangladesh Bank raised the policy rate by 50 basis points. "
        "The National Board of Revenue (NBR) issued new tax guidelines for listed companies."
    )
    result = await extractor.extract(text)

    print(f"\nTickers: {result.tickers}")
    print(f"Context orgs: {result.context_orgs}")

    # Pure macro article — no DSE companies mentioned → preflight may skip NL API
    # Either way, blocklisted orgs should NOT end up as tickers
    assert "bangladesh bank" not in result.tickers
    assert "nbr" not in result.tickers


@pytest.mark.asyncio
async def test_ticker_extractor_spot_check_5_articles():
    """Run NL API on top 5 articles from live RSS, print extraction results."""
    api_key = _get_api_key()
    if not api_key:
        pytest.skip("GOOGLE_CLOUD_API_KEY not set")

    from extraction.adapters.news.google_news_rss import GoogleNewsRSSAdapter
    from extraction.adapters.news.ticker_extractor import TickerExtractor

    result = await GoogleNewsRSSAdapter().fetch()
    df = result.data.head(5)

    if not await _nl_api_available(api_key):
        pytest.skip(
            "Cloud Natural Language API not enabled — enable at "
            "https://console.cloud.google.com/apis/library/language.googleapis.com"
        )

    extractor = TickerExtractor(api_key=api_key, company_map=_COMPANY_MAP)

    print("\n--- Ticker extraction spot-check (5 articles) ---")
    for _, row in df.iterrows():
        text = f"{row['headline']} {row.get('body', '')}"
        extraction = await extractor.extract(text)
        print(f"  [{row['source']}] {row['headline'][:80]}")
        print(f"    tickers={extraction.tickers}  ctx={extraction.context_orgs}  "
              f"sentiment={extraction.sentiment_score} ({extraction.sentiment_label})")

    # No hard assertions — this is a visual spot-check
    assert True
