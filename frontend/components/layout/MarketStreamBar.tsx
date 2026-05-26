'use client'
import { useEffect, useRef, useState } from 'react'

const CLOSED_POLL_MS = 5 * 60 * 1000 // 5 min — re-check if market opened

interface IndexSnap {
  dsex_value: number
  dsex_change_pct: number
  market_status: string
}

export default function MarketStreamBar({ apiBase }: { apiBase?: string }) {
  const base = apiBase ?? (process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8000')
  const [snap, setSnap] = useState<IndexSnap | null>(null)
  const esRef = useRef<EventSource | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    function connect() {
      const es = new EventSource(`${base}/api/market/stream`)
      esRef.current = es

      es.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data)
          if (!data.error) {
            setSnap(data)
            if (data.market_status !== 'Open') {
              // Server will close the stream; schedule sparse re-check
              es.close()
              timerRef.current = setTimeout(connect, CLOSED_POLL_MS)
            }
          }
        } catch {
          // ignore parse errors
        }
      }

      // Server closed stream (e.g. market just closed mid-session):
      // keep last snap visible, schedule reconnect instead of instant retry
      es.onerror = () => {
        es.close()
        timerRef.current = setTimeout(connect, CLOSED_POLL_MS)
      }
    }

    connect()
    return () => {
      esRef.current?.close()
      if (timerRef.current) clearTimeout(timerRef.current)
    }
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
