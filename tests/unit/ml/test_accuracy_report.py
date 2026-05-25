"""Tests for ml.monitoring.accuracy_report — outcome population + accuracy reporting."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock


# ── populate_outcomes ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_populate_outcomes_marks_correct_when_price_went_up():
    from ml.monitoring.accuracy_report import populate_outcomes

    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[
        {
            "id": 1,
            "ticker": "GP",
            "horizon_days": 5,
            "predicted_at": None,
            "predicted_direction": "up",
            "confidence": 0.65,
            "price_at_prediction": 100.0,
            "price_at_horizon": 110.0,
        }
    ])
    pool.execute = AsyncMock()

    count = await populate_outcomes(pool)

    assert count == 1
    pool.execute.assert_called_once()
    call_args = pool.execute.call_args[0]
    # args: sql, id, ticker, horizon, predicted_at, direction, confidence,
    #        price_pred, price_horizon, return_pct, actual_dir, correct, evaluated_at
    # correct is at index 11
    assert call_args[11] is True


@pytest.mark.asyncio
async def test_populate_outcomes_marks_incorrect_when_prediction_wrong():
    from ml.monitoring.accuracy_report import populate_outcomes

    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[
        {
            "id": 2,
            "ticker": "BRACBANK",
            "horizon_days": 10,
            "predicted_at": None,
            "predicted_direction": "up",
            "confidence": 0.60,
            "price_at_prediction": 50.0,
            "price_at_horizon": 45.0,   # went down, predicted up → wrong
        }
    ])
    pool.execute = AsyncMock()

    await populate_outcomes(pool)

    call_args = pool.execute.call_args[0]
    assert call_args[11] is False   # correct = False


@pytest.mark.asyncio
async def test_populate_outcomes_skips_rows_with_missing_prices():
    from ml.monitoring.accuracy_report import populate_outcomes

    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[
        {
            "id": 3, "ticker": "X", "horizon_days": 5, "predicted_at": None,
            "predicted_direction": "up", "confidence": 0.6,
            "price_at_prediction": None,   # no price data
            "price_at_horizon": None,
        }
    ])
    pool.execute = AsyncMock()

    count = await populate_outcomes(pool)

    assert count == 0
    pool.execute.assert_not_called()


# ── generate_report ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_report_structure():
    from ml.monitoring.accuracy_report import generate_report

    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[
        {"horizon_days": 5,  "total": 100, "correct": 55, "accuracy": 0.55},
        {"horizon_days": 10, "total": 80,  "correct": 40, "accuracy": 0.50},
        {"horizon_days": 20, "total": 60,  "correct": 31, "accuracy": 0.517},
    ])

    report = await generate_report(pool)

    assert "by_horizon" in report
    assert "generated_at" in report
    assert len(report["by_horizon"]) == 3
    assert report["by_horizon"][0]["horizon_days"] == 5


@pytest.mark.asyncio
async def test_generate_report_empty_when_no_outcomes():
    from ml.monitoring.accuracy_report import generate_report

    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])

    report = await generate_report(pool)
    assert report["by_horizon"] == []


# ── check_accuracy_thresholds ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_accuracy_returns_critical_below_45pct():
    from ml.monitoring.accuracy_report import check_accuracy_thresholds

    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[
        {"horizon_days": 5, "total": 100, "correct": 43, "accuracy": 0.43},
    ])
    result = await check_accuracy_thresholds(pool, warning_threshold=0.48, critical_threshold=0.45)
    assert result["alert_level"] == "CRITICAL"
    assert result["worst_horizon"] == 5


@pytest.mark.asyncio
async def test_check_accuracy_returns_warning_between_45_and_48():
    from ml.monitoring.accuracy_report import check_accuracy_thresholds

    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[
        {"horizon_days": 10, "total": 100, "correct": 47, "accuracy": 0.47},
    ])
    result = await check_accuracy_thresholds(pool, warning_threshold=0.48, critical_threshold=0.45)
    assert result["alert_level"] == "WARNING"


@pytest.mark.asyncio
async def test_check_accuracy_returns_none_when_ok():
    from ml.monitoring.accuracy_report import check_accuracy_thresholds

    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[
        {"horizon_days": 5,  "total": 100, "correct": 55, "accuracy": 0.55},
        {"horizon_days": 20, "total": 100, "correct": 52, "accuracy": 0.52},
    ])
    result = await check_accuracy_thresholds(pool)
    assert result["alert_level"] is None


@pytest.mark.asyncio
async def test_check_accuracy_skips_small_sample_horizons():
    from ml.monitoring.accuracy_report import check_accuracy_thresholds

    pool = MagicMock()
    # 5 outcomes — below min_samples=30, must not trigger alert
    pool.fetch = AsyncMock(return_value=[
        {"horizon_days": 5, "total": 5, "correct": 0, "accuracy": 0.0},
    ])
    result = await check_accuracy_thresholds(pool, min_samples=30)
    assert result["alert_level"] is None
    assert result["worst_horizon"] is None


@pytest.mark.asyncio
async def test_check_accuracy_picks_worst_horizon():
    from ml.monitoring.accuracy_report import check_accuracy_thresholds

    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[
        {"horizon_days": 5,  "total": 100, "correct": 55, "accuracy": 0.55},
        {"horizon_days": 10, "total": 100, "correct": 47, "accuracy": 0.47},  # worst
        {"horizon_days": 20, "total": 100, "correct": 50, "accuracy": 0.50},
    ])
    result = await check_accuracy_thresholds(pool, warning_threshold=0.48, critical_threshold=0.45)
    assert result["worst_horizon"] == 10
    assert result["alert_level"] == "WARNING"
