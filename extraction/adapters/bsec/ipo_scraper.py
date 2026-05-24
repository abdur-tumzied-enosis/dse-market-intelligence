from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
import pandas as pd
from bs4 import BeautifulSoup

from extraction.base import AdapterError, AdapterResult, BaseAdapter

_BASE = "https://sec.gov.bd"
_URLS: dict[str, str] = {
    "fixed":        f"{_BASE}/home/ipofixed",
    "bookbuilding": f"{_BASE}/home/ipobook",
}
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://sec.gov.bd/",
}

_DATE_RE   = re.compile(r"(\d{1,2})\s+([A-Za-z]+),\s*(\d{4})")
_AMOUNT_RE = re.compile(r"([\d.]+)")


def _parse_date(raw: str) -> str | None:
    m = _DATE_RE.search(raw.strip())
    if not m:
        return None
    try:
        return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%d %b %Y").date().isoformat()
    except ValueError:
        pass
    try:
        return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%d %B %Y").date().isoformat()
    except ValueError:
        return None


def _parse_amount(raw: str) -> Decimal | None:
    m = _AMOUNT_RE.search(raw.strip())
    if not m:
        return None
    try:
        return Decimal(m.group(1))
    except InvalidOperation:
        return None


def _parse_table(html: str, ipo_type: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    table = None
    for t in soup.find_all("table"):
        ths = [th.get_text(strip=True) for th in t.find_all("th")]
        if "Name of the Company" in ths:
            table = t
            break
    if table is None:
        raise ValueError(f"IPO table not found on {ipo_type} page")

    rows: list[dict] = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 7:
            continue
        company = tds[1].get_text(strip=True)
        if not company:
            continue

        prospectus_url: str | None = None
        if len(tds) > 7:
            link = tds[7].find("a")
            if link and link.get("href"):
                href = link["href"]
                prospectus_url = href if href.startswith("http") else f"{_BASE}{href}"

        rows.append({
            "company_name":   company,
            "ipo_type":       ipo_type,
            "consent_date":   _parse_date(tds[2].get_text(strip=True)),
            "sub_open_date":  _parse_date(tds[3].get_text(strip=True)),
            "sub_close_date": _parse_date(tds[4].get_text(strip=True)),
            "nrb_close_date": _parse_date(tds[5].get_text(strip=True)),
            "amount_crore":   _parse_amount(tds[6].get_text(strip=True)),
            "prospectus_url": prospectus_url,
        })
    return rows


class BsecIPOAdapter(BaseAdapter):
    """
    BSEC official site — IPO filings (fixed price + bookbuilding).

    Sources:
        https://sec.gov.bd/home/ipofixed   — ~137 records, 2008-present
        https://sec.gov.bd/home/ipobook    — ~19 records, 2022-present

    Static HTML — httpx + BeautifulSoup, no JS required.
    """
    name = "bsec_ipo"
    priority = 1
    timeout_seconds = 30

    def normalize(self, raw: pd.DataFrame) -> pd.DataFrame:
        df = raw.copy()
        df["fetched_at"] = datetime.now(timezone.utc)
        df["source"] = self.name
        return df

    async def fetch(self, **kwargs: Any) -> AdapterResult:
        all_rows: list[dict] = []
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            headers=_HEADERS,
            follow_redirects=True,
        ) as client:
            for ipo_type, url in _URLS.items():
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise AdapterError(
                        self.name, f"HTTP {exc.response.status_code} on {url}", retryable=True
                    ) from exc
                except Exception as exc:
                    raise AdapterError(self.name, f"request failed: {exc}", retryable=True) from exc

                try:
                    rows = _parse_table(resp.text, ipo_type)
                except Exception as exc:
                    raise AdapterError(
                        self.name, f"parse failed ({ipo_type}): {exc}", retryable=False
                    ) from exc

                all_rows.extend(rows)

        if not all_rows:
            raise AdapterError(self.name, "no rows parsed from either IPO page", retryable=True)

        raw_df = pd.DataFrame(all_rows)
        normalized = self.normalize(raw_df)

        return AdapterResult(
            data=normalized,
            source_name=self.name,
            fetched_at=datetime.now(timezone.utc),
            quality="ok",
            records=len(normalized),
            raw_sample=all_rows[0],
        )

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(
                timeout=10, headers=_HEADERS, follow_redirects=True
            ) as c:
                resp = await c.get(_URLS["fixed"])
                return resp.status_code == 200 and b"Name of the Company" in resp.content
        except Exception:
            return False
