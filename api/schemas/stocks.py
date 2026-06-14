# api/schemas/stocks.py
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel


class CompanyRow(BaseModel):
    ticker: str
    name: str
    sector: str
    category: str | None
    market_cap_bdt: Decimal | None
    is_active: bool
    pe: Decimal | None = None
    health_score: Decimal | None = None
    last_close: Decimal | None = None
    change_pct: Decimal | None = None
    rating: str = "N/A"
    # Risk/meta fields (migration 036) — populated only by the detail endpoint;
    # the list endpoint leaves them at None.
    face_value: Decimal | None = None
    market_lot: int | None = None
    electronic_share: bool | None = None
    debut_trading_date: date | None = None
    operational_status: str | None = None
    short_loan_mn: Decimal | None = None
    long_loan_mn: Decimal | None = None
    loan_as_on: date | None = None
    credit_rating_st: str | None = None
    credit_rating_lt: str | None = None
    delisting_remark: str | None = None


class LatestPrice(BaseModel):
    close: Decimal
    change_pct: Decimal | None
    volume: int | None
    value_bdt: Decimal | None
    high: Decimal | None
    low: Decimal | None
    time: datetime


class LatestFundamentals(BaseModel):
    eps: Decimal | None
    nav: Decimal | None
    pe: Decimal | None
    cash_div_pct: Decimal | None
    stock_div_pct: Decimal | None
    sponsor_pct: Decimal | None
    public_pct: Decimal | None
    fiscal_year: int | None


class HealthScoreRow(BaseModel):
    health_score: Decimal | None
    fundamental_score: Decimal | None
    momentum_score: Decimal | None
    scored_at: datetime
    fundamental_detail: dict[str, Any] | None = None


class StockDetail(BaseModel):
    company: CompanyRow
    latest_price: LatestPrice | None
    fundamentals: LatestFundamentals | None
    health_score: HealthScoreRow | None


class PricePoint(BaseModel):
    day: date
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal
    volume: int | None
    value_bdt: Decimal | None


class OHLCVResponse(BaseModel):
    ticker: str
    interval: str
    items: list[PricePoint]


class FundamentalsRow(BaseModel):
    fiscal_year: int | None
    eps: Decimal | None
    nav: Decimal | None
    pe: Decimal | None
    cash_div_pct: Decimal | None
    stock_div_pct: Decimal | None
    sponsor_pct: Decimal | None
    public_pct: Decimal | None
    net_profit_bdt: Decimal | None = None
    total_comprehensive_income_bdt: Decimal | None = None
    dividend_yield_pct: Decimal | None = None
    eps_basis: str | None = None
    fetched_at: datetime


class FundamentalsResponse(BaseModel):
    ticker: str
    items: list[FundamentalsRow]
    max_years: int
    is_truncated: bool


class ShareholdingSnapshot(BaseModel):
    as_on_date: date
    sponsor_pct: Decimal | None
    govt_pct: Decimal | None
    institution_pct: Decimal | None
    foreign_pct: Decimal | None
    public_pct: Decimal | None


class CorporateActionRow(BaseModel):
    fiscal_year: int
    action_type: str  # 'cash_div' | 'stock_div' | 'right_issue'
    value_pct: Decimal | None
    ratio_text: str | None
    ratio: Decimal | None


class QuarterlyEpsRow(BaseModel):
    fiscal_year: int
    quarter: int
    eps_basic: Decimal | None
    eps_diluted: Decimal | None
    period_end_price: Decimal | None


class TrackRecordResponse(BaseModel):
    ticker: str
    shareholding: list[ShareholdingSnapshot]
    actions: list[CorporateActionRow]
    quarterly: list[QuarterlyEpsRow]
    max_action_years: int
    is_truncated: bool


class PredictionRow(BaseModel):
    horizon_days: int
    predicted_direction: str
    confidence: Decimal
    target_price: Decimal | None
    predicted_at: datetime
    model_version: str


class PredictionsResponse(BaseModel):
    ticker: str
    predictions: list[PredictionRow]


class AnnouncementRow(BaseModel):
    id: int
    published_at: datetime
    headline: str
    announcement_type: str | None
    eps_value: Decimal | None
    eps_period: str | None
    dividend_cash_pct: Decimal | None
    dividend_stock_pct: Decimal | None


class AnnouncementsResponse(BaseModel):
    ticker: str
    total: int
    items: list[AnnouncementRow]


class WyckoffEvent(BaseModel):
    day: date
    # One of: SC | BC | SPRING | PS | AR | ST | TEST | SOS | LPS
    #         | PSY | UT | UTAD | SOW | LPSY
    type: str
    price: Decimal
    label: str
    help: str


class WyckoffRange(BaseModel):
    start_day: date
    end_day: date
    phase: str  # "accumulation" | "distribution" | "undetermined"
    phase_help: str
    confidence: float  # 0..1
    support: Decimal
    resistance: Decimal
    events: list[WyckoffEvent]


class WyckoffResponse(BaseModel):
    ticker: str
    interval: str
    ranges: list[WyckoffRange]


class LivePrice(BaseModel):
    ticker: str
    available: bool
    ltp: float | None = None
    high: float | None = None
    low: float | None = None
    prev_close: float | None = None
    change_pct: float | None = None
    volume: float | None = None
    value_bdt: float | None = None
    market_status: str
    status_source: str
    as_of: datetime
