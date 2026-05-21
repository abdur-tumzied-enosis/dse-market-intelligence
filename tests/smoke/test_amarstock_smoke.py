"""
AmarStock smoke tests — no market-hours dependency, runs any time.

Confirmed endpoints (2026-05-21, no auth required):
  /LatestPrice/dbfd2587c77f      all-stock live prices + fundamentals
  /data/1981d726120d/{ticker}    per-stock rich detail (JSON)
  /Info/DSE                      market summary (DSEX indices)
  /info/Stocks                   company list (code, name, sector)
  /qoutes/3ace8d562de8/{ticker}  historical High/Low/Volume (~688 records)
  /MarketPrice/328338530b39/{t}  market depth (bid/ask)
  POST /data/download/CSV        daily full-market snapshot (OHLCV all stocks)

Run: python -m pytest tests/smoke/test_amarstock_smoke.py -v -s --no-cov
"""
from __future__ import annotations

import io
import json
import pickle
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import pytest

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"
FIXTURE_DIR.mkdir(exist_ok=True)

BASE = "https://www.amarstock.com"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; DSEIntelBot/1.0)"}

FUND_TICKERS = ["SQURPHARMA", "BRACBANK", "GP", "BATBC", "RENATA", "BEXIMCO"]


def _save(name: str, obj: Any) -> None:
    pkl_path = FIXTURE_DIR / f"amarstock_{name}_sample.pkl"
    with open(pkl_path, "wb") as f:
        pickle.dump(obj, f)

    if isinstance(obj, pd.DataFrame):
        meta: dict[str, Any] = {
            "columns": list(obj.columns),
            "shape": list(obj.shape),
            "dtypes": {c: str(t) for c, t in obj.dtypes.items()},
        }
    elif isinstance(obj, list) and obj and isinstance(obj[0], dict):
        meta = {"type": "list", "len": len(obj), "first_keys": list(obj[0].keys())}
    elif isinstance(obj, dict):
        meta = {"type": "dict", "keys": list(obj.keys())}
    else:
        meta = {"type": str(type(obj))}

    json_path = FIXTURE_DIR / f"amarstock_{name}_columns.json"
    json_path.write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    print(f"\n[SAVED] {pkl_path.name}  meta={json.dumps(meta, default=str)[:200]}")


# ---------------------------------------------------------------------------
# 1. All-stock live prices + fundamentals
# ---------------------------------------------------------------------------

async def test_latest_price_all_stocks():
    """GET /LatestPrice/dbfd2587c77f — all stocks, OHLCV + fundamentals."""
    async with httpx.AsyncClient(timeout=20, headers=HEADERS, follow_redirects=True) as c:
        resp = await c.get(f"{BASE}/LatestPrice/dbfd2587c77f")

    print(f"\nHTTP {resp.status_code}  len={len(resp.content)}")
    assert resp.status_code == 200
    raw = resp.json()
    assert isinstance(raw, list), f"expected list, got {type(raw)}"
    assert len(raw) > 200, f"too few items: {len(raw)}"

    first = raw[0]
    print(f"Records: {len(raw)}")
    print(f"Keys ({len(first)}): {list(first.keys())}")
    print(f"Sample: {json.dumps(first, default=str)[:400]}")

    _save("latest_price_all", raw)


# ---------------------------------------------------------------------------
# 2. Per-stock detail (fundamentals + news + technicals)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ticker", ["SQURPHARMA", "BRACBANK", "GP"])
async def test_stock_detail(ticker: str):
    """GET /data/1981d726120d/{ticker} — rich per-stock data."""
    async with httpx.AsyncClient(timeout=20, headers=HEADERS, follow_redirects=True) as c:
        resp = await c.get(f"{BASE}/data/1981d726120d/{ticker}")

    print(f"\n[{ticker}] HTTP {resp.status_code}  len={len(resp.content)}")
    assert resp.status_code == 200
    raw = resp.json()
    assert isinstance(raw, dict), f"expected dict, got {type(raw)}"

    print(f"Keys ({len(raw)}): {list(raw.keys())}")
    for k, v in raw.items():
        if not k.startswith("news") or "date" in k.lower():
            print(f"  {k!r}: {str(v)[:60]}")

    _save(f"stock_detail_{ticker.lower()}", raw)

    assert raw.get("Scrip") == ticker
    assert raw.get("EPS") is not None or raw.get("NAV") is not None, "no fundamental data"


# ---------------------------------------------------------------------------
# 3. Market summary (DSEX indices)
# ---------------------------------------------------------------------------

async def test_market_summary():
    """GET /Info/DSE — DSEX/DS30/DSES + advance/decline."""
    async with httpx.AsyncClient(timeout=10, headers=HEADERS, follow_redirects=True) as c:
        resp = await c.get(f"{BASE}/Info/DSE")

    assert resp.status_code == 200
    raw = resp.json()
    print(f"\n/Info/DSE: {json.dumps(raw, default=str)}")

    _save("market_summary", raw)

    assert "IndexValue" in raw, "missing IndexValue (DSEX)"
    assert "TotalVolume" in raw, "missing TotalVolume"
    assert "MarketStatus" in raw


# ---------------------------------------------------------------------------
# 4. Company list
# ---------------------------------------------------------------------------

async def test_company_list():
    """GET /info/Stocks — full company list with sector."""
    async with httpx.AsyncClient(timeout=10, headers=HEADERS, follow_redirects=True) as c:
        resp = await c.get(f"{BASE}/info/Stocks")

    assert resp.status_code == 200
    raw = resp.json()
    assert isinstance(raw, list) and len(raw) > 300, f"expected 300+ companies, got {len(raw)}"

    print(f"\n/info/Stocks: {len(raw)} companies")
    print(f"Keys: {list(raw[0].keys())}")
    print(f"Sample[0..3]: {raw[:3]}")

    _save("company_list", raw)


# ---------------------------------------------------------------------------
# 5. Historical High/Low/Volume
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ticker", ["SQURPHARMA", "GP", "BRACBANK"])
async def test_historical_quotes(ticker: str):
    """GET /qoutes/3ace8d562de8/{ticker} — ~688 records, MaxPrice/MinPrice/Volume."""
    async with httpx.AsyncClient(timeout=20, headers=HEADERS, follow_redirects=True) as c:
        resp = await c.get(f"{BASE}/qoutes/3ace8d562de8/{ticker}")

    assert resp.status_code == 200
    raw = resp.json()
    assert isinstance(raw, list) and len(raw) > 10

    first = raw[0]
    last = raw[-1]
    print(f"\n[{ticker}] records={len(raw)}")
    print(f"Keys: {list(first.keys())}")
    print(f"First (newest): {json.dumps(first, default=str)}")
    print(f"Last  (oldest): {json.dumps(last, default=str)}")

    df = pd.DataFrame(raw)
    _save(f"historical_{ticker.lower()}", df)

    assert "MaxPrice" in first
    assert "MinPrice" in first
    assert "Volume" in first


# ---------------------------------------------------------------------------
# 6. Market depth
# ---------------------------------------------------------------------------

async def test_market_depth():
    """GET /MarketPrice/328338530b39/{ticker} — bid/ask order book."""
    ticker = "GP"
    async with httpx.AsyncClient(timeout=10, headers=HEADERS, follow_redirects=True) as c:
        resp = await c.get(f"{BASE}/MarketPrice/328338530b39/{ticker}")

    assert resp.status_code == 200
    raw = resp.json()
    assert isinstance(raw, list)

    print(f"\n/MarketPrice/{ticker}: {len(raw)} rows")
    if raw:
        print(f"Keys: {list(raw[0].keys())}")
        print(f"Sample: {raw[:3]}")

    _save("market_depth_gp", raw)


# ---------------------------------------------------------------------------
# 7. CSV download (all stocks, single date)
# ---------------------------------------------------------------------------

async def test_csv_daily_snapshot():
    """POST /data/download/CSV — all-stock OHLCV for one date."""
    from datetime import date
    today = date.today().strftime("%Y-%m-%d")

    async with httpx.AsyncClient(timeout=30, headers=HEADERS, follow_redirects=True) as c:
        resp = await c.post(
            f"{BASE}/data/download/CSV",
            data={"QuotesType": "1", "date": today},
        )

    ct = resp.headers.get("content-type", "")
    assert resp.status_code == 200, f"HTTP {resp.status_code}"
    assert "csv" in ct or len(resp.content) > 100

    df = pd.read_csv(io.StringIO(resp.text))
    print(f"\nCSV daily snapshot ({today}): shape={df.shape}")
    print(f"Columns: {list(df.columns)}")
    print(df.head(3).to_string())

    _save("csv_daily_snapshot", df)

    assert len(df) > 100, f"too few rows: {len(df)}"
    assert "Close" in df.columns or "close" in df.columns.str.lower().tolist()


# ---------------------------------------------------------------------------
# 8. Adapter integration tests (use real adapter classes)
# ---------------------------------------------------------------------------

async def test_live_prices_adapter():
    """AmarStockLivePricesAdapter.fetch() returns normalized DataFrame."""
    from extraction.adapters.amarstock.live_prices import AmarStockLivePricesAdapter
    adapter = AmarStockLivePricesAdapter()
    result = await adapter.fetch()
    df = result.data
    print(f"\nAdapter live_prices: {df.shape}")
    print(df.dtypes)
    print(df.head(3).to_string())
    assert len(df) > 200
    assert "ticker" in df.columns
    assert "close" in df.columns


async def test_fundamentals_adapter():
    """AmarStockFundamentalsAdapter.fetch(ticker) returns normalized DataFrame."""
    from extraction.adapters.amarstock.fundamentals_scraper import AmarStockFundamentalsAdapter
    adapter = AmarStockFundamentalsAdapter(request_delay=0)
    result = await adapter.fetch("SQURPHARMA")
    df = result.data
    print(f"\nAdapter fundamentals SQURPHARMA: {df.shape}")
    print(df.T.to_string())
    assert len(df) == 1
    assert df.iloc[0]["ticker"] == "SQURPHARMA"
    assert df.iloc[0]["eps"] is not None


async def test_historical_adapter():
    """AmarStockCSVAdapter.fetch(ticker) returns normalized DataFrame."""
    from extraction.adapters.amarstock.csv_historical import AmarStockCSVAdapter
    adapter = AmarStockCSVAdapter(request_delay=0)
    result = await adapter.fetch("GP")
    df = result.data
    print(f"\nAdapter historical GP: {df.shape}")
    print(df.head(3).to_string())
    assert len(df) > 10
    assert "high" in df.columns
    assert "volume" in df.columns
