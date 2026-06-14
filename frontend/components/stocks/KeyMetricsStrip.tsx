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
}: {
  groups: MetricGroupData[]
  score: number | null
}) {
  return (
    <div className="grid gap-6 px-4 pb-4 md:grid-cols-[220px_1fr]">
      {/* Signal */}
      <div className="md:border-r md:border-[#1f1f2b] md:pr-6">
        <HealthGauge score={score} />
        <div className="flex justify-center -mt-1">
          <RatingBadge score={score} />
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
