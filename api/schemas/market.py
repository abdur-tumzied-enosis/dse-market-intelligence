# api/schemas/market.py
from __future__ import annotations
from decimal import Decimal
from pydantic import BaseModel


class MarketSummary(BaseModel):
    total_stocks: int
    advance: int
    decline: int
    unchanged: int
    total_volume: int | None
    total_value_bdt: Decimal | None
    avg_change_pct: Decimal | None


class TopMover(BaseModel):
    ticker: str
    name: str
    close: Decimal
    change_pct: Decimal


class HeatmapItem(BaseModel):
    ticker: str
    name: str
    sector: str
    change_pct: float | None
    ltp: float | None
    value_bdt: float | None
    market_cap: float | None


class MarketIndices(BaseModel):
    dsex_value: float
    dsex_change_pct: float
    ds30_value: float
    ds30_change_pct: float
    dses_value: float
    dses_change_pct: float
    market_status: str
    status_source: str
    advance: int
    decline: int
    unchanged: int


class MarketRegime(BaseModel):
    regime: str                  # "Bull" | "Bear" | "Unknown"
    dsex: float | None
    ma: float | None
    window: int
    provisional: bool
    distance_pct: float | None
    as_of: str | None
    data_status: str             # "ok" | "provisional" | "insufficient"
