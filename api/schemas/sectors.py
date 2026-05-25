# api/schemas/sectors.py
from __future__ import annotations
from decimal import Decimal
from datetime import datetime
from pydantic import BaseModel


class SectorRow(BaseModel):
    sector: str
    pe: Decimal | None
    change_pct: Decimal | None
    market_cap_bdt: Decimal | None
    fetched_at: datetime | None


class SectorDetail(BaseModel):
    sector: str
    latest_pe: SectorRow | None
    pe_history: list[SectorRow]
    companies: list[str]
