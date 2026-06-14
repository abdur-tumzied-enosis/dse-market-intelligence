"""
Stream registry — all 16 DataStreams with priority-ordered adapter chains.
"""
from __future__ import annotations

from extraction.adapters.amarstock.csv_historical import AmarStockCSVAdapter
from extraction.adapters.amarstock.fundamentals_scraper import AmarStockFundamentalsAdapter
from extraction.adapters.amarstock.live_prices import AmarStockLivePricesAdapter
from extraction.adapters.bdshare.announcements import (
    BDShareAGMAdapter,
    BDShareAnnouncementsAdapter,
    BDSharePSNAdapter,
)
from extraction.adapters.bdshare.depth import BDShareDepthAdapter
from extraction.adapters.bdshare.fundamentals import BDShareCompanyInfoAdapter
from extraction.adapters.bdshare.historical import BDShareHistoricalAdapter
from extraction.adapters.bdshare.live_prices import BDShareLivePricesAdapter
from extraction.adapters.bdshare.market_info import BDShareMarketInfoAdapter
from extraction.adapters.bdshare.sector import BDShareSectorAdapter
from extraction.adapters.dse_direct.announcements import DSEDirectAnnouncementsAdapter, DSEDirectPSNAdapter
from extraction.adapters.dse_direct.company_info import DSEDirectCompanyInfoAdapter
from extraction.adapters.dse_direct.depth import DSEDirectDepthPlaywrightAdapter
from extraction.adapters.dse_direct.gainers_losers import DSEDirectGainersAdapter, DSEDirectLosersAdapter
from extraction.adapters.dse_direct.live_prices import DSEDirectLivePricesAdapter
from extraction.adapters.dse_direct.market_info import DSEDirectMarketInfoAdapter
from extraction.adapters.dse_direct.market_status import DSEMarketStatusAdapter
from extraction.adapters.dse_direct.pdf_reports import DSEDirectPDFAdapter
from extraction.adapters.dse_direct.sector_pe import DSEDirectSectorPEAdapter
from extraction.adapters.bsec.ipo_scraper import BsecIPOAdapter
from extraction.adapters.macro.bangladesh_bank import BangladeshBankAdapter
from extraction.adapters.macro.worldbank import WorldBankAdapter
from extraction.base import DataStream

from extraction.adapters.news.google_news_rss import GoogleNewsRSSAdapter


def _build_registry() -> dict[str, DataStream]:
    streams: dict[str, DataStream] = {}

    # ------------------------------------------------------------------
    # Stream: live_prices
    # Primary: bdshare (get_current_trade_data)
    # Fallback: amarstock API, dse_direct scrape
    # ------------------------------------------------------------------
    streams["live_prices"] = DataStream(name="live_prices", adapters=[
        DSEDirectLivePricesAdapter(),
        BDShareLivePricesAdapter(),
        AmarStockLivePricesAdapter(),
    ])

    # ------------------------------------------------------------------
    # Stream: historical_ohlcv
    # Primary: bdshare hist API — full real OHLCV (open/close included).
    # Fallback: amarstock CSV — high/low only, close synthesized (no_ohlc).
    # Used by gap_backfill; bulk loaders call adapters directly, not this chain.
    # ------------------------------------------------------------------
    streams["historical_ohlcv"] = DataStream(name="historical_ohlcv", adapters=[
        BDShareHistoricalAdapter(),
        AmarStockCSVAdapter(),
    ])

    # ------------------------------------------------------------------
    # Stream: market_indices
    # Primary: dse_direct homepage box — carries change_pct directly, reliable
    # Fallback: bdshare (get_market_info) — DSEX, DS30, DSES (change_pct derived)
    # ------------------------------------------------------------------
    streams["market_indices"] = DataStream(name="market_indices", adapters=[
        DSEDirectMarketInfoAdapter(),
        BDShareMarketInfoAdapter(),
    ])

    # ------------------------------------------------------------------
    # Stream: market_status
    # Single adapter — DSE homepage "Market Status: Open|Closed" flag.
    # No fallback adapter: extraction.market_status falls back to the clock.
    # ------------------------------------------------------------------
    streams["market_status"] = DataStream(name="market_status", adapters=[
        DSEMarketStatusAdapter(),
    ])

    # ------------------------------------------------------------------
    # Stream: fundamentals
    # Primary: amarstock (richest — 93 fields incl. quarterly EPS + MA signals)
    # Fallback 2: bdshare get_company_info (BLOCKED by lxml OSError — kept for retry)
    # Fallback 3: dse_direct /displayCompany.php (partial — no quarterly EPS/MA)
    # ------------------------------------------------------------------
    streams["fundamentals"] = DataStream(name="fundamentals", adapters=[
        AmarStockFundamentalsAdapter(),
        BDShareCompanyInfoAdapter(),
        DSEDirectCompanyInfoAdapter(),
    ])

    # ------------------------------------------------------------------
    # Stream: sector_performance
    # Primary: dse_direct sectoral PE page (confirmed 2026-05-23)
    # bdshare get_sector_performance() is dead — hits 404 PHP URLs
    # ------------------------------------------------------------------
    streams["sector_performance"] = DataStream(name="sector_performance", adapters=[
        DSEDirectSectorPEAdapter(),
    ])

    # ------------------------------------------------------------------
    # Stream: announcements
    # Primary: dse_direct Playwright (confirmed working 2026-05-21)
    # bdshare get_corporate_announcements() returns empty even during market hours
    # ------------------------------------------------------------------
    streams["announcements"] = DataStream(name="announcements", adapters=[
        DSEDirectAnnouncementsAdapter(),
        BDShareAnnouncementsAdapter(),
    ])

    # ------------------------------------------------------------------
    # Stream: psn (price sensitive news)
    # Primary: dse_direct Playwright (confirmed working 2026-05-21)
    # bdshare get_price_sensitive_news() returns empty even during market hours
    # ------------------------------------------------------------------
    streams["psn"] = DataStream(name="psn", adapters=[
        DSEDirectPSNAdapter(),
        BDSharePSNAdapter(),
    ])

    # ------------------------------------------------------------------
    # Stream: top_gainers_losers
    # Primary: dse_direct (top_ten_gainer.php + top_ten_loser.php)
    # bdshare get_top_gainers_losers() is dead — hits 404 PHP URLs
    # Returns combined df with direction='gainer'|'loser'
    # ------------------------------------------------------------------
    streams["top_gainers_losers"] = DataStream(name="top_gainers_losers", adapters=[
        DSEDirectGainersAdapter(),
        DSEDirectLosersAdapter(),
    ])

    # ------------------------------------------------------------------
    # Stream: agm_dividends
    # Primary: bdshare (get_agm_news) — cash_div_pct, stock_div_pct, agm_date
    # ------------------------------------------------------------------
    streams["agm_dividends"] = DataStream(name="agm_dividends", adapters=[
        BDShareAGMAdapter(),
    ])

    # ------------------------------------------------------------------
    # Stream: market_depth
    # Primary: bdshare (get_market_depth_data) — 5-level order book
    # Fallback: dse_direct Playwright
    # ------------------------------------------------------------------
    streams["market_depth"] = DataStream(name="market_depth", adapters=[
        BDShareDepthAdapter(),
        DSEDirectDepthPlaywrightAdapter(),
    ])

    # ------------------------------------------------------------------
    # Stream: annual_reports_pdf
    # Primary: dse_direct Playwright (PDF link discovery + download)
    # ------------------------------------------------------------------
    streams["annual_reports_pdf"] = DataStream(name="annual_reports_pdf", adapters=[
        DSEDirectPDFAdapter(),
    ])

    # ------------------------------------------------------------------
    # Stream: news_en (English-language BD financial news)
    # Sources: TBS, Financial Express, Daily Star, Dhaka Tribune
    # ------------------------------------------------------------------
    streams["news_en"] = DataStream(name="news_en", adapters=[
        GoogleNewsRSSAdapter(),
    ])

    # ------------------------------------------------------------------
    # Stream: news_bn (Bengali-language financial news)
    # Source: Prothom Alo (Playwright only — no RSS) — deferred
    # ------------------------------------------------------------------
    streams["news_bn"] = DataStream(name="news_bn", adapters=[
        GoogleNewsRSSAdapter(queries=["Bangladesh শেয়ার বাজার ডিএসই"]),
    ])

    # ------------------------------------------------------------------
    # Stream: macro_policy_rate
    # NOTE: bb.org.bd blocks scrapers with F5 BIG-IP CAPTCHA (confirmed 2026-05-21).
    # WorldBank is priority 1 (reliable). BB is priority 2 (best-effort, usually blocked).
    # BB URL confirmed from sitemap: /en/index.php/monetaryactivity/index
    # ------------------------------------------------------------------
    streams["macro_policy_rate"] = DataStream(name="macro_policy_rate", adapters=[
        WorldBankAdapter(indicator="FR.INR.RINR", priority=1),
        BangladeshBankAdapter(indicator="policy_rate", priority=2),
    ])

    # ------------------------------------------------------------------
    # Stream: macro_cpi
    # BB URL confirmed from sitemap: /en/index.php/econdata/inflation
    # ------------------------------------------------------------------
    streams["macro_cpi"] = DataStream(name="macro_cpi", adapters=[
        WorldBankAdapter(indicator="FP.CPI.TOTL.ZG", priority=1),
        BangladeshBankAdapter(indicator="cpi", priority=2),
    ])

    # ------------------------------------------------------------------
    # Stream: macro_usd_bdt
    # BB URL confirmed from sitemap: /en/index.php/econdata/exchangerate
    # WB PA.NUS.FCRF = annual average — less precise than BB daily rate.
    # ------------------------------------------------------------------
    streams["macro_usd_bdt"] = DataStream(name="macro_usd_bdt", adapters=[
        WorldBankAdapter(indicator="PA.NUS.FCRF", priority=1),
        BangladeshBankAdapter(indicator="usd_bdt", priority=2),
    ])

    # ------------------------------------------------------------------
    # Stream: macro_gdp
    # BB URL confirmed from sitemap: /en/index.php/econdata/nationalincome
    # ------------------------------------------------------------------
    streams["macro_gdp"] = DataStream(name="macro_gdp", adapters=[
        WorldBankAdapter(indicator="NY.GDP.MKTP.KD.ZG", priority=1),
        BangladeshBankAdapter(indicator="gdp", priority=2),
    ])

    # ------------------------------------------------------------------
    # Stream: macro_remittance
    # BB URL confirmed from sitemap: /en/index.php/econdata/wageremitance (note spelling)
    # ------------------------------------------------------------------
    streams["macro_remittance"] = DataStream(name="macro_remittance", adapters=[
        WorldBankAdapter(indicator="BX.TRF.PWKR.CD.DT", priority=1),
        BangladeshBankAdapter(indicator="remittance", priority=2),
    ])

    # ------------------------------------------------------------------
    # Stream: ipo_filings
    # BSEC official site — fixed price + bookbuilding IPOs
    # ~156 total records (137 fixed, 19 bookbuilding), 2008-present
    # ------------------------------------------------------------------
    streams["ipo_filings"] = DataStream(name="ipo_filings", adapters=[
        BsecIPOAdapter(),
    ])

    return streams


STREAMS: dict[str, DataStream] = _build_registry()
