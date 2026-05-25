"""Tests for ml.scoring.health_score."""
from __future__ import annotations

import pytest
from ml.scoring.health_score import compute_health_score


def test_all_scores_present():
    score = compute_health_score(
        fundamental_score=0.7,
        momentum_score=0.6,
        valuation_score=0.8,
        sentiment_score=0.5,
    )
    assert 0 <= score <= 100


def test_missing_scores_use_neutral():
    # With only fundamental_score, others default to 0.5 (neutral)
    score = compute_health_score(fundamental_score=1.0)
    assert 0 <= score <= 100


def test_all_zero_gives_low_score():
    score = compute_health_score(
        fundamental_score=0.0,
        momentum_score=0.0,
        valuation_score=0.0,
        sentiment_score=0.0,
    )
    assert score < 10


def test_all_one_gives_high_score():
    score = compute_health_score(
        fundamental_score=1.0,
        momentum_score=1.0,
        valuation_score=1.0,
        sentiment_score=1.0,
    )
    assert score > 90


def test_score_clipped_0_to_100():
    score = compute_health_score(
        fundamental_score=1.5,  # out of range — should clip
        momentum_score=1.5,
        valuation_score=1.5,
        sentiment_score=1.5,
    )
    assert score <= 100
