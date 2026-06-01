# Wyckoff Method Overlay — Design Spec

**Date:** 2026-06-01
**Status:** Approved (brainstorming) — pending implementation plan
**Surface:** `frontend/` stock detail page (`/stocks/[ticker]`), price chart.

## Goal

Overlay Wyckoff Method analysis on the stock price chart: detect consolidation
trading ranges, classify each as Accumulation vs Distribution, and mark
high-confidence volume events (climaxes, springs, upthrusts), with plain-language
help text for traders.

## Decisions (from brainstorming)

| Decision | Choice |
|---|---|
| Intelligence source | **Auto-detect (rule-based)** — ML pipeline still stubbed; rules now, ML later |
| Detection scope | **Trading ranges + phase classification + climaxes + spring/upthrust** |
| Event set | SC, BC, Spring, Upthrust only (high-confidence). NOT full PS/AR/ST/LPS/UTAD sequence |
| Overlay UX | **Toggle button** next to range buttons; analyzes the **currently visible range**; off by default |
| Help text | Plain-language definition per event/phase, shown on marker hover + legend |

### Explicitly out of scope (YAGNI)
- ML-based detection (rules only in v1).
- Full ordered schematic events (PS, AR, ST, LPS, UTAD, SOW, LPSY…).
- Manual annotation / editing / persistence.
- Point-&-figure cause-and-effect price targets.

## Architecture

```
daily_ohlcv ──► GET /stocks/{ticker}/wyckoff ──► WyckoffResponse (JSON)
                      │  (reuses the /prices OHLCV query)
                      └─ api/analysis/wyckoff.py   ← pure detector, offline-testable
                                                          │
PriceChart.tsx ◄── "Wyckoff" toggle ── fetch on toggle / range-change
   ├─ candleSeries.setMarkers([...])      events (+ hover tooltip)
   ├─ candleSeries.createPriceLine(...)   support / resistance
   └─ phase box (custom ISeriesPrimitive) shaded range + phase label
   └─ legend chip                         phase · confidence · dates + help
```

- New endpoint slots beside `/predictions`, `/score` in `api/routers/stocks.py`.
- Same cache + 404 shape as `/prices`: `_cache_get` / `_cache_set` (ttl 1h),
  404 if ticker missing in `companies`.
- Detector is a **separate pure-Python module** (`api/analysis/wyckoff.py`):
  takes a list of OHLCV bars, returns ranges/events. No DB access → unit-testable
  offline with synthetic bars (matches repo's offline-unit-test strategy).

## Detection algorithm (rule-based v1)

Input: daily OHLCV bars for the requested window. Tunable thresholds as named constants.

1. **Trading ranges** — rolling 20-day high/low band. A range = ≥15 consecutive
   bars where band width `(hi − lo) / mid` stays under a threshold AND the
   linear-regression slope of close is ~flat. Merge adjacent qualifying spans.
2. **Phase classification** — slope of the ~30 bars *before* the range:
   - prior downtrend → **Accumulation**
   - prior uptrend → **Distribution**
   - else → **undetermined**
   - confidence = f(prior-slope magnitude, volume dry-up inside range)
3. **Climaxes** — bar with `volume > 2.5× rolling-avg` + wide spread near a range edge:
   - down-spread at range low → **Selling Climax (SC)**
   - up-spread at range high → **Buying Climax (BC)**
4. **Spring / Upthrust** — false breakout that closes back inside the range within ≤3 bars:
   - low pierces support, closes back inside → **Spring** (bullish)
   - high pierces resistance, closes back inside → **Upthrust** (bearish)
5. **Support / Resistance** — range low cluster = support; high cluster = resistance.

Design bias: **precision over recall** — fewer, high-confidence calls on noisy DSE data.
Thresholds will need tuning against real tickers after v1 lands.

## Response schema (`api/schemas/stocks.py`)

```python
class WyckoffEvent(BaseModel):
    day: date
    type: str          # "SC" | "BC" | "SPRING" | "UPTHRUST"
    price: Decimal
    label: str         # short marker label, e.g. "SC"
    help: str          # plain-language definition (see below)

class WyckoffRange(BaseModel):
    start_day: date
    end_day: date
    phase: str         # "accumulation" | "distribution" | "undetermined"
    phase_help: str    # plain-language phase definition
    confidence: float  # 0..1
    support: Decimal
    resistance: Decimal
    events: list[WyckoffEvent]

class WyckoffResponse(BaseModel):
    ticker: str
    interval: str
    ranges: list[WyckoffRange]
```

Endpoint: `GET /stocks/{ticker}/wyckoff?from=&to=&interval=daily` (mirrors `/prices` params).

## Frontend rendering (`frontend/components/stocks/PriceChart.tsx`)

- **Events** → `candleSeries.setMarkers()` (native). SC/BC/Spring/Upthrust, colored,
  positioned above/below the bar, with short text. Tooltip on hover shows `help`.
- **Support/Resistance** → `createPriceLine()` for the active range (native horizontal lines).
- **Phase box** → translucent shaded rectangle over the range span + phase label.
  lightweight-charts v5 has **no native rectangle** → needs a small custom
  `ISeriesPrimitive`. This is the only non-trivial UI piece.
  - **Fallback** if the primitive proves costly: drop the box; use vertical markers
    at range start/end + the S/R lines + a legend chip. Cheaper, slightly less visual.
- **Legend chip** — under/over the chart: `Accumulation · 78% · 12 Mar–28 Apr`, with
  phase help on hover.
- **Toggle** — `Wyckoff` button beside the range buttons, off by default. On → fetch
  `/wyckoff` for the current range, draw overlays. Off / ticker-change / range-change →
  clear markers + price lines + box; refetch if still on.
- New type `WyckoffResponse` in `frontend/lib/types`.

## Help text (plain trader words)

Shipped by the backend per event/phase; surfaced on marker hover + legend.

| Term | Simple meaning |
|---|---|
| **Selling Climax (SC)** | Panic dumping — price crashes on huge volume. Often the bottom; big players buy what scared sellers throw away. |
| **Buying Climax (BC)** | Buying frenzy — price spikes on huge volume. Often the top; big players sell into the hype. |
| **Spring** | Price dips below support to trap sellers, then snaps back up. Bullish — fake breakdown before a rise. |
| **Upthrust** | Price pops above resistance to trap buyers, then drops back. Bearish — fake breakout before a fall. |
| **Accumulation** | Smart money quietly buying sideways after a drop. Usually comes before an up-move. |
| **Distribution** | Smart money quietly selling sideways after a run-up. Usually comes before a down-move. |

## Testing

- Unit tests for the detector with **synthetic** accumulation/distribution OHLCV
  series (offline, no DB) — assert range detection, phase label, and each event type.
- `make check` (ruff + mypy strict) clean.

## Reference
- https://www.investopedia.com/articles/active-trading/070715/making-money-wyckoff-way.asp
- https://fxopen.com/blog/en/the-wyckoff-trading-method/
