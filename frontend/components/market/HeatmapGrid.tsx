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

function groupBySector(items: HeatmapItem[]): [string, HeatmapItem[]][] {
  const map = new Map<string, HeatmapItem[]>()
  for (const item of items) {
    const sector = item.sector ?? 'Other'
    const group = map.get(sector) ?? []
    group.push(item)
    map.set(sector, group)
  }
  return Array.from(map.entries()).sort(([a], [b]) => a.localeCompare(b))
}

function HeatmapCell({ item }: { item: HeatmapItem }) {
  const pct = item.change_pct !== null ? Number(item.change_pct) : null
  return (
    <div
      className={`${cellColor(pct)} rounded p-1.5 flex flex-col items-center`}
      title={`${item.ticker} — ${item.sector}`}
    >
      <span className="text-[10px] font-semibold text-white leading-none">{item.ticker}</span>
      <span className="text-[10px] text-muted mt-0.5">
        {pct !== null ? `${pct > 0 ? '+' : ''}${pct.toFixed(1)}%` : '—'}
      </span>
    </div>
  )
}

export default function HeatmapGrid({ items }: HeatmapGridProps) {
  const sectors = groupBySector(items)

  return (
    <div className="bg-surface border border-border-custom rounded-lg p-4">
      <h3 className="text-xs text-muted uppercase tracking-widest mb-4">Market Heatmap</h3>
      {sectors.length === 0 && (
        <p className="text-muted text-xs">No data available.</p>
      )}
      <div className="space-y-5">
        {sectors.map(([sector, sectorItems]) => (
          <div key={sector}>
            <p className="text-[10px] text-muted uppercase tracking-widest mb-1.5 border-b border-border-custom pb-1">
              {sector}
            </p>
            <div className="grid grid-cols-[repeat(auto-fill,minmax(72px,1fr))] gap-1">
              {sectorItems.map((item) => (
                <HeatmapCell key={item.ticker} item={item} />
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
