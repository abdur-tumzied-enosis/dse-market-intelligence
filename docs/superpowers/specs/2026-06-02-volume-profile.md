# Volume Profile Indicator — Design

**Date:** 2026-06-02
**Target:** `frontend/components/stocks/PriceChart.tsx` (lightweight-charts v5)

## Goal

Add a **visible-range Volume Profile** overlay to the stock price chart. The
profile shows how much volume traded at each price level over the currently
visible time window, recomputed on every pan/zoom (TradingView-style). It
displays a Point of Control (POC) line and a 70% Value Area band (VAH/VAL).

Exposed via a new **"VP" toggle button** next to the existing Wyckoff toggle,
**default off**. VP and Wyckoff are independent — both may be on at once (VP
draws behind the candles; Wyckoff markers/boxes draw on top).

## Decisions (locked)

| Question | Decision |
|---|---|
| What it shows | POC + Value Area (full TradingView style) |
| Bin count | Fixed `N = 24` bins spanning visible price extent (const, easy to tweak) |
| Volume distribution | Spread each candle's volume evenly across all bins its high–low spans |
| Bar anchor | Left-anchored (bars grow rightward from the left edge), keeps clear of the busy right price scale |
| Toggle default | Off; independent of Wyckoff |
| Price extent | Tracks visible candles' high–low (bins resize as you pan into new highs/lows) |
| Recompute trigger | `subscribeVisibleLogicalRangeChange`, throttled via `requestAnimationFrame` |

## Architecture

Two units with a clean boundary: a **pure compute module** (testable, no chart
dependency) and a **rendering primitive** (canvas draw, mirrors the existing
`PhaseBoxPrimitive`).

### Unit 1 — `frontend/lib/volumeProfile.ts` (new, pure)

```ts
export interface VPBin {
  priceLow: number
  priceHigh: number
  volume: number
}

export interface VProfile {
  bins: VPBin[]    // length N, ascending price
  poc: number      // mid-price of the max-volume bin
  vah: number      // value-area high (top edge of 70% band)
  val: number      // value-area low  (bottom edge of 70% band)
  maxVol: number   // largest single-bin volume (for bar-width scaling)
}

export interface VPCandle {
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export function computeVolumeProfile(
  candles: VPCandle[],
  binCount?: number,   // default 24
): VProfile | null
```

**Algorithm:**

1. Filter out candles with no usable price. If none remain → return `null`.
2. Price extent: `lo = min(low)`, `hi = max(high)` across candles.
3. If `hi === lo` (flat price) → single effective bin at that price; all volume
   lands there; `poc = vah = val = lo`. Skip the proportional spread to avoid
   divide-by-zero.
4. Otherwise build `N` equal-height bins from `lo` to `hi`. For each candle,
   spread its `volume` evenly across every bin its `[low, high]` overlaps
   (a candle touching `k` bins contributes `volume / k` to each). A candle whose
   range falls inside a single bin contributes its full volume there.
5. `maxVol` = max bin volume. `poc` = mid-price of the max-volume bin.
6. **Value area:** start from the POC bin; repeatedly add the neighbour (above or
   below) with the larger volume, accumulating volume, until accumulated ≥ 70%
   of total volume. `vah` = top edge of the highest included bin, `val` = bottom
   edge of the lowest included bin.
7. Return `null` if total volume is 0.

### Unit 2 — `VolumeProfilePrimitive` (in PriceChart.tsx)

An `ISeriesPrimitive` attached to the candle series, mirroring
`PhaseBoxPrimitive` (PriceChart.tsx:642). `zOrder()` returns `'bottom'` so bars
render behind the candles.

- Holds the current `VProfile` (or `null`).
- `update(profile)` swaps the profile and triggers `requestUpdate()`.
- `draw()` (via `useBitmapCoordinateSpace`, same pattern as the phase box):
  - For each bin: `y` from `series.priceToCoordinate(binMid)`; bar height from
    the gap between adjacent bin mid-coordinates. Width = `(volume / maxVol) *
    chartWidth * BAR_MAX_FRACTION` (e.g. `0.35`). Anchored at the left edge
    (`x = 0`), growing rightward.
  - Bars at ~25% alpha so candles read through.
  - **POC:** the max-volume bin drawn with a brighter fill / a solid horizontal
    line at `poc`.
  - **Value area:** bins within `[val, vah]` shaded slightly stronger than the
    out-of-area bins, marking the 70% band.
- If profile is `null` → draw nothing.

Color: reuse the existing palette (e.g. `NEUTRAL`/violet family) so it reads as
an analytical overlay distinct from candle up/down green/red.

## Data Flow

The candle series does not expose its raw data back, and VP needs `volume` per
candle, so we cache the loaded array:

- Add `dataRef: useRef<VPCandle[]>([])`.
- Populate it in `loadData` (historical set) and keep today's bar in sync in
  `appendLiveBar` (PriceChart.tsx:236, 258).

**Recompute path:**

1. On VP toggle-on: attach the primitive, subscribe to
   `chart.timeScale().subscribeVisibleLogicalRangeChange(handler)`, run one
   initial compute.
2. `handler` throttles via `requestAnimationFrame` — coalesces a burst of
   pan/zoom events into a single recompute. Store the pending frame id to cancel.
3. Recompute (only when VP is on): read the visible logical range, map it to the
   visible time window, filter `dataRef.current` candles into that window, call
   `computeVolumeProfile(visible, N)`, then `vpPrimitive.update(profile)`.
4. A range-button change reloads data and (if VP on) recomputes. A live-bar
   update while VP is on also recomputes.

**Toggle:** `vpOn` state + `vpOnRef` (mirrors `wyckoffOn`/`wyckoffOnRef`,
PriceChart.tsx:109-110, 421-432). On → attach + subscribe + compute. Off →
detach primitive, unsubscribe, cancel pending rAF, clear profile.

## Edge Cases

- **Empty visible window / `computeVolumeProfile` → null:** primitive draws
  nothing; no crash.
- **Single candle:** one effective extent; band collapses to the POC.
- **Flat price (extent 0):** all volume in one bin, `poc = vah = val`; no
  divide-by-zero, no NaN.
- **Visible range past loaded data:** simply fewer candles in the window — fine.
- **Zero total volume:** `computeVolumeProfile` returns `null`.

## Testing

**Automated** — `frontend/lib/__tests__/volumeProfile.test.ts` (jest, offline),
exercising the pure function:

- Known multi-candle set → assert bin volumes, POC price, VAH/VAL, and that the
  value area covers ≥ 70% of total volume.
- Empty array → `null`.
- Single candle → one effective bin, `poc` equals its price, `vah === val`.
- Flat price (all `high === low`, or extent 0) → no `NaN`, single populated bin,
  band collapses to the POC.

**Manual** — the canvas `draw()` and toggle wiring are verified in the browser
(jsdom has no canvas). Confirm: bars left-anchored and translucent, candles
visible through them, POC + value-area band correct, bars recompute on pan/zoom,
VP + Wyckoff coexist, toggle off clears cleanly.

## Cleanup

- Unsubscribe the visible-range handler and cancel any pending `rAF` on toggle-off
  and in the chart-teardown effect (PriceChart.tsx:392-407).
- Detach the primitive on unmount and on toggle-off.

## Out of Scope (YAGNI)

- Buy/sell volume split (no intraday tick data; daily OHLCV only).
- Configurable bin count / value-area % in the UI (compile-time consts for now).
- Fixed-range (session) profiles — visible-range only.
- Backend changes — VP is computed entirely client-side from existing OHLCV.
