'use client'
import { useState } from 'react'
import type { HeatmapItem } from '@/lib/types'

interface HeatmapGridProps {
  items: HeatmapItem[]
}

type Filter = 'all' | 'gainers' | 'losers'

function cellBg(pct: number | null): string {
  if (pct === null) return 'bg-surface'
  if (pct > 4)  return 'bg-green-600'
  if (pct > 2)  return 'bg-green-700'
  if (pct > 0)  return 'bg-green-900'
  if (pct === 0) return 'bg-surface'
  if (pct > -2) return 'bg-red-900'
  if (pct > -4) return 'bg-red-700'
  return 'bg-red-600'
}

interface TooltipState {
  item: HeatmapItem
  x: number
  y: number
}

function groupBySector(items: HeatmapItem[]): [string, HeatmapItem[]][] {
  const map = new Map<string, HeatmapItem[]>()
  for (const item of items) {
    const s = item.sector || 'Other'
    const arr = map.get(s) ?? []
    arr.push(item)
    map.set(s, arr)
  }
  return Array.from(map.entries()).sort(([a], [b]) => a.localeCompare(b))
}

export default function HeatmapGrid({ items }: HeatmapGridProps) {
  const [filter, setFilter] = useState<Filter>('all')
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const [tooltip, setTooltip] = useState<TooltipState | null>(null)

  const filtered = items.filter((item) => {
    const pct = item.change_pct !== null ? Number(item.change_pct) : null
    if (filter === 'gainers') return pct !== null && pct > 0
    if (filter === 'losers')  return pct !== null && pct < 0
    return true
  })

  const sectors = groupBySector(filtered)

  function toggleSector(sector: string) {
    setCollapsed((prev) => {
      const next = new Set(prev)
      next.has(sector) ? next.delete(sector) : next.add(sector)
      return next
    })
  }

  return (
    <div className="bg-surface border border-border-custom rounded-lg p-4 relative">
      {/* Header + filter */}
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-xs text-muted uppercase tracking-widest">Market Heatmap</h3>
        <div className="flex gap-1">
          {(['all', 'gainers', 'losers'] as Filter[]).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`text-[10px] uppercase tracking-wide px-2.5 py-1 rounded transition-colors ${
                filter === f
                  ? 'bg-elevated text-white'
                  : 'text-muted hover:text-white'
              }`}
            >
              {f}
            </button>
          ))}
        </div>
      </div>

      {sectors.length === 0 && (
        <p className="text-muted text-xs">No data.</p>
      )}

      <div className="space-y-4">
        {sectors.map(([sector, sectorItems]) => {
          const isCollapsed = collapsed.has(sector)
          const avgPct = sectorItems.reduce((s, i) => s + Number(i.change_pct ?? 0), 0) / sectorItems.length
          const avgColor = avgPct > 0 ? 'text-accent-green' : avgPct < 0 ? 'text-accent-red' : 'text-muted'
          return (
            <div key={sector}>
              <button
                onClick={() => toggleSector(sector)}
                className="w-full flex items-center justify-between border-b border-border-custom pb-1 mb-1.5 group"
              >
                <span className="text-[10px] text-muted uppercase tracking-widest group-hover:text-white transition-colors">
                  {isCollapsed ? '▶' : '▼'} {sector}
                  <span className="ml-1.5 text-muted/60">({sectorItems.length})</span>
                </span>
                <span className={`text-[10px] font-medium ${avgColor}`}>
                  avg {avgPct > 0 ? '+' : ''}{avgPct.toFixed(2)}%
                </span>
              </button>

              {!isCollapsed && (
                <div className="grid grid-cols-[repeat(auto-fill,minmax(80px,1fr))] gap-1">
                  {sectorItems.map((item) => {
                    const pct = item.change_pct !== null ? Number(item.change_pct) : null
                    return (
                      <div
                        key={item.ticker}
                        className={`${cellBg(pct)} rounded p-1.5 flex flex-col items-center cursor-default transition-opacity hover:opacity-80`}
                        onMouseEnter={(e) => {
                          const rect = e.currentTarget.getBoundingClientRect()
                          const containerRect = e.currentTarget.closest('.relative')!.getBoundingClientRect()
                          setTooltip({
                            item,
                            x: rect.left - containerRect.left + rect.width / 2,
                            y: rect.top - containerRect.top - 8,
                          })
                        }}
                        onMouseLeave={() => setTooltip(null)}
                      >
                        <span className="text-[10px] font-semibold text-white leading-none text-center">{item.ticker}</span>
                        <span className="text-[10px] text-white/70 mt-0.5">
                          {pct !== null ? `${pct > 0 ? '+' : ''}${pct.toFixed(1)}%` : '—'}
                        </span>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* Tooltip */}
      {tooltip && (
        <div
          className="absolute z-50 pointer-events-none bg-elevated border border-border-custom rounded-lg px-3 py-2 shadow-lg text-xs -translate-x-1/2 -translate-y-full"
          style={{ left: tooltip.x, top: tooltip.y }}
        >
          <p className="font-semibold text-white mb-1">{tooltip.item.name}</p>
          <p className="text-muted">{tooltip.item.ticker} · {tooltip.item.sector}</p>
          <div className="flex gap-3 mt-1.5">
            <span className="text-muted">LTP <span className="text-white">{tooltip.item.ltp?.toFixed(2) ?? '—'}</span></span>
            <span className={`font-medium ${Number(tooltip.item.change_pct ?? 0) >= 0 ? 'text-accent-green' : 'text-accent-red'}`}>
              {Number(tooltip.item.change_pct ?? 0) > 0 ? '+' : ''}{Number(tooltip.item.change_pct ?? 0).toFixed(2)}%
            </span>
          </div>
        </div>
      )}
    </div>
  )
}
