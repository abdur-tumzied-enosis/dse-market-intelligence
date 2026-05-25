"""Monte Carlo DCF simulation over EPS growth uncertainty."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ml.valuation.dcf import DCFCalculator


@dataclass
class MonteCarloSimulator:
    """
    Run DCFCalculator across N scenarios where EPS growth is sampled from
    a normal distribution. Outputs P10/P50/P90 intrinsic value range.
    """
    eps_ttm: float
    eps_growth_mean: float
    eps_growth_std: float
    cost_of_equity: float
    terminal_growth: float = 0.03
    projection_years: int = 5
    n_scenarios: int = 10_000
    seed: Optional[int] = None

    def simulate(self, current_price: float) -> dict:
        """
        Returns dict with keys: p10, p50, p90, mean, downside_prob.
        downside_prob = fraction of scenarios where intrinsic_value < current_price.
        """
        rng = np.random.default_rng(self.seed)
        growth_samples = rng.normal(self.eps_growth_mean, self.eps_growth_std, self.n_scenarios)
        # Clip to avoid absurd values (e.g. -200% growth)
        growth_samples = np.clip(growth_samples, -0.5, 1.0)

        values = []
        for g in growth_samples:
            calc = DCFCalculator(
                eps_ttm=self.eps_ttm,
                eps_growth_rate=float(g),
                cost_of_equity=self.cost_of_equity,
                terminal_growth=self.terminal_growth,
                projection_years=self.projection_years,
            )
            result = calc.calculate(current_price)
            iv = result["intrinsic_value"]
            if iv is not None and iv > 0:
                values.append(iv)

        if not values:
            return {"p10": None, "p50": None, "p90": None, "mean": None, "downside_prob": None}

        arr = np.array(values)
        return {
            "p10": round(float(np.percentile(arr, 10)), 2),
            "p50": round(float(np.percentile(arr, 50)), 2),
            "p90": round(float(np.percentile(arr, 90)), 2),
            "mean": round(float(arr.mean()), 2),
            "downside_prob": round(float((arr < current_price).mean()), 4),
        }
