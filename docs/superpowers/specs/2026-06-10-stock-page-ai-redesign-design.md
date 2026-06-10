# Stock Detail Page Redesign — Highlighted Data + AI Analysis

**Date:** 2026-06-10
**Scope:** Frontend UI only (`frontend/`). No backend changes.
**Target route:** `/stocks/[ticker]` (e.g. `/stocks/MEGHNAPET`)
**File:** `frontend/app/(app)/stocks/[ticker]/page.tsx`

## Goal

Restructure the stock detail page so the chart leads, then company info, key
metrics, and historical data are moved below it and visually highlighted, and a
new AI Analysis section closes the page as the synthesizing verdict. AI Analysis
draws from two sources: ML predictions and an LLM narrative.

## Current State

The page uses a two-column grid (`minmax(0,1fr) 320px`):

- **Header band** — ticker, rating pill, company name, sector/category/ISIN
  badges, `LivePrice`.
- **Main grid** — `PriceChart` (left) + sidebar (right, 320px) containing Signal
  (`HealthGauge` + `RatingBadge` + fundamental/momentum bars), Key Metrics
  (valuation / per-share / dividends), Ownership split, Profile.
- **Second grid** — Historical Fundamentals (`FundamentalsCharts`, left) +
  Announcements (right).
- Disclaimer footer.

Data comes from `serverApi.stocks.detail`, `.fundamentals`, `.announcements`.

## Target Layout

Single full-width column (drop the 320px sidebar). Vertical order:

1. **Breadcrumb** — unchanged.
2. **Header band** — unchanged (ticker, rating pill, name, badges, `LivePrice`).
3. **Chart** — full width, taller. `Price · Volume` panel spans the row.
4. **Key Metrics + Signal** — moved below, highlighted. A wide stat strip
   promoting the former sidebar content: Valuation (P/E, Market Cap, Volume),
   Per Share (EPS, NAV), Dividends (cash/stock), plus the Signal gauge + rating
   + fundamental/momentum score bars folded in. Prominent cards across the row.
5. **Company Info** — moved below, highlighted. Profile fields (sector,
   category, ISIN, listing date, fiscal year) + Ownership split bar
   (sponsor vs public).
6. **Historical Fundamentals** — moved below, highlighted. `FundamentalsCharts`
   full width (larger than the current cramped 4-minibar grid). Keeps the
   `PaywallOverlay` when `is_truncated`.
7. **AI Analysis** — NEW closing verdict section (the goal). Two parts:
   - **ML predictions** — one card per horizon: direction (↑/↓), confidence %,
     target price. Source: `/api/analyze/{ticker}` → `ml_predictions`.
   - **LLM narrative** — written thesis block referencing the numbers.
8. **Announcements** — full width.
9. **Disclaimer** — unchanged.

## Components

Reuse existing: `HealthGauge`, `RatingBadge`, `FundamentalsCharts`,
`Announcements`, `LivePrice`, `PriceChart`, and the in-page `Panel` / `Reveal` /
`MetricGroup` primitives.

New:

- **`KeyMetricsStrip.tsx`** — the wide highlighted metrics + signal block
  (item 4). Server-safe (no client state); takes the metric rows, health score
  numbers, and rating as props. May fold `HealthGauge` (client) in via a child.
- **`AiAnalysis.tsx`** — the AI section (item 7). Renders prediction cards from
  an `MlPrediction[]` plus an optional `narrative` string. Owns both empty
  states.

Whether to extract a `CompanyInfo` card component or keep it inline in
`page.tsx` is left to implementation — it is small and currently inline.

## Data Layer

Add to `frontend/lib/server-api.ts`:

```ts
analyze: (ticker: string) =>
  serverGet<AnalyzeResponse>(`/api/analyze/${ticker.toUpperCase()}`),
```

Add to `frontend/lib/types.ts`, mirroring the backend `_fetch_ticker_analysis`
shape in `api/routers/analyze.py`:

```ts
export interface MlPrediction {
  horizon_days: number
  predicted_direction: string | null   // e.g. "UP" | "DOWN"
  confidence: number | null            // 0..1
  target_price: number | null
  predicted_at: string
}

export interface AnalyzeResponse {
  ticker: string
  company: { ticker: string; name: string; sector: string; category: string | null; market_cap_bdt: number | null }
  latest_price: { close: number; change_pct: number | null; high: number | null; low: number | null; volume: number | null; time: string } | null
  fundamentals: FundamentalsRow[]
  predictions: MlPrediction[]
  health_score: HealthScore | null
  recent_news: { title: string; published_at: string; sentiment_score: number | null; url: string | null }[]
  narrative?: string | null            // not yet produced by backend; optional
}
```

Note: `narrative` is not returned by the backend today. It is declared optional
so the UI compiles and degrades gracefully; it lights up if/when the backend
adds it.

The page fetches `analyze` alongside the existing three calls via
`Promise.allSettled`. A rejected `analyze` result must NOT block the page —
only `detail` rejection triggers `notFound()`. The AI section renders its empty
state when `analyze` is rejected or returns no predictions/narrative.

## Empty / Graceful States (UI-only scope)

- **ML predictions empty** (table is Phase-1E-stubbed, likely no rows): show a
  muted "No predictions yet" state inside the AI panel, not a blank gap.
- **Narrative absent** (no backend endpoint — the chat agent in `chat/` is not
  even registered in `api/main.py`): show a muted "AI narrative not available
  yet" state.
- **Whole `analyze` call fails**: AI section shows the combined empty state; the
  rest of the page is unaffected.

These are the explicit boundary of this work. Wiring the chat agent to a
narrative endpoint and populating `ml_predictions` are backend follow-ups,
tracked separately.

## Styling & Conventions

- Match the existing dark palette and `Panel` / `Reveal` / `MetricGroup`
  primitives already in `page.tsx`. Reuse `RATING_COLOR`, the `fmt*` helpers,
  and the staggered `Reveal` delay pattern.
- Prediction direction colors: up `#00d4a4`, down `#ff4d6a`, neutral `#6b6b80`
  (consistent with `RATING_COLOR`).
- Server components stay server components; only `HealthGauge`,
  `FundamentalsCharts`, `LivePrice`, `PriceChart`, `Announcements` are client.

## Out of Scope

- Any backend change (`api/`, `chat/`, `ml/`).
- Populating `ml_predictions`.
- Building a narrative-generation endpoint.
- Embedded per-ticker chat box.

## Testing

- Page renders with chart full width and all moved sections stacked below in the
  specified order.
- AI section renders prediction cards when `predictions` is non-empty.
- AI section renders both empty states when predictions/narrative are absent and
  when the `analyze` call is rejected — without breaking the rest of the page.
- Existing paywall behavior on Historical Fundamentals is preserved.
- Follow existing component test patterns under `frontend/__tests__/`.

## Implementation Note

`frontend/AGENTS.md` warns this is a customized Next.js with breaking changes
from upstream. Read the relevant guide under `node_modules/next/dist/docs/`
before writing page/component code.
