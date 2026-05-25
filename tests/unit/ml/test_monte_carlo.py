"""Tests for ml.valuation.monte_carlo."""
from __future__ import annotations

import numpy as np
import pytest
from ml.valuation.monte_carlo import MonteCarloSimulator


def test_output_keys():
    sim = MonteCarloSimulator(
        eps_ttm=10.0, eps_growth_mean=0.10, eps_growth_std=0.05,
        cost_of_equity=0.12, terminal_growth=0.03, n_scenarios=100,
    )
    result = sim.simulate(current_price=100.0)
    assert set(result.keys()) >= {"p10", "p50", "p90", "mean", "downside_prob"}


def test_p10_lt_p50_lt_p90():
    sim = MonteCarloSimulator(
        eps_ttm=10.0, eps_growth_mean=0.10, eps_growth_std=0.05,
        cost_of_equity=0.12, terminal_growth=0.03, n_scenarios=1000,
    )
    result = sim.simulate(current_price=100.0)
    assert result["p10"] < result["p50"] < result["p90"]


def test_downside_prob_range():
    sim = MonteCarloSimulator(
        eps_ttm=10.0, eps_growth_mean=0.10, eps_growth_std=0.05,
        cost_of_equity=0.12, terminal_growth=0.03, n_scenarios=500,
    )
    result = sim.simulate(current_price=100.0)
    assert 0.0 <= result["downside_prob"] <= 1.0


def test_reproducible_with_seed():
    kwargs = dict(eps_ttm=10.0, eps_growth_mean=0.10, eps_growth_std=0.05,
                  cost_of_equity=0.12, terminal_growth=0.03, n_scenarios=200, seed=42)
    r1 = MonteCarloSimulator(**kwargs).simulate(100.0)
    r2 = MonteCarloSimulator(**kwargs).simulate(100.0)
    assert r1["p50"] == r2["p50"]
