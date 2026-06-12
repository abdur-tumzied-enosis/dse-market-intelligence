import type { QuarterlyEpsRow } from '@/lib/types'

function num(v: number | null | undefined): number | null {
  if (v == null) return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

interface Props {
  quarterly: QuarterlyEpsRow[]
}

export default function QuarterlyEarnings({ quarterly }: Props) {
  const sorted = [...quarterly].sort(
    (a, b) => b.fiscal_year - a.fiscal_year || b.quarter - a.quarter,
  )
  if (!sorted.length) {
    return <p className="text-xs text-[#6b6b80] font-mono">No quarterly earnings yet</p>
  }

  // YoY comparison: same quarter, previous fiscal year
  const epsOf = (fy: number, q: number): number | null => {
    const row = sorted.find(r => r.fiscal_year === fy && r.quarter === q)
    return row ? num(row.eps_basic) : null
  }

  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
      {sorted.slice(0, 8).map(r => {
        const eps = num(r.eps_basic)
        const price = num(r.period_end_price)
        const prev = epsOf(r.fiscal_year - 1, r.quarter)
        const yoy = eps != null && prev != null && prev !== 0
          ? ((eps - prev) / Math.abs(prev)) * 100
          : null
        const epsColor = eps == null ? '#6b6b80' : eps >= 0 ? '#00d4a4' : '#ff4d6a'
        return (
          <div
            key={`${r.fiscal_year}-${r.quarter}`}
            className="rounded-lg border border-[#2a2a3a] bg-[#111118] px-3 py-2.5"
          >
            <p className="text-[9px] font-mono uppercase tracking-[0.15em] text-[#56566a] mb-1">
              FY{r.fiscal_year} · Q{r.quarter}
            </p>
            <div className="flex items-baseline gap-1.5">
              <span className="text-[18px] font-mono font-semibold tabular-nums" style={{ color: epsColor }}>
                {eps != null ? eps.toFixed(2) : '—'}
              </span>
              <span className="text-[9px] font-mono text-[#6b6b80]">EPS</span>
            </div>
            <div className="mt-1 flex items-center justify-between text-[9px] font-mono">
              <span className="text-[#6b6b80] tabular-nums">
                {price != null ? `@ ৳${price.toFixed(2)}` : ''}
              </span>
              {yoy != null && (
                <span className="tabular-nums" style={{ color: yoy >= 0 ? '#00d4a4' : '#ff4d6a' }}>
                  {yoy >= 0 ? '+' : ''}{yoy.toFixed(0)}% YoY
                </span>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}
