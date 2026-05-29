import { notFound } from 'next/navigation'
import Link from 'next/link'
import { serverApi } from '@/lib/server-api'
import PriceChart from '@/components/stocks/PriceChart'
import HealthGauge from '@/components/stocks/HealthGauge'
import RatingBadge from '@/components/stocks/RatingBadge'
import FundamentalsCharts from '@/components/stocks/FundamentalsCharts'

// ─── Formatting helpers ───────────────────────────────────────────────────────

function fmt(n: number | null | undefined, d = 2): string {
  if (n == null) return '—'
  return Number(n).toFixed(d)
}
function fmtBDT(n: number | null | undefined): string {
  if (n == null) return '—'
  return `৳${Number(n).toFixed(2)}`
}
function fmtCap(bdt: number | null | undefined): string {
  if (!bdt) return '—'
  const cr = Number(bdt) / 10_000_000
  if (cr >= 1000) return `৳${(cr / 1000).toFixed(1)}K Cr`
  return `৳${cr.toFixed(0)} Cr`
}
function fmtVol(v: number | null | undefined): string {
  const n = Number(v)
  if (!n) return '—'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}
function fmtPct(n: number | null | undefined): string {
  if (n == null) return '—'
  return `${Number(n)}%`
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default async function StockDetailPage({
  params,
}: {
  params: Promise<{ ticker: string }>
}) {
  const { ticker } = await params

  const [detailResult, fundsResult] = await Promise.allSettled([
    serverApi.stocks.detail(ticker),
    serverApi.stocks.fundamentals(ticker),
  ])

  if (detailResult.status === 'rejected') notFound()

  const { company, latest_price, health_score, fundamentals: lf } = detailResult.value
  const fundsData = fundsResult.status === 'fulfilled' ? fundsResult.value : null

  const price = Number(latest_price?.close)
  const changePct = Number(latest_price?.change_pct ?? 0)
  const isUp = changePct >= 0

  const scoreNum = health_score?.health_score != null ? Number(health_score.health_score) : null

  const metrics = [
    { label: 'P/E Ratio',   value: fmt(lf?.pe, 1) },
    { label: 'EPS',         value: fmtBDT(lf?.eps) },
    { label: 'NAV',         value: fmtBDT(lf?.nav) },
    { label: 'Cash Div',    value: fmtPct(lf?.cash_div_pct) },
    { label: 'Stock Div',   value: fmtPct(lf?.stock_div_pct) },
    { label: 'Market Cap',  value: fmtCap(company.market_cap_bdt) },
    { label: 'Volume',      value: fmtVol(latest_price?.volume) },
    { label: 'Sponsor %',   value: fmtPct(lf?.sponsor_pct) },
    { label: 'Public %',    value: fmtPct(lf?.public_pct) },
    { label: 'Fiscal Year', value: lf?.fiscal_year != null ? String(lf.fiscal_year) : '—' },
  ] as const

  return (
    <div className="space-y-4" style={{ maxWidth: '1640px' }}>

      {/* Breadcrumb */}
      <nav className="flex items-center gap-1.5 text-[11px] font-mono text-[#6b6b80]">
        <Link href="/stocks" className="hover:text-[#4d9eff] transition-colors">Stocks</Link>
        <span>/</span>
        <span className="text-[#e8e8f0]">{company.ticker}</span>
      </nav>

      {/* ── Header card ──────────────────────────────────────────────────────── */}
      <div className="bg-[#111118] border border-[#2a2a3a] rounded-xl px-6 py-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            {/* Symbol + name */}
            <div className="flex flex-wrap items-baseline gap-3 mb-1">
              <span className="text-[28px] font-mono font-bold tracking-tight text-white">
                {company.ticker}
              </span>
              <span className="text-[#6b6b80] text-sm">{company.name}</span>
              <span className="text-[10px] font-mono bg-[#1a1a24] text-[#6b6b80] px-2 py-0.5 rounded border border-[#2a2a3a]">
                {company.sector}
              </span>
              {company.category && (
                <span className="text-[10px] font-mono bg-[#1a1a24] text-[#6b6b80] px-2 py-0.5 rounded border border-[#2a2a3a]">
                  {company.category}
                </span>
              )}
            </div>
            {/* Price */}
            <div className="flex items-baseline gap-3 mt-2">
              <span className="text-[42px] font-mono font-bold tabular-nums leading-none text-white">
                {latest_price ? `৳${price.toFixed(2)}` : '—'}
              </span>
              {latest_price && (
                <span
                  className="text-[18px] font-mono font-semibold tabular-nums"
                  style={{ color: isUp ? '#00d4a4' : '#ff4d6a' }}
                >
                  {isUp ? '+' : ''}{changePct.toFixed(2)}%
                </span>
              )}
            </div>
          </div>

          {/* OHLV strip */}
          {latest_price && (
            <div className="flex gap-5 self-end pb-1">
              {[
                { l: 'HIGH',   v: fmtBDT(latest_price.high),   c: '#00d4a4' },
                { l: 'LOW',    v: fmtBDT(latest_price.low),    c: '#ff4d6a' },
                { l: 'VOLUME', v: fmtVol(latest_price.volume), c: null },
                { l: 'VALUE',  v: fmtCap(latest_price.value_bdt != null ? Number(latest_price.value_bdt) * 10_000_000 : null), c: null },
              ].map(({ l, v, c }) => (
                <div key={l} className="text-center">
                  <div className="text-[9px] font-mono uppercase tracking-[0.2em] text-[#6b6b80] mb-0.5">{l}</div>
                  <div className="text-sm font-mono font-medium tabular-nums"
                    style={{ color: c ?? '#e8e8f0' }}>
                    {v}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* ── Main grid: chart + sidebar ───────────────────────────────────────── */}
      <div className="grid gap-4" style={{ gridTemplateColumns: '1fr 296px' }}>

        {/* Chart */}
        <div className="bg-[#111118] border border-[#2a2a3a] rounded-xl p-4 h-[536px]">
          <PriceChart ticker={company.ticker} />
        </div>

        {/* Sidebar */}
        <div className="space-y-3">

          {/* Health gauge + rating */}
          <div className="bg-[#111118] border border-[#2a2a3a] rounded-xl px-4 pb-4">
            <HealthGauge score={scoreNum} />
            <div className="flex justify-center mt-1">
              <RatingBadge score={scoreNum} />
            </div>
            {health_score?.scored_at && (
              <p className="text-[9px] font-mono text-[#6b6b80] text-center mt-2">
                scored {new Date(health_score.scored_at).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })}
              </p>
            )}
          </div>

          {/* Key metrics */}
          <div className="bg-[#111118] border border-[#2a2a3a] rounded-xl p-4">
            <p className="text-[10px] font-mono uppercase tracking-[0.18em] text-[#6b6b80] mb-3">
              Key Metrics
            </p>
            <div className="space-y-2.5">
              {metrics.map(({ label, value }) => (
                <div key={label} className="flex justify-between items-baseline gap-2">
                  <span className="text-[11px] text-[#6b6b80] font-mono shrink-0">{label}</span>
                  <span className="text-[12px] font-mono font-medium tabular-nums text-[#e8e8f0] text-right">
                    {value}
                  </span>
                </div>
              ))}
            </div>
          </div>

          {/* Score breakdown */}
          {health_score && (
            <div className="bg-[#111118] border border-[#2a2a3a] rounded-xl p-4">
              <p className="text-[10px] font-mono uppercase tracking-[0.18em] text-[#6b6b80] mb-3">
                Score Breakdown
              </p>
              {[
                { label: 'Fundamental', value: health_score.fundamental_score },
                { label: 'Momentum',    value: health_score.momentum_score },
              ].map(({ label, value }) => {
                const n = Number(value ?? 0)
                const pct = Math.min(100, Math.max(0, n))
                return (
                  <div key={label} className="mb-2.5 last:mb-0">
                    <div className="flex justify-between text-[10px] font-mono mb-1">
                      <span className="text-[#6b6b80]">{label}</span>
                      <span className="text-[#e8e8f0]">{value != null ? n.toFixed(0) : '—'}</span>
                    </div>
                    <div className="h-1.5 rounded-full bg-[#1a1a24] overflow-hidden">
                      <div
                        className="h-full rounded-full transition-all duration-700"
                        style={{
                          width: `${pct}%`,
                          backgroundColor: pct >= 70 ? '#00d4a4' : pct >= 40 ? '#f5c842' : '#ff4d6a',
                        }}
                      />
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>

      {/* ── Fundamentals ─────────────────────────────────────────────────────── */}
      {fundsData && fundsData.items.length > 0 && (
        <div className="bg-[#111118] border border-[#2a2a3a] rounded-xl p-5">
          <p className="text-[10px] font-mono uppercase tracking-[0.18em] text-[#6b6b80] mb-4">
            Historical Fundamentals
          </p>
          <FundamentalsCharts items={fundsData.items} />
        </div>
      )}

      {/* Disclaimer */}
      <p className="text-[10px] font-mono text-[#6b6b80] text-center py-2">
        Data for informational purposes only. Not investment advice. Past performance does not guarantee future results.
      </p>
    </div>
  )
}
