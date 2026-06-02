# Volume Profile Indicator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a visible-range Volume Profile overlay (POC + 70% value area, left-anchored translucent bars) to the stock `PriceChart`, recomputing on pan/zoom, behind a default-off "VP" toggle.

**Architecture:** A pure, framework-free compute module (`frontend/lib/volumeProfile.ts`) bins candle volume by price and derives POC/value-area — fully unit-tested. A canvas-drawing `ISeriesPrimitive` (`VolumeProfilePrimitive`, in `PriceChart.tsx`) renders the profile behind the candles, mirroring the existing `PhaseBoxPrimitive`. PriceChart wiring caches the loaded candles, recomputes the profile on visible-range changes (throttled via `requestAnimationFrame`), and toggles the overlay.

**Tech Stack:** TypeScript, React 19, lightweight-charts v5, Jest + Testing Library (jsdom).

**Spec:** `docs/superpowers/specs/2026-06-02-volume-profile.md`

---

## File Structure

- **Create** `frontend/lib/volumeProfile.ts` — pure compute: `computeVolumeProfile()`, types `VPCandle`, `VPBin`, `VProfile`. No chart/React deps.
- **Create** `frontend/__tests__/lib/volumeProfile.test.ts` — unit tests for the compute module.
- **Modify** `frontend/components/stocks/PriceChart.tsx` — add `VolumeProfilePrimitive` class, VP constants, `dataRef`, recompute/throttle logic, visible-range subscription, toggle state + button, cleanup.

No backend changes. The profile is computed client-side from OHLCV already fetched by `loadData`.

---

## Task 1: Pure compute module (`computeVolumeProfile`)

**Files:**
- Create: `frontend/lib/volumeProfile.ts`
- Test: `frontend/__tests__/lib/volumeProfile.test.ts`

Test-driven. The function bins volume by price across the candle set, returns POC + value-area band. Edge rules: empty/zero-volume → `null`; single candle → collapse to one bin at its close; flat price (extent 0) → collapse to one bin at that price.

- [ ] **Step 1: Write the failing tests**

Create `frontend/__tests__/lib/volumeProfile.test.ts`:

```ts
import { computeVolumeProfile } from '@/lib/volumeProfile'
import type { VPCandle } from '@/lib/volumeProfile'

const c = (low: number, high: number, volume: number, close = (low + high) / 2): VPCandle => ({
  open: close,
  high,
  low,
  close,
  volume,
})

describe('computeVolumeProfile', () => {
  it('bins volume by price, finds POC and a 70% value area', () => {
    // Two non-overlapping candles, 2 bins over [0,10] (step 5):
    //   A: low 0 high 4 vol 100 -> bin0 (100)
    //   B: low 6 high 10 vol 60 -> bin1 (60)
    const profile = computeVolumeProfile([c(0, 4, 100), c(6, 10, 60)], 2)!
    expect(profile).not.toBeNull()
    expect(profile.bins).toHaveLength(2)
    expect(profile.bins[0].volume).toBeCloseTo(100, 5)
    expect(profile.bins[1].volume).toBeCloseTo(60, 5)
    expect(profile.maxVol).toBeCloseTo(100, 5)
    expect(profile.poc).toBeCloseTo(2.5, 5) // mid of bin0 [0,5)
    // value area expands from POC until >=70% of 160 (=112): needs both bins
    expect(profile.val).toBeCloseTo(0, 5)
    expect(profile.vah).toBeCloseTo(10, 5)
  })

  it('spreads a candle volume evenly across every bin it spans', () => {
    // Single-candle case is special (collapses); use two identical wide candles.
    // Range [0,10], 2 bins. Each candle spans both bins -> half each.
    const profile = computeVolumeProfile([c(0, 10, 80), c(0, 10, 80)], 2)!
    expect(profile.bins[0].volume).toBeCloseTo(80, 5)
    expect(profile.bins[1].volume).toBeCloseTo(80, 5)
  })

  it('returns null for empty input', () => {
    expect(computeVolumeProfile([], 24)).toBeNull()
  })

  it('returns null when total volume is zero', () => {
    expect(computeVolumeProfile([c(1, 2, 0), c(3, 4, 0)], 24)).toBeNull()
  })

  it('collapses a single candle to one bin at its close', () => {
    const profile = computeVolumeProfile([c(10, 20, 50, 15)], 24)!
    expect(profile.bins).toHaveLength(1)
    expect(profile.bins[0].volume).toBeCloseTo(50, 5)
    expect(profile.poc).toBeCloseTo(15, 5)
    expect(profile.vah).toBeCloseTo(15, 5)
    expect(profile.val).toBeCloseTo(15, 5)
    expect(profile.maxVol).toBeCloseTo(50, 5)
  })

  it('handles flat price (zero extent) without NaN', () => {
    const profile = computeVolumeProfile([c(10, 10, 30), c(10, 10, 20)], 24)!
    expect(profile.bins).toHaveLength(1)
    expect(profile.bins[0].volume).toBeCloseTo(50, 5)
    expect(profile.poc).toBeCloseTo(10, 5)
    expect(profile.vah).toBeCloseTo(10, 5)
    expect(profile.val).toBeCloseTo(10, 5)
    expect(Number.isNaN(profile.poc)).toBe(false)
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx jest volumeProfile -t "computeVolumeProfile"`
Expected: FAIL — `Cannot find module '@/lib/volumeProfile'`.

- [ ] **Step 3: Write the implementation**

Create `frontend/lib/volumeProfile.ts`:

```ts
// frontend/lib/volumeProfile.ts
// Pure volume-profile math: bin candle volume by price level, derive the
// Point of Control (POC) and the 70% Value Area band (VAH/VAL). No chart or
// React dependency so it is unit-testable in isolation.

export interface VPCandle {
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface VPBin {
  priceLow: number
  priceHigh: number
  volume: number
}

export interface VProfile {
  bins: VPBin[]   // ascending by price
  poc: number     // mid-price of the max-volume bin
  vah: number     // value-area high (top edge of the 70% band)
  val: number     // value-area low  (bottom edge of the 70% band)
  maxVol: number  // largest single-bin volume (for bar-width scaling)
}

const VALUE_AREA_FRACTION = 0.7

export function computeVolumeProfile(
  candles: VPCandle[],
  binCount = 24,
): VProfile | null {
  const valid = candles.filter(
    c =>
      Number.isFinite(c.high) &&
      Number.isFinite(c.low) &&
      Number.isFinite(c.volume) &&
      c.volume > 0,
  )
  if (valid.length === 0) return null

  let lo = Infinity
  let hi = -Infinity
  let totalVol = 0
  for (const c of valid) {
    if (c.low < lo) lo = c.low
    if (c.high > hi) hi = c.high
    totalVol += c.volume
  }
  if (totalVol <= 0) return null

  // Single candle: volume-at-price is unknowable from one daily bar, so
  // collapse to one bin at its close. Flat price (zero extent): collapse to lo.
  if (valid.length === 1) {
    const p = valid[0].close
    return { bins: [{ priceLow: p, priceHigh: p, volume: totalVol }], poc: p, vah: p, val: p, maxVol: totalVol }
  }
  if (hi <= lo) {
    return { bins: [{ priceLow: lo, priceHigh: lo, volume: totalVol }], poc: lo, vah: lo, val: lo, maxVol: totalVol }
  }

  const n = Math.max(1, Math.floor(binCount))
  const step = (hi - lo) / n
  const bins: VPBin[] = Array.from({ length: n }, (_, i) => ({
    priceLow: lo + i * step,
    priceHigh: lo + (i + 1) * step,
    volume: 0,
  }))

  // Spread each candle's volume evenly across every bin its [low, high] spans.
  for (const c of valid) {
    const lowIdx = Math.min(n - 1, Math.max(0, Math.floor((c.low - lo) / step)))
    const highIdx = Math.min(n - 1, Math.max(0, Math.floor((c.high - lo) / step)))
    const span = highIdx - lowIdx + 1
    const share = c.volume / span
    for (let i = lowIdx; i <= highIdx; i++) bins[i].volume += share
  }

  // POC = bin with the most volume.
  let pocIdx = 0
  let maxVol = 0
  for (let i = 0; i < n; i++) {
    if (bins[i].volume > maxVol) {
      maxVol = bins[i].volume
      pocIdx = i
    }
  }
  const poc = (bins[pocIdx].priceLow + bins[pocIdx].priceHigh) / 2

  // Value area: grow outward from the POC bin, each step adding the neighbour
  // (above or below) with the larger volume, until >= 70% of total volume.
  let acc = bins[pocIdx].volume
  let loIdx = pocIdx
  let hiIdx = pocIdx
  const target = totalVol * VALUE_AREA_FRACTION
  while (acc < target && (loIdx > 0 || hiIdx < n - 1)) {
    const below = loIdx > 0 ? bins[loIdx - 1].volume : -1
    const above = hiIdx < n - 1 ? bins[hiIdx + 1].volume : -1
    if (above >= below) {
      hiIdx += 1
      acc += bins[hiIdx].volume
    } else {
      loIdx -= 1
      acc += bins[loIdx].volume
    }
  }

  return { bins, poc, vah: bins[hiIdx].priceHigh, val: bins[loIdx].priceLow, maxVol }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx jest volumeProfile`
Expected: PASS — all 6 tests green.

- [ ] **Step 5: Lint + typecheck the new file**

Run: `cd frontend && npx eslint lib/volumeProfile.ts __tests__/lib/volumeProfile.test.ts`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/lib/volumeProfile.ts frontend/__tests__/lib/volumeProfile.test.ts
git commit -m "feat(frontend): volume profile compute module + tests"
```

---

## Task 2: `VolumeProfilePrimitive` (canvas overlay)

**Files:**
- Modify: `frontend/components/stocks/PriceChart.tsx` (append a new primitive class near `PhaseBoxPrimitive`, after line 686; add VP constants near the existing palette, after line 26)

This is canvas drawing — not unit-testable in jsdom (no canvas). Verified by typecheck/lint here and manual browser checks in Task 4. Mirrors the existing `PhaseBoxPrimitive`/`PhaseBoxPaneView`/`PhaseBoxPaneRenderer` structure and reuses the existing `withAlpha()` helper (PriceChart.tsx:578).

- [ ] **Step 1: Add VP constants**

After the palette block (PriceChart.tsx:26, the `const NEUTRAL` line), add:

```tsx
// Volume Profile palette + layout
const VP_BINS = 24
const VP_COLOR = '#7aa2f7'          // soft blue — distinct from candle green/red + Wyckoff violet
const VP_POC_COLOR = '#ffd166'      // amber POC line
const VP_BAR_MAX_FRACTION = 0.35    // widest bar = 35% of chart width
const VP_BAR_ALPHA = 0.22           // out-of-value-area bars
const VP_VA_ALPHA = 0.34            // value-area bars (slightly stronger)
```

- [ ] **Step 2: Add the primitive classes**

At the end of the file (after `PhaseBoxPrimitive`, line 686), add. Note `computeVolumeProfile`'s `VProfile` type is imported in Task 3 Step 1; this class only references it via the `VProfile` type import.

```tsx
// ─── Volume Profile primitive ────────────────────────────────────────────────
// Draws a left-anchored horizontal histogram of volume-by-price for the current
// VProfile, behind the candles (zOrder 'bottom'). POC drawn as a bright amber
// line; value-area bins shaded slightly stronger than the rest.

interface VPDrawState {
  profile: VProfile | null
  priceToCoordinate: (price: number) => number | null
}

class VolumeProfilePaneRenderer {
  constructor(private state: () => VPDrawState | null) {}

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  draw(target: any) {
    const s = this.state()
    if (!s || !s.profile || s.profile.maxVol <= 0) return
    const { profile, priceToCoordinate } = s
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    target.useBitmapCoordinateSpace((scope: any) => {
      const ctx = scope.context as CanvasRenderingContext2D
      const hr = scope.horizontalPixelRatio
      const vr = scope.verticalPixelRatio
      const maxBarPx = scope.mediaSize.width * VP_BAR_MAX_FRACTION

      for (const bin of profile.bins) {
        const yHigh = priceToCoordinate(bin.priceHigh)
        const yLow = priceToCoordinate(bin.priceLow)
        if (yHigh == null || yLow == null) continue
        const top = Math.min(yHigh, yLow) * vr
        const bottom = Math.max(yHigh, yLow) * vr
        const h = Math.max(1 * vr, bottom - top - 1 * vr) // 1px gap between bars
        const w = (bin.volume / profile.maxVol) * maxBarPx * hr
        const inVA = bin.priceHigh > profile.val && bin.priceLow < profile.vah
        ctx.fillStyle = withAlpha(VP_COLOR, inVA ? VP_VA_ALPHA : VP_BAR_ALPHA)
        ctx.fillRect(0, top, w, h)
      }

      // POC line across the full bar width
      const yPoc = priceToCoordinate(profile.poc)
      if (yPoc != null) {
        const y = yPoc * vr
        ctx.fillStyle = VP_POC_COLOR
        ctx.fillRect(0, y - 0.5 * vr, maxBarPx * hr, Math.max(1, 1 * vr))
      }
    })
  }
}

class VolumeProfilePaneView {
  private _renderer: VolumeProfilePaneRenderer
  constructor(state: () => VPDrawState | null) {
    this._renderer = new VolumeProfilePaneRenderer(state)
  }
  zOrder() {
    return 'bottom' as const
  }
  renderer() {
    return this._renderer
  }
}

class VolumeProfilePrimitive {
  private profile: VProfile | null = null
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  private series: any = null
  private requestUpdate: (() => void) | null = null
  private views: VolumeProfilePaneView[]

  constructor() {
    this.views = [new VolumeProfilePaneView(() => this.drawState())]
  }

  private drawState(): VPDrawState | null {
    if (!this.series) return null
    return {
      profile: this.profile,
      priceToCoordinate: (price: number) => this.series.priceToCoordinate(price),
    }
  }

  setProfile(profile: VProfile | null) {
    this.profile = profile
    this.requestUpdate?.()
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  attached(param: any) {
    this.series = param.series
    this.requestUpdate = param.requestUpdate
  }

  detached() {
    this.series = null
    this.requestUpdate = null
  }

  updateAllViews() {
    // profile is read lazily in draw(); nothing to cache
  }

  paneViews() {
    return this.views
  }
}
```

- [ ] **Step 3: Typecheck (will fail until Task 3 imports `VProfile`)**

`VProfile` is referenced here but imported in Task 3. Do not run typecheck in isolation between Task 2 and Task 3 — they are committed together at the end of Task 3. Proceed to Task 3.

---

## Task 3: Wire VP into PriceChart (data cache, recompute, toggle, cleanup)

**Files:**
- Modify: `frontend/components/stocks/PriceChart.tsx`

- [ ] **Step 1: Import the compute module**

At the top, after the existing `import { get } from '@/lib/api'` (PriceChart.tsx:4), add:

```tsx
import { computeVolumeProfile } from '@/lib/volumeProfile'
import type { VPCandle, VProfile } from '@/lib/volumeProfile'
```

- [ ] **Step 2: Add refs + state**

Inside `PriceChart`, after the `volRef` declaration (PriceChart.tsx:90), add:

```tsx
  // Volume Profile handles + cached candle data (series doesn't expose data back)
  const vpRef     = useRef<VolumeProfilePrimitive | null>(null)
  const dataRef   = useRef<Array<VPCandle & { time: string }>>([])
  const vpRafRef  = useRef<number | null>(null)
```

After the Wyckoff state block (`const [wyckoffBusy, setWyckoffBusy] = useState(false)`, PriceChart.tsx:112), add:

```tsx
  // Volume Profile state
  const [vpOn, setVpOn] = useState(false)
  const vpOnRef         = useRef(false)
```

- [ ] **Step 3: Add recompute + throttle callbacks**

Immediately after the refs/state above and before `fromForRange` (PriceChart.tsx:115), add:

```tsx
  // Recompute the volume profile from the candles inside the current visible
  // logical range and push it into the primitive. No-op unless VP is on.
  const recomputeVP = useCallback(() => {
    const chart = chartRef.current
    const vp = vpRef.current
    if (!vpOnRef.current || !chart || !vp) return
    const all = dataRef.current
    let visible = all
    const lr = chart.timeScale().getVisibleLogicalRange()
    if (lr) {
      const from = Math.max(0, Math.floor(lr.from))
      const to = Math.min(all.length - 1, Math.ceil(lr.to))
      visible = from <= to ? all.slice(from, to + 1) : []
    }
    vp.setProfile(computeVolumeProfile(visible, VP_BINS))
  }, [])

  // Coalesce a burst of pan/zoom events into a single recompute per frame.
  const scheduleVP = useCallback(() => {
    if (vpRafRef.current != null) cancelAnimationFrame(vpRafRef.current)
    vpRafRef.current = requestAnimationFrame(() => {
      vpRafRef.current = null
      recomputeVP()
    })
  }, [recomputeVP])
```

- [ ] **Step 4: Cache candles in `loadData` + recompute**

In `loadData`, replace the `volRef.current.setData(...)` block plus the lines through `await appendLiveBar()` (PriceChart.tsx:280-289) with:

```tsx
      volRef.current.setData(sorted.map(d => ({
        time:  d.day,
        value: Number(d.volume ?? 0),
        color: Number(d.close) >= Number(d.open ?? d.close)
          ? 'rgba(0,212,164,0.22)'
          : 'rgba(255,77,106,0.22)',
      })))

      // Cache raw candles for the volume profile (series can't return its data).
      dataRef.current = sorted.map(d => ({
        time:   d.day,
        open:   Number(d.open  ?? d.close),
        high:   Number(d.high  ?? d.close),
        low:    Number(d.low   ?? d.close),
        close:  Number(d.close),
        volume: Number(d.volume ?? 0),
      }))

      chartRef.current?.timeScale().fitContent()
      await appendLiveBar()  // overlay today's live candle on the historical data
      if (vpOnRef.current) recomputeVP()
```

Then add `recomputeVP` to `loadData`'s dependency array (PriceChart.tsx:295): change `}, [ticker, appendLiveBar])` to `}, [ticker, appendLiveBar, recomputeVP])`.

- [ ] **Step 5: Keep today's bar in the cache inside `appendLiveBar`**

In `appendLiveBar`, after the `volRef.current.update({...})` call (PriceChart.tsx:247-251), before `return live.market_status`, add:

```tsx
      const vpBar = { time: today, open, high, low, close, volume: Number(live.volume ?? 0) }
      const arr = dataRef.current
      if (arr.length && arr[arr.length - 1].time === today) arr[arr.length - 1] = vpBar
      else arr.push(vpBar)
      if (vpOnRef.current) recomputeVP()
```

Add `recomputeVP` to `appendLiveBar`'s dependency array (PriceChart.tsx:256): change `}, [ticker])` to `}, [ticker, recomputeVP])`.

- [ ] **Step 6: Subscribe to visible-range changes in the init effect**

In the init effect, after `volRef.current = volSeries` (PriceChart.tsx:387) and before `loadData('1Y')`, add:

```tsx
      chart.timeScale().subscribeVisibleLogicalRangeChange(scheduleVP)
```

In that effect's cleanup (PriceChart.tsx:392-406), add an unsubscribe + rAF cancel + VP reset. Replace the cleanup body with:

```tsx
    return () => {
      destroyed = true
      clearWyckoff()
      markersRef.current = null
      if (vpRafRef.current != null) { cancelAnimationFrame(vpRafRef.current); vpRafRef.current = null }
      try { chartRef.current?.timeScale().unsubscribeVisibleLogicalRangeChange(scheduleVP) } catch { /* gone */ }
      chartRef.current?.remove()
      chartRef.current  = null
      candleRef.current = null
      volRef.current    = null
      vpRef.current     = null
      lcModRef.current  = null
      setActiveRange('1Y')
      setHovered(null)
      setEventTip(null)
      setWyckoffOn(false)
      wyckoffOnRef.current = false
      setVpOn(false)
      vpOnRef.current = false
    }
```

Add `scheduleVP` to the init effect's dependency array (PriceChart.tsx:407): change `}, [ticker, loadData, clearWyckoff])` to `}, [ticker, loadData, clearWyckoff, scheduleVP])`.

- [ ] **Step 7: Add the toggle handler**

After `toggleWyckoff` (PriceChart.tsx:432), add:

```tsx
  // Toggle the Volume Profile overlay on/off.
  const toggleVP = useCallback(() => {
    setVpOn(prev => {
      const next = !prev
      vpOnRef.current = next
      if (next) {
        if (candleRef.current && !vpRef.current) {
          vpRef.current = new VolumeProfilePrimitive()
          candleRef.current.attachPrimitive(vpRef.current)
        }
        recomputeVP()
      } else {
        if (candleRef.current && vpRef.current) {
          try { candleRef.current.detachPrimitive(vpRef.current) } catch { /* gone */ }
        }
        vpRef.current = null
        if (vpRafRef.current != null) { cancelAnimationFrame(vpRafRef.current); vpRafRef.current = null }
      }
      return next
    })
  }, [recomputeVP])
```

- [ ] **Step 8: Add the VP toggle button**

After the Wyckoff `<button>` closing tag (PriceChart.tsx:485), still inside the `flex gap-1 items-center` div, add:

```tsx
          {/* Volume Profile toggle */}
          <button
            onClick={toggleVP}
            title="Overlay Volume Profile — volume traded per price level over the visible range (POC + 70% value area). Recomputes on pan/zoom."
            className={`px-2.5 py-0.5 text-[11px] font-mono rounded transition-all ${
              vpOn
                ? 'bg-[#7aa2f7]/15 text-[#7aa2f7] border border-[#7aa2f7]/30'
                : 'text-[#6b6b80] hover:text-[#e8e8f0] border border-transparent hover:border-[#2a2a3a]'
            }`}
          >
            VP
          </button>
```

- [ ] **Step 9: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors (the `VProfile` reference in Task 2 now resolves via the Step 1 import).

- [ ] **Step 10: Lint**

Run: `cd frontend && npx eslint components/stocks/PriceChart.tsx`
Expected: no errors.

- [ ] **Step 11: Run the full frontend test suite (regression)**

Run: `cd frontend && npx jest`
Expected: PASS — existing suites still green, plus the `volumeProfile` suite from Task 1.

- [ ] **Step 12: Build**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 13: Commit**

```bash
git add frontend/components/stocks/PriceChart.tsx
git commit -m "feat(frontend): visible-range volume profile overlay on PriceChart"
```

---

## Task 4: Manual verification (browser)

**Files:** none (verification only).

Canvas rendering can't be asserted in jsdom, so confirm visually.

- [ ] **Step 1: Start the frontend**

Run: `cd frontend && npm run dev` (port 3001). Open a stock detail page, e.g. `http://localhost:3001/stocks/<TICKER>`.

- [ ] **Step 2: Verify the overlay**

Confirm each:
- VP button sits next to Wyckoff, styled like the range buttons, off by default.
- Toggling VP on draws left-anchored translucent blue bars; candles remain visible through them.
- An amber POC line sits at the highest-volume price level; value-area bins (the 70% band) read slightly stronger than the rest.
- Panning/zooming recomputes the profile to the new visible window (bars resize/shift); no visible lag storm.
- Switching range buttons (1M…ALL) recomputes the profile.
- VP + Wyckoff both on: VP bars stay behind candles, Wyckoff markers/boxes on top; no conflict.
- Toggling VP off clears the bars cleanly; toggling back on redraws.
- Switching ticker resets VP to off with no console errors.

- [ ] **Step 3: Record the result**

If all pass, note completion. If anything is off, file the discrepancy and return to the relevant task.

---

## Self-Review Notes

- **Spec coverage:** POC + value area (Task 1 algorithm + Task 2 draw); N=24 const (`VP_BINS`); spread-high–low distribution (Task 1 Step 3 loop); left-anchored bars (Task 2 `fillRect(0, …)`); default-off toggle independent of Wyckoff (Task 3 Steps 7-8); visible-range price extent (Task 1 derives lo/hi from candles); rAF-throttled recompute on pan/zoom (Task 3 Steps 3, 6); edge cases empty/single/flat (Task 1 tests); cleanup/unsubscribe (Task 3 Step 6). All covered.
- **Type consistency:** `computeVolumeProfile`, `VPCandle`, `VProfile`, `VPBin` consistent across Tasks 1-3. `VolumeProfilePrimitive.setProfile`/`attachPrimitive`/`detachPrimitive` names consistent. `dataRef` element type `VPCandle & { time: string }` matches both the cache writes (Task 3 Steps 4-5) and `computeVolumeProfile`'s structural `VPCandle` param.
- **No placeholders:** every code step contains full code; commands have expected output.
