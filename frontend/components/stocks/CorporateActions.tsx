import type { CorporateActionRow } from '@/lib/types'

interface YearActions {
  year: number
  cash: number | null
  stock: number | null
  rights: string | null
}

function pivot(actions: CorporateActionRow[]): YearActions[] {
  const byYear = new Map<number, YearActions>()
  for (const a of actions) {
    let row = byYear.get(a.fiscal_year)
    if (!row) {
      row = { year: a.fiscal_year, cash: null, stock: null, rights: null }
      byYear.set(a.fiscal_year, row)
    }
    if (a.action_type === 'cash_div') row.cash = a.value_pct != null ? Number(a.value_pct) : null
    if (a.action_type === 'stock_div') row.stock = a.value_pct != null ? Number(a.value_pct) : null
    if (a.action_type === 'right_issue') row.rights = a.ratio_text ?? 'rights'
  }
  return [...byYear.values()].sort((a, b) => b.year - a.year)
}

/** Consecutive dividend-paying years (cash or stock) ending at the most recent action year. */
function dividendStreak(years: YearActions[]): number {
  let streak = 0
  let expected: number | null = null
  for (const y of years) {
    if (y.cash == null && y.stock == null) break
    if (expected != null && y.year !== expected) break
    streak += 1
    expected = y.year - 1
  }
  return streak
}

function Chip({ text, color }: { text: string; color: string }) {
  return (
    <span
      className="inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-mono tabular-nums"
      style={{ color, backgroundColor: `${color}14`, border: `1px solid ${color}2e` }}
    >
      {text}
    </span>
  )
}

interface Props {
  actions: CorporateActionRow[]
}

export default function CorporateActions({ actions }: Props) {
  const years = pivot(actions)
  if (!years.length) {
    return <p className="text-xs text-[#6b6b80] font-mono">No corporate-action history yet</p>
  }

  const streak = dividendStreak(years)
  const latestYear = years[0].year
  const rights10y = years.filter(y => y.rights != null && y.year > latestYear - 10).length

  return (
    <div className="space-y-3">
      {/* Analyst summary */}
      <div className="flex flex-wrap gap-1.5">
        <Chip
          text={`Dividend streak · ${streak} yr${streak === 1 ? '' : 's'}`}
          color={streak >= 5 ? '#00d4a4' : streak >= 1 ? '#f5c842' : '#ff4d6a'}
        />
        <Chip
          text={
            rights10y > 0
              ? `Dilution · ${rights10y} rights issue${rights10y === 1 ? '' : 's'} in 10y`
              : 'No rights issues in 10y'
          }
          color={rights10y > 0 ? '#ff4d6a' : '#00d4a4'}
        />
      </div>

      {/* Year-by-year history */}
      <div className="max-h-[260px] overflow-y-auto pr-1">
        <table className="w-full border-collapse">
          <thead className="sticky top-0 bg-[#13131c]">
            <tr className="text-left text-[9px] font-mono uppercase tracking-[0.15em] text-[#56566a]">
              <th className="py-1.5 pr-2 font-normal">Year</th>
              <th className="py-1.5 pr-2 font-normal">Cash</th>
              <th className="py-1.5 pr-2 font-normal">Stock</th>
              <th className="py-1.5 font-normal">Rights</th>
            </tr>
          </thead>
          <tbody>
            {years.map(y => (
              <tr key={y.year} className="border-t border-[#1f1f2c]">
                <td className="py-1.5 pr-2 text-[11px] font-mono text-[#d8d8e4] tabular-nums">
                  {y.year}
                </td>
                <td className="py-1.5 pr-2">
                  {y.cash != null ? (
                    <Chip text={`${y.cash}%`} color="#f5c842" />
                  ) : (
                    <span className="text-[10px] font-mono text-[#3a3a4c]">—</span>
                  )}
                </td>
                <td className="py-1.5 pr-2">
                  {y.stock != null ? (
                    <Chip text={`${y.stock}%B`} color="#4d9eff" />
                  ) : (
                    <span className="text-[10px] font-mono text-[#3a3a4c]">—</span>
                  )}
                </td>
                <td className="py-1.5">
                  {y.rights != null ? (
                    <Chip text={y.rights} color="#ff4d6a" />
                  ) : (
                    <span className="text-[10px] font-mono text-[#3a3a4c]">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
