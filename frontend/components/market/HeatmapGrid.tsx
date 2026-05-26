import type { HeatmapItem } from '@/lib/types'

interface HeatmapGridProps {
  items: HeatmapItem[]
}

function cellColor(changePct: number | null): string {
  if (changePct === null) return 'bg-surface'
  if (changePct > 2) return 'bg-green-700'
  if (changePct > 0) return 'bg-green-900'
  if (changePct === 0) return 'bg-surface'
  if (changePct > -2) return 'bg-red-900'
  return 'bg-red-700'
}

export default function HeatmapGrid({ items }: HeatmapGridProps) {
  return (
    <div className="bg-surface border border-border-custom rounded-lg p-4">
      <h3 className="text-xs text-muted uppercase tracking-widest mb-3">Market Heatmap</h3>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(72px,1fr))] gap-1">
        {items.map((item) => {
          // API may return numeric as string (asyncpg Decimal serialization)
          const pct = item.change_pct !== null ? Number(item.change_pct) : null
          return (
          <div
            key={item.ticker}
            className={`${cellColor(pct)} rounded p-1.5 flex flex-col items-center`}
            title={item.sector}
          >
            <span className="text-[10px] font-semibold text-white leading-none">{item.ticker}</span>
            <span className="text-[10px] text-muted mt-0.5">
              {pct !== null ? `${pct > 0 ? '+' : ''}${pct.toFixed(1)}%` : '—'}
            </span>
          </div>
          )
        })}
      </div>
    </div>
  )
}
