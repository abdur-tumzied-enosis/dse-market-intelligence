"""
Unit tests for the rule-based Wyckoff detector (api/analysis/wyckoff.py).

Fully offline: every test builds a hand-crafted synthetic OHLCV series and
feeds it to `detect_wyckoff`. No DB, no network, no fixtures.

The synthetic series are tuned against the detector's named thresholds:
  - ranges need >= MIN_RANGE_BARS (15) consecutive bars inside a flat, tight
    rolling-20 band, so each series has a ~30-bar prior trend + a long
    (~50-bar) flat consolidation. The flat band is deliberately *wide*
    (close swings ~+/-2 around the midline) so that a climax bar (spread >=
    CLIMAX_MIN_SPREAD) can still sit inside the 10th/90th-percentile S/R
    cluster without poking through the opposite edge and double-marking.
  - climax bars carry CLIMAX_VOLUME_MULT-x volume; spring/upthrust bars pierce
    the S/R cluster and close back inside.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from api.analysis.wyckoff import Bar, detect_wyckoff

# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

START = date(2025, 1, 1)


def _bar(idx: int, open_: float, high: float, low: float, close: float, volume: float) -> dict:
    """Build one OHLCV dict bar `idx` days after START."""
    return {
        "day": START + timedelta(days=idx),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def _accumulation_series() -> list[dict]:
    """Downtrend -> wide flat range with a Selling Climax then a Spring -> drift.

    Layout (indices):
      0..29   prior downtrend 120 -> 73.6 (normal volume)
      30..54  flat range, close swings 68 <-> 72 (low, dried-up volume)
      55      SELLING CLIMAX: wide down-bar near support, ~3x volume
      56..63  flat range
      64      SPRING: low pierces below support, closes back inside
      65..79  flat range
    """
    bars: list[dict] = []
    i = 0

    # Prior downtrend.
    for k in range(30):
        p = 120.0 - k * 1.6
        bars.append(_bar(i, p, p + 0.3, p - 0.3, p - 0.5, 1000))
        i += 1

    # Flat consolidation lead-in (wide band: close 68 <-> 72).
    for k in range(25):
        p = 72.0 if k % 2 else 68.0
        bars.append(_bar(i, p, p + 0.5, p - 0.5, p, 600))
        i += 1

    # Selling Climax: wide down-bar, low near support, ~3x volume.
    # spread = (71.0 - 67.6) / 68.2 = 0.0499 >= CLIMAX_MIN_SPREAD, close < open.
    bars.append(_bar(i, 70.8, 71.0, 67.6, 68.2, 1800))
    i += 1

    for k in range(8):
        p = 72.0 if k % 2 else 68.0
        bars.append(_bar(i, p, p + 0.5, p - 0.5, p, 600))
        i += 1

    # Spring: low pierces well below support, closes back inside the band.
    bars.append(_bar(i, 69.0, 70.0, 65.5, 70.0, 700))
    i += 1

    for k in range(15):
        p = 72.0 if k % 2 else 68.0
        bars.append(_bar(i, p, p + 0.5, p - 0.5, p, 600))
        i += 1

    return bars


def _distribution_series() -> list[dict]:
    """Uptrend -> wide flat range with a Buying Climax then an Upthrust.

    Mirror image of the accumulation series.
    """
    bars: list[dict] = []
    i = 0

    # Prior uptrend 70 -> 110.6.
    for k in range(30):
        p = 70.0 + k * 1.4
        bars.append(_bar(i, p, p + 0.3, p - 0.3, p + 0.5, 1000))
        i += 1

    # Flat consolidation (close 108 <-> 112).
    for k in range(25):
        p = 112.0 if k % 2 else 108.0
        bars.append(_bar(i, p, p + 0.5, p - 0.5, p, 600))
        i += 1

    # Buying Climax: wide up-bar, high near resistance, ~3x volume.
    # spread = (112.4 - 107.9) / 111.9 = 0.0402 >= CLIMAX_MIN_SPREAD, close > open,
    # high stays just below the 90th-pct resistance so it does NOT also mark an upthrust.
    bars.append(_bar(i, 108.5, 112.4, 107.9, 111.9, 1800))
    i += 1

    for k in range(8):
        p = 112.0 if k % 2 else 108.0
        bars.append(_bar(i, p, p + 0.5, p - 0.5, p, 600))
        i += 1

    # Upthrust: high pierces above resistance, closes back inside.
    bars.append(_bar(i, 110.5, 114.5, 110.0, 110.0, 700))
    i += 1

    for k in range(15):
        p = 112.0 if k % 2 else 108.0
        bars.append(_bar(i, p, p + 0.5, p - 0.5, p, 600))
        i += 1

    return bars


def _pure_trend_series() -> list[dict]:
    """Steady uptrend with no consolidation at all — should yield no ranges."""
    bars: list[dict] = []
    for k in range(80):
        p = 50.0 + k * 1.2
        bars.append(_bar(k, p, p + 0.4, p - 0.4, p + 0.6, 1000))
    return bars


# ---------------------------------------------------------------------------
# Accumulation
# ---------------------------------------------------------------------------

class TestAccumulation:
    @pytest.fixture(scope="class")
    def ranges(self):
        return detect_wyckoff(_accumulation_series())

    def test_one_range_detected(self, ranges):
        assert len(ranges) == 1

    def test_phase_is_accumulation(self, ranges):
        assert ranges[0].phase == "accumulation"

    def test_confidence_positive(self, ranges):
        # Clear prior downtrend + volume dry-up -> meaningfully confident.
        assert 0.0 < ranges[0].confidence <= 1.0

    def test_support_resistance_match_band(self, ranges):
        r = ranges[0]
        # Band midline ~70; cluster edges should bracket it tightly.
        assert r.support == pytest.approx(67.5, abs=1.0)
        assert r.resistance == pytest.approx(72.5, abs=1.0)
        assert r.support < r.resistance

    def test_selling_climax_present(self, ranges):
        types = [e.type for e in ranges[0].events]
        assert "SC" in types

    def test_spring_present(self, ranges):
        types = [e.type for e in ranges[0].events]
        assert "SPRING" in types

    def test_sc_label_and_price(self, ranges):
        sc = next(e for e in ranges[0].events if e.type == "SC")
        assert sc.label == "SC"
        # SC marker sits at the bar low.
        assert sc.price == pytest.approx(67.6, abs=0.5)

    def test_spring_label(self, ranges):
        spring = next(e for e in ranges[0].events if e.type == "SPRING")
        assert spring.label == "Spring"
        # Pierced below support.
        assert spring.price < ranges[0].support


# ---------------------------------------------------------------------------
# Distribution
# ---------------------------------------------------------------------------

class TestDistribution:
    @pytest.fixture(scope="class")
    def ranges(self):
        return detect_wyckoff(_distribution_series())

    def test_one_range_detected(self, ranges):
        assert len(ranges) == 1

    def test_phase_is_distribution(self, ranges):
        assert ranges[0].phase == "distribution"

    def test_confidence_positive(self, ranges):
        assert 0.0 < ranges[0].confidence <= 1.0

    def test_buying_climax_present(self, ranges):
        types = [e.type for e in ranges[0].events]
        assert "BC" in types

    def test_upthrust_present(self, ranges):
        types = [e.type for e in ranges[0].events]
        assert "UPTHRUST" in types

    def test_bc_label_and_price(self, ranges):
        bc = next(e for e in ranges[0].events if e.type == "BC")
        assert bc.label == "BC"
        # BC marker sits at the bar high.
        assert bc.price == pytest.approx(112.4, abs=0.5)

    def test_upthrust_label(self, ranges):
        ut = next(e for e in ranges[0].events if e.type == "UPTHRUST")
        assert ut.label == "Upthrust"
        # Pierced above resistance.
        assert ut.price > ranges[0].resistance


# ---------------------------------------------------------------------------
# Edge cases — no false positives
# ---------------------------------------------------------------------------

class TestNoFalsePositives:
    def test_pure_trend_yields_no_ranges(self):
        assert detect_wyckoff(_pure_trend_series()) == []

    def test_empty_input(self):
        assert detect_wyckoff([]) == []

    def test_short_input(self):
        # Fewer than MIN_RANGE_BARS bars -> nothing.
        short = _accumulation_series()[:5]
        assert detect_wyckoff(short) == []

    def test_accepts_bar_dataclasses(self):
        # Same series, but built as Bar dataclasses instead of dicts.
        dict_bars = _accumulation_series()
        bar_objs = [
            Bar(
                day=b["day"],
                open=b["open"],
                high=b["high"],
                low=b["low"],
                close=b["close"],
                volume=b["volume"],
            )
            for b in dict_bars
        ]
        from_dicts = detect_wyckoff(dict_bars)
        from_objs = detect_wyckoff(bar_objs)
        assert len(from_objs) == len(from_dicts) == 1
        assert from_objs[0].phase == from_dicts[0].phase


# ---------------------------------------------------------------------------
# Help text — every event and range carries plain-language help
# ---------------------------------------------------------------------------

class TestHelpText:
    @pytest.fixture(scope="class")
    def all_ranges(self):
        return detect_wyckoff(_accumulation_series()) + detect_wyckoff(_distribution_series())

    def test_each_range_has_phase_help(self, all_ranges):
        assert all_ranges  # sanity: we have ranges to check
        for r in all_ranges:
            assert isinstance(r.phase_help, str)
            assert r.phase_help.strip(), f"empty phase_help for {r.phase}"

    def test_each_event_has_help(self, all_ranges):
        events = [e for r in all_ranges for e in r.events]
        assert events  # sanity: we have events to check
        for e in events:
            assert isinstance(e.help, str)
            assert e.help.strip(), f"empty help for event {e.type}"

    def test_each_event_has_nonempty_label(self, all_ranges):
        events = [e for r in all_ranges for e in r.events]
        for e in events:
            assert isinstance(e.label, str)
            assert e.label.strip()
