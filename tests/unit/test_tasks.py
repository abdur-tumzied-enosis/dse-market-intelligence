"""Unit tests for ML Celery tasks in extraction/tasks.py."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from extraction import tasks


def test_run_ml_inference_delegates_to_async_inner():
    """Task calls _run_ml_inference_async and returns its result."""
    expected = {"fundamental_scored": 3, "lstm_predicted": 5, "dcf_valued": 2}
    mock = AsyncMock(return_value=expected)

    with patch.object(tasks, "_run_ml_inference_async", mock):
        result = tasks.run_ml_inference.apply().get()

    mock.assert_called_once()
    assert result == expected


def test_run_ml_inference_returns_dict_with_expected_keys():
    """Task result always has three count keys."""
    mock = AsyncMock(return_value={"fundamental_scored": 0, "lstm_predicted": 0, "dcf_valued": 0})

    with patch.object(tasks, "_run_ml_inference_async", mock):
        result = tasks.run_ml_inference.apply().get()

    assert "fundamental_scored" in result
    assert "lstm_predicted" in result
    assert "dcf_valued" in result


def test_retrain_ml_models_delegates_to_async_inner():
    """Task calls _retrain_ml_models_async and returns its result."""
    expected = {"outcomes_evaluated": 12, "version": "20260525", "errors": []}
    mock = AsyncMock(return_value=expected)

    with patch.object(tasks, "_retrain_ml_models_async", mock):
        result = tasks.retrain_ml_models.apply().get()

    mock.assert_called_once()
    assert result == expected


def test_retrain_ml_models_returns_dict_with_expected_keys():
    """Task result always has three keys."""
    mock = AsyncMock(return_value={"outcomes_evaluated": 0, "version": "20260525", "errors": []})

    with patch.object(tasks, "_retrain_ml_models_async", mock):
        result = tasks.retrain_ml_models.apply().get()

    assert "outcomes_evaluated" in result
    assert "version" in result
    assert "errors" in result
