"""
Macro adapter smoke tests — Bangladesh Bank HTML + World Bank API.

Hits live endpoints. Run:
    python -m pytest tests/smoke/test_macro_smoke.py -v -s --no-cov

Bangladesh Bank tests may fail if bb.org.bd is unreachable or has restructured.
World Bank tests are more stable (public API, well-maintained).
"""
from __future__ import annotations

import pickle
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"
FIXTURE_DIR.mkdir(exist_ok=True)

_BB_BASE = "https://www.bb.org.bd"
_BB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Referer": "https://www.bb.org.bd/",
}

_BB_URLS = {
    "policy_rate": f"{_BB_BASE}/monetaryactivity/monetarypolicy.php",
    "cpi":         f"{_BB_BASE}/econdata/inflation.php",
    "usd_bdt":     f"{_BB_BASE}/econdata/exchangerate.php",
    "remittance":  f"{_BB_BASE}/econdata/remittanceinward.php",
}


def _save(name: str, obj: object) -> None:
    path = FIXTURE_DIR / f"macro_{name}.pkl"
    with open(path, "wb") as f:
        pickle.dump(obj, f)
    print(f"\n[SAVED] {path.name}")


# ---------------------------------------------------------------------------
# 1. Bangladesh Bank reachability
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("indicator,url", list(_BB_URLS.items()))
async def test_bb_page_reachable(indicator: str, url: str):
    """Each Bangladesh Bank indicator page returns HTTP 200."""
    async with httpx.AsyncClient(timeout=30, headers=_BB_HEADERS, follow_redirects=True) as c:
        resp = await c.get(url)

    print(f"\n[{indicator}] HTTP {resp.status_code}  len={len(resp.content)}")
    assert resp.status_code == 200, f"{indicator} page returned {resp.status_code}"
    assert len(resp.content) > 1000, f"{indicator} page suspiciously small: {len(resp.content)} bytes"

    _save(f"bb_{indicator}_raw_html", resp.text[:3000])


# ---------------------------------------------------------------------------
# 2. Bangladesh Bank adapter — fetch + parse
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("indicator", list(_BB_URLS.keys()))
async def test_bb_adapter_fetch(indicator: str):
    """BangladeshBankAdapter.fetch() returns valid macro DataFrame."""
    from extraction.adapters.macro.bangladesh_bank import BangladeshBankAdapter

    adapter = BangladeshBankAdapter(indicator=indicator)
    result = await adapter.fetch()
    df = result.data

    print(f"\n[bb_{indicator}] rows={len(df)}")
    print(df.head(3).to_string())
    _save(f"bb_{indicator}_df", df)

    assert len(df) > 0, f"bb_{indicator}: empty DataFrame"
    assert "indicator" in df.columns
    assert "value" in df.columns
    assert "period" in df.columns
    assert "unit" in df.columns
    assert df["indicator"].iloc[0] == indicator
    assert all(isinstance(v, Decimal) for v in df["value"].dropna())
    assert result.quality == "ok"


# ---------------------------------------------------------------------------
# 3. World Bank API — reachability + parse
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("indicator_code,expected_name", [
    ("FP.CPI.TOTL.ZG", "cpi"),
    ("NY.GDP.MKTP.KD.ZG", "gdp"),
    ("PA.NUS.FCRF", "usd_bdt"),
    ("BX.TRF.PWKR.CD.DT", "remittance"),
])
async def test_worldbank_adapter_fetch(indicator_code: str, expected_name: str):
    """WorldBankAdapter.fetch() returns valid macro DataFrame."""
    from extraction.adapters.macro.worldbank import WorldBankAdapter

    adapter = WorldBankAdapter(indicator=indicator_code, priority=2)
    result = await adapter.fetch()
    df = result.data

    print(f"\n[worldbank/{indicator_code}] rows={len(df)}")
    print(df.head(3).to_string())
    _save(f"wb_{indicator_code.replace('.', '_')}_df", df)

    assert len(df) > 0, f"worldbank {indicator_code}: empty DataFrame"
    assert "indicator" in df.columns
    assert "value" in df.columns
    assert "period" in df.columns
    assert df["indicator"].iloc[0] == expected_name
    assert all(isinstance(v, Decimal) for v in df["value"].dropna())
    assert result.quality == "ok"


# ---------------------------------------------------------------------------
# 4. WorldBankAdapter.health_check
# ---------------------------------------------------------------------------

async def test_worldbank_health_check():
    """WorldBankAdapter.health_check() returns True for a valid indicator."""
    from extraction.adapters.macro.worldbank import WorldBankAdapter

    adapter = WorldBankAdapter(indicator="FP.CPI.TOTL.ZG")
    ok = await adapter.health_check()
    assert ok, "World Bank health check failed"


# ---------------------------------------------------------------------------
# 5. Registry — all macro streams wired with adapters
# ---------------------------------------------------------------------------

def test_registry_macro_streams():
    """All 5 macro streams present in STREAMS with at least 1 adapter each."""
    from extraction.registry import STREAMS

    macro_streams = [
        "macro_policy_rate",
        "macro_cpi",
        "macro_usd_bdt",
        "macro_gdp",
        "macro_remittance",
    ]
    for stream_name in macro_streams:
        assert stream_name in STREAMS, f"stream '{stream_name}' missing from registry"
        stream = STREAMS[stream_name]
        assert len(stream.adapters) > 0, f"stream '{stream_name}' has no adapters"
        print(f"\n  {stream_name}: {[a.name for a in stream.adapters]}")

    print("\n[OK] all macro streams present in registry")


# ---------------------------------------------------------------------------
# 6. Failover — WorldBank takes over when BB fails
# ---------------------------------------------------------------------------

async def test_macro_cpi_failover():
    """macro_cpi DataStream falls over to BB adapter if WorldBank (priority 1) fails."""
    from unittest.mock import AsyncMock, patch

    from extraction.base import AdapterError
    from extraction.registry import STREAMS

    cpi_stream = STREAMS["macro_cpi"]

    # adapters[0] = WorldBankAdapter (priority 1), adapters[1] = BangladeshBankAdapter (priority 2)
    with patch.object(
        cpi_stream.adapters[0],  # WorldBankAdapter
        "fetch",
        new_callable=AsyncMock,
        side_effect=AdapterError("worldbank_fp_cpi_totl_zg", "simulated WB failure", retryable=True),
    ):
        with patch.object(
            cpi_stream.adapters[1],  # BangladeshBankAdapter
            "fetch",
            new_callable=AsyncMock,
            side_effect=AdapterError("bb_cpi", "simulated BB CAPTCHA", retryable=False),
        ):
            from extraction.base import AllAdaptersFailedError
            try:
                await cpi_stream.fetch()
                assert False, "expected AllAdaptersFailedError"
            except AllAdaptersFailedError as exc:
                assert "worldbank_fp_cpi_totl_zg" in str(exc)
                assert "bb_cpi" in str(exc)
                print(f"\n[OK] both adapters failed as expected: {exc}")
