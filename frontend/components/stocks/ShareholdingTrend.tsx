import type { ShareholdingSnapshot } from '@/lib/types'

// Segment order mirrors the DSE page: sponsor → govt → institution → foreign → public.
const SEGMENTS = [
  { key: 'sponsor_pct', label: 'Sponsor', color: '#a78bfa' },
  { key: 'govt_pct', label: 'Govt', color: '#f5c842' },
  { key: 'institution_pct', label: 'Institute', color: '#4d9eff' },
  { key: 'foreign_pct', label: 'Foreign', color: '#00d4a4' },
  { key: 'public_pct', label: 'Public', color: '#56566a' },
] as const

type SegmentKey = (typeof SEGMENTS)[number]['key']

function num(v: number | null | undefined): number | null {
  if (v == null) return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

function fmtDate(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: '2-digit' })
}

function DeltaChip({ label, delta }: { label: string; delta: number }) {
  // ±0.05pp is page-rounding noise, not a position change
  const flat = Math.abs(delta) < 0.05
  const color = flat ? '#6b6b80' : delta > 0 ? '#00d4a4' : '#ff4d6a'
  const arrow = flat ? '→' : delta > 0 ? '▲' : '▼'
  return (
    <span
      className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[10px] font-mono"
      style={{ color, backgroundColor: `${color}14`, border: `1px solid ${color}33` }}
    >
      {label} {arrow}
      <span className="tabular-nums">
        {flat ? 'flat' : `${delta > 0 ? '+' : ''}${delta.toFixed(2)}pp`}
      </span>
    </span>
  )
}

interface Props {
  snapshots: ShareholdingSnapshot[]
}

export default function ShareholdingTrend({ snapshots }: Props) {
  const sorted = [...snapshots].sort(
    (a, b) => new Date(a.as_on_date).getTime() - new Date(b.as_on_date).getTime(),
  )
  if (!sorted.length) {
    return <p className="text-xs text-[#6b6b80] font-mono">No shareholding history yet</p>
  }

  const oldest = sorted[0]
  const latest = sorted[sorted.length - 1]

  const deltaOf = (key: SegmentKey): number | null => {
    const a = num(oldest[key])
    const b = num(latest[key])
    return a != null && b != null ? b - a : null
  }
  const instDelta = deltaOf('institution_pct')
  const foreignDelta = deltaOf('foreign_pct')
  const sponsorDelta = deltaOf('sponsor_pct')
  const smartMoney =
    instDelta != null || foreignDelta != null
      ? (instDelta ?? 0) + (foreignDelta ?? 0)
      : null

  return (
    <div className="space-y-4">
      {/* One stacked bar per dated snapshot */}
      <div className="space-y-2.5">
        {sorted.map(snap => {
          const parts = SEGMENTS.map(s => ({ ...s, value: num(snap[s.key]) ?? 0 }))
          const total = parts.reduce((sum, p) => sum + p.value, 0)
          return (
            <div key={snap.as_on_date} className="flex items-center gap-3">
              <span className="w-[72px] shrink-0 text-[10px] font-mono text-[#8a8a9e] tabular-nums">
                {fmtDate(snap.as_on_date)}
              </span>
              <div className="flex h-3 flex-1 rounded-full overflow-hidden bg-[#1a1a24]">
                {total > 0 &&
                  parts.map(
                    p =>
                      p.value > 0 && (
                        <div
                          key={p.key}
                          title={`${p.label} ${p.value.toFixed(2)}%`}
                          className="h-full"
                          style={{ width: `${(p.value / total) * 100}%`, background: p.color }}
                        />
                      ),
                  )}
              </div>
            </div>
          )
        })}
      </div>

      {/* Legend with latest values */}
      <div className="flex flex-wrap gap-x-4 gap-y-1.5">
        {SEGMENTS.map(s => {
          const v = num(latest[s.key])
          if (v == null) return null
          return (
            <span key={s.key} className="inline-flex items-center gap-1.5 text-[10px] font-mono">
              <span className="h-2 w-2 rounded-sm" style={{ background: s.color }} />
              <span className="text-[#8a8a9e]">{s.label}</span>
              <span className="text-[#d8d8e4] tabular-nums">{v.toFixed(2)}%</span>
            </span>
          )
        })}
      </div>

      {/* Flow read: who moved between oldest and latest snapshot */}
      {sorted.length > 1 && (
        <div className="border-t border-[#2a2a3a] pt-3">
          <p className="text-[9px] font-mono uppercase tracking-[0.2em] text-[#56566a] mb-2">
            Flow · {fmtDate(oldest.as_on_date)} → {fmtDate(latest.as_on_date)}
          </p>
          <div className="flex flex-wrap gap-1.5">
            {sponsorDelta != null && <DeltaChip label="Sponsor" delta={sponsorDelta} />}
            {instDelta != null && <DeltaChip label="Institute" delta={instDelta} />}
            {foreignDelta != null && <DeltaChip label="Foreign" delta={foreignDelta} />}
            {smartMoney != null && Math.abs(smartMoney) >= 0.05 && (
              <span
                className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[10px] font-mono font-semibold"
                style={{
                  color: smartMoney > 0 ? '#00d4a4' : '#ff4d6a',
                  backgroundColor: smartMoney > 0 ? '#00d4a414' : '#ff4d6a14',
                  border: `1px solid ${smartMoney > 0 ? '#00d4a433' : '#ff4d6a33'}`,
                }}
              >
                {smartMoney > 0 ? 'Smart money entering' : 'Smart money exiting'}
                <span className="tabular-nums">
                  {smartMoney > 0 ? '+' : ''}
                  {smartMoney.toFixed(2)}pp
                </span>
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
