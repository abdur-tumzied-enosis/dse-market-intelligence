import type { FundamentalDetail } from '@/lib/types'

// ─── Helpers ──────────────────────────────────────────────────────────────────

/** Descriptive word for the headline score — deliberately NOT buy/sell.
 *  Bands match the color thresholds + pillar bars (≥70 / ≥40 / <40). */
function scoreWord(n: number | null): string {
  if (n == null) return 'No data'
  if (n >= 70) return 'Strong'
  if (n >= 40) return 'Fair'
  return 'Weak'
}

/** Headline number color: green ≥70, gold ≥40, red <40. */
function headlineColor(n: number | null): string {
  if (n == null) return '#56566a'
  if (n >= 70) return '#00d4a4'
  if (n >= 40) return '#f5c842'
  return '#ff4d6a'
}

/** Bar fill color, matching the existing ScoreBar pattern in KeyMetricsStrip. */
function barColor(pct: number): string {
  return pct >= 70 ? '#00d4a4' : pct >= 40 ? '#f5c842' : '#ff4d6a'
}

// Fixed pillar order + Title Case labels + beginner tooltips.
// Keys are lowercase in detail.pillars. Score = percentile vs market peers (higher = better).
const PILLARS: { key: string; label: string; help: string }[] = [
  { key: 'growth', label: 'Growth', help: 'Is the company growing earnings, book value and profits over time?' },
  { key: 'value', label: 'Value', help: 'Is the stock cheap relative to its earnings, assets and sector peers?' },
  { key: 'quality', label: 'Quality', help: 'How stable and genuine are the profits — return on equity, consistency, earnings quality.' },
  { key: 'dividends', label: 'Dividends', help: 'Track record of paying regular cash dividends to shareholders.' },
  { key: 'safety', label: 'Safety', help: 'Financial risk: debt levels and share dilution. Higher = safer (less debt).' },
  { key: 'ownership', label: 'Ownership', help: 'Are institutions and foreign investors increasing their stake?' },
]

// ─── Pillar bar (reuses KeyMetricsStrip ScoreBar visual) ────────────────────────

function PillarBar({ label, value, help }: { label: string; value: number | null; help: string }) {
  const hasValue = value != null
  const n = Number(value ?? 0)
  const pct = Math.min(100, Math.max(0, n))
  return (
    <div>
      <div className="flex justify-between text-[10px] font-mono mb-1">
        <span
          className="text-[#8a8a9e] cursor-help decoration-dotted decoration-[#3a3a4a] underline underline-offset-2"
          title={help}
        >
          {label}
        </span>
        <span className="text-[#e8e8f0] tabular-nums">{hasValue ? n.toFixed(0) : '—'}</span>
      </div>
      <div className="h-1.5 rounded-full bg-[#1a1a24] overflow-hidden">
        {hasValue && (
          <div
            className="h-full rounded-full transition-[width] duration-700"
            style={{ width: `${pct}%`, backgroundColor: barColor(pct) }}
          />
        )}
      </div>
    </div>
  )
}

// ─── Driver line ────────────────────────────────────────────────────────────────

function DriverLine({ sentence, good }: { sentence: string; good: boolean }) {
  const color = good ? '#00d4a4' : '#ff4d6a'
  return (
    <div className="flex items-baseline gap-2">
      <span className="text-[9px] leading-none shrink-0" style={{ color }}>
        {good ? '▲' : '▼'}
      </span>
      <span className="text-[11px] font-mono text-[#8a8a9e] leading-snug">{sentence}</span>
    </div>
  )
}

// ─── Card ────────────────────────────────────────────────────────────────────────

export default function FundamentalScorecard({ detail }: { detail: FundamentalDetail | null }) {
  if (!detail) return null

  const health = detail.health_score != null ? Number(detail.health_score) : null
  const pillars = detail.pillars ?? {}
  const anyPillar = PILLARS.some(({ key }) => pillars[key] != null)

  // Truly nothing to show → render nothing.
  if (health == null && !anyPillar) return null

  const drivers = detail.drivers ?? []
  const strengths = drivers.filter((d) => d.polarity === 'good').slice(0, 3)
  const watch = drivers.filter((d) => d.polarity === 'bad').slice(0, 3)

  return (
    <div className="grid gap-6 px-4 pb-4 md:grid-cols-[200px_1fr]">
      {/* Headline */}
      <div className="md:border-r md:border-[#1f1f2b] md:pr-6">
        <div className="flex items-baseline gap-2">
          <span
            className="text-[44px] leading-none font-mono font-semibold tabular-nums"
            style={{ color: headlineColor(health) }}
          >
            {health != null ? health.toFixed(0) : '—'}
          </span>
          <span className="text-[12px] font-mono text-[#6b6b80]">/ 100</span>
        </div>
        <p
          className="mt-2 text-[13px] font-mono font-medium uppercase tracking-[0.12em]"
          style={{ color: headlineColor(health) }}
        >
          {scoreWord(health)}
        </p>
        <p className="mt-1 text-[10px] font-mono text-[#56566a] leading-snug">
          Fundamental health vs market peers
        </p>
      </div>

      {/* Pillars + drivers */}
      <div className="space-y-4">
        <div className="grid gap-x-8 gap-y-2.5 sm:grid-cols-2">
          {PILLARS.map(({ key, label, help }) => (
            <PillarBar key={key} label={label} value={pillars[key] ?? null} help={help} />
          ))}
        </div>

        {(strengths.length > 0 || watch.length > 0) && (
          <div className="grid gap-x-8 gap-y-3 border-t border-[#1f1f2c] pt-3 sm:grid-cols-2">
            {strengths.length > 0 && (
              <div>
                <p className="text-[9px] font-mono uppercase tracking-[0.2em] text-[#56566a] mb-1.5">
                  Strengths
                </p>
                <div className="space-y-1.5">
                  {strengths.map((d, i) => (
                    <DriverLine key={`s-${i}`} sentence={d.sentence} good />
                  ))}
                </div>
              </div>
            )}
            {watch.length > 0 && (
              <div>
                <p className="text-[9px] font-mono uppercase tracking-[0.2em] text-[#56566a] mb-1.5">
                  Watch
                </p>
                <div className="space-y-1.5">
                  {watch.map((d, i) => (
                    <DriverLine key={`w-${i}`} sentence={d.sentence} good={false} />
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
