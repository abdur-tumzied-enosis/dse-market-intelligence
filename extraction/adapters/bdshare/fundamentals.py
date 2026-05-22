from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from extraction.base import AdapterError, AdapterResult, BaseAdapter
from extraction.normalizers import normalize_ticker, to_decimal


def _latest_year_row(df: pd.DataFrame) -> pd.Series | None:
    """Return the last row where col[0] is a 4-digit year (2000–2030)."""
    for i in range(len(df) - 1, -1, -1):
        row = df.iloc[i]
        try:
            y = int(str(row.iloc[0]).strip())
            if 2000 <= y <= 2030:
                return row
        except (ValueError, TypeError):
            pass
    return None


def _safe_cell(row: pd.Series, idx: int) -> str:
    try:
        v = str(row.iloc[idx]).strip()
        return "" if v in ("-", "nan", "None", "") else v
    except IndexError:
        return ""


def _parse_shareholding_row(df: pd.DataFrame) -> dict[str, Any]:
    """
    Parse a (1, 5) shareholding table whose cells look like
    'Sponsor/Director: 43.59', 'Govt: 0.00', 'Institute: 14.05', etc.
    """
    result: dict[str, Any] = {}
    if df.shape[0] == 0:
        return result
    row = df.iloc[0]
    for cell in row:
        text = str(cell)
        m = re.search(r"(?:Sponsor|Director)", text, re.I)
        if m:
            v = re.search(r"[\d.]+$", text.split(":")[-1].strip())
            if v:
                result["sponsor_pct"] = to_decimal(v.group())
            continue
        if re.search(r"\bInstitut", text, re.I):
            v = re.search(r"[\d.]+$", text.split(":")[-1].strip())
            if v:
                result["institution_pct"] = to_decimal(v.group())
            continue
        if re.search(r"\bPublic|\bGeneral", text, re.I):
            v = re.search(r"[\d.]+$", text.split(":")[-1].strip())
            if v:
                result["public_pct"] = to_decimal(v.group())
            continue
        if re.search(r"\bForeign", text, re.I):
            v = re.search(r"[\d.]+$", text.split(":")[-1].strip())
            if v:
                result["foreign_pct"] = to_decimal(v.group())

    return result


class BDShareCompanyInfoAdapter(BaseAdapter):
    """
    bdshare get_company_info(ticker) — patched to return last 15 tables
    from DSE displayCompany page (see extraction/adapters/bdshare/__init__.py).

    Expected table layout (last 15 tables as of 2026):
      [-15..-9] : earlier template / summary tables
      [-9]      : (8,14) EPS / NAV historical by year
      [-8]      : (9,10) P/E / Dividend historical by year
      [-7]      : (2,2)  company URLs
      [-6]      : (7,2)  basic info + shareholding text
      [-5..-3]  : (1,5)  shareholding snapshots (oldest → newest)
      [-2]      : (9,3)  operational / loan status
      [-1]      : (10,3) contact info
      [last]    : (1,1)  disclaimer
    """

    name = "bdshare_company_info"
    priority = 2
    timeout_seconds = 20

    def normalize(self, raw: list[pd.DataFrame], ticker: str = "") -> pd.DataFrame:
        result: dict[str, Any] = {
            "ticker":     normalize_ticker(ticker),
            "fetched_at": datetime.now(timezone.utc),
            "source":     self.name,
        }

        for df in raw:
            if df is None or len(df) == 0:
                continue

            ncols = df.shape[1]

            # ---- EPS / NAV table: 14 columns, year rows ---- #
            if ncols == 14 and result.get("eps") is None:
                row = _latest_year_row(df)
                if row is not None:
                    # col 5 = EPS - Continuing Operations, Basic, Original
                    # col 8 = NAV Per Share, Original
                    eps_v = _safe_cell(row, 5)
                    nav_v = _safe_cell(row, 8)
                    if eps_v:
                        result["eps"] = to_decimal(eps_v)
                    if nav_v:
                        result["nav"] = to_decimal(nav_v)
                continue

            # ---- P/E table: 10 columns, year rows ---- #
            if ncols == 10 and result.get("pe") is None:
                row = _latest_year_row(df)
                if row is not None:
                    # col 5 = P/E using EPS Continuing Basic Original
                    pe_v = _safe_cell(row, 5)
                    if pe_v:
                        result["pe"] = to_decimal(pe_v)
                continue

            # ---- Shareholding snapshot: (1, 5) ---- #
            if df.shape == (1, 5):
                # Take the last snapshot seen (most recent)
                parsed = _parse_shareholding_row(df)
                if parsed:
                    result.update(parsed)
                continue

        return pd.DataFrame([result])

    async def fetch(self, ticker: str, **kwargs: Any) -> AdapterResult:
        try:
            import bdshare as bd
        except ImportError as exc:
            raise AdapterError(self.name, "bdshare not installed", retryable=False) from exc

        ticker = normalize_ticker(ticker)
        try:
            raw: list[pd.DataFrame] = bd.get_company_info(ticker)
        except Exception as exc:
            raise AdapterError(self.name, f"get_company_info({ticker}) failed: {exc}") from exc

        if not raw:
            raise AdapterError(self.name, f"no data for {ticker}", retryable=True)

        normalized = self.normalize(raw, ticker=ticker)
        return AdapterResult(
            data=normalized,
            source_name=self.name,
            fetched_at=datetime.now(timezone.utc),
            quality="partial",
            records=len(normalized),
            raw_sample={f"df_{i}": (df.iloc[0].to_dict() if len(df) > 0 else {}) for i, df in enumerate(raw)},
        )

    async def health_check(self) -> bool:
        try:
            import bdshare as bd
            raw = bd.get_company_info("SQURPHARMA")
            return raw is not None and len(raw) > 0
        except Exception:
            return False
