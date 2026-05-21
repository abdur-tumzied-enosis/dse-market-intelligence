"""
Stream registry — all 16 DataStreams with priority-ordered adapter chains.
Adapters are stubbed until implemented; replace stubs with real instances in each phase.
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
from extraction.base import DataStream

# Phase 1E — dse_direct + playwright adapters (TODO)
# Phase 1F — news adapters (TODO)
# Phase 1G — macro adapters (TODO)


def _build_registry() -> dict[str, DataStream]:
    streams: dict[str, DataStream] = {}

    # ------------------------------------------------------------------
    # Stream: live_prices
    # Primary: bdshare (get_current_trade_data)
    # Fallback: amarstock API, dse_direct scrape
    # ------------------------------------------------------------------
    streams["live_prices"] = DataStream(name="live_prices", adapters=[
        BDShareLivePricesAdapter(),
        AmarStockLivePricesAdapter(),
        # DSEDirectLivePricesAdapter(priority=3),  # Phase 1E
    ])

    # ------------------------------------------------------------------
    # Stream: historical_ohlcv
    # Primary: amarstock CSV (bulk), bdshare hist API (incremental)
    # ------------------------------------------------------------------
    streams["historical_ohlcv"] = DataStream(name="historical_ohlcv", adapters=[
        AmarStockCSVAdapter(),
        BDShareHistoricalAdapter(),
    ])

    # ------------------------------------------------------------------
    # Stream: market_indices
    # Primary: bdshare (get_market_info) — DSEX, DS30, DSES
    # ------------------------------------------------------------------
    streams["market_indices"] = DataStream(name="market_indices", adapters=[
        BDShareMarketInfoAdapter(),
        # DSEDirectLivePricesAdapter(priority=2),  # Phase 1E
    ])

    # ------------------------------------------------------------------
    # Stream: fundamentals
    # Primary: amarstock scrape (richest data), bdshare get_company_info
    # ------------------------------------------------------------------
    streams["fundamentals"] = DataStream(name="fundamentals", adapters=[
        AmarStockFundamentalsAdapter(),
        BDShareCompanyInfoAdapter(),
    ])

    # ------------------------------------------------------------------
    # Stream: sector_performance
    # Primary: bdshare (get_sector_performance)
    # ------------------------------------------------------------------
    streams["sector_performance"] = DataStream(name="sector_performance", adapters=[
        BDShareSectorAdapter(),
    ])

    # ------------------------------------------------------------------
    # Stream: announcements
    # Primary: bdshare (get_corporate_announcements), dse_direct scrape
    # ------------------------------------------------------------------
    streams["announcements"] = DataStream(name="announcements", adapters=[
        BDShareAnnouncementsAdapter(),
        # DSEDirectAnnouncementsAdapter(priority=2), # Phase 1E
    ])

    # ------------------------------------------------------------------
    # Stream: psn (price sensitive news)
    # Primary: bdshare (get_price_sensitive_news)
    # ------------------------------------------------------------------
    streams["psn"] = DataStream(name="psn", adapters=[
        BDSharePSNAdapter(),
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
        # DSEDirectDepthPlaywrightAdapter(priority=2), # Phase 1E
    ])

    # ------------------------------------------------------------------
    # Stream: annual_reports_pdf
    # Primary: dse_direct Playwright (PDF link discovery + download)
    # ------------------------------------------------------------------
    streams["annual_reports_pdf"] = DataStream(name="annual_reports_pdf", adapters=[
        # DSEDirectPDFAdapter(priority=1),         # Phase 1E
    ])

    # ------------------------------------------------------------------
    # Stream: news_en (English-language BD financial news)
    # Sources: TBS, Financial Express, Daily Star, Dhaka Tribune
    # ------------------------------------------------------------------
    streams["news_en"] = DataStream(name="news_en", adapters=[
        # TBSNewsRSSAdapter(priority=1),           # Phase 1F
        # FinancialExpressRSSAdapter(priority=2),  # Phase 1F
        # DailyStarRSSAdapter(priority=3),         # Phase 1F
        # DhakaTribuneRSSAdapter(priority=4),      # Phase 1F
    ])

    # ------------------------------------------------------------------
    # Stream: news_bn (Bengali-language financial news)
    # Source: Prothom Alo (Playwright only — no RSS)
    # ------------------------------------------------------------------
    streams["news_bn"] = DataStream(name="news_bn", adapters=[
        # ProthomAloPlaywrightAdapter(priority=1), # Phase 1F
    ])

    # ------------------------------------------------------------------
    # Stream: macro_policy_rate
    # Primary: Bangladesh Bank HTML scrape
    # Fallback: World Bank API (6-month lag)
    # ------------------------------------------------------------------
    streams["macro_policy_rate"] = DataStream(name="macro_policy_rate", adapters=[
        # BangladeshBankAdapter(indicator="policy_rate", priority=1),   # Phase 1G
        # WorldBankAdapter(indicator="FR.INR.RINR", priority=2),        # Phase 1G
    ])

    # ------------------------------------------------------------------
    # Stream: macro_cpi
    # ------------------------------------------------------------------
    streams["macro_cpi"] = DataStream(name="macro_cpi", adapters=[
        # BangladeshBankAdapter(indicator="cpi", priority=1),           # Phase 1G
        # WorldBankAdapter(indicator="FP.CPI.TOTL.ZG", priority=2),     # Phase 1G
    ])

    # ------------------------------------------------------------------
    # Stream: macro_usd_bdt
    # ------------------------------------------------------------------
    streams["macro_usd_bdt"] = DataStream(name="macro_usd_bdt", adapters=[
        # BangladeshBankAdapter(indicator="usd_bdt", priority=1),       # Phase 1G
        # WorldBankAdapter(indicator="PA.NUS.FCRF", priority=2),        # Phase 1G
    ])

    # ------------------------------------------------------------------
    # Stream: macro_gdp
    # ------------------------------------------------------------------
    streams["macro_gdp"] = DataStream(name="macro_gdp", adapters=[
        # WorldBankAdapter(indicator="NY.GDP.MKTP.CD", priority=1),     # Phase 1G
    ])

    # ------------------------------------------------------------------
    # Stream: macro_remittance
    # ------------------------------------------------------------------
    streams["macro_remittance"] = DataStream(name="macro_remittance", adapters=[
        # BangladeshBankAdapter(indicator="remittance", priority=1),    # Phase 1G
        # WorldBankAdapter(indicator="BX.TRF.PWKR.CD.DT", priority=2), # Phase 1G
    ])

    return streams


STREAMS: dict[str, DataStream] = _build_registry()
