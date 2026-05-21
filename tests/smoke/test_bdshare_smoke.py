"""
bdshare smoke tests — must run during DSE market hours:
  Sun–Thu 10:00–14:30 BD time = 04:00–08:30 UTC

Run: make smoke-test
     python -m pytest tests/smoke/test_bdshare_smoke.py -v -s --no-cov

These tests hit the real DSE API. They record actual column names and
save fixture files so unit tests can run offline.
"""
from __future__ import annotations

import json
import pickle
from pathlib import Path

import pandas as pd
import pytest

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"
FIXTURE_DIR.mkdir(exist_ok=True)


def _save_fixture(name: str, df: pd.DataFrame | list) -> None:
    path = FIXTURE_DIR / f"bdshare_{name}_sample.pkl"
    with open(path, "wb") as f:
        pickle.dump(df, f)
    # Also save column names as JSON for quick reference
    if isinstance(df, pd.DataFrame):
        cols = {"columns": list(df.columns), "shape": list(df.shape), "sample": df.head(2).to_dict()}
    else:
        cols = {"type": "list", "len": len(df), "df_shapes": [list(d.shape) for d in df if isinstance(d, pd.DataFrame)]}
    json_path = FIXTURE_DIR / f"bdshare_{name}_columns.json"
    json_path.write_text(json.dumps(cols, indent=2, default=str))
    print(f"\n[SAVED] {path.name} — columns: {cols}")


@pytest.fixture(scope="module")
def bd():
    try:
        import bdshare as _bd
        return _bd
    except ImportError:
        pytest.skip("bdshare not installed — run: uv pip install bdshare==1.2.1")


def test_get_current_trade_data(bd):
    """Live prices for all DSE-listed stocks."""
    df = bd.get_current_trade_data()
    assert df is not None, "returned None"
    assert len(df) > 100, f"too few rows: {len(df)}"
    print(f"\nget_current_trade_data: {df.shape} | columns: {list(df.columns)}")
    print(df.head(2).to_string())
    _save_fixture("current_trade_data", df)


def test_get_hist_data(bd):
    """Historical OHLCV. get_hist_data deprecated → use get_historical_data(start, end, code)."""
    df = bd.get_historical_data("2024-01-01", "2024-12-31", "SQURPHARMA")
    assert df is not None
    assert len(df) > 50, f"too few rows: {len(df)}"
    print(f"\nget_hist_data: {df.shape} | columns: {list(df.columns)}")
    print(df.head(2).to_string())
    _save_fixture("hist_data", df)


def test_get_basic_hist_data(bd):
    """TA-friendly historical data. get_basic_hist_data deprecated → use get_basic_historical_data."""
    df = bd.get_basic_historical_data("2024-01-01", "2024-12-31", "SQURPHARMA")
    assert df is not None
    assert len(df) > 50, f"too few rows: {len(df)}"
    print(f"\nget_basic_historical_data: {df.shape} | columns: {list(df.columns)}")
    print(df.head(2).to_string())
    _save_fixture("basic_hist_data", df)


def test_get_market_info(bd):
    """DSEX / DS30 / DSES index snapshot."""
    df = bd.get_market_info()
    assert df is not None
    print(f"\nget_market_info: {df.shape} | columns: {list(df.columns)}")
    print(df.to_string())
    _save_fixture("market_info", df)


def test_get_latest_pe(bd):
    """Latest PE + EPS by sector."""
    df = bd.get_latest_pe()
    assert df is not None
    print(f"\nget_latest_pe: {df.shape} | columns: {list(df.columns)}")
    print(df.head(3).to_string())
    _save_fixture("latest_pe", df)


def test_get_company_info_pharma(bd):
    """Company info for pharma sector — list of DataFrames."""
    result = bd.get_company_info("SQURPHARMA")
    assert result is not None
    assert isinstance(result, list)
    print(f"\nget_company_info(SQURPHARMA): {len(result)} DataFrames")
    for i, df in enumerate(result):
        if isinstance(df, pd.DataFrame):
            print(f"  df[{i}]: shape={df.shape} | columns={list(df.columns)}")
            print(df.head(2).to_string())
    _save_fixture("company_info_squrpharma", result)


def test_get_company_info_banking(bd):
    """Company info for banking sector — structure may differ from pharma."""
    result = bd.get_company_info("BRACBANK")
    assert result is not None
    print(f"\nget_company_info(BRACBANK): {len(result)} DataFrames")
    for i, df in enumerate(result):
        if isinstance(df, pd.DataFrame):
            print(f"  df[{i}]: shape={df.shape} | columns={list(df.columns)}")
    _save_fixture("company_info_bracbank", result)


def test_get_sector_performance(bd):
    df = bd.get_sector_performance()
    assert df is not None
    print(f"\nget_sector_performance: {df.shape} | columns: {list(df.columns)}")
    print(df.head(3).to_string())
    _save_fixture("sector_performance", df)


def test_get_top_gainers_losers(bd):
    df = bd.get_top_gainers_losers()
    assert df is not None
    print(f"\nget_top_gainers_losers: {df.shape} | columns: {list(df.columns)}")
    print(df.head(3).to_string())
    _save_fixture("top_gainers_losers", df)


def test_get_corporate_announcements(bd):
    df = bd.get_corporate_announcements()
    assert df is not None
    print(f"\nget_corporate_announcements: {df.shape} | columns: {list(df.columns)}")
    print(df.head(2).to_string())
    _save_fixture("corporate_announcements", df)


def test_get_price_sensitive_news(bd):
    df = bd.get_price_sensitive_news()
    assert df is not None
    print(f"\nget_price_sensitive_news: {df.shape} | columns: {list(df.columns)}")
    print(df.head(2).to_string())
    _save_fixture("price_sensitive_news", df)


def test_get_agm_news(bd):
    df = bd.get_agm_news()
    assert df is not None
    print(f"\nget_agm_news: {df.shape} | columns: {list(df.columns)}")
    print(df.head(2).to_string())
    _save_fixture("agm_news", df)


def test_get_market_depth_data(bd):
    result = bd.get_market_depth_data("GP")
    assert result is not None
    if isinstance(result, list):
        print(f"\nget_market_depth_data(GP): list of {len(result)} items")
        for i, item in enumerate(result):
            if isinstance(item, pd.DataFrame):
                print(f"  item[{i}]: shape={item.shape} | columns={list(item.columns)}")
                print(item.head(3).to_string())
    elif isinstance(result, pd.DataFrame):
        print(f"\nget_market_depth_data(GP): DataFrame {result.shape}")
        print(result.to_string())
    _save_fixture("market_depth_gp", result)
