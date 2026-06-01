"""Pure rule-based Wyckoff detector.

No DB, no FastAPI imports. Operates over a list of OHLCV bars and returns
trading ranges with phase classification and high-confidence volume events
(Selling Climax, Buying Climax, Spring, Upthrust).

Design bias: precision over recall — fewer, high-confidence calls on noisy
DSE data. Every threshold below is a tunable named module constant.

The router maps the returned dataclasses to pydantic response models.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# --------------------------------------------------------------------------
# Tunable thresholds (named constants)
# --------------------------------------------------------------------------

#: Window for the rolling high/low band used to detect a trading range.
ROLLING_BAND_WINDOW: int = 20
#: Max relative band width (hi - lo) / mid for a bar to count as "in range".
MAX_BAND_WIDTH: float = 0.18
#: Max absolute normalized regression slope (per-bar, relative to mid price)
#: for a span of closes to count as "flat".
MAX_FLAT_SLOPE: float = 0.004
#: Minimum number of consecutive qualifying bars to form a trading range.
MIN_RANGE_BARS: int = 15
#: Gap (in bars) below which two qualifying spans are merged into one range.
RANGE_MERGE_GAP: int = 3

#: Look-back window (bars before the range) used to classify the prior trend.
PRIOR_TREND_WINDOW: int = 30
#: Normalized prior-trend slope magnitude above which we call a clear trend.
#: Below this the range stays "undetermined".
PRIOR_TREND_SLOPE_MIN: float = 0.0015
#: Prior-slope magnitude that maps to full confidence from trend alone.
PRIOR_TREND_SLOPE_FULL: float = 0.006

#: Volume multiple over the rolling average that flags a climax bar.
CLIMAX_VOLUME_MULT: float = 2.5
#: Window for the rolling average volume used by climax detection.
CLIMAX_VOLUME_WINDOW: int = 20
#: Minimum bar spread (high - low) / close to count as a "wide" climax bar.
CLIMAX_MIN_SPREAD: float = 0.04
#: Fraction of the range height defining the "edge zone" near support /
#: resistance where a climax must occur.
EDGE_ZONE_FRAC: float = 0.30

#: Max bars within which a false breakout must close back inside the range
#: to count as a spring / upthrust.
FALSE_BREAKOUT_MAX_BARS: int = 3
#: Minimum pierce depth beyond support / resistance, relative to range
#: height, for a spring / upthrust to register (filters noise).
FALSE_BREAKOUT_MIN_PIERCE: float = 0.005

#: Percentile used to derive the support (low cluster) and resistance (high
#: cluster) lines. Using a percentile rather than the absolute min/max keeps a
#: lone false-breakout pierce from defining the band, so springs / upthrusts
#: can still register against the robust consolidation edge.
SR_CLUSTER_PERCENTILE: float = 0.10

#: Weight of the volume-dry-up component in range confidence (the remainder
#: comes from the prior-trend slope magnitude).
DRY_UP_CONFIDENCE_WEIGHT: float = 0.3

# --------------------------------------------------------------------------
# Help text (plain trader words) — shipped per event type / phase.
# --------------------------------------------------------------------------

EVENT_HELP: dict[str, str] = {
    "SC": (
        "Panic dumping — price crashes on huge volume. Often the bottom; "
        "big players buy what scared sellers throw away."
    ),
    "BC": (
        "Buying frenzy — price spikes on huge volume. Often the top; "
        "big players sell into the hype."
    ),
    "SPRING": (
        "Price dips below support to trap sellers, then snaps back up. "
        "Bullish — fake breakdown before a rise."
    ),
    "UPTHRUST": (
        "Price pops above resistance to trap buyers, then drops back. "
        "Bearish — fake breakout before a fall."
    ),
}

PHASE_HELP: dict[str, str] = {
    "accumulation": (
        "Smart money quietly buying sideways after a drop. "
        "Usually comes before an up-move."
    ),
    "distribution": (
        "Smart money quietly selling sideways after a run-up. "
        "Usually comes before a down-move."
    ),
    "undetermined": (
        "Sideways consolidation with no clear prior trend — direction of the "
        "next move is unclear."
    ),
}

# Short marker labels per event type.
EVENT_LABEL: dict[str, str] = {
    "SC": "SC",
    "BC": "BC",
    "SPRING": "Spring",
    "UPTHRUST": "Upthrust",
}

# --------------------------------------------------------------------------
# Data structures
# --------------------------------------------------------------------------


@dataclass
class Bar:
    """A single OHLCV bar."""

    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class WyckoffEvent:
    day: date
    type: str  # "SC" | "BC" | "SPRING" | "UPTHRUST"
    price: float
    label: str
    help: str


@dataclass
class WyckoffRange:
    start_day: date
    end_day: date
    phase: str  # "accumulation" | "distribution" | "undetermined"
    phase_help: str
    confidence: float
    support: float
    resistance: float
    events: list[WyckoffEvent] = field(default_factory=list)


# --------------------------------------------------------------------------
# Small numeric helpers
# --------------------------------------------------------------------------


def _linreg_slope(values: list[float]) -> float:
    """Ordinary-least-squares slope of values against bar index (0..n-1)."""
    n = len(values)
    if n < 2:
        return 0.0
    xs = range(n)
    mean_x = (n - 1) / 2.0
    mean_y = sum(values) / n
    num = 0.0
    den = 0.0
    for x, y in zip(xs, values, strict=True):
        dx = x - mean_x
        num += dx * (y - mean_y)
        den += dx * dx
    if den == 0.0:
        return 0.0
    return num / den


def _normalized_slope(values: list[float]) -> float:
    """Per-bar slope expressed relative to the mean level (unit: fraction/bar)."""
    if not values:
        return 0.0
    mean_y = sum(values) / len(values)
    if mean_y == 0.0:
        return 0.0
    return _linreg_slope(values) / mean_y


def _rolling_avg(values: list[float], end_idx: int, window: int) -> float:
    """Average of `values` over the `window` bars ending at end_idx (inclusive)."""
    start = max(0, end_idx - window + 1)
    span = values[start : end_idx + 1]
    if not span:
        return 0.0
    return sum(span) / len(span)


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


# --------------------------------------------------------------------------
# Step 1 — trading range detection
# --------------------------------------------------------------------------


def _qualifying_bars(bars: list[Bar]) -> list[bool]:
    """Per-bar flag: is this bar inside a flat, tight rolling band?"""
    n = len(bars)
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    closes = [b.close for b in bars]
    flags = [False] * n
    for i in range(n):
        start = max(0, i - ROLLING_BAND_WINDOW + 1)
        if i - start + 1 < ROLLING_BAND_WINDOW:
            # Not enough history yet for a full band.
            continue
        band_hi = max(highs[start : i + 1])
        band_lo = min(lows[start : i + 1])
        mid = (band_hi + band_lo) / 2.0
        if mid <= 0.0:
            continue
        width = (band_hi - band_lo) / mid
        if width > MAX_BAND_WIDTH:
            continue
        slope = abs(_normalized_slope(closes[start : i + 1]))
        if slope > MAX_FLAT_SLOPE:
            continue
        flags[i] = True
    return flags


@dataclass
class _Span:
    start: int  # index into bars (inclusive)
    end: int  # index into bars (inclusive)


def _find_spans(flags: list[bool]) -> list[_Span]:
    """Contiguous runs of qualifying bars, merged across small gaps, filtered
    to those at least MIN_RANGE_BARS long."""
    raw: list[_Span] = []
    i = 0
    n = len(flags)
    while i < n:
        if not flags[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and flags[j + 1]:
            j += 1
        raw.append(_Span(i, j))
        i = j + 1

    if not raw:
        return []

    # Merge adjacent spans separated by a gap <= RANGE_MERGE_GAP.
    merged: list[_Span] = [raw[0]]
    for span in raw[1:]:
        prev = merged[-1]
        if span.start - prev.end - 1 <= RANGE_MERGE_GAP:
            merged[-1] = _Span(prev.start, span.end)
        else:
            merged.append(span)

    return [s for s in merged if (s.end - s.start + 1) >= MIN_RANGE_BARS]


# --------------------------------------------------------------------------
# Step 2 — phase classification
# --------------------------------------------------------------------------


def _classify_phase(bars: list[Bar], span: _Span) -> tuple[str, float]:
    """Classify the range phase from the prior-trend slope, and compute a
    confidence in 0..1 from prior-slope magnitude + in-range volume dry-up.
    Returns (phase, confidence)."""
    prior_start = max(0, span.start - PRIOR_TREND_WINDOW)
    prior_closes = [b.close for b in bars[prior_start : span.start]]
    prior_slope = _normalized_slope(prior_closes) if len(prior_closes) >= 2 else 0.0

    mag = abs(prior_slope)
    if mag < PRIOR_TREND_SLOPE_MIN:
        phase = "undetermined"
    elif prior_slope < 0:
        phase = "accumulation"  # prior downtrend
    else:
        phase = "distribution"  # prior uptrend

    # Trend-strength component (0..1).
    trend_conf = _clamp01(
        (mag - PRIOR_TREND_SLOPE_MIN)
        / max(PRIOR_TREND_SLOPE_FULL - PRIOR_TREND_SLOPE_MIN, 1e-9)
    )

    # Volume-dry-up component: in-range avg volume vs prior-trend avg volume.
    range_vol = [b.volume for b in bars[span.start : span.end + 1]]
    prior_vol = [b.volume for b in bars[prior_start : span.start]]
    dry_up = 0.0
    if range_vol and prior_vol:
        rv = sum(range_vol) / len(range_vol)
        pv = sum(prior_vol) / len(prior_vol)
        if pv > 0:
            # Lower in-range volume → higher dry-up score.
            dry_up = _clamp01(1.0 - rv / pv)

    if phase == "undetermined":
        confidence = 0.0
    else:
        confidence = _clamp01(
            (1.0 - DRY_UP_CONFIDENCE_WEIGHT) * trend_conf
            + DRY_UP_CONFIDENCE_WEIGHT * dry_up
        )
    return phase, round(confidence, 4)


# --------------------------------------------------------------------------
# Step 3 — climaxes (SC / BC)
# --------------------------------------------------------------------------


def _detect_climaxes(
    bars: list[Bar], span: _Span, support: float, resistance: float
) -> list[WyckoffEvent]:
    events: list[WyckoffEvent] = []
    volumes = [b.volume for b in bars]
    height = resistance - support
    if height <= 0.0:
        return events
    edge = height * EDGE_ZONE_FRAC

    for i in range(span.start, span.end + 1):
        bar = bars[i]
        avg_vol = _rolling_avg(volumes, i, CLIMAX_VOLUME_WINDOW)
        if avg_vol <= 0.0 or bar.volume < CLIMAX_VOLUME_MULT * avg_vol:
            continue
        if bar.close <= 0.0:
            continue
        spread = (bar.high - bar.low) / bar.close
        if spread < CLIMAX_MIN_SPREAD:
            continue
        down_bar = bar.close < bar.open
        up_bar = bar.close > bar.open
        # Selling climax: wide down-bar near the support edge.
        if down_bar and bar.low <= support + edge:
            events.append(
                WyckoffEvent(
                    day=bar.day,
                    type="SC",
                    price=bar.low,
                    label=EVENT_LABEL["SC"],
                    help=EVENT_HELP["SC"],
                )
            )
        # Buying climax: wide up-bar near the resistance edge.
        elif up_bar and bar.high >= resistance - edge:
            events.append(
                WyckoffEvent(
                    day=bar.day,
                    type="BC",
                    price=bar.high,
                    label=EVENT_LABEL["BC"],
                    help=EVENT_HELP["BC"],
                )
            )
    return events


# --------------------------------------------------------------------------
# Step 4 — spring / upthrust (false breakouts)
# --------------------------------------------------------------------------


def _detect_false_breakouts(
    bars: list[Bar], span: _Span, support: float, resistance: float
) -> list[WyckoffEvent]:
    events: list[WyckoffEvent] = []
    height = resistance - support
    if height <= 0.0:
        return events
    min_pierce = height * FALSE_BREAKOUT_MIN_PIERCE

    for i in range(span.start, span.end + 1):
        bar = bars[i]
        # Spring: low pierces below support, then a close back inside within
        # FALSE_BREAKOUT_MAX_BARS bars.
        if bar.low < support - min_pierce:
            recovered = False
            for k in range(i, min(i + FALSE_BREAKOUT_MAX_BARS + 1, span.end + 1)):
                if bars[k].close >= support:
                    recovered = True
                    break
            if recovered:
                events.append(
                    WyckoffEvent(
                        day=bar.day,
                        type="SPRING",
                        price=bar.low,
                        label=EVENT_LABEL["SPRING"],
                        help=EVENT_HELP["SPRING"],
                    )
                )
        # Upthrust: high pierces above resistance, then a close back inside.
        elif bar.high > resistance + min_pierce:
            recovered = False
            for k in range(i, min(i + FALSE_BREAKOUT_MAX_BARS + 1, span.end + 1)):
                if bars[k].close <= resistance:
                    recovered = True
                    break
            if recovered:
                events.append(
                    WyckoffEvent(
                        day=bar.day,
                        type="UPTHRUST",
                        price=bar.high,
                        label=EVENT_LABEL["UPTHRUST"],
                        help=EVENT_HELP["UPTHRUST"],
                    )
                )
    return events


# --------------------------------------------------------------------------
# Step 5 — support / resistance
# --------------------------------------------------------------------------


def _percentile(sorted_vals: list[float], q: float) -> float:
    """Linear-interpolated percentile of an already-sorted list, q in 0..1."""
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    pos = q * (n - 1)
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


def _support_resistance(bars: list[Bar], span: _Span) -> tuple[float, float]:
    """Support = range low cluster; resistance = range high cluster.

    Uses a percentile of the lows / highs (the cluster edge) rather than the
    absolute min / max, so a single false-breakout pierce does not redefine the
    band — letting springs / upthrusts register against the robust edge.
    """
    span_bars = bars[span.start : span.end + 1]
    lows = sorted(b.low for b in span_bars)
    highs = sorted(b.high for b in span_bars)
    support = _percentile(lows, SR_CLUSTER_PERCENTILE)
    resistance = _percentile(highs, 1.0 - SR_CLUSTER_PERCENTILE)
    return support, resistance


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def _coerce_bars(bars: list[Bar] | list[dict[str, object]]) -> list[Bar]:
    out: list[Bar] = []
    for b in bars:
        if isinstance(b, Bar):
            out.append(b)
            continue
        out.append(
            Bar(
                day=b["day"],  # type: ignore[arg-type]
                open=float(b["open"]),  # type: ignore[arg-type]
                high=float(b["high"]),  # type: ignore[arg-type]
                low=float(b["low"]),  # type: ignore[arg-type]
                close=float(b["close"]),  # type: ignore[arg-type]
                volume=float(b["volume"]),  # type: ignore[arg-type]
            )
        )
    return out


def detect_wyckoff(bars: list[Bar] | list[dict[str, object]]) -> list[WyckoffRange]:
    """Detect Wyckoff trading ranges, phases and events.

    `bars` must be ordered oldest → newest. Accepts Bar dataclasses or plain
    dicts with keys day/open/high/low/close/volume. Returns a list of
    WyckoffRange (chronological).
    """
    parsed = _coerce_bars(bars)
    if len(parsed) < MIN_RANGE_BARS:
        return []

    flags = _qualifying_bars(parsed)
    spans = _find_spans(flags)

    ranges: list[WyckoffRange] = []
    for span in spans:
        support, resistance = _support_resistance(parsed, span)
        phase, confidence = _classify_phase(parsed, span)
        events = _detect_climaxes(parsed, span, support, resistance)
        events += _detect_false_breakouts(parsed, span, support, resistance)
        events.sort(key=lambda e: e.day)
        ranges.append(
            WyckoffRange(
                start_day=parsed[span.start].day,
                end_day=parsed[span.end].day,
                phase=phase,
                phase_help=PHASE_HELP[phase],
                confidence=confidence,
                support=support,
                resistance=resistance,
                events=events,
            )
        )
    return ranges
