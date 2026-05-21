"""
DSE Direct smoke tests — verifies dsebd.org endpoints are reachable and parseable.

All tests hit the live site. Run selectively; skip Playwright tests if browser not installed.

Run all:       python -m pytest tests/smoke/test_dse_direct_smoke.py -v -s --no-cov
Run fast only: python -m pytest tests/smoke/test_dse_direct_smoke.py -v -s --no-cov -m "not playwright"
"""
from __future__ import annotations

import json
import pickle
from pathlib import Path

import httpx
import pytest

from extraction.adapters.dse_direct.announcements import (
    DSEDirectAnnouncementsAdapter,
    DSEDirectPSNAdapter,
)
from extraction.adapters.dse_direct.live_prices import DSEDirectLivePricesAdapter

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"
FIXTURE_DIR.mkdir(exist_ok=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Referer": "https://www.dsebd.org/",
}
BASE = "https://www.dsebd.org"


def _save(name: str, obj: object) -> None:
    path = FIXTURE_DIR / f"dse_direct_{name}.pkl"
    with open(path, "wb") as f:
        pickle.dump(obj, f)
    print(f"\n[SAVED] {path.name}")


# ---------------------------------------------------------------------------
# 1. Live prices HTML table
# ---------------------------------------------------------------------------

async def test_live_prices_page_reachable():
    """GET /latest_share_price_scroll_l.php returns 200 with stock table HTML."""
    async with httpx.AsyncClient(timeout=30, headers=HEADERS, follow_redirects=True) as c:
        resp = await c.get(f"{BASE}/latest_share_price_scroll_l.php")

    print(f"\nHTTP {resp.status_code}  len={len(resp.content)}")
    assert resp.status_code == 200
    assert len(resp.content) > 1000, "page suspiciously small"
    # At minimum the page should contain a table tag
    assert b"<table" in resp.content or b"<TABLE" in resp.content

    _save("live_prices_raw_html", resp.text[:5000])


async def test_live_prices_parse():
    """DSEDirectLivePricesAdapter parses live prices table correctly."""
    adapter = DSEDirectLivePricesAdapter()
    result = await adapter.fetch()
    df = result.data

    print(f"\nLive prices: {df.shape}")
    print(df.dtypes)
    print(df.head(3).to_string())

    _save("live_prices_df", df)

    assert len(df) > 50, f"too few rows: {len(df)}"
    assert "ticker" in df.columns
    assert "close" in df.columns
    assert df["ticker"].iloc[0] == df["ticker"].iloc[0].upper()


# ---------------------------------------------------------------------------
# 2. Corporate announcements
# ---------------------------------------------------------------------------

async def test_announcements_page_reachable():
    """GET /display_news.php (today's news) returns 200."""
    async with httpx.AsyncClient(timeout=25, headers=HEADERS, follow_redirects=True) as c:
        resp = await c.get(f"{BASE}/display_news.php")

    print(f"\nHTTP {resp.status_code}  len={len(resp.content)}")
    assert resp.status_code == 200
    assert len(resp.content) > 10_000, "page too small — unexpected response"

    _save("announcements_raw_html", resp.text[:5000])


@pytest.mark.playwright
async def test_announcements_parse():
    """DSEDirectAnnouncementsAdapter.fetch() returns normalized DataFrame (Playwright)."""
    adapter = DSEDirectAnnouncementsAdapter()
    result = await adapter.fetch()
    df = result.data

    print(f"\nAnnouncements: {df.shape}")
    print(df.head(3).to_string())

    _save("announcements_df", df)

    assert len(df) > 0
    assert "ticker" in df.columns


# ---------------------------------------------------------------------------
# 3. Price-sensitive news
# ---------------------------------------------------------------------------

async def test_psn_page_reachable():
    """GET /news_archive_7days.php (last 7 days) returns 200."""
    async with httpx.AsyncClient(timeout=25, headers=HEADERS, follow_redirects=True) as c:
        resp = await c.get(f"{BASE}/news_archive_7days.php")

    print(f"\nHTTP {resp.status_code}  len={len(resp.content)}")
    assert resp.status_code == 200
    assert len(resp.content) > 10_000

    _save("psn_raw_html", resp.text[:5000])


@pytest.mark.playwright
async def test_psn_parse():
    """DSEDirectPSNAdapter.fetch() returns normalized DataFrame (Playwright)."""
    adapter = DSEDirectPSNAdapter()
    result = await adapter.fetch()
    df = result.data

    print(f"\nPSN: {df.shape}")
    print(df.head(3).to_string())

    assert len(df) > 0
    assert "ticker" in df.columns


# ---------------------------------------------------------------------------
# 4. Playwright — market depth (requires: playwright install chromium)
# ---------------------------------------------------------------------------

@pytest.mark.playwright
async def test_depth_playwright():
    """
    DSEDirectDepthPlaywrightAdapter — returns price stats (partial quality).

    Full bid/ask order book requires DSE-Mobile / M-invest auth (confirmed 2026-05-21).
    Adapter returns open/close/high/low/trades from Price Statistics table instead.
    """
    from extraction.adapters.dse_direct.depth import DSEDirectDepthPlaywrightAdapter

    adapter = DSEDirectDepthPlaywrightAdapter()
    result = await adapter.fetch(ticker="GP")
    df = result.data

    print(f"\nDepth GP (partial — auth required for full bid/ask): {df.shape}")
    print(df.T.to_string())

    _save("depth_gp_df", df)

    assert len(df) == 1
    assert result.quality == "partial"
    # bid/ask columns exist in schema but are None (require auth)
    assert "bid_price_1" in df.columns
    assert "ask_price_1" in df.columns
    # Price stats should be present
    price_cols = {"open", "close", "high", "low", "prev_close", "trades"}
    present = price_cols.intersection(df.columns)
    assert present, f"no price stat columns found; got {list(df.columns)}"


# ---------------------------------------------------------------------------
# 5. PDF reports — STUB (DSE does not host company annual reports)
# ---------------------------------------------------------------------------

def test_pdf_adapter_raises_clear_error():
    """
    DSEDirectPDFAdapter.fetch() raises AdapterError(retryable=False) with BSEC guidance.

    DSE (dsebd.org) does not host company annual report PDFs (confirmed 2026-05-21).
    Company filings are on BSEC (sec.gov.bd). BsecPDFAdapter needed for this stream.
    """
    import asyncio
    from extraction.adapters.dse_direct.pdf_reports import DSEDirectPDFAdapter
    from extraction.base import AdapterError

    adapter = DSEDirectPDFAdapter()

    async def run():
        return await adapter.fetch(ticker="BRACBANK")

    with pytest.raises(AdapterError) as exc_info:
        asyncio.run(run())

    err = exc_info.value
    assert not err.retryable
    assert "BSEC" in str(err) or "sec.gov.bd" in str(err), (
        f"Error message should mention BSEC: {err}"
    )


# ---------------------------------------------------------------------------
# 6. Registry smoke — all DSE Direct streams importable
# ---------------------------------------------------------------------------

def test_registry_dse_direct_streams():
    """Registry includes dse_direct adapters in correct streams."""
    from extraction.registry import STREAMS

    live = STREAMS["live_prices"]
    adapter_names = [a.name for a in live.adapters]
    assert "dse_direct_live_prices" in adapter_names

    ann = STREAMS["announcements"]
    adapter_names = [a.name for a in ann.adapters]
    assert "dse_direct_announcements" in adapter_names

    depth = STREAMS["market_depth"]
    adapter_names = [a.name for a in depth.adapters]
    assert "dse_direct_depth" in adapter_names

    pdf = STREAMS["annual_reports_pdf"]
    adapter_names = [a.name for a in pdf.adapters]
    assert "dse_direct_pdf" in adapter_names

    print("\n[OK] all dse_direct adapters present in registry")
