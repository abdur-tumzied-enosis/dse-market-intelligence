# api/schemas/stocks.py
from __future__ import annotations
from datetime import datetime, date
from decimal import Decimal
from pydantic import BaseModel


class CompanyRow(BaseModel):
    ticker: str
    name: str
    sector: str
    category: str | None
    market_cap_bdt: Decimal | None
    is_active: bool


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
    fetched_at: datetime


class FundamentalsResponse(BaseModel):
    ticker: str
    items: list[FundamentalsRow]


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
