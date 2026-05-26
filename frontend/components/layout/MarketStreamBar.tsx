'use client'
import { useEffect, useState } from 'react'

interface IndexSnap {
  dsex_value: number
  dsex_change_pct: number
  market_status: string
}

export default function MarketStreamBar({ apiBase }: { apiBase?: string }) {
  const base = apiBase ?? (process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8000')
  const [snap, setSnap] = useState<IndexSnap | null>(null)

  useEffect(() => {
    const es = new EventSource(`${base}/api/market/stream`)
    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        if (!data.error) setSnap(data)
      } catch {
        // ignore parse errors
      }
    }
    es.onerror = () => setSnap(null)
    return () => es.close()
  }, [base])

  if (!snap) {
    return (
      <div className="flex items-center gap-2 text-sm">
        <span className="text-muted text-xs uppercase tracking-widest">DSEX</span>
        <span className="text-muted">—</span>
      </div>
    )
  }

  const isUp = snap.dsex_change_pct >= 0
  const sign = isUp ? '+' : ''
  return (
    <div className="flex items-center gap-3">
      <span className="text-muted text-xs uppercase tracking-widest">DSEX</span>
      <span className="text-white font-semibold text-sm">
        {snap.dsex_value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
      </span>
      <span className={`text-xs font-medium ${isUp ? 'text-accent-green' : 'text-accent-red'}`}>
        {sign}{snap.dsex_change_pct.toFixed(2)}%
      </span>
      <span className={`text-xs px-1.5 py-0.5 rounded ${
        snap.market_status === 'Open' ? 'bg-accent-green/20 text-accent-green' : 'bg-muted/20 text-muted'
      }`}>
        {snap.market_status}
      </span>
    </div>
  )
}
