import type { MlPrediction } from '@/lib/types'

const DIR_COLOR: Record<string, string> = {
  UP: '#00d4a4',
  DOWN: '#ff4d6a',
}

function horizonLabel(days: number): string {
  const map: Record<number, string> = { 365: '1Y', 1095: '3Y', 1825: '5Y', 3650: '10Y' }
  return map[days] ?? `${days}d`
}

function dirArrow(dir: string | null): string {
  if (dir === 'UP') return '↑'
  if (dir === 'DOWN') return '↓'
  return '·'
}

function PredictionCard({ p }: { p: MlPrediction }) {
  const color = (p.predicted_direction && DIR_COLOR[p.predicted_direction]) ?? '#6b6b80'
  const conf = p.confidence != null ? `${Math.round(p.confidence * 100)}%` : '—'
  const target = p.target_price != null ? `৳${Number(p.target_price).toFixed(2)}` : '—'
  return (
    <div className="rounded-lg border border-[#2a2a3a] bg-[#111118] px-4 py-3">
      <div className="flex items-center justify-between mb-2">
        <span className="text-[11px] font-mono uppercase tracking-[0.18em] text-[#6b6b80]">
          {horizonLabel(p.horizon_days)}
        </span>
        <span className="text-[15px] font-mono font-bold" style={{ color }}>
          {dirArrow(p.predicted_direction)} {p.predicted_direction ?? '—'}
        </span>
      </div>
      <div className="flex justify-between text-[11px] font-mono">
        <span className="text-[#8a8a9e]">Confidence</span>
        <span className="tabular-nums" style={{ color }}>{conf}</span>
      </div>
      <div className="flex justify-between text-[11px] font-mono mt-1">
        <span className="text-[#8a8a9e]">Target</span>
        <span className="tabular-nums text-[#e8e8f0]">{target}</span>
      </div>
    </div>
  )
}

export default function AiAnalysis({
  predictions,
  narrative,
}: {
  predictions: MlPrediction[]
  narrative?: string | null
}) {
  return (
    <div className="px-4 pb-4 space-y-4">
      {/* ML predictions */}
      <div>
        <p className="text-[9px] font-mono uppercase tracking-[0.2em] text-[#56566a] mb-2">
          ML Predictions
        </p>
        {predictions.length > 0 ? (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {predictions.map((p) => (
              <PredictionCard key={`${p.horizon_days}-${p.predicted_at}`} p={p} />
            ))}
          </div>
        ) : (
          <p className="text-[12px] font-mono text-[#6b6b80] py-3">
            No predictions yet — model output will appear here once available.
          </p>
        )}
      </div>

      {/* LLM narrative */}
      <div className="pt-3 border-t border-[#1f1f2b]">
        <p className="text-[9px] font-mono uppercase tracking-[0.2em] text-[#56566a] mb-2">
          AI Narrative
        </p>
        {narrative ? (
          <p className="text-[13px] leading-relaxed text-[#d8d8e4] whitespace-pre-line">
            {narrative}
          </p>
        ) : (
          <p className="text-[12px] font-mono text-[#6b6b80] py-1">
            AI narrative not available yet.
          </p>
        )}
      </div>
    </div>
  )
}
