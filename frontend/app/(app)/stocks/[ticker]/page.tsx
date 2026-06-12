import { notFound } from 'next/navigation'
import Link from 'next/link'
import { serverApi } from '@/lib/server-api'
import { getRating, type Rating } from '@/lib/rating'
import PriceChart from '@/components/stocks/PriceChart'
import FundamentalsCharts from '@/components/stocks/FundamentalsCharts'
import LivePrice from '@/components/stocks/LivePrice'
import Announcements from '@/components/stocks/Announcements'
import PaywallOverlay from '@/components/ui/PaywallOverlay'
import KeyMetricsStrip from '@/components/stocks/KeyMetricsStrip'
import AiAnalysis from '@/components/stocks/AiAnalysis'
import ShareholdingTrend from '@/components/stocks/ShareholdingTrend'
import CorporateActions from '@/components/stocks/CorporateActions'
import QuarterlyEarnings from '@/components/stocks/QuarterlyEarnings'
import CompanyProfile from '@/components/stocks/CompanyProfile'

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

// ─── Page ─────────────────────────────────────────────────────────────────────

export default async function StockDetailPage({
  params,
}: {
  params: Promise<{ ticker: string }>
}) {
  const { ticker } = await params

  const [detailResult, fundsResult, annResult, analyzeResult, trackResult] = await Promise.allSettled([
    serverApi.stocks.detail(ticker),
    serverApi.stocks.fundamentals(ticker),
    serverApi.stocks.announcements(ticker),
    // NOTE: analyze returns more than predictions+narrative; only those are used here.
    // When real ML inference lands, consider a slimmer endpoint or client-side lazy fetch to avoid blocking SSR.
    serverApi.stocks.analyze(ticker),
    serverApi.stocks.trackRecord(ticker),
  ])

  if (detailResult.status === 'rejected') notFound()

  const { company, latest_price, health_score, fundamentals: lf } = detailResult.value
  const fundsData = fundsResult.status === 'fulfilled' ? fundsResult.value : null
  const annData = annResult.status === 'fulfilled' ? annResult.value : null
  const analyzeData = analyzeResult.status === 'fulfilled' ? analyzeResult.value : null
  const trackData = trackResult.status === 'fulfilled' ? trackResult.value : null

  const hasShareholding = (trackData?.shareholding.length ?? 0) > 0
  const hasActions = (trackData?.actions.length ?? 0) > 0
  const hasQuarterly = (trackData?.quarterly.length ?? 0) > 0

  const scoreNum = health_score?.health_score != null ? Number(health_score.health_score) : null
  const rating = getRating(scoreNum)
  const ratingColor = RATING_COLOR[rating]

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
  const metricGroups = [
    { title: 'Valuation', rows: valuation },
    { title: 'Per Share', rows: perShare },
    { title: 'Dividends', rows: dividends },
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

      {/* ── Chart (full width) ─────────────────────────────────────────────── */}
      <Reveal delay={80}>
        <Panel className="h-[560px] flex flex-col" title="Price · Volume" accent="#4d9eff">
          <div className="flex-1 min-h-0 px-4 pb-4">
            <PriceChart ticker={company.ticker} />
          </div>
        </Panel>
      </Reveal>

      {/* ── Key metrics + signal (highlighted) ─────────────────────────────── */}
      <Reveal delay={140}>
        <Panel title="Key Metrics" accent="#00d4a4">
          <KeyMetricsStrip
            groups={metricGroups}
            score={scoreNum}
            fundamentalScore={health_score?.fundamental_score ?? null}
            momentumScore={health_score?.momentum_score ?? null}
          />
          {health_score?.scored_at && (
            <p className="text-[9px] font-mono text-[#6b6b80] px-4 pb-3">
              scored {fmtDate(health_score.scored_at)}
            </p>
          )}
        </Panel>
      </Reveal>

      {/* ── Company info: fact tiles + risk & leverage ─────────────────────── */}
      <Reveal delay={200}>
        <Panel title="Company Info" accent="#6b6b80">
          <CompanyProfile company={company} latest={lf} showOwnership={!hasShareholding} />
        </Panel>
      </Reveal>

      {/* ── Ownership flow + corporate actions (track record) ──────────────── */}
      {(hasShareholding || hasActions) && (
        <Reveal delay={230}>
          <div className={`grid gap-4 ${hasShareholding && hasActions ? 'lg:grid-cols-2' : ''}`}>
            {hasShareholding && (
              <Panel title="Ownership Flow" accent="#a78bfa">
                <div className="px-4 pb-4">
                  <ShareholdingTrend snapshots={trackData!.shareholding} />
                </div>
              </Panel>
            )}
            {hasActions && (
              <Panel title="Corporate Actions" accent="#f5c842">
                <div className="px-4 pb-4">
                  {trackData!.is_truncated ? (
                    <PaywallOverlay feature="Unlock full corporate-action history with Pro">
                      <CorporateActions actions={trackData!.actions} />
                    </PaywallOverlay>
                  ) : (
                    <CorporateActions actions={trackData!.actions} />
                  )}
                </div>
              </Panel>
            )}
          </div>
        </Reveal>
      )}

      {/* ── Quarterly earnings momentum ─────────────────────────────────────── */}
      {hasQuarterly && (
        <Reveal delay={250}>
          <Panel title="Quarterly Earnings" accent="#4d9eff">
            <div className="px-4 pb-4">
              <QuarterlyEarnings quarterly={trackData!.quarterly} />
            </div>
          </Panel>
        </Reveal>
      )}

      {/* ── Historical fundamentals (highlighted, full width) ──────────────── */}
      {fundsData && fundsData.items.length > 0 && (
        <Reveal delay={260}>
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
      )}

      {/* ── AI analysis (closing verdict) ──────────────────────────────────── */}
      <Reveal delay={320}>
        <Panel title="AI Analysis" accent="#a78bfa">
          <AiAnalysis
            predictions={analyzeData?.predictions ?? []}
            narrative={analyzeData?.narrative ?? null}
          />
        </Panel>
      </Reveal>

      {/* ── Announcements (full width) ─────────────────────────────────────── */}
      <Reveal delay={380}>
        <Panel title="Announcements" accent="#f5c842">
          <div className="px-4 pb-3 max-h-[360px] overflow-y-auto">
            <Announcements items={annData?.items ?? []} />
          </div>
        </Panel>
      </Reveal>

      {/* Disclaimer */}
      <p className="text-[10px] font-mono text-[#6b6b80] text-center py-2">
        Data for informational purposes only. Not investment advice. Past performance does not guarantee future results.
      </p>
    </div>
  )
}
