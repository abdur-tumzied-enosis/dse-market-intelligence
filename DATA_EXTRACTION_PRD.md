# DSE Stock Intelligence Platform — Data Extraction PRD

**Version:** 1.0  
**Status:** Draft  
**Owner:** Data Engineering  

---

## 0. TL;DR

Build a **plug-and-play data extraction layer** where every data type has 2–3 ranked source adapters.
If source A goes down, the system automatically falls back to B, then C — with zero code changes
and full observability. No single source failure should degrade the user-facing product.

---

## 1. Problem Statement

The current architecture names data sources but treats each as a single point of failure.
When AmarStock changes its API (no SLA), or DSE restructures its HTML, the entire data
type goes dark. The unknowns:

- Which source is most reliable per data type?
- What exact fields does each source return, and do they match the DB schema?
- How do we normalize across sources (column names, types, timezones differ)?
- How do we detect a source has gone stale or broken before users notice?
- Where does each piece of data actually live in the source (URL, selector, API path)?

This PRD eliminates all of those unknowns.

---

## 2. Goals

| # | Goal |
|---|------|
| G1 | Every data type has ≥ 2 working source adapters before launch |
| G2 | Failover is automatic — no human intervention for source switches |
| G3 | Every adapter has a live health check callable in < 5 s |
| G4 | Normalized output schema is identical regardless of which source ran |
| G5 | Every ingested row is tagged with its source + adapter version |
| G6 | New source can be plugged in by writing one adapter class (no other changes) |

### Non-Goals

- Real-time WebSocket feeds (DSE does not publish them)
- Options / derivatives data (DSE does not have them)
- International market data
- Alternative data (satellite, credit card spend, etc.) — Phase 2+

---

## 3. Complete Data Inventory

### 3.1 Data Types × Sources Matrix

| Data Type | Frequency | bdshare | AmarStock API | DSE Direct (dsebd.org) | Playwright Scrape | Other |
|---|---|---|---|---|---|---|
| Live prices (all stocks) | 15 min | `get_current_trade_data()` ✅ | `/latest-share-price` ✅ | `dsebd.org/latest_share_price.php` ✅ | Fallback only | — |
| Historical OHLCV | On demand | `get_hist_data()` ✅ | CSV bulk download ✅ | dsebd.org share price archive | Playwright for gaps | — |
| DSEX / DS30 / DSES index | 15 min | `get_market_info()` ✅ | `/index-summary` ✅ | `dsebd.org/dseX_index.php` | — | — |
| Market depth (order book) | 15 min | `get_market_depth_data(sym)` ✅ | Not available ❌ | Playwright required | Playwright ✅ | — |
| Top gainers / losers | 15 min | `get_top_gainers_losers()` ✅ | `/top-gainer-loser` | dsebd.org | — | — |
| Sector performance | Daily | `get_sector_performance()` ✅ | Not available ❌ | dsebd.org sector page | — | — |
| PE ratios (all stocks) | Weekly | `get_latest_pe()` ✅ | Embedded in stock page | dsebd.org | — | — |
| Company fundamentals (EPS, NAV, Beta, Float) | Weekly | `get_company_info(sym)` ⚠️ partial | AmarStock stock-chart page scrape ✅ | dsebd.org company page | Playwright fallback | — |
| Shareholding structure | Weekly | `get_company_info(sym)` ⚠️ partial | AmarStock scrape | dsebd.org | Playwright | — |
| Dividend history | Weekly | `get_agm_news()` ⚠️ events only | AmarStock dividend page | dsebd.org dividend tab | — | — |
| Corporate announcements | 2 hr | `get_corporate_announcements()` ✅ | Not available ❌ | `dsebd.org/latest_news.php` ✅ | — | — |
| Price-sensitive news (PSN) | 2 hr | `get_price_sensitive_news()` ✅ | Not available ❌ | `dsebd.org/price_sensitive_news.php` ✅ | — | — |
| AGM / EGM notices | Daily | `get_agm_news()` ✅ | Not available ❌ | dsebd.org AGM page | — | — |
| Annual report PDFs | Yearly | Not available ❌ | Not available ❌ | dsebd.org company filings | Playwright download ✅ | Manual upload |
| BD financial news (English) | Daily | Not available ❌ | Not available ❌ | Not available ❌ | Playwright / RSS | The Financial Express, Daily Star |
| BD financial news (Bengali) | Daily | Not available ❌ | Not available ❌ | Not available ❌ | Playwright / RSS | Prothom Alo, bdnews24 |
| Macro: Policy rate | Monthly | Not available ❌ | Not available ❌ | Not available ❌ | Bangladesh Bank site | World Bank API |
| Macro: CPI / Inflation | Monthly | Not available ❌ | Not available ❌ | Not available ❌ | BBS (stats.gov.bd) | World Bank API |
| Macro: GDP growth | Quarterly | Not available ❌ | Not available ❌ | Not available ❌ | BBS | World Bank API |
| Macro: USD/BDT rate | Daily | Not available ❌ | Not available ❌ | Not available ❌ | Bangladesh Bank | Open exchange rates API |
| Macro: Remittance / FDI | Monthly | Not available ❌ | Not available ❌ | Not available ❌ | Bangladesh Bank | World Bank API |
| IPO / rights offer filings | Ad-hoc | Not available ❌ | Not available ❌ | BSEC bsec.gov.bd ✅ | Playwright | — |
| Circuit breaker status | Daily | Not available ❌ | Not available ❌ | dsebd.org | — | — |

**Legend:** ✅ Reliable, documented  ⚠️ Partial / untested  ❌ Not available from this source

---

## 4. Source Profiles

### 4.1 bdshare (pip install bdshare)

```
Type:          Python library — scrapes dsebd.org + DSE API
Version:       1.2.1 (2026-02-22)
Maintainer:    Raisul Islam (open source, MIT)
Built-in:      Retries (3×), exponential backoff, caching (configurable TTL),
               rate limiting (5 calls/s sliding window), BDShareError exceptions,
               fallback URLs (primary + alternate DSE endpoint)
Risk:          Library not maintained by DSE — breaks if dsebd.org HTML changes
               Maintainer is a single individual (bus factor = 1)
Best for:      Live prices, historical OHLCV, market indices, basic PE, 
               corporate announcements, news
NOT useful for: Annual reports, macro data, detailed fundamentals (EPS audited,
                shareholding %, beta), BD news sites
```

**All columns returned (confirmed):**

| Function | Key Columns |
|---|---|
| `get_current_trade_data()` | symbol, ltp, high, low, close, ycp, change, trade, value, volume |
| `get_hist_data()` | date, open, high, low, close, volume, value, trade |
| `get_basic_hist_data()` | date, open, high, low, close, volume (TA-library compatible order) |
| `get_market_info()` | date, dsex, dses, ds30, total_value, total_volume, total_trade |
| `get_latest_pe()` | symbol, pe_ratio, eps, close_price, sector |
| `get_company_info()` | list of DataFrames: profile, shareholding, financials (structure varies) |
| `get_sector_performance()` | sector, change_pct, market_cap, pe, total_stocks |
| `get_top_gainers_losers()` | symbol, ltp, change, change_pct, volume |
| `get_corporate_announcements()` | date, company, ticker, category, details |
| `get_price_sensitive_news()` | date, company, ticker, headline, details |
| `get_agm_news()` | date, company, ticker, cash_div_pct, stock_div_pct, agm_date |
| `get_market_depth_data(sym)` | buy_qty, buy_price, sell_price, sell_qty (5 levels each) |

---

### 4.2 AmarStock API

```
Base URL:      https://api.amarstock.com
Type:          Unofficial REST API (no documented SLA, no auth required currently)
Format:        JSON
Risk:          Can add auth or change endpoints without notice
               No official status page
Best for:      Live prices (backup), historical bulk CSV download (primary for initial load),
               detailed per-stock fundamentals page (PE, NAV, EPS, shareholding)
```

**Confirmed endpoints from architecture doc:**

| Endpoint | Method | Returns |
|---|---|---|
| `/latest-share-price` | GET | All stocks live: TRADING_CODE, OPEN, HIGH, LOW, LTP, YCP, VOLUME, VALUE, TRADE |
| `/stock-chart/{ticker}` | GET (HTML page, scrape) | EPS (audited/unaudited/Q), PE (audited/unaudited), NAV, market cap, free float %, beta, shareholding %, 52wk high/low, dividend history |
| CSV download | GET (file) | Historical OHLCV per ticker or bulk |

> **Note:** AmarStock stock-chart pages are HTML — require BeautifulSoup scraping, NOT a JSON API call. Fragile. Must be tested with Playwright for structure validation.

---

### 4.3 DSE Direct (dsebd.org)

```
Type:          Official source — HTML pages + some JSON endpoints
Auth:          None
Risk:          Low risk for data accuracy (official), moderate risk for scraping
               (HTML changes without notice, some pages require JS rendering)
Best for:      Corporate announcements, PSN, AGM news, annual report PDF links,
               circuit breaker status, IPO filings, BSEC circulars
Playwright:    Required for pages that load data via JavaScript (market depth,
               some announcement listings)
```

**Key URLs:**

| Data | URL |
|---|---|
| Live prices | `dsebd.org/latest_share_price.php` |
| DSEX index | `dsebd.org/dseX_index.php` |
| Corporate announcements | `dsebd.org/latest_news.php` |
| Price-sensitive news | `dsebd.org/price_sensitive_news.php` |
| AGM notices | `dsebd.org/agm_egm_notice.php` |
| Company filings / annual reports | `dsebd.org/companyinfo.php?reqType=financials&cname={ticker}` |
| Sector PE | `dsebd.org/sector_wise_summary.php` |
| Circuit breaker list | `dsebd.org/circuit_breaker.php` |
| IPO subscriptions | `dsebd.org/ipo_subscription.php` |

---

### 4.4 News Sources (Playwright / RSS)

| Source | Language | URL Pattern | Method | Tickers extracted by |
|---|---|---|---|---|
| The Financial Express BD | English | `thefinancialexpress.com.bd/stock-market/` | RSS + scrape | LLM (haiku) NER |
| The Daily Star Business | English | `thedailystar.net/business/` | RSS + scrape | LLM NER |
| TBS News | English | `tbsnews.net/economy/stocks/` | RSS ✅ | LLM NER |
| Prothom Alo Business | Bengali | `prothomalo.com/business/` | Scrape | LLM NER |
| bdnews24 Business | English | `bdnews24.com/business/` | RSS + scrape | LLM NER |
| Dhaka Tribune Business | English | `dhakatribune.com/business/` | RSS | LLM NER |
| Samakal Business | Bengali | `samakal.com/business/` | Scrape | LLM NER |

---

### 4.5 Bangladesh Bank (Macro Data)

```
Base URL:      https://www.bb.org.bd
Type:          Official — HTML tables + downloadable Excel/PDF
Risk:          Stable structure, low scraping risk — updated monthly
               Some data only in PDF table (requires extraction)
```

| Indicator | URL / Location | Format | Frequency |
|---|---|---|---|
| Policy rate (repo) | `bb.org.bd/monetaryactivity/monetarypolicy.php` | HTML table | Monthly |
| Inflation (CPI) | `bb.org.bd/econdata/inflation.php` | HTML table | Monthly |
| USD/BDT exchange rate | `bb.org.bd/econdata/exchangerate.php` | HTML / downloadable Excel | Daily |
| Forex reserves | `bb.org.bd/econdata/foreignexchange.php` | HTML | Weekly |
| Remittance | `bb.org.bd/econdata/remittanceinward.php` | HTML | Monthly |

---

### 4.6 World Bank API (Macro Fallback)

```
Base URL:    https://api.worldbank.org/v2/country/BD/indicator/{code}?format=json
Auth:        None
Indicators:
  GDP growth (annual %):  NY.GDP.MKTP.KD.ZG
  Inflation (CPI):        FP.CPI.TOTL.ZG
  FDI inflows:           BX.KLT.DINV.CD.WD
Lag:         ~3–6 months behind (annual data)
Use:         Backup only for Bangladesh Bank outages
```

---

### 4.7 BSEC (Bangladesh Securities and Exchange Commission)

```
URL:     https://www.sec.gov.bd (or bsec.gov.bd)
Data:    IPO approvals, rights offer filings, BSEC circulars / directives
Method:  Playwright scrape (site is JS-heavy)
Frequency: Ad-hoc / weekly check
```

---

## 5. Plug-and-Play Adapter Architecture

### 5.1 Design Principles

```
Every data type = one DataStream
Each DataStream has N adapters, ordered by priority
Adapters are interchangeable: same input, same output schema
The DataStream engine tries adapter[0], on failure tries adapter[1], etc.
Health checks run every 6 hours and reorder adapter priority dynamically
```

### 5.2 Class Hierarchy

```python
# extraction/base.py

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
import pandas as pd

@dataclass
class AdapterResult:
    data:         pd.DataFrame
    source_name:  str          # e.g. "bdshare_v1.2.1"
    fetched_at:   datetime
    quality:      str          # "ok" | "suspect" | "partial"
    records:      int
    raw_sample:   dict         # first row before normalization — for debugging


class BaseAdapter(ABC):
    name: str                  # unique ID, versioned: "amarstock_live_v2"
    priority: int              # lower = preferred
    timeout_seconds: int = 15
    
    @abstractmethod
    async def fetch(self, **kwargs) -> AdapterResult:
        """Fetch data. Raise AdapterError on any failure."""
    
    @abstractmethod
    async def health_check(self) -> bool:
        """Return True if source is reachable and returning valid structure."""
    
    @abstractmethod
    def normalize(self, raw: Any) -> pd.DataFrame:
        """Transform source-specific output to canonical schema."""


class AdapterError(Exception):
    def __init__(self, adapter_name: str, cause: Exception):
        self.adapter_name = adapter_name
        self.cause = cause
        super().__init__(f"{adapter_name} failed: {cause}")
```

### 5.3 DataStream Engine

```python
# extraction/stream.py

class DataStream:
    """
    Manages a list of adapters for one data type.
    Tries each in priority order, returns first success.
    Tracks failures and updates health scores.
    """
    
    def __init__(self, stream_id: str, adapters: list[BaseAdapter]):
        self.stream_id = stream_id
        self.adapters = sorted(adapters, key=lambda a: a.priority)
    
    async def fetch(self, **kwargs) -> AdapterResult:
        errors = []
        for adapter in self.adapters:
            try:
                result = await adapter.fetch(**kwargs)
                await self._record_success(adapter.name)
                return result
            except AdapterError as e:
                errors.append(e)
                await self._record_failure(adapter.name, str(e.cause))
                log.warning(f"[{self.stream_id}] {adapter.name} failed, trying next")
                continue
        
        # All adapters failed
        await alert_ops(
            level="CRITICAL",
            message=f"ALL adapters failed for stream {self.stream_id}",
            details={"errors": [str(e) for e in errors]}
        )
        raise AllAdaptersFailedError(self.stream_id, errors)
    
    async def health_check_all(self) -> dict[str, bool]:
        results = {}
        for adapter in self.adapters:
            results[adapter.name] = await adapter.health_check()
        return results
```

### 5.4 Adapter Registry (complete list)

```python
# extraction/registry.py

STREAMS = {

    # ── LIVE PRICES ──────────────────────────────────────────────────────────
    "live_prices": DataStream("live_prices", [
        BDShareLivePricesAdapter(priority=1),        # bdshare.get_current_trade_data()
        AmarStockLivePricesAdapter(priority=2),      # api.amarstock.com/latest-share-price
        DSEDirectLivePricesAdapter(priority=3),      # Playwright → dsebd.org/latest_share_price.php
    ]),

    # ── HISTORICAL OHLCV ─────────────────────────────────────────────────────
    "historical_ohlcv": DataStream("historical_ohlcv", [
        BDShareHistoricalAdapter(priority=1),        # bdshare.get_hist_data()
        AmarStockCSVAdapter(priority=2),             # bulk CSV download
        DSEDirectHistoricalAdapter(priority=3),      # Playwright → dsebd.org archive
    ]),

    # ── MARKET INDICES ───────────────────────────────────────────────────────
    "market_indices": DataStream("market_indices", [
        BDShareMarketInfoAdapter(priority=1),        # bdshare.get_market_info()
        AmarStockIndexAdapter(priority=2),           # api.amarstock.com/index-summary
        DSEDirectIndexAdapter(priority=3),           # Playwright → dsebd.org/dseX_index.php
    ]),

    # ── FUNDAMENTALS (PE, EPS, NAV, beta, float, shareholding) ──────────────
    "fundamentals": DataStream("fundamentals", [
        AmarStockFundamentalsAdapter(priority=1),    # scrape stock-chart/{ticker}
        BDShareCompanyInfoAdapter(priority=2),       # bdshare.get_company_info()
        DSEDirectFundamentalsAdapter(priority=3),    # Playwright → dsebd.org company page
    ]),

    # ── SECTOR PERFORMANCE ───────────────────────────────────────────────────
    "sector_performance": DataStream("sector_performance", [
        BDShareSectorAdapter(priority=1),            # bdshare.get_sector_performance()
        DSEDirectSectorAdapter(priority=2),          # Playwright → dsebd.org sector page
    ]),

    # ── CORPORATE ANNOUNCEMENTS ──────────────────────────────────────────────
    "announcements": DataStream("announcements", [
        BDShareAnnouncementsAdapter(priority=1),     # bdshare.get_corporate_announcements()
        DSEDirectAnnouncementsAdapter(priority=2),   # dsebd.org/latest_news.php
        PlaywrightAnnouncementsAdapter(priority=3),  # Playwright fallback (JS-rendered)
    ]),

    # ── PRICE-SENSITIVE NEWS ─────────────────────────────────────────────────
    "psn": DataStream("psn", [
        BDSharePSNAdapter(priority=1),               # bdshare.get_price_sensitive_news()
        DSEDirectPSNAdapter(priority=2),             # dsebd.org/price_sensitive_news.php
    ]),

    # ── AGM / DIVIDEND EVENTS ────────────────────────────────────────────────
    "agm_dividends": DataStream("agm_dividends", [
        BDShareAGMAdapter(priority=1),               # bdshare.get_agm_news()
        DSEDirectAGMAdapter(priority=2),             # dsebd.org/agm_egm_notice.php
    ]),

    # ── MARKET DEPTH (ORDER BOOK) ────────────────────────────────────────────
    "market_depth": DataStream("market_depth", [
        BDShareDepthAdapter(priority=1),             # bdshare.get_market_depth_data()
        PlaywrightDepthAdapter(priority=2),          # Playwright → dsebd.org (JS-rendered)
    ]),

    # ── ANNUAL REPORT PDFs ───────────────────────────────────────────────────
    "annual_reports_pdf": DataStream("annual_reports_pdf", [
        DSEDirectPDFAdapter(priority=1),             # Playwright → dsebd.org filings tab
        ManualUploadAdapter(priority=2),             # admin manual upload
    ]),

    # ── BD FINANCIAL NEWS ───────────────────────────────────────────────────
    "news_en": DataStream("news_en", [
        TBSNewsRSSAdapter(priority=1),               # tbsnews.net RSS ✅ (most reliable)
        FinancialExpressAdapter(priority=2),         # RSS + scrape
        DailyStarBusinessAdapter(priority=3),        # RSS
        DhakaTribuneAdapter(priority=4),             # RSS
    ]),

    "news_bn": DataStream("news_bn", [
        ProthomAloAdapter(priority=1),               # Playwright (no public RSS)
        SamakalAdapter(priority=2),                  # Playwright
    ]),

    # ── MACRO DATA ───────────────────────────────────────────────────────────
    "macro_policy_rate": DataStream("macro_policy_rate", [
        BangladeshBankPolicyRateAdapter(priority=1), # bb.org.bd HTML scrape
        WorldBankAdapter(priority=2, indicator="FR.INR.RINR"),  # fallback (lagged)
    ]),

    "macro_cpi": DataStream("macro_cpi", [
        BangladeshBankCPIAdapter(priority=1),        # bb.org.bd/econdata/inflation.php
        WorldBankAdapter(priority=2, indicator="FP.CPI.TOTL.ZG"),
    ]),

    "macro_usd_bdt": DataStream("macro_usd_bdt", [
        BangladeshBankFXAdapter(priority=1),         # bb.org.bd exchange rate
        OpenExchangeRatesAdapter(priority=2),        # openexchangerates.org (free tier)
    ]),

    "macro_gdp": DataStream("macro_gdp", [
        BangladeshBankGDPAdapter(priority=1),        # BBS / bb.org.bd GDP release
        WorldBankAdapter(priority=2, indicator="NY.GDP.MKTP.KD.ZG"),
    ]),

    "macro_remittance": DataStream("macro_remittance", [
        BangladeshBankRemittanceAdapter(priority=1), # bb.org.bd/econdata/remittanceinward
        WorldBankAdapter(priority=2, indicator="BX.TRF.PWKR.CD.DT"),
    ]),

    # ── IPO / BSEC FILINGS ──────────────────────────────────────────────────
    "ipo_filings": DataStream("ipo_filings", [
        BSECPlaywrightAdapter(priority=1),           # Playwright → bsec.gov.bd
        DSEDirectIPOAdapter(priority=2),             # dsebd.org/ipo_subscription.php
    ]),
}
```

---

## 6. Canonical Schemas (Normalized Output)

Every adapter must return a DataFrame matching these exact column names and types.
No adapter-specific column names survive past the `normalize()` call.

### 6.1 Live Price Record

```python
{
    "ticker":      str,       # "SQURPHARMA" — always uppercase
    "ts":          datetime,  # UTC — convert from BD time (UTC+6)
    "open":        Decimal,
    "high":        Decimal,
    "low":         Decimal,
    "close":       Decimal,   # LTP for live
    "ycp":         Decimal,   # yesterday close
    "volume":      int,
    "value":       Decimal,   # BDT millions
    "trades":      int,
    # Lineage
    "source":      str,       # "bdshare_v1.2.1"
    "ingested_at": datetime,
    "quality_flag":str,       # "ok" | "suspect"
}
```

### 6.2 Historical OHLCV Record

Same as live price but `ts` is end-of-day date (not intraday).

### 6.3 Fundamental Record

```python
{
    "ticker":           str,
    "recorded_at":      date,
    "eps_audited":      Optional[Decimal],
    "eps_unaudited":    Optional[Decimal],
    "eps_q1":           Optional[Decimal],
    "eps_q2":           Optional[Decimal],
    "eps_q3":           Optional[Decimal],
    "pe_audited":       Optional[Decimal],
    "pe_unaudited":     Optional[Decimal],
    "nav":              Optional[Decimal],
    "nav_price_ratio":  Optional[Decimal],
    "market_cap":       Optional[int],      # BDT
    "free_float_pct":   Optional[Decimal],
    "beta":             Optional[Decimal],
    "div_directors":    Optional[Decimal],  # shareholding %
    "div_govt":         Optional[Decimal],
    "div_institution":  Optional[Decimal],
    "div_foreign":      Optional[Decimal],
    "div_public":       Optional[Decimal],
    "week52_high":      Optional[Decimal],
    "week52_low":       Optional[Decimal],
    "source":           str,
    "ingested_at":      datetime,
    "quality_flag":     str,
}
```

### 6.4 Announcement Record

```python
{
    "ticker":        str,
    "published_at":  datetime,  # UTC
    "category":      str,       # "CORPORATE" | "PSN" | "AGM" | "DIVIDEND"
    "headline":      str,
    "detail":        Optional[str],
    "source_url":    Optional[str],
    "source":        str,
    "ingested_at":   datetime,
}
```

### 6.5 Macro Indicator Record

```python
{
    "indicator":    str,         # "CPI" | "POLICY_RATE" | "USD_BDT" | "GDP_GROWTH" etc.
    "recorded_at":  date,
    "value":        Decimal,
    "unit":         str,         # "percent" | "bdt_per_usd" | "bdt_millions"
    "source":       str,
    "ingested_at":  datetime,
}
```

---

## 7. Playwright Integration

Playwright is the **last-resort and PDF-discovery adapter**. It handles:
1. JS-rendered pages that BeautifulSoup/requests cannot parse
2. Pages requiring cookie acceptance or simple interaction
3. PDF link discovery and download from DSE company pages
4. AmarStock stock-chart page validation (detect HTML structure changes)

### 7.1 When to use Playwright vs requests+BS4

| Condition | Use |
|---|---|
| Static HTML, no JS required | requests + BeautifulSoup |
| Page loads data via XHR/fetch after DOM ready | Playwright |
| Need to click tabs, accordions, pagination | Playwright |
| Downloading files (PDF annual reports) | Playwright |
| Health-checking page structure is intact | Playwright headless snapshot |
| Volume scraping (350 tickers × weekly) | requests + BS4 (Playwright too slow) |

### 7.2 Playwright Adapter Base

```python
# extraction/adapters/playwright_base.py

from playwright.async_api import async_playwright, Page

class PlaywrightAdapter(BaseAdapter):
    headless: bool = True
    
    async def get_page(self, url: str, wait_for: str = None) -> Page:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self.headless)
            page = await browser.new_page()
            await page.goto(url, wait_until="networkidle", timeout=30000)
            if wait_for:
                await page.wait_for_selector(wait_for, timeout=15000)
            return page
    
    async def health_check(self) -> bool:
        try:
            page = await self.get_page(self.health_check_url)
            text = await page.text_content("body")
            return self.health_check_keyword in text
        except Exception:
            return False
```

### 7.3 Annual Report PDF Discovery

```python
# extraction/adapters/dse_pdf_adapter.py

class DSEDirectPDFAdapter(PlaywrightAdapter):
    name = "dse_direct_pdf_v1"
    priority = 1
    health_check_url = "https://dsebd.org"
    health_check_keyword = "Dhaka Stock Exchange"

    async def fetch(self, ticker: str) -> AdapterResult:
        url = f"https://dsebd.org/companyinfo.php?reqType=financials&cname={ticker}"
        page = await self.get_page(url, wait_for="table.table-bordered")
        
        # Find all PDF links in the annual report section
        pdf_links = await page.eval_on_selector_all(
            "a[href$='.pdf']",
            "elements => elements.map(e => ({text: e.innerText, href: e.href}))"
        )
        
        # Filter: only annual reports (look for "Annual Report" in link text)
        annual_reports = [
            {"url": link["href"], "label": link["text"], "ticker": ticker}
            for link in pdf_links
            if "annual" in link["text"].lower() or "report" in link["text"].lower()
        ]
        
        df = pd.DataFrame(annual_reports)
        return AdapterResult(
            data=df,
            source_name=self.name,
            fetched_at=datetime.utcnow(),
            quality="ok" if len(df) > 0 else "suspect",
            records=len(df),
            raw_sample=annual_reports[0] if annual_reports else {}
        )
```

---

## 8. Source Health Monitoring

### 8.1 Health Check Schedule

```
Every 6 hours → run health_check_all() on every stream
Record results in source_health table
Alert if primary adapter fails health check for 2 consecutive runs
Auto-promote backup adapter to priority=1 if primary fails 3× in a row
```

### 8.2 Source Health Table

```sql
CREATE TABLE source_health (
    id            SERIAL PRIMARY KEY,
    adapter_name  TEXT NOT NULL,
    stream_id     TEXT NOT NULL,
    checked_at    TIMESTAMPTZ DEFAULT NOW(),
    is_healthy    BOOLEAN,
    latency_ms    INTEGER,
    error_msg     TEXT,
    structure_hash TEXT   -- hash of page structure — detect silent HTML changes
);

CREATE INDEX ON source_health (adapter_name, checked_at DESC);
```

### 8.3 Structure Change Detection (AmarStock / dsebd.org)

Silent changes — page loads, returns 200, but HTML structure has changed — are the
hardest failure mode. Detect with structure hashing:

```python
async def compute_structure_hash(url: str) -> str:
    """
    Hash the CSS selector tree of a page's key data tables.
    If hash changes between runs, the scraper likely needs an update.
    """
    page = await get_page(url)
    # Extract all table headers as a stable fingerprint
    headers = await page.eval_on_selector_all(
        "table th", "els => els.map(e => e.innerText.trim())"
    )
    fingerprint = "|".join(sorted(headers))
    return hashlib.md5(fingerprint.encode()).hexdigest()
```

Alert ops when `structure_hash` changes — do NOT silently serve bad data.

---

## 9. bdshare Integration Details

bdshare is the fastest path to working data. Use it as primary for everything it covers.

### 9.1 Recommended Usage Pattern

```python
# extraction/adapters/bdshare_adapters.py

import bdshare as bs
from bdshare import BDShare, BDShareError

class BDShareLivePricesAdapter(BaseAdapter):
    name = "bdshare_live_v1"
    priority = 1
    
    async def fetch(self, ticker: str = None) -> AdapterResult:
        try:
            with BDShare() as bd:
                if ticker:
                    raw = bd.get_current_trades(ticker)
                else:
                    raw = bs.get_current_trade_data()
            
            df = self.normalize(raw)
            return AdapterResult(
                data=df,
                source_name=self.name,
                fetched_at=datetime.utcnow(),
                quality="ok",
                records=len(df),
                raw_sample=raw.iloc[0].to_dict() if len(raw) > 0 else {}
            )
        except BDShareError as e:
            raise AdapterError(self.name, e)
    
    def normalize(self, raw: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame({
            "ticker":      raw["symbol"].str.upper(),
            "ts":          datetime.utcnow(),    # live data — timestamp is now
            "open":        pd.to_numeric(raw["open"],   errors="coerce"),
            "high":        pd.to_numeric(raw["high"],   errors="coerce"),
            "low":         pd.to_numeric(raw["low"],    errors="coerce"),
            "close":       pd.to_numeric(raw["ltp"],    errors="coerce"),   # LTP = last traded
            "ycp":         pd.to_numeric(raw["ycp"],    errors="coerce"),
            "volume":      pd.to_numeric(raw["volume"], errors="coerce").astype("Int64"),
            "value":       pd.to_numeric(raw["value"],  errors="coerce"),
            "trades":      pd.to_numeric(raw["trade"],  errors="coerce").astype("Int64"),
            "source":      self.name,
            "ingested_at": datetime.utcnow(),
            "quality_flag":"ok",
        })
    
    async def health_check(self) -> bool:
        try:
            df = bs.get_current_trade_data()
            return len(df) >= 100   # expect 300+ stocks; 100 is minimum signal
        except Exception:
            return False
```

### 9.2 bdshare Column Mapping (all adapters)

| Canonical Field | bdshare column | Notes |
|---|---|---|
| ticker | symbol | `.str.upper()` |
| close (live) | ltp | Last Traded Price |
| close (historical) | close | |
| ycp | ycp | Yesterday Close Price |
| volume | volume | integer |
| value | value | BDT millions |
| trades | trade | integer |
| pe_ratio | pe_ratio | from get_latest_pe() |
| eps | eps | from get_latest_pe() |
| sector | sector | from get_latest_pe() |

---

## 10. Freshness SLAs

| Data Type | Maximum Acceptable Staleness | Alert Threshold |
|---|---|---|
| Live prices | 20 minutes | > 20 min during market hours |
| DSEX index | 20 minutes | > 20 min during market hours |
| Market depth | 30 minutes | > 30 min during market hours |
| Corporate announcements | 3 hours | > 4 hours |
| Price-sensitive news | 3 hours | > 4 hours |
| Fundamentals (PE, NAV, EPS) | 8 days | > 10 days |
| Shareholding % | 30 days | > 35 days |
| Sector performance | 25 hours | > 30 hours |
| BD news (English) | 4 hours | > 6 hours |
| BD news (Bengali) | 8 hours | > 12 hours |
| Macro: USD/BDT | 25 hours | > 30 hours |
| Macro: Policy rate | 35 days | > 40 days |
| Macro: CPI | 35 days | > 40 days |
| Macro: GDP | 100 days | > 120 days |
| Annual report PDFs | 90 days | > 120 days |

---

## 11. Data Quality Rules (per stream)

```python
QUALITY_RULES = {
    "live_prices": [
        # At least 300 of ~350 stocks present
        lambda r: r["stocks_received"] >= 300,
        # No single stock price changed > 10% in one tick (DSE circuit breaker = 10%)
        lambda r: r["max_pct_change_single_tick"] <= 10.5,
        # DSEX value is non-zero
        lambda r: r["dsex_value"] > 0,
        # Timestamp within last 30 minutes
        lambda r: r["latest_ts"] >= datetime.utcnow() - timedelta(minutes=30),
    ],
    "historical_ohlcv": [
        # high >= low for all rows
        lambda r: r["high_lt_low_count"] == 0,
        # close within [low, high]
        lambda r: r["close_out_of_range_count"] == 0,
        # no future dates
        lambda r: r["future_date_count"] == 0,
        # no zero or negative prices
        lambda r: r["invalid_price_count"] == 0,
    ],
    "fundamentals": [
        # PE between 0 and 500 (outlier check)
        lambda r: r["pe_outlier_count"] == 0,
        # EPS not flipping sign more than once in 3 years (data error signal)
        lambda r: r["eps_sign_flip_count"] < 5,
        # NAV > 0 for all non-Z-category stocks
        lambda r: r["negative_nav_count"] == 0,
    ],
    "announcements": [
        # All records have a ticker
        lambda r: r["missing_ticker_count"] == 0,
        # No records older than 1 year (sanity check on date parsing)
        lambda r: r["old_record_count"] == 0,
    ],
}
```

---

## 12. Implementation Phases

### Phase 1 — Core Data (Week 1–2)

**Goal:** Live prices + historical OHLCV + market indices working with full fallback chain.

```
[ ] Install and test bdshare 1.2.1 against all methods
    → Document actual column names returned (may differ from README)
    → Note any fields that are None / missing
    → Record response time under load (350 tickers)
[ ] Implement BaseAdapter + DataStream + AdapterResult classes
[ ] Implement BDShareLivePricesAdapter + normalize()
[ ] Implement AmarStockLivePricesAdapter + normalize()
[ ] Implement Playwright DSE fallback adapter (live prices)
[ ] Verify all three return identical schema for SQURPHARMA, BRACBANK, GRAMEENPHONE
[ ] Implement health_check() for all three
[ ] Wire DataStream("live_prices") with all three adapters
[ ] Run load test: 350 tickers × 3 concurrent calls → check rate limiting
[ ] Historical: BDShareHistoricalAdapter + AmarStockCSVAdapter
[ ] Bulk load 2012–present via AmarStock CSV (one-time)
[ ] Unit tests: normalize() for each adapter using saved fixture responses
```

### Phase 2 — Fundamentals + Announcements (Week 3)

```
[ ] AmarStockFundamentalsAdapter: scrape stock-chart/{ticker} with BS4
    → Map all fields to canonical schema
    → Test 10 tickers across sectors
    → Validate against bdshare get_company_info() for same ticker
[ ] BDShareCompanyInfoAdapter: get_company_info() — parse list of DataFrames
    → Identify which DataFrame contains which fields (structure varies by company)
[ ] DSEDirectFundamentalsAdapter: Playwright fallback
[ ] BDShareAnnouncementsAdapter: get_corporate_announcements() + get_price_sensitive_news()
[ ] DSEDirectAnnouncementsAdapter: HTML scrape dsebd.org
[ ] BDShareAGMAdapter: get_agm_news() → dividends table
[ ] Quality checks wired for fundamentals + announcements
```

### Phase 3 — News + Macro (Week 4)

```
[ ] TBSNewsRSSAdapter → RSS parsing → LLM ticker extraction (haiku)
[ ] FinancialExpressAdapter + DailyStarAdapter (RSS primary, Playwright fallback)
[ ] ProthomAloAdapter (Playwright — Bengali, no RSS)
[ ] BangladeshBankFXAdapter → USD/BDT rate HTML scrape
[ ] BangladeshBankPolicyRateAdapter → policy rate HTML scrape
[ ] WorldBankAdapter → fallback for all macro indicators
[ ] Macro data freshness checks
```

### Phase 4 — Annual Reports + BSEC (Week 5)

```
[ ] DSEDirectPDFAdapter: Playwright → find all PDF links per ticker
[ ] PDF download + S3/MinIO upload
[ ] Annual report extraction pipeline (Claude Files API)
[ ] BSECPlaywrightAdapter: IPO filings + BSEC circulars
[ ] ManualUploadAdapter: admin endpoint for manual PDF ingestion
```

### Phase 5 — Observability (Week 6)

```
[ ] pipeline_jobs table + job_run() context manager
[ ] source_health table + 6-hourly health checks
[ ] Structure hash monitoring for AmarStock + dsebd.org pages
[ ] Grafana dashboard: pipeline health, data freshness, error rates
[ ] WhatsApp + email alert routing
[ ] Admin API: /api/admin/pipeline/status, /trigger/{job_id}
```

---

## 13. Open Questions (Resolved)

| Question | Answer |
|---|---|
| Does bdshare return EPS audited vs unaudited separately? | **No** — `get_latest_pe()` returns single `eps` field. AmarStock scrape needed for audited/unaudited split. |
| Does bdshare return shareholding %? | **Partial** — `get_company_info()` returns a list of DataFrames; shareholding is in one of them but structure varies. Must test per company. |
| Does bdshare return dividend history (year-by-year)? | **No** — only AGM events (declaration date, % declared). Full history needs AmarStock or DSE direct. |
| Does AmarStock have a documented JSON API for fundamentals? | **No** — fundamentals are in HTML stock-chart pages only. BS4 scrape required. |
| Does DSE publish historical data beyond 1 year via API? | **No** — only CSV downloads or bdshare wrapper. |
| Do news sources have RSS feeds? | TBSNews ✅, Financial Express ✅, Daily Star ✅, Dhaka Tribune ✅. Prothom Alo and Samakal do NOT — require Playwright. |
| Is Bangladesh Bank API available? | **No JSON API** — HTML tables only. Scraping required. World Bank API is fallback (6-month lag). |
| Which pages on dsebd.org require JS (Playwright)? | Market depth, some announcement tabs, company filings tab. Price pages are static HTML. |
| How do we handle bdshare bus-factor risk? | Pin version in requirements.txt. Fork repo. Write DSE direct adapters as parallel fallback so bdshare is replaceable. |
| Is AmarStock API rate-limited? | **Unknown** — no published limits. Implement 2s delay between calls defensively. |

---

## 14. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| bdshare breaks (dsebd.org HTML change) | High (3–6 months horizon) | Medium | AmarStock adapter runs in parallel; DSE Playwright is tertiary |
| AmarStock adds auth / changes endpoints | Medium | High | bdshare + DSE direct cover same data |
| DSE website goes down | Low | Critical | bdshare has built-in fallback URLs; Redis serves stale cache |
| bdshare maintainer abandons project | Medium | Low | Fork on GitHub; pin version; our adapters wrap it |
| Bangladesh Bank restructures website | Low | Medium | World Bank API fallback covers all macro indicators (with lag) |
| Prothom Alo blocks scraping | Medium | Low | Bengali news is low priority; 3 other Bengali sources exist |
| Claude Files API unavailable | Low | Medium | Queue PDFs; retry when available; no user-facing impact |
| PDF structure too complex for Claude extraction | Low | Medium | Manual data entry fallback for top 50 companies |
| AmarStock rate-limits after bulk historical load | Medium | Low | Throttle to 1 req/3s; run over multiple nights |

---

## 15. Testing Strategy

### 15.1 Per-Adapter Unit Tests

Each adapter has fixtures: saved raw responses (real data from source, anonymized if needed).
Tests verify `normalize()` produces exact canonical schema regardless of raw variation.

```python
# tests/extraction/test_bdshare_live.py

def test_normalize_live_prices():
    raw = pd.read_pickle("fixtures/bdshare_live_sample.pkl")
    adapter = BDShareLivePricesAdapter()
    result = adapter.normalize(raw)
    
    # Schema contract
    assert list(result.columns) == LIVE_PRICE_CANONICAL_COLUMNS
    assert result["ticker"].str.isupper().all()
    assert (result["high"] >= result["low"]).all()
    assert result["volume"].dtype == "Int64"
    assert result["source"].eq("bdshare_v1.2.1").all()
```

### 15.2 Failover Integration Tests

```python
async def test_live_prices_failover():
    """When bdshare fails, AmarStock adapter must succeed."""
    stream = STREAMS["live_prices"]
    
    # Patch bdshare to raise BDShareError
    with patch.object(BDShareLivePricesAdapter, "fetch", side_effect=AdapterError(...)):
        result = await stream.fetch()
        assert result.source_name.startswith("amarstock_")
        assert len(result.data) >= 100
```

### 15.3 Schema Consistency Test

```python
async def test_all_adapters_same_schema():
    """All live_price adapters must return identical column set."""
    ticker = "SQURPHARMA"
    schemas = []
    for adapter in STREAMS["live_prices"].adapters:
        result = await adapter.fetch(ticker=ticker)
        schemas.append(set(result.data.columns))
    
    assert len(set(frozenset(s) for s in schemas)) == 1, "Schema mismatch across adapters"
```

---

## 16. File Structure

```
extraction/
├── base.py                    # BaseAdapter, DataStream, AdapterResult, AdapterError
├── registry.py                # STREAMS dict — all DataStream definitions
├── normalizers.py             # Shared normalization utilities (BDT parsing, timezone convert)
├── quality.py                 # QUALITY_RULES + run_quality_checks()
├── health.py                  # Health check scheduler + structure hash monitoring
├── adapters/
│   ├── bdshare/
│   │   ├── live_prices.py
│   │   ├── historical.py
│   │   ├── market_info.py
│   │   ├── fundamentals.py
│   │   ├── sector.py
│   │   ├── announcements.py
│   │   ├── depth.py
│   │   └── agm.py
│   ├── amarstock/
│   │   ├── live_prices.py
│   │   ├── csv_historical.py
│   │   └── fundamentals_scraper.py
│   ├── dse_direct/
│   │   ├── live_prices.py
│   │   ├── announcements.py
│   │   ├── pdf_discovery.py
│   │   └── playwright_base.py
│   ├── news/
│   │   ├── tbs_rss.py
│   │   ├── financial_express.py
│   │   ├── daily_star.py
│   │   ├── prothomalo_playwright.py
│   │   └── ticker_extractor.py   # LLM (haiku) NER for ticker mentions
│   ├── macro/
│   │   ├── bangladesh_bank.py
│   │   └── world_bank.py
│   └── bsec/
│       └── ipo_playwright.py
├── scheduler.py               # APScheduler job definitions
├── tasks.py                   # Celery task definitions
└── tests/
    ├── fixtures/              # Saved raw API responses
    ├── test_adapters/         # Per-adapter unit tests
    └── test_integration/      # Failover + schema consistency tests
```

---

## 17. Management UI + AI Ops Agent

### 17.0 Why This Exists

A data platform with 16 streams, 36 adapters, and 7 external sources will break silently
without active governance. This section defines:

1. **Management API** — control plane for the extraction layer
2. **Management UI** — human-readable dashboard over that API
3. **AI Ops Agent** — Claude-powered autonomous governor that monitors, diagnoses,
   and acts on pipeline state; escalates high-risk decisions to humans

Design principle: **AI acts, human approves**. The agent can take low-risk actions
autonomously (retry, promote adapter, send alert). Anything destructive or structural
goes into an approval queue for a human to accept or reject.

---

### 17.1 Management API

All internal — not exposed to end users. Auth: admin JWT only.

```
Stream & Adapter Management:
  GET  /mgmt/streams                         → all streams + current adapter priority
  GET  /mgmt/streams/{stream_id}             → single stream detail
  GET  /mgmt/streams/{stream_id}/adapters    → adapter list with health scores
  POST /mgmt/adapters/{adapter_name}/promote → move to priority=1
  POST /mgmt/adapters/{adapter_name}/demote  → move to last priority
  POST /mgmt/adapters/{adapter_name}/pause   → disable adapter (skip in failover chain)
  POST /mgmt/adapters/{adapter_name}/resume  → re-enable paused adapter

Pipeline Control:
  GET  /mgmt/jobs                            → all job status (latest run per job)
  GET  /mgmt/jobs/{job_id}/history           → run history with duration + status
  POST /mgmt/jobs/{job_id}/trigger           → manual run
  POST /mgmt/jobs/{job_id}/pause             → suspend scheduled job
  POST /mgmt/jobs/{job_id}/resume            → re-enable job

Data Quality:
  GET  /mgmt/quality/failures                → all quality check failures (last 7 days)
  GET  /mgmt/freshness                       → staleness per stream vs SLA
  GET  /mgmt/freshness/{stream_id}           → detailed freshness timeline
  GET  /mgmt/data/{stream_id}/sample         → last 10 rows ingested (for spot-check)
  POST /mgmt/data/{stream_id}/override-quality → manually mark data as ok/suspect

Source Health:
  GET  /mgmt/health                          → all adapter health (current + history)
  GET  /mgmt/health/{adapter_name}           → single adapter health timeline
  POST /mgmt/health/{adapter_name}/check     → trigger on-demand health check
  GET  /mgmt/structure-hashes               → HTML structure hash history per scraper

Alerts:
  GET  /mgmt/alerts                          → alert history (filterable by level)
  POST /mgmt/alerts/{id}/acknowledge         → mark alert acknowledged
  GET  /mgmt/alerts/unacked                  → all unacknowledged alerts

AI Agent:
  GET  /mgmt/agent/status                    → is agent running, last run time
  POST /mgmt/agent/run                       → trigger manual agent inspection cycle
  GET  /mgmt/agent/decisions                 → log of all AI decisions taken
  GET  /mgmt/agent/queue                     → pending approval-required decisions
  POST /mgmt/agent/queue/{decision_id}/approve
  POST /mgmt/agent/queue/{decision_id}/reject
  GET  /mgmt/agent/chat                      → open conversation with ops agent (SSE)
  POST /mgmt/agent/chat                      → send message to ops agent
```

---

### 17.2 Management UI Pages

```
/mgmt/dashboard
  ├── Stream Health Grid
  │     16 tiles (one per stream) — color: green / yellow / red
  │     Each tile: stream name, last run, freshness, active adapter, error count
  ├── Data Freshness Panel
  │     Bar per stream — shows staleness vs SLA threshold
  │     Red bar = SLA breach, yellow = approaching, green = ok
  ├── Active Alerts strip (count + severity)
  └── AI Agent status (last run, decisions taken today, pending approvals)

/mgmt/streams
  ├── Table: stream, adapter count, primary adapter, health %, last success, records/day
  └── Click → /mgmt/streams/{id}

/mgmt/streams/{stream_id}
  ├── Adapter priority list (drag-to-reorder, promote/pause buttons)
  ├── Health history chart per adapter (last 30 days, % uptime)
  ├── Run history timeline (success / partial / failed — color coded)
  ├── Data sample viewer (last 10 rows ingested, raw + normalized)
  ├── Quality check results (pass/fail per rule, per run)
  └── Structure hash diff viewer (shows exactly what HTML changed if detected)

/mgmt/jobs
  ├── APScheduler job table: id, schedule, last run, status, duration
  ├── Celery queue status: pending tasks, active workers, failed tasks
  └── Manual trigger buttons per job

/mgmt/alerts
  ├── Timeline of all alerts (CRITICAL / WARNING / INFO)
  ├── Filter by: level, stream, adapter, time range
  ├── Acknowledge button
  └── Link to relevant stream/job from each alert

/mgmt/agent
  ├── Agent Decision Log
  │     Table: timestamp, action_type, target, reasoning, outcome, risk_level
  │     Color: AUTO (green) / APPROVAL_REQUIRED (orange) / REJECTED (red)
  ├── Approval Queue
  │     Pending decisions the agent wants to take — Approve / Reject per item
  │     Each item shows: proposed action, reasoning, estimated impact, rollback plan
  ├── Chat Console
  │     Full chat interface with the ops agent
  │     Ask: "Why did live_prices fail at 11am?" / "What's wrong with AmarStock?"
  │     Agent can run tools inline and show results in chat
  └── Scheduled Runs panel (next run, run interval, last run summary)
```

---

### 17.3 AI Ops Agent Design

```python
# mgmt/agent/ops_agent.py

AGENT_SYSTEM_PROMPT = """
You are the Data Pipeline Operations Agent for the DSE Stock Intelligence Platform.

Your job: monitor data extraction pipelines, diagnose failures, take corrective actions,
and keep all 16 data streams healthy with minimal human intervention.

You have access to:
- Real-time pipeline status (job runs, adapter health, data freshness)
- Data quality check results and failure details
- HTML structure hashes (detect when scrapers break due to site changes)
- Alert history
- Raw data samples from each stream
- Ability to trigger actions: retry jobs, promote/demote adapters, pause bad sources

Decision authority:
  AUTO (act without approval):
    - Retry a failed job (up to 3x)
    - Send alerts to ops team
    - Mark data as 'suspect' in DB
    - Log structure hash changes
    - Promote a backup adapter when primary fails health check 2x in a row

  APPROVAL_REQUIRED (queue for human review):
    - Pause an adapter permanently
    - Change a job schedule
    - Promote an adapter from priority 3 → priority 1 (skip tier)
    - Disable a data stream entirely
    - Change quality check thresholds

  NEVER (human-only actions):
    - Modify adapter source code
    - Delete data from DB
    - Change DB schema
    - Modify environment variables / credentials

Communication style:
  - Be concise in decision logs
  - In chat, be direct — give specific file:line references when diagnosing scraper breaks
  - Always state the reasoning for each action taken
  - When proposing approval-required actions, include: what, why, impact, rollback plan
"""

AGENT_TOOLS = [
    {
        "name": "get_stream_status",
        "description": "Get current health, freshness, and last run details for one or all streams",
        "input_schema": {
            "type": "object",
            "properties": {
                "stream_id": {"type": "string", "description": "omit for all streams"}
            }
        }
    },
    {
        "name": "get_adapter_health_history",
        "description": "Get health check results for an adapter over the last N days",
        "input_schema": {
            "type": "object",
            "properties": {
                "adapter_name": {"type": "string"},
                "days":         {"type": "integer", "default": 7}
            },
            "required": ["adapter_name"]
        }
    },
    {
        "name": "get_data_sample",
        "description": "Get the last N rows ingested from a stream — for spotting data corruption",
        "input_schema": {
            "type": "object",
            "properties": {
                "stream_id": {"type": "string"},
                "n":         {"type": "integer", "default": 10}
            },
            "required": ["stream_id"]
        }
    },
    {
        "name": "get_quality_failures",
        "description": "Get quality check failures for a stream in the last N hours",
        "input_schema": {
            "type": "object",
            "properties": {
                "stream_id":  {"type": "string"},
                "hours_back": {"type": "integer", "default": 24}
            }
        }
    },
    {
        "name": "get_structure_hash_diff",
        "description": "Get the diff when an HTML page structure changed — helps diagnose broken scrapers",
        "input_schema": {
            "type": "object",
            "properties": {
                "adapter_name": {"type": "string"}
            },
            "required": ["adapter_name"]
        }
    },
    {
        "name": "get_error_logs",
        "description": "Get raw error messages from failed pipeline job runs",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id":     {"type": "string"},
                "hours_back": {"type": "integer", "default": 24},
                "limit":      {"type": "integer", "default": 20}
            }
        }
    },
    {
        "name": "trigger_job",
        "description": "Manually trigger a pipeline job to run now (AUTO — no approval needed)",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id":  {"type": "string"},
                "reason":  {"type": "string"}
            },
            "required": ["job_id", "reason"]
        }
    },
    {
        "name": "promote_adapter",
        "description": "Promote an adapter to priority=1 within its stream (AUTO if moving 1 rank; APPROVAL if skipping tiers)",
        "input_schema": {
            "type": "object",
            "properties": {
                "adapter_name": {"type": "string"},
                "reason":       {"type": "string"}
            },
            "required": ["adapter_name", "reason"]
        }
    },
    {
        "name": "pause_adapter",
        "description": "Pause an adapter — it will be skipped in failover chain. APPROVAL_REQUIRED.",
        "input_schema": {
            "type": "object",
            "properties": {
                "adapter_name":  {"type": "string"},
                "reason":        {"type": "string"},
                "resume_after":  {"type": "string", "description": "ISO datetime or 'manual'"}
            },
            "required": ["adapter_name", "reason"]
        }
    },
    {
        "name": "flag_data_suspect",
        "description": "Mark a time range of ingested data as suspect quality in the DB",
        "input_schema": {
            "type": "object",
            "properties": {
                "stream_id":  {"type": "string"},
                "from_ts":    {"type": "string"},
                "to_ts":      {"type": "string"},
                "reason":     {"type": "string"}
            },
            "required": ["stream_id", "from_ts", "to_ts", "reason"]
        }
    },
    {
        "name": "send_alert",
        "description": "Send an alert to ops team via configured channel",
        "input_schema": {
            "type": "object",
            "properties": {
                "level":   {"type": "string", "enum": ["CRITICAL","WARNING","INFO"]},
                "message": {"type": "string"},
                "details": {"type": "object"}
            },
            "required": ["level", "message"]
        }
    },
    {
        "name": "queue_approval",
        "description": "Queue an action for human approval before it executes",
        "input_schema": {
            "type": "object",
            "properties": {
                "action_type":   {"type": "string"},
                "target":        {"type": "string"},
                "reasoning":     {"type": "string"},
                "impact":        {"type": "string"},
                "rollback_plan": {"type": "string"},
                "proposed_args": {"type": "object"}
            },
            "required": ["action_type", "target", "reasoning", "impact", "rollback_plan"]
        }
    }
]
```

---

### 17.4 Agent Run Modes

#### Mode A: Scheduled Inspection (every 30 min)

Triggered by APScheduler. Agent runs a full health sweep:

```
1. get_stream_status() — all 16 streams
2. For any stream with health != "ok":
   a. get_error_logs() + get_quality_failures()
   b. get_adapter_health_history() for active adapter
   c. Diagnose: network failure? HTML change? rate limit? bad data?
   d. Take action per decision authority table (auto vs approval)
3. Check freshness vs SLAs → alert on any breach
4. Check structure hash changes → alert + queue approval for adapter update
5. Write decision log entry regardless of action taken
6. If all healthy: write "all_clear" log (1 line, no alert)
```

#### Mode B: Alert Hook (triggered by critical failure)

When `alert_ops(level="CRITICAL")` fires → immediately triggers agent run
focused on the failing stream. Faster response than waiting for next 30-min tick.

#### Mode C: Human Chat (on-demand via /mgmt/agent/chat)

Human asks free-form questions. Agent uses same tools but responds conversationally.

**Example interactions:**

```
Human: "live_prices failed 3 times this morning, what happened?"

Agent: [calls get_error_logs("live_price_pull", hours_back=8)]
       [calls get_adapter_health_history("bdshare_live_v1")]
       [calls get_structure_hash_diff("bdshare_live_v1")]

→ "bdshare_live_v1 started returning 0 stocks at 10:03 BD time.
   Structure hash changed on dsebd.org at 09:58 — the table class
   'table-share-price' was renamed to 'table-share-price-new'.
   AmarStock adapter (priority 2) took over automatically at 10:04.
   
   To fix: update bdshare BDShareLivePricesAdapter selector in
   extraction/adapters/bdshare/live_prices.py:47.
   
   I've queued a pause on bdshare_live_v1 for your approval so
   it stops failing and dragging down response time."
```

```
Human: "fundamentals data looks wrong for BRACBANK, NAV shows 0"

Agent: [calls get_data_sample("fundamentals", n=20)]
       [calls get_quality_failures("fundamentals", hours_back=72)]

→ "Last 3 BRACBANK fundamental rows all have nav=0. Quality check
   'negative_nav_count' should have caught this but BRACBANK is Z-category
   so it was excluded from that rule. Bug in quality.py:83 — Z-category
   exclusion is too broad.
   
   Root cause: AmarStock stock-chart page for BRACBANK returns an empty
   NAV cell when the company has a pending audit. This is real data, not
   a scraper error.
   
   I've flagged BRACBANK nav rows from 2026-05-15 to 2026-05-21 as
   'suspect'. Recommend adding a 'pending_audit' flag to the fundamentals
   schema."
```

---

### 17.5 Decision Log Schema

Every agent action — auto or approval-required — is recorded.

```sql
CREATE TABLE agent_decisions (
    id              SERIAL PRIMARY KEY,
    decided_at      TIMESTAMPTZ DEFAULT NOW(),
    run_mode        TEXT,        -- "scheduled" | "alert_hook" | "chat"
    action_type     TEXT,        -- "trigger_job" | "promote_adapter" | "flag_suspect" etc.
    target          TEXT,        -- stream_id or adapter_name
    reasoning       TEXT,        -- agent's explanation (1–3 sentences)
    risk_level      TEXT,        -- "AUTO" | "APPROVAL_REQUIRED"
    status          TEXT,        -- "executed" | "pending_approval" | "approved" | "rejected"
    approved_by     TEXT,        -- human username if approved
    outcome         TEXT,        -- what happened after execution
    tool_calls      JSONB        -- full tool call log for audit
);
```

---

### 17.6 Human-in-the-Loop Approval Flow

```
Agent decides: pause_adapter("amarstock_fundamentals_v1", reason="...")

→ status = "pending_approval"
→ UI shows in Approval Queue:
   ┌────────────────────────────────────────────────────────────┐
   │ ⚠️  APPROVAL REQUIRED                                       │
   │ Action:   Pause adapter amarstock_fundamentals_v1          │
   │ Stream:   fundamentals                                      │
   │ Reasoning: Adapter returned malformed HTML for 47/350      │
   │            tickers in last run. Quality failure rate 13%.  │
   │ Impact:   fundamentals stream falls back to               │
   │           bdshare_company_info_v1 (priority 2).            │
   │            Coverage may drop from 350 to ~280 tickers.    │
   │ Rollback: Resume adapter via /mgmt/adapters/…/resume       │
   │                                                             │
   │   [ Approve ]   [ Reject ]   [ Ask Agent ]                │
   └────────────────────────────────────────────────────────────┘

→ Human clicks Approve → action executes → status = "approved"
→ Human clicks Reject  → action cancelled → agent logs rejection reason
→ Human clicks Ask Agent → opens chat scoped to this decision
```

---

### 17.7 UI Tech Stack

Sits in the same Next.js 14 frontend, protected by admin role check.

```typescript
// Separate Next.js route group: app/(mgmt)/mgmt/...

Components:
├── StreamHealthGrid      — 16 tiles, color = health status, auto-refreshes every 60s
├── FreshnessBar          — one bar per stream showing staleness vs SLA
├── AdapterPriorityList   — drag-to-reorder adapter chain, promote/pause buttons
├── DataSampleViewer      — table of last N rows, raw + normalized toggle
├── StructureHashDiff     — diff view of detected HTML changes (like a code diff)
├── AgentDecisionLog      — table with expandable rows showing tool call trace
├── ApprovalQueueCard     — card per pending decision with approve/reject/ask buttons
├── AgentChatWindow       — streaming SSE chat, same component as end-user chat
│                           but tools are mgmt tools, not analyst tools
└── JobTriggerPanel       — buttons per APScheduler job with last-run status

Auth:
  Admin JWT required for all /mgmt routes
  Agent chat authenticated same way — agent cannot act on behalf of non-admin
```

---

### 17.8 What the Agent Cannot Fix Automatically

These require a developer — agent detects and flags, never acts:

| Issue | Agent Response |
|---|---|
| AmarStock changed HTML structure → scraper broken | Flags structure hash change, shows diff, creates approval-queue item to pause adapter, posts exact file+line to fix in chat |
| bdshare library bug (not our code) | Detects via health check, promotes backup adapter (auto), flags in chat with bdshare GitHub issue link suggestion |
| New source needed (existing chain fully failing) | Alerts CRITICAL, shows which stream is dark, states "all adapters exhausted — new source adapter required" |
| Data schema mismatch after DSE changes field format | Quality check catches it, agent flags data as suspect, surfaces in chat with specific field name + sample values |
| ML models trained on suspect data | Out of scope — agent flags suspect data in DB; ML pipeline checks quality_flag before training |

---

### 17.9 Summary: Three Layers of Governance

```
Layer 1 — Passive (always on):
  Dashboards + freshness bars + alert history.
  Humans can read, no action required.

Layer 2 — Active Control Plane (on demand):
  Management API + UI buttons.
  Humans trigger retries, promote/pause adapters, inspect data samples.
  Full control without touching code or SSH.

Layer 3 — AI Ops Agent (autonomous + supervised):
  Runs every 30 min + on critical alerts + on human request.
  Takes low-risk actions automatically (retries, adapter promotion).
  Queues high-risk actions for human approval.
  Available as chat interface for open-ended diagnosis.
  Full audit trail of every decision — nothing is a black box.
```
