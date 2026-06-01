"""Pure rule-based Wyckoff detector.

No DB, no FastAPI imports. Operates over a list of OHLCV bars and returns
trading ranges with phase classification and a high-confidence, chronologically
ordered Wyckoff *event sequence* per range.

The sequence is detected as a sequential state machine anchored on the climax
(SC / BC — sets the range extreme) and the Automatic Rally / Reaction (AR —
the first sharp counter-move that pins the opposite boundary). Once those two
anchors fix support and resistance, the remaining events are labeled by their
position in the range plus their volume / spread relative to those anchors:

  ACCUMULATION:  PS -> SC -> AR -> ST -> Spring -> Test -> SOS -> LPS
  DISTRIBUTION:  PSY -> BC -> AR -> ST -> UT -> UTAD -> SOW -> LPSY

Every event is OPTIONAL — a given range may emit only some of them.

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

#: Extra historical bars a caller should fetch *before* the visible window so
#: the detector has lead-in for the rolling-band warm-up (ROLLING_BAND_WINDOW)
#: plus the prior-trend classification (PRIOR_TREND_WINDOW), with margin.
#: Without this buffer short windows (1M, 3M) starve and detect nothing —
#: see clip_ranges_to_window for the matching display-side clamp.
LOOKBACK_BARS: int = ROLLING_BAND_WINDOW + PRIOR_TREND_WINDOW + 10

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

# -- Sequence (state-machine) thresholds -----------------------------------

#: How far (fraction of range height, measured from the climax extreme) a bar
#: must sit toward the opposite boundary to count as the Automatic Rally /
#: Reaction high / low. The AR is the first reaction that travels at least this
#: far off the climax extreme.
AR_MIN_TRAVEL_FRAC: float = 0.55
#: Max bars after the climax within which the AR extreme must be reached.
AR_MAX_BARS: int = 8

#: Volume multiple over the rolling average that flags a "preliminary" bump
#: (PS / PSY) — a notable, above-average-volume bar that precedes the climax.
PRELIM_VOLUME_MULT: float = 1.6
#: Max bars before the climax to search for the preliminary support / supply.
PRELIM_LOOKBACK_BARS: int = 10

#: A Secondary Test must return within this fraction of range height of the
#: climax extreme (i.e. retest the support low / resistance high) ...
ST_EDGE_FRAC: float = 0.30
#: ... while carrying at most this multiple of the climax bar's volume
#: (lower volume than the climax is the signature of a successful test).
ST_MAX_VOLUME_FRAC: float = 0.75

#: A confirming Test (accumulation, after the Spring) is a low-volume retest of
#: the low: volume at most this multiple of the rolling average volume.
TEST_MAX_VOLUME_MULT: float = 1.0

#: A Sign of Strength / Weakness needs a wide bar: spread (hi - lo)/close at
#: least this much, with above-average volume, that closes beyond the
#: AR/opposite boundary in the trend direction.
SOS_MIN_SPREAD: float = 0.035
#: Volume multiple over the rolling average required for an SOS / SOW bar.
SOS_VOLUME_MULT: float = 1.4
#: Min close penetration beyond resistance / support (fraction of range
#: height) for an SOS / SOW to register.
SOS_MIN_PENETRATION: float = 0.02

#: An LPS / LPSY is a pullback / weak rally AFTER an SOS / SOW. It must hold
#: within this fraction of range height of the broken boundary (now flipped
#: support / resistance) — a tight return that confirms the break held.
LPS_HOLD_FRAC: float = 0.30

#: Fraction of the range elapsed (by bar position) beyond which an upthrust is
#: considered "late" and is promoted from UT to the terminal UTAD signal.
UTAD_LATE_FRAC: float = 0.66

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
    "PS": (
        "First real buying steps in after a long drop — volume picks up and "
        "the slide starts to slow, hinting the bottom is near."
    ),
    "AR": (
        "The first sharp bounce (or drop) right after the climax — selling "
        "(or buying) has dried up, and this swing marks the range's far edge."
    ),
    "ST": (
        "Price revisits the climax low (or high) but on lighter volume and a "
        "narrower bar — proof the panic is fading and the level is holding."
    ),
    "TEST": (
        "A quiet, low-volume dip back to the lows after a spring — almost no "
        "sellers left, confirming the coast is clear to go up."
    ),
    "SOS": (
        "A strong, wide up-bar on heavy volume that breaks above the range — "
        "buyers are now in control and the markup is starting."
    ),
    "LPS": (
        "A higher pullback after the breakout that holds above old resistance "
        "(now support) — the last low-risk spot to buy before the rise."
    ),
    "PSY": (
        "First real selling appears after a long run-up — volume picks up and "
        "the climb stalls, hinting the top is near."
    ),
    "UT": (
        "Price pokes above resistance to trap buyers, then falls back inside — "
        "a failed breakout that warns supply is present."
    ),
    "UTAD": (
        "A late fake breakout above the range after testing has finished — the "
        "terminal bull trap and a strong signal the top is in."
    ),
    "SOW": (
        "A strong, wide down-bar on heavy volume that breaks below the range — "
        "sellers are now in control and the markdown is starting."
    ),
    "LPSY": (
        "A weak, lower bounce after the breakdown that fails at old support "
        "(now resistance) — the last low-risk spot to sell before the fall."
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
    "PS": "PS",
    "AR": "AR",
    "ST": "ST",
    "TEST": "Test",
    "SOS": "SOS",
    "LPS": "LPS",
    "PSY": "PSY",
    "UT": "UT",
    "UTAD": "UTAD",
    "SOW": "SOW",
    "LPSY": "LPSY",
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
    # One of: SC | BC | SPRING | PS | AR | ST | TEST | SOS | LPS
    #         | PSY | UT | UTAD | SOW | LPSY
    type: str
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
# Step 3 — support / resistance
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
# Step 4 — sequential event state machine
# --------------------------------------------------------------------------


def _make_event(idx: int, bars: list[Bar], etype: str, price: float) -> WyckoffEvent:
    return WyckoffEvent(
        day=bars[idx].day,
        type=etype,
        price=price,
        label=EVENT_LABEL[etype],
        help=EVENT_HELP[etype],
    )


def _find_climax_index(
    bars: list[Bar], span: _Span, support: float, resistance: float, accumulation: bool
) -> int | None:
    """Index of the most prominent climax (SC for accumulation, BC for
    distribution) inside the span — the highest-volume qualifying climax bar.
    Returns None if no climax bar is found."""
    volumes = [b.volume for b in bars]
    height = resistance - support
    if height <= 0.0:
        return None
    edge = height * EDGE_ZONE_FRAC

    best_idx: int | None = None
    best_vol = 0.0
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
        if accumulation:
            if not (bar.close < bar.open and bar.low <= support + edge):
                continue
        else:
            if not (bar.close > bar.open and bar.high >= resistance - edge):
                continue
        if bar.volume > best_vol:
            best_vol = bar.volume
            best_idx = i
    return best_idx


def _find_ar_index(
    bars: list[Bar], span: _Span, climax_idx: int, support: float,
    resistance: float, accumulation: bool,
) -> int | None:
    """The Automatic Rally / Reaction: the first sharp counter-move after the
    climax that travels at least AR_MIN_TRAVEL_FRAC of the range height off the
    climax extreme, within AR_MAX_BARS bars. Returns the index of the bar
    reaching the AR extreme, or None."""
    height = resistance - support
    if height <= 0.0:
        return None
    climax = bars[climax_idx]
    end = min(climax_idx + AR_MAX_BARS, span.end)

    best_idx: int | None = None
    if accumulation:
        # Rally off the SC low: look for the highest high reached.
        base = climax.low
        best_high = base
        for i in range(climax_idx + 1, end + 1):
            if bars[i].high > best_high:
                best_high = bars[i].high
                if (best_high - base) >= AR_MIN_TRAVEL_FRAC * height:
                    best_idx = i
    else:
        # Reaction off the BC high: look for the lowest low reached.
        base = climax.high
        best_low = base
        for i in range(climax_idx + 1, end + 1):
            if bars[i].low < best_low:
                best_low = bars[i].low
                if (base - best_low) >= AR_MIN_TRAVEL_FRAC * height:
                    best_idx = i
    return best_idx


def _find_prelim_index(
    bars: list[Bar], span: _Span, climax_idx: int, accumulation: bool
) -> int | None:
    """Preliminary Support / Supply: a notable above-average-volume bar before
    the climax (within PRELIM_LOOKBACK_BARS). For accumulation pick a down-ish
    bar (buying bumping a downtrend); for distribution an up-ish bar. Returns
    the most prominent (highest-volume) such bar, or None."""
    volumes = [b.volume for b in bars]
    lo = max(span.start, climax_idx - PRELIM_LOOKBACK_BARS)
    best_idx: int | None = None
    best_vol = 0.0
    for i in range(lo, climax_idx):
        bar = bars[i]
        avg_vol = _rolling_avg(volumes, i, CLIMAX_VOLUME_WINDOW)
        if avg_vol <= 0.0 or bar.volume < PRELIM_VOLUME_MULT * avg_vol:
            continue
        if bar.volume > best_vol:
            best_vol = bar.volume
            best_idx = i
    return best_idx


def _find_st_index(
    bars: list[Bar], climax_idx: int, search_end: int, support: float,
    resistance: float, accumulation: bool, climax_vol: float,
) -> int | None:
    """Secondary Test: after the AR, price returns toward the climax extreme on
    lower volume / narrower spread than the climax. Search from after the AR up
    to search_end. Pick the bar that returns nearest the extreme on light
    volume. Returns the index, or None."""
    height = resistance - support
    if height <= 0.0:
        return None
    edge = height * ST_EDGE_FRAC
    vol_cap = climax_vol * ST_MAX_VOLUME_FRAC

    best_idx: int | None = None
    best_dist = edge + 1.0  # nearest approach to the extreme
    for i in range(climax_idx + 1, search_end + 1):
        bar = bars[i]
        if bar.volume > vol_cap:
            continue
        if accumulation:
            if bar.low > support + edge:
                continue
            dist = abs(bar.low - support)
        else:
            if bar.high < resistance - edge:
                continue
            dist = abs(resistance - bar.high)
        if dist < best_dist:
            best_dist = dist
            best_idx = i
    return best_idx


def _find_spring_index(
    bars: list[Bar], span: _Span, search_start: int, support: float, height: float
) -> int | None:
    """Spring (accumulation): low pierces below support then closes back inside
    within FALSE_BREAKOUT_MAX_BARS. Search from search_start onward. Returns the
    index of the piercing bar, or None."""
    min_pierce = height * FALSE_BREAKOUT_MIN_PIERCE
    for i in range(search_start, span.end + 1):
        bar = bars[i]
        if bar.low < support - min_pierce:
            for k in range(i, min(i + FALSE_BREAKOUT_MAX_BARS + 1, span.end + 1)):
                if bars[k].close >= support:
                    return i
    return None


def _find_upthrust_index(
    bars: list[Bar], span: _Span, search_start: int, resistance: float, height: float
) -> int | None:
    """Upthrust (distribution): high pierces above resistance then closes back
    inside within FALSE_BREAKOUT_MAX_BARS. Search from search_start onward.
    Returns the index of the piercing bar, or None."""
    min_pierce = height * FALSE_BREAKOUT_MIN_PIERCE
    for i in range(search_start, span.end + 1):
        bar = bars[i]
        if bar.high > resistance + min_pierce:
            for k in range(i, min(i + FALSE_BREAKOUT_MAX_BARS + 1, span.end + 1)):
                if bars[k].close <= resistance:
                    return i
    return None


def _find_test_index(
    bars: list[Bar], span: _Span, search_start: int, support: float,
    resistance: float,
) -> int | None:
    """Confirming Test (accumulation, after the Spring): a low-volume retest of
    the low. Search from search_start onward for a bar near support on volume
    below TEST_MAX_VOLUME_MULT x the rolling average. Returns the index."""
    volumes = [b.volume for b in bars]
    height = resistance - support
    if height <= 0.0:
        return None
    edge = height * ST_EDGE_FRAC
    for i in range(search_start, span.end + 1):
        bar = bars[i]
        if bar.low > support + edge:
            continue
        avg_vol = _rolling_avg(volumes, i, CLIMAX_VOLUME_WINDOW)
        if avg_vol <= 0.0 or bar.volume > TEST_MAX_VOLUME_MULT * avg_vol:
            continue
        return i
    return None


def _find_sos_index(
    bars: list[Bar], span: _Span, search_start: int, support: float,
    resistance: float, accumulation: bool,
) -> int | None:
    """Sign of Strength / Weakness: a wide bar on above-average volume that
    closes beyond the boundary in the trend direction (above resistance for
    accumulation, below support for distribution). Search from search_start
    onward. Returns the index, or None."""
    volumes = [b.volume for b in bars]
    height = resistance - support
    if height <= 0.0:
        return None
    penetration = height * SOS_MIN_PENETRATION
    for i in range(search_start, span.end + 1):
        bar = bars[i]
        if bar.close <= 0.0:
            continue
        spread = (bar.high - bar.low) / bar.close
        if spread < SOS_MIN_SPREAD:
            continue
        avg_vol = _rolling_avg(volumes, i, CLIMAX_VOLUME_WINDOW)
        if avg_vol <= 0.0 or bar.volume < SOS_VOLUME_MULT * avg_vol:
            continue
        if accumulation:
            if bar.close >= resistance + penetration:
                return i
        else:
            if bar.close <= support - penetration:
                return i
    return None


def _find_lps_index(
    bars: list[Bar], span: _Span, search_start: int, support: float,
    resistance: float, accumulation: bool,
) -> int | None:
    """Last Point of Support / Supply: after the SOS / SOW, a pullback (accum)
    or weak rally (dist) that holds near the broken boundary (now flipped).
    Accumulation: a higher-low pullback holding above old resistance.
    Distribution: a lower-high rally failing at old support. Returns the index
    of the holding bar, or None."""
    height = resistance - support
    if height <= 0.0:
        return None
    hold = height * LPS_HOLD_FRAC
    for i in range(search_start, span.end + 1):
        bar = bars[i]
        if accumulation:
            # Pullback toward old resistance (now support); must hold above it.
            if bar.low >= resistance - hold and bar.low <= resistance + hold:
                return i
        else:
            # Rally toward old support (now resistance); must fail below it.
            if bar.high <= support + hold and bar.high >= support - hold:
                return i
    return None


def _detect_sequence(
    bars: list[Bar], span: _Span, phase: str, support: float, resistance: float
) -> list[WyckoffEvent]:
    """Run the sequential Wyckoff state machine for one range and return its
    events in chronological (day) order. Anchors on the climax (SC/BC) and the
    AR; every subsequent event is optional and located relative to those
    anchors and the support / resistance band."""
    height = resistance - support
    if height <= 0.0 or phase == "undetermined":
        return []

    accumulation = phase == "accumulation"
    climax_idx = _find_climax_index(bars, span, support, resistance, accumulation)
    if climax_idx is None:
        # No anchor climax → fall back to standalone false-breakout detection
        # only (so a range without a clear climax still surfaces a spring / UT).
        return _detect_anchorless(bars, span, support, resistance, accumulation)

    climax = bars[climax_idx]
    climax_vol = climax.volume

    by_idx: dict[int, WyckoffEvent] = {}

    # --- Climax anchor (SC / BC) ---
    if accumulation:
        by_idx[climax_idx] = _make_event(climax_idx, bars, "SC", climax.low)
    else:
        by_idx[climax_idx] = _make_event(climax_idx, bars, "BC", climax.high)

    # --- Preliminary support / supply (before the climax) ---
    prelim_idx = _find_prelim_index(bars, span, climax_idx, accumulation)
    if prelim_idx is not None and prelim_idx not in by_idx:
        if accumulation:
            by_idx[prelim_idx] = _make_event(prelim_idx, bars, "PS", bars[prelim_idx].low)
        else:
            by_idx[prelim_idx] = _make_event(prelim_idx, bars, "PSY", bars[prelim_idx].high)

    # --- Automatic Rally / Reaction (sets the opposite boundary) ---
    ar_idx = _find_ar_index(bars, span, climax_idx, support, resistance, accumulation)
    if ar_idx is not None and ar_idx not in by_idx:
        ar_price = bars[ar_idx].high if accumulation else bars[ar_idx].low
        by_idx[ar_idx] = _make_event(ar_idx, bars, "AR", ar_price)

    # After the AR (if any) is where secondary action begins.
    after_ar = ar_idx if ar_idx is not None else climax_idx

    # --- Secondary Test (return toward the climax extreme, lower volume) ---
    st_idx = _find_st_index(
        bars, climax_idx, span.end, support, resistance, accumulation, climax_vol
    )
    if st_idx is not None and st_idx > after_ar and st_idx not in by_idx:
        st_price = bars[st_idx].low if accumulation else bars[st_idx].high
        by_idx[st_idx] = _make_event(st_idx, bars, "ST", st_price)

    span_len = span.end - span.start
    late_threshold = span.start + int(span_len * UTAD_LATE_FRAC)

    if accumulation:
        # --- Spring (false breakdown below support) ---
        spring_idx = _find_spring_index(bars, span, after_ar + 1, support, height)
        spring_start = span.end + 1
        if spring_idx is not None and spring_idx not in by_idx:
            by_idx[spring_idx] = _make_event(spring_idx, bars, "SPRING", bars[spring_idx].low)
            spring_start = spring_idx + 1

        # --- Confirming Test (low-volume retest after the spring) ---
        if spring_idx is not None:
            test_idx = _find_test_index(bars, span, spring_start, support, resistance)
            if test_idx is not None and test_idx not in by_idx:
                by_idx[test_idx] = _make_event(test_idx, bars, "TEST", bars[test_idx].low)

        # --- Sign of Strength (break above resistance) ---
        sos_idx = _find_sos_index(
            bars, span, after_ar + 1, support, resistance, accumulation
        )
        if sos_idx is not None and sos_idx not in by_idx:
            by_idx[sos_idx] = _make_event(sos_idx, bars, "SOS", bars[sos_idx].close)
            # --- Last Point of Support (pullback after SOS) ---
            lps_idx = _find_lps_index(
                bars, span, sos_idx + 1, support, resistance, accumulation
            )
            if lps_idx is not None and lps_idx not in by_idx:
                by_idx[lps_idx] = _make_event(lps_idx, bars, "LPS", bars[lps_idx].low)
    else:
        # --- Upthrust(s): in-range false break above resistance ---
        ut_idx = _find_upthrust_index(bars, span, after_ar + 1, resistance, height)
        if ut_idx is not None and ut_idx not in by_idx:
            # Late upthrust (after an ST, in the back third) → terminal UTAD.
            is_late = ut_idx >= late_threshold and st_idx is not None and ut_idx > st_idx
            etype = "UTAD" if is_late else "UT"
            by_idx[ut_idx] = _make_event(ut_idx, bars, etype, bars[ut_idx].high)
            # If we labeled UT, look for a later terminal UTAD beyond it.
            if etype == "UT":
                utad_idx = _find_upthrust_index(
                    bars, span, ut_idx + 1, resistance, height
                )
                if (
                    utad_idx is not None
                    and utad_idx not in by_idx
                    and utad_idx >= late_threshold
                ):
                    by_idx[utad_idx] = _make_event(
                        utad_idx, bars, "UTAD", bars[utad_idx].high
                    )

        # --- Sign of Weakness (break below support) ---
        sow_idx = _find_sos_index(
            bars, span, after_ar + 1, support, resistance, accumulation
        )
        if sow_idx is not None and sow_idx not in by_idx:
            by_idx[sow_idx] = _make_event(sow_idx, bars, "SOW", bars[sow_idx].close)
            # --- Last Point of Supply (weak rally after SOW) ---
            lpsy_idx = _find_lps_index(
                bars, span, sow_idx + 1, support, resistance, accumulation
            )
            if lpsy_idx is not None and lpsy_idx not in by_idx:
                by_idx[lpsy_idx] = _make_event(lpsy_idx, bars, "LPSY", bars[lpsy_idx].high)

    return [by_idx[i] for i in sorted(by_idx)]


def _detect_anchorless(
    bars: list[Bar], span: _Span, support: float, resistance: float, accumulation: bool
) -> list[WyckoffEvent]:
    """Fallback when no climax anchor exists: surface only a high-confidence
    false breakout (Spring for accumulation, UT for distribution)."""
    height = resistance - support
    if height <= 0.0:
        return []
    by_idx: dict[int, WyckoffEvent] = {}
    if accumulation:
        spring_idx = _find_spring_index(bars, span, span.start, support, height)
        if spring_idx is not None:
            by_idx[spring_idx] = _make_event(
                spring_idx, bars, "SPRING", bars[spring_idx].low
            )
    else:
        ut_idx = _find_upthrust_index(bars, span, span.start, resistance, height)
        if ut_idx is not None:
            span_len = span.end - span.start
            late_threshold = span.start + int(span_len * UTAD_LATE_FRAC)
            etype = "UTAD" if ut_idx >= late_threshold else "UT"
            by_idx[ut_idx] = _make_event(ut_idx, bars, etype, bars[ut_idx].high)
    return [by_idx[i] for i in sorted(by_idx)]


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
    """Detect Wyckoff trading ranges, phases and the full event sequence.

    `bars` must be ordered oldest → newest. Accepts Bar dataclasses or plain
    dicts with keys day/open/high/low/close/volume. Returns a list of
    WyckoffRange (chronological), each carrying its events in chronological
    (day) order.
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
        events = _detect_sequence(parsed, span, phase, support, resistance)
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


def clip_ranges_to_window(
    ranges: list[WyckoffRange], from_date: date | None
) -> list[WyckoffRange]:
    """Restrict detected ranges to a visible window starting at ``from_date``.

    Detection is run over buffered data that extends ``LOOKBACK_BARS`` before
    ``from_date`` (lead-in for the band warm-up + prior-trend classification).
    This keeps only ranges that reach into the visible window, clamps each
    range's start to ``from_date``, and drops events that fall before it — so a
    caller receives nothing earlier than the window it asked for, while the
    phase / support / resistance still benefit from the full lead-in.

    ``from_date is None`` (no window requested) returns ``ranges`` unchanged.
    """
    if from_date is None:
        return ranges
    clipped: list[WyckoffRange] = []
    for rng in ranges:
        if rng.end_day < from_date:
            continue
        if rng.start_day < from_date:
            rng.start_day = from_date
        rng.events = [ev for ev in rng.events if ev.day >= from_date]
        clipped.append(rng)
    return clipped
