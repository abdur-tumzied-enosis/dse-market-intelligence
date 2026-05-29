# PriceChart — lightweight-charts Migration

**Date:** 2026-05-27  
**Scope:** Replace custom SVG candlestick chart in `frontend/components/stocks/PriceChart.tsx` with TradingView lightweight-charts v5.

---

## Goal

Replace the hand-rolled SVG chart (candles, axes, grid, crosshair, drag/zoom) with lightweight-charts. Keep the surrounding UI (range buttons, OHLCV hover strip) unchanged.

---

## Package

```
lightweight-charts  (latest v5.x)
```

Install into `frontend/` (not the monorepo root).

---

## Component: PriceChart.tsx

### What changes

| Current (custom SVG) | Replacement |
|---|---|
| SVG candles + wicks | `CandlestickSeries` |
| SVG volume bars | `HistogramSeries` overlaid on price pane |
| Manual Y-axis, X-axis, grid | library built-ins |
| Custom drag/pan, wheel zoom | library built-ins |
| Custom crosshair lines + price label | library built-in crosshair |
| ResizeObserver → `svgWidth/svgHeight` state | `chart.applyOptions({ width, height })` via ResizeObserver |

### What stays

- Range buttons (1M / 3M / 6M / 1Y / 3Y / ALL) + fetch logic — unchanged
- OHLCV hover strip (O / H / L / C / V date) — driven by `chart.subscribeCrosshairMove()` instead of custom mouse state
- Dark theme colors

### Volume layout

Overlaid on the price chart pane as semi-transparent histogram bars at the bottom (like TradingView.com). `priceScaleId: 'volume'` with `scaleMargins: { top: 0.8, bottom: 0 }` keeps volume in the bottom 20% without overlapping candles.

---

## Theme

Match existing dark palette:

| Property | Value |
|---|---|
| Background | `#111118` |
| Grid lines | `#252535` |
| Axis text | `#6b6b80` |
| Crosshair | `#4d9eff` |
| Up candle | `#00d4a4` |
| Down candle | `#ff4d6a` |
| Volume up | `rgba(0,212,164,0.25)` |
| Volume down | `rgba(255,77,106,0.25)` |
| Border (candlewick) | same as body color |

---

## Data Transform

API returns `OHLCVResponse.items` sorted newest-first (ORDER BY day DESC). Transform to lightweight-charts format:

```ts
// lightweight-charts requires { time, open, high, low, close } with time ascending
const candles = [...resp.items]
  .reverse()                      // oldest first
  .filter(d => d.close != null)
  .map(d => ({
    time: d.day as string,        // "YYYY-MM-DD"
    open:  Number(d.open  ?? d.close),
    high:  Number(d.high  ?? d.close),
    low:   Number(d.low   ?? d.close),
    close: Number(d.close),
  }))

const volumes = [...resp.items]
  .reverse()
  .filter(d => d.close != null)
  .map(d => ({
    time:  d.day as string,
    value: Number(d.volume ?? 0),
    color: Number(d.close) >= Number(d.open ?? d.close)
      ? 'rgba(0,212,164,0.25)'
      : 'rgba(255,77,106,0.25)',
  }))
```

---

## Lifecycle

```
mount     → createChart(container, options) + add series
range btn → fetch new data → series.setData(candles); volSeries.setData(vols)
            → chart.timeScale().fitContent()
resize    → ResizeObserver → chart.applyOptions({ width, height })
unmount   → chart.remove()
ticker ↑  → remove() + recreate (effect dependency on ticker)
```

---

## File Changes

| File | Change |
|---|---|
| `frontend/package.json` | add `lightweight-charts` |
| `frontend/components/stocks/PriceChart.tsx` | full rewrite |

No other files change.

---

## Out of Scope

- Comparing multiple tickers on the same chart
- Drawing tools
- Indicators (MA, RSI, etc.)
- Saving chart state across navigation
