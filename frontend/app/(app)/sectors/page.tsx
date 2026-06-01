import { serverApi } from '@/lib/server-api'
import SectorHeatmap from '@/components/sectors/SectorHeatmap'
import type { SectorRow } from '@/lib/types'

function fmtCap(bdt: number | null): string {
  if (!bdt) return '—'
  const cr = Number(bdt) / 10_000_000
  if (cr >= 1000) return `৳${(cr / 1000).toFixed(1)}K Cr`
  return `৳${cr.toFixed(0)} Cr`
}

function fmtPe(pe: number | null): string {
  return pe == null ? '—' : Number(pe).toFixed(1)
}

export default async function SectorsPage() {
  let sectors: SectorRow[] = []
  let error: string | null = null
  try {
    sectors = await serverApi.sectors.list()
  } catch {
    error = 'Failed to load sectors'
  }

  // Largest market cap first for the table.
  const rows = [...sectors].sort(
    (a, b) => Number(b.market_cap_bdt ?? 0) - Number(a.market_cap_bdt ?? 0)
  )

  return (
    <div className="space-y-5 max-w-[1400px]">
      <div>
        <h1 className="text-2xl font-bold text-white tracking-tight">Sectors</h1>
        <p className="text-[#6b6b80] text-sm font-mono mt-0.5">
          {error ? '—' : `${rows.length} sectors`}
        </p>
      </div>

      {error ? (
        <div className="bg-[#111118] border border-[#2a2a3a] rounded-xl py-16 text-center text-[#ff4d6a] font-mono text-sm">
          {error}
        </div>
      ) : (
        <>
          <SectorHeatmap sectors={rows} />

          <div className="bg-[#111118] border border-[#2a2a3a] rounded-xl overflow-hidden">
            <table className="w-full">
              <thead>
                <tr className="border-b border-[#2a2a3a]">
                  <th className="py-3 pl-4 pr-2 text-left text-[10px] font-mono uppercase tracking-wider text-[#6b6b80]">Sector</th>
                  <th className="py-3 px-2 text-right text-[10px] font-mono uppercase tracking-wider text-[#6b6b80]">P/E</th>
                  <th className="py-3 px-2 text-right text-[10px] font-mono uppercase tracking-wider text-[#6b6b80]">Change</th>
                  <th className="py-3 pl-2 pr-4 text-right text-[10px] font-mono uppercase tracking-wider text-[#6b6b80]">Market Cap</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(s => {
                  const chg = s.change_pct == null ? null : Number(s.change_pct)
                  return (
                    <tr key={s.sector} className="border-b border-[#1a1a24] hover:bg-[#0f0f18] transition-colors">
                      <td className="py-3 pl-4 pr-2 text-sm text-[#e8e8f0]">{s.sector}</td>
                      <td className="py-3 px-2 text-right text-sm font-mono tabular-nums text-[#e8e8f0]">{fmtPe(s.pe)}</td>
                      <td className={`py-3 px-2 text-right text-sm font-mono tabular-nums ${chg == null ? 'text-[#6b6b80]' : chg >= 0 ? 'text-[#00d4a4]' : 'text-[#ff4d6a]'}`}>
                        {chg == null ? '—' : `${chg > 0 ? '+' : ''}${chg.toFixed(2)}%`}
                      </td>
                      <td className="py-3 pl-2 pr-4 text-right text-sm font-mono tabular-nums text-[#e8e8f0]">{fmtCap(s.market_cap_bdt)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          <p className="text-[10px] font-mono text-[#6b6b80] text-center py-2">
            Sector P/E sourced from DSE. For informational purposes only. Not investment advice.
          </p>
        </>
      )}
    </div>
  )
}
