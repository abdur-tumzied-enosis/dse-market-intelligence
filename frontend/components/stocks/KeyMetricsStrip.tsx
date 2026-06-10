import HealthGauge from '@/components/stocks/HealthGauge'
import RatingBadge from '@/components/stocks/RatingBadge'

interface MetricRow {
  label: string
  value: string
  accent?: string
}
interface MetricGroupData {
  title: string
  rows: MetricRow[]
}

function ScoreBar({ label, value }: { label: string; value: number | null }) {
  const n = Number(value ?? 0)
  const pct = Math.min(100, Math.max(0, n))
  const color = pct >= 70 ? '#00d4a4' : pct >= 40 ? '#f5c842' : '#ff4d6a'
  return (
    <div>
      <div className="flex justify-between text-[10px] font-mono mb-1">
        <span className="text-[#8a8a9e]">{label}</span>
        <span className="text-[#e8e8f0] tabular-nums">{value != null ? n.toFixed(0) : '—'}</span>
      </div>
      <div className="h-1.5 rounded-full bg-[#1a1a24] overflow-hidden">
        <div className="h-full rounded-full" style={{ width: `${pct}%`, backgroundColor: color }} />
      </div>
    </div>
  )
}

function Group({ data }: { data: MetricGroupData }) {
  return (
    <div>
      <p className="text-[9px] font-mono uppercase tracking-[0.2em] text-[#56566a] mb-1.5">
        {data.title}
      </p>
      <div className="space-y-1.5">
        {data.rows.map(({ label, value, accent }) => (
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

export default function KeyMetricsStrip({
  groups,
  score,
  fundamentalScore,
  momentumScore,
  ratingColor,
}: {
  groups: MetricGroupData[]
  score: number | null
  fundamentalScore: number | null
  momentumScore: number | null
  ratingColor: string
}) {
  return (
    <div className="grid gap-6 px-4 pb-4 md:grid-cols-[220px_1fr]">
      {/* Signal */}
      <div className="md:border-r md:border-[#1f1f2b] md:pr-6">
        <HealthGauge score={score} />
        <div className="flex justify-center -mt-1 mb-3">
          <RatingBadge score={score} />
        </div>
        <div className="space-y-2.5">
          <ScoreBar label="Fundamental" value={fundamentalScore} />
          <ScoreBar label="Momentum" value={momentumScore} />
        </div>
      </div>

      {/* Metric groups */}
      <div className="grid gap-x-8 gap-y-4 sm:grid-cols-2 lg:grid-cols-3">
        {groups.map((g) => (
          <Group key={g.title} data={g} />
        ))}
      </div>
    </div>
  )
}
