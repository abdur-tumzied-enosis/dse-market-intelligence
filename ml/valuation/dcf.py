"""Discounted Cash Flow intrinsic value calculator."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class DCFCalculator:
    """
    Simple DDM-style DCF using EPS as proxy for free cash flow per share.

    Assumes: FCF ≈ EPS (appropriate for asset-light businesses; overestimates
    for capital-intensive sectors like banking/cement — use with awareness).
    """
    eps_ttm: float
    eps_growth_rate: float      # expected annual growth for projection_years
    cost_of_equity: float       # WACC proxy (risk-free + beta × market premium)
    terminal_growth: float      # long-run growth after projection period
    projection_years: int = 5

    def calculate(self, current_price: float) -> dict:
        """
        Returns dict with keys: intrinsic_value, margin_of_safety_pct,
        pv_earnings, terminal_value. Values are None if EPS <= 0.
        """
        if self.eps_ttm <= 0:
            return {
                "intrinsic_value": None,
                "margin_of_safety_pct": None,
                "pv_earnings": None,
                "terminal_value": None,
            }

        r = self.cost_of_equity
        g = self.eps_growth_rate
        g_t = self.terminal_growth

        # PV of projected earnings
        pv_earnings = 0.0
        eps = self.eps_ttm
        for year in range(1, self.projection_years + 1):
            eps = eps * (1 + g)
            pv_earnings += eps / (1 + r) ** year

        # Terminal value (Gordon Growth Model)
        eps_terminal = self.eps_ttm * (1 + g) ** self.projection_years
        if r <= g_t:
            terminal_value = 0.0  # model breaks down — cost < terminal growth
        else:
            terminal_value = (eps_terminal * (1 + g_t)) / (r - g_t)
        pv_terminal = terminal_value / (1 + r) ** self.projection_years

        intrinsic_value = pv_earnings + pv_terminal
        margin_of_safety_pct = (intrinsic_value - current_price) / current_price * 100

        return {
            "intrinsic_value": round(intrinsic_value, 2),
            "margin_of_safety_pct": round(margin_of_safety_pct, 2),
            "pv_earnings": round(pv_earnings, 2),
            "terminal_value": round(pv_terminal, 2),
        }
