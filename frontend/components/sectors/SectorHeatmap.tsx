'use client'
import { useRef, useState, useEffect } from 'react'
import { squarify, type Rect } from '@/lib/treemap'
import type { SectorRow } from '@/lib/types'

function cellStyle(pct: number | null): { bg: string; text: string } {
  if (pct === null) return { bg: '#1a1a24', text: '#6b6b80' }
  if (pct > 6)  return { bg: '#15803d', text: '#fff' }
  if (pct > 3)  return { bg: '#166534', text: '#fff' }
  if (pct > 0)  return { bg: '#14532d', text: '#d1fae5' }
  if (pct === 0) return { bg: '#1a1a24', text: '#6b6b80' }
  if (pct > -3) return { bg: '#7f1d1d', text: '#fee2e2' }
  if (pct > -6) return { bg: '#991b1b', text: '#fff' }
  return { bg: '#b91c1c', text: '#fff' }
}

export default function SectorHeatmap({ sectors }: { sectors: SectorRow[] }) {
  const ref = useRef<HTMLDivElement>(null)
  const [dims, setDims] = useState({ w: 800, h: 480 })

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const obs = new ResizeObserver(([e]) => {
      const { width } = e.contentRect
      setDims({ w: width, h: Math.max(300, Math.round(width * 0.55)) })
    })
    obs.observe(el)
    return () => obs.disconnect()
  }, [])

  const sorted = [...sectors].sort(
    (a, b) => Number(b.market_cap_bdt ?? 1) - Number(a.market_cap_bdt ?? 1)
  )
  const values = sorted.map(s => Math.max(1, Number(s.market_cap_bdt ?? 1)))
  const rects: Rect[] = squarify(values, { x: 0, y: 0, w: dims.w, h: dims.h })

  return (
    <div className="bg-[#111118] border border-[#2a2a3a] rounded-xl p-4">
      <h3 className="text-xs text-[#6b6b80] uppercase tracking-widest mb-3">Sector Performance</h3>
      <div ref={ref} className="relative overflow-hidden rounded" style={{ height: dims.h }}>
        {sorted.map((s, i) => {
          const r = rects[i]
          if (!r || r.w < 2 || r.h < 2) return null
          const pct = s.change_pct === null ? null : Number(s.change_pct)
          const { bg, text } = cellStyle(pct)
          const showLabel = r.w > 54 && r.h > 28
          return (
            <div
              key={s.sector}
              className="absolute flex flex-col items-center justify-center overflow-hidden text-center px-1"
              style={{ left: r.x + 1, top: r.y + 1, width: Math.max(0, r.w - 2), height: Math.max(0, r.h - 2), background: bg, borderRadius: 3 }}
            >
              {showLabel && (
                <>
                  <span className="font-semibold leading-tight truncate w-full" style={{ color: text, fontSize: Math.min(13, Math.max(8, r.w / 9)) }}>
                    {s.sector}
                  </span>
                  {pct !== null && (
                    <span className="leading-none mt-0.5" style={{ color: text, fontSize: Math.min(12, Math.max(8, r.w / 11)), opacity: 0.9 }}>
                      {pct > 0 ? '+' : ''}{pct.toFixed(1)}%
                    </span>
                  )}
                </>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
