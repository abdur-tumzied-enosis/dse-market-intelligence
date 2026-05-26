'use client'
import { useState, useRef, useEffect } from 'react'
import type { HeatmapItem } from '@/lib/types'

// ── Squarified treemap ────────────────────────────────────────────────────────

interface Rect { x: number; y: number; w: number; h: number }

function worstAspect(row: number[], rowLen: number): number {
  const s = row.reduce((a, b) => a + b, 0)
  const rowW = s / rowLen
  let worst = 0
  for (const a of row) {
    const h = a / rowW
    worst = Math.max(worst, Math.max(rowW / h, h / rowW))
  }
  return worst
}

function squarify(values: number[], rect: Rect): Rect[] {
  const total = values.reduce((a, b) => a + b, 0)
  if (total === 0 || rect.w <= 0 || rect.h <= 0) return values.map(() => rect)
  const area = rect.w * rect.h
  const scaled = values.map(v => (v / total) * area)
  const out: Rect[] = new Array(values.length)
  layout(scaled, rect, 0, out)
  return out
}

function layout(areas: number[], rect: Rect, offset: number, out: Rect[]) {
  if (areas.length === 0) return
  if (areas.length === 1) { out[offset] = rect; return }

  const { x, y, w, h } = rect
  const horizontal = w >= h
  const side = horizontal ? h : w

  // Build row greedily while aspect improves
  let row: number[] = []
  let i = 0
  for (; i < areas.length; i++) {
    const candidate = [...row, areas[i]]
    if (row.length > 0 && worstAspect(candidate, side) > worstAspect(row, side)) break
    row = candidate
  }

  const rowSum = row.reduce((a, b) => a + b, 0)
  const rowDim = rowSum / side
  let pos = horizontal ? y : x
  for (let j = 0; j < row.length; j++) {
    const itemDim = row[j] / rowDim
    out[offset + j] = horizontal
      ? { x, y: pos, w: rowDim, h: itemDim }
      : { x: pos, y, w: itemDim, h: rowDim }
    pos += itemDim
  }

  const nextRect = horizontal
    ? { x: x + rowDim, y, w: w - rowDim, h }
    : { x, y: y + rowDim, w, h: h - rowDim }
  layout(areas.slice(i), nextRect, offset + i, out)
}

// ── Color scale ───────────────────────────────────────────────────────────────

function cellStyle(pct: number | null): { bg: string; text: string } {
  if (pct === null) return { bg: '#1a1a24', text: '#6b6b80' }
  if (pct >  6) return { bg: '#15803d', text: '#fff' }
  if (pct >  3) return { bg: '#166534', text: '#fff' }
  if (pct >  0) return { bg: '#14532d', text: '#d1fae5' }
  if (pct === 0) return { bg: '#1a1a24', text: '#6b6b80' }
  if (pct > -3) return { bg: '#7f1d1d', text: '#fee2e2' }
  if (pct > -6) return { bg: '#991b1b', text: '#fff' }
  return { bg: '#b91c1c', text: '#fff' }
}

// ── Types ─────────────────────────────────────────────────────────────────────

interface HeatmapGridProps { items: HeatmapItem[] }
type Filter = 'all' | 'gainers' | 'losers'

interface Tooltip {
  item: HeatmapItem
  x: number
  y: number
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function HeatmapGrid({ items }: HeatmapGridProps) {
  const [filter, setFilter] = useState<Filter>('all')
  const [tooltip, setTooltip] = useState<Tooltip | null>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const [dims, setDims] = useState({ w: 800, h: 520 })

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const obs = new ResizeObserver(([e]) => {
      const { width } = e.contentRect
      setDims({ w: width, h: Math.max(320, Math.round(width * 0.6)) })
    })
    obs.observe(el)
    return () => obs.disconnect()
  }, [])

  const filtered = items.filter(item => {
    const p = Number(item.change_pct ?? 0)
    if (filter === 'gainers') return p > 0
    if (filter === 'losers')  return p < 0
    return true
  })

  // Group by sector, sort sectors by total market cap desc
  const sectorMap = new Map<string, HeatmapItem[]>()
  for (const item of filtered) {
    const s = item.sector || 'Other'
    const arr = sectorMap.get(s) ?? []
    arr.push(item)
    sectorMap.set(s, arr)
  }

  const sectorSize = (items: HeatmapItem[]) =>
    items.reduce((s, i) => s + (i.market_cap ?? i.value_bdt ?? 1), 0)

  const sectors = Array.from(sectorMap.entries())
    .sort(([, a], [, b]) => sectorSize(b) - sectorSize(a))

  const sectorValues = sectors.map(([, items]) => sectorSize(items))

  // Top-level treemap: one rect per sector
  const sectorRects = squarify(sectorValues, { x: 0, y: 0, w: dims.w, h: dims.h })

  return (
    <div className="bg-surface border border-border-custom rounded-lg p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-xs text-muted uppercase tracking-widest">Market Heatmap</h3>
        <div className="flex gap-1">
          {(['all', 'gainers', 'losers'] as Filter[]).map(f => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`text-[10px] uppercase tracking-wide px-2.5 py-1 rounded transition-colors ${
                filter === f ? 'bg-elevated text-white' : 'text-muted hover:text-white'
              }`}
            >
              {f}
            </button>
          ))}
        </div>
      </div>

      {/* Treemap canvas */}
      <div
        ref={containerRef}
        className="relative overflow-hidden rounded"
        style={{ height: dims.h }}
      >
        {sectors.map(([sector, sItems], si) => {
          const sr = sectorRects[si]
          if (!sr) return null

          // Sort stocks by market cap desc for better layout
          const sorted = [...sItems].sort(
            (a, b) => (b.market_cap ?? b.value_bdt ?? 1) - (a.market_cap ?? a.value_bdt ?? 1)
          )
          const stockValues = sorted.map(i => i.market_cap ?? i.value_bdt ?? 1)
          const GAP = 2
          const innerRect = {
            x: GAP, y: GAP,
            w: Math.max(0, sr.w - GAP * 2),
            h: Math.max(0, sr.h - GAP * 2),
          }
          const stockRects = squarify(stockValues, innerRect)

          return (
            <div
              key={sector}
              className="absolute"
              style={{ left: sr.x, top: sr.y, width: sr.w, height: sr.h }}
            >
              {/* Sector label */}
              {sr.w > 60 && sr.h > 24 && (
                <div
                  className="absolute z-10 text-[9px] text-white/40 uppercase tracking-wider font-medium pointer-events-none select-none"
                  style={{ left: GAP + 4, top: GAP + 3 }}
                >
                  {sector}
                </div>
              )}

              {sorted.map((item, ii) => {
                const r = stockRects[ii]
                if (!r || r.w < 2 || r.h < 2) return null
                const pct = item.change_pct !== null ? Number(item.change_pct) : null
                const { bg, text } = cellStyle(pct)
                const showTicker = r.w > 36 && r.h > 20
                const showPct    = r.w > 36 && r.h > 34

                return (
                  <div
                    key={item.ticker}
                    className="absolute flex flex-col items-center justify-center overflow-hidden cursor-default transition-brightness"
                    style={{
                      left: r.x + 1, top: r.y + 1,
                      width: Math.max(0, r.w - 2),
                      height: Math.max(0, r.h - 2),
                      background: bg,
                      borderRadius: 3,
                    }}
                    onMouseEnter={e => {
                      const box = e.currentTarget.getBoundingClientRect()
                      const container = containerRef.current!.getBoundingClientRect()
                      setTooltip({
                        item,
                        x: box.left - container.left + box.width / 2,
                        y: box.top - container.top,
                      })
                    }}
                    onMouseLeave={() => setTooltip(null)}
                  >
                    {showTicker && (
                      <span
                        className="font-semibold leading-none text-center px-0.5 truncate w-full text-center"
                        style={{ color: text, fontSize: Math.min(11, Math.max(7, r.w / 6)) }}
                      >
                        {item.ticker}
                      </span>
                    )}
                    {showPct && pct !== null && (
                      <span
                        className="leading-none mt-0.5"
                        style={{ color: text, fontSize: Math.min(10, Math.max(7, r.w / 7)), opacity: 0.85 }}
                      >
                        {pct > 0 ? '+' : ''}{pct.toFixed(1)}%
                      </span>
                    )}
                  </div>
                )
              })}
            </div>
          )
        })}

        {/* Tooltip */}
        {tooltip && (
          <div
            className="absolute z-50 pointer-events-none bg-elevated border border-border-custom rounded-lg px-3 py-2.5 shadow-xl text-xs -translate-x-1/2 -translate-y-full"
            style={{ left: tooltip.x, top: tooltip.y - 8 }}
          >
            <p className="font-semibold text-white mb-0.5 max-w-[180px] truncate">{tooltip.item.name}</p>
            <p className="text-muted mb-1.5">{tooltip.item.ticker} · {tooltip.item.sector}</p>
            <div className="flex gap-4">
              <div>
                <p className="text-muted text-[10px]">LTP</p>
                <p className="text-white font-medium">{tooltip.item.ltp?.toFixed(2) ?? '—'}</p>
              </div>
              <div>
                <p className="text-muted text-[10px]">Change</p>
                <p className={`font-medium ${Number(tooltip.item.change_pct ?? 0) >= 0 ? 'text-accent-green' : 'text-accent-red'}`}>
                  {Number(tooltip.item.change_pct ?? 0) > 0 ? '+' : ''}{Number(tooltip.item.change_pct ?? 0).toFixed(2)}%
                </p>
              </div>
              {tooltip.item.market_cap && (
                <div>
                  <p className="text-muted text-[10px]">Mkt Cap</p>
                  <p className="text-white font-medium">{(tooltip.item.market_cap / 1000).toFixed(1)}B</p>
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Legend */}
      <div className="flex items-center gap-1 mt-2 justify-end">
        {[
          { bg: '#b91c1c', label: '< −6%' },
          { bg: '#991b1b', label: '−6 to −3%' },
          { bg: '#7f1d1d', label: '−3 to 0%' },
          { bg: '#1a1a24', label: '0%' },
          { bg: '#14532d', label: '0 to +3%' },
          { bg: '#166534', label: '+3 to +6%' },
          { bg: '#15803d', label: '> +6%' },
        ].map(({ bg, label }) => (
          <div key={label} className="flex items-center gap-0.5 group relative">
            <div className="w-4 h-3 rounded-sm" style={{ background: bg }} title={label} />
          </div>
        ))}
        <span className="text-[9px] text-muted ml-1">% change</span>
      </div>
    </div>
  )
}
