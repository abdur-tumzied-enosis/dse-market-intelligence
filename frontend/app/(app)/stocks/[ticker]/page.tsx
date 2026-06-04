import { notFound } from 'next/navigation'
import Link from 'next/link'
import { serverApi } from '@/lib/server-api'
import { getRating, type Rating } from '@/lib/rating'
import PriceChart from '@/components/stocks/PriceChart'
import HealthGauge from '@/components/stocks/HealthGauge'
import RatingBadge from '@/components/stocks/RatingBadge'
import FundamentalsCharts from '@/components/stocks/FundamentalsCharts'
import LivePrice from '@/components/stocks/LivePrice'
import Announcements from '@/components/stocks/Announcements'
import PaywallOverlay from '@/components/ui/PaywallOverlay'

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
function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
}

const RATING_COLOR: Record<Rating, string> = {
  STRONG_BUY: '#00d4a4',
  BUY: '#00d4a4',
  HOLD: '#f5c842',
  SELL: '#ff4d6a',
  STRONG_SELL: '#ff4d6a',
  'N/A': '#6b6b80',
}

// ─── Layout primitives (server-safe) ──────────────────────────────────────────

function Reveal({
  delay = 0,
  className = '',
  children,
}: {
  delay?: number
  className?: string
  children: React.ReactNode
}) {
  return (
    <div
      className={`animate-in fade-in-0 slide-in-from-bottom-3 duration-500 ${className}`}
      style={{ animationDelay: `${delay}ms`, animationFillMode: 'backwards' }}
    >
      {children}
    </div>
  )
}

function Panel({
  title,
  accent = '#4d9eff',
  className = '',
  children,
}: {
  title?: string
  accent?: string
  className?: string
  children: React.ReactNode
}) {
  return (
    <div
      className={`rounded-xl border border-[#2a2a3a] bg-gradient-to-b from-[#13131c] to-[#0f0f17] shadow-[0_1px_0_rgba(255,255,255,0.03)_inset,0_8px_24px_-12px_rgba(0,0,0,0.6)] ${className}`}
    >
      {title && (
        <div className="flex items-center gap-2 px-4 pt-3.5 pb-2">
          <span className="h-3 w-0.5 rounded-full" style={{ background: accent }} />
          <p className="text-[10px] font-mono uppercase tracking-[0.18em] text-[#6b6b80]">
            {title}
          </p>
        </div>
      )}
      {children}
    </div>
  )
}

function MetricGroup({
  title,
  rows,
}: {
  title: string
  rows: { label: string; value: string; accent?: string }[]
}) {
  return (
    <div>
      <p className="text-[9px] font-mono uppercase tracking-[0.2em] text-[#56566a] mb-1.5">
        {title}
      </p>
      <div className="space-y-1.5">
        {rows.map(({ label, value, accent }) => (
          <div key={label} className="flex justify-between items-baseline gap-2">
            <span className="text-[11px] text-[#8a8a9e] font-mono shrink-0">{label}</span>
            <span
              className="text-[12px] font-mono font-medium tabular-nums text-right"
              style={{ color: accent ?? '#e8e8f0' }}
            >
              {value}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default async function StockDetailPage({
  params,
}: {
  params: Promise<{ ticker: string }>
}) {
  const { ticker } = await params

  const [detailResult, fundsResult, annResult] = await Promise.allSettled([
    serverApi.stocks.detail(ticker),
    serverApi.stocks.fundamentals(ticker),
    serverApi.stocks.announcements(ticker),
  ])

  if (detailResult.status === 'rejected') notFound()

  const { company, latest_price, health_score, fundamentals: lf } = detailResult.value
  const fundsData = fundsResult.status === 'fulfilled' ? fundsResult.value : null
  const annData = annResult.status === 'fulfilled' ? annResult.value : null

  const scoreNum = health_score?.health_score != null ? Number(health_score.health_score) : null
  const rating = getRating(scoreNum)
  const ratingColor = RATING_COLOR[rating]

  // Ownership split (sponsor vs public), normalised for the bar.
  const sponsor = lf?.sponsor_pct != null ? Number(lf.sponsor_pct) : null
  const publicPct = lf?.public_pct != null ? Number(lf.public_pct) : null
  const ownTotal = (sponsor ?? 0) + (publicPct ?? 0)
  const hasOwnership = ownTotal > 0

  const valuation = [
    { label: 'P/E Ratio', value: fmt(lf?.pe, 1) },
    { label: 'Market Cap', value: fmtCap(company.market_cap_bdt) },
    { label: 'Volume', value: fmtVol(latest_price?.volume) },
  ]
  const perShare = [
    { label: 'EPS', value: fmtBDT(lf?.eps) },
    { label: 'NAV', value: fmtBDT(lf?.nav) },
  ]
  const dividends = [
    { label: 'Cash Div', value: fmtPct(lf?.cash_div_pct), accent: '#f5c842' },
    { label: 'Stock Div', value: fmtPct(lf?.stock_div_pct), accent: '#4d9eff' },
  ]
  const profile = [
    { label: 'Sector', value: company.sector },
    { label: 'Category', value: company.category ?? '—' },
    { label: 'ISIN', value: company.isin ?? '—' },
    { label: 'Listed', value: fmtDate(company.listing_date) },
    { label: 'Fiscal Yr', value: lf?.fiscal_year != null ? String(lf.fiscal_year) : '—' },
  ]

  return (
    <div className="space-y-4 mx-auto" style={{ maxWidth: '1640px' }}>

      {/* Breadcrumb */}
      <nav className="flex items-center gap-1.5 text-[11px] font-mono text-[#6b6b80]">
        <Link href="/stocks" className="hover:text-[#4d9eff] transition-colors">Stocks</Link>
        <span>/</span>
        <span className="text-[#e8e8f0]">{company.ticker}</span>
      </nav>

      {/* ── Command header band ──────────────────────────────────────────────── */}
      <Reveal>
        <header className="relative overflow-hidden rounded-2xl border border-[#2a2a3a] bg-gradient-to-br from-[#16161f] via-[#111118] to-[#0c0c12]">
          {/* rating-coloured accent strip */}
          <span className="absolute left-0 inset-y-0 w-1" style={{ background: ratingColor }} />
          {/* soft directional glow */}
          <div
            className="pointer-events-none absolute -top-24 -right-16 h-64 w-64 rounded-full opacity-[0.07] blur-3xl"
            style={{ background: ratingColor }}
          />
          <div className="relative px-6 py-5 flex flex-wrap items-start justify-between gap-x-8 gap-y-5">
            {/* Identity */}
            <div className="min-w-0">
              <div className="flex flex-wrap items-baseline gap-3 mb-2">
                <span className="text-[32px] font-mono font-bold tracking-tight text-white leading-none">
                  {company.ticker}
                </span>
                {/* Signal pill */}
                <span
                  className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px] font-mono font-semibold uppercase tracking-wider"
                  style={{
                    color: ratingColor,
                    backgroundColor: `${ratingColor}1a`,
                    border: `1px solid ${ratingColor}44`,
                  }}
                >
                  <span className="h-1.5 w-1.5 rounded-full" style={{ background: ratingColor }} />
                  {rating.replace('_', ' ')}
                  {scoreNum != null && (
                    <span className="tabular-nums opacity-80">· {Math.round(scoreNum)}</span>
                  )}
                </span>
              </div>
              <div className="text-[15px] text-[#d8d8e4] mb-2 max-w-[420px] truncate">
                {company.name}
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-[10px] font-mono bg-[#1a1a24] text-[#9b9bb0] px-2 py-0.5 rounded border border-[#2a2a3a]">
                  {company.sector}
                </span>
                {company.category && (
                  <span className="text-[10px] font-mono bg-[#1a1a24] text-[#9b9bb0] px-2 py-0.5 rounded border border-[#2a2a3a]">
                    {company.category}
                  </span>
                )}
                <span
                  className={`text-[10px] font-mono px-2 py-0.5 rounded border ${
                    company.is_active
                      ? 'text-[#00d4a4] border-[#00d4a4]/30 bg-[#00d4a4]/10'
                      : 'text-[#6b6b80] border-[#2a2a3a] bg-[#1a1a24]'
                  }`}
                >
                  {company.is_active ? 'Active' : 'Inactive'}
                </span>
                {company.isin && (
                  <span className="text-[10px] font-mono text-[#6b6b80]">{company.isin}</span>
                )}
              </div>
            </div>

            {/* Live price + OHLV + day range — client component polls every 2 min */}
            <LivePrice ticker={company.ticker} initial={latest_price} />
          </div>
        </header>
      </Reveal>

      {/* ── Main grid: chart + sidebar ───────────────────────────────────────── */}
      <div className="grid gap-4" style={{ gridTemplateColumns: 'minmax(0,1fr) 320px' }}>

        {/* Chart */}
        <Reveal delay={80}>
          <Panel className="h-[560px] flex flex-col" title="Price · Volume" accent="#4d9eff">
            <div className="flex-1 min-h-0 px-4 pb-4">
              <PriceChart ticker={company.ticker} />
            </div>
          </Panel>
        </Reveal>

        {/* Sidebar */}
        <div className="space-y-4">

          {/* Signal — gauge + rating + breakdown unified */}
          <Reveal delay={140}>
            <Panel title="Signal" accent={ratingColor}>
              <div className="px-4 pb-4">
                <HealthGauge score={scoreNum} />
                <div className="flex justify-center -mt-1 mb-1">
                  <RatingBadge score={scoreNum} />
                </div>
                {health_score?.scored_at && (
                  <p className="text-[9px] font-mono text-[#6b6b80] text-center mb-3">
                    scored {fmtDate(health_score.scored_at)}
                  </p>
                )}
                {health_score && (
                  <div className="space-y-2.5 pt-3 border-t border-[#2a2a3a]">
                    {[
                      { label: 'Fundamental', value: health_score.fundamental_score },
                      { label: 'Momentum', value: health_score.momentum_score },
                    ].map(({ label, value }) => {
                      const n = Number(value ?? 0)
                      const pct = Math.min(100, Math.max(0, n))
                      return (
                        <div key={label}>
                          <div className="flex justify-between text-[10px] font-mono mb-1">
                            <span className="text-[#8a8a9e]">{label}</span>
                            <span className="text-[#e8e8f0] tabular-nums">
                              {value != null ? n.toFixed(0) : '—'}
                            </span>
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
            </Panel>
          </Reveal>

          {/* Key metrics — grouped */}
          <Reveal delay={200}>
            <Panel title="Key Metrics" accent="#00d4a4">
              <div className="px-4 pb-4 space-y-3.5">
                <MetricGroup title="Valuation" rows={valuation} />
                <div className="border-t border-[#1f1f2b]" />
                <MetricGroup title="Per Share" rows={perShare} />
                <div className="border-t border-[#1f1f2b]" />
                <MetricGroup title="Dividends" rows={dividends} />
              </div>
            </Panel>
          </Reveal>

          {/* Ownership split */}
          {hasOwnership && (
            <Reveal delay={250}>
              <Panel title="Ownership" accent="#a78bfa">
                <div className="px-4 pb-4">
                  <div className="flex h-2.5 rounded-full overflow-hidden bg-[#1a1a24]">
                    {sponsor != null && (
                      <div
                        className="h-full"
                        style={{ width: `${(sponsor / ownTotal) * 100}%`, background: '#a78bfa' }}
                      />
                    )}
                    {publicPct != null && (
                      <div
                        className="h-full"
                        style={{ width: `${(publicPct / ownTotal) * 100}%`, background: '#4d9eff' }}
                      />
                    )}
                  </div>
                  <div className="flex justify-between mt-2 text-[10px] font-mono">
                    <span className="text-[#a78bfa]">
                      Sponsor <span className="tabular-nums">{fmtPct(sponsor)}</span>
                    </span>
                    <span className="text-[#4d9eff]">
                      Public <span className="tabular-nums">{fmtPct(publicPct)}</span>
                    </span>
                  </div>
                </div>
              </Panel>
            </Reveal>
          )}

          {/* Company profile */}
          <Reveal delay={300}>
            <Panel title="Profile" accent="#6b6b80">
              <div className="px-4 pb-4 space-y-1.5">
                {profile.map(({ label, value }) => (
                  <div key={label} className="flex justify-between items-baseline gap-2">
                    <span className="text-[11px] text-[#8a8a9e] font-mono shrink-0">{label}</span>
                    <span className="text-[11px] font-mono text-[#d8d8e4] text-right truncate">
                      {value}
                    </span>
                  </div>
                ))}
              </div>
            </Panel>
          </Reveal>
        </div>
      </div>

      {/* ── Fundamentals + Announcements ─────────────────────────────────────── */}
      <div className="grid gap-4" style={{ gridTemplateColumns: 'minmax(0,1fr) 320px' }}>
        {fundsData && fundsData.items.length > 0 ? (
          <Reveal delay={360}>
            <Panel title="Historical Fundamentals" accent="#00d4a4">
              <div className="p-4 pt-1">
                {fundsData.is_truncated ? (
                  <PaywallOverlay feature="Unlock 10 years of fundamentals with Pro">
                    <FundamentalsCharts items={fundsData.items} />
                  </PaywallOverlay>
                ) : (
                  <FundamentalsCharts items={fundsData.items} />
                )}
              </div>
            </Panel>
          </Reveal>
        ) : (
          <div />
        )}

        {/* Announcements */}
        <Reveal delay={400}>
          <Panel title="Announcements" accent="#f5c842" className="h-full">
            <div className="px-4 pb-3 max-h-[360px] overflow-y-auto">
              <Announcements items={annData?.items ?? []} />
            </div>
          </Panel>
        </Reveal>
      </div>

      {/* Disclaimer */}
      <p className="text-[10px] font-mono text-[#6b6b80] text-center py-2">
        Data for informational purposes only. Not investment advice. Past performance does not guarantee future results.
      </p>
    </div>
  )
}
