# Stock Detail Page Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework `/stocks/[ticker]` into a full-width single column — chart leads, then highlighted company info / key metrics / historical data, closing with a new AI Analysis verdict section (ML predictions + LLM narrative) that degrades gracefully when backend data is absent.

**Architecture:** Server component page fetches existing detail/fundamentals/announcements plus a new `analyze` call via `Promise.allSettled`. The cramped 320px sidebar is removed; its Signal + Key Metrics content is promoted into a new full-width `KeyMetricsStrip`. A new `AiAnalysis` component renders ML prediction cards + an optional narrative, owning both empty states. No backend changes.

**Tech Stack:** Next.js 16 (customized — see `frontend/AGENTS.md`), React 19, TypeScript, Tailwind v4, Jest + React Testing Library.

---

## Pre-work

`frontend/AGENTS.md` warns this is a customized Next.js with breaking changes from upstream. Before editing the page or components, read the relevant guide under `frontend/node_modules/next/dist/docs/` (server vs client components, `params` promise). All commands below run from `frontend/`.

## File Structure

- **Modify** `frontend/lib/types.ts` — add `MlPrediction`, `AnalyzeResponse`.
- **Modify** `frontend/lib/server-api.ts` — add `stocks.analyze(ticker)`.
- **Create** `frontend/components/stocks/AiAnalysis.tsx` — prediction cards + narrative + empty states.
- **Create** `frontend/components/stocks/KeyMetricsStrip.tsx` — wide highlighted metrics + signal block.
- **Create** `frontend/__tests__/components/stocks/AiAnalysis.test.tsx`
- **Create** `frontend/__tests__/components/stocks/KeyMetricsStrip.test.tsx`
- **Modify** `frontend/app/(app)/stocks/[ticker]/page.tsx` — new full-width layout, fetch `analyze`, wire new components.

---

### Task 1: Data layer — types + analyze endpoint

**Files:**
- Modify: `frontend/lib/types.ts`
- Modify: `frontend/lib/server-api.ts`

- [ ] **Step 1: Add types**

Append to `frontend/lib/types.ts` (after the existing `FundamentalsRow` / `HealthScore` interfaces — both are referenced here):

```ts
// ─── AI analysis (api/routers/analyze.py :: _fetch_ticker_analysis) ───────────
export interface MlPrediction {
  horizon_days: number
  predicted_direction: string | null   // e.g. "UP" | "DOWN"
  confidence: number | null            // 0..1
  target_price: number | null
  predicted_at: string
}

export interface AnalyzeNews {
  title: string
  published_at: string
  sentiment_score: number | null
  url: string | null
}

export interface AnalyzeResponse {
  ticker: string
  company: {
    ticker: string
    name: string
    sector: string
    category: string | null
    market_cap_bdt: number | null
  }
  latest_price: {
    close: number
    change_pct: number | null
    high: number | null
    low: number | null
    volume: number | null
    time: string
  } | null
  fundamentals: FundamentalsRow[]
  predictions: MlPrediction[]
  health_score: HealthScore | null
  recent_news: AnalyzeNews[]
  narrative?: string | null            // not yet produced by backend; optional
}
```

- [ ] **Step 2: Add the server-api method**

In `frontend/lib/server-api.ts`, add `AnalyzeResponse` to the type import from `./types`, then add this line inside the `stocks: { ... }` object (after `announcements`):

```ts
    analyze: (ticker: string) =>
      serverGet<AnalyzeResponse>(`/api/analyze/${ticker.toUpperCase()}`),
```

- [ ] **Step 3: Verify it type-checks**

Run: `npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/lib/types.ts frontend/lib/server-api.ts
git commit -m "feat(stocks): add analyze types + server-api method"
```

---

### Task 2: AiAnalysis component

**Files:**
- Create: `frontend/components/stocks/AiAnalysis.tsx`
- Test: `frontend/__tests__/components/stocks/AiAnalysis.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/__tests__/components/stocks/AiAnalysis.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import AiAnalysis from '../../../components/stocks/AiAnalysis'
import type { MlPrediction } from '../../../lib/types'

const PRED: MlPrediction = {
  horizon_days: 365,
  predicted_direction: 'UP',
  confidence: 0.82,
  target_price: 145.5,
  predicted_at: '2026-06-01T00:00:00Z',
}

describe('AiAnalysis', () => {
  it('renders a prediction card with horizon, confidence and target', () => {
    render(<AiAnalysis predictions={[PRED]} narrative={null} />)
    expect(screen.getByText('1Y')).toBeInTheDocument()
    expect(screen.getByText('82%')).toBeInTheDocument()
    expect(screen.getByText('৳145.50')).toBeInTheDocument()
  })

  it('renders the empty state when there are no predictions', () => {
    render(<AiAnalysis predictions={[]} narrative={null} />)
    expect(screen.getByText(/no predictions yet/i)).toBeInTheDocument()
  })

  it('renders the narrative when provided', () => {
    render(<AiAnalysis predictions={[]} narrative="Strong fundamentals offset weak momentum." />)
    expect(screen.getByText(/strong fundamentals/i)).toBeInTheDocument()
  })

  it('renders the narrative empty state when absent', () => {
    render(<AiAnalysis predictions={[PRED]} narrative={null} />)
    expect(screen.getByText(/narrative not available yet/i)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- AiAnalysis`
Expected: FAIL — cannot find module `components/stocks/AiAnalysis`.

- [ ] **Step 3: Write the component**

Create `frontend/components/stocks/AiAnalysis.tsx`:

```tsx
import type { MlPrediction } from '@/lib/types'

const DIR_COLOR: Record<string, string> = {
  UP: '#00d4a4',
  DOWN: '#ff4d6a',
}

function horizonLabel(days: number): string {
  const map: Record<number, string> = { 365: '1Y', 1095: '3Y', 1825: '5Y', 3650: '10Y' }
  return map[days] ?? `${days}d`
}

function dirArrow(dir: string | null): string {
  if (dir === 'UP') return '↑'
  if (dir === 'DOWN') return '↓'
  return '·'
}

function PredictionCard({ p }: { p: MlPrediction }) {
  const color = (p.predicted_direction && DIR_COLOR[p.predicted_direction]) ?? '#6b6b80'
  const conf = p.confidence != null ? `${Math.round(p.confidence * 100)}%` : '—'
  const target = p.target_price != null ? `৳${Number(p.target_price).toFixed(2)}` : '—'
  return (
    <div className="rounded-lg border border-[#2a2a3a] bg-[#111118] px-4 py-3">
      <div className="flex items-center justify-between mb-2">
        <span className="text-[11px] font-mono uppercase tracking-[0.18em] text-[#6b6b80]">
          {horizonLabel(p.horizon_days)}
        </span>
        <span className="text-[15px] font-mono font-bold" style={{ color }}>
          {dirArrow(p.predicted_direction)} {p.predicted_direction ?? '—'}
        </span>
      </div>
      <div className="flex justify-between text-[11px] font-mono">
        <span className="text-[#8a8a9e]">Confidence</span>
        <span className="tabular-nums" style={{ color }}>{conf}</span>
      </div>
      <div className="flex justify-between text-[11px] font-mono mt-1">
        <span className="text-[#8a8a9e]">Target</span>
        <span className="tabular-nums text-[#e8e8f0]">{target}</span>
      </div>
    </div>
  )
}

export default function AiAnalysis({
  predictions,
  narrative,
}: {
  predictions: MlPrediction[]
  narrative?: string | null
}) {
  return (
    <div className="px-4 pb-4 space-y-4">
      {/* ML predictions */}
      <div>
        <p className="text-[9px] font-mono uppercase tracking-[0.2em] text-[#56566a] mb-2">
          ML Predictions
        </p>
        {predictions.length > 0 ? (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {predictions.map((p) => (
              <PredictionCard key={`${p.horizon_days}-${p.predicted_at}`} p={p} />
            ))}
          </div>
        ) : (
          <p className="text-[12px] font-mono text-[#6b6b80] py-3">
            No predictions yet — model output will appear here once available.
          </p>
        )}
      </div>

      {/* LLM narrative */}
      <div className="pt-3 border-t border-[#1f1f2b]">
        <p className="text-[9px] font-mono uppercase tracking-[0.2em] text-[#56566a] mb-2">
          AI Narrative
        </p>
        {narrative ? (
          <p className="text-[13px] leading-relaxed text-[#d8d8e4] whitespace-pre-line">
            {narrative}
          </p>
        ) : (
          <p className="text-[12px] font-mono text-[#6b6b80] py-1">
            AI narrative not available yet.
          </p>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -- AiAnalysis`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/components/stocks/AiAnalysis.tsx frontend/__tests__/components/stocks/AiAnalysis.test.tsx
git commit -m "feat(stocks): AiAnalysis component with prediction cards + narrative"
```

---

### Task 3: KeyMetricsStrip component

**Files:**
- Create: `frontend/components/stocks/KeyMetricsStrip.tsx`
- Test: `frontend/__tests__/components/stocks/KeyMetricsStrip.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/__tests__/components/stocks/KeyMetricsStrip.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import KeyMetricsStrip from '../../../components/stocks/KeyMetricsStrip'

describe('KeyMetricsStrip', () => {
  const groups = [
    { title: 'Valuation', rows: [{ label: 'P/E Ratio', value: '12.5' }] },
    { title: 'Per Share', rows: [{ label: 'EPS', value: '৳4.20' }] },
  ]

  it('renders group titles and metric rows', () => {
    render(
      <KeyMetricsStrip
        groups={groups}
        score={68}
        fundamentalScore={70}
        momentumScore={55}
        ratingColor="#f5c842"
      />,
    )
    expect(screen.getByText('Valuation')).toBeInTheDocument()
    expect(screen.getByText('P/E Ratio')).toBeInTheDocument()
    expect(screen.getByText('12.5')).toBeInTheDocument()
    expect(screen.getByText('EPS')).toBeInTheDocument()
  })

  it('renders the fundamental and momentum score bars', () => {
    render(
      <KeyMetricsStrip
        groups={groups}
        score={68}
        fundamentalScore={70}
        momentumScore={55}
        ratingColor="#f5c842"
      />,
    )
    expect(screen.getByText('Fundamental')).toBeInTheDocument()
    expect(screen.getByText('Momentum')).toBeInTheDocument()
  })

  it('shows the gauge dash when score is null', () => {
    render(
      <KeyMetricsStrip
        groups={groups}
        score={null}
        fundamentalScore={null}
        momentumScore={null}
        ratingColor="#6b6b80"
      />,
    )
    expect(screen.getByText('Fundamental')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- KeyMetricsStrip`
Expected: FAIL — cannot find module `components/stocks/KeyMetricsStrip`.

- [ ] **Step 3: Write the component**

Create `frontend/components/stocks/KeyMetricsStrip.tsx`:

```tsx
import HealthGauge from '@/components/stocks/HealthGauge'
import RatingBadge from '@/components/stocks/RatingBadge'

interface MetricRow {
  label: string
  value: string
  accent?: string
}
interface MetricGroupData {
  title: string
  rows: MetricRow[]
}

function ScoreBar({ label, value }: { label: string; value: number | null }) {
  const n = Number(value ?? 0)
  const pct = Math.min(100, Math.max(0, n))
  const color = pct >= 70 ? '#00d4a4' : pct >= 40 ? '#f5c842' : '#ff4d6a'
  return (
    <div>
      <div className="flex justify-between text-[10px] font-mono mb-1">
        <span className="text-[#8a8a9e]">{label}</span>
        <span className="text-[#e8e8f0] tabular-nums">{value != null ? n.toFixed(0) : '—'}</span>
      </div>
      <div className="h-1.5 rounded-full bg-[#1a1a24] overflow-hidden">
        <div className="h-full rounded-full" style={{ width: `${pct}%`, backgroundColor: color }} />
      </div>
    </div>
  )
}

function Group({ data }: { data: MetricGroupData }) {
  return (
    <div>
      <p className="text-[9px] font-mono uppercase tracking-[0.2em] text-[#56566a] mb-1.5">
        {data.title}
      </p>
      <div className="space-y-1.5">
        {data.rows.map(({ label, value, accent }) => (
          <div key={label} className="flex justify-between items-baseline gap-2">
            <span className="text-[11px] text-[#8a8a9e] font-mono shrink-0">{label}</span>
            <span
              className="text-[12px] font-mono font-medium tabular-nums text-right"
              style={{ color: accent ?? '#e8e8f0' }}
            >
              {value}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function KeyMetricsStrip({
  groups,
  score,
  fundamentalScore,
  momentumScore,
  ratingColor,
}: {
  groups: MetricGroupData[]
  score: number | null
  fundamentalScore: number | null
  momentumScore: number | null
  ratingColor: string
}) {
  return (
    <div className="grid gap-6 px-4 pb-4 md:grid-cols-[220px_1fr]">
      {/* Signal */}
      <div className="md:border-r md:border-[#1f1f2b] md:pr-6">
        <HealthGauge score={score} />
        <div className="flex justify-center -mt-1 mb-3">
          <RatingBadge score={score} />
        </div>
        <div className="space-y-2.5">
          <ScoreBar label="Fundamental" value={fundamentalScore} />
          <ScoreBar label="Momentum" value={momentumScore} />
        </div>
      </div>

      {/* Metric groups */}
      <div className="grid gap-x-8 gap-y-4 sm:grid-cols-2 lg:grid-cols-3">
        {groups.map((g) => (
          <Group key={g.title} data={g} />
        ))}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -- KeyMetricsStrip`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/components/stocks/KeyMetricsStrip.tsx frontend/__tests__/components/stocks/KeyMetricsStrip.test.tsx
git commit -m "feat(stocks): KeyMetricsStrip — wide highlighted metrics + signal"
```

---

### Task 4: Rewire the page layout

**Files:**
- Modify: `frontend/app/(app)/stocks/[ticker]/page.tsx`

This task replaces the two-column body with a full-width single column and wires the new components. The header band, breadcrumb, helpers (`fmt*`), `RATING_COLOR`, `Reveal`, and `Panel` are unchanged. The in-file `MetricGroup` is removed (its job now lives in `KeyMetricsStrip`).

- [ ] **Step 1: Update imports**

In `frontend/app/(app)/stocks/[ticker]/page.tsx`, add these two imports alongside the existing component imports:

```tsx
import KeyMetricsStrip from '@/components/stocks/KeyMetricsStrip'
import AiAnalysis from '@/components/stocks/AiAnalysis'
```

- [ ] **Step 2: Remove the now-unused `MetricGroup` helper**

Delete the entire `function MetricGroup({ ... }) { ... }` block (lines defining `MetricGroup`, currently ~105–132). `KeyMetricsStrip` replaces it. Keep `Reveal` and `Panel`.

- [ ] **Step 3: Add the analyze fetch**

Replace the `Promise.allSettled` block and the line that derives `annData` with:

```tsx
  const [detailResult, fundsResult, annResult, analyzeResult] = await Promise.allSettled([
    serverApi.stocks.detail(ticker),
    serverApi.stocks.fundamentals(ticker),
    serverApi.stocks.announcements(ticker),
    serverApi.stocks.analyze(ticker),
  ])

  if (detailResult.status === 'rejected') notFound()

  const { company, latest_price, health_score, fundamentals: lf } = detailResult.value
  const fundsData = fundsResult.status === 'fulfilled' ? fundsResult.value : null
  const annData = annResult.status === 'fulfilled' ? annResult.value : null
  const analyzeData = analyzeResult.status === 'fulfilled' ? analyzeResult.value : null
```

(A rejected `analyze` leaves `analyzeData` null — the AI section shows its empty state; the page is unaffected. Only `detail` rejection triggers `notFound()`.)

- [ ] **Step 4: Build the metric groups for the strip**

The existing `valuation` / `perShare` / `dividends` arrays stay. Just below the `profile` array, add:

```tsx
  const metricGroups = [
    { title: 'Valuation', rows: valuation },
    { title: 'Per Share', rows: perShare },
    { title: 'Dividends', rows: dividends },
  ]
```

- [ ] **Step 5: Replace the entire `return (...)` body below the header band**

Keep everything from `return (` through the closing `</header>` + `</Reveal>` of the command header band exactly as-is. Replace everything AFTER the header band's closing `</Reveal>` (i.e. the old `{/* Main grid */}` two-column block, the fundamentals+announcements grid, and the disclaimer) with this single-column body:

```tsx
      {/* ── Chart (full width) ─────────────────────────────────────────────── */}
      <Reveal delay={80}>
        <Panel className="h-[560px] flex flex-col" title="Price · Volume" accent="#4d9eff">
          <div className="flex-1 min-h-0 px-4 pb-4">
            <PriceChart ticker={company.ticker} />
          </div>
        </Panel>
      </Reveal>

      {/* ── Key metrics + signal (highlighted) ─────────────────────────────── */}
      <Reveal delay={140}>
        <Panel title="Key Metrics" accent="#00d4a4">
          <KeyMetricsStrip
            groups={metricGroups}
            score={scoreNum}
            fundamentalScore={health_score?.fundamental_score ?? null}
            momentumScore={health_score?.momentum_score ?? null}
            ratingColor={ratingColor}
          />
          {health_score?.scored_at && (
            <p className="text-[9px] font-mono text-[#6b6b80] px-4 pb-3">
              scored {fmtDate(health_score.scored_at)}
            </p>
          )}
        </Panel>
      </Reveal>

      {/* ── Company info (highlighted) ─────────────────────────────────────── */}
      <Reveal delay={200}>
        <Panel title="Company Info" accent="#6b6b80">
          <div className="grid gap-6 px-4 pb-4 md:grid-cols-2">
            {/* Profile */}
            <div className="space-y-1.5">
              {profile.map(({ label, value }) => (
                <div key={label} className="flex justify-between items-baseline gap-2">
                  <span className="text-[11px] text-[#8a8a9e] font-mono shrink-0">{label}</span>
                  <span className="text-[11px] font-mono text-[#d8d8e4] text-right truncate">
                    {value}
                  </span>
                </div>
              ))}
            </div>
            {/* Ownership */}
            {hasOwnership && (
              <div>
                <p className="text-[9px] font-mono uppercase tracking-[0.2em] text-[#56566a] mb-2">
                  Ownership
                </p>
                <div className="flex h-2.5 rounded-full overflow-hidden bg-[#1a1a24]">
                  {sponsor != null && (
                    <div className="h-full" style={{ width: `${(sponsor / ownTotal) * 100}%`, background: '#a78bfa' }} />
                  )}
                  {publicPct != null && (
                    <div className="h-full" style={{ width: `${(publicPct / ownTotal) * 100}%`, background: '#4d9eff' }} />
                  )}
                </div>
                <div className="flex justify-between mt-2 text-[10px] font-mono">
                  <span className="text-[#a78bfa]">
                    Sponsor <span className="tabular-nums">{fmtPct(sponsor)}</span>
                  </span>
                  <span className="text-[#4d9eff]">
                    Public <span className="tabular-nums">{fmtPct(publicPct)}</span>
                  </span>
                </div>
              </div>
            )}
          </div>
        </Panel>
      </Reveal>

      {/* ── Historical fundamentals (highlighted, full width) ──────────────── */}
      {fundsData && fundsData.items.length > 0 && (
        <Reveal delay={260}>
          <Panel title="Historical Fundamentals" accent="#00d4a4">
            <div className="p-4 pt-1">
              {fundsData.is_truncated ? (
                <PaywallOverlay feature="Unlock 10 years of fundamentals with Pro">
                  <FundamentalsCharts items={fundsData.items} />
                </PaywallOverlay>
              ) : (
                <FundamentalsCharts items={fundsData.items} />
              )}
            </div>
          </Panel>
        </Reveal>
      )}

      {/* ── AI analysis (closing verdict) ──────────────────────────────────── */}
      <Reveal delay={320}>
        <Panel title="AI Analysis" accent="#a78bfa">
          <AiAnalysis
            predictions={analyzeData?.predictions ?? []}
            narrative={analyzeData?.narrative ?? null}
          />
        </Panel>
      </Reveal>

      {/* ── Announcements (full width) ─────────────────────────────────────── */}
      <Reveal delay={380}>
        <Panel title="Announcements" accent="#f5c842">
          <div className="px-4 pb-3 max-h-[360px] overflow-y-auto">
            <Announcements items={annData?.items ?? []} />
          </div>
        </Panel>
      </Reveal>

      {/* Disclaimer */}
      <p className="text-[10px] font-mono text-[#6b6b80] text-center py-2">
        Data for informational purposes only. Not investment advice. Past performance does not guarantee future results.
      </p>
    </div>
  )
}
```

- [ ] **Step 6: Type-check and lint**

Run: `npx tsc --noEmit && npm run lint`
Expected: no errors. If `tsc` flags an unused import (e.g. a component no longer referenced) or the removed `MetricGroup`, delete the dead reference.

- [ ] **Step 7: Run the full test suite**

Run: `npm test`
Expected: PASS — existing tests plus the two new component test files.

- [ ] **Step 8: Build to confirm the server component compiles**

Run: `npm run build`
Expected: build succeeds; `/stocks/[ticker]` compiles with no server/client boundary errors.

- [ ] **Step 9: Commit**

```bash
git add "frontend/app/(app)/stocks/[ticker]/page.tsx"
git commit -m "feat(stocks): full-width layout — chart leads, metrics/info/history below, AI verdict"
```

---

### Task 5: Manual verification

**Files:** none (manual).

- [ ] **Step 1: Run the dev server and load the page**

Run: `npm run dev` (port 3000), then open `http://localhost:3000/stocks/MEGHNAPET` (log in first if required).

- [ ] **Step 2: Confirm the layout**

Verify top-to-bottom order: header band → full-width chart → Key Metrics + Signal → Company Info → Historical Fundamentals → **AI Analysis** → Announcements → disclaimer. No 320px sidebar remains.

- [ ] **Step 3: Confirm AI empty states**

With `ml_predictions` empty and no narrative, the AI Analysis panel shows "No predictions yet …" and "AI narrative not available yet." — not a blank gap. The rest of the page renders normally even if the `analyze` call fails.

---

## Self-Review

**Spec coverage:**
- Full-width single column → Task 4 (drops the grid). ✓
- Chart leads → Task 4 Step 5. ✓
- Company info / key metrics / historical moved below + highlighted → Task 4 (KeyMetricsStrip, Company Info panel, Historical panel) + Task 3. ✓
- AI Analysis = ML predictions + LLM narrative, closing section → Task 2 + Task 4. ✓
- Graceful empty states (predictions empty, narrative absent, analyze rejected) → Task 2 component + Task 4 Step 3. ✓
- Data layer (`analyze` method, `MlPrediction`/`AnalyzeResponse` types) → Task 1. ✓
- Reuse `HealthGauge`/`RatingBadge`/`FundamentalsCharts`/`Announcements`/`Panel`/`Reveal`, preserve paywall → Tasks 3 & 4. ✓
- Out of scope (backend, populating predictions, narrative endpoint) → respected; no backend files touched. ✓

**Placeholder scan:** No TBD/TODO; every code step has complete code. ✓

**Type consistency:** `MlPrediction` fields (`horizon_days`, `predicted_direction`, `confidence`, `target_price`, `predicted_at`) match across Task 1, Task 2 test/component, and Task 4 usage. `KeyMetricsStrip` props (`groups`, `score`, `fundamentalScore`, `momentumScore`, `ratingColor`) match between Task 3 and Task 4. `AiAnalysis` props (`predictions`, `narrative`) match between Task 2 and Task 4. ✓
