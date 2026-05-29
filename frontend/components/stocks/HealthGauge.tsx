'use client'

const R = 72
const CX = 88
const CY = 96
const SW = 11
const CIRC = Math.PI * R

function gaugeColor(score: number | null): string {
  if (score == null) return '#3a3a4a'
  if (score >= 70) return '#00d4a4'
  if (score >= 40) return '#f5c842'
  return '#ff4d6a'
}

export default function HealthGauge({ score }: { score: number | null }) {
  const pct = score != null ? Math.max(0, Math.min(100, score)) / 100 : 0
  const offset = CIRC * (1 - pct)
  const color = gaugeColor(score)
  const sx = CX - R
  const ex = CX + R

  return (
    <div className="flex flex-col items-center py-3">
      <div className="relative">
        <svg width={CX * 2 - 4} height={CY - 10} viewBox={`0 ${CY - R - SW - 2} ${CX * 2 - 4} ${R + SW + 8}`}>
          {/* Track */}
          <path
            d={`M ${sx} ${CY} A ${R} ${R} 0 0 0 ${ex} ${CY}`}
            fill="none"
            stroke="#222230"
            strokeWidth={SW}
            strokeLinecap="round"
          />
          {/* Progress */}
          <path
            d={`M ${sx} ${CY} A ${R} ${R} 0 0 0 ${ex} ${CY}`}
            fill="none"
            stroke={color}
            strokeWidth={SW}
            strokeLinecap="round"
            strokeDasharray={CIRC}
            strokeDashoffset={offset}
            style={{ transition: 'stroke-dashoffset 1.1s cubic-bezier(0.4,0,0.2,1), stroke 0.4s ease' }}
          />
          {/* Glow */}
          {score != null && score > 0 && (
            <path
              d={`M ${sx} ${CY} A ${R} ${R} 0 0 0 ${ex} ${CY}`}
              fill="none"
              stroke={color}
              strokeWidth={SW + 6}
              strokeLinecap="round"
              strokeDasharray={CIRC}
              strokeDashoffset={offset}
              opacity="0.12"
              style={{ transition: 'stroke-dashoffset 1.1s cubic-bezier(0.4,0,0.2,1)' }}
            />
          )}
        </svg>
        {/* Score overlay */}
        <div className="absolute inset-x-0 bottom-0 flex flex-col items-center">
          <span
            className="text-[38px] font-mono font-bold tabular-nums leading-none tracking-tight"
            style={{ color }}
          >
            {score != null ? Math.round(score) : '—'}
          </span>
          <span className="text-[9px] font-mono uppercase tracking-[0.2em] text-[#6b6b80] mt-0.5">
            / 100
          </span>
        </div>
      </div>
      <span className="text-[10px] font-mono uppercase tracking-[0.18em] text-[#6b6b80] mt-3">
        Health Score
      </span>
    </div>
  )
}
