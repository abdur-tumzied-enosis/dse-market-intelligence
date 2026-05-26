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


class MarketIndices(BaseModel):
    dsex_value: float
    dsex_change_pct: float
    ds30_value: float
    ds30_change_pct: float
    dses_value: float
    dses_change_pct: float
    market_status: str
    advance: int
    decline: int
    unchanged: int
