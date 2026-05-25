"""Tests for ml.valuation.dcf."""
from __future__ import annotations

import pytest
from ml.valuation.dcf import DCFCalculator


def test_intrinsic_value_positive_eps():
    calc = DCFCalculator(
        eps_ttm=10.0,
        eps_growth_rate=0.10,
        cost_of_equity=0.12,
        terminal_growth=0.03,
        projection_years=5,
    )
    result = calc.calculate(current_price=100.0)
    assert result["intrinsic_value"] > 0
    assert "margin_of_safety_pct" in result


def test_margin_of_safety_negative_when_overvalued():
    calc = DCFCalculator(
        eps_ttm=5.0,
        eps_growth_rate=0.05,
        cost_of_equity=0.12,
        terminal_growth=0.03,
        projection_years=5,
    )
    result = calc.calculate(current_price=500.0)
    assert result["margin_of_safety_pct"] < 0


def test_margin_of_safety_positive_when_undervalued():
    calc = DCFCalculator(
        eps_ttm=15.0,
        eps_growth_rate=0.15,
        cost_of_equity=0.12,
        terminal_growth=0.03,
        projection_years=5,
    )
    result = calc.calculate(current_price=50.0)
    assert result["margin_of_safety_pct"] > 0


def test_zero_or_negative_eps_returns_none():
    calc = DCFCalculator(
        eps_ttm=0.0,
        eps_growth_rate=0.10,
        cost_of_equity=0.12,
        terminal_growth=0.03,
        projection_years=5,
    )
    result = calc.calculate(current_price=100.0)
    assert result["intrinsic_value"] is None


def test_result_keys():
    calc = DCFCalculator(10.0, 0.10, 0.12, 0.03, 5)
    result = calc.calculate(100.0)
    assert set(result.keys()) >= {"intrinsic_value", "margin_of_safety_pct",
                                   "pv_earnings", "terminal_value"}
