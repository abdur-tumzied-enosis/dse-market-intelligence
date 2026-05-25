"""Tests that job_nightly_ml and job_quarterly dispatch to Celery, not run ML inline."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from extraction import tasks
from extraction.scheduler import job_nightly_ml, job_quarterly


async def test_job_nightly_ml_enqueues_run_ml_inference():
    """job_nightly_ml must call run_ml_inference.delay() — never run ML inline."""
    mock_delay = MagicMock()
    with patch.object(tasks.run_ml_inference, "delay", mock_delay):
        await job_nightly_ml()
    mock_delay.assert_called_once_with()


async def test_job_quarterly_enqueues_retrain_ml_models():
    """job_quarterly must call retrain_ml_models.delay() — never run retrain inline."""
    mock_delay = MagicMock()
    with patch.object(tasks.retrain_ml_models, "delay", mock_delay):
        await job_quarterly()
    mock_delay.assert_called_once_with()
