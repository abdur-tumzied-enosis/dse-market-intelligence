from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
import pandas as pd
from bs4 import BeautifulSoup

from extraction.base import AdapterError, AdapterResult, BaseAdapter
from extraction.normalizers import to_decimal

SECTOR_PE_URL = "https://www.dsebd.org/sectoral_PE.php"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.dsebd.org/",
}

# Confirmed columns (2026-05-23): #, Sector Name, Sectoral Median P/E
# 18 sectors total.


class DSEDirectSectorPEAdapter(BaseAdapter):
    """
    DSE official site — sectoral median P/E ratios.
    URL: https://www.dsebd.org/sectoral_PE.php
    Method: HTTP + BeautifulSoup (no JS). Confirmed working 2026-05-23.
    Returns 18 rows: one per DSE sector.
    """
    name = "dse_direct_sector_pe"
    priority = 1
    timeout_seconds = 20

    def _parse_html(self, html: str) -> pd.DataFrame:
        soup = BeautifulSoup(html, "lxml")
        table = None
        for t in soup.find_all("table"):
            ths = [th.get_text(strip=True).upper() for th in t.find_all("th")]
            if any("SECTOR" in h for h in ths) or any("P/E" in h for h in ths):
                table = t
                break
        if table is None:
            # Fallback: any table with 3 cols that has numeric-looking data
            for t in soup.find_all("table"):
                tds = t.find_all("td")
                if len(tds) >= 6:
                    table = t
                    break
        if table is None:
            raise ValueError("sector PE table not found")

        rows = []
        for tr in table.find_all("tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cells) >= 2:
                rows.append(cells)
        if not rows:
            raise ValueError("sector PE table has no data rows")
        return pd.DataFrame(rows)

    def normalize(self, raw: pd.DataFrame) -> pd.DataFrame:
        df = raw.copy()
        # Raw columns: 0=rank, 1=sector_name (or link text), 2=median_pe.
        # Some rows may have 2 cols if rank merged; be flexible.
        if df.shape[1] >= 3:
            df.columns = ["rank", "sector_name", "median_pe"] + list(df.columns[3:])
        elif df.shape[1] == 2:
            df.columns = ["sector_name", "median_pe"]
        else:
            raise ValueError(f"unexpected column count: {df.shape[1]}")

        # Emit the canonical sector_performance schema (matches the sector_pe
        # table, the quality rule, and the bdshare sibling adapter). The DSE
        # sectoral_PE page carries only the median P/E; change_pct and
        # market_cap_bdt have no source here and are enriched from
        # companies/stock_prices by job_sector_pe before insert.
        out = pd.DataFrame()
        out["sector"]         = df["sector_name"].str.strip()
        out["pe"]             = df["median_pe"].apply(to_decimal)
        out["change_pct"]     = None
        out["market_cap_bdt"] = None
        out["fetched_at"]     = datetime.now(timezone.utc)
        out["source"]         = self.name
        # Drop header-like rows where sector is empty or pe is null.
        out = out.loc[out["sector"].str.len() > 0]
        out = out.loc[out["pe"].notna()]
        return out.reset_index(drop=True)

    async def fetch(self, **kwargs: Any) -> AdapterResult:
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, headers=HEADERS, follow_redirects=True) as c:
                resp = await c.get(SECTOR_PE_URL)
                resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise AdapterError(self.name, f"HTTP {exc.response.status_code}", retryable=True) from exc
        except Exception as exc:
            raise AdapterError(self.name, f"request failed: {exc}", retryable=True) from exc

        try:
            raw = self._parse_html(resp.text)
        except Exception as exc:
            raise AdapterError(self.name, f"parse failed: {exc}", retryable=False) from exc

        if raw.empty:
            raise AdapterError(self.name, "empty table", retryable=True)

        try:
            normalized = self.normalize(raw)
        except Exception as exc:
            raise AdapterError(self.name, f"normalize failed: {exc}", retryable=False) from exc

        if normalized.empty:
            raise AdapterError(self.name, "no valid sector rows after normalize", retryable=True)

        return AdapterResult(
            data=normalized,
            source_name=self.name,
            fetched_at=datetime.now(timezone.utc),
            quality="ok",
            records=len(normalized),
            raw_sample=raw.iloc[0].to_dict() if not raw.empty else {},
        )

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10, headers=HEADERS, follow_redirects=True) as c:
                resp = await c.get(SECTOR_PE_URL)
                return resp.status_code == 200 and b"P/E" in resp.content
        except Exception:
            return False
