"""Data quality rules applied after every extraction."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pandas as pd


@dataclass
class QualityFailure:
    rule: str
    ticker: str | None
    detail: str
    severity: str  # "warning" | "error"


def _check_required_columns(df: pd.DataFrame, required: list[str], stream: str) -> list[QualityFailure]:
    missing = [c for c in required if c not in df.columns]
    if missing:
        return [QualityFailure(rule="missing_columns", ticker=None, detail=f"{stream}: {missing}", severity="error")]
    return []


def _check_price_range(df: pd.DataFrame, threshold_pct: float = 25.0) -> list[QualityFailure]:
    failures = []
    if "close" not in df.columns or "prev_close" not in df.columns:
        return failures
    for _, row in df.iterrows():
        close = row.get("close")
        prev = row.get("prev_close")
        if close is None or prev is None or prev == 0:
            continue
        change = abs((float(close) - float(prev)) / float(prev)) * 100
        if change > threshold_pct:
            failures.append(
                QualityFailure(
                    rule="price_spike",
                    ticker=row.get("ticker"),
                    detail=f"change {change:.1f}% > threshold {threshold_pct}%",
                    severity="warning",
                )
            )
    return failures


def _check_no_empty_dataframe(df: pd.DataFrame, stream: str) -> list[QualityFailure]:
    if len(df) == 0:
        return [QualityFailure(rule="empty_result", ticker=None, detail=f"{stream} returned 0 rows", severity="error")]
    return []


def _check_shareholding_sum(df: pd.DataFrame, tolerance: float = 1.0) -> list[QualityFailure]:
    """Warn when shareholding pct columns are present but don't sum to ~100."""
    _COLS = ["sponsor_pct", "govt_pct", "institution_pct", "foreign_pct", "public_pct"]
    present = [c for c in _COLS if c in df.columns]
    if len(present) < 2:
        return []
    failures = []
    for _, row in df.iterrows():
        vals = [row[c] for c in present if row[c] is not None and not (isinstance(row[c], float) and pd.isna(row[c]))]
        if not vals:
            continue
        total = sum(float(v) for v in vals)
        if abs(total - 100.0) > tolerance:
            failures.append(QualityFailure(
                rule="shareholding_sum",
                ticker=row.get("ticker"),
                detail=f"shareholding pct sum={total:.2f} (expected ~100)",
                severity="warning",
            ))
    return failures


def _check_eps_bounds(df: pd.DataFrame, lo: float = -1000.0, hi: float = 1000.0) -> list[QualityFailure]:
    """Warn when eps values fall outside the plausible range [lo, hi].

    ±1000 per design FR8 — wide enough for high-EPS outliers (RECKITTBEN ~165)
    while catching column-mapping mistakes (e.g. profit-mn landing in eps).
    """
    if "eps" not in df.columns:
        return []
    failures = []
    for _, row in df.iterrows():
        v = row.get("eps")
        if v is None or (isinstance(v, float) and pd.isna(v)):
            continue
        fv = float(v)
        if fv < lo or fv > hi:
            failures.append(QualityFailure(
                rule="eps_out_of_bounds",
                ticker=row.get("ticker"),
                detail=f"eps={fv} outside [{lo}, {hi}]",
                severity="warning",
            ))
    return failures


def _check_nav_bounds(df: pd.DataFrame, lo: float = -10000.0) -> list[QualityFailure]:
    """Warn when NAV per share is implausibly negative (design FR8: nav > -10000)."""
    if "nav" not in df.columns:
        return []
    failures = []
    for _, row in df.iterrows():
        v = row.get("nav")
        if v is None or (isinstance(v, float) and pd.isna(v)):
            continue
        if float(v) < lo:
            failures.append(QualityFailure(
                rule="nav_out_of_bounds",
                ticker=row.get("ticker"),
                detail=f"nav={float(v)} below {lo}",
                severity="warning",
            ))
    return failures


def _check_non_negative_prices(df: pd.DataFrame) -> list[QualityFailure]:
    failures = []
    price_cols = [c for c in ("open", "high", "low", "close", "ltp") if c in df.columns]
    for col in price_cols:
        neg_tickers = df.loc[df[col] < 0, "ticker"].tolist() if "ticker" in df.columns else []
        if neg_tickers:
            failures.append(
                QualityFailure(
                    rule="negative_price",
                    ticker=str(neg_tickers[:5]),
                    detail=f"{col} negative for {len(neg_tickers)} tickers",
                    severity="error",
                )
            )
    return failures


QUALITY_RULES: dict[str, list[str]] = {
    "live_prices": ["ticker", "close", "volume", "fetched_at"],
    "historical_ohlcv": ["ticker", "date", "open", "high", "low", "close", "volume"],
    "market_indices": ["index_name", "value", "change_pct", "fetched_at"],
    "fundamentals": ["ticker", "eps", "pe", "nav", "fetched_at"],
    "sector_performance": ["sector", "change_pct", "fetched_at"],
    "announcements": ["ticker", "category", "published_at"],
    "psn": ["ticker", "published_at", "headline"],
    "agm_dividends": ["ticker", "agm_date", "cash_div_pct"],
    "market_depth": ["ticker", "bid_price_1", "ask_price_1"],
    "news_en": ["headline", "published_at", "source"],
    "news_bn": ["headline", "published_at", "source"],
    "macro_policy_rate": ["indicator", "value", "period"],
    "macro_cpi": ["indicator", "value", "period"],
    "macro_usd_bdt": ["indicator", "value", "period"],
    "macro_gdp": ["indicator", "value", "period"],
    "macro_remittance": ["indicator", "value", "period"],
}


def run_quality_checks(
    df: pd.DataFrame,
    stream_name: str,
    price_spike_threshold: float = 25.0,
) -> list[QualityFailure]:
    failures: list[QualityFailure] = []
    required = QUALITY_RULES.get(stream_name, [])

    failures += _check_no_empty_dataframe(df, stream_name)
    if failures:
        return failures  # no point checking further if empty

    failures += _check_required_columns(df, required, stream_name)
    failures += _check_non_negative_prices(df)
    failures += _check_price_range(df, threshold_pct=price_spike_threshold)
    # Column-guarded — only active when shareholding/eps columns are present;
    # no effect on existing streams that lack these fields.
    failures += _check_shareholding_sum(df)
    failures += _check_eps_bounds(df)
    failures += _check_nav_bounds(df)

    return failures
