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

from datetime import UTC, date, datetime, timedelta

import pytest

from api.analysis.wyckoff import (
    EVENT_HELP,
    EVENT_LABEL,
    LOOKBACK_BARS,
    Bar,
    WyckoffEvent,
    WyckoffRange,
    clip_ranges_to_window,
    detect_wyckoff,
)

#: The full 14-value Wyckoff event union the detector may emit (UPTHRUST was
#: split into UT / UTAD and removed). Every emitted event's ``type`` must be a
#: member of this set.
WYCKOFF_EVENT_TYPES = {
    "SC",
    "BC",
    "SPRING",
    "PS",
    "AR",
    "ST",
    "TEST",
    "SOS",
    "LPS",
    "PSY",
    "UT",
    "UTAD",
    "SOW",
    "LPSY",
}

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
# Full-schematic builders — engineered to surface the entire event sequence.
#
# These series are hand-tuned against the detector's named thresholds so the
# state machine fires every optional event in chronological order:
#   ACCUMULATION:  PS -> SC -> AR -> ST -> Spring -> Test -> SOS -> LPS
#   DISTRIBUTION:  PSY -> BC -> AR -> ST -> UT -> UTAD -> SOW -> LPSY
#
# Key tuning notes (so the asserts below stay legible):
#   - The flat band is built with asymmetric wicks: low-bars wick down to the
#     support anchor, high-bars wick up to the resistance anchor, so the 10th /
#     90th-percentile S/R cluster locks onto stable levels. This keeps the ST
#     (which returns *near* but not *through* the climax extreme) from drifting
#     the percentile onto itself and being mis-read as a Spring / UT.
#   - The climax bar carries ~3x volume and a spread >= CLIMAX_MIN_SPREAD.
#   - PS / PSY are pre-climax bars carrying >= PRELIM_VOLUME_MULT volume.
#   - In distribution, an early in-range upthrust (before UTAD_LATE_FRAC of the
#     range) is labeled UT; a later terminal one (after the ST, past the late
#     mark) is promoted to UTAD — hence the long mid-range consolidation.
# ---------------------------------------------------------------------------


def _accum_flat(i: int, bars: list[dict], n: int, vol: float = 600.0) -> int:
    """Append `n` flat accumulation band bars (closes 69<->71); low-bars wick
    down to the 68.0 support anchor, high-bars up to the 71.4 resistance."""
    for k in range(n):
        if k % 2:
            bars.append(_bar(i, 71.0, 71.4, 70.6, 71.0, vol))  # high bar
        else:
            bars.append(_bar(i, 69.0, 69.4, 68.0, 69.0, vol))  # low bar -> support
        i += 1
    return i


def _full_accumulation_series() -> list[dict]:
    """Downtrend -> wide flat range carrying the full accumulation schematic:
    PS -> SC -> AR -> ST -> Spring -> Test -> SOS -> LPS."""
    bars: list[dict] = []
    i = 0

    # Prior downtrend (normal volume) -> classifies the range as accumulation.
    for k in range(30):
        p = 120.0 - k * 1.6
        bars.append(_bar(i, p, p + 0.3, p - 0.3, p - 0.5, 1000))
        i += 1

    # Long flat lead-in so the range span opens well before the climax,
    # leaving room for a Preliminary Support bar inside the span.
    i = _accum_flat(i, bars, 25)

    # PS — preliminary support: elevated-volume bar before the climax.
    bars.append(_bar(i, 70.5, 70.7, 68.4, 68.7, 1600))
    i += 1
    i = _accum_flat(i, bars, 4)

    # SC — selling climax: wide down-bar near support on ~3x volume.
    bars.append(_bar(i, 70.0, 70.2, 66.8, 67.4, 2200))
    i += 1

    # AR — automatic rally: sharp bounce to the top of the band (its low sits
    # well above support so it does not pre-empt the ST as the nearest-low bar).
    bars.append(_bar(i, 68.6, 72.0, 68.5, 71.8, 800))
    i += 1
    i = _accum_flat(i, bars, 3)

    # ST — secondary test: returns near (but above) support on light volume.
    bars.append(_bar(i, 68.9, 69.2, 68.4, 68.6, 900))
    i += 1
    i = _accum_flat(i, bars, 3)

    # Spring — deep false breakdown below support, closes back inside.
    bars.append(_bar(i, 68.5, 69.0, 65.0, 69.0, 700))
    i += 1

    # Test — low-volume retest of the low after the spring.
    bars.append(_bar(i, 68.0, 68.4, 66.9, 67.6, 400))
    i += 1
    i = _accum_flat(i, bars, 2)

    # SOS — sign of strength: wide up-bar on heavy volume breaking resistance.
    bars.append(_bar(i, 69.5, 74.5, 69.3, 74.0, 1200))
    i += 1

    # LPS — last point of support: pullback holding near old resistance.
    bars.append(_bar(i, 72.0, 72.5, 70.8, 71.6, 700))
    i += 1
    i = _accum_flat(i, bars, 3)

    return bars


def _dist_flat(i: int, bars: list[dict], n: int, vol: float = 600.0) -> int:
    """Append `n` flat distribution band bars (closes 129<->131); high-bars
    wick up to the 132.0 resistance anchor, low-bars down to 128.6."""
    for k in range(n):
        if k % 2:
            bars.append(_bar(i, 131.0, 132.0, 130.6, 131.0, vol))  # high -> res
        else:
            bars.append(_bar(i, 129.0, 129.4, 128.6, 129.0, vol))  # low bar
        i += 1
    return i


def _full_distribution_series() -> list[dict]:
    """Uptrend -> wide flat range carrying the full distribution schematic:
    PSY -> BC -> AR -> ST -> UT -> UTAD -> SOW -> LPSY (mirror of accumulation).
    """
    bars: list[dict] = []
    i = 0

    # Prior uptrend -> classifies the range as distribution.
    for k in range(30):
        p = 80.0 + k * 1.6
        bars.append(_bar(i, p, p + 0.3, p - 0.3, p + 0.5, 1000))
        i += 1

    i = _dist_flat(i, bars, 25)

    # PSY — preliminary supply: elevated-volume up-ish bar before the climax.
    bars.append(_bar(i, 129.5, 131.6, 129.3, 131.3, 1600))
    i += 1
    i = _dist_flat(i, bars, 4)

    # BC — buying climax: wide up-bar near resistance on ~3x volume
    # (spread >= CLIMAX_MIN_SPREAD = 0.04).
    bars.append(_bar(i, 129.2, 134.0, 128.5, 133.2, 2200))
    i += 1

    # AR — automatic reaction: sharp drop to the bottom of the band (its high
    # sits well below resistance so it does not pre-empt the ST).
    bars.append(_bar(i, 131.4, 131.5, 128.0, 128.2, 800))
    i += 1
    i = _dist_flat(i, bars, 3)

    # ST — secondary test: returns near (but below) resistance on light volume.
    bars.append(_bar(i, 131.1, 131.6, 131.0, 131.4, 900))
    i += 1
    i = _dist_flat(i, bars, 2)

    # UT — early in-range upthrust: pierces above resistance, closes inside.
    bars.append(_bar(i, 131.0, 135.0, 131.0, 131.0, 700))
    i += 1

    # Long mid-range consolidation so the next upthrust falls past the
    # UTAD_LATE_FRAC mark and is promoted to the terminal UTAD.
    i = _dist_flat(i, bars, 14)

    # UTAD — terminal (late) upthrust above resistance after the ST.
    bars.append(_bar(i, 131.5, 135.5, 131.4, 131.5, 650))
    i += 1

    # SOW — sign of weakness: wide down-bar on heavy volume breaking support.
    bars.append(_bar(i, 130.5, 131.0, 126.0, 126.5, 1200))
    i += 1

    # LPSY — last point of supply: weak rally failing at old support.
    bars.append(_bar(i, 128.0, 129.2, 127.5, 128.4, 700))
    i += 1
    i = _dist_flat(i, bars, 2)

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
        # UPTHRUST was split into UT (in-range false break) and UTAD (terminal,
        # late upthrust). Either flavour satisfies "an upthrust was detected".
        types = [e.type for e in ranges[0].events]
        assert "UT" in types or "UTAD" in types

    def test_bc_label_and_price(self, ranges):
        bc = next(e for e in ranges[0].events if e.type == "BC")
        assert bc.label == "BC"
        # BC marker sits at the bar high.
        assert bc.price == pytest.approx(112.4, abs=0.5)

    def test_upthrust_label(self, ranges):
        ut = next(e for e in ranges[0].events if e.type in ("UT", "UTAD"))
        assert ut.label in ("UT", "UTAD")
        # Pierced above resistance.
        assert ut.price > ranges[0].resistance


# ---------------------------------------------------------------------------
# Full accumulation schematic — PS, SC, AR, ST, Spring, Test, SOS, LPS
# ---------------------------------------------------------------------------

class TestFullAccumulationSchematic:
    @pytest.fixture(scope="class")
    def ranges(self):
        return detect_wyckoff(_full_accumulation_series())

    @pytest.fixture(scope="class")
    def events(self, ranges):
        assert len(ranges) == 1
        return ranges[0].events

    def test_phase_is_accumulation(self, ranges):
        assert ranges[0].phase == "accumulation"

    def test_full_event_set_detected(self, events):
        # The detector should surface (close to) the entire accumulation
        # schematic for this purpose-built series.
        types = {e.type for e in events}
        expected = {"PS", "SC", "AR", "ST", "SPRING", "TEST", "SOS", "LPS"}
        missing = expected - types
        assert not missing, f"missing accumulation events: {sorted(missing)}"

    def test_spring_still_detected(self, events):
        # Regression guard: the headline accumulation signal must survive.
        assert "SPRING" in {e.type for e in events}

    def test_events_in_chronological_order(self, events):
        days = [e.day for e in events]
        assert days == sorted(days), "events must be emitted in day order"

    def test_sc_precedes_ar_precedes_sos(self, events):
        day_of = {e.type: e.day for e in events}
        assert day_of["SC"] < day_of["AR"], "SC must precede AR"
        assert day_of["AR"] < day_of["SOS"], "AR must precede SOS"

    def test_ps_precedes_sc(self, events):
        # PS is optional, but when emitted it must come before the climax.
        types = {e.type for e in events}
        if "PS" in types:
            day_of = {e.type: e.day for e in events}
            assert day_of["PS"] < day_of["SC"], "PS must precede SC"

    def test_no_upthrust_type_in_accumulation(self, events):
        # UPTHRUST was removed entirely; an accumulation range never carries it.
        types = {e.type for e in events}
        assert "UPTHRUST" not in types
        assert types.isdisjoint({"UT", "UTAD"})


# ---------------------------------------------------------------------------
# Full distribution schematic — PSY, BC, AR, ST, UT, UTAD, SOW, LPSY
# ---------------------------------------------------------------------------

class TestFullDistributionSchematic:
    @pytest.fixture(scope="class")
    def ranges(self):
        return detect_wyckoff(_full_distribution_series())

    @pytest.fixture(scope="class")
    def events(self, ranges):
        assert len(ranges) == 1
        return ranges[0].events

    def test_phase_is_distribution(self, ranges):
        assert ranges[0].phase == "distribution"

    def test_full_event_set_detected(self, events):
        types = {e.type for e in events}
        expected = {"PSY", "BC", "AR", "ST", "UT", "UTAD", "SOW", "LPSY"}
        missing = expected - types
        assert not missing, f"missing distribution events: {sorted(missing)}"

    def test_events_in_chronological_order(self, events):
        days = [e.day for e in events]
        assert days == sorted(days), "events must be emitted in day order"

    def test_bc_precedes_ar(self, events):
        day_of = {e.type: e.day for e in events}
        assert day_of["BC"] < day_of["AR"], "BC must precede AR"

    def test_utad_after_st(self, events):
        # UTAD is the terminal upthrust — when emitted it must follow the ST.
        types = {e.type for e in events}
        if "UTAD" in types:
            day_of = {e.type: e.day for e in events}
            assert "ST" in types, "UTAD without a preceding ST is inconsistent"
            assert day_of["UTAD"] > day_of["ST"], "UTAD must occur after the ST"

    def test_ut_precedes_utad(self, events):
        # When both flavours are present the in-range UT precedes the terminal
        # UTAD.
        types = {e.type for e in events}
        if "UT" in types and "UTAD" in types:
            day_of = {e.type: e.day for e in events}
            assert day_of["UT"] < day_of["UTAD"]

    def test_no_upthrust_type_ever_emitted(self, events):
        # The generic UPTHRUST type was intentionally removed (split into
        # UT / UTAD). It must never appear.
        types = [e.type for e in events]
        assert "UPTHRUST" not in types


# ---------------------------------------------------------------------------
# Global event invariants — across every schematic series
# ---------------------------------------------------------------------------

class TestEventInvariants:
    @pytest.fixture(scope="class")
    def all_events(self):
        ranges = (
            detect_wyckoff(_accumulation_series())
            + detect_wyckoff(_distribution_series())
            + detect_wyckoff(_full_accumulation_series())
            + detect_wyckoff(_full_distribution_series())
        )
        return [e for r in ranges for e in r.events]

    def test_we_have_events_to_check(self, all_events):
        assert all_events

    def test_every_type_within_union(self, all_events):
        for e in all_events:
            assert e.type in WYCKOFF_EVENT_TYPES, f"unknown event type {e.type!r}"

    def test_no_upthrust_anywhere(self, all_events):
        assert all(e.type != "UPTHRUST" for e in all_events)

    def test_every_event_has_nonempty_help(self, all_events):
        for e in all_events:
            assert isinstance(e.help, str)
            assert e.help.strip(), f"empty help for {e.type}"
            # Help must match the canonical text for that type.
            assert e.help == EVENT_HELP[e.type]

    def test_every_event_has_nonempty_label(self, all_events):
        for e in all_events:
            assert isinstance(e.label, str)
            assert e.label.strip(), f"empty label for {e.type}"
            assert e.label == EVENT_LABEL[e.type]


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


# ---------------------------------------------------------------------------
# Visible-window clipping (short-range fix: 1M/3M starved without lead-in).
# Detection runs over buffered data extending LOOKBACK_BARS before the window;
# clip_ranges_to_window restricts the *display* back to the requested window.
# ---------------------------------------------------------------------------


class TestClipRangesToWindow:
    def _range(
        self, start: date, end: date, event_days: list[date]
    ) -> WyckoffRange:
        return WyckoffRange(
            start_day=start,
            end_day=end,
            phase="accumulation",
            phase_help="x",
            confidence=0.5,
            support=1.0,
            resistance=2.0,
            events=[
                WyckoffEvent(day=d, type="ST", price=1.5, label="ST", help="y")
                for d in event_days
            ],
        )

    def test_lookback_covers_warmup_and_prior_trend(self):
        # The buffer must be large enough for the band warm-up + prior-trend.
        from api.analysis.wyckoff import (
            PRIOR_TREND_WINDOW,
            ROLLING_BAND_WINDOW,
        )

        assert LOOKBACK_BARS >= ROLLING_BAND_WINDOW + PRIOR_TREND_WINDOW

    def test_none_from_date_returns_unchanged(self):
        ranges = [self._range(date(2025, 1, 1), date(2025, 2, 1), [date(2025, 1, 10)])]
        assert clip_ranges_to_window(ranges, None) is ranges

    def test_drops_range_entirely_before_window(self):
        ranges = [self._range(date(2025, 1, 1), date(2025, 1, 20), [date(2025, 1, 10)])]
        assert clip_ranges_to_window(ranges, date(2025, 2, 1)) == []

    def test_keeps_range_overlapping_window_clamps_start(self):
        window = date(2025, 2, 1)
        ranges = [
            self._range(
                date(2025, 1, 1),
                date(2025, 3, 1),
                [date(2025, 1, 15), date(2025, 2, 10)],
            )
        ]
        out = clip_ranges_to_window(ranges, window)
        assert len(out) == 1
        # start clamped to the window edge; the pre-window event dropped.
        assert out[0].start_day == window
        assert [e.day for e in out[0].events] == [date(2025, 2, 10)]

    def test_short_window_recovers_with_lookback_buffer(self):
        # Regression for the reported bug: a bare short window detects nothing,
        # but prepending LOOKBACK_BARS of lead-in recovers the range.
        bars: list[dict[str, object]] = []
        d = date(2025, 1, 1)
        px = 160.0
        for _ in range(60):  # downtrend lead-in
            px -= 1.0
            bars.append(
                dict(day=d, open=px + 0.5, high=px + 1.0, low=px - 1.0, close=px, volume=100000)
            )
            d += timedelta(days=1)
        for i in range(40):  # flat consolidation
            c = 100.0 + ((-1) ** i) * 1.5
            bars.append(
                dict(day=d, open=c, high=c + 2.0, low=c - 2.0, close=c, volume=80000)
            )
            d += timedelta(days=1)

        view_start = len(bars) - 21  # ~1M of visible bars
        from_date = bars[view_start]["day"]
        assert isinstance(from_date, date)

        bare = detect_wyckoff(bars[view_start:])
        buffered = clip_ranges_to_window(
            detect_wyckoff(bars[max(0, view_start - LOOKBACK_BARS):]), from_date
        )

        assert bare == []  # bare short window starves
        assert buffered  # lead-in buffer recovers detection
        assert all(r.end_day >= from_date for r in buffered)

    def test_timestamptz_day_normalized_to_date(self):
        # DB `day` is timestamptz -> bars arrive as datetime. Detection must
        # emit `date` boundaries so clip_ranges_to_window can compare against a
        # `date` window bound without raising (regression: datetime vs date).
        bars: list[dict[str, object]] = []
        dt = datetime(2025, 1, 1, tzinfo=UTC)
        px = 160.0
        for _ in range(40):  # downtrend lead-in
            px -= 1.0
            bars.append(
                dict(day=dt, open=px + 0.5, high=px + 1.0, low=px - 1.0, close=px, volume=100000)
            )
            dt += timedelta(days=1)
        for i in range(40):  # flat consolidation
            c = 100.0 + ((-1) ** i) * 1.5
            bars.append(
                dict(day=dt, open=c, high=c + 2.0, low=c - 2.0, close=c, volume=80000)
            )
            dt += timedelta(days=1)

        ranges = detect_wyckoff(bars)
        assert ranges
        for r in ranges:
            assert type(r.start_day) is date  # not datetime
            assert type(r.end_day) is date
            for e in r.events:
                assert type(e.day) is date

        # Must not raise comparing date boundaries to a date window bound.
        clipped = clip_ranges_to_window(ranges, date(2025, 2, 15))
        assert all(r.end_day >= date(2025, 2, 15) for r in clipped)
