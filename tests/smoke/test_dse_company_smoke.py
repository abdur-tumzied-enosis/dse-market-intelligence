"""
DSE company page smoke test — fetches displayCompany.php for structurally
distinct tickers and saves raw HTML pickles for offline unit tests.

Run: docker exec dse_worker sh -c "cd /app/.worktrees/fundamentals-enrichment && python -m pytest tests/smoke/test_dse_company_smoke.py -v -s --no-cov"
"""
from __future__ import annotations

import pickle
from pathlib import Path

import httpx
import pytest

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"
FIXTURE_DIR.mkdir(exist_ok=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Referer": "https://old.dsebd.org/",
}
URL = "https://old.dsebd.org/displayCompany.php?name={ticker}"

# Layout-distinct pages: bank (EPS-CO columns, 3 shareholding rows), MNC/telecom,
# pharma (Jun year-end), Z-category (sparse data).
TICKERS = ["CITYBANK", "GP", "SQURPHARMA", "FAMILYTEX"]


@pytest.mark.parametrize("ticker", TICKERS)
async def test_company_page_fetch_and_save(ticker: str) -> None:
    # dsebd.org serves an incomplete cert chain — verification fails everywhere,
    # not just in Docker. Public-data scrape; verify=False is the accepted tradeoff.
    async with httpx.AsyncClient(timeout=30, headers=HEADERS, follow_redirects=True, verify=False) as c:
        resp = await c.get(URL.format(ticker=ticker))

    print(f"\n{ticker}: HTTP {resp.status_code} len={len(resp.text)}")
    assert resp.status_code == 200
    assert len(resp.text) > 50_000, "page suspiciously small"

    assert "Share Holding Percentage" in resp.text

    path = FIXTURE_DIR / f"dse_company_{ticker}.pkl"
    with open(path, "wb") as f:
        pickle.dump(resp.text, f)
    print(f"[SAVED] {path.name}")
