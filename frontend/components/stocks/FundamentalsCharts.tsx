'use client'

import type { FundamentalsRow } from '@/lib/types'

interface MiniBarProps {
  data: { year: number; value: number }[]
  label: string
  suffix?: string
  decimals?: number
}

function MiniBar({ data, label, suffix = '', decimals = 1 }: MiniBarProps) {
  if (!data.length) {
    return (
      <div className="bg-[#111118] border border-[#2a2a3a] rounded-xl p-4">
        <p className="text-[10px] font-mono uppercase tracking-[0.15em] text-[#6b6b80] mb-2">{label}</p>
        <p className="text-xs text-[#6b6b80] font-mono">No data</p>
      </div>
    )
  }

  const hasNeg = data.some(d => d.value < 0)
  const absMax = Math.max(...data.map(d => Math.abs(d.value)), 0.01)

  const W = 260
  const H = 90
  const LABEL_H = 20
  const n = data.length
  const totalW = W
  const gap = Math.max(2, Math.floor(totalW * 0.05 / n))
  const barW = Math.max(4, (totalW - gap * (n + 1)) / n)
  const zeroY = hasNeg ? H / 2 : H

  return (
    <div className="bg-[#111118] border border-[#2a2a3a] rounded-xl p-4">
      <p className="text-[10px] font-mono uppercase tracking-[0.15em] text-[#6b6b80] mb-3">{label}</p>
      <svg width="100%" viewBox={`0 0 ${W} ${H + LABEL_H}`} preserveAspectRatio="xMidYMid meet">
        {/* Zero line */}
        {hasNeg && (
          <line x1={0} y1={zeroY} x2={W} y2={zeroY} stroke="#2a2a3a" strokeWidth="1" />
        )}
        {data.map((d, i) => {
          const x = gap + i * (barW + gap)
          const isPos = d.value >= 0
          const maxH = hasNeg ? H / 2 - 4 : H - 4
          const bH = Math.max((Math.abs(d.value) / absMax) * maxH, 1)
          const bY = isPos ? zeroY - bH : zeroY
          const color = isPos ? '#00d4a4' : '#ff4d6a'
          const fmt = Math.abs(d.value) >= 1000
            ? `${(d.value / 1000).toFixed(1)}K`
            : d.value.toFixed(decimals)

          return (
            <g key={i}>
              <rect x={x} y={bY} width={barW} height={bH}
                fill={color} opacity="0.75" rx="1.5" />
              {barW > 16 && (
                <text x={x + barW / 2} y={isPos ? bY - 3 : bY + bH + 10}
                  textAnchor="middle" fontSize="8" fill={color} fontFamily="monospace">
                  {fmt}{suffix}
                </text>
              )}
              <text x={x + barW / 2} y={H + LABEL_H - 2}
                textAnchor="middle" fontSize="8" fill="#6b6b80" fontFamily="monospace">
                {String(d.year).slice(2)}
              </text>
            </g>
          )
        })}
      </svg>
    </div>
  )
}

interface Props {
  items: FundamentalsRow[]
}

export default function FundamentalsCharts({ items }: Props) {
  const sorted = [...items]
    .filter(r => r.fiscal_year != null)
    .sort((a, b) => (a.fiscal_year ?? 0) - (b.fiscal_year ?? 0))
    .slice(-8)

  const toData = (field: keyof FundamentalsRow) =>
    sorted
      .filter(r => r[field] != null)
      .map(r => ({ year: r.fiscal_year!, value: Number(r[field]) }))

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
      <MiniBar data={toData('eps')} label="EPS (৳)" decimals={2} />
      <MiniBar data={toData('nav')} label="NAV (৳)" decimals={1} />
      <MiniBar data={toData('pe')} label="P/E Ratio" decimals={1} />
      <MiniBar data={toData('cash_div_pct')} label="Cash Dividend" suffix="%" decimals={0} />
    </div>
  )
}
