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
    sector: str
    change_pct: Decimal | None
    value_bdt: Decimal | None
